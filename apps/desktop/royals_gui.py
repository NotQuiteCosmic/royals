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
# The computer thinks on a worker thread. Measured on the packed-int board, a search costs
# roughly four to five times the level below it -- depth 3 a tenth of a second, 4 half a
# second, 5 a bit over two, 6 ten and a half. So the shallow end doesn't need a thread at
# all and the deep end very much does, and a search inside a click handler is that long
# with the window unable to redraw and greyed out by the window server.
#
# (MainPlay's comment above its depth prompt still quotes the figures from before that
# rewrite -- 3 about a second and a half, 4 closer to ten. Those are about twenty times
# what the same searches cost now.)

import math
import queue
import threading

import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

# The engine is an installed package now (pip install -e ./engine), so the sys.path fixup
# that used to sit here is gone. Aliased on import so every call site below reads exactly
# as it did when these were top-level modules.
from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as artificialPlayer


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
INK = "#0d0d0c"
PAPER = "#e9e6de"

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
LIGHT = "#e2ded5"
LIGHT_HI = "#f8f6f2"
LIGHT_LO = "#b4afa5"
DARK = "#1e1e1c"
DARK_HI = "#494844"
DARK_LO = "#000000"

# The same two shifted for the states a square can be in. Canvas has no alpha, so a
# highlight is a different fill rather than a wash over one -- and with no colour to
# spend, the shift has to be in lightness: a played-from square lifts, a square open for
# entering sinks.
LIGHT_LAST = "#f6f3ec"
DARK_LAST = "#403f3a"
LIGHT_ENTRY = "#c6c2b8"
DARK_ENTRY = "#0a0a09"

# The two sides. Blue takes the light pieces and red the dark ones, and each is outlined
# in the other's shade so both read on both squares.
BLUE = "#f6f4ef"
BLUE_DK = "#0d0d0c"
RED = "#0d0d0c"
RED_DK = "#f6f4ef"

SELECT = "#0d0d0c"
# Destinations can no longer differ by colour, so they differ by shape: a jump is a filled
# disc, a push an open ring, a freeing push an open square. The legend in the panel says
# so, since a shape code has to be told where a colour code could just be seen.
JUMP = "#0d0d0c"
PUSH = "#0d0d0c"
FREE = "#0d0d0c"
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
def bevel(c, x0, y0, x1, y1, depth, hi, lo, raised=True):
    top, bottom = (hi, lo) if raised else (lo, hi)
    for i in range(depth):
        c.create_line(x0 + i, y0 + i, x1 - i, y0 + i, fill=top)
        c.create_line(x0 + i, y0 + i, x0 + i, y1 - i, fill=top)
        c.create_line(x0 + i, y1 - i, x1 - i + 1, y1 - i, fill=bottom)
        c.create_line(x1 - i, y0 + i, x1 - i, y1 - i, fill=bottom)


# A filled panel with that edge on it.
def plate(c, x0, y0, x1, y1, fill, hi, lo, depth=2, raised=True):
    c.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
    bevel(c, x0, y0, x1, y1, depth, hi, lo, raised)


# Text with a hard shadow under it, which is what makes lettering look cut rather than
# printed. The shadow goes down-right, matching the light the bevels assume.
def engrave(c, x, y, text, font, fill, shadow=INK, **kw):
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
# make: a cone, standing three chips high -- which is also what a dragon is worth. Its
# foot is narrower than a chip, so a dragon and the three-high stack next to it are told
# apart by the taper first and the footprint second.
#
# Narrow matters more than it looks. A cone only has a silhouette while its apex projects
# clear of its base; past that the eye is over the point and a cone is honestly a disc.
# The pitch that happens at is atan(h/r), so 0.48 over 0.26 keeps the point up to about 62
# degrees, and the board opens at 55. A broader foot looks better standing still and turns
# into a dome the moment the board is tilted back, which is when it matters.
R_DRAGON = 0.26
H_DRAGON = 3 * H_CHIP

# Where the band round the cone sits, as a fraction of the way up the slant. Low, because
# a fraction of the slant sits higher on screen than it sounds: what shows above the band
# is the near face and the whole of the far one, so a band at the honest middle reads as a
# hat. Wide, because the band is the whole of what a lid icon used to say and a hairline
# would not survive the piece being small on screen.
RING_LO = 0.26
RING_HI = 0.46

