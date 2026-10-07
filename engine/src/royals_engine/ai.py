import math
import operator
import random

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import perlin as Perlin

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
def listMoves(moveArray, origin, movingPris, into):
    if not moveArray: return

    for target in moveArray[0]: into.append((origin, "jump", target, movingPris))
    for target in moveArray[1]: into.append((origin, "push", target, movingPris))
    # a break scatters the whole square, so there is no carrying-prisoners variant of one
    if not movingPris:
        for heading in moveArray[2]: into.append((origin, "break", heading, False))
    # A push into allies being held is two moves onto one square -- shove the whole thing
    # along, or free them and stand where they stood. Both get listed, and a square can
    # appear in possPushes and possFrees at once, which is the point of keeping them apart.
    for target in moveArray[5]: into.append((origin, "free", target, movingPris))


# Applies one move and returns the resulting board, leaving cBoard alone.
def performOneStep(cBoard, contr, move):
    kind = move[MOVE_KIND]
    if kind == "jump":
        return Engine.exeMove(cBoard, move[MOVE_ORIGIN], move[MOVE_TARGET] + 1, contr, move[MOVE_PRIS])
    if kind == "push":
        return Engine.exePush(cBoard, move[MOVE_ORIGIN], move[MOVE_TARGET] + 1, contr, move[MOVE_PRIS])
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
    if spaces is None: spaces = Hasher.Parse_Board(cBoard)

    everything = []
    for origin in makePossList(cBoard, contr, spaces):
        s = spaces[origin - 1]

        # our spy on their square: the origin has to carry the spyBreak flag, and breaking
        # out is the only thing checkMoves will offer back
        if s[Hasher.SIDE] != contr:
            tOrigin = makeOrigin(cBoard, origin, False, True)
            listMoves(Engine.checkMoves(cBoard, tOrigin, contr, spaces), origin, False, everything)
            continue

        carrying = [False]
        if s[Hasher.PRISCOUNT]: carrying.append(True)

        for movingPris in carrying:
            tOrigin = makeOrigin(cBoard, origin, movingPris)
            # the board is already parsed, so checkMoves needn't walk it again
            listMoves(Engine.checkMoves(cBoard, tOrigin, contr, spaces), origin, movingPris,
                      everything)
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

####### The weights #######
# Every number the evaluator multiplies by, in SCALE units, as a module global. They are
# globals rather than literals so that the tuning harness (tuning/) can swap a whole set in
# and out between one side's search and the other's -- see setWeights -- and they are read
# into locals once at the top of evaluateSides, so the cost of that is one LOAD_GLOBAL per
# weight per call against a loop over 49 squares.
#
# The defaults were tuned by self-play in October 2026 -- see tuning/RESULTS.md for every
# game count and interval behind the numbers below. Until then they were the hand-tuned
# values the evaluator had always used (DIAG 250, GROUP 1500, PRISONER 800, stacks n*n*1000,
# CAPTIVE 200, SPREAD 1500, ROYAL_SPY 5000), which tuning/pool/original.json keeps and every
# gauntlet still plays against. Measured at scale on GitHub Actions, the tuned set beats it
# at every depth from 3 to 7, every interval clear of zero (self-play Elo, which runs hot):
#   depth 3  +41 [+28, +54]  1000 pairs      depth 6  +18 [+2, +35]    500 pairs
#   depth 4  +20 [+15, +25]  5000 pairs      depth 7  +44 [+20, +67]   200 pairs
#   depth 5  +12 [+4, +21]   2000 pairs
# Read that as "a real gain of ten to forty Elo at any depth", not as a trend: the tie-
# breaker story said the gain should fade as the search deepens, and depth 7 says it does
# not. It costs about 10% of node rate, because the two gather terms turn the post-pass on
# at every leaf -- wall time, not strength, at a fixed depth.
# The whole gain comes from two places, found by ablation:
#   - the two tiny gather terms at the bottom of this block, worth about +25 together;
#   - the stack table bending away from the square law, worth about +15.
# Everything else moved less than its own probe size over 20,000 paired games, twice, so the
# hand tuning of those terms stands as it was.

