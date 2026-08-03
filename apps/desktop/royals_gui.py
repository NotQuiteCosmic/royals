####################################################################################
#### A WINDOW FOR ROYALS ###########################################################
####################################################################################
# The same game MainPlay.py plays in the terminal, in a tkinter window. Nothing in
# Engine.py, Hasher.py or artificialPlayer.py is touched or needed differently: every
# one of those functions takes a board and hands a new one back, so this file only has
# to decide when to call them and how to draw what comes out.
#
# The one real difference is shape. MainPlay is a straight line -- it blocks on input()
# inside nested while loops, and the state of the turn lives in the call stack. A window
# can never block, so the same state is written down instead:
#
#   phase        -- "entering", "play" or "over"
#   enterIndex   -- how far down Engine.enteringSequence() the entering has got
#   turn         -- as MainPlay counts it; contr is turn % 2
#   selected     -- the origin a human has clicked, or None while they are choosing one
#
# and every click advances that by exactly one step. advance() is the loop MainPlay
# runs; it returns to the event loop wherever MainPlay would have called input().
#
# The computer thinks on a worker thread, and it still needs one -- but for a different
# reason than it used to, so the reasoning is worth restating rather than patching.
#
# A search costs roughly three times the level below it. With the compiled engine (the
# royals-accel wheel) depth 7 is about a fifth of a second, 8 under a second, 9 about three.
# Without it, in pure Python, everything is some thirty-six times dearer: depth 6 is a
# couple of seconds and 8 is most of a minute. Both are configurations this window has to be
# usable in, since the wheel is optional by design.
#
# So at the top of the range a search is seconds either way, and seconds inside a click
# handler is a window that cannot redraw and gets greyed out by the window server. The
# thread stays.
#
# What changed is what the thread buys. It used to be the only thing between the player and
# a frozen window, because a Python search holds the GIL the whole way through and tkinter
# cannot repaint without it -- the thread moved the freeze rather than removing it. The
# compiled engine releases the GIL for the duration of the search, so the window genuinely
# keeps painting while the computer thinks. On the pure-Python path it is still the old
# bargain.
#
# (Every timing above has been wrong twice: once when the packed-int board landed and again
# when the engine was ported. If they read as suspiciously round, re-measure before trusting
# them -- see docs/ARCHITECTURE.md for how to run both engines.)

import datetime
import math
import queue
import random
import threading

import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
import tkinter.colorchooser as colorchooser
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox

# The palette, as data. Imported by name rather than star so it is obvious at every call site
# which side of the line a thing is on: `theme` owns the schema and the files, this module
# owns the drawing and the names the drawing reads.
import theme as Theme

# The engine is an installed package now (pip install -e ./engine), so the sys.path fixup
# that used to sit here is gone. Aliased on import so every call site below reads exactly
# as it did when these were top-level modules.
from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as artificialPlayer
from royals_engine import notation as N
from royals_engine import record as Record
from royals_engine import _accel


# Whether the compiled engine is answering. Read once, here, because everything downstream
# of it -- how deep the buttons go, what the advice says, what colour the badge is -- has to
# agree, and because _accel itself decides once at import for the same reason.
FAST_ENGINE = _accel.active()

# How deep the buttons offer to go, and what to say about it. Both depend on the engine: the
# difference between the two is about thirty-six times, which is the difference between "a
# fifth of a second" and "most of a minute" at the top of the range. One set of numbers
# cannot honestly describe both.
DEPTH_MAX = 10
DEPTH_DEFAULT = 7 if FAST_ENGINE else 5
DEPTH_ADVICE = (
    "7 to 9 recommended — 8 takes about a second a move, 9 about five. 10 is the deepest "
    "there is and thinks for something like twenty seconds a move."
    if FAST_ENGINE else
    "5 or 6 recommended — 6 takes about two and a half seconds a move, 7 nearer ten. "
    "Past that it is minutes a move, and 10 is not worth starting. Installing "
    "royals-accel makes the deeper settings practical."
)


####################################################################################
####### LOOK #######################################################################
####################################################################################
# Dressed after the DOS-era chess programs -- Battle Chess and its contemporaries.
# Those had no alpha channel and no font smoothing, and got all their depth from one
# trick: a light line along a shape's top and left, a dark one along its bottom and
# right, and the eye reads it as carved out of something. Everything below is built
# from that rule and a warm VGA palette, so it needs no image files and follows CELL
# if the board is ever resized.
#
# The one thing that had to be built rather than configured is the controls. On macOS
# tk draws Buttons, Radiobuttons and Scales natively and ignores most colour asked of
# them, so a native control sits on this palette like a window from another program.
# The Stone* widgets further down are Labels and Canvases wearing the same two-line
# bevel as everything else, and they carry the DOS-era (*) and [X] markers besides.

# These were the board's geometry when it was drawn flat. The board is projected now and
# takes its scale from however large the canvas happens to be, so all that is left of them
# is the size the window opens at and the one ratio the projection still needs: how wide
# the carved border is compared to a square.
CELL = 82
# The carved border the playfield sits in. The rank and file letters are cut into it,
# where a real board has them, rather than floating outside it.
FRAME = 34

PAD_L = FRAME
PAD_T = FRAME
PAD_R = FRAME
PAD_B = FRAME

BOARD_W = PAD_L + 7 * CELL + PAD_R
BOARD_H = PAD_T + 7 * CELL + PAD_B

# What the board canvas asks for when the game window opens. Turned forty-five degrees a
# seven by seven board is nearly ten squares across, so holding the old squares at the old
# size costs about half as much width again -- and the window is now draggable, so this is
# only where it starts. Both are pulled in if the screen is too small to hold them.
OPEN_W = 900
OPEN_H = 740

# Off-white and black, and the greys that fall between them. Nothing here carries a hue,
# so everything the colour used to say has to be said another way: the two sides differ by
# value rather than by tint, and the three kinds of destination differ by shape.
#
# **Every name in this block is the schema of a theme file.** `theme.DEFAULT` is these values
# copied out, and `theme.py`'s first test holds the two against each other -- so a colour
# added here without a line there fails the build rather than becoming a shade no preset can
# reach. That is also why seven names that used to sit in this block are gone: `PAPER`,
# `WHITE_DK`, `BLACK_DK`, `SELECT`, `JUMP`, `PUSH` and `FREE` each appeared exactly once, at
# their own definition, and a setting that paints nothing is worse than no setting at all.
# (The destination marks do still differ by shape rather than colour; what they no longer do
# is read a colour from here. See drawSquare, which picks a tone per square from the square
# it is drawn on.)
INK = "#0d0d0c"

####### the cabinet #######
EDGE_DK = "#a7a299"
EDGE = "#dbd7ce"
EDGE_LT = "#f7f5f0"
RULE = "#2a2a28"
RULE_LT = "#12120f"
RULE_DK = "#8d8880"

####### the panel it all sits on #######
PANEL = "#e9e6de"
PANEL_LT = "#f4f2ec"
PANEL_DK = "#b9b4aa"
# recessed areas -- the log well, the board surround
WELL = "#d8d4ca"

TEXT = "#161614"
TEXT_DIM = "#6f6a62"
TEXT_KEY = "#0d0d0c"

####### the squares, each with its two bevel shades #######
# The dark square is a grey and not a black, and the reason is the pieces. Black takes the
# dark pieces, and on a near-black square a black piece was a pale lid floating over
# nothing -- its wall, which is where a standing piece keeps most of its ink, had no ground
# to be seen against. There is only so much room between the two square shades and the two
# piece shades, and the squares are what has to give: a piece that is hard to see is worse
# than a board that is less stark.
# Where the grey landed, and why it is not a stop or two darker. It has two contrasts to
# keep and they pull opposite ways: against a black piece it is 3.0 to 1, and against the
# light square 4.8. The first is the floor at which a large solid shape is reliably seen
# and is the whole reason this square stopped being black; the second is what keeps the
# board a checkerboard. Every step darker buys the checkerboard about a fifth of a stop
# and costs the piece the same, and the piece is the thing that has to be seen.
LIGHT = "#e2ded5"
LIGHT_HI = "#f8f6f2"
LIGHT_LO = "#b4afa5"
DARK = "#615e56"
DARK_HI = "#847f76"
DARK_LO = "#3b3933"

# The same two shifted for the states a square can be in. Canvas has no alpha, so a
# highlight is a different fill rather than a wash over one -- and with no colour to
# spend, the shift has to be in lightness: a played-from square lifts, a square open for
# entering sinks.
LIGHT_LAST = "#f6f3ec"
DARK_LAST = "#7e7a6e"
LIGHT_ENTRY = "#c6c2b8"
DARK_ENTRY = "#48453e"

# The two sides. White takes the light pieces and black the dark ones.
WHITE = "#f6f4ef"
BLACK = "#0d0d0c"

# The bands a standing chip's wall is lit in, and the line round its foot. These five used to
# be hex literals written straight into CHIP_WALL, which meant the wall of a piece -- where
# most of a standing piece's ink is -- was the one thing on the board no theme could reach.
# Named here so it can be, and so CHIP_WALL below is assembled from names rather than
# from values.
WHITE_HI = "#ffffff"
WHITE_LO = "#b6b1a7"
# The fourth of the four, and the one doing real work: it is what keeps a pale chip on a pale
# square from disappearing. White's rim is a grey; black's is INK, which is to say its own.
WHITE_RIM = "#6f6a62"
BLACK_HI = "#38352f"
BLACK_LO = "#000000"

LABEL = "#3a3833"

PIECE_NAMES = {Hasher.ROYAL: "Royal", Hasher.PAWNS: "Pawn", Hasher.SPY: "Spy"}

# Filled by initFonts once there is a Tk root to ask about families.
FONT = {}


# The families wanted, best first, falling back to whatever the machine has. A heavy
# serif is what the era used for anything carved and a mono for anything tabular.
def initFonts():
    have = set(tkfont.families())

    def pick(names, size, weight="normal"):
        for name in names:
            if name in have: return (name, size, weight)
        return (names[-1], size, weight)

    serif = ["Palatino", "Georgia", "Times New Roman", "Times", "Helvetica"]
    mono = ["Menlo", "Monaco", "Courier New", "Courier", "Helvetica"]

    FONT["title"] = pick(serif, 34, "bold")
    FONT["sub"] = pick(serif, 12)
    FONT["legend"] = pick(serif, 11, "bold")
    FONT["glyph"] = pick(serif, 23, "bold")
    FONT["dragon"] = pick(serif, 13, "bold")
    FONT["pris"] = pick(serif, 12, "bold")
    FONT["coord"] = pick(serif, 13, "bold")
    FONT["status"] = pick(serif, 16, "bold")
    FONT["body"] = pick(serif, 12)
    FONT["small"] = pick(serif, 11)
    FONT["button"] = pick(serif, 13, "bold")
    FONT["mark"] = pick(mono, 12, "bold")
    FONT["log"] = pick(mono, 11)


####### the two-line bevel everything is built from #######

# A chiselled edge on the rectangle x0,y0..x1,y1. `raised` puts the light along the top
# and left so the shape stands up; false swaps them and it reads as cut into the surface.
# `tags` is here for the one plate that is drawn on the board rather than in the panel and
# so has to be findable by a click -- the BREAK badge. Panel widgets pass nothing and are
# unaffected.
#
# The lines are drawn at the hairline weight rather than at tk's default of one pixel, which
# is what they used to take by saying nothing. They are the same thing at the Normal setting
# and differ at the other two, which is the point: a bevel is a silhouette, and a player who
# asks for heavier lines is asking for this edge too.
def bevel(c, x0, y0, x1, y1, depth, hi, lo, raised=True, tags=None):
    top, bottom = (hi, lo) if raised else (lo, hi)
    tags = tags or ()
    w = LINE_W["hair"]
    for i in range(depth):
        c.create_line(x0 + i, y0 + i, x1 - i, y0 + i, fill=top, width=w, tags=tags)
        c.create_line(x0 + i, y0 + i, x0 + i, y1 - i, fill=top, width=w, tags=tags)
        c.create_line(x0 + i, y1 - i, x1 - i + 1, y1 - i, fill=bottom, width=w, tags=tags)
        c.create_line(x1 - i, y0 + i, x1 - i, y1 - i, fill=bottom, width=w, tags=tags)


# A filled panel with that edge on it.
def plate(c, x0, y0, x1, y1, fill, hi, lo, depth=2, raised=True, tags=None):
    c.create_rectangle(x0, y0, x1, y1, fill=fill, outline="", tags=tags or ())
    bevel(c, x0, y0, x1, y1, depth, hi, lo, raised, tags)


# Text with a hard shadow under it, which is what makes lettering look cut rather than
# printed. The shadow goes down-right, matching the light the bevels assume.
#
# `shadow=None` rather than `shadow=INK`, and that is not a style preference: a default
# argument is bound once, when the `def` runs, so `shadow=INK` would have captured the ink
# colour at import and gone on drawing it after a theme had changed INK. Resolved in the body,
# it is read when the call happens. Every current call passes a shadow explicitly, so this
# fixes nothing today -- it disarms a trap that would have gone off the first time somebody
# left the argument out.
def engrave(c, x, y, text, font, fill, shadow=None, **kw):
    if shadow is None: shadow = INK
    c.create_text(x + 1, y + 1, text=text, font=font, fill=shadow, **kw)
    return c.create_text(x, y, text=text, font=font, fill=fill, **kw)


####################################################################################
####### THE VIEW ###################################################################
####################################################################################
# The board is drawn in three dimensions now, because the game is played in three: a
# square holds a stack, and a stack is a thing with a height. Flat, six pieces on one
# square had to be spelled out as six little shapes crammed into 82 pixels; standing
# up, they are six chips piled on each other and the height says it before the icons
# do.
#
# Everything below is orthographic -- no perspective divide, so every square stays the
# same size and the board reads as a board rather than as a photograph of one. World
# units are cells. The playing surface is the plane z = 0, the cabinet hangs below it
# and the pieces stand above it. Yaw turns the board about its centre, pitch is how far
# the camera has been lifted off the horizon:
#
#     cx = x cos y - y sin y            sx = ox + S cx
#     cy = x sin y + y cos y            sy = oy + S (cy sin p - z cos p)
#
# Four things fall out of that, and the whole renderer is built on them:
#
#   * z appears only in sy, and only as -S cos p. The vertical axis is therefore always
#     exactly vertical on screen whatever the yaw, so a chip can be an oval, a rectangle
#     and an oval -- axis-aligned, and exact rather than approximated.
#   * a circle lying flat projects to an ellipse whose axes are the screen axes, always,
#     because turning a circle about its own centre does nothing to it. So create_oval
#     still draws the round pieces and the round destination rings.
#   * there is a direction in the board's own surface, U = (cos y, -sin y), that projects
#     to exactly (S, 0) -- along the screen, at full length, at every angle. Anything
#     laid out along U is genuinely painted flat on the surface and yet never squashed
#     along its length. That is what lets the old flat icons be reused as they are: put
#     them in the U,V plane and the projection is nothing but a squash of their height.
#   * +cy is towards the viewer, so the painter's order is ascending cy. A stack only
#     ever grows up the screen, which is to say towards the squares behind it, so a far
#     stack can never cover a near square and drawing square by square, far to near, is
#     exactly right rather than nearly right.

# The cabinet, in cells: the border is as wide a fraction of a square as it always was.
E_FRAME = FRAME / float(CELL)
E_HALF = 3.5 + E_FRAME
SLAB_T = 0.30

# A piece is a chip, and every chip is the same chip -- what a piece is gets said by the
# icon on its lid, not by its shape, exactly as it was said by its shape before. One
# radius for all of them, top of the pile included: the top chip used to be drawn wider,
# as a capping stone that made the head of a pile findable at a glance, and it cost more
# than it bought. A stack whose top step is wider than the step under it reads as a piece
# of a different size rather than as the same piece higher up, and the overhang throws a
# step into the silhouette that no stack actually has.
R_CHIP = 0.40
# Tall enough that six of them are half a square high on screen at the angle the board
# opens at. Thinner chips are prettier and say nothing: the whole point of standing the
# pieces up is that a full square should be visible as a full square from across the room.
H_CHIP = 0.16
H_MAX = 6 * H_CHIP + 0.04

# The dragon can't stack and never shares a square, so it gets a solid a pile can never
# make: an obelisk. Five chips high, and its foot is a square nine tenths of a board
# square across -- against a chip, which is eight tenths of one across its diameter, so
# the dragon is the widest thing on the board as well as the tallest. Neither figure is
# one the other pieces are measured in and neither can be.
#
# `r` here is the corner-to-corner half-width, so nine tenths across the flats is
# 0.90/sqrt(2). That the diagonal comes out past half a square is not the overhang it
# looks like: the foot and the square are both aligned to the board, so what a foot's
# corner has to clear is the square's corner and not its edge, and a 0.90 square sits
# inside a 1.00 square with a twentieth to spare on every side.
#
# The width is a deliberate trade against slenderness and it is worth being plain about
# what it costs. A piece's width is on screen whole at every angle while its height is
# scaled by cos(pitch), so at the angle the board opens at this one is nine tenths of a
# square wide and under half a square tall on the screen -- getting on for twice as wide
# as it is high. It does not read as a slender thing and is not trying to. It reads as a
# thing that has taken the square, which is the right story for the one piece nothing may
# ever share a square with, and a story a narrow shaft could not tell: a slim obelisk is a
# marker standing on a square, and this is a monument occupying one.
#
# What still says obelisk rather than pyramid, now that the proportions no longer do, is
# the taper and the pyramidion: the shaft draws in only slightly and then stops, and the
# cap is a separate, sharper slope sitting on the end of it. A pyramid is one slope from
# foot to point. This is two, and the break between them is the whole of the reading.
#
# A square plan is worth more here than a round one. A cylinder and a cone are the same
# outline from every side, so a chip and a cone differ only in how their edges taper, and
# that is a thin difference at the angle the board opens at. An obelisk turns with the
# board: it has an arris running down the front of it and two faces at different shades
# meeting there, which is a thing no stack of discs ever shows. This wide, that arris is
# the longest line on the board and the piece is unmistakable from across the room.
R_DRAGON = 0.6364
H_DRAGON = 5 * H_CHIP

# Slightly, as obelisks go -- a real one draws in by about a quarter over its height, and
# this one is thirty pixels tall. Much under 0.8 and the taper stops reading as a taper
# and starts reading as a spire.
DRAGON_TAPER = 0.85
# The pyramidion, as a fraction of the height. A real one is nearer a tenth, which at this
# size is two pixels and a rumour; this is the smallest cap that still says the top of the
# piece comes to a point rather than being cut off flat.
DRAGON_CAP = 0.15

STUD_R = 0.09
STUD_H = 0.05

# The two weights an arrow is drawn at, in cells: shaft half-width, barb half-width, and how
# much of the run the head takes. A break being offered is wide and grey and has to be read
# from across the board; a move already made is a thin black record that must not shout over
# the position it is describing. The numbers are the whole difference between them.
BRK_SHAFT, BRK_BARB, BRK_HEAD = 0.15, 0.34, 0.55
FLY_SHAFT, FLY_BARB, FLY_HEAD = 0.055, 0.135, 0.30

# Both ends of a run are pulled in off the square's centre, but only just. Set wide enough
# to keep an arrow clear of both squares, a one-square arrow is 0.42 cells long with a 0.30
# head on it -- an arrowhead with a stub behind it, and it reads as a smudge. An arrow whose
# job is to say a piece went from here to there has to reach both ends to say it.
RUN_FOOT, RUN_TIP = 0.18, 0.16

# A flight arrow rides just off the tile and no higher, so it stays visibly attached to the
# two squares it joins.
FLY_Z = 0.02

# Pitch stops short of flat at both ends. Below about fifteen degrees the far half of the
# board is behind the near half; at ninety exactly every stack is edge-on to the camera
# and has no height at all, which is the flat board this replaced.
PITCH_MIN = math.radians(15.0)
PITCH_MAX = math.radians(88.0)
YAW_DEF = math.radians(45.0)
PITCH_DEF = math.radians(55.0)

# About three hundred pixels of drag for half a turn, and the whole pitch range in under
# two hundred -- enough that a flick moves the view and a nudge doesn't.
YAW_PER_PX = math.radians(0.60)
PITCH_PER_PX = math.radians(0.40)
# how far the hand may wander before a click is read as a drag instead
DRAG_SLOP = 4