STUD_R = 0.09
STUD_H = 0.05

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
# square free of lettering. Which side a piece belongs to is its fill: blue is the light
# one, red the dark. Each is drawn with the other's shade as its outline, so a dark piece
# on a dark square and a light one on a light square both still have an edge.
#
#   royal    square      the one that has to arrive last, and the one worth most
#   spy      triangle    the one everything else gathers on to
#   pawn     disc        one per pawn, up to four
#
# There is no dragon here. It is the one piece that never shares a square with anything,
# so it never has to be told apart from its neighbours on a lid -- it is drawn as a cone
# instead, and the shape of the piece is the whole of the icon. See cone().

# A pale halo is laid down under every piece before the piece itself. Without it the code
# inverted with the square: a light piece read as a hollow ring on a white square and as a
# solid disc on a black one, and its opponent did the same the other way round, so which
# side a piece belonged to depended on what it happened to be standing on. With the halo
# both sides sit on the same pale ground wherever they are, and the rule holds everywhere
# -- blue has a light centre, red a dark one.
PIECE_HALO = "#f4f1ea"

# The shades a chip's wall is banded in, and the line drawn round its foot. Standing up,
# the wall is where most of a piece's ink is, so this is where the side is now said -- the
# lid has to stay pale for both sides, for the same reason the halo did.
#
# The last of the four is what keeps a pale chip on a pale square from disappearing. Three
# shades of near-white are a cylinder on a dark square and a smudge on a light one; the
# rim line is the outline the flat pieces had, kept for the same reason they had it.
#
#          mid       highlight   shadow      rim
CHIP_WALL = {
    0: (BLUE, "#ffffff", "#b6b1a7", "#6f6a62"),
    1: (RED, DARK_HI, DARK_LO, INK),
}


def pieceFill(side):
    return RED if side else BLUE


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
        c.create_polygon(flat, fill=fill, outline=lo, width=1, tags=tags)
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
        c.create_line(x0, y0, x1, y1,
                      fill=top if (nx + ny) < 0 else bottom, tags=tags)


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
    c.create_line(px - rx, lid, px - rx, base, fill=wallEdge, tags=tags)
    c.create_line(px + rx, lid, px + rx, base, fill=wallEdge, tags=tags)
    c.create_arc(px - rx, base - ry, px + rx, base + ry, start=180, extent=180,
                 style="arc", outline=wallEdge, width=1, tags=tags)

    c.create_oval(px - rx, lid - ry, px + rx, lid + ry,
                  fill=topFill, outline=topEdge, width=w, tags=tags)
    return lid