# What a jump's worth of mobility is worth against the stacking score: JUMPREACH is 4 in the
# middle of the board and 2 on the rim, so at a quarter point a lone piece is worth up to half
# a point for where it stands and a stack of four up to two -- a nudge toward good ground,
# nowhere near enough to turn down a merge for it. Tuning left it exactly where it was;
# taking it away costs 20 Elo [6, 34], so a nudge is what it is.
DIAG_WEIGHT = 250

# Holding a prisoner is worth about 0.8 apiece. The two kinds are weighted apart because they
# are not alike -- a captive spy can break itself out and shatters its captor's stack when it
# does; captive pawns are inert until a push frees them -- though tuning found no daylight
# between them worth more than noise.
PRISONER_PAWN_WEIGHT = 830
PRISONER_SPY_WEIGHT = 800

# each separate group costs about 1.5
GROUP_PENALTY = 1480

# What a standing stack of n pieces is worth, for n from 1 to 5. This was n*n*SCALE, a
# square law, and the tuner bent it the same way in two independent runs: a 4-stack is worth
# a little less than the square law said and a 2 and a 5 a little more. On its own the bend
# is worth about 15 Elo at depth 3. Six is the win and is scored by WIN_SCORE, not from
# here -- see STACK_VALUE below.
STACK_1 = 1020
STACK_2 = 4120
STACK_3 = 8740
STACK_4 = 15310
STACK_5 = 25370

# Pieces of yours being held cost this percentage of what the same stack would be worth
# standing. 200 was the old "being held costs double": the group is counted as a stack for
# its owner and then charged twice over, so its net worth is minus one stack. At 100
# captivity would be neutral; below it, strangely, a comfort.
CAPTIVE_PCT = 205

# The spread penalty's multiplier, about 1.5 times the larger coordinate standard deviation.
# It used to be baked into spreadPenalty as a 9 under the root -- see there for why pulling
# it out as a weight changes nothing. This is the term that drives play: at 0 the evaluator
# loses 114 Elo [74, 156], six times what losing the mobility term costs.
SPREAD_WEIGHT = 1530

# Royal and spy on one square short of a win cost this times the square of the group count.
ROYAL_SPY_PENALTY = 5010

####### Candidate terms #######
# Things about a position the evaluator did not use to look at. Each is here because the
# rules single the thing out, and each went in at 0 -- not computed, golden.txt untouched --
# until the tournaments in tuning/RESULTS.md said what it was worth. Two earned a place, two
# did not and stay at 0 for anyone who wants to try them at another depth.
#
# The two that earned a place are TINY, and that is the finding. Under the old weights only
# 54% of a position's moves had distinct scores and the best move was an exact tie in 17.5%
# of positions, left to board order to settle. Five thousandths of a point per piece per jump
# towards the spy never outweighs a real term; it only says which of two otherwise-equal
# moves is the one that gathers, and that is worth about 23 Elo. The same term at 100 --
# where it starts outweighing real terms -- costs 42. Treat these as tie-breakers, not
# positional terms, and don't be tempted to make them "meaningful".

# The spy is the only square anyone can gather on: a spy can't jump onto anything and nothing
# can jump onto a royal, so the pawns come to the spy and the royal comes last. This charges
# each other group its lone-piece jump distance to the spy's square, per piece. JUMPDIST
# knows what the spread penalty can't: a jump never changes square colour, so a group on the
# other colour from the spy is UNREACHABLE (12) however near it stands, until a push moves it.
# +23 Elo [+11, +35] alone at 5; -42 [-67, -17] at 100.
SPY_DIST_WEIGHT = 5

# Per piece, for a standing group the enemy could land on next ply: an enemy stack with no
# spy in it, weighing at least as much as the group (prisoners and all, which is what a jump
# has to beat), within its own jump reach. Ignores what stands in the way, which makes it an
# over-estimate, but a consistent one. A leaf can't see the capture coming; this is a guess at
# what the search would have seen one ply further on. At depth 3 the search sees it anyway:
# -8 Elo [-21, +5] at 1000, and SPSA never lifted it off the floor. Off.
THREAT_PENALTY = 0

# Per pawn standing on the spy's square. Four pawns stacked anywhere else are a stack; four
# pawns on the spy are two moves from a win -- or so the argument went. -45 Elo [-71, -19]
# at 1000: paying for pawns already on the spy made the engine sit on them. Off.
SPY_STACK_WEIGHT = 0

