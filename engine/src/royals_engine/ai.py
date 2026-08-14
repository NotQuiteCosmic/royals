import math
import operator
import random

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import perlin as Perlin
from royals_engine import _accel

# Every accelerated function below keeps its pure-Python body untouched underneath a two-line
# shim. That is the whole design: the Python is still the reference implementation and still
# what golden_moves.txt was recorded from, so the compiled engine is checked against it rather
# than replacing it. `ROYALS_NO_ACCEL=1` takes the Python path even where the wheel is
# installed, which is what the differential CI job runs on. See _accel.py.
#
# What is NOT delegated, and won't be: everything from "Entering" downwards. It runs on Perlin
# noise over floats seeded through random.Random, so it is bound to CPython's Mersenne Twister
# -- reproducing that in another language is exacting work for a path called a dozen times a
# game that contributes nothing to search speed. golden_enter.txt exists to keep that
# distinction visible.

# the ordering sort's key, made once rather than as a fresh lambda per node
FIRST = operator.itemgetter(0)

##############################
####### Some AI Garbge #######
##############################
# Ported from the backup's AI section onto the hashed board. The big structural win here is
# that nothing gets copied: Mod_Space returns a fresh board rather than editing in place, so
# a child board is just a return value and the parent survives untouched. The 2D version had
# to copy.deepcopy the whole board at every node of the tree.

# counts how many boards the last search looked at, the way the backup's global did
calcCount = 0

####### What a move is #######
# A move is one flat tuple:
#
#   (origin, kind, target, movingPris)
#
#   origin      -- 1-based square it leaves from
#   kind        -- "jump", "push", "free" or "break"
#   target      -- for the first three, the 0-based square it goes to, as checkMoves reports
#                  them; for a break, an index into Engine.pushDirs
#   movingPris  -- whether the prisoners on the origin come along
#
# It used to be [origin, [kind, target], movingPris], a list holding a list, which meant it
# could not be a dictionary key -- so the killer and history tables were fed a tuple built
# fresh from it every time a move was so much as looked at. In a depth-6 search that came to
# over 400,000 tuples built for no other purpose. Flat and hashable, a move is its own key.
MOVE_ORIGIN = 0
MOVE_KIND = 1
MOVE_TARGET = 2
MOVE_PRIS = 3

# PROTOTYPE (Engine.PUSH_RANGE): a fifth element on a push, holding how far the mover chose to
# travel. Present only when the ceiling is above 1, so off the flag every move is the four-tuple
# it has always been and nothing that reads one can tell the difference. The places that
# unpacked a move into four names take move[:4] for that reason.
MOVE_TRAVEL = 4

# matches the labels checkMoves puts in alphBreaks, indexed by direction the same way
HEADINGS = Engine.HEADINGS


# The AI picks its own square instead of asking, so it needs the origin object getOrigin
# would otherwise hand back. Building one is Engine's business now that an origin is just
# the square and its code; this stays as the name the GUI and the search already call.
def makeOrigin(cBoard, square, movingPris = False, spyBreak = False):
    return Engine.makeOrigin(cBoard, square, movingPris, spyBreak)


####### Board geometry #######
# How far apart two squares are in lone-piece jumps, and how many jumps a lone piece has
# from each square. Both are measured off checkMoves at import rather than written down, so
# if the jump rules move again everything built on them moves too.
#
# Three facts fall out of these tables, and the entering heuristic leans on all three:
#   - jumps run along diagonals for as many squares as the stack weighs, so they never
#     change a square's colour. Two squares of opposite colour come back UNREACHABLE, which
#     is why nothing here needs a separate parity term.
#   - the outer ring of the board has two jumps out of it where the inside has four.
#   - a stack holding a spy can't jump onto anything and nothing can jump onto a royal, so
#     the spy is the only piece the others can gather on to, and the royal arrives last.

UNREACHABLE = 12

JUMPREACH = {}
JUMPDIST = {}

def buildJumpTables():
    empty = Hasher.EMPTY_BOARD

    graph = {}
    for square in range(1, 50):
        board = Engine.dropPiece(empty, square, 0, Hasher.PAWNS)
        # checkMoves hands back 0-based targets
        graph[square] = [t + 1 for t in Engine.checkMoves(board, makeOrigin(board, square), 0)[0]]
        JUMPREACH[square] = len(graph[square])

    # every square's distance to every other, breadth first over that graph. A lone piece
    # is the slowest thing on the board -- stacks jump their own weight -- so these are an
    # over-estimate, but a consistent one, which is all a ranking needs.
    for start in range(1, 50):
        found = {start: 0}
        frontier = [start]

        while frontier:
            onwards = []
            for square in frontier:
                for target in graph[square]:
                    if target in found: continue
                    found[target] = found[square] + 1
                    onwards.append(target)
            frontier = onwards

        # anything the walk never reached is on the other colour: not far away, but shut off
        JUMPDIST[start] = [found.get(square, UNREACHABLE) for square in range(0, 50)]


buildJumpTables()


# Every square the controller could move out of. A square where its pieces are being held
# prisoner belongs to the other side, so it never shows up here.
def makePossList(cBoard, contr, spaces = None):
    if spaces is None: spaces = Hasher.Parse_Board(cBoard)

    possList = []
    for square in range(1, 50):
        s = spaces[square - 1]

        # a square the other side holds is still playable if it is holding our spy: the
        # spy can break out of it, and that is the only move it has
        if s[Hasher.SIDE] != contr:
            if s[Hasher.CAPSPY]: possList.append(square)
            continue

        if s[Hasher.MYPIECES]: possList.append(square)
    return possList


# Flattens a checkMoves result into moves out of one square, in the shape described up top.
# The backup kept squares and direction words in one possMoves list and had to keep pulling
# the string "break" back out of it; tagging avoids that entirely.
def listMoves(moveArray, origin, movingPris, into, cBoard = None, spaces = None, contr = None):
    if not moveArray: return

    for target in moveArray[0]: into.append((origin, "jump", target, movingPris))

    # PROTOTYPE (Engine.PUSH_RANGE): a push is one move per distance it may travel, so a
    # four-strength stack shoving a lone pawn offers four. Off the flag the ceiling is 1
    # everywhere and this emits exactly the one move it always has, in the same order.
    #
    # The fifth element is the distance. Everything that reads a move by index -- MOVE_ORIGIN
    # through MOVE_PRIS -- is unaffected; the handful of places that unpacked a move into
    # four names take move[:4] now, which reads the same either way.
    for target in moveArray[1]:
        ceiling = 1
        if Engine.PUSH_RANGE and cBoard is not None:
            direction = Engine.PUSHFROM[origin][target + 1]
            if direction is not None:
                pRange = Engine.getLegalPushLength(cBoard, origin, cBoard[origin - 1], direction,
                                                   contr, movingPris, False, spaces)
                ceiling = Engine.pushMaxTravel(cBoard, origin, cBoard[origin - 1], direction,
                                               pRange, spaces)
        # Distance 1 is the plain four-tuple, whatever the ceiling: absent means one square,
        # so spelling it out would be a second way of writing the same move and the two
        # engines would have to agree about which one to emit. They do agree -- by there
        # being only one shape.
        into.append((origin, "push", target, movingPris))
        for step in range(2, ceiling + 1):
            into.append((origin, "push", target, movingPris, step))
    # a break scatters the whole square, so there is no carrying-prisoners variant of one
    if not movingPris:
        for heading in moveArray[2]: into.append((origin, "break", heading, False))
    # A push into allies being held is two moves onto one square -- shove the whole thing
    # along, or free them and stand where they stood. Both get listed, and a square can
    # appear in possPushes and possFrees at once, which is the point of keeping them apart.
    for target in moveArray[5]: into.append((origin, "free", target, movingPris))