# The dragon. A cone is harder than a chip for one reason: a cylinder's silhouette is its
# two vertical sides at every angle the board can be turned to, and a cone's is a pair of
# tangents that walk round the base as it tilts, until at a steep enough pitch the apex
# falls inside the base and there is no silhouette left at all.
#
# Rather than case-split on that, the surface is drawn as what it is -- a fan of triangles
# from the apex out to the rim, laid down far ones first. That comes out right in both
# regimes, and it shades without needing a clip: each triangle takes the band its own
# position on screen puts it in, the same three bands lit from the same top left as the
# chip's wall, so a cone and a chip standing next to each other agree about the light.
#
# `ring` is a band round the middle in the other side's shade. It is the whole of what
# this piece says about itself now -- there is no lid to paint an icon on -- and it is
# drawn face by face inside the same loop so it inherits the same near-far ordering. The
# cone is ruled from apex to rim and the projection is affine, so a point a fraction of
# the way up a rule really is the straight lerp of the two ends on screen.
def cone(c, view, x, y, z0, r, h, walls, ring=None, w=2, tags=None):
    wallMid, wallHi, wallLo, wallEdge = walls
    tags = tags or ()
    px, base = view.project(x, y, z0)
    rx = r * view.scale
    ry = r * view.scale * view.sinP
    d = h * view.scale * view.cosP
    ax, ay = px, base - d

    # The rim in screen terms. A circle lying flat projects to an ellipse square-on to the
    # screen whatever the yaw -- the same fact View.disc leans on -- so it can be walked
    # here directly, and how near the camera a rim point is is just how far down the screen
    # it has fallen.
    steps = 20 if view.detail else 10
    rim = []
    for i in range(steps):
        t = 2.0 * math.pi * i / steps
        rim.append((px + rx * math.cos(t), base + ry * math.sin(t)))

    band = rx * 0.35

    # `sx` is a face's own place across the piece, not across the canvas
    def shade(sx):
        if not view.detail: return wallMid
        if sx < px - band: return wallHi
        return wallMid if sx <= px + band else wallLo

    def up(p, f):
        return (p[0] + (ax - p[0]) * f, p[1] + (ay - p[1]) * f)

    faces = [(rim[i], rim[(i + 1) % steps]) for i in range(steps)]
    faces.sort(key=lambda f: f[0][1] + f[1][1])

    # Every face outlined in its own fill: neighbours share an edge, and a hairline of
    # board showing between two of them reads as a crack down the piece. That overlap is
    # also why a face carrying a band has to be cut into three pieces at the band's edges
    # rather than drawn whole and banded over. Cut, the next face round overpaints its
    # neighbour foot on foot and band on band; whole, its foot would overpaint the
    # neighbour's band, and the piece came out with a spoke in the ring for every face.
    def facet(pts, fill):
        flat = []
        for sx, sy in pts:
            flat.append(sx)
            flat.append(sy)
        c.create_polygon(flat, fill=fill, outline=fill, width=1, tags=tags)

    apex = (ax, ay)
    for p0, p1 in faces:
        fill = shade(0.5 * (p0[0] + p1[0]))
        if not ring:
            facet((apex, p0, p1), fill)
            continue
        a0, a1 = up(p0, RING_LO), up(p1, RING_LO)
        b0, b1 = up(p0, RING_HI), up(p1, RING_HI)
        facet((p0, p1, a1, a0), fill)
        facet((a0, a1, b1, b0), ring)
        facet((b0, b1, apex), fill)

    # The silhouette, over the top of the fan: two tangents and the front of the rim while
    # the apex stands clear, and the rim on its own once it doesn't. The tangents touch
    # where the polar line of the apex cuts the base ellipse, which at a low pitch is a
    # long way round from the widest point -- drawing them to the widest point instead is
    # the wrong figure, and looks it.
    if d > ry:
        t0 = math.asin(ry / d)
        pts = []
        for i in range(steps + 1):
            t = -t0 + (math.pi + 2.0 * t0) * i / float(steps)
            pts.append(px + rx * math.cos(t))
            pts.append(base + ry * math.sin(t))
        c.create_line(pts, fill=wallEdge, width=w, tags=tags)
        c.create_line(ax, ay, pts[0], pts[1], fill=wallEdge, width=w, tags=tags)
        c.create_line(ax, ay, pts[-2], pts[-1], fill=wallEdge, width=w, tags=tags)
    else:
        c.create_oval(px - rx, base - ry, px + rx, base + ry,
                      fill="", outline=wallEdge, width=w, tags=tags)
    return ay


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
class StoneChoice(tk.Frame):
    def __init__(self, parent, text, variable, value=None, command=None, fg=None):
        tk.Frame.__init__(self, parent, bg=PANEL)

        self.variable = variable
        self.value = value
        self.command = command
        self.enabled = True
        self.colour = fg or TEXT

        self.mark = tk.Label(self, font=FONT["mark"], bg=PANEL, fg=TEXT_KEY, padx=0)
        self.mark.pack(side="left")
        self.label = tk.Label(self, text=" " + text, font=FONT["body"], bg=PANEL,
                              fg=self.colour, anchor="w")
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
            self.mark.configure(fg=PANEL_LT)
            self.label.configure(fg=TEXT_DIM)
        else:
            self.mark.configure(fg=TEXT_KEY if on else TEXT_DIM)
            self.label.configure(fg=self.colour)

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
    return "Red" if contr else "Blue"


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

        # the setup screen is a column of controls and wants no more room than it asks for
        self.viewReady = False
        self.root.resizable(False, False)
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
        self.depthVar = tk.IntVar(value=3)
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
        for value, text, colour in ((0, "Blue  —  enters first", BLUE),
                                    (1, "Red  —  moves first", RED)):
            b = StoneChoice(self.sideBox, text, self.sideVar, value, fg=colour)
            b.pack(fill="x", pady=1)
            self.sideButtons.append(b)

        # depth
        self.aiBox = self.carvedBox(self.frame, "COMPUTER")

        tk.Label(self.aiBox, text="Search depth", bg=PANEL, fg=TEXT,
                 font=FONT["body"], anchor="w").pack(fill="x")

        row = tk.Frame(self.aiBox, bg=PANEL)
        row.pack(fill="x", pady=(2, 2))
        # A spinbox is a native control here and would not take any of this palette. Six
        # is the whole range anyway, so it costs nothing to lay it out and gains a control
        # that matches everything around it.
        self.depthButtons = []
        for value in range(1, 7):
            b = StoneChoice(row, str(value), self.depthVar, value)
            b.pack(side="left", padx=(0, 12))
            self.depthButtons.append(b)

        tk.Label(self.aiBox, text="4 or 5 recommended — 5 takes a couple of seconds a move, "
                                  "6 about ten",
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

        tk.Label(self.aiBox, text="0 opens the same way every game. The seed is logged, so an "
                                  "opening worth seeing again can be played again.",
                 bg=PANEL, fg=TEXT_DIM, font=FONT["small"], wraplength=400,
                 justify="left", anchor="w").pack(fill="x")

        StoneButton(self.frame, "START GAME", self.startGame).pack(anchor="w", pady=(4, 0))

        self.refreshSetup()

    def showNoise(self):
        self.noiseValue.configure(text="%3d" % self.noiseVar.get())

    # Side only means something in a 1 player game, and there is no computer to configure
    # in a 2 player one.
    def refreshSetup(self):
        for b in self.sideButtons: b.setEnabled(self.modeVar.get() == 1)

        playsItself = self.modeVar.get() != 0
        for b in self.depthButtons: b.setEnabled(playsItself)
        self.noiseScale.setEnabled(playsItself)
        self.noiseValue.configure(fg=TEXT_KEY if playsItself else TEXT_DIM)

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
        self.root.bind("<Home>", lambda e: self.resetView())

        self.viewW = openW
        self.viewH = openH
        self.view.fit(self.viewW, self.viewH)

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

        self.breakFrame = tk.Frame(panel, bg=PANEL)
        self.breakFrame.pack(fill="x", pady=(6, 10))

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
        # Every line is already headed "Blue:" or "Red:", so in one colour the side is
        # said rather than shown and both read in ink. Only the asides are dimmed.
        self.logText.tag_configure("blue", foreground=TEXT)
        self.logText.tag_configure("red", foreground=TEXT)
        self.logText.tag_configure("grey", foreground=TEXT_DIM)
        self.logText.configure(state="disabled")

        buttons = tk.Frame(panel, bg=PANEL)
        buttons.pack(fill="x")
        StoneButton(buttons, "NEW GAME", self.buildSetup,
                    font=FONT["small"]).pack(side="left")
        StoneButton(buttons, "RESET VIEW", self.resetView,
                    font=FONT["small"]).pack(side="left", padx=(8, 0))

    # In one colour the board says everything by shape, and a shape has to be told where a
    # colour could just be seen. Drawn with the same icon functions the board uses, so the
    # key can never drift from what it is describing.
    def buildLegend(self, parent):
        c = tk.Canvas(parent, width=296, height=206, bg=PANEL, highlightthickness=0)
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
        # code the board draws it with -- standing on a baseline of its own rather than
        # centred on the row, since a piece that stands up has a foot and not a centre.
        key.ox, key.oy = 164.0, 60.0
        key.scale = 56.0   # a shade larger than the piles below: it is a smaller piece
        cone(c, key, 0.0, 0.0, 0.0, R_DRAGON, H_DRAGON,
             CHIP_WALL[0], INK, 1)
        key.scale = 40.0
        c.create_text(182, 55, text="dragon", font=FONT["small"], fill=TEXT_DIM,
                      anchor="w")

        # The board stands its pieces up now, so the key has to as well. The shapes above
        # are what a lid can have on it; the piles below are what a square looks like from
        # the side, and which side of them it belongs to.
        heading(80, "SIDES")
        pile(4, 128, 3, 0, "blue")
        pile(150, 128, 3, 1, "red")
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

        if mode != 0:
            noise = self.noiseVar.get() / 100.0
            seed = artificialPlayer.setEntryNoise(noise)
            if noise: self.log("Entering seed: " + str(seed), "grey")
            else: self.log("Fixed opening (no entering noise).", "grey")

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
        self.movingPris = False
        self.spyBreak = False
        self.hasPris = False
        self.aiBusy = False

        Engine.koReset()
        artificialPlayer.newGame()

        self.log("ENTERING — royals first, then the four pawns, then the spies.", "grey")
        self.log("A royal or pawn can't enter touching something you already control, your "
                 "dragon included. A spy goes anywhere empty.", "grey")
        self.log("Drag anywhere on the board to turn it. Double click off the play, or "
                 "RESET VIEW, to put it back.", "grey")

        # there is a board now, so the canvas may draw
        self.viewReady = True
        self.advance()

    ################################################################################
    ####### THE LOOP ###############################################################
    ################################################################################
    # Everywhere MainPlay would call input(), one of these returns instead and the click
    # handler picks the game back up.

    def advance(self):
        self.redraw()
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
                self.log(sideName(contr) + " has nowhere to enter a "
                         + PIECE_NAMES[piece] + " — skipped.", "grey")
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
            self.runAI(lambda b=self.board, c=contr, p=piece, s=isSpy:
                       artificialPlayer.chooseEntry(b, c, p, s), self.finishAIEntry)
            return

        self.startPlay()

    def finishAIEntry(self, square):
        contr = self.entryContr
        piece = self.entryPiece

        # chooseEntry gives back None when there is nowhere, though enterStep has already
        # checked that there is somewhere.
        if square is None:
            self.log(sideName(contr) + " has nowhere to enter a "
                     + PIECE_NAMES[piece] + " — skipped.", "grey")
        else:
            self.board = Engine.dropPiece(self.board, square, contr, piece)
            self.lastMove = [square]
            self.log(sideName(contr) + " " + PIECE_NAMES[piece] + " enters at "
                     + Hasher.IndexToAlg(square - 1).upper(), "red" if contr else "blue")

        self.enterIndex += 1
        self.advance()

    def enterClick(self, square):
        contr = self.entryContr

        if square in self.entryOptions:
            self.board = Engine.dropPiece(self.board, square, contr, self.entryPiece)
            self.lastMove = [square]
            self.log(sideName(contr) + " " + PIECE_NAMES[self.entryPiece] + " enters at "
                     + Hasher.IndexToAlg(square - 1).upper(), "red" if contr else "blue")
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
        self.clearBreaks()
        self.prisCheck.setEnabled(False)
        self.freeCheck.setEnabled(False)
        self.prisVar.set(0)

        # A side with nothing to move loses its turn. MainPlay only ever hit this for the
        # computer; a human can be stuck just as easily, so the check is made for both.
        moves = artificialPlayer.listAllMoves(self.board, contr)
        if not moves:
            self.legalOrigins = set()
            self.log(sideName(contr) + " has no legal move, passing.", "grey")
            self.passes += 1
            self.turn += 1

            if self.passes > 1:
                self.finish("Neither side can move.", None)
                return

            self.redraw()
            # back through the event loop rather than straight down the stack
            self.root.after(400, self.advance)
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
            self.log(sideName(contr) + " has no legal move, passing.", "grey")
            self.passes += 1
            self.turn += 1

            if self.passes > 1:
                self.finish("Neither side can move.", None)
                return

            self.root.after(400, self.advance)
            return

        self.log(sideName(contr) + ": " + artificialPlayer.describeMove(move),
                 "red" if contr else "blue")
        self.log("   score " + artificialPlayer.scoreText(score) + ", "
                 + str(artificialPlayer.calcCount) + " boards considered", "grey")

        origin = move[artificialPlayer.MOVE_ORIGIN]
        # a break has a direction where the others have a square, so the origin is the only
        # square there is to highlight
        if move[artificialPlayer.MOVE_KIND] == "break": target = origin
        else: target = move[artificialPlayer.MOVE_TARGET] + 1
        self.commit(board, [origin, target])

    ####### A COMPLETED MOVE #######
    # Ko, the win check and the turn counter, in the order MainPlay does them. Returns
    # whether the move stood.
    def commit(self, board, squares):
        # A move that puts the game back into a position it has already stood in is taken
        # back and the side has another go. The computer is filtered at the root of its
        # search and never gets here; this is what stops a human doing it.
        if Engine.koBreaks(board):
            self.setHint("Ko: the game has already stood there. Move again.")
            self.log("Ko — move taken back.", "grey")
            self.selected = None
            self.moveArray = []
            self.clearBreaks()
            self.redraw()
            return False

        self.board = board
        self.lastMove = squares
        Engine.koRecord(board)
        self.passes = 0
        self.turn += 1

        gameEnd, winner = Hasher.Check_For_Winner(board)
        if gameEnd:
            if winner == [1, 0]: self.finish("Blue wins!", 0)
            elif winner == [0, 1]: self.finish("Red wins!", 1)
            else: self.finish("A tie.", None)
            return True

        self.advance()
        return True

    def finish(self, text, contr):
        self.phase = "over"
        self.selected = None
        self.moveArray = []
        self.clearBreaks()
        self.prisCheck.setEnabled(False)
        self.freeCheck.setEnabled(False)
        self.setStatus(text, contr)
        self.setHint("Game finished.")
        if contr is None: self.log("Game finished. " + text, "grey")
        else: self.log("Game finished. " + text, "red" if contr else "blue")
        self.redraw()

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

        if self.aiBusy or self.phase == "over": return

        square = self.squareAt(event.x, event.y)
        if square is None: return
        self.boardClick(square)

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
        if not self.clickWasInert(self.squareAt(event.x, event.y)): return
        self.resetView()

    def clickWasInert(self, square):
        if square is None: return True
        if self.aiBusy or self.phase == "over": return True

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
            self.clearBreaks()
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

        self.clearBreaks()
        if self.moveArray:
            jumps = len(self.moveArray[0])
            pushes = len(self.moveArray[1])
            frees = len(self.moveArray[5])
            for word, heading in zip(self.moveArray[4], self.moveArray[2]):
                StoneButton(self.breakFrame, word.upper(),
                            lambda h=heading: self.playBreak(h),
                            font=FONT["small"]).pack(side="left", padx=(0, 5))

            # only worth offering the choice where a square is under both, which is the
            # only case the box decides anything
            both = set(self.moveArray[1]) & set(self.moveArray[5])
            self.freeCheck.setEnabled(bool(both))

            bits = []
            if jumps: bits.append(str(jumps) + " jump" + ("s" if jumps != 1 else ""))
            if pushes: bits.append(str(pushes) + " push" + ("es" if pushes != 1 else ""))
            if frees: bits.append(str(frees) + " freeing")
            if self.moveArray[4]: bits.append(str(len(self.moveArray[4])) + " break")

            self.setHint(Hasher.IndexToAlg(self.selected - 1).upper() + ": "
                         + (", ".join(bits) if bits else "nothing legal")
                         + ".  Click it again to put it back down.")
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
        self.log(sideName(self.contr) + ": " + artificialPlayer.describeMove(move),
                 "red" if self.contr else "blue")
        self.commit(board, [origin, square])

    def playBreak(self, heading):
        origin = self.selected
        board = Engine.exeBreak(self.board, origin, heading, self.contr)

        move = (origin, "break", Engine.pushIndex(heading), False)
        self.log(sideName(self.contr) + ": " + artificialPlayer.describeMove(move),
                 "red" if self.contr else "blue")
        self.commit(board, [origin])

    def clearBreaks(self):
        for child in self.breakFrame.winfo_children(): child.destroy()

    ################################################################################
    ####### THE COMPUTER'S THREAD ##################################################
    ################################################################################
    # Depth 6 is about ten seconds, and tkinter redraws nothing while a callback is
    # running. So the search goes on a worker and the result comes back through a queue
    # the main thread polls. The worker only ever reads the board it was handed --
    # Mod_Space returns a new board rather than editing one, so there is nothing here
    # for the two threads to disagree about.

    def runAI(self, work, done):
        self.aiBusy = True
        results = queue.Queue()

        def run():
            try: results.put(("ok", work()))
            except Exception as error: results.put(("error", error))

        threading.Thread(target=run, daemon=True).start()
        self.pollAI(results, done)

    def pollAI(self, results, done):
        try: kind, payload = results.get_nowait()
        except queue.Empty:
            self.root.after(60, lambda: self.pollAI(results, done))
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
    # DisplayHashBoard prints every square twice, once on a blue row and once on a red
    # one, because a square can hold one side's stack and the other side's prisoners at
    # the same time. Here that is one square with the stack standing on it and the
    # prisoners in a slot along its near edge, drawn in the shade of whoever lost them.

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

        # the near two strips of lettering, held back so the pieces don't stand on them
        self.drawCoords(v, True)

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
            c.create_polygon(wall, fill=EDGE_DK, outline=RULE_DK, width=1)

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
                         (INK, EDGE_DK, RULE_LT, INK), EDGE_DK, INK, 1)

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
                # A cone three chips high where everything else is a pile, so the piece
                # that cannot stack cannot be mistaken for a stack of three. It carries no
                # icon: there is no lid to put one on, and a cone is already a shape
                # nothing else on the board has. What the diamond's punched hole used to
                # say -- this is neither a royal nor anything that can be gathered -- is
                # said instead by a band round its middle in the other side's shade, which
                # has the second virtue of showing up whichever square it is standing on.
                cone(c, v, bx, by, 0.0, R_DRAGON, H_DRAGON,
                     walls, PIECE_HALO if side else INK, 2, tag)
            else:
                # One chip per piece, all the same chip, piled up -- the same width the
                # whole way up, so the pile is one column and not a stepped one. What
                # finds the head of it is the pale lid, which no chip below the top one
                # shows any of.
                n = s[Hasher.SPY] + s[Hasher.PAWNS] + s[Hasher.ROYAL]
                for k in range(n):
                    chip(c, v, bx, by, k * H_CHIP, R_CHIP, H_CHIP,
                         walls, PIECE_HALO, INK, 2, tag)

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
                    planeRow(c, v, upper, bx, by, lz, 0.0, -0.15, 0.120, 0.048, side, 2, tag)
                    planeRow(c, v, pawns, bx, by, lz, 0.0, 0.16, 0.078, 0.026, side, 2, tag)
                elif upper:
                    r = 0.160 if len(upper) == 1 else 0.145
                    planeRow(c, v, upper, bx, by, lz, 0.0, 0.0, r, 0.065, side, 2, tag)
                elif pawns:
                    r = 0.100 if len(pawns) <= 2 else 0.082
                    planeRow(c, v, pawns, bx, by, lz, 0.0, 0.0, r, 0.030, side, 2, tag)

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
                             fill="", outline=tone, width=2,
                             dash=(3, 3) if v.detail else (), tags=tag)

        # the chosen square gets a solid double rule, so it never reads as merely available
        if square == self.selected:
            c.create_polygon(v.poly(self.inset(wx, wy, 0.476), zTile),
                             fill="", outline=counter, width=4, tags=tag)
            c.create_polygon(v.poly(self.inset(wx, wy, 0.439), zTile),
                             fill="", outline=tone, width=2, tags=tag)

        ####### where it could go #######
        if self.moveArray and self.phase == "play":
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
                             fill="", outline=tone, width=4,
                             dash=(7, 5) if v.detail else (), tags=tag)
            return

        # the rings hold the icons' squash floor rather than the honest one, so that a
        # ring is still recognisably a ring when the board is lying nearly flat
        px, py = v.project(wx, wy, z)
        sx, sy = v.planeScale()
        rx, ry = 0.43 * sx, 0.43 * sy
        c.create_oval(px - rx, py - ry, px + rx, py + ry,
                      fill="", outline=tone, width=4,
                      dash=(7, 5) if (kind == "push" and v.detail) else (), tags=tag)

    ################################################################################
    ####### PANEL ##################################################################
    ################################################################################

    # Whose turn it is was the one thing the colour said on its own. It is marked instead
    # with the shape the side's pieces wear: hollow for blue, filled for red.
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