# Per piece standing on the other colour from the spy. The same fact SPY_DIST_WEIGHT charges
# at a distance of 12, pulled out on its own so the two can be weighed apart. +20 Elo
# [+10, +31] alone at 31, and still +12 [+1, +23] at 500; with SPY_DIST_WEIGHT alongside the
# pair add little over either alone, so this is belt to the other's braces.
WRONG_COLOUR_PENALTY = 31

# The tunables, by name. This tuple is the single list: getWeights and setWeights work off it
# and the harness stores weight sets keyed by these names, so a new weight is added here and
# nowhere else needs telling.
WEIGHT_NAMES = (
    "DIAG_WEIGHT", "GROUP_PENALTY", "PRISONER_PAWN_WEIGHT", "PRISONER_SPY_WEIGHT",
    "STACK_1", "STACK_2", "STACK_3", "STACK_4", "STACK_5",
    "CAPTIVE_PCT", "SPREAD_WEIGHT", "ROYAL_SPY_PENALTY",
    "SPY_DIST_WEIGHT", "THREAT_PENALTY", "SPY_STACK_WEIGHT", "WRONG_COLOUR_PENALTY",
)

# Where each weight is allowed to go. The search reads any score at or beyond WIN_SCORE as
# a won game and refuses to file it in the table, so the evaluation of a real position has
# to stay well short of it: these ranges are roughly three times the defaults, which keeps
# any position that can actually arise a long way under WIN_SCORE // 2. Signs are fixed --
# a penalty may shrink to nothing but not turn into a reward, since the rules settle which
# way each of these points. setWeights refuses anything outside them, and the tuner clamps.
WEIGHT_RANGES = {
    "DIAG_WEIGHT": (0, 1000),
    "GROUP_PENALTY": (0, 5000),
    "PRISONER_PAWN_WEIGHT": (0, 3000),
    "PRISONER_SPY_WEIGHT": (0, 3000),
    "STACK_1": (0, 6000),
    "STACK_2": (0, 15000),
    "STACK_3": (0, 30000),
    "STACK_4": (0, 50000),
    "STACK_5": (0, 75000),
    "CAPTIVE_PCT": (0, 400),
    "SPREAD_WEIGHT": (0, 5000),
    "ROYAL_SPY_PENALTY": (0, 15000),
    "SPY_DIST_WEIGHT": (0, 1000),
    "THREAT_PENALTY": (0, 10000),
    "SPY_STACK_WEIGHT": (0, 10000),
    "WRONG_COLOUR_PENALTY": (0, 5000),
}

# Whether any of the candidate terms is switched on. They all need the groups gathered up
# during the board walk and looked at again afterwards; with every one of them at 0 that
# second look is skipped and the groups are not even collected, so the terms cost one
# truthiness test until something turns them on. Computed from the defaults at import by the
# _rebuildWeightTables call below JUMPHIT -- a literal here was wrong once the defaults
# turned two terms on: the search skipped them until something called setWeights, and only
# the tuning harness ever did.
ANY_POST = False

# Indexed by how many pieces stand on the square, 0 to 6. Zero at both ends: an empty square
# says nothing and six is the win, which evaluateSides scores as WIN_SCORE instead. Rebuilt
# by setWeights whenever a STACK_n changes, so the evaluator reads one tuple and never
# branches on the size.
STACK_VALUE = (0, STACK_1, STACK_2, STACK_3, STACK_4, STACK_5, 0)


def _rebuildWeightTables():
    global STACK_VALUE, ANY_POST
    STACK_VALUE = (0, STACK_1, STACK_2, STACK_3, STACK_4, STACK_5, 0)
    ANY_POST = bool(SPY_DIST_WEIGHT or THREAT_PENALTY or SPY_STACK_WEIGHT or WRONG_COLOUR_PENALTY)


# Every square a stack of a given weight could land on from a given square, on an empty
# board: JUMPHIT[weight][square] is a frozenset of 0-based targets, for weights 0 to 6.
# Drawn from JUMPRAY, so it wraps where a jump wraps and stops where one stops. What stands
# in the way is not consulted -- a royal, a dragon or a square holding the mover's own
# captives would end the strand early -- so this is the most a stack could reach, which is
# the right side to err on for a threat.
def buildJumpHit():
    table = []
    for weight in range(0, Engine.MAX_STACK + 1):
        row = [frozenset()]
        for square in range(1, 50):
            row.append(frozenset(target for strand in Engine.JUMPRAY[square]
                                 for target in strand[:weight]))
        table.append(tuple(row))
    return tuple(table)