# Applies one move and returns the resulting board, leaving cBoard alone.
# The kinds performOneStep knows how to execute. Written down rather than left implicit in
# the if-chain below, which is what let an unknown kind fall through to being treated as a
# break -- see checkMove.
KINDS = ("jump", "push", "free", "break")


def checkSide(contr):
    """A side is 0 or 1. Nothing else indexes anything sensible."""
    if contr not in (0, 1):
        raise ValueError("side must be 0 (blue) or 1 (red), got %r" % (contr,))
    return contr


def checkMove(move):
    """Reject a move that is not one, before anything tries to execute it.

    This exists because the if-chain below used to end in an unconditional
    `return Engine.exeBreak(...)`, so a move with a kind of "wobble" -- or a typo, or a
    tuple built by hand in a REPL -- was silently executed as a break and handed back a
    board. Nothing raised, and the caller got a position that had nothing to do with what
    it asked for. A move that isn't a move should say so.

    The bounds match Hasher.Get_Space_Data's: squares run 1 to 49, and an origin outside
    that used to reach `cBoard[-1]` and quietly execute against the last square of the
    board. A break's target is a direction index rather than a square, which is the
    asymmetry notation.py exists to contain.
    """
    origin, kind, target, _pris = move[:4]

    if kind not in KINDS:
        raise ValueError("%r is not a move kind (one of %s)" % (kind, ", ".join(KINDS)))

    if not isinstance(origin, int) or origin < 1 or origin > 49:
        raise IndexError("origin %r is off the board (squares run 1 to 49)" % (origin,))

    if kind == "break":
        if not isinstance(target, int) or target < 0 or target >= len(Engine.pushDirs):
            raise IndexError("break direction %r is not one of 0 to %d"
                             % (target, len(Engine.pushDirs) - 1))
    elif not isinstance(target, int) or target < 0 or target > 48:
        raise IndexError("target %r is off the board (0-based squares run 0 to 48)" % (target,))

    return move


def performOneStep(cBoard, contr, move):
    # Validated before the shim, not inside each branch, so both implementations refuse the
    # same input in the same way. The compiled side validates again for anything calling it
    # directly, but this is what makes the two agree.
    checkSide(contr)
    checkMove(move)

    if _accel.accel is not None:
        # Comes back a tuple, not a list. A board is its own key in the ko set and the
        # transposition table, so a list here would silently stop matching.
        return _accel.accel.perform_one_step(cBoard, contr, move)

    kind = move[MOVE_KIND]
    if kind == "jump":
        return Engine.exeMove(cBoard, move[MOVE_ORIGIN], move[MOVE_TARGET] + 1, contr, move[MOVE_PRIS])
    if kind == "push":
        # A fifth element is the distance the mover chose; absent means one square, which is
        # what a push has always been and what every record written before this rule says.
        travel = move[MOVE_TRAVEL] if len(move) > MOVE_TRAVEL else None
        return Engine.exePush(cBoard, move[MOVE_ORIGIN], move[MOVE_TARGET] + 1, contr,
                              move[MOVE_PRIS], False, True, travel)
    if kind == "free":
        return Engine.exePush(cBoard, move[MOVE_ORIGIN], move[MOVE_TARGET] + 1, contr, move[MOVE_PRIS], True)
    return Engine.exeBreak(cBoard, move[MOVE_ORIGIN], move[MOVE_TARGET], contr)


# Every move available to a side, as [origin, move, movingPris] triples. Squares holding
# prisoners get their jumps and pushes listed twice: once leaving the prisoners behind and
# once dragging them along. A break always scatters the whole square, so it has no variant.
# A push into allies being held is listed twice too, as 'push' and 'free' -- but that pair
# comes out of checkMoves rather than from the loop here, and only ever while not carrying
# prisoners, since a stack bringing its own can't free anybody.
def listAllMoves(cBoard, contr, spaces = None):
    # Checked even though the search calls this at every node: measured at 8.9ns, which is
    # 0.56ms across a depth-6 pure-Python search that takes 2.6 seconds. Two hundredths of a
    # percent is not a reason to leave an entry point unguarded -- and game.py calls this
    # directly, so it is an entry point whatever the search does with it.
    checkSide(contr)

    if _accel.accel is not None:
        # `spaces` is only ever a parse of cBoard the caller already had, so ignoring it
        # changes nothing but who does the walk.
        return _accel.accel.list_all_moves(cBoard, contr)

    if spaces is None: spaces = Hasher.Parse_Board(cBoard)

    everything = []
    for origin in makePossList(cBoard, contr, spaces):
        s = spaces[origin - 1]

        # our spy on their square: the origin has to carry the spyBreak flag, and breaking
        # out is the only thing checkMoves will offer back
        if s[Hasher.SIDE] != contr:
            tOrigin = makeOrigin(cBoard, origin, False, True)
            listMoves(Engine.checkMoves(cBoard, tOrigin, contr, spaces), origin, False, everything,
                      cBoard, spaces, contr)
            continue

        carrying = [False]
        if s[Hasher.PRISCOUNT]: carrying.append(True)

        for movingPris in carrying:
            tOrigin = makeOrigin(cBoard, origin, movingPris)
            # the board is already parsed, so checkMoves needn't walk it again
            listMoves(Engine.checkMoves(cBoard, tOrigin, contr, spaces), origin, movingPris,
                      everything, cBoard, spaces, contr)
    return everything


####### Judging a position #######

# Scores are whole numbers of thousandths of a point.
#
# They used to be floats, and alpha-beta compares its numbers exactly -- `beta <= alpha`,
# `score > bestScore` -- so a difference in the last place, which is all it takes for two
# interpreters to disagree about a square root or about how a sum was folded, could flip a
# cutoff and change the move played. Measured over 3064 positions, CPython and PyPy differed
# on 400 of them by up to 1.5e-14 relative: never enough to show at four decimal places,
# occasionally enough to pick a different move. The regression suite passed under one
# interpreter and failed under the other on exactly that.
#
# Integers settle it. Every weight below is exact at this scale, including the spread
# penalty, which was the only irrational term -- see spreadPenalty.
SCALE = 1000

# What a won position scores, before the depth scaling minimax puts on it. Comfortably above
# anything the terms below can reach, so a win is never confused with a good position.
WIN_SCORE = 1000 * SCALE

# Stands in for the infinities the search used to open its window with, so that no float
# enters the search at all. A win scaled by depth stays far below this.
INFINITY = WIN_SCORE * 1000

# The weights, in SCALE units. What a jump's worth of mobility is worth against the stacking
# score: JUMPREACH is 4 in the middle of the board and 2 on the rim, so at a quarter point a
# lone piece is worth up to half a point for where it stands and a stack of four up to two --
# a nudge toward good ground, nowhere near enough to turn down a merge for it.
DIAG_WEIGHT = SCALE // 4
# holding a prisoner is worth 0.8, and each separate group costs 1.5
PRISONER_WEIGHT = (SCALE * 4) // 5
GROUP_PENALTY = (SCALE * 3) // 2