# Below this the lids are squashed so far that a triangle stops reading as a triangle, so
# the icons quietly stop foreshortening and hold a floor instead. They are then very
# slightly out of the surface they are supposed to be painted on. It is a lie, and it is
# the difference between a legible board at fifteen degrees and a row of grey slivers.
ICON_SQUASH_MIN = 0.45

FIT_MARGIN = 10
ROOT2 = math.sqrt(2.0)


####### the pieces #######
# One shape per kind of piece, and the count of pawns shown as that many of their shape
# rather than as a numeral -- four is few enough to read at a glance, and it keeps the
# square free of lettering. Which side a piece belongs to is its fill: white is the light
# one, black the dark. Each is drawn with the other's shade as its outline, so a dark piece
# on a dark square and a light one on a light square both still have an edge.
#
#   royal    square      the one that has to arrive last, and the one worth most
#   spy      triangle    the one everything else gathers on to
#   pawn     disc        one per pawn, up to four
#
# There is no dragon here. It is the one piece that never shares a square with anything,
# so it never has to be told apart from its neighbours on a lid -- it is drawn as an
# obelisk instead, and the shape of the piece is the whole of the icon. See obelisk().

# A pale halo is laid down under every piece before the piece itself. Without it the code
# inverted with the square: a light piece read as a hollow ring on a white square and as a
# solid disc on a black one, and its opponent did the same the other way round, so which
# side a piece belonged to depended on what it happened to be standing on. With the halo
# both sides sit on the same pale ground wherever they are, and the rule holds everywhere
# -- white has a light centre, black a dark one.
PIECE_HALO = "#f4f1ea"

# The shades a chip's wall is banded in, and the line drawn round its foot. Standing up,
# the wall is where most of a piece's ink is, so this is where the side is now said -- the
# lid has to stay pale for both sides, for the same reason the halo did.
#
# The last of the four is what keeps a pale chip on a pale square from disappearing. Three
# shades of near-white are a cylinder on a dark square and a smudge on a light one; the
# rim line is the outline the flat pieces had, kept for the same reason they had it.
#
# The dark side's three shades used to be the dark square's own two bevel shades, which
# held while the squares were nearly black and stopped the moment they were grey: a
# highlight borrowed from the square is a highlight that disappears on that square, and
# grey banding on a black piece makes it read as a grey piece. These are the piece's own
# and they stay near black, which is now the whole of how it is told from what it stands on.
#
# **Built by a function, and not a literal, because a theme can move any of the five names it
# reads.** A dict literal here would snapshot them at import and go on painting the old colours
# after applyTheme had assigned new ones -- the piece walls would be the one part of the board
# that never changed. Every table below this line that is assembled from colours needs the
# same treatment, and buildTables is where they all live.
#
#          mid       highlight   shadow      rim
def chipWalls():
    return {
        0: (WHITE, WHITE_HI, WHITE_LO, WHITE_RIM),
        1: (BLACK, BLACK_HI, BLACK_LO, INK),
    }


CHIP_WALL = chipWalls()

# A shade part way between two of the above, for the faces of a piece that meets the light
# at some angle other than the three the chip's wall knows about. Memoised: an obelisk
# asks for eight of them every time the board is redrawn, and the board is redrawn all the
# way through a drag. This is not a gradient sneaking back in -- what comes out is one more
# flat colour, for one more flat polygon, on a canvas that still has no alpha in it.
_MIXED = {}


def mixShade(a, b, t):
    key = (a, b, round(t, 2))
    got = _MIXED.get(key)
    if got is None:
        parts = []
        for i in (1, 3, 5):
            lo, hi = int(a[i:i + 2], 16), int(b[i:i + 2], 16)
            parts.append(int(round(lo + (hi - lo) * key[2])))
        got = _MIXED[key] = "#%02x%02x%02x" % tuple(parts)
    return got


####### Wearing a theme #######
# How heavy a drawn line is. Three weights rather than one, because the difference between
# them is a difference in kind and not of degree: a hairline is a silhouette, a piece line is
# an edge meant to be seen, a mark line is something the board is telling you. Scaling one
# number would make the marks shout before the silhouettes were visible.
LINE_W = dict(Theme.DEFAULT["lines"])

# The wash behind the board, or {"on": False}. Drawn as bands -- see drawGradient.
GRADIENT = dict(Theme.DEFAULT["gradient"])


def applyTheme(theme):
    """Wear a theme. Raises Theme.ThemeError, having changed nothing, if it is not one.

    **The palette is module-level names and this assigns into them.** That reads like a
    shortcut and is in fact the only honest option: every colour in this file is read by a
    plain global lookup at the moment it is used, so reassigning the name is exactly as
    complete as threading a palette object through three thousand lines, and does not require
    touching a single call site. What it costs is this function, which has to know the two
    places a colour is captured rather than looked up.

    Both are here. `CHIP_WALL` is a table assembled *from* colours, so it is rebuilt rather
    than reassigned. `_MIXED` is keyed by the colour values themselves, so it is already
    correct across a swap -- it is cleared to stop it growing a set of entries per theme the
    player tries, not for correctness.

    What this does not do is repaint anything. The board takes a new palette on its next
    redraw, because redraw() clears the canvas and reads every colour again; the widgets take
    it when their builder next runs, because a tk option is copied into the widget at
    construction. Both of those are the caller's business, and the caller is the appearance
    screen, which rebuilds itself.
    """
    theme = Theme.validate(theme)

    globals().update(theme["colours"])
    LINE_W.update(theme["lines"])
    GRADIENT.update(theme["gradient"])

    global CHIP_WALL
    CHIP_WALL = chipWalls()
    _MIXED.clear()

    return theme


def currentTheme(name="Current"):
    """The palette as it stands, as a theme -- what the appearance screen starts editing."""
    return {
        "name": name,
        "colours": {key: globals()[key] for key in Theme.DEFAULT["colours"]},
        "lines": dict(LINE_W),
        "gradient": dict(GRADIENT),
    }


def pieceFill(side):
    return BLACK if side else WHITE


def drawPiece(c, draw, x, y, r, side, w=2):
    draw(c, x, y, r, PIECE_HALO, PIECE_HALO, w + 3)
    draw(c, x, y, r, pieceFill(side), INK, w)


# Each shape written once, as corners on a unit square, so the flat drawing below and the
# projected drawing further down are reading from the same table. The legend used to be
# drawn with the same functions the board was for exactly this reason -- a key that can
# drift from what it describes is worse than no key -- and one table keeps that true now
# that the board draws its icons squashed onto the lid of a chip and the legend doesn't.
# None rather than a list of corners means a circle: a disc has no corners to write down,
# and create_oval draws it better than any number of them would.
SHAPE_ROYAL = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
SHAPE_SPY = ((0.0, -1.0), (1.0, 0.78), (-1.0, 0.78))
SHAPE_DISC = None


# One shape, at a point, with its two radii given apart so it can be squashed. rx == ry
# is the flat case and is what the legend and the old board ask for.
def shapeAt(c, shape, x, y, rx, ry, fill, edge, w=2, tags=None):
    if shape is SHAPE_DISC:
        return c.create_oval(x - rx, y - ry, x + rx, y + ry,
                             fill=fill, outline=edge, width=w, tags=tags or ())
    pts = []
    for u, v in shape:
        pts.append(x + u * rx)
        pts.append(y + v * ry)
    return c.create_polygon(pts, fill=fill, outline=edge, width=w, tags=tags or ())


def iconRoyal(c, x, y, r, fill, edge, w=2):
    shapeAt(c, SHAPE_ROYAL, x, y, r, r, fill, edge, w)


def iconSpy(c, x, y, r, fill, edge, w=2):
    shapeAt(c, SHAPE_SPY, x, y, r, r, fill, edge, w)


def iconPawn(c, x, y, r, fill, edge, w=2):
    shapeAt(c, SHAPE_DISC, x, y, r, r, fill, edge, w)


# Lays a row of shapes out centred on x, and draws them. `items` is a list of the icon
# functions to place, in order.
def iconRow(c, items, x, y, r, gap, side, w=2):
    if not items: return
    step = r * 2 + gap
    start = x - (len(items) - 1) * step / 2.0
    for i, draw in enumerate(items):
        drawPiece(c, draw, start + i * step, y, r, side, w)


####### the camera #######

# Where the board is being looked at from. A plain object rather than state hung off the
# window, because there are two of them: the live one the board is drawn through, and a
# fixed one the legend uses to draw its illustrative chips at an angle that never moves.
class View:
    __slots__ = ("yaw", "pitch", "scale", "ox", "oy",
                 "sinY", "cosY", "sinP", "cosP", "squash", "detail")

    def __init__(self, yaw=YAW_DEF, pitch=PITCH_DEF, scale=60.0, ox=0.0, oy=0.0):
        self.scale = scale
        self.ox = ox
        self.oy = oy
        self.detail = True
        self.setAngles(yaw, pitch)

    def setAngles(self, yaw, pitch):
        self.yaw = yaw % (2.0 * math.pi)
        self.pitch = max(PITCH_MIN, min(PITCH_MAX, pitch))
        self.sinY = math.sin(self.yaw)
        self.cosY = math.cos(self.yaw)
        self.sinP = math.sin(self.pitch)
        self.cosP = math.cos(self.pitch)
        self.squash = max(self.sinP, ICON_SQUASH_MIN)

    # The scale that puts the whole cabinet on a canvas this size, and the origin that
    # centres it. The width is worked out for the worst yaw there is rather than for the
    # yaw actually in force: the honest figure is up to a third larger when the board is
    # square-on, but it changes as the board turns, and a board that breathes in and out
    # under the hand while being rotated is far more distracting than a wider margin. It
    # also means the board can never be caught spilling off the canvas mid-turn.
    def fit(self, w, h):
        reach = ROOT2 * E_HALF
        wide = (w - 2 * FIT_MARGIN) / (2.0 * reach)
        tall = (h - 2 * FIT_MARGIN) / (2.0 * reach * self.sinP
                                       + (H_MAX + SLAB_T) * self.cosP)
        self.scale = max(8.0, min(wide, tall))
        self.ox = w / 2.0
        used = self.scale * (2.0 * reach * self.sinP + (H_MAX + SLAB_T) * self.cosP)
        self.oy = (h - used) / 2.0 + self.scale * (reach * self.sinP + H_MAX * self.cosP)

    def project(self, x, y, z=0.0):
        cx = x * self.cosY - y * self.sinY
        cy = x * self.sinY + y * self.cosY
        return (self.ox + self.scale * cx,
                self.oy + self.scale * (cy * self.sinP - z * self.cosP))

    # Straight back out again, onto the playing surface. Exact, because the surface is
    # the one plane the projection can be inverted on without knowing anything else, and
    # sin(pitch) is never smaller than sin(15 degrees) so it is always safe to divide by.
    def ground(self, sx, sy):
        cx = (sx - self.ox) / self.scale
        cy = (sy - self.oy) / (self.scale * self.sinP)
        return (cx * self.cosY + cy * self.sinY,
                -cx * self.sinY + cy * self.cosY)

    # A step of `d` cells straight down the screen, given as a step across the board. The
    # surface's own down-the-screen direction, so a thing put here is genuinely lying on
    # the board and yet is always on the near side of whatever it belongs to.
    def alongV(self, d):
        return (d * self.sinY, d * self.cosY)

    def square(self, sx, sy):
        x, y = self.ground(sx, sy)
        i = int(math.floor(x + 3.5))
        j = int(math.floor(y + 3.5))
        if i < 0 or i > 6 or j < 0 or j > 6: return None
        return j * 7 + i + 1

    # How near the camera a point is. Bigger is nearer, so drawing in ascending order of
    # this puts the far things down first.
    def depth(self, x, y, z=0.0):
        cy = x * self.sinY + y * self.cosY
        return cy * self.cosP + z * self.sinP

    def poly(self, pts, z=0.0):
        flat = []
        for x, y in pts:
            sx, sy = self.project(x, y, z)
            flat.append(sx)
            flat.append(sy)
        return flat

    # The bounding box of a circle lying flat -- always square-on to the screen, whatever
    # the yaw, since turning a circle about its centre leaves it where it was.
    def disc(self, x, y, z, r):
        sx, sy = self.project(x, y, z)
        rx = r * self.scale
        ry = r * self.scale * self.sinP
        return (sx - rx, sy - ry, sx + rx, sy + ry)

    # The scale for things painted on the surface: full length across the screen, squashed
    # down it, and never squashed past the floor.
    def planeScale(self):
        return (self.scale, self.scale * self.squash)

    # Icons grow a little as the board flattens, so that what is lost in height is partly
    # given back in width and an icon keeps roughly the area it had. This applies to the
    # icons themselves and not to the gaps between them -- spreading the row as well would
    # walk it off the edge of the lid it is painted on.
    def iconGrow(self):
        return 1.0 + 0.45 * (1.0 - self.sinP)


####### the board's own two-line bevel, projected #######

# What plate() is for a rectangle, this is for the four-sided figure a square becomes once
# it has been turned. The light stays where it always was -- top left of the screen, the
# corner every other bevel in this window assumes -- rather than being fixed to a corner
# of the board and swinging round with it. A lamp bolted to the board would be the honest
# thing and it looks wrong: the panel beside it is lit from the top left and does not
# move, and the two have to agree or the board reads as a picture pasted on rather than
# as part of the same cabinet.
def polyPlate(c, view, pts, z, fill, hi, lo, raised=True, tags=None):
    flat = view.poly(pts, z)
    tags = tags or ()

    if not view.detail:
        c.create_polygon(flat, fill=fill, outline=lo, width=LINE_W["hair"], tags=tags)
        return

    c.create_polygon(flat, fill=fill, outline="", tags=tags)

    n = len(flat) // 2
    corners = [(flat[i * 2], flat[i * 2 + 1]) for i in range(n)]

    # which way round the corners came out, so "outward" means outward
    area = 0.0
    for i in range(n):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    wind = 1.0 if area > 0 else -1.0

    top, bottom = (hi, lo) if raised else (lo, hi)
    for i in range(n):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % n]
        # outward normal of this edge, in screen terms
        nx = (y1 - y0) * wind
        ny = -(x1 - x0) * wind
        c.create_line(x0, y0, x1, y1, width=LINE_W["hair"],
                      fill=top if (nx + ny) < 0 else bottom, tags=tags)


####### arrows lying in the board #######
# Two things want to draw a run along the board: a break offering to scatter a stack a
# certain distance, and a record of the distance a piece has just travelled. They are the
# same figure at two weights, so they are one function -- a wide grey one and a thin black
# one cannot drift apart in style if there is only one of them.

# The board wraps, and a run along it can leave one edge and arrive at the other. Drawn
# straight, that is a line across the middle of the board saying a piece went somewhere it
# never went. So a run is cut where it leaves the playfield and continued from the far side:
# this returns the pieces it falls into, each as
#
#     (ox, oy, u0, u1, final)
#
# where the leg's points are (ox + dx*u, oy + dy*u) and `u` keeps running across the cut, so
# the caller measures distance along the whole run rather than per piece. Only the last leg
# is the one that arrives, and only it gets a head.
#
# `u` counts steps of (dx, dy) and not cells: a diagonal step is longer than an orthogonal
# one, which is why everything measured in cells is divided through by the step's length
# before it gets here.
def rayLegs(ax, ay, dx, dy, u0, u1):
    edgeX = 3.5 if dx > 0 else -3.5
    edgeY = 3.5 if dy > 0 else -3.5

    legs = []
    ox, oy, u = ax, ay, u0
    # A run can be eleven squares long and lap the board more than once; the bound is a
    # backstop against a zero direction slipping in, not a real limit.
    for _ in range(24):
        cutU = u1
        for d, o, edge in ((dx, ox, edgeX), (dy, oy, edgeY)):
            if not d: continue
            at = (edge - o) / d
            if u + 1e-6 < at < cutU: cutU = at

        final = cutU >= u1 - 1e-6
        legs.append((ox, oy, u, min(cutU, u1), final))
        if final: break

        # Step the leg's own origin a whole board back along whichever axis just left it,
        # so the same `u` carries on from the opposite edge. Both, at a corner.
        if dx and abs((edgeX - ox) / dx - cutU) < 1e-6: ox -= 7.0 * dx
        if dy and abs((edgeY - oy) / dy - cutU) < 1e-6: oy -= 7.0 * dy
        u = cutU

    return legs


# One leg as a flat polygon in board coordinates: a rectangle, or the whole arrow if this
# is the leg that arrives. The width is taken perpendicular to the run and normalised, so a
# diagonal arrow is as thick as an orthogonal one rather than half again as thick.
def arrowLeg(ox, oy, dx, dy, u0, u1, s, b, head, final):
    span = math.hypot(dx, dy)
    px, py = -dy / span, dx / span

    def at(u, w):
        return (ox + dx * u + px * w, oy + dy * u + py * w)

    if not final:
        return [at(u0, s), at(u1, s), at(u1, -s), at(u0, -s)]

    # A run cut just short of its end leaves too little of the last leg for a head; it takes
    # what there is rather than growing backwards off the edge it just came through.
    tip = max(u0, u1 - head)
    return [at(u0, s), at(tip, s), at(tip, b), at(u1, 0.0),
            at(tip, -b), at(tip, -s), at(u0, -s)]


# The arrow itself. `halo` is the pale casing every piece on this board is drawn over: an
# arrow crosses light squares and dark ones in the same breath, so no single fill can be
# trusted to show up, and the casing is what makes one shade work on both.
# `edgeW` is not a detail. An outline is drawn half in and half out of the shape, so two
# pixels of it eat one off each side of the shaft -- which a wide arrow never notices and a
# thin one is entirely made of. Drawn at 2 the narrow arrow came out white: the pale edge
# had covered the black it was supposed to be outlining.
def planeArrow(c, view, legs, dx, dy, z, s, b, head, fill, edge, halo, casing,
               edgeW=2, tags=None):
    tags = tags or ()
    span = math.hypot(dx, dy)
    px, py = -dy / span, dx / span

    for ox, oy, u0, u1, final in legs:
        if u1 - u0 < 1e-6: continue
        flat = view.poly(arrowLeg(ox, oy, dx, dy, u0, u1, s, b, head, final), z)
        if view.detail:
            c.create_polygon(flat, fill=halo, outline=halo, width=casing, tags=tags)
        c.create_polygon(flat, fill=fill, outline=edge, width=edgeW, tags=tags)

        # A cut end gets a bar across it, so the reader sees the run leave the board rather
        # than stop at the edge of it.
        if not final:
            bar = view.poly([(ox + dx * u1 + px * b, oy + dy * u1 + py * b),
                             (ox + dx * u1 - px * b, oy + dy * u1 - py * b)], z)
            c.create_line(bar, fill=edge, width=LINE_W["piece"], tags=tags)


# A piece. The axis of a cylinder is always straight up the screen however the board is
# turned, so this needs no polygons at all: the bottom rim, the wall, and the lid. The
# wall is banded rather than shaded because there is no alpha here and never was -- the
# three hard stripes are the same trick the bevels play, and at this size the eye reads
# them as a curve.
def chip(c, view, x, y, z0, r, h, walls, topFill, topEdge, w=2, tags=None):
    wallMid, wallHi, wallLo, wallEdge = walls
    tags = tags or ()
    px, base = view.project(x, y, z0)
    lid = base - h * view.scale * view.cosP
    rx = r * view.scale
    ry = r * view.scale * view.sinP

    c.create_oval(px - rx, base - ry, px + rx, base + ry,
                  fill=wallMid, outline="", tags=tags)

    if view.detail:
        band = rx * 0.35
        c.create_rectangle(px - rx, lid, px - band, base, fill=wallHi, outline="", tags=tags)
        c.create_rectangle(px - band, lid, px + band, base, fill=wallMid, outline="", tags=tags)
        c.create_rectangle(px + band, lid, px + rx, base, fill=wallLo, outline="", tags=tags)
    else:
        c.create_rectangle(px - rx, lid, px + rx, base, fill=wallMid, outline="", tags=tags)

    # the silhouette: the two sides of the wall, and the half of the foot that shows below
    # it. An arc rather than the whole ellipse, because the other half is inside the chip.
    hair = LINE_W["hair"]
    c.create_line(px - rx, lid, px - rx, base, fill=wallEdge, width=hair, tags=tags)
    c.create_line(px + rx, lid, px + rx, base, fill=wallEdge, width=hair, tags=tags)
    c.create_arc(px - rx, base - ry, px + rx, base + ry, start=180, extent=180,
                 style="arc", outline=wallEdge, width=hair, tags=tags)

    c.create_oval(px - rx, lid - ry, px + rx, lid + ry,
                  fill=topFill, outline=topEdge, width=w, tags=tags)
    return lid