JUMPHIT = buildJumpHit()

# STACK_VALUE and ANY_POST as the defaults imply, before anything can read them
_rebuildWeightTables()


# Which colour a 1-based square is, 0 or 1. Jumps are diagonal, so a stack never leaves its
# colour by jumping; two squares of different colour can only be joined by a push.
def squareColour(square):
    return ((square - 1) % 7 + (square - 1) // 7) & 1


# The weights in force, as a dict keyed by WEIGHT_NAMES. This is the shape the tuning
# harness saves, compares and hands back to setWeights.
def getWeights():
    return {name: globals()[name] for name in WEIGHT_NAMES}


# What the evaluator shipped with, frozen at import before anything can change them.
DEFAULT_WEIGHTS = getWeights()


# Puts a set of weights in force. Takes a dict of some or all of WEIGHT_NAMES; anything not
# named is left as it is.
#
# Integers only, checked with type() rather than isinstance so that a bool is refused too.
# A float here would quietly undo the reason the evaluator is in whole numbers at all --
# CPython and PyPy disagreeing in the last place and flipping a cutoff -- and it would do so
# only on the machine where it was tried.
#
# The transposition table holds scores that the weights in force WHEN THEY WERE WRITTEN
# produced, so changing the weights under it leaves it full of answers to a different
# question. By default this clears it, along with the killer and history tables. The tuning
# harness keeps a table per side and swaps them itself, so it passes clear = False.
def setWeights(weights, clear = True):
    for name, value in weights.items():
        if name not in WEIGHT_RANGES:
            raise KeyError("no evaluation weight called %r" % (name,))
        if type(value) is not int:
            raise TypeError("%s must be an int, not %s -- floats re-open the CPython/PyPy "
                            "last-place bug" % (name, type(value).__name__))
        low, high = WEIGHT_RANGES[name]
        if not low <= value <= high:
            raise ValueError("%s = %d is outside %d..%d" % (name, value, low, high))

    for name, value in weights.items():
        globals()[name] = value
    _rebuildWeightTables()

    if clear: newGame()


# The spread penalty: SPREAD_WEIGHT times the larger of the two coordinate standard
# deviations, exactly, with SPREAD_WEIGHT already in SCALE units.
#
# The standard deviation of n whole numbers with sum s and sum of squares s2 is
# sqrt(n*s2 - s*s) / n, and the quantity under the root is itself a whole number. Both axes
# always have the same n -- a group contributes one coordinate to each -- so which axis is
# the larger can be decided by comparing those two whole numbers, before any root is drawn.
#
# math.isqrt is an exact integer square root, so scaling by SPREAD_WEIGHT^2 going in and
# dividing by n coming out gives SPREAD_WEIGHT * stdev with no float involved. It truncates
# rather than rounds, so the answer can sit a thousandth of a point under the true value --
# that is a rounding of the heuristic, not a disagreement: every machine truncates to the
# same integer.
#
# This used to be written isqrt(9 * SCALE * SCALE * q) // (2 * n), with the 1.5 carried as a
# 3 under the root and a 2 in the divisor. Pulling it out as a weight is exact and not just
# close: with a = 1500 * sqrt(q) and k = floor(a), floor(2a) is 2k or 2k + 1, and
# floor((2k + e) / 2n) = floor(k / n) for e in {0, 1}, because writing k = rn + s with s < n
# gives 2s + e <= 2n - 1 < 2n. Checked exhaustively for every n up to 12 and every q the
# board can produce (coordinates run 0..6, so q <= 9 n^2): zero mismatches.
def spreadPenalty(n, sx, sx2, sy, sy2):
    q = max(n * sx2 - sx * sx, n * sy2 - sy * sy)
    if q <= 0: return 0

    return math.isqrt(SPREAD_WEIGHT * SPREAD_WEIGHT * q) // n


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
    unpack = Hasher.UNPACK

    # the weights, read once rather than at every square
    stackValue = STACK_VALUE
    diagWeight = DIAG_WEIGHT
    prisonerPawn = PRISONER_PAWN_WEIGHT
    prisonerSpy = PRISONER_SPY_WEIGHT
    captivePct = CAPTIVE_PCT
    anyPost = ANY_POST

    adv = [0, 0]
    groups = [0, 0]
    sx = [0, 0]; sx2 = [0, 0]; sy = [0, 0]; sy2 = [0, 0]
    royalIdiot = [False, False]
    # a side with six on one square scores the win outright, whatever else it has going on
    won = [False, False]

    # for the candidate terms: each side's standing groups as (square, pieces, fields), and
    # where its spy stands, 0 if it doesn't. Only gathered when a term wants them.
    standing = [[], []]
    spySquare = [0, 0]

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
                if myPieces < 6: adv[w] += stackValue[myPieces]
                elif myPieces == 6: won[w] = True

                # drives pieces towards each other
                sx[w] += x; sx2[w] += x * x
                sy[w] += y; sy2[w] += y * y

                # mobile ground is worth standing on. One dict lookup, no search.
                adv[w] += diagWeight * JUMPREACH[square] * myPieces

                if anyPost:
                    standing[w].append((square, myPieces, s))
                    if s[Hasher.SPY]: spySquare[w] = square

            # holding prisoners is worth something...
            if s[Hasher.PRISFLAG]:
                adv[w] += s[Hasher.CAPPAWNS] * prisonerPawn + s[Hasher.CAPSPY] * prisonerSpy

            # DON'T PUT UR DANG ROYAL AND SPY IN THE SAME PLACE
            if s[Hasher.SPY] and s[Hasher.ROYAL] and myPieces != 6: royalIdiot[w] = True

        ###### and the other side's people held in it ######
        # a captured group is never a dragon and never holds prisoners of its own, so the
        # mobility, prisoner and royal-and-spy terms have nothing to say about it
        theirs = s[Hasher.PRISCOUNT]
        if theirs:
            groups[o] += 1
            # a royal is never taken, so this is at most five and never the win
            adv[o] += stackValue[theirs]

            sx[o] += x; sx2[o] += x * x
            sy[o] += y; sy2[o] += y * y

            # ...and being held costs CAPTIVE_PCT of what the stack is worth. Exact at the
            # default of 200: 200 * v // 100 is 2 * v for any whole v.
            adv[o] -= (captivePct * stackValue[theirs]) // 100

    for side in (0, 1):
        if won[side]:
            adv[side] = WIN_SCORE
            continue

        # swept off the board, so the terms below have nothing to divide by
        if groups[side] == 0: continue

        # Hand tuning once moved each of these off its value in turn and found nothing
        # better; 40,000 games of SPSA agreed about these two. The spread penalty is the
        # one that matters: at 0 the AI loses 114 Elo.
        adv[side] -= groups[side] * GROUP_PENALTY
        adv[side] -= spreadPenalty(groups[side], sx[side], sx2[side], sy[side], sy2[side])

        # if the royal and the spy are together, the penalty grows as the groups thin out
        if royalIdiot[side]: adv[side] -= (groups[side] * groups[side]) * ROYAL_SPY_PENALTY

        if anyPost: adv[side] += candidateTerms(side, standing, spySquare)

    return adv


# The candidate terms for one side -- see the weights up top for what each one means. Kept
# out of evaluateSides so that the evaluator the goldens were recorded from reads as it did,
# and so that with every weight at 0 none of this is reached at all.
def candidateTerms(side, standing, spySquare):
    mine = standing[side]
    total = 0

    spySq = spySquare[side]
    if spySq:
        if SPY_STACK_WEIGHT:
            for square, pieces, s in mine:
                if square == spySq:
                    total += SPY_STACK_WEIGHT * s[Hasher.PAWNS]
                    break

        if SPY_DIST_WEIGHT or WRONG_COLOUR_PENALTY:
            distance = JUMPDIST
            spyColour = squareColour(spySq)
            for square, pieces, s in mine:
                if square == spySq: continue
                total -= SPY_DIST_WEIGHT * distance[square][spySq] * pieces
                if WRONG_COLOUR_PENALTY and squareColour(square) != spyColour:
                    total -= WRONG_COLOUR_PENALTY * pieces

    if THREAT_PENALTY:
        theirs = standing[1 - side]
        for square, pieces, s in mine:
            # nothing lands on a royal, or on a square holding the mover's own captives
            if s[Hasher.ROYAL] or s[Hasher.PRISFLAG]: continue
            weight = s[Hasher.WEIGHT]
            target = square - 1
            for esq, epieces, es in theirs:
                # a stack with a spy in it can't land on anything; a lighter one can't take us
                if es[Hasher.SPY]: continue
                captors = es[Hasher.CAPTORS]
                if captors < weight: continue
                if target in JUMPHIT[captors][esq]:
                    total -= THREAT_PENALTY * pieces
                    break

    return total


# One side's score. Nothing in the search uses this -- it evaluates both sides at once --
# but it is the shape the heuristic is easiest to read in, and it keeps the two definitions
# from drifting apart by being the same one.
def checkPosition(cBoard, contr, spaces = None):
    return evaluateSides(cBoard)[contr]


def fullCheck(cBoard, contr):
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

# How many positions one generation holds before it is retired. 300k of these costs a few
# hundred MB, which is the point where keeping more stops paying for itself at the depths
# anyone actually plays at.
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
def minimax(cBoard, contr, rootContr, alpha, beta, depthTrack, pvMove = None, atRoot = False):
    global calcCount
    calcCount += 1

    gameEnd, winner = Hasher.Check_For_Winner(cBoard)
    if gameEnd:
        # scaling by the depth left makes a win now beat the same win three moves out
        if winner[rootContr]: return [WIN_SCORE * (depthTrack + 1), None]
        return [-WIN_SCORE * (depthTrack + 1), None]

    if depthTrack == 0: return [fullCheck(cBoard, rootContr), None]

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

        score = minimax(child, int(not contr), rootContr, alpha, beta, depthTrack - 1)[0]

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
# on positions the new one is unlikely to reach. Call it wherever Engine.koReset is called.
def newGame():
    global killers, history, table, tableOld
    killers = {}
    history = {}
    table = {}
    tableOld = {}


# Picks a move and plays it. Returns [board, move, score] -- board unchanged if there was
# no legal move.
def takeTurn(cBoard, contr, depth = 3):
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


# The square a side would gather on and what it costs to get everyone there, in lone-piece
# jumps. Squares rather than pieces: a stack travels together, so it is one journey however
# much is standing on it. Returns [square, cost], or [0, 0] with nothing on the board.
def enteringAnchor(spaces, mine):
    if not mine: return [0, 0]

    anchor = 0
    gather = None
    for candidate in range(1, 50):
        total = 0
        for square in mine: total += JUMPDIST[square][candidate]

        # ties go to the square with more ways out of it
        if gather is None or total < gather or (total == gather and JUMPREACH[candidate] > JUMPREACH[anchor]):
            gather = total
            anchor = candidate

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
    scored = []
    for square in Engine.enteringOptions(cBoard, contr, isSpy, spaces):
        after = Engine.dropPiece(cBoard, square, contr, piece)
        afterSpaces = Hasher.Parse_Board(after)
        scored.append([entryDiff(afterSpaces, contr), square, after, afterSpaces])

    # square number last and only to settle what is otherwise a coin toss -- sorting on it
    # any earlier is what made the old version march down the a file
    scored.sort(key=lambda row: (-row[0], -JUMPREACH[row[1]], row[1]))
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

    maximizing = (contr == rootContr)
    best = None

    for row in shortlist:
        score = enterSearch(row[2], row[3], steps, index + 1, rootContr, alpha, beta, depth - 1)

        if best is None or (score > best if maximizing else score < best): best = score

        if maximizing: alpha = max(alpha, score)
        else: beta = min(beta, score)

        if beta <= alpha: break

    return best


# Picks where to enter a piece. Returns None if nowhere is legal.
def chooseEntry(cBoard, contr, piece, isSpy, depth = ENTRY_DEPTH):
    spaces = Hasher.Parse_Board(cBoard)

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
        score = enterSearch(row[2], row[3], steps, start + 1, contr, alpha, math.inf, depth - 1)

        if bestScore is None or score > bestScore:
            bestScore = score
            bestSquare = row[1]

        alpha = max(alpha, score)

    return bestSquare


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
        text += " " + kind + "s to " + Hasher.IndexToAlg(target).upper()

    if move[MOVE_PRIS]: text += " (with prisoners)"
    return text