# The spread penalty: 1.5 times the larger of the two coordinate standard deviations, in
# SCALE units, exactly.
#
# The standard deviation of n whole numbers with sum s and sum of squares s2 is
# sqrt(n*s2 - s*s) / n, and the quantity under the root is itself a whole number. Both axes
# always have the same n -- a group contributes one coordinate to each -- so which axis is
# the larger can be decided by comparing those two whole numbers, before any root is drawn.
#
# math.isqrt is an exact integer square root, so scaling by (3*SCALE)^2 going in and dividing
# by 2n coming out gives 1.5 * stdev * SCALE with no float involved. It truncates rather than
# rounds, so the answer can sit a thousandth of a point under the true value -- that is a
# rounding of the heuristic, not a disagreement: every machine truncates to the same integer.
def spreadPenalty(n, sx, sx2, sy, sy2):
    q = max(n * sx2 - sx * sx, n * sy2 - sy * sy)
    if q <= 0: return 0

    return math.isqrt(9 * SCALE * SCALE * q) // (2 * n)


# Formats a score the way a person reads it, since the number itself is in thousandths.
def scoreText(score):
    return "%.2f" % (score / SCALE)


# Scores a position from one side's point of view. Stacks are worth the square of their
# size, prisoners you hold are worth 0.8 apiece, pieces of yours in captivity cost double
# what the stack would otherwise be worth, and the whole score is penalised for being spread
# thin. Six pieces on one square is the spy, four pawns and the royal, so it's a win.
#
#
# There used to be a square-colour term here, abs(blackSq * 1.2 - whiteSq) * (5 / groups),
# meant to favour the main diagonals because those are the squares that wrap and so are the
# most mobile on the board. The premise is right -- a main-diagonal square averages 3.38
# jumps out of it against 2.67 for the other squares of its colour -- but the term did the
# opposite. All 13 diagonal squares are one colour and the 1.2 sat on the other, so six
# pieces massed on the diagonal's colour scored 30 against 36 for the colour holding none
# of them. Nor could it have worked with the multiplier moved: it rewards concentration on
# a colour, 25 squares, when the advantage belongs to 13 of them, and the other twelve are
# the least mobile squares there are. Tuning agreed it was doing nothing -- deleting it
# scored -0.5, inside the noise. What replaced it is the DIAG_WEIGHT term below, which asks
# JUMPREACH about the square a piece is actually on instead of counting colours.
#
# One caveat on that: JUMPREACH counts jumps out of a square, which is 4 inside the board
# and 2 on the rim. The main diagonals score well under it (3.38 against 2.67 for the rest
# of their colour) because nine of their thirteen squares are interior -- but their other
# advantage, that they are the squares a jump can wrap round, shows up in distances rather
# than in counts and this doesn't see it. JUMPDIST has it (mean 2.88 against 3.07) if a
# sharper version is ever wanted.
#
# A gather-cost term also stood here for a while: for each candidate gathering square, what
# every group's journey costs, minimised over the squares. It read better -- a smooth
# gradient from six scattered pieces to six on one, where this pays myPieces*myPieces in
# dribs and then jumps to 1000 -- but it cost 1.22x on a depth-3 search and never beat this
# in a measurement that survived scrutiny. The spread penalty below is what actually drives
# play: removing that scores -3.5 with every seed agreeing.
#
# The weights themselves are up with SCALE, since they only mean anything alongside it.


# Scores a position from both sides at once, as [blue, red].
#
# The search wants the difference between the two, and every square has something to say to
# both of them: whoever holds it counts the stack standing there, and the other side counts
# whatever of theirs is being held in it. So one walk answers both questions, where scoring
# each side in turn walked the board twice and asked 49 empty squares to say nothing, twice.
# Empty squares are skipped outright here, and on a real board most of them are empty.
def evaluateSides(cBoard):
    if _accel.accel is not None:
        # A list, matching what the walk below returns -- callers index it and fullCheck
        # subtracts across it, but nothing should discover a tuple where it expected to be
        # able to mutate.
        return list(_accel.accel.evaluate_sides(cBoard))

    unpack = Hasher.UNPACK

    adv = [0, 0]
    groups = [0, 0]
    sx = [0, 0]; sx2 = [0, 0]; sy = [0, 0]; sy2 = [0, 0]
    royalIdiot = [False, False]
    # a side with six on one square scores the win outright, whatever else it has going on
    won = [False, False]

    for square in range(1, 50):
        code = cBoard[square - 1]
        # says nothing to either side
        if not code: continue

        s = unpack[code]
        # the side holding the square, and the side whose people are held in it
        w = s[Hasher.SIDE]
        o = 1 - w

        x = (square - 1) % 7
        y = (square - 1) // 7

        ###### the holder's own stack ######
        myPieces = s[Hasher.MYPIECES]
        if myPieces:
            # the dragon sits outside all of this -- it can't stack, be captured, or win
            if not s[Hasher.DRAGON]:
                groups[w] += 1
                if myPieces < 6: adv[w] += myPieces * myPieces * SCALE
                elif myPieces == 6: won[w] = True

                # drives pieces towards each other
                sx[w] += x; sx2[w] += x * x
                sy[w] += y; sy2[w] += y * y

                # mobile ground is worth standing on. One dict lookup, no search.
                adv[w] += DIAG_WEIGHT * JUMPREACH[square] * myPieces

            # holding prisoners is worth something...
            prisoners = s[Hasher.PRISCOUNT]
            if prisoners: adv[w] += prisoners * PRISONER_WEIGHT

            # DON'T PUT UR DANG ROYAL AND SPY IN THE SAME PLACE
            if s[Hasher.SPY] and s[Hasher.ROYAL] and myPieces != 6: royalIdiot[w] = True

        ###### and the other side's people held in it ######
        # a captured group is never a dragon and never holds prisoners of its own, so the
        # mobility, prisoner and royal-and-spy terms have nothing to say about it
        theirs = s[Hasher.PRISCOUNT]
        if theirs:
            groups[o] += 1
            if theirs < 6: adv[o] += theirs * theirs * SCALE
            elif theirs == 6: won[o] = True

            sx[o] += x; sx2[o] += x * x
            sy[o] += y; sy2[o] += y * y

            # ...and being held costs double
            adv[o] -= theirs * theirs * 2 * SCALE

    for side in (0, 1):
        if won[side]:
            adv[side] = WIN_SCORE
            continue

        # swept off the board, so the terms below have nothing to divide by
        if groups[side] == 0: continue

        # tuning moved each of these off its value in turn and nothing beat where they
        # already stand. The spread penalty is the one that matters: at 0 the AI plays
        # measurably worse.
        adv[side] -= groups[side] * GROUP_PENALTY
        adv[side] -= spreadPenalty(groups[side], sx[side], sx2[side], sy[side], sy2[side])

        # if the royal and the spy are together, the penalty grows as the groups thin out
        if royalIdiot[side]: adv[side] -= (groups[side] * groups[side]) * 5 * SCALE

    return adv


# One side's score. Nothing in the search uses this -- it evaluates both sides at once --
# but it is the shape the heuristic is easiest to read in, and it keeps the two definitions
# from drifting apart by being the same one.
def checkPosition(cBoard, contr, spaces = None):
    checkSide(contr)
    return evaluateSides(cBoard)[contr]


def fullCheck(cBoard, contr):
    # `adv[contr] - adv[1 - contr]` on a two-element list is the whole reason this needs
    # checking: contr=2 reads off the end, and contr=-1 quietly reads the *other* side and
    # returns a plausible number with the sign inverted. The second is worse than the first,
    # because nothing raises.
    checkSide(contr)

    if _accel.accel is not None:
        return _accel.accel.full_check(cBoard, contr)

    adv = evaluateSides(cBoard)
    return adv[contr] - adv[1 - contr]