# The outline of a convex solid, from the corners it is made of. Every point of the solid
# is a mix of its corners, so every point of its shadow is a mix of theirs, and the hull of
# the shadows is exactly the shape a silhouette has to be -- no hidden-edge test needed and
# none of the seams that come of stroking each face and hoping. Monotone chain, on nine
# points, twice a frame; anything cleverer would be a way of spending thought to save
# nothing.
def screenHull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3: return list(pts)

    def side(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0.0: break
                out.pop()
            out.append(p)
        return out[:-1]

    return side(pts) + side(pts[::-1])


# The dragon: an obelisk, drawn face by face. Unlike the chip this one has to be built in
# the world and projected rather than laid out on the screen -- a round piece looks the
# same from every side and can be drawn straight in screen terms, but a square-plan one
# turns with the board, and turning is most of what makes it read as a standing object.
#
# Eight faces: the four sides of the shaft, and the four of the pyramidion on top. They go
# down in order of how near the camera their middles are, and for a convex solid that is
# all the hidden-surface removal there is to do -- whatever is in front is drawn last and
# covers what it should. The silhouette goes over the top afterwards, as the hull of the
# corners, which is both the outline of the piece and every arris the eye should see.
#
# Each face is flat, so each takes one flat shade, which is the honest thing a facet does
# and a nicer thing than a cylinder can manage: the shade is how squarely the face turns
# to the light the whole cabinet is lit from, up at the top left. The two faces meeting at
# the front arris always take different shades, and that difference is what says obelisk
# from across the room, at every pitch, including the ones where a cone gave up and became
# a disc. Nothing is painted on it -- there is no lid to put an icon on, and it needs none.
def obelisk(c, view, x, y, z0, r, h, walls, w=2, tags=None):
    wallMid, wallHi, wallLo, wallEdge = walls
    tags = tags or ()

    # `r` is the corner-to-corner half-width; `half` is the flat-to-flat one, which is what
    # the plan is actually built from and the figure to compare against a square, since the
    # foot and the square it stands on are aligned and a foot 2*half across fits a square
    # 1.00 across whatever its diagonal does.
    half = r / ROOT2
    plan = ((1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0), (1.0, -1.0))
    neckZ = z0 + h * (1.0 - DRAGON_CAP)

    foot = [(x + half * u, y + half * v, z0) for u, v in plan]
    neck = [(x + half * DRAGON_TAPER * u, y + half * DRAGON_TAPER * v, neckZ) for u, v in plan]
    tip = (x, y, z0 + h)

    footS = [view.project(*p) for p in foot]
    neckS = [view.project(*p) for p in neck]
    tipS = view.project(*tip)

    # A face's outward direction in plan, turned and squashed the way the board is, is its
    # normal as the screen sees it -- which is all the light needs to know. The lamp leans
    # mostly across the screen and only a little down it: weighted evenly, a face pointing
    # straight at the camera would come out as dark as one pointing away, since at a low
    # pitch those two project to nearly the same direction and only the leaning separates
    # them. `up` is the pyramidion, which tilts back into the light and is lifted for it.
    def shade(du, dv, up):
        if not view.detail: return wallMid
        sx = du * view.cosY - dv * view.sinY
        sy = (du * view.sinY + dv * view.cosY) * view.sinP
        n = math.hypot(sx, sy)
        lit = -(0.85 * sx + 0.35 * sy) / n if n else 0.0
        if up: lit = 0.30 + 0.55 * lit
        if lit >= 0.0: return mixShade(wallMid, wallHi, min(1.0, lit))
        return mixShade(wallMid, wallLo, min(1.0, -lit))

    def depthOf(ws):
        return sum(view.depth(*p) for p in ws) / float(len(ws))

    faces = []
    for i in range(4):
        j = (i + 1) % 4
        du = 0.5 * (plan[i][0] + plan[j][0])
        dv = 0.5 * (plan[i][1] + plan[j][1])
        faces.append((depthOf((foot[i], foot[j], neck[j], neck[i])),
                      (footS[i], footS[j], neckS[j], neckS[i]), shade(du, dv, False)))
        faces.append((depthOf((neck[i], neck[j], tip)),
                      (neckS[i], neckS[j], tipS), shade(du, dv, True)))
    faces.sort(key=lambda f: f[0])

    shell = screenHull(footS + neckS + [tipS])
    flat = []
    for sx, sy in shell:
        flat.append(sx)
        flat.append(sy)

    # The hull laid down first as well as last. The faces meet along shared edges, and a
    # hairline of board coming through one of those seams reads as a crack in the piece;
    # filling the whole outline once underneath means there is nothing behind it to show.
    if len(shell) >= 3:
        c.create_polygon(flat, fill=wallMid, outline="", tags=tags)

    for _, pts, fill in faces:
        corners = []
        for sx, sy in pts:
            corners.append(sx)
            corners.append(sy)
        c.create_polygon(corners, fill=fill, outline=wallEdge, width=LINE_W["hair"],
                         tags=tags)

    if len(shell) >= 3:
        c.create_polygon(flat, fill="", outline=wallEdge, width=w, tags=tags)
    return tipS[1]


####### the icons, lying flat on whatever they are painted on #######
# `ax,ay,az` is the world point the group is anchored to, and `du,dv` are offsets from it
# measured in the surface's own two directions. Along U the projection does nothing at
# all, so a row laid out that way runs straight across the screen at full size at every
# angle; only V shortens, and only the shapes themselves squash. That is the whole of the
# work -- there is no rotation and no shear to do, and the icons are the same icons.

def planeShape(c, view, shape, ax, ay, az, du, dv, r, fill, edge, w=2, tags=None):
    px, py = view.project(ax, ay, az)
    sx, sy = view.planeScale()
    grow = view.iconGrow()
    return shapeAt(c, shape, px + du * sx, py + dv * sy,
                   r * sx * grow, r * sy * grow, fill, edge, w, tags)


# `halo` is what the flat pieces always laid down under themselves so that neither side
# ever inverted against the square it was standing on. On the lid of a chip it is not
# wanted: the lid is already that pale ground, for exactly the same reason, and a halo on
# top of it is a fatter shape and nothing else.
def planePiece(c, view, shape, ax, ay, az, du, dv, r, side, w=2, tags=None, halo=False):
    if halo and view.detail:
        planeShape(c, view, shape, ax, ay, az, du, dv, r,
                   PIECE_HALO, PIECE_HALO, w + 3, tags)
    planeShape(c, view, shape, ax, ay, az, du, dv, r,
               pieceFill(side), INK, w, tags)


def planeRow(c, view, shapes, ax, ay, az, du, dv, r, gap, side, w=2, tags=None, halo=False):
    if not shapes: return
    step = r * 2 + gap
    start = du - (len(shapes) - 1) * step / 2.0
    for i, shape in enumerate(shapes):
        planePiece(c, view, shape, ax, ay, az, start + i * step, dv, r, side, w, tags, halo)


####### controls, since the native ones won't wear any of this #######

class StoneButton(tk.Label):
    def __init__(self, parent, text, command, font=None, width=None, **kw):
        tk.Label.__init__(self, parent, text=text, font=font or FONT["button"],
                          bg=PANEL_LT, fg=TEXT, bd=3, relief="raised",
                          padx=16, pady=6, **kw)
        if width: self.configure(width=width)
        self.command = command
        self.enabled = True
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)

    def press(self, event):
        if self.enabled: self.configure(relief="sunken", bg=PANEL)

    def release(self, event):
        if not self.enabled: return
        self.configure(relief="raised", bg=PANEL_LT)
        if self.command: self.command()

    def setEnabled(self, on):
        self.enabled = on
        self.configure(fg=TEXT if on else TEXT_DIM,
                       bg=PANEL_LT if on else PANEL)


# One line of a group: a marker, then its label. `value` None makes it a checkbox that
# toggles its variable between 0 and 1; anything else makes it a radio that sets the
# variable to that value. The markers are the ones the era used, drawn in mono so the
# text after them lines up whichever way they are showing.
#
# Every line reads in ink, and the row for the white side is why that is written down. It
# used to be lettered in the white the pieces are drawn in, which against this panel is
# 1.06 to 1 -- a label the eye cannot find at all, on the one control a player has to read
# before the game starts. A side is said here, the way the log and the status line say it,
# and shown only where there is a board to show it on.
class StoneChoice(tk.Frame):
    def __init__(self, parent, text, variable, value=None, command=None):
        tk.Frame.__init__(self, parent, bg=PANEL)

        self.variable = variable
        self.value = value
        self.command = command
        self.enabled = True

        self.mark = tk.Label(self, font=FONT["mark"], bg=PANEL, fg=TEXT_KEY, padx=0)
        self.mark.pack(side="left")
        self.label = tk.Label(self, text=" " + text, font=FONT["body"], bg=PANEL,
                              fg=TEXT, anchor="w")
        self.label.pack(side="left", fill="x", expand=True)

        for widget in (self, self.mark, self.label):
            widget.bind("<Button-1>", self.click)

        variable.trace_add("write", lambda *a: self.refresh())
        self.refresh()

    def chosen(self):
        if self.value is None: return bool(self.variable.get())
        return self.variable.get() == self.value

    def click(self, event):
        if not self.enabled: return
        if self.value is None: self.variable.set(0 if self.variable.get() else 1)
        else: self.variable.set(self.value)
        if self.command: self.command()

    def refresh(self):
        on = self.chosen()
        if self.value is None: self.mark.configure(text="[X]" if on else "[ ]")
        else: self.mark.configure(text="(*)" if on else "( )")

        if not self.enabled:
            # The whole row dims to one colour. The marker used to be drawn in PANEL_LT --
            # a shade *lighter* than the panel it sits on, 1.06 to 1 -- which is not a muted
            # control but an absent one: the two boxes in the side panel start disabled, so
            # what a player saw there was a label with nothing in front of it. Off is a
            # state a control can be in and still be read.
            self.mark.configure(fg=TEXT_DIM)
            self.label.configure(fg=TEXT_DIM)
        else:
            self.mark.configure(fg=TEXT_KEY if on else TEXT_DIM)
            self.label.configure(fg=TEXT)

    def setEnabled(self, on):
        self.enabled = on
        self.refresh()


# A carved trough with a brass fill and a handle, standing in for tk.Scale.
class StoneSlider(tk.Canvas):
    def __init__(self, parent, variable, width=330, height=30, low=0, high=100):
        tk.Canvas.__init__(self, parent, width=width, height=height, bg=PANEL,
                           highlightthickness=0)
        self.variable = variable
        self.low = low
        self.high = high
        self.w = width
        self.h = height
        self.enabled = True

        self.bind("<Button-1>", self.drag)
        self.bind("<B1-Motion>", self.drag)
        variable.trace_add("write", lambda *a: self.redraw())
        self.redraw()

    # the travel the handle's centre has, inset so it never hangs off either end
    def span(self):
        return 12, self.w - 12

    def drag(self, event):
        if not self.enabled: return
        x0, x1 = self.span()
        t = (event.x - x0) / float(x1 - x0)
        t = max(0.0, min(1.0, t))
        self.variable.set(int(round(self.low + t * (self.high - self.low))))

    def redraw(self):
        self.delete("all")
        x0, x1 = self.span()
        mid = self.h // 2

        # the trough, cut in
        plate(self, x0 - 8, mid - 5, x1 + 8, mid + 5, WELL, EDGE_LT, INK, 2, False)

        t = (self.variable.get() - self.low) / float(self.high - self.low)
        handle = x0 + t * (x1 - x0)

        if t > 0:
            self.create_rectangle(x0 - 6, mid - 3, handle, mid + 3,
                                  fill=RULE if self.enabled else EDGE, outline="")

        plate(self, handle - 7, mid - 11, handle + 7, mid + 11,
              PANEL_LT if self.enabled else PANEL, EDGE_LT, INK, 2, True)

    def setEnabled(self, on):
        self.enabled = on
        self.redraw()


def sideName(contr):
    return "Black" if contr else "White"


# What a position in a record is called: "Move 7", or "Entering 3 of 12" during the opening.
#
# Neither is the ply number the slider is indexed by, and that is why this exists. A record
# counts the twelve placements as plies 1 to 12, so the seventh move of a game is ply 19 --
# the right handle for code, and no help to somebody trying to say where they have got to.
# `phase` and `turn` come off the Position, which got them from record.turn_of_ply, so this
# is formatting and not a second opinion about the numbering.
def reviewLabel(spot, enterSteps):
    if not spot.turn:
        return "Start"
    if spot.phase == Record.PHASE_ENTERING:
        return "Entering %d of %d" % (spot.turn, enterSteps)
    return "Move %d" % spot.turn


class RoyalsWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("Royals")
        self.root.configure(bg=PANEL)

        # families can only be asked about once there is a root
        initFonts()

        # Where the board is being looked at from, and everything that goes with turning
        # it. These are set up before any window is built because a Configure event can
        # reach the canvas before startGame has made a board for it to show -- viewReady
        # is what holds the drawing off until there is something to draw.
        self.view = View()
        self.viewReady = False
        self.viewW = BOARD_W
        self.viewH = BOARD_H
        self.dragging = False
        self.dragFrom = (0, 0)
        self.dragBase = (YAW_DEF, PITCH_DEF)
        self.swallowRelease = False
        self.redrawPending = False

        # Which game is the current one, as a number that only ever goes up. Anything this
        # window hands to the event loop -- a search, a pause -- carries the number the game
        # had when it was asked for, and is dropped on arrival if the number has moved on.
        # See pollAI for what goes wrong without it.
        self.gameGen = 0

        # What the window is wearing, before anything is built in it -- a widget copies its
        # colours in when it is made, so the theme has to be on before the first one exists.
        #
        # Nothing here may stop the game starting. A configuration file that is missing,
        # unreadable, or names a theme that has since been deleted or edited into nonsense is
        # a reason to open in the default look and say so; it is not a reason to fail to open.
        # The note is kept rather than printed because there is no log to print it into yet.
        self.themeNote = None
        try:
            applyTheme(Theme.readConfig())
        except FileNotFoundError:
            pass                # nobody has chosen a theme yet, which is the common case
        except (Theme.ThemeError, OSError, ValueError) as error:
            self.themeNote = ("Could not use the saved theme (%s). Showing the default."
                              % (error,))

        self.frame = None
        self.buildSetup()

    ################################################################################
    ####### SETUP ##################################################################
    ################################################################################
    # The questions MainPlay asks before the game starts -- mode, side, depth, entering
    # noise -- as widgets. Same defaults it uses, and the same recommendations in the
    # labels.

    # A group of options, cut into the panel with a brass legend across its top edge.
    def carvedBox(self, parent, legend):
        box = tk.LabelFrame(parent, text="  " + legend + "  ", font=FONT["legend"],
                            bg=PANEL, fg=RULE_LT, bd=3, relief="ridge",
                            labelanchor="nw", padx=14, pady=10)
        box.pack(fill="x", pady=(0, 14))
        return box

    def buildSetup(self):
        if self.frame: self.frame.destroy()

        # Whatever was being played is over as far as this window is concerned, whether it
        # finished or the player walked away from it mid-search.
        self.gameGen += 1

        # And so is whatever was being reviewed. Every review handler already declines to do
        # anything when this is None, so dropping it here is what makes the arrow keys -- which
        # are bound to the root and therefore still live on this screen -- no-ops rather than
        # eight TclErrors against the widgets buildGame's frame took with it. The review was
        # of a game that no longer exists; there is nothing to guard, only something to forget.
        self.review = None

        # the setup screen is a column of controls and wants no more room than it asks for
        self.viewReady = False
        self.root.resizable(False, False)
        # And it wants the floor back. buildGame raises it to 820x620 for the board's sake and
        # used to be the only thing that ever touched it, so a window that had held one game
        # kept the board's minimum for the rest of the session -- on a screen with no board on
        # it. Nothing looked wrong; the window simply would not get smaller again.
        self.root.minsize(1, 1)
        self.root.geometry("")

        self.frame = tk.Frame(self.root, bg=PANEL, padx=30, pady=26)
        self.frame.pack(fill="both", expand=True)

        # The title goes on a canvas rather than in a Label so the lettering can carry the
        # same cut shadow as the board does.
        head = tk.Canvas(self.frame, width=430, height=104, bg=PANEL, highlightthickness=0)
        head.pack(anchor="w", pady=(0, 18))
        plate(head, 0, 0, 429, 103, EDGE, EDGE_LT, EDGE_DK, 3, True)
        plate(head, 8, 8, 421, 95, PANEL, EDGE_DK, EDGE_LT, 2, False)
        engrave(head, 26, 40, "ROYALS", FONT["title"], TEXT, EDGE_LT, anchor="w")
        engrave(head, 28, 74, "Gather your spy, four pawns and royal onto one square.",
                FONT["sub"], TEXT_DIM, EDGE_LT, anchor="w")

        self.modeVar = tk.IntVar(value=1)
        self.sideVar = tk.IntVar(value=0)
        self.entryVar = tk.IntVar(value=0)
        self.depthVar = tk.IntVar(value=DEPTH_DEFAULT)
        self.noiseVar = tk.IntVar(value=50)

        # mode
        box = self.carvedBox(self.frame, "PLAYERS")
        for value, text in ((0, "2 player  —  both sides at the keyboard"),
                            (1, "1 player  —  you against the computer"),
                            (2, "0 player  —  the computer against itself")):
            StoneChoice(box, text, self.modeVar, value,
                        command=self.refreshSetup).pack(fill="x", pady=1)

        # side
        self.sideBox = self.carvedBox(self.frame, "YOUR SIDE")
        self.sideButtons = []
        # The hollow and filled squares are the ones setStatus puts in front of whose turn
        # it is, so the same two marks mean the same two sides everywhere a player reads
        # them. They carry the colour; the lettering does not have to.
        for value, text in ((0, "□  White  —  enters first"),
                            (1, "■  Black  —  moves first")):
            b = StoneChoice(self.sideBox, text, self.sideVar, value)
            b.pack(fill="x", pady=1)
            self.sideButtons.append(b)

        # entering. Its own box rather than a line in COMPUTER below, because it governs
        # every side's placements in every mode -- including a 2 player game, where there is
        # no computer at all and the box below is empty of anything that applies.
        self.entryBox = self.carvedBox(self.frame, "ENTERING")
        for value, text in ((0, "Players choose their squares"),
                            (1, "Squares chosen at random  —  the placement rules still apply")):
            StoneChoice(self.entryBox, text, self.entryVar, value,
                        command=self.refreshSetup).pack(fill="x", pady=1)

        # depth
        self.aiBox = self.carvedBox(self.frame, "COMPUTER")

        tk.Label(self.aiBox, text="Search depth", bg=PANEL, fg=TEXT,
                 font=FONT["body"], anchor="w").pack(fill="x")

        # A spinbox is a native control here and would not take any of this palette. Ten is
        # the whole range, so it costs nothing to lay it out and gains a control that
        # matches everything around it.
        #
        # The range used to stop at six because seven was ten seconds of waiting. With the
        # compiled engine seven is a fifth of a second, so the old ceiling was cutting off
        # the settings a player would actually want.
        #
        # Two rows of five rather than one of ten, and that is about the window rather than
        # about the settings: laid out in a line, ten buttons come to 486 pixels and drag
        # the whole setup screen ninety wider than the title plate above them, which is
        # fixed. Five to a row leaves the column the width it was drawn to be.
        self.depthButtons = []
        for start in (1, 6):
            row = tk.Frame(self.aiBox, bg=PANEL)
            row.pack(fill="x", pady=(2, 0))
            for value in range(start, start + 5):
                b = StoneChoice(row, str(value), self.depthVar, value)
                b.pack(side="left", padx=(0, 8))
                self.depthButtons.append(b)

        # Which advice is true depends on which engine is answering, and the difference is
        # a factor of thirty-six -- large enough that one sentence cannot serve both. Saying
        # "7 to 9" on a machine with no wheel would be recommending a minute-long wait.
        tk.Label(self.aiBox, text=DEPTH_ADVICE,
                 bg=PANEL, fg=TEXT_DIM, font=FONT["small"], wraplength=400,
                 justify="left", anchor="w").pack(fill="x", pady=(0, 8))

        noiseRow = tk.Frame(self.aiBox, bg=PANEL)
        noiseRow.pack(fill="x", pady=(4, 0))
        tk.Label(noiseRow, text="Variety in the computer's entering", bg=PANEL, fg=TEXT,
                 font=FONT["body"], anchor="w").pack(side="left")
        self.noiseValue = tk.Label(noiseRow, bg=PANEL, fg=TEXT_KEY, font=FONT["mark"])
        self.noiseValue.pack(side="right")
        self.noiseVar.trace_add("write", lambda *a: self.showNoise())
        self.showNoise()

        self.noiseScale = StoneSlider(self.aiBox, self.noiseVar)
        self.noiseScale.pack(anchor="w")

        tk.Label(self.aiBox, text="0 opens the same way every game; 100 puts every piece on a "
                                  "square drawn at random. The seed is logged, so an opening "
                                  "worth seeing again can be played again.",
                 bg=PANEL, fg=TEXT_DIM, font=FONT["small"], wraplength=400,
                 justify="left", anchor="w").pack(fill="x")

        start = tk.Frame(self.frame, bg=PANEL)
        start.pack(anchor="w", pady=(4, 0))
        StoneButton(start, "START GAME", self.startGame).pack(side="left")
        # A game saved from here, or downloaded from the browser -- they are the same file.
        StoneButton(start, "OPEN A SAVED GAME", self.openGame,
                    font=FONT["small"]).pack(side="left", padx=(12, 0))
        StoneButton(start, "APPEARANCE", self.buildAppearance,
                    font=FONT["small"]).pack(side="left", padx=(12, 0))

        self.refreshSetup()

    def showNoise(self):
        self.noiseValue.configure(text="%3d" % self.noiseVar.get())

    # Side only means something in a 1 player game, and there is no computer to configure
    # in a 2 player one. The variety slider goes further than that: it feeds chooseEntry,
    # and a random opening never calls it, so in that mode it is a control that would do
    # nothing at all.
    def refreshSetup(self):
        for b in self.sideButtons: b.setEnabled(self.modeVar.get() == 1)

        playsItself = self.modeVar.get() != 0
        liveNoise = playsItself and not self.entryVar.get()
        for b in self.depthButtons: b.setEnabled(playsItself)
        self.noiseScale.setEnabled(liveNoise)
        self.noiseValue.configure(fg=TEXT_KEY if liveNoise else TEXT_DIM)

    ################################################################################
    ####### APPEARANCE #############################################################
    ################################################################################
    # The third screen. Everything a theme carries is edited here, against a miniature of the
    # board drawn by the same functions the board is drawn by.
    #
    # **The preview is drawn from the theme being edited, not from the palette.** That is
    # possible because polyPlate, chip and obelisk take every colour they use as an argument
    # and read no globals -- so the miniature can show a theme that has not been applied to
    # anything, and what it shows is the real drawing code rather than an impression of it.
    # It is also why the screen can offer REVERT: nothing has happened to the palette until
    # APPLY is pressed.

    # The squares the miniature shows, as (file, rank) from its own centre, and what stands
    # on them. Two stacks and a dragon: enough for the light square, the dark square, both
    # sides' chips, a lid icon and the piece that is drawn a different way entirely.
    PREVIEW = ((-1, -1, 0, 3), (0, -1, None, 0), (1, -1, 1, 1),
               (-1, 0, None, 0), (0, 0, 0, 1), (1, 0, None, 0),
               (-1, 1, 1, 2), (0, 1, None, 0), (1, 1, "dragon", 0))

    def buildAppearance(self):
        if self.frame: self.frame.destroy()

        # Leaving a game for this screen is leaving it: the generation bump is what stops an
        # in-flight search landing on a board that is no longer on screen, and review state
        # has to go for the same reason buildSetup drops it -- the arrow keys are bound to
        # the root and are live on every screen.
        self.gameGen += 1
        self.review = None
        self.viewReady = False

        # buildGame's floor is never lifted anywhere else, so a window that has held a game
        # cannot shrink again until something says so. This screen is narrower than a board.
        self.root.resizable(False, False)
        self.root.minsize(1, 1)
        self.root.geometry("")

        # The theme being edited, and the one to go back to. Copied, not referenced: REVERT
        # has to have something that the swatch buttons cannot have already changed.
        self.pending = currentTheme()
        self.reverting = currentTheme()

        self.frame = tk.Frame(self.root, bg=PANEL, padx=26, pady=22)
        self.frame.pack(fill="both", expand=True)

        head = tk.Frame(self.frame, bg=PANEL)
        head.pack(fill="x", pady=(0, 14))
        tk.Label(head, text="APPEARANCE", font=FONT["legend"], fg=RULE_LT,
                 bg=PANEL, anchor="w").pack(side="left")
        self.themeName = tk.Label(head, text=self.pending["name"], font=FONT["small"],
                                  fg=TEXT_DIM, bg=PANEL, anchor="e")
        self.themeName.pack(side="right")

        # Two columns, and they are frames rather than grid cells because carvedBox packs
        # itself into whatever it is given. Packing is per-parent, so a column each works
        # and nothing about carvedBox has to change.
        columns = tk.Frame(self.frame, bg=PANEL)
        columns.pack(fill="both", expand=True)
        left = tk.Frame(columns, bg=PANEL)
        left.pack(side="left", fill="y", anchor="n")
        right = tk.Frame(columns, bg=PANEL, padx=18)
        right.pack(side="left", fill="y", anchor="n")

        self.buildColourBox(left)
        self.buildGradientBox(left)
        self.buildLineBox(left)
        self.buildPreviewBox(right)
        self.buildPresetBox(right)

        self.refreshAppearance()

    def buildColourBox(self, parent):
        box = self.carvedBox(parent, "COLOURS")
        self.swatches = {}

        for role, label, base, _deps in Theme.ROLES:
            row = tk.Frame(box, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=PANEL, fg=TEXT, font=FONT["body"],
                     anchor="w", width=16).pack(side="left")

            swatch = tk.Label(row, text="        ", bd=2, relief="sunken",
                              bg=self.pending["colours"][base])
            swatch.pack(side="left")
            swatch.bind("<Button-1>", lambda e, r=role: self.pickColour(r))
            self.swatches[role] = swatch

            tk.Label(row, text="", bg=PANEL, fg=TEXT_DIM, font=FONT["small"],
                     width=9, anchor="w").pack(side="left", padx=(8, 0))

        tk.Label(box, text="Each of these carries its own shades with it — a lighter bevel, "
                           "a dimmer text — which are worked out from the one you pick.",
                 bg=PANEL, fg=TEXT_DIM, font=FONT["small"], wraplength=300,
                 justify="left", anchor="w").pack(fill="x", pady=(6, 0))

        # What this window has got wrong twice. Both were colours nobody could read, and
        # neither was noticed by anyone reading the source, so the number goes on the screen.
        self.contrastLabel = tk.Label(box, text="", bg=PANEL, fg=TEXT_DIM,
                                      font=FONT["small"], wraplength=300,
                                      justify="left", anchor="w")
        self.contrastLabel.pack(fill="x", pady=(6, 0))

    def buildGradientBox(self, parent):
        box = self.carvedBox(parent, "BOARD GRADIENT")

        self.gradVar = tk.IntVar(value=1 if self.pending["gradient"]["on"] else 0)
        StoneChoice(box, "A wash behind the board", self.gradVar,
                    command=self.onGradientToggle).pack(fill="x", pady=1)

        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x", pady=(4, 2))
        self.gradSwatches = {}
        for end in ("from", "to"):
            tk.Label(row, text=end, bg=PANEL, fg=TEXT, font=FONT["body"]).pack(side="left")
            swatch = tk.Label(row, text="      ", bd=2, relief="sunken",
                              bg=self.pending["gradient"][end])
            swatch.pack(side="left", padx=(4, 14))
            swatch.bind("<Button-1>", lambda e, w=end: self.pickGradient(w))
            self.gradSwatches[end] = swatch

        self.dirVar = tk.StringVar(value=self.pending["gradient"]["direction"])
        dirs = tk.Frame(box, bg=PANEL)
        dirs.pack(fill="x")
        self.dirButtons = []
        for name in Theme.DIRECTIONS:
            b = StoneButton(dirs, name, lambda n=name: self.setDirection(n),
                            font=FONT["small"])
            b.pack(side="left", padx=(0, 6))
            self.dirButtons.append((name, b))

        self.bandsVar = tk.IntVar(value=self.pending["gradient"]["bands"])
        self.bandsVar.trace_add("write", lambda *a: self.onBands())
        self.bandsScale = StoneSlider(box, self.bandsVar, width=280,
                                      low=Theme.BANDS_MIN, high=Theme.BANDS_MAX)
        self.bandsScale.pack(anchor="w", pady=(6, 0))
        self.bandsLabel = tk.Label(box, text="", bg=PANEL, fg=TEXT_DIM, font=FONT["small"],
                                   anchor="w")
        self.bandsLabel.pack(fill="x")

    def buildLineBox(self, parent):
        box = self.carvedBox(parent, "LINE")

        self.weightVar = tk.StringVar(value=self.weightPreset())
        for key in ("fine", "normal", "heavy"):
            StoneChoice(box, key.capitalize(), self.weightVar, key,
                        command=self.onWeight).pack(fill="x", pady=1)

        tk.Label(box, text="How heavy the outlines are: the silhouettes round pieces and "
                           "squares, and the rings that say where a stack may go.",
                 bg=PANEL, fg=TEXT_DIM, font=FONT["small"], wraplength=300,
                 justify="left", anchor="w").pack(fill="x", pady=(6, 0))

    def buildPreviewBox(self, parent):
        box = self.carvedBox(parent, "PREVIEW")
        self.previewCanvas = tk.Canvas(box, width=280, height=250, bg=PANEL,
                                       highlightthickness=0)
        self.previewCanvas.pack()
        self.previewView = View(YAW_DEF, PITCH_DEF, scale=34.0)

    def buildPresetBox(self, parent):
        box = self.carvedBox(parent, "PRESETS")

        self.presetList = tk.Frame(box, bg=PANEL)
        self.presetList.pack(fill="x")
        self.fillPresets()

        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x", pady=(10, 0))
        StoneButton(row, "SAVE AS", self.saveTheme, font=FONT["small"]).pack(side="left")
        StoneButton(row, "OPEN", self.openTheme,
                    font=FONT["small"]).pack(side="left", padx=(8, 0))

        row2 = tk.Frame(box, bg=PANEL)
        row2.pack(fill="x", pady=(8, 0))
        StoneButton(row2, "APPLY", self.applyPending).pack(side="left")
        StoneButton(row2, "REVERT", self.revertPending,
                    font=FONT["small"]).pack(side="left", padx=(8, 0))
        StoneButton(row2, "DEFAULT", self.defaultPending,
                    font=FONT["small"]).pack(side="left", padx=(8, 0))

        tk.Label(box, text="APPLY dresses the window and remembers the theme for next time. "
                           "Nothing above has touched it until then.",
                 bg=PANEL, fg=TEXT_DIM, font=FONT["small"], wraplength=300,
                 justify="left", anchor="w").pack(fill="x", pady=(8, 0))

        StoneButton(box, "BACK", self.buildSetup, font=FONT["small"]).pack(anchor="w",
                                                                           pady=(12, 0))

    def fillPresets(self):
        for child in self.presetList.winfo_children(): child.destroy()

        found = Theme.presets()
        if not found:
            tk.Label(self.presetList, text="No theme files found.", bg=PANEL, fg=TEXT_DIM,
                     font=FONT["small"], anchor="w").pack(fill="x")
            return

        for name, path in found[:8]:
            StoneButton(self.presetList, name, lambda p=path: self.loadPreset(p),
                        font=FONT["small"]).pack(fill="x", pady=1)

    ####### editing #######

    def weightPreset(self):
        for key, weights in Theme.WEIGHT_PRESETS.items():
            if weights == self.pending["lines"]: return key
        return "normal"

    def pickColour(self, role):
        for name, label, base, _deps in Theme.ROLES:
            if name != role: continue
            chosen = colorchooser.askcolor(color=self.pending["colours"][base],
                                           title=label, parent=self.root)[1]
            if not chosen: return
            self.pending["colours"][base] = chosen.lower()
            self.pending["colours"].update(Theme.derive(role, chosen.lower()))
            self.refreshAppearance()
            return

    def pickGradient(self, end):
        chosen = colorchooser.askcolor(color=self.pending["gradient"][end],
                                       title="Gradient " + end, parent=self.root)[1]
        if not chosen: return
        self.pending["gradient"][end] = chosen.lower()
        self.gradVar.set(1)
        self.refreshAppearance()

    def setDirection(self, name):
        self.dirVar.set(name)
        self.pending["gradient"]["direction"] = name
        self.refreshAppearance()

    def onGradientToggle(self):
        self.pending["gradient"]["on"] = bool(self.gradVar.get())
        self.refreshAppearance()

    def onBands(self):
        self.pending["gradient"]["bands"] = int(self.bandsVar.get())
        self.refreshAppearance()

    def onWeight(self):
        self.pending["lines"] = dict(Theme.WEIGHT_PRESETS[self.weightVar.get()])
        self.refreshAppearance()

    def refreshAppearance(self):
        """Put the screen and the miniature back in step with the theme being edited."""
        for role, _label, base, _deps in Theme.ROLES:
            self.swatches[role].configure(bg=self.pending["colours"][base])
        for end in ("from", "to"):
            self.gradSwatches[end].configure(bg=self.pending["gradient"][end])

        live = bool(self.pending["gradient"]["on"])
        for name, button in self.dirButtons:
            button.setEnabled(live and name != self.dirVar.get())
        self.bandsScale.setEnabled(live)
        self.bandsLabel.configure(
            text="%d bands" % self.pending["gradient"]["bands"] if live
                 else "The board sits on a flat colour.")

        # Which three pairs, and why not the obvious one. A white piece's *fill* against a
        # light square is 1.2 to 1 in the default palette and always has been: a pale chip is
        # read by the rim drawn round its foot, which is what that rim is for. Measuring the
        # fill would put a warning on the board this window has shipped with since the start,
        # and a warning that is always on is a warning nobody reads. So the pale piece is
        # measured by its rim, and the dark one by its body -- each by the thing that actually
        # tells it from what it is standing on.
        colours = self.pending["colours"]
        pairs = (("Lettering on the panel", colours["TEXT"], colours["PANEL"]),
                 ("A black piece on a dark square", colours["BLACK"], colours["DARK"]),
                 ("A white piece's rim on a light square",
                  colours["WHITE_RIM"], colours["LIGHT"]))
        worst = min(pairs, key=lambda row: Theme.contrast(row[1], row[2]))
        ratio = Theme.contrast(worst[1], worst[2])
        self.contrastLabel.configure(
            text="%s: %.1f to 1.%s" % (worst[0], ratio,
                                       "" if ratio >= 3.0 else "  Hard to make out."),
            fg=TEXT_DIM if ratio >= 3.0 else TEXT)

        self.themeName.configure(text=self.pending["name"])
        self.drawPreview()

    ####### the miniature #######

    def drawPreview(self):
        """A corner of a board, drawn from `self.pending` rather than from the palette."""
        c = self.previewCanvas
        c.delete("all")
        v = self.previewView
        v.detail = True

        t = self.pending["colours"]
        w = self.pending["lines"]

        width = int(c.cget("width"))
        height = int(c.cget("height"))
        gradient = self.pending["gradient"]
        if gradient["on"]:
            bands = max(Theme.BANDS_MIN, min(Theme.BANDS_MAX, int(gradient["bands"])))
            for i in range(bands):
                shade = mixShade(gradient["from"], gradient["to"],
                                 i / float(bands - 1) if bands > 1 else 0.0)
                if gradient["direction"] == "horizontal":
                    c.create_rectangle(width * i / float(bands), 0,
                                       width * (i + 1) / float(bands) + 1, height,
                                       fill=shade, outline="")
                elif gradient["direction"] == "diagonal":
                    span = (width + height) * (i + 1) / float(bands)
                    back = (width + height) * i / float(bands)
                    c.create_polygon(back, 0, span, 0, 0, span, 0, back,
                                     fill=shade, outline="")
                else:
                    c.create_rectangle(0, height * i / float(bands), width,
                                       height * (i + 1) / float(bands) + 1,
                                       fill=shade, outline="")
        else:
            c.configure(bg=t["PANEL"])

        v.ox, v.oy = width / 2.0, height / 2.0 + 34.0
        v.scale = 34.0

        walls = {0: (t["WHITE"], t["WHITE_HI"], t["WHITE_LO"], t["WHITE_RIM"]),
                 1: (t["BLACK"], t["BLACK_HI"], t["BLACK_LO"], t["INK"])}

        # Far to near, the same rule the board's own pass follows.
        for wx, wy, side, count in sorted(self.PREVIEW, key=lambda s: v.depth(s[0], s[1])):
            dark = (wx + wy) % 2 == 0
            face = (t["DARK"], t["DARK_HI"], t["DARK_LO"]) if dark else \
                   (t["LIGHT"], t["LIGHT_HI"], t["LIGHT_LO"])
            polyPlate(c, v, [(wx - 0.5, wy - 0.5), (wx + 0.5, wy - 0.5),
                             (wx + 0.5, wy + 0.5), (wx - 0.5, wy + 0.5)],
                      0.0, face[0], face[1], face[2], True)

            if side == "dragon":
                obelisk(c, v, wx, wy, 0.0, R_DRAGON, H_DRAGON, walls[0], w["piece"])
                continue
            if side is None or not count:
                continue

            for k in range(count):
                chip(c, v, wx, wy, k * H_CHIP, R_CHIP, H_CHIP, walls[side],
                     t["PIECE_HALO"], t["INK"], w["piece"])

            lid = count * H_CHIP + 0.001
            icons = [SHAPE_ROYAL] if count > 2 else [SHAPE_SPY]
            planeRow(c, v, icons, wx, wy, lid, 0.0, 0.0, 0.130, 0.05,
                     side, w["piece"])

        # One mark, because a mark is a line and this box is also the line-weight preview.
        px, py = v.project(0.0, -1.0, 0.0)
        sx, sy = v.planeScale()
        c.create_oval(px - 0.43 * sx, py - 0.43 * sy, px + 0.43 * sx, py + 0.43 * sy,
                      fill="", outline=t["INK"], width=w["mark"], dash=(7, 5))

    ####### presets #######

    def loadPreset(self, path):
        try:
            self.pending = Theme.load(path)
        except (Theme.ThemeError, OSError) as error:
            messagebox.showerror("Royals", "That theme could not be read.\n\n%s" % (error,),
                                 parent=self.root)
            return
        self.syncControls()

    def openTheme(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Open a theme",
            filetypes=[("Royals theme", "*.json"), ("All files", "*")])
        if not path: return
        self.loadPreset(path)

    def saveTheme(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save this theme", defaultextension=".json",
            initialdir=str(Theme.themeDir()),
            filetypes=[("Royals theme", "*.json"), ("All files", "*")],
            initialfile="royals-theme.json")
        if not path: return

        try:
            Theme.themeDir().mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        self.pending["name"] = name[:-5] if name.endswith(".json") else name
        try:
            Theme.save(path, self.pending)
        except (Theme.ThemeError, OSError) as error:
            messagebox.showerror("Royals", "That theme could not be saved.\n\n%s" % (error,),
                                 parent=self.root)
            return
        self.fillPresets()
        self.refreshAppearance()

    def syncControls(self):
        """Put the controls back in step after the whole theme was replaced under them."""
        self.gradVar.set(1 if self.pending["gradient"]["on"] else 0)
        self.dirVar.set(self.pending["gradient"]["direction"])
        self.bandsVar.set(self.pending["gradient"]["bands"])
        self.weightVar.set(self.weightPreset())
        self.refreshAppearance()

    def revertPending(self):
        self.pending = dict(self.reverting)
        self.pending["colours"] = dict(self.reverting["colours"])
        self.pending["lines"] = dict(self.reverting["lines"])
        self.pending["gradient"] = dict(self.reverting["gradient"])
        self.syncControls()

    def defaultPending(self):
        self.pending = Theme.validate(Theme.DEFAULT)
        self.syncControls()

    def applyPending(self):
        try:
            applyTheme(self.pending)
        except Theme.ThemeError as error:
            messagebox.showerror("Royals", "That theme could not be used.\n\n%s" % (error,),
                                 parent=self.root)
            return

        Theme.writeConfig(self.pending)
        # Rebuilt rather than recoloured: a tk widget copies its colours in when it is made,
        # so the only way to re-dress this screen is to make it again. It comes back showing
        # the theme it just applied, which is also what makes APPLY visibly do something.
        self.buildAppearance()

    ################################################################################
    ####### GAME WINDOW ############################################################
    ################################################################################

    def buildGame(self):
        if self.frame: self.frame.destroy()

        # The board takes its scale from however much room it is given now, so unlike the
        # setup screen there is something to be gained by letting the window be dragged
        # bigger. The floor is the smallest board still worth looking at plus the panel,
        # which cannot usefully shrink at all.
        self.root.resizable(True, True)
        self.root.minsize(820, 620)
        self.root.geometry("")

        self.frame = tk.Frame(self.root, bg=PANEL)
        self.frame.pack(fill="both", expand=True)
        self.frame.grid_rowconfigure(0, weight=1)
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_columnconfigure(1, weight=0, minsize=310)

        # The width and height here are only what the window opens at; after that the
        # canvas takes whatever the grid gives it and the projection is refitted. Neither
        # is allowed to open larger than the screen can hold it.
        roomW = int(self.root.winfo_screenwidth() * 0.92) - 310 - 42
        roomH = int(self.root.winfo_screenheight() * 0.88) - 28
        openW = max(420, min(OPEN_W, roomW))
        openH = max(400, min(OPEN_H, roomH))

        self.canvas = tk.Canvas(self.frame, width=openW, height=openH,
                                bg=PANEL, highlightthickness=0)
        self.canvas.grid(row=0, column=0, padx=(14, 7), pady=14, sticky="nsew")
        self.canvas.bind("<ButtonPress-1>", self.onPress)
        self.canvas.bind("<B1-Motion>", self.onDrag)
        self.canvas.bind("<ButtonRelease-1>", self.onRelease)
        self.canvas.bind("<Double-Button-1>", self.onDoubleClick)
        self.canvas.bind("<Configure>", self.onResize)
        # Home means two things depending on what the window is doing, and the one that
        # applies while reviewing is the one a person pressing it there wants.
        self.root.bind("<Home>", self.onHome)
        self.root.bind("<End>", lambda e: self.reviewGoTo(1 << 30))
        self.root.bind("<Left>", lambda e: self.reviewStep(-1))
        self.root.bind("<Right>", lambda e: self.reviewStep(1))

        self.viewW = openW
        self.viewH = openH
        self.view.fit(self.viewW, self.viewH)

        # The drag flags belong to the canvas that has just been replaced. A game begun while
        # the board was being turned used to inherit dragging=True from the one before it, and
        # redraw reads that as `v.detail = not self.dragging` -- so the new board came up in
        # the low-detail form the drag uses and stayed there until somebody turned it again.
        self.dragging = False
        self.dragFrom = (0, 0)
        self.dragBase = (YAW_DEF, PITCH_DEF)
        self.swallowRelease = False
        self.redrawPending = False

        panel = tk.Frame(self.frame, bg=PANEL, width=310)
        panel.grid(row=0, column=1, sticky="nsew", padx=(7, 14), pady=14)
        panel.grid_propagate(False)

        # whose move it is, on a plate of its own so the colour has something to sit on
        self.statusPlate = tk.Frame(panel, bg=PANEL_LT, bd=3, relief="raised")
        self.statusPlate.pack(fill="x")
        self.statusLabel = tk.Label(self.statusPlate, text="", font=FONT["status"],
                                    bg=PANEL_LT, wraplength=280, justify="left",
                                    anchor="w", padx=10, pady=7)
        self.statusLabel.pack(fill="x")

        self.hintLabel = tk.Label(panel, text="", font=FONT["small"], fg=TEXT_DIM,
                                  bg=PANEL, wraplength=290, justify="left", anchor="w")
        self.hintLabel.pack(fill="x", pady=(8, 10))

        self.prisVar = tk.IntVar(value=0)
        self.prisCheck = StoneChoice(panel, "Bring prisoners along", self.prisVar,
                                     command=self.onPrisToggle)
        self.prisCheck.setEnabled(False)
        self.prisCheck.pack(fill="x", pady=1)

        # Where a push could either free the allies held on a square or shove the whole
        # square along with them still in it, both are legal and the player picks. On by
        # default: freeing is the reason you shoved a jailer in the first place, most of
        # the time. Unlike the prisoner box this doesn't change what checkMoves returns,
        # so it needs no callback -- it is read when the destination is clicked.
        self.freeVar = tk.IntVar(value=1)
        self.freeCheck = StoneChoice(panel, "Free allies rather than shove them on",
                                     self.freeVar)
        self.freeCheck.setEnabled(False)
        self.freeCheck.pack(fill="x", pady=1)


        self.buildLegend(panel)

        tk.Label(panel, text="GAME LOG", font=FONT["legend"], fg=RULE_LT,
                 bg=PANEL, anchor="w").pack(fill="x")

        # The log is sunk into the panel: a light hairline under the top edge and a dark
        # one over the bottom, which is the same bevel the board and the buttons wear.
        logBox = tk.Frame(panel, bg=WELL, bd=3, relief="sunken")
        logBox.pack(fill="both", expand=True, pady=(3, 12))
        # no scrollbar -- it is the one part tk draws natively on macOS, and the log
        # follows itself down anyway. The wheel still works over it.
        self.logText = tk.Text(logBox, width=30, height=9, font=FONT["log"],
                               wrap="word", bg=WELL, fg=TEXT_DIM,
                               relief="flat", padx=8, pady=8,
                               highlightthickness=0, insertbackground=TEXT)
        self.logText.pack(fill="both", expand=True)
        # Every line is already headed "White:" or "Black:", so in one colour the side is
        # said rather than shown and both read in ink. Only the asides are dimmed.
        self.logText.tag_configure("white", foreground=TEXT)
        self.logText.tag_configure("black", foreground=TEXT)
        self.logText.tag_configure("grey", foreground=TEXT_DIM)
        self.logText.configure(state="disabled")

        # A theme that could not be loaded at startup, said once, in the first place there
        # has been to say it. Cleared so a player is not told about it every game.
        if self.themeNote:
            self.log(self.themeNote, "grey")
            self.themeNote = None

        buttons = tk.Frame(panel, bg=PANEL)
        buttons.pack(fill="x")
        StoneButton(buttons, "NEW GAME", self.buildSetup,
                    font=FONT["small"]).pack(side="left")
        StoneButton(buttons, "RESET VIEW", self.resetView,
                    font=FONT["small"]).pack(side="left", padx=(8, 0))

        # Keeping the game and walking back through it. A second row rather than a longer
        # one: four of these do not fit across a 310px panel at this font.
        self.archiveRow = tk.Frame(panel, bg=PANEL)
        self.archiveRow.pack(fill="x", pady=(8, 0))
        StoneButton(self.archiveRow, "SAVE", self.saveGame,
                    font=FONT["small"]).pack(side="left")
        StoneButton(self.archiveRow, "REVIEW", self.startReview,
                    font=FONT["small"]).pack(side="left", padx=(8, 0))

        # The review controls stand in the same place, and only while reviewing. Built here
        # rather than on demand so the panel's height does not jump as it appears; the
        # scrubber is the one part that has to be rebuilt, because a StoneSlider takes its
        # range at construction and the range is however long the game turned out to be.
        self.reviewRow = tk.Frame(panel, bg=PANEL)
        self.reviewVar = tk.IntVar(value=0)
        self.reviewVar.trace_add("write", self.onReviewSlide)

        steps = tk.Frame(self.reviewRow, bg=PANEL)
        steps.pack(fill="x")
        for text, command in (("|<", lambda: self.reviewGoTo(0)),
                              ("<", lambda: self.reviewStep(-1)),
                              (">", lambda: self.reviewStep(1)),
                              (">|", lambda: self.reviewGoTo(1 << 30))):
            StoneButton(steps, text, command, font=FONT["small"],
                        width=2).pack(side="left", padx=(0, 6))
        StoneButton(steps, "DONE", self.exitReview,
                    font=FONT["small"]).pack(side="right")

        # Which move is on the board, directly over the scrubber. This is the number
        # somebody stepping through a game keeps their place by, so it is the one thing in
        # the row at reading size; the ply position beside it is smaller, because it is
        # what the slider is indexed by rather than what anyone would say out loud. They
        # are not the same number -- see turnMark, and record.turn_of_ply.
        markRow = tk.Frame(self.reviewRow, bg=PANEL)
        markRow.pack(fill="x", pady=(7, 1))
        self.reviewMark = tk.Label(markRow, text="", font=FONT["status"], bg=PANEL,
                                   fg=TEXT_KEY, anchor="w")
        self.reviewMark.pack(side="left")
        self.reviewPly = tk.Label(markRow, text="", font=FONT["small"], bg=PANEL,
                                  fg=TEXT_DIM, anchor="e")
        self.reviewPly.pack(side="right")

        self.reviewScale = None
        self.reviewScaleHolder = tk.Frame(self.reviewRow, bg=PANEL)
        self.reviewScaleHolder.pack(fill="x")

    # In one colour the board says everything by shape, and a shape has to be told where a
    # colour could just be seen. Drawn with the same icon functions the board uses, so the
    # key can never drift from what it is describing.
    def buildLegend(self, parent):
        c = tk.Canvas(parent, width=296, height=272, bg=PANEL, highlightthickness=0)
        c.pack(fill="x", pady=(0, 8))

        # A view of its own, fixed, so the chips below are drawn by the same code the
        # board draws its chips with, at an angle that never moves under them.
        key = View(YAW_DEF, PITCH_DEF, scale=40.0)

        def heading(y, text):
            c.create_text(0, y, text=text, font=FONT["legend"], fill=RULE_LT, anchor="w")

        def entry(x, y, draw, label, side=0):
            drawPiece(c, draw, x + 8, y, 7, side, 2)
            c.create_text(x + 22, y, text=label, font=FONT["small"], fill=TEXT_DIM,
                          anchor="w")

        # a little pile, standing on the y given rather than centred on it
        def pile(x, base, count, side, label):
            key.ox, key.oy = x + 16, base
            for k in range(count):
                chip(c, key, 0.0, 0.0, k * H_CHIP, R_CHIP, H_CHIP,
                     CHIP_WALL[side], PIECE_HALO, INK, 1)
            c.create_text(x + 38, base - 6, text=label, font=FONT["small"],
                          fill=TEXT_DIM, anchor="w")

        heading(8, "PIECES")
        entry(4, 30, iconSpy, "spy")
        entry(150, 30, iconPawn, "pawn")
        entry(4, 52, iconRoyal, "royal")

        # The dragon has left this list, because the list is of things a lid can have on
        # it and the dragon has no lid. So the key shows the piece itself, drawn by the
        # code the board draws it with and from the same fixed angle as the piles below --
        # standing on a baseline of its own rather than centred on the row, since a piece
        # that stands up has a foot and not a centre.
        #
        # Further from the eye than the piles rather than nearer, which is the reverse of
        # what it was while the piece was narrow enough to need the help. A foot nine tenths
        # of a square across is nine tenths of the drawing scale across on screen, and at
        # the piles' scale this one runs into the SIDES heading under it and into its own
        # label beside it. The key is a key: what it owes the reader is the shape, not the
        # piece's size next to a pile, which the board itself says better than a panel can.
        key.ox, key.oy = 162.0, 58.0
        key.scale = 30.0
        obelisk(c, key, 0.0, 0.0, 0.0, R_DRAGON, H_DRAGON, CHIP_WALL[0], 1)
        key.scale = 40.0
        c.create_text(186, 56, text="dragon", font=FONT["small"], fill=TEXT_DIM,
                      anchor="w")

        # The board stands its pieces up now, so the key has to as well. The shapes above
        # are what a lid can have on it; the piles below are what a square looks like from
        # the side, and which side of them it belongs to.
        heading(80, "SIDES")
        pile(4, 128, 3, 0, "white")
        pile(150, 128, 3, 1, "black")
        c.create_text(0, 146, text="one chip a piece — the lid says which",
                      font=FONT["small"], fill=TEXT_DIM, anchor="w")

        # drawn the way the board draws them: a ring round the square, solid or broken
        heading(170, "WHERE IT CAN GO")
        c.create_oval(3, 185, 21, 203, outline=INK, width=3)
        c.create_text(27, 194, text="jump", font=FONT["small"], fill=TEXT_DIM, anchor="w")
        c.create_oval(99, 185, 117, 203, outline=INK, width=3, dash=(4, 3))
        c.create_text(123, 194, text="push", font=FONT["small"], fill=TEXT_DIM, anchor="w")
        c.create_rectangle(195, 185, 213, 203, outline=INK, width=3, dash=(4, 3))
        c.create_text(219, 194, text="free", font=FONT["small"], fill=TEXT_DIM, anchor="w")

        # The two arrows, drawn by the code that draws them on the board and through a view
        # looking straight down at it, so the key shows the figure and not a foreshortened
        # version of it. A wide one is where a break would scatter; a thin one is where a
        # piece went last turn, and it is the only mark here that describes the past.
        flat = View(0.0, math.radians(90.0), scale=42.0, ox=0.0, oy=0.0)
        heading(210, "ARROWS")
        flat.ox, flat.oy = 6.0, 236.0
        planeArrow(c, flat, rayLegs(0.0, 0.0, 1.0, 0.0, 0.0, 1.5), 1.0, 0.0, 0.0,
                   BRK_SHAFT, BRK_BARB, BRK_HEAD, PANEL_DK, INK, PIECE_HALO, 5, 2)
        c.create_text(78, 236, text="break, and how far",
                      font=FONT["small"], fill=TEXT_DIM, anchor="w")
        flat.ox, flat.oy = 6.0, 258.0
        planeArrow(c, flat, rayLegs(0.0, 0.0, 1.0, 0.0, 0.0, 1.5), 1.0, 0.0, 0.0,
                   FLY_SHAFT, FLY_BARB, FLY_HEAD, INK, INK, PIECE_HALO, 4, 1)
        c.create_text(78, 258, text="moved last turn",
                      font=FONT["small"], fill=TEXT_DIM, anchor="w")

    ################################################################################
    ####### STARTING ###############################################################
    ################################################################################

    def startGame(self):
        mode = self.modeVar.get()

        if mode == 0: self.humanSides = [True, True]
        elif mode == 2: self.humanSides = [False, False]
        elif self.sideVar.get() == 0: self.humanSides = [True, False]
        else: self.humanSides = [False, True]

        self.aiDepth = max(1, int(self.depthVar.get()))

        self.buildGame()
        self.resetGameState()

        # Nobody picks their squares: the twelve placements are drawn uniformly from the
        # legal ones, both sides alike. The seed is logged for the same reason the entering
        # noise seed is -- an opening worth seeing again can be played again.
        #
        # After resetGameState rather than before it, because that is where the two of them
        # are given the defaults a game gets when nobody chose -- which is what openGame
        # relies on, having no menu to read them from.
        self.randomEntry = bool(self.entryVar.get())
        self.entrySeed = random.randrange(1 << 30) if self.randomEntry else None

        if self.randomEntry:
            # setEntryNoise is skipped outright here rather than set to zero: it only feeds
            # chooseEntry and entryShortlist, and a random opening calls neither.
            self.log("Entering at random. Seed: " + str(self.entrySeed), "grey")
        elif mode != 0:
            # The slider drives two things and the seed ties both to this game: how far the
            # Perlin field tilts the ranking, and how loosely enterVaried picks off it.
            self.entryNoise = self.noiseVar.get() / 100.0
            self.entrySeed = artificialPlayer.setEntryNoise(self.entryNoise)
            if self.entryNoise >= 1.0:
                self.log("The computer enters at random. Seed: " + str(self.entrySeed), "grey")
            elif self.entryNoise:
                self.log("Entering seed: " + str(self.entrySeed), "grey")
            else:
                self.log("Fixed opening (no entering variety).", "grey")

        self.log("ENTERING — royals first, then the four pawns, then the spies.", "grey")
        if self.randomEntry:
            self.log("Nobody chooses: every piece lands on a square drawn from the legal "
                     "ones. A royal or pawn can't enter touching something its own side "
                     "already controls; a spy goes anywhere empty.", "grey")
        else:
            self.log("A royal or pawn can't enter touching something you already control, "
                     "your dragon included. A spy goes anywhere empty.", "grey")
        self.log("Drag anywhere on the board to turn it. Double click off the play, or "
                 "RESET VIEW, to put it back.", "grey")

        # there is a board now, so the canvas may draw
        self.viewReady = True
        self.advance()

    # Everything a game window needs before anything is drawn in it. Split out of startGame
    # because opening a saved game needs the same blank slate and none of the questions
    # above it -- a record carries its own opening, so there is no seed or noise to set.
    def resetGameState(self):
        # A new game, so nothing the last one left in the event loop belongs here any more.
        self.gameGen += 1

        # What a game is played under when nobody was asked -- openGame has no menu to read.
        # startGame overwrites these immediately after calling this.
        self.randomEntry = False
        self.entrySeed = None
        self.entryNoise = 0.0

        self.board = Hasher.Entering_Board()
        self.phase = "entering"
        self.enterSteps = Engine.enteringSequence()
        self.enterIndex = 0
        self.entryOptions = []
        self.entryContr = 0
        self.entryPiece = Hasher.ROYAL
        self.turn = 0
        self.contr = 0
        self.passes = 0
        self.selected = None
        self.moveArray = []
        self.legalOrigins = set()
        self.lastMove = []
        # what the last move moved, as Engine.moveFlights reports it -- the arrows drawn
        # over the board are the only account of a computer move that doesn't need reading
        self.lastFlights = ()
        self.movingPris = False
        self.spyBreak = False
        # whether the break arrows are showing. The badge over the stack toggles it, and
        # anything that ends the selection or the turn puts it back down.
        self.breakOpen = False
        self.hasPris = False
        self.aiBusy = False

        # The game as it is played, one RAN token per ply. Until this existed the window
        # kept no account of a game at all -- it called the executors directly and wrote
        # down only boards, into the ko set -- so a game played here left nothing anybody
        # could keep, send on or read back. See recordPly.
        self.record = []
        self.review = None

        Engine.koReset()
        artificialPlayer.newGame()

    ################################################################################
    ####### THE LOOP ###############################################################
    ################################################################################
    # Everywhere MainPlay would call input(), one of these returns instead and the click
    # handler picks the game back up.

    def advance(self):
        self.redraw()

        # A search is already out on this position, and it will advance the game itself when
        # it answers. Stepping again here is what starts a second one -- two searches on one
        # position, two results, two moves played for one side. Reachable from anything that
        # can call advance twice: a pause that fires late, a click that arrives while the
        # computer is thinking.
        if self.aiBusy: return

        if self.phase == "entering": self.enterStep()
        elif self.phase == "play": self.playStep()

    ####### ENTERING #######
    def enterStep(self):
        while self.enterIndex < len(self.enterSteps):
            contr, piece = self.enterSteps[self.enterIndex]
            isSpy = (piece == Hasher.SPY)
            options = Engine.enteringOptions(self.board, contr, isSpy)

            # every square is hemmed in, so this side sits the step out
            if not options:
                self.log(self.turnMark() + sideName(contr) + " has nowhere to enter a "
                         + PIECE_NAMES[piece] + " — skipped.", "grey")
                self.recordPly(N.PASS)
                self.enterIndex += 1
                continue

            # A random opening is placed inside this one call: the branch continues rather
            # than returning, so all twelve land before the event loop gets the window back.
            # That is what keeps it free of timers -- nothing is left scheduled for a game
            # that NEW GAME may have torn down, and there is no gap between placements for a
            # click to arrive in. entryOptions is never filled either, so drawSquare
            # highlights nothing and boardClick has nothing to act on.
            if self.randomEntry:
                square = Engine.randomEntry(self.board, contr, isSpy,
                                            Engine.entryRng(self.entrySeed, self.enterIndex))
                self.board = Engine.dropPiece(self.board, square, contr, piece)
                # A dealt placement is a ply like any other: the record has to carry it, or
                # a saved random game replays every later move as the other side's.
                self.recordPly(N.encode_entry(piece, square))
                self.lastMove = [square]
                self.lastFlights = ()
                self.log(self.turnMark() + sideName(contr) + " "
                         + PIECE_NAMES[piece] + " enters at "
                         + Hasher.IndexToAlg(square - 1).upper(),
                         "black" if contr else "white")
                self.enterIndex += 1
                continue

            self.entryOptions = options
            self.entryContr = contr
            self.entryPiece = piece

            label = sideName(contr) + " " + PIECE_NAMES[piece]
            count = str(self.enterIndex + 1) + " of " + str(len(self.enterSteps))

            if self.humanSides[contr]:
                self.setStatus(label, contr)
                self.setHint("Click a green square to enter it.  (" + count + ")")
                self.redraw()
                return

            self.setStatus(label, contr)
            self.setHint("Thinking…  (" + count + ")")
            self.redraw()
            self.runAI(lambda b=self.board, c=contr, p=piece, s=isSpy,
                              n=self.entryNoise,
                              r=Engine.entryRng(self.entrySeed, self.enterIndex):
                       artificialPlayer.enterVaried(b, c, p, s, n, r), self.finishAIEntry)
            return

        self.startPlay()

    def finishAIEntry(self, square):
        contr = self.entryContr
        piece = self.entryPiece

        # chooseEntry gives back None when there is nowhere, though enterStep has already
        # checked that there is somewhere.
        if square is None:
            self.log(self.turnMark() + sideName(contr) + " has nowhere to enter a "
                     + PIECE_NAMES[piece] + " — skipped.", "grey")
            self.recordPly(N.PASS)
        else:
            self.board = Engine.dropPiece(self.board, square, contr, piece)
            self.recordPly(N.encode_entry(piece, square))
            self.lastMove = [square]
            self.lastFlights = ()
            self.log(self.turnMark() + sideName(contr) + " "
                     + PIECE_NAMES[piece] + " enters at "
                     + Hasher.IndexToAlg(square - 1).upper(),
                     "black" if contr else "white")

        self.enterIndex += 1
        self.advance()

    def enterClick(self, square):
        contr = self.entryContr

        if square in self.entryOptions:
            self.board = Engine.dropPiece(self.board, square, contr, self.entryPiece)
            self.recordPly(N.encode_entry(self.entryPiece, square))
            self.lastMove = [square]
            self.lastFlights = ()
            self.log(self.turnMark() + sideName(contr) + " "
                     + PIECE_NAMES[self.entryPiece] + " enters at "
                     + Hasher.IndexToAlg(square - 1).upper(),
                     "black" if contr else "white")
            self.enterIndex += 1
            self.advance()
            return

        # the same two reasons MainPlay gives
        if Hasher.Check_For_Occupancy(Hasher.Get_Space_Data(self.board, square)):
            self.setHint("That square is taken.")
        else:
            self.setHint("Too close to a piece you already control.")

    ####### PLAY #######
    def startPlay(self):
        # the history starts on the position entering left behind, so the first move can't
        # be undone back into it either
        Engine.koReset()
        Engine.koRecord(self.board)

        # whoever entered second opens, so the side that placed the last spy moves now
        self.turn = 1
        self.passes = 0
        self.phase = "play"
        self.selected = None

        self.log("— entering complete —", "grey")
        self.advance()

    def playStep(self):
        contr = self.turn % 2
        self.contr = contr
        self.selected = None
        self.moveArray = []
        self.breakOpen = False
        self.prisCheck.setEnabled(False)
        self.freeCheck.setEnabled(False)
        self.prisVar.set(0)

        # A side with nothing to move loses its turn. MainPlay only ever hit this for the
        # computer; a human can be stuck just as easily, so the check is made for both.
        moves = artificialPlayer.listAllMoves(self.board, contr)
        if not moves:
            self.legalOrigins = set()
            self.log(self.turnMark() + sideName(contr) + " has no legal move, passing.", "grey")
            self.recordPly(N.PASS)
            self.passes += 1
            self.turn += 1

            if self.passes > 1:
                self.finish("Neither side can move.", None)
                return

            self.redraw()
            # back through the event loop rather than straight down the stack
            self.later(400, self.advance)
            return

        self.legalOrigins = set(m[0] for m in moves)
        self.setStatus(sideName(contr) + " to move", contr)

        if self.humanSides[contr]:
            self.setHint("Click one of the outlined squares.")
            self.redraw()
            return

        self.setHint("Thinking…")
        self.redraw()
        self.runAI(lambda b=self.board, c=contr, d=self.aiDepth:
                   artificialPlayer.takeTurn(b, c, d), self.finishAIMove)

    def finishAIMove(self, result):
        board, move, score = result
        contr = self.contr

        if move is None:
            # playStep found it a move, so this can only be the ko filter having taken the
            # last one away underneath it.
            self.log(self.turnMark() + sideName(contr) + " has no legal move, passing.", "grey")
            self.recordPly(N.PASS)
            self.passes += 1
            self.turn += 1

            if self.passes > 1:
                self.finish("Neither side can move.", None)
                return

            self.later(400, self.advance)
            return

        self.log(self.turnMark() + sideName(contr) + ": "
                 + artificialPlayer.describeMove(move),
                 "black" if contr else "white")
        self.log("   score " + artificialPlayer.scoreText(score) + ", "
                 + str(artificialPlayer.calcCount) + " boards considered", "grey")

        origin = move[artificialPlayer.MOVE_ORIGIN]
        # a break has a direction where the others have a square, so the origin is the only
        # square there is to highlight
        if move[artificialPlayer.MOVE_KIND] == "break": target = origin
        else: target = move[artificialPlayer.MOVE_TARGET] + 1
        self.commit(board, [origin, target], move)

    ####### A COMPLETED MOVE #######
    # Ko, the win check and the turn counter, in the order MainPlay does them. Returns
    # whether the move stood.
    #
    # `move` is what was played, and this is the one place that can ask what it moved:
    # self.board is still the board the move was made on until a few lines down, and
    # moveFlights measures against that one. It is also where a move that ko takes back
    # returns early, which is exactly right -- a move that was taken back never happened,
    # and the arrows still describe the last one that did.
    def commit(self, board, squares, move=None):
        # A move that puts the game back into a position it has already stood in is taken
        # back and the side has another go. The computer is filtered at the root of its
        # search and never gets here; this is what stops a human doing it.
        if Engine.koBreaks(board):
            self.setHint("Ko: the game has already stood there. Move again.")
            self.log("Ko — move taken back.", "grey")
            self.selected = None
            self.moveArray = []
            self.breakOpen = False
            self.redraw()
            return False

        self.lastFlights = Engine.moveFlights(self.board, move, self.contr) if move else ()
        self.board = board
        self.lastMove = squares
        # Below the ko check on purpose: a move that was taken back never happened, and a
        # record that held it would replay into a game nobody played. This is the only
        # place a move reaches the record, and every caller passes one.
        self.recordPly(N.encode_move(move))
        Engine.koRecord(board)
        self.passes = 0
        self.turn += 1

        gameEnd, winner = Hasher.Check_For_Winner(board)
        if gameEnd:
            if winner == [1, 0]: self.finish("White wins!", 0)
            elif winner == [0, 1]: self.finish("Black wins!", 1)
            else: self.finish("A tie.", None)
            return True

        self.advance()
        return True

    def finish(self, text, contr):
        self.phase = "over"
        self.selected = None
        self.moveArray = []
        self.breakOpen = False
        self.prisCheck.setEnabled(False)
        self.freeCheck.setEnabled(False)
        self.setStatus(text, contr)
        self.setHint("Game finished.")
        if contr is None: self.log("Game finished. " + text, "grey")
        else: self.log("Game finished. " + text, "black" if contr else "white")
        self.redraw()
        if self.record:
            self.log("SAVE keeps this game as a text file you can open again.", "grey")

    ################################################################################
    ####### THE RECORD #############################################################
    ################################################################################
    # A game played here used to leave nothing behind. The window called the executors
    # directly and wrote down only boards, into the ko set, so when it closed the game was
    # gone -- there was nothing to keep, nothing to send anybody, and nothing to read back.
    #
    # What it writes now is the same list of RAN tokens the server stores in a database
    # column and hands to a browser, which is what makes a game saved here openable there
    # and the other way about. That interchange is not a happy accident: it is the reason
    # every ply is written down, including the ones where nothing happened.
    #
    # **The entering phase is twelve plies whatever happens in it.** A side with nowhere to
    # place sits the step out, and that skip has to be recorded, because the side of every
    # later ply is derived from its position in the list. Leave one out and the record still
    # reads perfectly, still replays without complaint, and replays every move after it as
    # the *other* side's -- into a real position that nobody played to. The same goes for a
    # pass during play. That is the failure mode this section is shaped around, and
    # tests/test_desktop_record.py is what holds it shut.

    # What a log line is filed under: which of the twelve placements, or which move.
    #
    # Two numberings that both restart at "— entering complete —", rather than one running
    # count, because a game's seventh move is its nineteenth ply and "19" is the number the
    # *record* uses. It is the right handle for code and no help at all to somebody reading
    # back through a game. `royals_engine.record.turn_of_ply` is the same rule stated once
    # for the review UIs; these two counters are where it comes from.
    #
    # Read off `enterIndex` and `turn` rather than off len(self.record), and that is not
    # incidental: the six sites that log a ply do not agree on whether they log before or
    # after recording it, so a mark derived from the record's length would be right at some
    # of them and quietly off by one at the others. These two are correct at all six --
    # enterIndex is the 0-based entering step until it is stepped past, and `turn` is the
    # engine's move counter, which opens at 1 with whoever entered second.
    def turnMark(self):
        number = self.enterIndex + 1 if self.phase == "entering" else self.turn
        return "%2d. " % number

    def recordPly(self, token):
        """Write one ply down. Every path that consumes a ply comes through here.

        Deliberately not the place any decision is made: the callers know whether a ply
        happened, and a recorder that tried to work it out for itself would be a second
        opinion about the game loop.
        """
        self.record.append(token)

    def recordNote(self):
        """The one comment line a saved game carries. Metadata only -- it is dropped on read."""
        who = ("two players" if all(self.humanSides)
               else "the computer against itself" if not any(self.humanSides)
               else "against the computer at depth %d" % self.aiDepth)
        how = self.statusLabel.cget("text").strip("■□ ") if self.phase == "over" else "unfinished"
        return "%s  %s  %s  %d plies" % (
            datetime.date.today().isoformat(), who, how, len(self.record))

    def saveGame(self):
        if not self.record:
            self.setHint("Nothing to save yet — the game has not started.")
            return

        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save this game",
            defaultextension=".txt", filetypes=[("Royals game", "*.txt"), ("All files", "*")],
            initialfile="royals-%s.txt" % datetime.date.today().isoformat())
        if not path:
            return

        try:
            # encode_game decodes every token on the way past, so a recorder that has
            # written one down wrong is caught here, with the ply named -- rather than
            # producing a file that fails to open later with nothing left to say why.
            text = N.encode_game(self.record, notes=[self.recordNote()])
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except (OSError, N.NotationError) as error:
            messagebox.showerror("Royals", "That game could not be saved.\n\n%s" % (error,),
                                 parent=self.root)
            return

        self.log("Saved %d plies to %s" % (len(self.record), path), "grey")
        self.setHint("Saved. Open it again from the first screen to walk through it.")

    # Opening a file, from the setup screen. There is no game in the window yet, so this
    # builds one for the record to be drawn in.
    def openGame(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Open a saved game",
            filetypes=[("Royals game", "*.txt"), ("All files", "*")])
        if not path:
            return

        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            moves, spots = Record.read(text)
        except (OSError, UnicodeDecodeError, N.NotationError, Record.RecordError) as error:
            messagebox.showerror("Royals", "That file could not be read.\n\n%s" % (error,),
                                 parent=self.root)
            return

        if not moves:
            messagebox.showerror("Royals", "There are no moves in that file.",
                                 parent=self.root)
            return

        # A record carries its own opening, so none of the setup screen's answers apply.
        # These are set only because the panel and the drawing read them.
        self.humanSides = [True, True]
        self.aiDepth = DEPTH_DEFAULT
        self.buildGame()
        self.resetGameState()
        self.record = list(moves)
        self.viewReady = True

        name = path.rsplit("/", 1)[-1]
        self.log("Opened %s — %d plies." % (name, len(moves)), "grey")
        self.beginReview(moves, spots, live=False, title=name)

    ####### REVIEW #######
    # An animated slideshow of a game that has already been played, and nothing more.
    #
    # It needs none of the rules and acquires none of them. The positions were worked out
    # by royals_engine.record, which only applies plies -- no move generation, no winner
    # check, and above all no ko: a move in a record was already found legal at the moment
    # it was played, so there is nothing here left to decide. koRecord and koReset are not
    # called from anywhere below, which is what lets a review of a finished game sit inside
    # this window without disturbing the ko set of the game still in it.

    # Everything review writes over, so that DONE puts the window back as it was instead of
    # re-entering the game loop -- which would set the computer thinking again about a move
    # it has already made.
    RESUME_FIELDS = ("phase", "board", "lastMove", "lastFlights", "selected", "moveArray",
                     "breakOpen", "legalOrigins", "movingPris", "spyBreak", "hasPris")

    def showReviewRow(self, on):
        if not on:
            self.reviewRow.pack_forget()
            self.archiveRow.pack(fill="x", pady=(8, 0))
            return

        self.archiveRow.pack_forget()
        if self.reviewScale is not None:
            self.reviewScale.destroy()
        # Set before the slider is built, and to the position review is about to open at,
        # so the trace sees a value it already agrees with and nothing bounces.
        self.reviewVar.set(self.review["at"])
        # max(1, ...) because a StoneSlider divides by its range. A one-ply record has a
        # travel of one and looks right; a zero-ply one is refused before it gets here.
        self.reviewScale = StoneSlider(self.reviewScaleHolder, self.reviewVar, width=290,
                                       low=0, high=max(1, len(self.review["spots"]) - 1))
        self.reviewScale.pack(anchor="w")
        self.reviewRow.pack(fill="x", pady=(8, 0))

    def startReview(self):
        """Walk back through the game in this window."""
        if not self.record:
            self.setHint("Nothing to review yet — no moves have been made.")
            return
        if self.aiBusy:
            # The search is on a worker thread and its result lands in commit(), which
            # would move the board out from under the review.
            self.setHint("Wait for the computer to finish its move.")
            return
        try:
            spots = Record.positions(self.record)
        except Record.RecordError as error:
            # Only reachable if the recorder above has a bug, which is exactly when it is
            # worth saying so loudly rather than showing a plausible wrong game.
            messagebox.showerror("Royals", "This game's record is not readable.\n\n%s"
                                 % (error,), parent=self.root)
            return
        self.beginReview(self.record, spots, live=True, title="This game")

    def beginReview(self, moves, spots, live, title):
        self.review = {
            "moves": list(moves), "spots": spots, "at": len(spots) - 1,
            "live": live, "title": title,
            "resume": {name: getattr(self, name) for name in self.RESUME_FIELDS}
                      if live else None,
            "status": self.statusLabel.cget("text"),
            "hint": self.hintLabel.cget("text"),
        }

        self.phase = "review"
        self.selected = None
        self.moveArray = []
        self.legalOrigins = set()
        self.breakOpen = False
        self.prisCheck.setEnabled(False)
        self.freeCheck.setEnabled(False)
        self.prisVar.set(0)

        self.showReviewRow(True)
        self.reviewGoTo(len(spots) - 1)

    def exitReview(self):
        review, self.review = self.review, None
        self.showReviewRow(False)

        if review and review["resume"]:
            for name, value in review["resume"].items():
                setattr(self, name, value)
            self.statusLabel.configure(text=review["status"])
            self.setHint(review["hint"])
            self.prisCheck.setEnabled(bool(self.hasPris))
            self.redraw()
            return

        # Nothing to go back to -- this was a file, opened into an otherwise empty window.
        self.buildSetup()

    def reviewGoTo(self, at):
        review = self.review
        if not review:
            return

        review["at"] = at = max(0, min(len(review["spots"]) - 1, at))
        spot = review["spots"][at]

        self.board = spot.board
        self.lastMove = list(spot.squares)
        self.lastFlights = spot.flights

        # Setting the variable redraws the slider. onReviewSlide guards against the loop
        # this would otherwise make: a trace fires on every set, equal value or not.
        self.reviewVar.set(at)

        last = len(review["spots"]) - 1
        self.reviewMark.configure(text=reviewLabel(spot, len(self.enterSteps)))
        self.reviewPly.configure(text="ply %d of %d" % (at, last))

        if at == 0:
            self.setStatus("Before the first piece was entered", None)
        else:
            self.setStatus("%s   %s" % (reviewLabel(spot, len(self.enterSteps)),
                                        spot.token), spot.side)
        self.setHint("%s — ← and → step through it, Home and End jump to either end. "
                     "Drag to turn the board." % review["title"])
        self.redraw()

    def reviewStep(self, delta):
        if self.review: self.reviewGoTo(self.review["at"] + delta)

    def onReviewSlide(self, *args):
        review = self.review
        if review and self.reviewVar.get() != review["at"]:
            self.reviewGoTo(self.reviewVar.get())

    ################################################################################
    ####### CLICKS #################################################################
    ################################################################################

    # Which square is under the pointer -- meaning the one whose pieces are there to be
    # seen, not the one the floor happens to be at that point. A stack grows up the
    # screen, towards the squares behind it, and steeply: at twenty degrees the lid of a
    # six-high pile stands nearly two rows back from its own base. So the floor alone
    # would hand back a square two rows away from the one that was clicked on.
    #
    # Everything a square draws is tagged with that square, and the canvas keeps its items
    # in the order they were drawn, which here is the order they are stacked in. So the
    # last tagged item over a point is the topmost thing visible there, and no arithmetic
    # is needed at all. The floor is the fallback, for the hairlines between tiles and for
    # the outlines that are only drawn as outlines and so are only hit on their stroke.
    def squareAt(self, sx, sy):
        for item in reversed(self.canvas.find_overlapping(sx, sy, sx, sy)):
            for name in self.canvas.gettags(item):
                if name.startswith("sq"): return int(name[2:])
        return self.view.square(sx, sy)

    # The mark under the pointer: -1 for the BREAK badge, otherwise the direction index of
    # a break arrow, or None for neither. Read before squareAt and not folded into it,
    # because these float over squares that mean something else entirely when clicked -- the
    # badge sits over the selected stack, whose square means "put it back down" -- and
    # because squareAt falls through to the floor, which would answer for them regardless.
    #
    # The badge's tag deliberately does not start with "brk". A prefix collision here would
    # not fail, it would misroute, and a click that plays the wrong move is worse than one
    # that plays none.
    def markAt(self, sx, sy):
        for item in reversed(self.canvas.find_overlapping(sx, sy, sx, sy)):
            for name in self.canvas.gettags(item):
                if name == "breakbadge": return -1
                if name.startswith("brk"): return int(name[3:])
        return None

    ####### the view under the hand #######
    # A press that goes nowhere is a move; a press that travels is the board being turned.
    # Both start the same way, so nothing is decided until the hand has moved further than
    # it moves by accident.

    def onPress(self, event):
        self.dragging = False
        self.dragFrom = (event.x, event.y)
        self.dragBase = (self.view.yaw, self.view.pitch)

    def onDrag(self, event):
        dx = event.x - self.dragFrom[0]
        dy = event.y - self.dragFrom[1]

        if not self.dragging:
            if max(abs(dx), abs(dy)) < DRAG_SLOP: return
            self.dragging = True

        # Turning the board is looking at it, not playing it, so this is deliberately not
        # behind the aiBusy guard the clicks are behind. It is safe for the reason the
        # thread is safe at all: the worker only ever reads the board it was handed.
        self.view.setAngles(self.dragBase[0] + dx * YAW_PER_PX,
                            self.dragBase[1] - dy * PITCH_PER_PX)
        self.view.fit(self.viewW, self.viewH)
        self.requestRedraw()

    def onRelease(self, event):
        # the release that ends a double click has already been answered by the first one
        if self.swallowRelease:
            self.swallowRelease = False
            return

        if self.dragging:
            self.dragging = False
            self.requestRedraw()
            return

        # Dragging is handled above, so the board can still be turned while reviewing --
        # looking at an old position from another angle is the point. What a review has no
        # answer to is a click on a square, because nothing in it is anybody's to move.
        if self.aiBusy or self.phase in ("over", "review"): return

        # The engine badge is not part of the game, and it has to be answered before either
        # of the two lookups below or it will be answered *by* them: squareAt falls through
        # to self.view.square(), which returns whatever square lies under the point whether
        # or not anything was drawn there. So a click on a decoration in the corner would
        # pick up or put down a piece on the square behind it -- the same misrouting the
        # break badge's tag is careful to avoid, arriving by a different road.
        #
        # Swallowed rather than ignored: returning here means the click does nothing at all,
        # which is what a status indicator should do when clicked.
        if self.engineBadgeHint(event.x, event.y):
            self.setHint(_accel.describe())
            return

        mark = self.markAt(event.x, event.y)
        if mark is not None:
            self.markClick(mark)
            return

        square = self.squareAt(event.x, event.y)
        if square is None: return
        self.boardClick(square)

    # The badge, or one of the arrows it opens.
    def markClick(self, mark):
        if self.breakOrigin() is None: return

        if mark < 0:
            self.breakOpen = not self.breakOpen
            self.setHint("Click an arrow to scatter that way, or BREAK again to put it away."
                         if self.breakOpen
                         else "Click one of the outlined squares, or BREAK over the stack.")
            self.redraw()
            return

        # A stale arrow can't be clicked -- the board is redrawn whenever moveArray changes
        # -- but the move is checked against the list anyway rather than trusted from a tag.
        if mark in self.moveArray[2]: self.playBreak(mark)

    def boardClick(self, square):
        if self.phase == "entering":
            if self.humanSides[self.entryContr]: self.enterClick(square)
            return

        if self.phase == "play" and self.humanSides[self.contr]:
            self.playClick(square)

    # A double click puts the view back. Tk has already delivered the first click by the
    # time this arrives, so the only way to make the pair atomic would be to sit on every
    # click for a quarter of a second waiting to see whether a second one follows -- a
    # quarter second of lag on every move of the game, to pay for a shortcut. Instead the
    # reset only happens where the first click did nothing anyway, which is everywhere a
    # person would actually double click to straighten the board up. Double clicking a
    # legal destination plays the move and leaves the view alone; the button does the rest.
    def onDoubleClick(self, event):
        self.swallowRelease = True
        # a second click on the badge is how the arrows are put away again, and straightening
        # the board out from under them is not what that asked for
        if self.markAt(event.x, event.y) is not None: return
        if not self.clickWasInert(self.squareAt(event.x, event.y)): return
        self.resetView()

    def clickWasInert(self, square):
        if square is None: return True
        # Nothing on a reviewed board answers a click, so every double click on one is free
        # to mean "straighten this up" -- which is the whole point of the shortcut.
        if self.aiBusy or self.phase in ("over", "review"): return True

        if self.phase == "entering":
            if not self.humanSides[self.entryContr]: return True
            return square not in self.entryOptions

        if not self.humanSides[self.contr]: return True
        if square == self.selected: return False
        if square in self.legalOrigins: return False
        if self.moveArray:
            target = square - 1
            for kind in (0, 1, 5):
                if target in self.moveArray[kind]: return False
        return True

    def onHome(self, event):
        if self.review: return self.reviewGoTo(0)
        self.resetView()

    def resetView(self):
        self.view.setAngles(YAW_DEF, PITCH_DEF)
        self.view.fit(self.viewW, self.viewH)
        self.requestRedraw()

    def onResize(self, event):
        if not self.viewReady: return
        if event.width == self.viewW and event.height == self.viewH: return
        self.viewW = event.width
        self.viewH = event.height
        self.view.fit(self.viewW, self.viewH)
        self.requestRedraw()

    # A drag fires a motion event far faster than a board this size can be redrawn, and
    # every one of them would otherwise queue a full repaint behind the last. Collapsing
    # them onto the idle loop means one repaint per chance to draw, which is all a screen
    # can show anyway. Only the drag and the resize come through here -- the game's own
    # redraws happen once per move and want to happen now.
    def requestRedraw(self):
        if self.redrawPending: return
        self.redrawPending = True
        self.root.after_idle(self.flushRedraw)

    def flushRedraw(self):
        self.redrawPending = False
        self.redraw()

    def playClick(self, square):
        # choosing what to move
        if self.selected is None:
            self.select(square)
            return

        # clicking it again puts it back down
        if square == self.selected:
            self.selected = None
            self.moveArray = []
            self.breakOpen = False
            self.prisCheck.setEnabled(False)
            self.freeCheck.setEnabled(False)
            self.prisVar.set(0)
            self.setHint("Click one of the outlined squares.")
            self.redraw()
            return

        if self.moveArray:
            # possJumps and possPushes are 0-based, squares here are 1-based
            if (square - 1) in self.moveArray[0]:
                self.playHuman("jump", square)
                return
            canFree = (square - 1) in self.moveArray[5]
            canShove = (square - 1) in self.moveArray[1]
            if canFree or canShove:
                # both can be true of one square -- the checkbox is what picks between
                # them, and whichever is the only option wins regardless of it
                freeing = canFree and (bool(self.freeVar.get()) or not canShove)
                self.playHuman("free" if freeing else "push", square)
                return

        # not a destination, so read it as picking a different piece
        if square in self.legalOrigins:
            self.select(square)
            return

        self.setHint("Not a legal move from "
                     + Hasher.IndexToAlg(self.selected - 1).upper() + ".")

    # Picks up a square, working out for itself what getOrigin would have asked about:
    # whether this is our spy breaking out of their stack, and whether there are prisoners
    # on it that could be brought along.
    def select(self, square):
        if square not in self.legalOrigins:
            spaces = Hasher.Parse_Board(self.board)
            s = spaces[square - 1]
            if not Hasher.Space_Occupied(s): self.setHint("Nothing on that square.")
            elif s[Hasher.SIDE] != self.contr: self.setHint("That isn't yours to move.")
            else: self.setHint("That piece has nowhere to go.")
            return

        s = Hasher.Parse_Board(self.board)[square - 1]

        # our spy on their square: breaking out is the only move it has
        self.spyBreak = (s[Hasher.SIDE] != self.contr and bool(s[Hasher.CAPSPY]))
        self.hasPris = (s[Hasher.SIDE] == self.contr
                        and bool(s[Hasher.CAPSPY] or s[Hasher.CAPPAWNS]))

        self.selected = square
        self.prisVar.set(0)
        self.prisCheck.setEnabled(bool(self.hasPris))
        self.refreshMoves()

    # Reads the moves out of the engine for the square in hand. Called again whenever the
    # prisoner checkbox moves, because carrying them changes what is legal.
    def refreshMoves(self):
        movingPris = bool(self.prisVar.get()) and self.hasPris
        tOrigin = artificialPlayer.makeOrigin(self.board, self.selected, movingPris, self.spyBreak)
        self.moveArray = Engine.checkMoves(self.board, tOrigin, self.contr)
        self.movingPris = movingPris

        self.breakOpen = False
        if self.moveArray:
            jumps = len(self.moveArray[0])
            pushes = len(self.moveArray[1])
            frees = len(self.moveArray[5])

            # only worth offering the choice where a square is under both, which is the
            # only case the box decides anything
            both = set(self.moveArray[1]) & set(self.moveArray[5])
            self.freeCheck.setEnabled(bool(both))

            bits = []
            if jumps: bits.append(str(jumps) + " jump" + ("s" if jumps != 1 else ""))
            if pushes: bits.append(str(pushes) + " push" + ("es" if pushes != 1 else ""))
            if frees: bits.append(str(frees) + " freeing")
            if self.moveArray[2]: bits.append(str(len(self.moveArray[2])) + " break")

            # Breaks used to be four buttons down here named after board directions, which
            # was fine until the board could be turned: "LEFT" points wherever the drag left
            # it pointing. They are on the board now, where every other move already is.
            self.setHint(Hasher.IndexToAlg(self.selected - 1).upper() + ": "
                         + (", ".join(bits) if bits else "nothing legal")
                         + (".  BREAK is over the stack." if self.moveArray[2] else ".")
                         + "  Click it again to put it back down.")
        else:
            self.freeCheck.setEnabled(False)
            self.setHint("Nothing legal from there"
                         + (" while carrying prisoners." if movingPris else "."))

        self.redraw()

    def onPrisToggle(self):
        if self.selected is not None: self.refreshMoves()

    def playHuman(self, kind, square):
        origin = self.selected
        if kind == "jump":
            board = Engine.exeMove(self.board, origin, square, self.contr, self.movingPris)
        else:
            board = Engine.exePush(self.board, origin, square, self.contr, self.movingPris,
                                   kind == "free")

        move = (origin, kind, square - 1, self.movingPris)
        # logged before commit, so a move the ko rule takes back reads as the move
        # followed by the note taking it back, and a move that ends the game doesn't
        # print after the result.
        self.log(self.turnMark() + sideName(self.contr) + ": "
                 + artificialPlayer.describeMove(move),
                 "black" if self.contr else "white")
        self.commit(board, [origin, square], move)

    def playBreak(self, heading):
        origin = self.selected
        board = Engine.exeBreak(self.board, origin, heading, self.contr)

        move = (origin, "break", Engine.pushIndex(heading), False)
        self.log(self.turnMark() + sideName(self.contr) + ": "
                 + artificialPlayer.describeMove(move),
                 "black" if self.contr else "white")
        self.commit(board, [origin], move)

    ################################################################################
    ####### THE COMPUTER'S THREAD ##################################################
    ################################################################################
    # Depth 6 is about ten seconds, and tkinter redraws nothing while a callback is
    # running. So the search goes on a worker and the result comes back through a queue
    # the main thread polls. The worker only ever reads the board it was handed --
    # Mod_Space returns a new board rather than editing one, so there is nothing here
    # for the two threads to disagree about.

    # root.after, dropped if the game that asked for it has since been torn down. Every
    # deferred thing this window does goes through here or through pollAI's generation
    # check; a bare root.after is how a game nobody is playing gets stepped.
    def later(self, ms, fn):
        gen = self.gameGen
        self.root.after(ms, lambda: fn() if gen == self.gameGen else None)

    def runAI(self, work, done):
        self.aiBusy = True
        results = queue.Queue()

        # BaseException rather than Exception, which normally reads as a mistake and here is
        # the whole point. A panic in the compiled engine arrives as pyo3_runtime.
        # PanicException, which derives from BaseException so that it cannot be swallowed by
        # a passing `except Exception` -- and this was exactly such a handler. The thread
        # died without ever putting anything in the queue, so pollAI polled an empty queue
        # forever: aiBusy stayed set, the window sat on "Thinking..." with no log line, and
        # the aiBusy guard in boardClick swallowed every click. A frozen window with nothing
        # written down is the worst answer available; the error branch below is the right one
        # and simply was not being reached.
        #
        # This does not make the engine usable again. A panic poisons the Rust search state,
        # so every later search in this process raises too -- but it raises *visibly* now,
        # and each one lands in the log instead of hanging.
        #
        # KeyboardInterrupt and SystemExit are caught along with it. That costs nothing: they
        # are delivered to the main thread, not to this daemon worker, and a process on its
        # way out does not care what this queue holds.
        def run():
            try: results.put(("ok", work()))
            except BaseException as error: results.put(("error", error))

        threading.Thread(target=run, daemon=True).start()
        self.pollAI(results, done, self.gameGen)

    # The search runs on a thread and answers into a queue; this watches the queue from the
    # event loop, which is what keeps the window painting while it thinks.
    #
    # **The generation is the whole point of this function's signature.** A search cannot be
    # called off -- the thread has no interrupt and Python has no way to give it one -- so
    # NEW GAME pressed mid-search leaves a worker that is still going to answer, about a
    # position that is no longer on the board. Answering it landed the *previous* game's
    # position on the new game: `commit` writes self.board wholesale, `finishAIEntry` drops a
    # piece, both append a ply to a record that never contained it, and koRecord puts an old
    # board into the ko set the new game just cleared. That last one is the quiet part -- the
    # new game then refuses a legal first move as a repetition, and the record it writes will
    # not replay at all.
    #
    # It was survivable while the deepest search was a fifth of a second. At ten ply it is
    # closer to twenty, which is long enough that a player gets bored, starts another game,
    # and watches the old one reappear.
    #
    # So the answer is dropped rather than the search stopped: no reschedule, no `done`, and
    # aiBusy deliberately left alone, because it belongs to whatever game is running now. The
    # thread finishes into a queue nobody reads and is collected with it.
    def pollAI(self, results, done, gen):
        if gen != self.gameGen: return

        try: kind, payload = results.get_nowait()
        except queue.Empty:
            self.root.after(60, lambda: self.pollAI(results, done, gen))
            return

        self.aiBusy = False

        if kind == "error":
            self.log("The computer's search raised: " + repr(payload), "grey")
            self.finish("Stopped — see the log.", None)
            return

        done(payload)

    ################################################################################
    ####### DRAWING ################################################################
    ################################################################################
    # DisplayHashBoard prints every square twice, one row per side, because a square can
    # hold one side's stack and the other side's prisoners at the same time. Here that is
    # one square with the stack standing on it and the prisoners in a slot along its near
    # edge, drawn in the shade of whoever lost them.

    def redraw(self):
        # buildGame lays this canvas out before startGame has made a board, and the
        # resize that follows it arrives through the event loop -- so the first Configure
        # can land here with nothing yet to draw.
        if not self.viewReady: return

        c = self.canvas
        c.delete("all")
        v = self.view
        v.detail = not self.dragging
        spaces = Hasher.Parse_Board(self.board)

        # First, so everything else is drawn over it -- canvas items stack in the order they
        # were made, and this one is the ground.
        self.drawGradient(c, v)

        self.drawCabinet(v)

        # Far to near, and that is the whole of the hidden surface problem here. A stack
        # only ever grows up the screen -- which is to say towards the squares behind it
        # -- so a far square can never hide a near one, and one pass in this order is
        # exactly right rather than nearly right. Everything a square draws, tile and
        # pieces and marks alike, is drawn inside the pass; a second pass for the marks
        # would put them through walls.
        order = sorted(range(1, 50),
                       key=lambda n: v.depth((n - 1) % 7 - 3.0, (n - 1) // 7 - 3.0))
        for square in order:
            self.drawSquare(v, square, spaces[square - 1])

        # The two marks that run along a line of squares rather than sitting on one, so
        # neither has a turn in the pass above. What was played first, what is being offered
        # over the top of it -- the offer is the thing being decided, so it wins.
        self.drawFlights(v)
        self.drawBreakUI(v, spaces)

        # the near two strips of lettering, held back so the pieces don't stand on them
        self.drawCoords(v, True)

        # Last of all, and in screen space: the badge belongs to the window rather than to
        # the board, so it neither turns with the yaw nor moves with a drag.
        self.drawEngineBadge()

    # ---- the engine badge -----------------------------------------------------------------
    #
    # Which engine is answering, in the top-left corner of the board. Colour when the compiled
    # one is, grey when it is the Python.
    #
    # Worth having because the difference is otherwise invisible: the two play identically and
    # the only symptom of a missing wheel is that the machine feels slow, which is
    # indistinguishable from the machine being busy. A player who never installs royals-accel
    # should be able to see that, not deduce it.
    #
    # Drawn rather than loaded. Nothing else in this file is an image -- the whole interface is
    # bevel(), plate() and engrave() -- and Pillow is not a dependency, so a PNG would mean
    # adding one and then greyscaling at runtime. Drawn, the two states are a palette swap.
    #
    # A stack of three, because that is what the game is about: pieces gathering onto a square.
    #
    # **The two states differ in value, not hue, and that is not a compromise.** This palette
    # is achromatic on purpose -- see LOOK above -- and measuring it bears that out: every
    # colour in the file has a channel spread under 24. There is no colour here to take away,
    # so a literal "colour versus greyscale" would have produced two identical badges: an
    # indicator that looks implemented and says nothing, which is worse than none.
    #
    # So the compiled engine gets the full ink-on-paper range the pieces themselves use, and
    # the Python gets the muted greys the sunk parts of the cabinet use. Lit up against dimmed.
    # Both are drawn by the same code from the four names below, so they cannot drift apart.

    BADGE_X = 16          # from the canvas's left edge
    BADGE_Y = 16          # from its top edge
    BADGE_W = 30
    BADGE_H = 34

    def drawEngineBadge(self):
        c = self.canvas
        x, y, w, h = self.BADGE_X, self.BADGE_Y, self.BADGE_W, self.BADGE_H

        # body / highlight / shadow / label -- the whole difference between the two states.
        if FAST_ENGINE:
            body, lit, shade, ink = WHITE, EDGE_LT, RULE, TEXT
        else:
            body, lit, shade, ink = WELL, EDGE, EDGE_DK, TEXT_DIM

        plate(c, x, y, x + w, y + h, PANEL, EDGE_LT, EDGE_DK, depth=2, tags="enginebadge")

        # three discs, near edge to far, the way a stack reads on the board
        for i in range(3):
            top = y + h - 10 - i * 6
            c.create_oval(x + 6, top, x + w - 6, top + 9,
                          fill=body, outline=shade, tags="enginebadge")
            # the light line along the top -- the same trick everything else here is built on
            c.create_arc(x + 6, top, x + w - 6, top + 9, start=20, extent=140,
                         style="arc", outline=lit, tags="enginebadge")

        engrave(c, x + w / 2.0, y + 8, "RS" if FAST_ENGINE else "PY",
                FONT["small"], ink, EDGE_LT, tags="enginebadge")

    # What the badge would say if there were room to say it. Bound to <Motion> rather than
    # drawn, because the board underneath is already carrying every mark the game needs and
    # a permanent caption in the corner would be one more thing between the player and it.
    def engineBadgeHint(self, sx, sy):
        x, y, w, h = self.BADGE_X, self.BADGE_Y, self.BADGE_W, self.BADGE_H
        return x <= sx <= x + w and y <= sy <= y + h

    # The wash the board stands on, when a theme asks for one.
    #
    # **Bands, because the canvas has no gradient and no alpha.** Everything else in this file
    # works around that by choosing a different flat colour rather than laying a wash over
    # one; here there is nothing underneath to choose against, so a wash is N flat rectangles
    # with the fill stepped between two ends. Twenty-four is enough that the steps are not
    # visible at the sizes this window opens at.
    #
    # It collapses to a single rectangle while the board is being dragged, on the same switch
    # -- `v.detail` -- that already thins the chips and the tiles. A gradient is the one thing
    # here whose cost is paid per frame rather than per piece: the board draws around two
    # thousand items already, and turning it should not also be repainting sixty-four
    # rectangles behind them.
    #
    # A theme with the gradient off leaves the canvas showing its own background colour, which
    # is set from PANEL when the game screen is built. That costs nothing at all and is why
    # `on` defaults to false.
    def drawGradient(self, c, v):
        if not GRADIENT.get("on"): return

        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        start, end = GRADIENT["from"], GRADIENT["to"]

        if not v.detail:
            c.create_rectangle(0, 0, w, h, fill=mixShade(start, end, 0.5), outline="")
            return

        bands = max(Theme.BANDS_MIN, min(Theme.BANDS_MAX, int(GRADIENT["bands"])))
        direction = GRADIENT["direction"]

        # Diagonal has no rectangle that draws it, so it is done as a run of parallelograms
        # sheared across the canvas -- each one wide enough to cover the corner it reaches.
        for i in range(bands):
            shade = mixShade(start, end, i / float(bands - 1) if bands > 1 else 0.0)
            if direction == "horizontal":
                x0 = w * i / float(bands)
                c.create_rectangle(x0, 0, w * (i + 1) / float(bands) + 1, h,
                                   fill=shade, outline="")
            elif direction == "diagonal":
                span = (w + h) * (i + 1) / float(bands)
                back = (w + h) * i / float(bands)
                c.create_polygon(back, 0, span, 0, 0, span, 0, back,
                                 fill=shade, outline="")
            else:
                y0 = h * i / float(bands)
                c.create_rectangle(0, y0, w, h * (i + 1) / float(bands) + 1,
                                   fill=shade, outline="")

    # The board as a thing with a thickness: a slab, the carved border laid on top of it,
    # the black rule round the playfield and the field sunk behind that. Flat, the border
    # was one rectangle with the playfield punched out of it. Turned, it has to be four
    # trapezoids instead, because a canvas polygon cannot have a hole in it.
    def drawCabinet(self, v):
        c = self.canvas
        e = E_HALF
        f = 3.5
        corners = [(-e, -e), (e, -e), (e, e), (-e, e)]

        # The walls of the slab. Two of the four face the camera at any yaw and the other
        # two are behind the top surface, where they would only draw over themselves.
        for i in range(4):
            x0, y0 = corners[i]
            x1, y1 = corners[(i + 1) % 4]
            nx, ny = (y1 - y0), -(x1 - x0)
            if nx * v.sinY + ny * v.cosY <= 0: continue
            wall = v.poly([(x0, y0), (x1, y1)], 0.0) + v.poly([(x1, y1), (x0, y0)], -SLAB_T)
            c.create_polygon(wall, fill=EDGE_DK, outline=RULE_DK, width=LINE_W["hair"])

        # the border, as the four strips left over once the playfield is taken out
        for a, b, d, e2 in (((-e, -e), (e, -e), (f, -f), (-f, -f)),
                            ((e, -e), (e, e), (f, f), (f, -f)),
                            ((e, e), (-e, e), (-f, f), (f, f)),
                            ((-e, e), (-e, -e), (-f, -f), (-f, f))):
            polyPlate(c, v, [a, b, d, e2], 0.0, EDGE, EDGE_LT, EDGE_DK, True)

        # the black rule round the field, and the field itself sunk behind it
        rule = f + 9.0 / CELL
        well = f + 4.0 / CELL
        c.create_polygon(v.poly([(-rule, -rule), (rule, -rule),
                                 (rule, rule), (-rule, rule)], 0.0),
                         fill=INK, outline="")
        polyPlate(c, v, [(-well, -well), (well, -well), (well, well), (-well, well)],
                  0.0, WELL, EDGE_DK, EDGE_LT, False)

        # A stud in each corner of the border, the way a real cabinet is pinned. Flat
        # these were two little circles; standing up they are the smallest chips there
        # are, which is the same code and keeps them visible when the board is low.
        if v.detail:
            p = e - E_FRAME / 2.0
            for sx in (-p, p):
                for sy in (-p, p):
                    chip(c, v, sx, sy, 0.0, STUD_R, STUD_H,
                         (INK, EDGE_DK, RULE_LT, INK), EDGE_DK, INK, LINE_W["hair"])

        self.drawCoords(v, False)

    # Coordinates cut into the border itself, the way a real board has them. A canvas can
    # turn text but it cannot shear it, so a letter genuinely lying in the surface is not
    # a thing that can be drawn: these stand upright on the border instead, turned to
    # follow the strip they are cut into, and read as stamped on rather than carved in.
    #
    # `near` picks which half is wanted. The two far strips go down with the cabinet, so
    # a tall stack in the back row can stand in front of them, which is what should
    # happen; the two near ones are held back until after the squares, so nothing stands
    # in front of them, which is also what should happen.
    def drawCoords(self, v, near):
        c = self.canvas
        m = 3.5 + E_FRAME / 2.0

        def turn(dx, dy):
            sx = dx * v.cosY - dy * v.sinY
            sy = (dx * v.sinY + dy * v.cosY) * v.sinP
            deg = math.degrees(math.atan2(-sy, sx))
            if deg > 90.0: deg -= 180.0
            elif deg <= -90.0: deg += 180.0
            return deg

        # each strip: the point it is centred on, the way it runs, and what it spells
        strips = (((0.0, -m), (1.0, 0.0), "ABCDEFG"),
                  ((0.0, m), (1.0, 0.0), "ABCDEFG"),
                  ((-m, 0.0), (0.0, 1.0), "1234567"),
                  ((m, 0.0), (0.0, 1.0), "1234567"))

        for (cx, cy), (dx, dy), text in strips:
            # how near the camera this strip is; ties are the two side strips seen
            # square-on, and they are wanted
            if (cx * v.sinY + cy * v.cosY >= 0.0) != near: continue
            angle = turn(dx, dy) % 360.0
            for k in range(7):
                step = k - 3.0
                px, py = v.project(cx + dx * step, cy + dy * step, 0.0)
                # on a pale surface it is the highlight under a letter that makes it read
                # as stamped in, where on a dark one it was the shadow over it
                engrave(c, px, py, text[k], FONT["coord"], LABEL, EDGE_LT, angle=angle)

    # How tall whatever is standing on a square is, in cells. Everything a square wants to
    # say about itself -- which piece may move, which is chosen, where it could go -- is
    # said at this height rather than on the tile, so that a mark about a six-high stack
    # is not left underneath it.
    def stackTop(self, s):
        if not Hasher.Space_Occupied(s): return 0.0
        if s[Hasher.DRAGON]: return H_DRAGON
        return (s[Hasher.SPY] + s[Hasher.PAWNS] + s[Hasher.ROYAL]) * H_CHIP

    def drawSquare(self, v, square, s):
        c = self.canvas
        tag = "sq%d" % square
        wx = (square - 1) % 7 - 3.0
        wy = (square - 1) // 7 - 3.0

        # 7 is odd, so square number parity is the colour of the square -- the same thing
        # DisplayHashBoard tests, and the parity the diagonal jumps preserve.
        dark = (square % 2 == 0)

        entryOption = (self.phase == "entering" and square in self.entryOptions
                       and self.humanSides[self.entryContr])

        if entryOption:
            fill, hi, lo = (DARK_ENTRY, LIGHT_ENTRY, DARK_LO) if dark \
                else (LIGHT_ENTRY, LIGHT_HI, DARK_ENTRY)
        elif square in self.lastMove:
            fill, hi, lo = (DARK_LAST, LIGHT_LAST, DARK_LO) if dark \
                else (LIGHT_LAST, LIGHT_HI, DARK_LAST)
        elif dark:
            fill, hi, lo = DARK, DARK_HI, DARK_LO
        else:
            fill, hi, lo = LIGHT, LIGHT_HI, LIGHT_LO

        # Every square is a tile in its own right rather than a flat patch of colour: the
        # light corner catches the same top-left light the frame does, so the board reads
        # as laid rather than painted.
        polyPlate(c, v, [(wx - 0.5, wy - 0.5), (wx + 0.5, wy - 0.5),
                         (wx + 0.5, wy + 0.5), (wx - 0.5, wy + 0.5)],
                  0.0, fill, hi, lo, True, tag)

        # Whichever of the two shades stands out against this square. An outline in one
        # fixed colour disappeared on half the board.
        tone = LIGHT_HI if dark else INK
        counter = INK if dark else LIGHT_HI

        # A mark about the square goes on the square, and a mark about where a piece could
        # go rides at the height of whatever is standing there. The first two are drawn
        # round the rim of the tile, wider than any chip, so a stack cannot hide them and
        # lifting them off the board would only make them look unmoored from it. A
        # destination has to survive being drawn on an occupied square, which is the one
        # case where the tile is genuinely underneath something.
        zTile = 0.002
        zTop = self.stackTop(s) + 0.002

        ####### what is standing here #######
        if Hasher.Space_Occupied(s):
            side = s[Hasher.SIDE]
            walls = CHIP_WALL[side]

            held = s[Hasher.CAPSPY] + s[Hasher.CAPPAWNS]
            # the standing stack steps back off the slot its prisoners sit in
            bx, by = wx, wy
            if held:
                dx, dy = v.alongV(-0.10)
                bx, by = wx + dx, wy + dy

            if s[Hasher.DRAGON]:
                # An obelisk where everything else is a pile: nine tenths of the square
                # across its foot, five chips tall, drawing in slightly to a pyramidion.
                # It carries no icon, and it is the one piece on the board with no pale lid
                # -- there is nothing to tell apart on this square, because a dragon is all
                # there ever is on it.
                obelisk(c, v, bx, by, 0.0, R_DRAGON, H_DRAGON, walls, LINE_W["piece"], tag)
            else:
                # One chip per piece, all the same chip, piled up -- the same width the
                # whole way up, so the pile is one column and not a stepped one. What
                # finds the head of it is the pale lid, which no chip below the top one
                # shows any of.
                n = s[Hasher.SPY] + s[Hasher.PAWNS] + s[Hasher.ROYAL]
                for k in range(n):
                    chip(c, v, bx, by, k * H_CHIP, R_CHIP, H_CHIP,
                         walls, PIECE_HALO, INK, LINE_W["piece"], tag)

                # The same icons the board always used, the same royal-and-spy-then-pawns
                # arrangement, painted flat on the lid of the top chip. The height says
                # how many; the icons still say which, because six chips of the same chip
                # cannot. Both sides get a pale lid for the reason both sides always got a
                # pale halo, and the side is carried by the walls underneath instead.
                upper = []
                if s[Hasher.SPY]: upper.append(SHAPE_SPY)
                if s[Hasher.ROYAL]: upper.append(SHAPE_ROYAL)
                pawns = [SHAPE_DISC] * s[Hasher.PAWNS]

                # The sizes are the old ones divided through by a square and then pulled
                # in until four pawns fit inside the rim of a lid rather than hanging off
                # it. Flat they could overhang their cell and nothing looked wrong;
                # standing on an object, an icon over the edge looks like a mistake.
                lz = n * H_CHIP + 0.001
                if upper and pawns:
                    planeRow(c, v, upper, bx, by, lz, 0.0, -0.15, 0.120, 0.048, side, LINE_W["piece"], tag)
                    planeRow(c, v, pawns, bx, by, lz, 0.0, 0.16, 0.078, 0.026, side, LINE_W["piece"], tag)
                elif upper:
                    r = 0.160 if len(upper) == 1 else 0.145
                    planeRow(c, v, upper, bx, by, lz, 0.0, 0.0, r, 0.065, side, LINE_W["piece"], tag)
                elif pawns:
                    r = 0.100 if len(pawns) <= 2 else 0.082
                    planeRow(c, v, pawns, bx, by, lz, 0.0, 0.0, r, 0.030, side, LINE_W["piece"], tag)

            # Prisoners belong to the other side, so they are drawn in the other side's
            # shade: what is shown is whose pieces these are, not who holds them. They sit
            # smaller, in a slot cut into the square -- held, not standing. The slot is
            # laid out in the surface's own screen-down direction rather than along the
            # board's, so it is always on the near edge of its square: fixed to the board
            # it would spend half of every turn behind the stack that is holding it.
            if held:
                self.drawPrisoners(v, wx, wy, side, s, tag)

        ####### what this square has to say about itself #######

        # a square this side could move from, before one has been picked
        if (self.phase == "play" and self.selected is None
                and self.humanSides[self.contr] and square in self.legalOrigins):
            c.create_polygon(v.poly(self.inset(wx, wy, 0.463), zTile),
                             fill="", outline=tone, width=LINE_W["piece"],
                             dash=(3, 3) if v.detail else (), tags=tag)

        # the chosen square gets a solid double rule, so it never reads as merely available
        if square == self.selected:
            c.create_polygon(v.poly(self.inset(wx, wy, 0.476), zTile),
                             fill="", outline=counter, width=LINE_W["mark"], tags=tag)
            c.create_polygon(v.poly(self.inset(wx, wy, 0.439), zTile),
                             fill="", outline=tone, width=LINE_W["piece"], tags=tag)

        ####### where it could go #######
        # While the break arrows are out they are the question being asked, and they cross
        # these very squares on their way. Two families of mark over the same ground is
        # unreadable, so the rings stand down until the break is put away.
        if self.moveArray and self.phase == "play" and not self.breakOpen:
            target = square - 1
            if target in self.moveArray[0]:
                self.drawTarget(v, wx, wy, zTop, "jump", tone, tag)
            elif target in self.moveArray[1]:
                self.drawTarget(v, wx, wy, zTop, "push", tone, tag)
            elif target in self.moveArray[5]:
                self.drawTarget(v, wx, wy, zTop, "free", tone, tag)

    # the corners of a square inset by the same margin all round
    def inset(self, wx, wy, r):
        return [(wx - r, wy - r), (wx + r, wy - r), (wx + r, wy + r), (wx - r, wy + r)]

    def drawPrisoners(self, v, wx, wy, side, s, tag):
        c = self.canvas
        px, py = v.project(wx, wy, 0.001)
        sx, sy = v.planeScale()

        # a fixed grey rather than one of the square's own shades, so the slot is visible
        # whichever colour square it is cut into
        plate(c, px - 0.34 * sx, py + 0.245 * sy, px + 0.34 * sx, py + 0.415 * sy,
              PANEL_DK, PIECE_HALO, INK, 1, False)

        caught = []
        if s[Hasher.CAPSPY]: caught.append(SHAPE_SPY)
        caught += [SHAPE_DISC] * s[Hasher.CAPPAWNS]
        # these keep their halo -- the slot they sit in is a mid grey, not the pale lid a
        # chip's icons get for nothing
        planeRow(c, v, caught, wx, wy, 0.002, 0.0, 0.33, 0.052, 0.036,
                 int(not side), 1, tag, halo=True)

    # With no colour to tell the three kinds of destination apart, they are told apart by
    # shape: a jump is a solid ring, a push a broken one, and a freeing push is square
    # rather than round.
    #
    # All three are drawn round the edge of the square rather than in the middle of it,
    # whether anything is standing there or not. A filled dot in the middle was the obvious
    # thing for an empty square, and it was the wrong thing: at that size it read as a
    # pawn, and a board full of destinations looked like a board full of pieces. Round the
    # edge nothing can be mistaken for something standing on the square, and an occupied
    # destination keeps showing what is already on it.
    #
    # They ride at the height of whatever is standing on the square rather than on the
    # tile, so an occupied destination is a ring hanging over the top of its stack instead
    # of a mark buried under it -- which is the same rule as before, kept through the
    # third dimension rather than in spite of it.
    def drawTarget(self, v, wx, wy, z, kind, tone, tag):
        c = self.canvas

        if kind == "free":
            c.create_polygon(v.poly(self.inset(wx, wy, 0.43), z),
                             fill="", outline=tone, width=LINE_W["mark"],
                             dash=(7, 5) if v.detail else (), tags=tag)
            return

        # the rings hold the icons' squash floor rather than the honest one, so that a
        # ring is still recognisably a ring when the board is lying nearly flat
        px, py = v.project(wx, wy, z)
        sx, sy = v.planeScale()
        rx, ry = 0.43 * sx, 0.43 * sy
        c.create_oval(px - rx, py - ry, px + rx, py + ry,
                      fill="", outline=tone, width=LINE_W["mark"],
                      dash=(7, 5) if (kind == "push" and v.detail) else (), tags=tag)

    ################################################################################
    ####### THINGS DRAWN ALONG A RUN OF SQUARES ####################################
    ################################################################################
    # Both of these are drawn after the square pass rather than inside it, which is the one
    # exception to the rule set out at the top of redraw. That rule is about a mark that
    # belongs to a square: lifted out of the pass, it would be drawn over the cabinet wall
    # standing in front of it. These belong to no square -- an arrow is one object lying
    # across five of them, and there is no square in the pass whose turn it is. They are
    # also the two marks that have to be read over whatever they cross, which is what being
    # drawn last gives them for free.

    # The square offering to break, or None. It is asked for in three places -- the badge,
    # the arrows, and the click that lands on either -- and they must agree, or the board
    # grows a button that does nothing.
    def breakOrigin(self):
        if self.phase != "play" or not self.humanSides[self.contr]: return None
        if self.selected is None or not self.moveArray: return None
        if not self.moveArray[2]: return None
        return self.selected

    def drawBreakUI(self, v, spaces):
        square = self.breakOrigin()
        if square is None: return

        c = self.canvas
        wx = (square - 1) % 7 - 3.0
        wy = (square - 1) // 7 - 3.0

        if self.breakOpen:
            for d in self.moveArray[2]:
                reach = Engine.checkBreak(self.board, square, self.board[square - 1],
                                          d, self.contr)
                if reach < 2: continue

                # The arrow runs to the far edge of the last square that catches a piece, so
                # its length is the reach -- which is the thing four identical buttons could
                # never say, and it differs per direction.
                dx, dy = Engine.pushDirs[d]
                ray = Engine.BREAKRAY[square][d]
                z = max(self.stackTop(spaces[ray[i]]) for i in range(0, reach)) + 0.03
                legs = rayLegs(wx, wy, dx, dy, 0.45, (reach - 1) + 0.45)
                planeArrow(c, v, legs, dx, dy, z, BRK_SHAFT, BRK_BARB, BRK_HEAD,
                           PANEL_DK, INK, PIECE_HALO, 5, 2, "brk%d" % d)

        # last, so the badge is never buried under an arrow leaving its own square
        self.drawBreakBadge(v, wx, wy, self.stackTop(spaces[square - 1]))

    def drawBreakBadge(self, v, wx, wy, top):
        c = self.canvas
        px, py = v.project(wx, wy, top + 0.34)
        hx, hy = v.project(wx, wy, top)

        # At the steepest pitch cos(pitch) is about a thirtieth, so a lift measured in board
        # units is worth a pixel and the badge would come to rest on the lid it is supposed
        # to be floating over. The gap has a floor in pixels for that reason alone.
        py = min(py, hy - max(16.0, 0.30 * v.scale))
        halfW = max(26.0, 0.52 * v.scale)
        halfH = max(11.0, 0.19 * v.scale)

        # A stalk down to the head of the stack. At a low pitch anything raised projects up
        # the screen into the rank behind it, and without the stalk the badge reads as
        # belonging to whichever square it happens to be floating in front of.
        c.create_line(px, py + halfH, hx, hy, fill=INK, tags="breakbadge")
        plate(c, px - halfW, py - halfH, px + halfW, py + halfH,
              PANEL if self.breakOpen else PANEL_LT, EDGE_LT, EDGE_DK, 2,
              not self.breakOpen, "breakbadge")
        engrave(c, px, py, "BREAK", FONT["small"], TEXT, EDGE_LT, tags="breakbadge")

    # What the last move moved. The log has said this all along, but a computer move at
    # depth three is over in a tenth of a second and reading a line of notation to find out
    # what just happened is not looking at the board.
    def drawFlights(self, v):
        c = self.canvas

        # A break throws pieces out along one line, so its flights are nested arrows out of
        # one square, and drawing them on top of each other is unreadable. The longest says
        # everything the shorter ones do -- how far the scatter reached.
        longest = {}
        for flight in self.lastFlights:
            key = (flight[0], flight[2], flight[3])
            if key not in longest or flight[4] > longest[key][4]: longest[key] = flight

        for fromSquare, toSquare, dx, dy, steps in longest.values():
            span = math.hypot(dx, dy)
            u0, u1 = RUN_FOOT / span, steps - RUN_TIP / span
            if u1 <= u0: continue

            legs = rayLegs((fromSquare - 1) % 7 - 3.0, (fromSquare - 1) // 7 - 3.0,
                           dx, dy, u0, u1)
            planeArrow(c, v, legs, dx, dy, FLY_Z, FLY_SHAFT, FLY_BARB, FLY_HEAD,
                       INK, INK, PIECE_HALO, 4, 1, "flight")

    ################################################################################
    ####### PANEL ##################################################################
    ################################################################################

    # Whose turn it is was the one thing the colour said on its own. It is marked instead
    # with the shape the side's pieces wear: hollow for white, filled for black.
    def setStatus(self, text, contr):
        if contr is not None: text = ("■  " if contr else "□  ") + text
        self.statusLabel.configure(text=text, fg=TEXT)

    def setHint(self, text):
        self.hintLabel.configure(text=text)

    def log(self, text, tag="grey"):
        self.logText.configure(state="normal")
        self.logText.insert("end", text + "\n", tag)
        self.logText.see("end")
        self.logText.configure(state="disabled")


def main():
    root = tk.Tk()
    RoyalsWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