####### Move ordering #######

# Alpha-beta only prunes when a good move is tried early: search the best move first and it
# examines roughly the square root of the tree, search the worst first and it degenerates
# towards plain minimax. None of what follows changes the score a search returns -- it only
# changes the order moves are tried, and so how much of the tree can be skipped.

# moves that caused a cutoff, keyed by depth, two deep -- the "killer" moves
killers = {}
# a running tally of which moves have caused cutoffs anywhere, keyed by the move itself
history = {}

# Positions already searched, so the same one reached by a different order of moves doesn't
# get searched twice. Keyed on (board, side to move, stamp) -- a board is a tuple of 49
# codes, one canonical value per position that hashes on its own, which is the whole reason
# this is cheap here. The bit-string version had to build a fresh string to key on at every
# node. Each entry is [depth searched, score, what the score means, best move found].
#
# This used to be emptied at the start of every move, so the only reuse it ever got was
# between the deepening passes inside one search. That threw away most of its value: a move
# advances the game by one ply, so the position the next search starts from is one the last
# search had already looked at, and so is most of the tree under it.
#
# What kept it from persisting is that it has to stay bounded -- a depth-9 search alone fills
# it with 800k positions -- and that root answers go stale, which the ko stamp below handles.
#
# Bounding it: entries go into `table`, and when that fills, it becomes `tableOld` and a
# fresh one starts. Lookups try both. So the table never holds more than two generations,
# memory is capped at roughly twice the limit, and what ages out is what has gone longest
# without being written -- rather than everything at once, which is what clearing did.
table = {}
tableOld = {}

# How many positions one generation holds before it is retired, past which keeping more stops
# paying for itself at the depths anyone actually plays at.
#
# What that costs depends on which engine is answering, and the gap is wide: measured at this
# limit, a compiled worker peaks around 79 MB, where this pure-Python table ran to roughly 175
# MB per generation -- and tableOld means two are live at once. A server sets it lower for that
# reason; see the measured table in web/src/royals_web/ai_pool.py, which is where the number
# that matters is written down.
TABLE_LIMIT = 300000

# Whether any of it survives from one move to the next. Turning this off restores what the
# search did before -- everything thrown away at the start of every move -- which is what
# the gain from keeping it is measured against.
PERSIST = True

# Whether a node tries the table's move before generating any others. See the comment in
# minimax. Off restores generating everything up front, which is the only way to get the
# node counts this search produced before staging existed -- so it is what the change is
# measured against.
STAGED = True

# What a stored score is worth. A search that cut off never finished looking, so its score
# is only a bound -- treating one as exact is the usual way this goes wrong.
EXACT = 0       # the search completed inside its window: this is the real value
LOWER = 1       # it cut off high, so the true value is at least this
UPPER = 2       # nothing beat alpha, so the true value is at most this


# Called when a move caused a cutoff, so it gets looked at sooner next time.
def rememberCutoff(move, depthTrack):
    slot = killers.setdefault(depthTrack, [])
    if move not in slot:
        slot.insert(0, move)
        del slot[2:]

    # deep cutoffs pruned more, so they count for more
    history[move] = history.get(move, 0) + depthTrack * depthTrack


# Sorts moves so the promising ones go first: the move the last, shallower search liked,
# then captures biggest-first, then whatever has caused cutoffs before.
def orderMoves(moves, spaces, contr, depthTrack, pvMove):
    # hoisted: both are read once per move, and this is the busiest loop in the search after
    # move generation itself
    slot = killers.get(depthTrack, [])
    seen = history.get
    scored = []

    for move in moves:
        if move == pvMove:
            scored.append((1000000, move))
            continue

        kind = move[MOVE_KIND]
        score = 0

        if kind != "break":
            targ = spaces[move[MOVE_TARGET]]

            if kind == "jump" and targ[Hasher.OCCUPIED] and targ[Hasher.SIDE] != contr:
                # a jump onto the enemy takes everything standing there
                score += 1000 + targ[Hasher.CAPTORS] * 100
            elif kind == "push" and targ[Hasher.PRISFLAG] and targ[Hasher.SIDE] != contr:
                # a push into a jailer frees our own people
                score += 800 + targ[Hasher.PRISCOUNT] * 100

        if move in slot: score += 500 - slot.index(move) * 10

        score += seen(move, 0)

        scored.append((score, move))

    # a stable sort, so equal-scoring moves keep board order and the search stays repeatable.
    # Sorting on the pair's first element only -- a move is a tuple now, so sorting the pairs
    # whole would fall through to comparing moves and quietly reorder the ties.
    scored.sort(key = FIRST, reverse = True)
    return [pair[1] for pair in scored]


####### Searching #######

# Alpha-beta search. Returns [score, move] with move as [origin, ['jump'|'push'|'break',
# target], movingPris], or None where there was nothing to choose.
# Scores always read from rootContr's side of the table, so the search maximises on that
# side's turn and minimises on the other's.
#   pvMove -- what a previous, shallower pass thought was best here. Tried first.
#   atRoot -- this is the move actually about to be played, so the ko rule applies to it.
#             Deeper nodes are hypothetical: they carry a history of their own that the
#             game's record knows nothing about, which is why the backup only tested at the
#             root either.
#   gatherExt -- how many unresolved-gather extensions this path may still spend. See the
#             leaf check below. One is all the rule needs; the count exists so a pathological
#             position cannot walk the extension down forever.
def minimax(cBoard, contr, rootContr, alpha, beta, depthTrack, pvMove = None, atRoot = False,
            gatherExt = 1):
    global calcCount
    calcCount += 1

    # A gather is not a win until it has survived a reply. The rule is stated as a property of
    # the position rather than as an extra turn -- **you have won if, at the start of your own
    # turn, your spy, four pawns and royal are still on one square** -- which is exactly the
    # same thing and needs no flag and no counter to carry between plies.
    #
    # So this node is terminal only when the side to move is the side that has gathered: they
    # got there, the opponent had their answer, and it did not come. A gather by the side that
    # just moved is not terminal at all, because `contr` is precisely the player who still has
    # a reply to find -- so the search plays on and finds it, which is the whole point of the
    # rule. In practice the reply that exists is a lone spy's push, which shatters what it
    # hits; nothing else can touch a full stack, since a break needs the enemy's own spy in it
    # and landing on a weight-six square needs six of your own.
    #
    # Check_For_Winner is untouched and stays a pure detector of "there is a completed stack".
    # That is deliberate: regress.py's move sweep calls it and breaks on it, so leaving the
    # detector alone is what keeps golden_moves.txt byte-identical through a rules change.
    winner = Hasher.Check_For_Winner(cBoard)[1]
    if winner[contr]:
        # scaling by the depth left makes a win now beat the same win three moves out
        if contr == rootContr: return [WIN_SCORE * (depthTrack + 1), None]
        return [-WIN_SCORE * (depthTrack + 1), None]

    # An unresolved gather is not a position to stand and evaluate. The terminal test above
    # deliberately does not fire for a stack the side to move has just been handed -- `contr`
    # is the player who still owes a reply -- but a leaf has no ply left to find it in, and
    # `evaluateSides` answers WIN_SCORE flat for six on a square. So the horizon undoes the
    # rule: at depth N the search sees a gather made on the last ply, calls it won, and never
    # looks at the lone spy standing next to it whose push scatters the lot.
    #
    # One more ply is exactly what the rule asks for and no more, because the rule is "it has
    # to survive one reply". Extending to 1 makes this leaf behave as an ordinary depth-1
    # node, which is not a coincidence and is what lets the result go in the table as one:
    # a real depth-1 search of this position does the same thing, since its children are
    # depth-0 nodes where `contr` has flipped and the terminal test above fires.
    #
    # Costs nothing in the ordinary case -- `winner` is already in hand, and positions with a
    # completed stack in them are a vanishing fraction of leaves.
    if depthTrack == 0:
        if not (gatherExt and winner[1 - contr]):
            return [fullCheck(cBoard, rootContr), None]
        depthTrack = 1
        gatherExt -= 1

    # Only interior nodes consult the table, and only below the two checks above. Probing
    # first would let a leaf answer with a stored one-ply search instead of the standing
    # evaluation -- searching deeper than it was asked to, which makes a depth-N answer
    # stop meaning depth N. It also skips building the key at leaves, which are most nodes.
    #
    # The root has moves struck off it that the same position reached deeper still has, so
    # its answer is filed apart from theirs rather than standing in for them. It is also the
    # only answer that goes stale: those strikings-off came from the ko history, which grows
    # by one every time a move is actually played. Stamping root entries with the generation
    # means a later search simply doesn't find the older one. Everything below the root never
    # looked at the ko history to begin with, so it is filed under a stamp of -1 and stays
    # good for the rest of the game -- which is what makes keeping the table worth anything.
    if atRoot: tableKey = (cBoard, contr, Engine.koGeneration)
    else: tableKey = (cBoard, contr, -1)

    entry = table.get(tableKey)
    if entry is None: entry = tableOld.get(tableKey)

    # Which way this node is being read. alpha and beta are kept in rootContr's terms all
    # the way down, so a node is a max node exactly when the side to move is the side the
    # search is being run for.
    maximizing = (contr == rootContr)

    if entry != None and entry[0] >= depthTrack:
        # searched before, at least as deep as we need now.
        #
        # Entries are filed from the point of view of the side to move in them, not of the
        # side the search is being run for. Those were the same thing while the table lasted
        # a single move -- rootContr never changed inside one -- but it now outlives the move
        # and the two sides take turns being rootContr. Filed the old way, blue's search
        # would read back red's score for the same position and take it at face value, with
        # the sign the wrong way round. Turning it here costs a negation and makes an entry
        # worth having whichever side goes on to look for it.
        score = entry[1] if maximizing else -entry[1]
        flag = entry[2]
        # a floor on a score becomes a ceiling once the score is negated
        if not maximizing:
            if flag == LOWER: flag = UPPER
            elif flag == UPPER: flag = LOWER

        if flag == EXACT: return [score, entry[3]]
        elif flag == LOWER: alpha = max(alpha, score)
        else: beta = min(beta, score)

        # the bound alone already settles it
        if alpha >= beta: return [score, entry[3]]

    # The window the search below actually runs with, after any tightening the table did.
    # What the answer is worth has to be judged against this, not against the window the
    # caller asked for -- a value landing between the two is a bound, not a real score.
    searchAlpha = alpha
    searchBeta = beta

    # whatever won here last time is the best guess at what wins here now
    if pvMove == None and entry != None: pvMove = entry[3]

    # Most interior nodes cut off -- 85% of them, measured -- and 78% of those cut off on
    # the very first move tried. Where there is a stored move, that first move IS the stored
    # move, because orderMoves puts it in front of everything else. So generating the other
    # forty, scoring them and sorting them is work done to be thrown away.
    #
    # This tries it before generating anything. What follows is the same list in the same
    # order with that one move lifted out of it, so the search sees exactly the sequence it
    # saw before: same nodes, same cutoffs, same answer, minus the generation at every node
    # the stored move settles. Parsing the board goes with it, since only ordering needs it.
    #
    # Safe because the transposition table is keyed on the board itself -- a tuple of 49
    # codes -- and not on a hash of it. There are no collisions to guard against, so a stored
    # move was generated for exactly this position and is legal here by construction. An
    # engine keying on a Zobrist hash would have to verify the move before playing it.
    def candidates():
        if STAGED and pvMove != None:
            yield pvMove

            spaces = Hasher.Parse_Board(cBoard)
            for move in orderMoves(listAllMoves(cBoard, contr, spaces), spaces, contr, depthTrack, pvMove):
                # orderMoves scores pvMove above everything, so this drops the first entry
                # and nothing else
                if move != pvMove: yield move
            return

        spaces = Hasher.Parse_Board(cBoard)
        for move in orderMoves(listAllMoves(cBoard, contr, spaces), spaces, contr, depthTrack, pvMove):
            yield move

    bestScore = None
    bestMove = None

    for move in candidates():
        child = performOneStep(cBoard, contr, move)

        # a move that returns the game somewhere it has already been isn't one to offer
        if atRoot and Engine.koBreaks(child): continue

        # gatherExt rides down the path, not across the tree: a sibling branch gets whatever
        # this node was handed, and only the branch that actually spent an extension is short.
        score = minimax(child, int(not contr), rootContr, alpha, beta, depthTrack - 1,
                        None, False, gatherExt)[0]

        if bestScore is None or (score > bestScore if maximizing else score < bestScore):
            bestScore = score
            bestMove = move

        if maximizing: alpha = max(alpha, score)
        else: beta = min(beta, score)

        # the other side would never let us down this branch, so stop reading it
        if beta <= alpha:
            rememberCutoff(move, depthTrack)
            break

    # nothing legal anywhere, so the position stands as it is
    if bestMove is None: return [fullCheck(cBoard, rootContr), None]

    # File the answer away, recording how much of it we actually established. Scores that
    # come back from a win are left out on purpose: they are scaled by the depth remaining,
    # so the same win filed at one depth reads as a different score at another.
    if abs(bestScore) < WIN_SCORE:
        # fell short of the window, so all we learned is a ceiling; ran past it, so all we
        # learned is a floor; landed inside it and the search really did settle the matter.
        if bestScore <= searchAlpha: flag = UPPER
        elif bestScore >= searchBeta: flag = LOWER
        else: flag = EXACT

        # filed from the side to move's point of view, the same turn the probe undoes
        keepScore = bestScore
        if not maximizing:
            keepScore = -bestScore
            if flag == LOWER: flag = UPPER
            elif flag == UPPER: flag = LOWER

        if entry == None or depthTrack >= entry[0]:
            # writes always land in the live generation, so an entry found in the old one
            # and still worth having is carried forward rather than aged out again
            table[tableKey] = [depthTrack, keepScore, flag, bestMove]

    return [bestScore, bestMove]


# Picks a side's move. Returns [score, move].
# Searches depth 1, then 2, and so on up to depth, handing each pass's answer to the next to
# try first. The shallow passes look like waste, but the tree grows several times over per
# level, so they cost little and the ordering they hand up saves far more than they spend.
# It also means an interrupted search still has a usable move to fall back on.
# The bounds are wider than any real score rather than the backup's +/-2048, which a win
# could exceed once the depth scaling pushed it past 2048 -- that clipped the pruning against
# real scores. INFINITY rather than math.inf so the search stays entirely in whole numbers.
def chooseMove(cBoard, contr, depth = 3):
    global calcCount, killers, history, table, tableOld
    checkSide(contr)

    if _accel.accel is not None:
        # The ko history is handed over on every call rather than mirrored on the other side.
        # Engine.koTrack stays the single authority, which is what lets ai_pool.py keep its
        # load-one-game / run / drop discipline with no changes at all.
        # TABLE_LIMIT goes across on every call rather than being mirrored once. It is a plain
        # module global that callers assign to -- ai_pool.py drops it to 50,000 because the
        # default costs more memory per worker than a small box has to spare -- and an
        # assignment cannot
        # trigger a setter, so a mirrored copy would go stale the first time anyone used the
        # knob as documented.
        score, move, nodes = _accel.accel.choose_move(
            cBoard, contr, depth, list(Engine.koTrack), TABLE_LIMIT)
        calcCount = nodes
        return [score, move]

    calcCount = 0

    if not PERSIST: newGame()

    # Killers are a move that cut off at a given depth, with no record of where -- worth
    # keeping across the deepening passes of one search, where the position really is the
    # same, and misleading across a move, where it isn't. So they start each move empty.
    killers = {}

    # History is keyed by the move itself and is meant to say which moves tend to be worth
    # trying anywhere, so it is worth carrying between moves. Halving it each time keeps
    # what recent searches learned worth more than what old ones did, and stops the counts
    # growing until they swamp the capture and killer bonuses they are meant to break ties
    # between. Entries that have decayed to nothing are dropped rather than kept at zero.
    if history:
        history = {key: half for key, half in
                   ((key, count // 2) for key, count in history.items()) if half}

    # Retire a generation when the live one fills. Everything the retired one holds is still
    # reachable until the next changeover, and anything still being asked for gets written
    # forward into the live table as it is found.
    if len(table) > TABLE_LIMIT:
        tableOld = table
        table = {}

    best = [0, None]
    for step in range(1, depth + 1):
        best = minimax(cBoard, contr, contr, -INFINITY, INFINITY, step, best[1], True)

    return best


# Forgets everything learned about the game just played. The table stays true across moves
# but a new game is a different game, and leaving a full one standing would only spend memory
# on positions the new one is unlikely to reach.
#
# **Call it when a different game is starting -- not simply wherever Engine.koReset is.** The
# two do travel together at the start of a game, which is what that shorter rule was reaching
# for, but they answer different questions and one caller has them apart on purpose. A driver
# taking a move back rebuilds the ko history to a prefix of the *same* game, so it calls
# koReset and must not call this: the table's contents are precisely the positions the
# resumed game is about to walk back through, and dropping them buys nothing and costs the
# search everything it already knew. It is safe to keep for the reason the stamps exist --
# root entries are keyed on Engine.koGeneration, which koReset and every koRecord bump, so a
# rebuilt history cannot match one filed against the line that was abandoned, and everything
# below the root never read the ko history to begin with. See royals_gui.resumeAt.
def newGame():
    global killers, history, table, tableOld

    # Both sides, always -- not either/or. ai_pool.py calls this between jobs so that game B's
    # positions never land in game A's tables, and a worker that cleared only the Python half
    # would carry the compiled transposition table straight into the next game. Not a crash:
    # a search answering confidently about a tree belonging to a different game.
    if _accel.accel is not None:
        _accel.accel.new_game()

    killers = {}
    history = {}
    table = {}
    tableOld = {}


# Picks a move and plays it. Returns [board, move, score] -- board unchanged if there was
# no legal move.
def takeTurn(cBoard, contr, depth = 3):
    global calcCount
    checkSide(contr)

    if _accel.accel is not None:
        # Shimmed separately from chooseMove even though the Python body below would already
        # get there through it: doing both halves on the other side is one crossing instead of
        # two, and this is the call ai_pool.py actually makes.
        board, move, score, nodes = _accel.accel.take_turn(
            cBoard, contr, depth, list(Engine.koTrack), TABLE_LIMIT)
        calcCount = nodes
        return [board, move, score]

    score, move = chooseMove(cBoard, contr, depth)
    if move is None: return [cBoard, None, score]

    return [performOneStep(cBoard, contr, move), move, score]


##############################
######## Entering ############
##############################
# Entering is a different problem from the game that follows it, and checkPosition is the
# wrong tool for it. Nothing is stacked yet, so its myPieces*myPieces term is 1 for every
# piece and its groups term is -1.5 for every piece: both are the same whichever square is
# picked. That leaves the square-colour term deciding the whole thing, which put blue's
# royal on b1 out of an eighteen-way tie and strung the pawns down the a and b files.
#
# What actually matters is that the win is six pieces on one square, so a placement is good
# exactly insofar as it lets the six converge. Three facts about the board decide that, and
# the tables below get all three from the engine rather than restating them:
#   - jumps run along diagonals for as many squares as the stack weighs, so they never
#     change a square's colour. Pieces entered on opposite colours can never meet by
#     jumping at all -- one of them has to push first, which needs an enemy to shove.
#   - the outer ring of the board has two jumps out of it where the inside has four.
#   - a stack holding a spy can't jump onto anything and nothing can jump onto a royal, so
#     the spy is the only piece the others can gather on to, and the royal arrives last.

# turns to gather counts for most of the decision, but a formation that gathers a move
# slower on better squares is worth more than one that gathers fast against the rim.
GATHER_WEIGHT = 1.0
MOBILITY_WEIGHT = 0.35
CONTEST_WEIGHT = 0.6


####### Entering noise #######
# Left to itself the heuristic opens the same way every game -- the same twelve squares in
# the same order, with both royals coming out of a fifteen-way tie that nothing settles but
# square number. This tilts the scores with a Perlin field so the opening varies without
# coming apart. See Perlin.py for why the noise has to be spatially coherent.

# How far the field can shift one placement's score at intensity 1.
#
# Half a point was the first guess -- enough to reshuffle close decisions, measured across a
# full entering sequence as the best square beating the second best by 0.0 to 1.1. It turned
# out to make the intensity setting almost meaningless. Most of those decisions are exact
# ties (both royals come out of a fifteen-way one), so any noise at all settles them, and
# settling the royal cascades through everything placed after it. Intensity 0.1 and intensity
# 1.0 moved the gathering point the same 1.2 squares from where it sits with no noise: on at
# all was doing the work, and the slider had nowhere to travel.
#
# At 1.5 the field can also outweigh a real preference, not just a tie, so the setting spans
# something. Measured over eight seeds, mean distance the gathering point moves:
#   weight 0.5 -- 1.27 squares at intensity 0.25, 1.38 at 1.0. Barely a range.
#   weight 1.5 -- 1.25 at 0.25, 1.89 at 1.0.
#   weight 3.0 -- 1.33 at 0.25, 2.03 at 1.0. Little more travel for a lot more weight.
# Cost of the varied openings, in turns to gather: none any of these are able to measure.
# Every setting lands between 9.1 and 9.6 against the fixed opening's 10.0, because what the
# noise mostly displaces is a tiebreak that was settling on the lowest square number.
ENTRY_NOISE_WEIGHT = 1.5

# Intensity, 0 to 1. At 0 the entering is bit-for-bit what it was before any of this, so
# the old fixed opening is still what you get by default. Set it through setEntryNoise.
ENTRY_NOISE = 0.0

# One field per side, indexed the way contr is: [blue, red]. Flat until setEntryNoise.
ENTRY_FIELDS = [Perlin.flatField(), Perlin.flatField()]

# What the current fields were built from, so a game can be played again.
ENTRY_SEED = None


# Sets how varied the computer's entering is and draws the fields it varies along.
#   intensity -- 0 for the old deterministic opening, 1 for as far as this goes. This is
#                the value a slider would drive.
#   seed      -- omit for a fresh opening, or pass one back to replay an old one.
# Returns the seed used.
def setEntryNoise(intensity, seed = None):
    global ENTRY_NOISE, ENTRY_FIELDS, ENTRY_SEED

    ENTRY_NOISE = max(0.0, min(1.0, float(intensity)))

    if seed is None: seed = random.randrange(1 << 30)
    ENTRY_SEED = seed

    # A field each, or both sides would drift toward the same hill and the two formations
    # would come out as mirror images of each other.
    ENTRY_FIELDS = [Perlin.field(seed), Perlin.field(seed + 1)]

    return seed


# Answers enteringAnchor has already worked out, keyed by the squares it was asked about.
#
# The walk below is 49 candidates against every piece, and enterSearch asks for it far more
# often than it asks anything new: it is a minimax over placements, and every order of
# reaching the same placements arrives at the same set of squares. Measured over three
# games, 107,750 calls between them held 3,368 distinct sets -- 97% of that work was a
# repeat of work already done.
#
# Cached across games rather than per game, which is worth being clear about because the
# tables beside this one are not. An anchor depends on JUMPDIST and JUMPREACH and on
# nothing else -- not on the board, not on whose turn it is, not on the noise fields, which
# is why the first parameter below goes unread. Those tables are built at import and never
# move, so an answer stays true for the life of the process and newGame has no business
# dropping it.
#
# What that leaves is memory, so it is bounded. A game contributes on the order of 1,100
# sets, so this holds roughly eighteen games' worth and then simply stops growing: past the
# limit the walk still runs and still answers, it just isn't written down. Unbounded, a
# server playing back to back would accumulate squares nobody will ask about again.
ANCHOR_MEMO = {}
ANCHOR_MEMO_LIMIT = 20_000


# The square a side would gather on and what it costs to get everyone there, in lone-piece
# jumps. Squares rather than pieces: a stack travels together, so it is one journey however
# much is standing on it. Returns [square, cost], or [0, 0] with nothing on the board.
def enteringAnchor(spaces, mine):
    if not mine: return [0, 0]

    # entryScore builds `mine` by walking the board in order, so this is already canonical.
    # Nothing breaks if a caller ever hands them over in some other order -- the sum below
    # doesn't care, so the worst an unsorted list costs is a second entry saying the same
    # thing.
    key = tuple(mine)
    hit = ANCHOR_MEMO.get(key)
    # rebuilt rather than handed back, because the caller unpacks it and a shared list is
    # one caller's mistake away from being everybody's
    if hit is not None: return [hit[0], hit[1]]

    anchor = 0
    gather = None
    for candidate in range(1, 50):
        total = 0
        for square in mine: total += JUMPDIST[square][candidate]

        # ties go to the square with more ways out of it
        if gather is None or total < gather or (total == gather and JUMPREACH[candidate] > JUMPREACH[anchor]):
            gather = total
            anchor = candidate

    if len(ANCHOR_MEMO) < ANCHOR_MEMO_LIMIT: ANCHOR_MEMO[key] = (anchor, gather)

    return [anchor, gather]


# What a side's shape is worth during entering. The dragon is left out throughout: it can't
# stack, can't be captured and can't win, so it is no part of the gather.
def entryScore(spaces, contr):
    mine = []
    theirs = []
    for square in range(1, 50):
        s = spaces[square - 1]
        if s[Hasher.DRAGON] or not s[Hasher.OCCUPIED]: continue
        if s[Hasher.SIDE] == contr: mine.append(square)
        else: theirs.append(square)

    anchor, gather = enteringAnchor(spaces, mine)
    if anchor == 0: return 0.0

    # a piece on the wrong colour scores UNREACHABLE against every anchor there is, so
    # splitting the colours costs more here than any amount of distance can
    score = -GATHER_WEIGHT * gather

    # The noise goes in here, inside what enterSearch evaluates, rather than over the square
    # chooseEntry finally returns. That way the search plans with the tilt and assembles a
    # formation that holds together around it -- noise laid over the root would be a
    # tiebreak that every placement after it then worked to undo.
    #
    # Keyed by the side being scored, so entryDiff stays symmetric and each side keeps its
    # own taste in shapes. That includes whichever side the search is treating as the
    # opponent, which is the point: it should expect them to form up somewhere of their own.
    field = ENTRY_FIELDS[contr]
    for square in mine:
        score += MOBILITY_WEIGHT * JUMPREACH[square] + ENTRY_NOISE_WEIGHT * ENTRY_NOISE * field[square]

    # forming up under the enemy's nose invites the stack being taken while it is still
    # small. Plain adjacency rather than jump distance, since a neighbour on the other
    # colour can still push into you.
    crowd = Engine.enteringNeighbours(anchor)
    for square in theirs:
        if square in crowd: score -= CONTEST_WEIGHT

    return score


# Both sides read off one walk of the board. The difference is what the search needs: the
# opponent places between your placements, so their shape is not a constant.
def entryDiff(spaces, contr):
    return entryScore(spaces, contr) - entryScore(spaces, int(not contr))


####### Searching the entering order #######

# how many squares get considered per placement, and how many placements deep to look.
# Width matters more than depth here: the shortlist is what keeps the branching factor off
# forty-odd legal squares, and the heuristic is good enough that the cut is safe.
ENTRY_WIDTH = 6
ENTRY_DEPTH = 4


# The entering steps still to come. Read off the board rather than counted by the caller,
# so this works wherever it is called from -- enteringSequence lists each side and piece
# exactly as many times as it gets entered, so striking off what is already down leaves
# what is left, in order.
def enteringRemaining(spaces):
    down = {}
    for square in range(1, 50):
        s = spaces[square - 1]
        if s[Hasher.DRAGON] or not s[Hasher.OCCUPIED]: continue

        side = s[Hasher.SIDE]
        for piece in (Hasher.SPY, Hasher.PAWNS, Hasher.ROYAL):
            if s[piece]: down[(side, piece)] = down.get((side, piece), 0) + s[piece]

    steps = []
    for step in Engine.enteringSequence():
        key = (step[0], step[1])
        if down.get(key, 0):
            down[key] -= 1
            continue
        steps.append(step)

    return steps


# The squares worth looking at for one placement, best first, with the board each one
# leads to. Returns [score, square, board, spaces] rows.
def entryShortlist(cBoard, spaces, contr, piece, isSpy, width):
    # `contr` owns the piece; under reversed entering the opponent is the one choosing, and
    # every row is scored and ordered from their point of view. entryDiff(s, a) is exactly
    # -entryDiff(s, 1 - a), so scoring the chooser and keeping the descending sort spells
    # "worst for the owner first" without a second sort order to keep straight.
    #
    # The candidate squares still come from enteringOptions asked about the OWNER: what is
    # legal has not changed, only who is picking from it.
    chooser = Engine.enteringChooser(contr)

    scored = []
    for square in Engine.enteringOptions(cBoard, contr, isSpy, spaces):
        after = Engine.dropPiece(cBoard, square, contr, piece)
        afterSpaces = Hasher.Parse_Board(after)
        scored.append([entryDiff(afterSpaces, chooser), square, after, afterSpaces])

    # Reach flips with the rest of it. A side placing its own pieces wanted the square with
    # the most jumps out of it; a side placing its opponent's wants the one with the fewest,
    # so the tie-break sorts ascending now.
    #
    # square number last and only to settle what is otherwise a coin toss -- sorting on it
    # any earlier is what made the old version march down the a file
    scored.sort(key=lambda row: (-row[0], JUMPREACH[row[1]], row[1]))
    return scored[:width]


# Alpha-beta over the rest of the entering order. Same shape as minimax, with a placement
# where it has a move and entryDiff where it has checkPosition.
def enterSearch(cBoard, spaces, steps, index, rootContr, alpha, beta, depth):
    if depth == 0 or index >= len(steps): return entryDiff(spaces, rootContr)

    contr = steps[index][0]
    piece = steps[index][1]

    shortlist = entryShortlist(cBoard, spaces, contr, piece, (piece == Hasher.SPY), ENTRY_WIDTH)
    # hemmed in everywhere, so this side sits the step out and the order carries on
    if not shortlist:
        return enterSearch(cBoard, spaces, steps, index + 1, rootContr, alpha, beta, depth)

    # `contr` owns the piece being placed at this step, but under reversed entering the
    # decision is the other side's -- so a node is a max node when the CHOOSER is the side
    # this search is being run for, not when the owner is. Getting this wrong inverts the
    # whole search quietly: it still returns a legal square, just the one the opponent would
    # most like to have.
    maximizing = (Engine.enteringChooser(contr) == rootContr)
    best = None

    for row in shortlist:
        score = enterSearch(row[2], row[3], steps, index + 1, rootContr, alpha, beta, depth - 1)

        if best is None or (score > best if maximizing else score < best): best = score

        if maximizing: alpha = max(alpha, score)
        else: beta = min(beta, score)

        if beta <= alpha: break

    return best


# Picks where to enter a piece. Returns None if nowhere is legal.
#
# **`contr` is the side the piece belongs to, not the side deciding.** Under reversed entering
# those are opposites, and keeping the argument as the owner is what lets every caller stay
# exactly as it was: enteringSequence still hands out [owner, piece] steps, and the record
# still derives a ply's side from its index. The flip happens here, once.
def chooseEntry(cBoard, contr, piece, isSpy, depth = ENTRY_DEPTH):
    spaces = Hasher.Parse_Board(cBoard)
    chooser = Engine.enteringChooser(contr)

    shortlist = entryShortlist(cBoard, spaces, contr, piece, isSpy, ENTRY_WIDTH)
    if not shortlist: return None

    # where in the order this placement falls, so the search knows what follows it. If the
    # board doesn't agree with the sequence -- a step skipped for want of anywhere legal --
    # there is nothing to look down and the shortlist's own ranking stands.
    steps = enteringRemaining(spaces)
    start = 0
    while start < len(steps) and steps[start] != [contr, piece]: start += 1
    if start >= len(steps): return shortlist[0][1]

    bestSquare = None
    bestScore = None
    alpha = -math.inf

    for row in shortlist:
        # rooted at the chooser, so the score being maximised is the harm done to the owner
        score = enterSearch(row[2], row[3], steps, start + 1, chooser, alpha, math.inf, depth - 1)

        if bestScore is None or score > bestScore:
            bestScore = score
            bestSquare = row[1]

        alpha = max(alpha, score)

    return bestSquare


####### How loosely it picks #######
# The entering setting, end to end. `chooseEntry` above always plays the best square it can
# find; this is the same choice made more or less strictly, and it is what every front end's
# variety slider now drives.
#
# **Both ends are exact rather than approached.** At 0 this is `chooseEntry` itself, so the
# opening is the fixed one, the same twelve squares every game. At 1 it is a uniform draw over
# every legal square, which is genuinely random and not merely very varied. The old setting
# could not reach that end from either direction: it tilted the scores with a Perlin field, and
# a field has a strongest square, so turning the tilt up past the heuristic makes the opening
# *more* predictable rather than less -- it converges on the field's own peak.
#
# In between, rank r is drawn with weight `intensity ** r`. That one expression is the whole
# scheme and it needs no tuning constant: 0 ** 0 is 1 with every later weight zero, so 0 is the
# top square; every weight is 1 at intensity 1, so that is uniform; and in between it is a
# geometric decay whose ratio is the slider. The two short-circuits below are therefore
# shortcuts and not special cases -- they are what the weights already say, minus the work.
#
# The ranking is `chooseEntry`'s own answer first, then every other legal square in shortlist
# order. Deliberately not the order of the root search's scores: the loop above raises `alpha`
# across candidates, so a losing candidate's score is a bound on how bad it is rather than a
# value, and ranking by it would be ranking by how early each was cut off. The tail is ordered
# by `entryDiff`, which the shortlist has already computed for every legal square anyway.
#
# The draw is a cumulative walk over one `rng.random()`. A softmax over the scores would read
# better and would put an `exp()` on the path CPython and PyPy have already once disagreed on
# in the last place -- see the note on entryScore's floats in docs/ARCHITECTURE.md. Plain
# addition in a fixed order does not have that problem.
def enterVaried(cBoard, contr, piece, isSpy, intensity, rng, depth = ENTRY_DEPTH):
    intensity = max(0.0, min(1.0, float(intensity)))

    if intensity <= 0.0: return chooseEntry(cBoard, contr, piece, isSpy, depth)
    if intensity >= 1.0: return Engine.randomEntry(cBoard, contr, isSpy, rng)

    spaces = Hasher.Parse_Board(cBoard)
    ranked = entryShortlist(cBoard, spaces, contr, piece, isSpy, 49)
    if not ranked: return None

    best = chooseEntry(cBoard, contr, piece, isSpy, depth)
    squares = [row[1] for row in ranked if row[1] != best]
    if best is not None: squares.insert(0, best)

    weights = []
    total = 0.0
    weight = 1.0
    for _ in squares:
        weights.append(weight)
        total += weight
        weight *= intensity

    cut = rng.random() * total
    running = 0.0
    for square, weight in zip(squares, weights):
        running += weight
        if cut < running: return square

    # Only reachable if the accumulated total drifts in the last place. The best square is
    # what the game would have played anyway, so that is the right thing to fall back on.
    return squares[0]


# Spells a move out for the move log.
def describeMove(move):
    if move is None: return "no legal move"

    kind = move[MOVE_KIND]
    target = move[MOVE_TARGET]

    text = Hasher.IndexToAlg(move[MOVE_ORIGIN] - 1).upper()
    if kind == "break":
        text += " breaks " + HEADINGS[target]
    elif kind == "free":
        # "frees to E4" doesn't read, and the square is where the freed pieces end up
        # standing rather than somewhere they are sent
        text += " frees " + Hasher.IndexToAlg(target).upper()
    else:
        # A push's target names the square it shoves, which is where the stack lands only
        # when it travels one -- so under the push-range variant the log has to work out
        # where the stack actually came to rest, or a three-square push reads as a
        # one-square push into a square the stack is not standing on.
        landing = target
        far = move[MOVE_TRAVEL] if len(move) > MOVE_TRAVEL else 1
        if kind == "push" and far > 1:
            origin = move[MOVE_ORIGIN]
            landing = Engine.pushSquare(origin, Engine.findDirection(origin, target + 1),
                                        far) - 1

        text += " " + kind + "s to " + Hasher.IndexToAlg(landing).upper()
        if kind == "push" and far > 1:
            text += " (%d squares)" % (far,)

    if move[MOVE_PRIS]: text += " (with prisoners)"
    return text
