# RoyalsLib was imported here and never used once -- dropped with the package split.
import random

from royals_engine import hasher as Hasher

Diag1 = [[0, 6], [1, 5], [2, 4], [3, 3], [4, 2], [5, 1], [6, 0]]
Diag2 = [[6, 6], [5, 5], [4, 4], [3, 3], [2, 2], [1, 1], [0, 0]]
jumpDirs = [[1, 1], [1, -1], [-1, 1], [-1, -1]]
pushDirs = [[0, 1], [0, -1], [-1, 0], [1, 0]]

# A direction is carried as an index into these lists wherever it is being used rather than
# displayed. This maps a [dx, dy] back to one, for the callers that still hold a pair -- the
# GUI's break buttons and MainPlay's typed headings. Jumps need no such thing: a jump
# direction never leaves this file.
PUSH_INDEX = {(d[0], d[1]): i for i, d in enumerate(pushDirs)}

# What each push direction is called, indexed the same way. direction[0] moves along the
# columns and direction[1] along the ranks, and rank 1 prints at the top -- so +1 on the
# second axis reads as "down". The backup labelled these the other way round because its
# board was indexed [row][column].
HEADINGS = ["down", "up", "left", "right"]


# Whichever way a direction was handed over. An index passes straight through, so a caller
# that already has one pays nothing and one holding a pair is not made to care.
def pushIndex(direction):
    if isinstance(direction, int): return direction
    return PUSH_INDEX.get((direction[0], direction[1]))


####### The origin object #######
# A chosen square plus how it is being moved out of, as
#   (movingPris, spyBreak, square, code)
# with square 1-based. It used to be a bit string -- two flag bits, then the square's pointer
# into the packed board, then its payload -- which is why anything reading one had to slice
# and re-parse. The square is carried outright now, so there is nothing to look up.
ORIGIN_PRIS = 0
ORIGIN_SPYBREAK = 1
ORIGIN_SQUARE = 2
ORIGIN_CODE = 3


def makeOrigin(cBoard, square, movingPris = False, spyBreak = False):
    return (movingPris, spyBreak, square, cBoard[square - 1])


# The weight of the stack an origin object describes -- the same question the other three
# answer, with prisoners counting against the total only when they are being carried along.
def sumWeightOrigin(tOrigin):
    s = Hasher.UNPACK[tOrigin[ORIGIN_CODE]]

    # counting fields rather than reading bits at fixed offsets means an empty square
    # answers 0 instead of running off the end of the payload.
    if tOrigin[ORIGIN_PRIS]: return s[Hasher.STRENGTH]
    return s[Hasher.CAPTORS]

# The weights, read off a Parse_Space field list. A dragon is worth 3 whichever way you
# count it. Everything hot works from these; the raw-space versions underneath just parse
# and hand over, so there is one copy of each rule.
#
# The rules themselves moved into Hasher, because that is where they get precomputed for
# every code -- see Hasher.WEIGHT and friends. They keep their names here, since this is
# where the game's vocabulary lives and every caller already says Engine.spaceWeight.
spaceWeight = Hasher.spaceWeight
spaceStrength = Hasher.spaceStrength
spaceCaptors = Hasher.spaceCaptors


def sumWeight(tData):
    return Hasher.UNPACK[tData][Hasher.WEIGHT]

def sumStrength(tData):
    return Hasher.UNPACK[tData][Hasher.STRENGTH]

def sumWeightOfCaptors(tData):
    return Hasher.UNPACK[tData][Hasher.CAPTORS]



def wrapToBoard(x, y) :
	#print("n+ " + str(coords))
	return (((x + 14) % 7) + ((y + 14) % 7) * 7)


# The 1-based square n steps along direction from a 1-based origin, wrapped.
def pushSquare(origin, direction, n):
    x = (origin - 1) % 7 + direction[0] * n
    y = (origin - 1) // 7 + direction[1] * n
    return wrapToBoard(x, y) + 1


# Works out the unit step between two adjacent 1-based squares. A push only ever moves one
# square, so a gap of 6 along an axis means it wrapped round the edge rather than travelled.
# Returns [0, 0] if the squares aren't actually neighbours.
def findDirection(origin, destination):
    stepX = ((destination - 1) % 7 - (origin - 1) % 7 + 7) % 7
    stepY = ((destination - 1) // 7 - (origin - 1) // 7 + 7) % 7

    dirX = 0
    if stepX == 1: dirX = 1
    elif stepX == 6: dirX = -1

    dirY = 0
    if stepY == 1: dirY = 1
    elif stepY == 6: dirY = -1

    return [dirX, dirY]


####### Rays #######
# Every walk this file makes across the board -- a jump strand, the line a push shoves, the
# trail a break scatters along -- is a fixed sequence of squares. Which sequence depends on
# where you start and which way you go, and on nothing else: not on what is standing there,
# not on who is moving, not on how heavy they are. Weight decides how FAR along the sequence
# you get, never what the sequence is.
#
# That was being rediscovered a step at a time, in the innermost loop of the innermost
# function of the search: two multiplications, two additions, two modulos and a bounds test
# per square, plus -- in the jump loop -- `moveX in range(0, 7)`, which builds a range object
# to answer what a comparison answers five times faster, and `[originX, originY] in
# correctDiag`, which builds a list and scans a list of lists to re-answer a question about
# the origin that could not have changed since the strand set out.
#
# So the sequences are drawn once here, at import, and walked as tuples thereafter.
#
# All of them hold 0-based square indices, because that is what indexes a parsed board.
# The few callers that want 1-based square numbers add one at their own edge.

# The most a jump can travel: a stack is at most the spy, four pawns and the royal.
MAX_STACK = 6

# How far a push can reach before getLegalPushLength's runaway backstop gives up. The board
# wraps, so the line itself is endless -- this is the point past which something has gone
# wrong rather than a limit of the geometry.
PUSH_REACH = 21

# The most pieces a break can scatter: a full stack of six plus the five prisoners it could
# be holding. Weight counts prisoners here, unlike everywhere jumps are concerned.
MAX_SCATTER = 11


def buildJumpRays():
    rays = [None] * 50
    for square in range(1, 50):
        originX = (square - 1) % 7
        originY = (square - 1) // 7

        perDir = []
        for j in jumpDirs:
            # a strand may only wrap round the edge if it set out from one of the two long
            # diagonals, and which diagonal that is depends on the direction's slope
            correctDiag = Diag1 if j[0] + j[1] == 0 else Diag2
            canWrap = [originX, originY] in correctDiag

            moveX, moveY = originX, originY
            strand = []
            for step in range(0, MAX_STACK):
                moveX += j[0]
                moveY += j[1]

                if not (0 <= moveX < 7 and 0 <= moveY < 7):
                    # off the edge and not entitled to wrap: the strand simply stops
                    if not canWrap: break
                    moveX = (moveX + 7) % 7
                    moveY = (moveY + 7) % 7

                strand.append(moveX + moveY * 7)

            perDir.append(tuple(strand))
        rays[square] = tuple(perDir)
    return tuple(rays)


def buildPushRays():
    rays = [None] * 50
    for square in range(1, 50):
        x0 = (square - 1) % 7
        y0 = (square - 1) // 7

        perDir = []
        for d in pushDirs:
            # You can't start a push by shoving something off the edge -- there has to be a
            # square next to you to push into. An empty ray says exactly that. Everything
            # beyond the first step wraps as normal, which is why only this one is tested.
            if not (0 <= x0 + d[0] < 7 and 0 <= y0 + d[1] < 7):
                perDir.append(())
                continue

            perDir.append(tuple(wrapToBoard(x0 + d[0] * n, y0 + d[1] * n)
                                for n in range(1, PUSH_REACH + 1)))
        rays[square] = tuple(perDir)
    return tuple(rays)


def buildBreakRays():
    # a break starts by dropping a piece back onto the square it is leaving, so unlike the
    # two above this ray includes its own origin, at offset 0
    rays = [None] * 50
    for square in range(1, 50):
        x0 = (square - 1) % 7
        y0 = (square - 1) // 7
        rays[square] = tuple(tuple(wrapToBoard(x0 + d[0] * n, y0 + d[1] * n)
                                   for n in range(0, MAX_SCATTER))
                             for d in pushDirs)
    return tuple(rays)


# All indexed [1-based square][direction index].
JUMPRAY = buildJumpRays()
PUSHRAY = buildPushRays()
BREAKRAY = buildBreakRays()

# Which push direction leads from one 1-based square to an adjacent one, or None if they
# aren't neighbours -- findDirection's answer, looked up rather than worked out.
PUSHFROM = tuple(tuple(pushIndex(findDirection(origin, destination)) if origin and destination
                       else None
                       for destination in range(0, 50))
                 for origin in range(0, 50))


# Returns the integer range of a push. GODDAMN.
# Whether a stack is the sort of thing that can free allies it pushes into. The rule is the
# jump rule: freeing means stepping onto the square the prisoners were held on and standing
# with them, so anything that can't legally land on top of other pieces can't free either.
#   - a spy can't, standing or in a stack. It slips into a square rather than onto it, which
#     is the same reason it shatters what it shoves instead of joining it.
#   - a dragon can't. Its square holds nothing but the dragon, so there is nowhere for the
#     freed pieces to stand.
#   - a stack carrying its own prisoners can't, whatever else is in it, royal included.
#     Freeing would leave allies it just freed, the stack itself, and the prisoners it
#     brought along all on one square -- a sandwich, with the stack pushing over prisoners
#     from both sides at once.
# The royal is no part of this. It decides the DISCOUNT, not the right to free.
def canFreePrisoners(origin, movingPris):
    if movingPris: return False
    if origin[Hasher.DRAGON]: return False
    if origin[Hasher.SPY]: return False
    return True


# Whether this particular push would be a freeing one: an eligible stack, shoving an enemy
# square that is holding pieces of ours. Prisoners belong to whichever side does NOT control
# the square, so they are ours exactly when the square is the enemy's.
def wouldFree(origin, targ, contr, movingPris):
    # asked once per push direction per origin per node, so it reads the fields itself
    # rather than going through canFreePrisoners and Space_Has_Pris to ask the same three
    # questions. Same three questions, in the same order.
    if movingPris: return False
    if not targ[Hasher.PRISFLAG]: return False
    if targ[Hasher.SIDE] == contr: return False
    return not (origin[Hasher.DRAGON] or origin[Hasher.SPY])


#   freePris -- which of the two branches is being asked about. A push into a square holding
#               allies of ours is two different moves: free them, leaving the jailers to be
#               shoved on alone, or shove the whole square along with the prisoners still in
#               it. They cost differently, so they have to be measured separately, and
#               checkMoves offers whichever of them come back legal.
#   dirIndex -- an index into pushDirs, or the [dx, dy] pair itself for callers that still
#               hold one. The line being walked comes from PUSHRAY, so the direction is
#               only ever used to choose it.
def getLegalPushLength(cBoard, inputSpace, inputData, dirIndex, contr, movingPris, spyCap, spaces = None, freePris = False):
    # one walk of the board serves the whole check; callers already holding a parsed board
    # pass it straight in.
    if spaces is None: spaces = Hasher.Parse_Board(cBoard)

    if not isinstance(dirIndex, int): dirIndex = pushIndex(dirIndex)

    # an empty ray is a first step off the edge, and there is no push without a square to
    # push into
    ray = PUSHRAY[inputSpace][dirIndex]
    if not ray: return 0

    origin = Hasher.UNPACK[inputData]
    pLength = 0

    # inputData is a square code, the same thing Get_Space_Data returns.
    if movingPris: strength = origin[Hasher.STRENGTH]
    else: strength = origin[Hasher.CAPTORS]

    # basically, each time we check whether the next square is occupied, and we subtract the total weight of said square from the strength.
    # If strength remains 0 or greater, then this direction is an option, and a non-zero length of push is returned.
    for target in ray:
        targ = spaces[target]

        ###CLASSIC GAUNTLET STRUCTURE -- exception beyond emptiness, the main check is the strength minus the accumulated length.
        #first, let's check if the space is occupied... also where the loop ends with a 'success':
        if not targ[Hasher.OCCUPIED]:
            return pLength

        #in the first space of adjacency, there are some exceptions having to do with spies, royals, and captured pieces. BIG
        if pLength == 0:

            #original space is just a spy: weight 1 means the captors' weight is the spy and nothing else.
            spyOnly = origin[Hasher.SPY] and origin[Hasher.CAPTORS] == 1

            #the freeing branch, and only if this stack is entitled to it. Asking to free
            #where that isn't on offer is not the same move made expensively -- it isn't a
            #move, so it reports no push rather than falling through to the other branch.
            if freePris:
                if not wouldFree(origin, targ, contr, movingPris): return 0

                #the royal's discount: it shoves the jailers aside for one, however many of
                #them are standing. Everyone else pays for the jailers in full -- the
                #prisoners are what is being freed, so they never cost anything here.
                if origin[Hasher.ROYAL]:
                    strength -= 1
                else:
                    strength -= targ[Hasher.CAPTORS]

            #a lone spy slips in, so that square always costs exactly 1.
            elif spyOnly:
                strength -= 1

                #...and the stack it slipped into comes apart. That is not decided here:
                #this function only ever answers a length, and a break is its own move type.
                #exePush does the scattering once the shuffle is done -- see the shatter
                #branch at the end of it.

            #shoving the whole square along, prisoners and all. They are being carried rather
            #than liberated, so their weight counts against the push exactly like anyone
            #else's. On a square holding nobody this is the same number spaceCaptors gives,
            #which is why an ordinary push is unaffected by any of this.
            else:
                strength -= targ[Hasher.WEIGHT]

        # but outside of those circumstances, we sum the weight of the adjacent square and subtract it from our original strength:
        else:
            strength -= targ[Hasher.WEIGHT]

        #and then we check: is it greater than -1? If so, keep going. If not, no push possible.
        if strength < 0: return 0
        pLength += 1

    # runaway backstop -- a push can't outrun the board. Falling off the end of the ray means
    # PUSH_REACH squares in a row were occupied and paid for, which is not a position.
    # (This used to print a debug marker to stdout. The engine is imported by a web worker
    # now, so it stays silent and simply reports no push -- which is what it always meant.)
    return 0


# Returns how many squares a break out of inputSpace would cover, 0 meaning no break.
# A break scatters the stack one piece per square along direction, starting on the origin
# itself, so the range can never exceed the piece count.
#   inputData -- the origin's square code, the shape Get_Space_Data returns.
#   contr     -- who is breaking. The square's own side is what the scatter keys off, so
#                this only matters if the two ever disagree.
def checkBreak(cBoard, inputSpace, inputData, dirIndex, contr, spaces = None):
    if spaces is None: spaces = Hasher.Parse_Board(cBoard)
    origin = Hasher.UNPACK[inputData]

    # nothing to scatter
    if not origin[Hasher.OCCUPIED]: return 0

    # weight equals piece count here: checkMoves only calls this for stacks holding a spy,
    # which keeps dragons (weight 3, but a single piece) off this path.
    pieces = origin[Hasher.WEIGHT]

    # the side holding the square owns the standing stack; the prisoners are the other's,
    # and they are the ones that fall first
    control = origin[Hasher.SIDE]
    prisoners = origin[Hasher.PRISCOUNT]

    if not isinstance(dirIndex, int): dirIndex = pushIndex(dirIndex)
    ray = BREAKRAY[inputSpace][dirIndex]

    breakRange = 0
    for i in range(0, pieces):
        targ = spaces[ray[i]]

        # every 7th step lands back on the origin, which is being emptied anyway, so it
        # has nothing to block against.
        if i % 7 != 0:
            # A royal or a dragon stops the scatter dead, either side's. The dragon is not
            # only a rule: dropPiece rebuilds the square through Build_Space, which zeroes
            # every field past a dragon, so a piece merged onto one would erase it.
            if targ[Hasher.ROYAL] or targ[Hasher.DRAGON]: break

            # Everything left is asked of the piece that is falling and not of the player
            # breaking, and those are not always the same side: prisoners drop first, so
            # the early steps are dropping the other side's people and the rest our own.
            # Getting this wrong the obvious way -- measuring against the breaker -- lets a
            # freed captive land on a stack of its captor's and take the whole thing
            # prisoner on its way past.
            if i < prisoners: dropping = int(not control)
            else: dropping = control

            # A square the falling piece is at home on is no obstacle at all; it joins
            # whatever is standing there. Only the other side's squares block, and only
            # these two ways.
            if targ[Hasher.SIDE] != dropping:
                # More than one piece is too much to knock aside. The threshold grows by
                # one each full lap, which is what (i // 7) + 1 is for -- integer division,
                # as it was before the py2 port turned it into a float -- because by then
                # this walk has already dropped a piece here that the board it is reading
                # doesn't show. A lone enemy is not an obstacle: the piece lands on it and
                # takes it captive.
                if targ[Hasher.WEIGHT] > (i // 7) + 1: break

                # And an enemy holding anyone is an obstacle whatever it weighs. This is
                # not the test above said twice: a lone jailer weighs 2, so weight stops it
                # on the first lap, but by the second the tolerance is 2 as well and it
                # would slip straight through. What that would cost is the rule that a
                # break never frees anybody -- dropPiece's capture branch releases whoever
                # the square it lands on was holding, and this is the only thing keeping
                # exeBreak out of it.
                if targ[Hasher.PRISFLAG]: break

        breakRange += 1

    # a range of 1 drops every piece back on the origin, which is no move at all --
    # the break has to get at least one piece off the square to be worth offering.
    if breakRange < 2: return 0

    return breakRange


####### Entering #######
# Before anyone moves, the pieces go onto the board one at a time, players alternating:
# royals, then the four pawns, then the spies. A royal or a pawn may not be entered
# touching anything you already control, your dragon included; a spy ignores that and only
# needs an empty square. Blue enters first, so red takes the opening move.

# The eight neighbours of a square. Entering does not wrap round the edges the way moves do
# -- the backup's placement threw away any neighbour off the board, so this does too.
enterAdj = [[-1, -1], [-1, 0], [-1, 1],
            [ 0, -1],          [ 0, 1],
            [ 1, -1], [ 1, 0], [ 1, 1]]


# The on-board neighbours of a 1-based square, as 1-based squares.
def enteringNeighbours(square):
    x = (square - 1) % 7
    y = (square - 1) // 7

    out = []
    for step in enterAdj:
        nx = x + step[0]
        ny = y + step[1]
        if nx < 0 or nx > 6 or ny < 0 or ny > 6: continue
        out.append(ny * 7 + nx + 1)

    return out


# Whether a piece may be entered on a square. Pieces the other side controls are no
# obstacle -- only your own crowd you out.
def enteringLegal(spaces, square, contr, isSpy):
    if spaces[square - 1][Hasher.OCCUPIED]: return False

    # a spy slips in anywhere empty
    if isSpy: return True

    for neighbour in enteringNeighbours(square):
        s = spaces[neighbour - 1]
        if s[Hasher.OCCUPIED] and s[Hasher.SIDE] == contr: return False

    return True


# Every square a piece could be entered on.
def enteringOptions(cBoard, contr, isSpy, spaces = None):
    if spaces is None: spaces = Hasher.Parse_Board(cBoard)

    options = []
    for square in range(1, 50):
        if enteringLegal(spaces, square, contr, isSpy): options.append(square)

    return options


# The RNG for one placement of a random opening, where nobody picks their squares and the
# twelve pieces land wherever the rules allow. Seeded per step rather than per game, so an
# opening is a pure function of (seed, step): the desktop and the server draw the same
# squares from the same seed without having to agree on how many times either of them has
# already called this, and a step some side had to sit out does not shift every later draw.
#
# The shift is a bijection because a step is 0..11, so two seeds can never collide.
def entryRng(seed, step):
    return random.Random((int(seed) << 6) | int(step))


# A square to enter on, drawn uniformly from the legal ones, or None if there are none.
# This is the whole of the random opening: it is enteringOptions and a die, so a placement
# it makes is legal for exactly the reason a clicked one is, and neither the rules nor the
# entering heuristic in ai.py has anything to say about it.
#
# The rng is a parameter rather than something seeded here on purpose -- no module-level RNG
# state in the engine, and one definition of the seed-to-step derivation instead of two that
# can drift apart.
def randomEntry(cBoard, contr, isSpy, rng, spaces = None):
    options = enteringOptions(cBoard, contr, isSpy, spaces)
    if not options: return None

    return options[rng.randrange(len(options))]


# The whole entering order, as [side, piece] steps in the order they happen.
def enteringSequence():
    order = [Hasher.ROYAL]
    for n in range(0, 4): order.append(Hasher.PAWNS)
    order.append(Hasher.SPY)

    steps = []
    for piece in order:
        # blue enters first at every stage
        for contr in (0, 1): steps.append([contr, piece])

    return steps


# Reversed entering: **the player who picks a square is not the player who owns the piece.**
# You lay out your opponent's army, trying to leave them as badly placed as the rules allow.
#
# A function rather than `1 - contr` written out at each call site, because the interesting
# thing about this rule is not the arithmetic but which of the two sides a given piece of
# code means. Every place that has to tell "whose piece is this" apart from "whose decision
# is this" now says so in its own text.
#
# **Legality is untouched, and deliberately.** "A royal or pawn may not enter on a square
# touching anything you already control" is a fact about the piece's OWNER -- it is their
# army that must not crowd itself -- so enteringOptions is still asked about the owner and
# answers exactly as it did before. Only the hand on the piece changes.
#
# enteringSequence is untouched too: step i still places the same side's same piece in the
# same order. That is what keeps the record format working without an edit, since a `@Rd3`
# token carries the piece and the square and derives the side from the ply index.
def enteringChooser(contr):
    return 1 - contr


####### KO #######
# A move may not put the board back into any position the game has already stood in -- not
# a window of recent turns but the whole history, from the position entering left behind
# onwards. Every move must leave the game somewhere it has never been.
#
# The backup kept the last four positions in koTrack and compared the board against each of
# them after the move had been made, taking it back and making the side move again if it
# matched. Two things change here. A board is one canonical value that hashes on its own, so
# the comparison is cheap enough to make before the move instead of after: the search asks
# koBreaks about each of its root moves and never offers one that repeats. That does away
# with the backup's koNO -- there is no rejected move to remember, because a rejected move
# never gets proposed. And the history is a set, so testing against a game's worth of
# positions costs the same as testing against four.

# every position the game has stood in
koTrack = set()

# Bumped whenever that set changes, and never reused. The search keeps its answers between
# moves now, and the ones it worked out at the root had ko-breaking moves struck off them --
# so they are only good for the history that was standing at the time. Stamping them with
# this retires them the moment another position is recorded, without having to work out
# which of them the new entry actually invalidated. Deeper answers need no stamp: nothing
# below the root consults the ko history at all, so they stay true for the rest of the game.
koGeneration = 0


# Wipes the history. A new game has nothing to repeat.
def koReset():
    global koGeneration
    koTrack.clear()
    koGeneration += 1


# Whether a board returns the game to a position it has already been in.
def koBreaks(cBoard):
    return cBoard in koTrack


# Files a position the game has actually reached.
def koRecord(cBoard):
    global koGeneration
    koTrack.add(cBoard)
    koGeneration += 1


# Performs a jump: the stack sitting on origin lands on destination.
#   origin, destination -- 1-based square numbers
#   movingPris          -- whether the prisoners on the origin come along for the ride
# Returns the new board. Mod_Space splices rather than mutates, so the caller has to
# take the value back.
# NOTE assumes the mover controls the origin. The spyBreak case -- moving your own spy
# out of an enemy square that holds it prisoner -- needs its own handling, and doesn't
# have any yet on either side of the port.
def exeMove(cBoard, origin, destination, contr, movingPris):
    # both squares come from move generation, so they are known-good 1-based numbers --
    # this is the search's make-move path and skips Get_Space_Data's bounds check
    o = Hasher.UNPACK[cBoard[origin - 1]]
    d = Hasher.UNPACK[cBoard[destination - 1]]

    # the pieces making the trip, plus their prisoners if those were invited
    newSpy = o[Hasher.SPY]
    newPawns = o[Hasher.PAWNS]
    newRoyal = o[Hasher.ROYAL]
    newCapSpy = 0
    newCapPawns = 0
    if movingPris:
        newCapSpy = o[Hasher.CAPSPY]
        newCapPawns = o[Hasher.CAPPAWNS]

    # empty destination merges with nothing. Deliberately not OCCUPIED: that would count a
    # dragon, and a dragon square zeroes every other field, so merging with one would write
    # the arriving stack into a square that has nowhere to put it.
    if d[Hasher.SIDE] or d[Hasher.SPY] or d[Hasher.PAWNS] or d[Hasher.ROYAL] or d[Hasher.CAPSPY] or d[Hasher.CAPPAWNS]:
        if d[Hasher.SIDE] == contr:
            # landing on your own stack: the two merge and its prisoners stay put.
            newSpy += d[Hasher.SPY]
            newPawns += d[Hasher.PAWNS]
            newRoyal += d[Hasher.ROYAL]
            newCapSpy += d[Hasher.CAPSPY]
            newCapPawns += d[Hasher.CAPPAWNS]
        else:
            # landing on the enemy takes them prisoner. checkMoves already refused this
            # square if it held a royal, a dragon, or allies of yours in captivity.
            newCapSpy += d[Hasher.SPY]
            newCapPawns += d[Hasher.PAWNS]

    newDest = Hasher.Build_Space(contr, o[Hasher.DRAGON], newSpy, newPawns, newRoyal, newCapSpy, newCapPawns)

    # prisoners left behind go free, standing back up as a stack of their own side.
    if movingPris:
        newOrigin = 0
    else:
        newOrigin = Hasher.Build_Space(int(not contr), 0, o[Hasher.CAPSPY], o[Hasher.CAPPAWNS], 0)

    # both squares were read off the board above, so the order these go back in doesn't
    # matter -- it used to, because a write could change the packed board's length and shift
    # every square after it.
    cBoard = Hasher.Mod_Space(cBoard, destination, newDest)
    cBoard = Hasher.Mod_Space(cBoard, origin, newOrigin)
    return cBoard


# Drops a single piece belonging to `side` onto a square. `piece` is one of Hasher.SPY,
# Hasher.PAWNS or Hasher.ROYAL. Returns the new board.
def dropPiece(cBoard, square, side, piece):
    t = Hasher.UNPACK[cBoard[square - 1]]

    spy = pawns = royal = capSpy = capPawns = 0

    if not (t[Hasher.SPY] or t[Hasher.PAWNS] or t[Hasher.ROYAL] or t[Hasher.CAPSPY] or t[Hasher.CAPPAWNS]):
        # empty square, nothing to reconcile
        pass
    elif t[Hasher.SIDE] == side:
        # our own square: join the stack, its prisoners stay prisoners
        spy = t[Hasher.SPY]; pawns = t[Hasher.PAWNS]; royal = t[Hasher.ROYAL]
        capSpy = t[Hasher.CAPSPY]; capPawns = t[Hasher.CAPPAWNS]
    else:
        # landing on the enemy takes them captive, and frees any of ours they were holding
        capSpy = t[Hasher.SPY]; capPawns = t[Hasher.PAWNS]
        spy = t[Hasher.CAPSPY]; pawns = t[Hasher.CAPPAWNS]

    if piece == Hasher.SPY: spy += 1
    elif piece == Hasher.PAWNS: pawns += 1
    else: royal += 1

    return Hasher.Mod_Space(cBoard, square,
                            Hasher.Build_Space(side, 0, spy, pawns, royal, capSpy, capPawns))


# Shatters the stack on inputSquare, scattering it one piece per square along direction.
# Prisoners fall first, then the standing stack with the royal last, and whatever is still
# in hand lands together on the final square. Returns the new board.
def exeBreak(cBoard, inputSquare, direction, contr):
    data = cBoard[inputSquare - 1]
    s = Hasher.UNPACK[data]

    # a dragon is a single piece, so there is nothing to scatter
    if s[Hasher.DRAGON]: return cBoard

    # the GUI and the older text modes hand a [dx, dy] over; the search hands an index
    direction = pushIndex(direction)
    if direction is None: return cBoard

    breakRange = checkBreak(cBoard, inputSquare, data, direction, contr)
    if breakRange == 0: return cBoard

    # the side holding the square owns the standing stack; the prisoners are the other's.
    control = s[Hasher.SIDE]

    # queue the pieces in the order they fall
    queue = []
    for n in range(0, s[Hasher.CAPSPY]): queue.append([int(not control), Hasher.SPY])
    for n in range(0, s[Hasher.CAPPAWNS]): queue.append([int(not control), Hasher.PAWNS])
    if s[Hasher.SPY]: queue.append([control, Hasher.SPY])
    for n in range(0, s[Hasher.PAWNS]): queue.append([control, Hasher.PAWNS])
    if s[Hasher.ROYAL]: queue.append([control, Hasher.ROYAL])

    # clear the square, then let them fall back across it and onward
    cBoard = Hasher.Mod_Space(cBoard, inputSquare, 0)

    scatter = BREAKRAY[inputSquare][direction]

    for i in range(0, breakRange):
        if not queue: break
        # the ray is 0-based, dropPiece numbers squares from 1
        targ = scatter[i] + 1

        if i + 1 == breakRange:
            # the last square catches everything still falling
            while queue:
                drop = queue.pop(0)
                cBoard = dropPiece(cBoard, targ, drop[0], drop[1])
        else:
            drop = queue.pop(0)
            cBoard = dropPiece(cBoard, targ, drop[0], drop[1])

    return cBoard


# Performs a push: the stack on origin shoves the line of squares ahead of it one step,
# then steps onto the vacated square.
#   origin, destination -- 1-based square numbers, destination adjacent to origin
#   movingPris          -- whether the prisoners on the origin come along
# Returns the new board.
#   shatter             -- whether a lone spy's shove goes on to scatter what it displaced.
#                          Always true in play; moveFlights turns it off to get at the board
#                          in between the shuffle and that scatter, which is the only place
#                          the scatter can be measured from. Defaulted, so every caller that
#                          existed before it did behaves exactly as it did.
def exePush(cBoard, origin, destination, contr, movingPris, freePris = False, shatter = True):
    direction = PUSHFROM[origin][destination]
    # not neighbours, so there is no push to make
    if direction is None: return cBoard

    inputData = cBoard[origin - 1]
    pRange = getLegalPushLength(cBoard, origin, inputData, direction, contr, movingPris, False,
                                None, freePris)
    if pRange == 0: return cBoard

    o = Hasher.UNPACK[inputData]

    # the line this push runs along, offset 0 being the origin itself -- so `line[n]` is
    # what pushSquare(origin, direction, n) used to work out, 0-based
    line = (origin - 1,) + PUSHRAY[origin][direction]

    adj = Hasher.UNPACK[cBoard[line[1]]]

    # Shoving a square that holds pieces of ours captive frees them: the jailors get shoved
    # on alone and our own pieces stand back up where they were being held, joining the stack
    # that just walked in. Both branches are legal moves, so which one this is comes from the
    # caller -- and it has to agree exactly with what getLegalPushLength was asked, since
    # that is what granted the push its range in the first place. wouldFree is what keeps a
    # dragon out of here: Build_Space drops every other field on a dragon square, so a dragon
    # taking this branch wrote the pieces it had just freed into itself and lost them.
    freeing = freePris and wouldFree(o, adj, contr, movingPris)

    # grab every square the shuffle needs before touching anything -- Mod_Space returns a
    # new board on each write, so these have to come off the original.
    payloads = {}
    for n in range(0, pRange + 2):
        payloads[n] = cBoard[line[n]]

    # Work out every square's new contents before committing any of them. Walking from the
    # far end inwards means each square is written after the one beyond it has already
    # taken its copy, so the later assignment to a given offset is the one that sticks.
    newPayloads = {}
    for k in range(0, pRange):
        d = pRange - k

        if freeing and d == 1:
            # offset 2 is free by now: either the cascade above just emptied it, or the
            # push stopped there because it was empty to begin with.
            newPayloads[2] = Hasher.Build_Space(adj[Hasher.SIDE], adj[Hasher.DRAGON],
                                                adj[Hasher.SPY], adj[Hasher.PAWNS], adj[Hasher.ROYAL])
            newPayloads[1] = Hasher.Build_Space(contr, 0, adj[Hasher.CAPSPY], adj[Hasher.CAPPAWNS], 0)
        else:
            newPayloads[d + 1] = payloads[d]
            newPayloads[d] = 0

    # and now the pushing stack itself steps forward.
    if movingPris:
        # the whole square travels, prisoners included
        newPayloads[1] = payloads[0]
        newPayloads[0] = 0
    else:
        # freed pieces may already be standing on the destination, so merge rather than set
        newSpy = o[Hasher.SPY]
        newPawns = o[Hasher.PAWNS]
        if freeing:
            newSpy += adj[Hasher.CAPSPY]
            newPawns += adj[Hasher.CAPPAWNS]
        newPayloads[1] = Hasher.Build_Space(contr, o[Hasher.DRAGON], newSpy, newPawns, o[Hasher.ROYAL])
        # prisoners left behind go free, standing back up as a stack of their own side
        newPayloads[0] = Hasher.Build_Space(int(not contr), 0, o[Hasher.CAPSPY], o[Hasher.CAPPAWNS], 0)

    # One copy of the board for the whole shuffle rather than one per square: Mod_Space
    # rebuilds all 49 every time it is called, and a push writes up to eight of them.
    # Still in ascending offset order, because a push long enough to lap the board has two
    # offsets landing on one square and the later of them is the one that stands.
    cells = list(cBoard)
    for n in sorted(newPayloads):
        cells[line[n]] = newPayloads[n]
    cBoard = tuple(cells)

    # A lone spy's shove shatters what it hits. getLegalPushLength only ever grants a lone
    # spy a range of 1 -- it spends its single point of strength on the adjacent square and
    # has nothing left for a second -- so the stack it displaced is now sitting two squares
    # out, and it scatters onward from there.
    if shatter and o[Hasher.SPY] and o[Hasher.CAPTORS] == 1:
        cBoard = exeBreak(cBoard, line[2] + 1, direction, contr)

    return cBoard


####### What a move moves #######
# Which pieces a move picks up and where it puts them down, for a front end that wants to
# draw what just happened rather than print it. Nothing in here writes a board back, and
# nothing in the search calls it.
#
# It lives beside the executors and not in notation.py because it is ray-table work --
# PUSHRAY, BREAKRAY, JUMPRAY, checkBreak, getLegalPushLength -- and it is the same walk
# exePush and exeBreak make. Kept next to them, the two can be read against each other;
# kept anywhere else, they drift and the arrows quietly start lying.


def moveFlights(cBoard, move, contr):
    """The journeys a move makes, as a tuple of

        (fromSquare, toSquare, dx, dy, steps)

    with both squares 1-BASED, (dx, dy) the unit step of the trip and steps >= 1 how many of
    them it took.

    `cBoard` MUST be the board as it stood BEFORE the move. checkBreak and
    getLegalPushLength both measure what is standing on the origin, and a move measured on
    the board it produced answers a different question.

    The step and the count are handed over rather than left to be worked out from the two
    squares, because on a wrapping board the two do not determine each other: a1 to g1 is
    one step left or six steps right, and only the walk that actually happened knows which.

    A flight is a piece changing square, and nothing else is one. Captures, stacks merging,
    prisoners freed and prisoners left standing where they were being held are all changes
    of state on a square that had one already -- no journey is made, so none is reported.
    """
    origin, kind, target, movingPris = move

    if kind == "jump":
        # The move tuple says where the stack landed, not how it got there, and on a torus
        # that is genuinely ambiguous: on the long diagonals a strand and the one opposite
        # it both arrive, their step counts summing to 7. The short way round is the one a
        # player watched, so it is the one drawn.
        best = None
        for d, strand in enumerate(JUMPRAY[origin]):
            if target in strand:
                steps = strand.index(target) + 1
                if best is None or steps < best[0]: best = (steps, d)
        if best is None: return ()
        steps, d = best
        return ((origin, target + 1, jumpDirs[d][0], jumpDirs[d][1], steps),)

    if kind in ("push", "free"):
        destination = target + 1
        direction = PUSHFROM[origin][destination]
        if direction is None: return ()

        inputData = cBoard[origin - 1]
        pRange = getLegalPushLength(cBoard, origin, inputData, direction, contr, movingPris,
                                    False, None, kind == "free")
        if pRange == 0: return ()

        # The whole line steps up one, offset n to offset n+1, for every n from the origin
        # out to the far end -- which is the half of a push that never showed on the board
        # before, since only the origin and the square next to it were ever marked. The
        # freeing branch travels the same distances; all it changes is which pieces are in
        # which payload when they arrive.
        line = (origin - 1,) + PUSHRAY[origin][direction]
        dx, dy = pushDirs[direction]
        flights = [(line[n] + 1, line[n + 1] + 1, dx, dy, 1) for n in range(0, pRange + 1)]

        # A lone spy's shove shatters what it hits, and that scatter cannot be read off the
        # board handed in: its ray wraps back over the squares the shuffle has just moved.
        # So run the push without it and ask the board in between.
        o = Hasher.UNPACK[inputData]
        if o[Hasher.SPY] and o[Hasher.CAPTORS] == 1:
            mid = exePush(cBoard, origin, destination, contr, movingPris,
                          kind == "free", False)
            flights.extend(moveFlights(mid, (line[2] + 1, "break", direction, False), contr))

        return tuple(flights)

    if kind == "break":
        direction = pushIndex(target)
        if direction is None: return ()
        inputData = cBoard[origin - 1]
        # exeBreak's own two refusals, so this answers () exactly where it would do nothing
        if Hasher.UNPACK[inputData][Hasher.DRAGON]: return ()
        reach = checkBreak(cBoard, origin, inputData, direction, contr)
        if reach == 0: return ()

        # The ray includes its own origin at offset 0, and the piece that falls back onto
        # the square it started on has gone nowhere -- so the journeys start at offset 1.
        # The last square catches everything still falling, which is a count and not a
        # further trip, so it needs no flight of its own beyond the one that reaches it.
        ray = BREAKRAY[origin][direction]
        dx, dy = pushDirs[direction]
        return tuple((origin, ray[i] + 1, dx, dy, i) for i in range(1, reach))

    return ()





def checkMoves(cBoard, tOrigin, contr, spaces = None):
    # One walk of the board covers the whole call -- nothing here changes it. Callers that
    # already hold a parsed board (the AI does, per node) hand it in rather than re-walking.
    if spaces is None: spaces = Hasher.Parse_Board(cBoard)

    # breaks up the Origin object. It carries its own square, so there is no pointer to
    # resolve back into one.
    movingPris = bool(tOrigin[ORIGIN_PRIS])
    spyCap = bool(tOrigin[ORIGIN_SPYBREAK])
    cacheSpace = tOrigin[ORIGIN_SQUARE]
    cacheCode = tOrigin[ORIGIN_CODE]
    cacheS = Hasher.UNPACK[cacheCode]
    if movingPris: pieceWeight = cacheS[Hasher.STRENGTH]
    else: pieceWeight = cacheS[Hasher.CAPTORS]
    #print(str(cacheSpace))
    moveX = 0
    moveY = 0
    global jumpDirs


    # Is there a dragon? cause then we don't look any further.
    dragonBool = False
    if cacheS[Hasher.DRAGON]: dragonBool = True

    # if there's a spy in the stack that is moving, set spyBool to True for capturing purposes.
    spyBool = False
    if not dragonBool:
        if cacheS[Hasher.SPY]: spyBool = True


    ###### DIAGONAL JUMP SUITE ######
    # WHAT A PAIN. I think I've gotten corners, edges, and royals/dragons done.
    # And weight, captures, not capturing a stack that is capturing your pieces,
    # not allowing spy to capture anything.
    # Option to subtract captured pieces from current weight AND allow you to bring them along.
    possJumps = []
    possPushes = []
    possBreaks = []
    # freeing pushes, kept apart from possPushes because they are a different move onto the
    # same square: the destination alone no longer says what the push does.
    possFrees = []

    # A spy still being held prisoner has exactly one move in it: breaking out. It doesn't
    # jump and it doesn't push, so both of those are skipped for it -- the stack it is
    # breaking belongs to its captor, not to it.
    # A stack carrying more prisoners than it has pieces weighs less than nothing, and there
    # is no strand short enough for that; the old loop got there by way of an empty range().
    jumpRays = ()
    if not spyCap and pieceWeight > 0: jumpRays = JUMPRAY[cacheSpace]

    #goes through the various jump directions.
    for strand in jumpRays:
        ###down-right diagonal first...
        # The strand is drawn at import: as many squares as the stack can travel, already
        # wrapped where this origin is entitled to wrap and already stopped where it isn't.
        # Weight decides how much of it is walked and nothing else, so it is a slice.
        for square in strand[:pieceWeight]:
            check = spaces[square]


            checkWeight = check[Hasher.WEIGHT]

            # the wee movingPrisoners suite of breakages, and it has to come FIRST.
            # If there are ANY pieces at all, either side's, BREAK IT. (check is the whole
            # square's fields, so there is no per-side index here -- the square's weight
            # already covers both.) A stack with prisoners in tow cannot change size, so
            # there is no landing on anyone, and the square stops the strand besides.
            #
            # This used to sit below the friendly branch, which `continue`s -- so it never
            # saw a square we control, and a carrying stack was offered a merge onto its own
            # pieces. "Either side's" was always the intent; the short-circuit shadowed it.
            # Nothing is lost by moving it up here: a dragon square can never hold prisoners
            # (Build_Space drops every other field on one), so movingPris and dragonBool are
            # mutually exclusive, and every gate it now precedes breaks on these squares too.
            # The way to land on an ally is to leave the prisoners behind -- listAllMoves
            # always offers that variant alongside this one.
            if movingPris and checkWeight != 0: break

            # Your own ground is open to you: any square your side holds is a legal landing,
            # whatever is standing there and whatever it weighs. Without this a stack can't
            # join a heavier friendly one, and since winning means gathering the spy, all
            # four pawns and the royal onto a single square, they could never all arrive.
            # Four pieces sit outside it, and they hold on either side of the board:
            #   a spy jumps onto nothing,
            #   a royal is jumped onto by nothing,
            #   a dragon does neither -- its payload ends after three bits with no room for
            #   company, so a merge wouldn't stack it, it would erase it,
            #   a stack carrying prisoners lands on nothing at all -- handled above.
            # Whatever this rules out falls through to the checks below, which stop it.
            # Assembly still works: the pawns gather on the spy, then the royal comes last.
            friendly = (check[Hasher.OCCUPIED] and check[Hasher.SIDE] == contr
                        and not check[Hasher.DRAGON] and not check[Hasher.ROYAL])

            if friendly and not dragonBool and not spyBool:
                possJumps.append(square)
                continue

            # if there are ANY pieces present in the new space AND a spy is being moved, break
            if spyBool == True and checkWeight != 0: break
            # if there are ANY pieces present in the new space AND a dragon is being moved, continue BECAUSE FLIGHT
            if dragonBool == True and checkWeight != 0: continue

            # if there are allies captured on the target square, break
            if check[Hasher.PRISFLAG]:
                if dragonBool:
                    continue
                else:
                    break


            # if there are any royals or dragons on the square, break
            if check[Hasher.ROYAL] or check[Hasher.DRAGON]:
                if dragonBool:
                    continue
                else:
                    break


            # if the weight of any defenders is greater than any attackers, break.
            if checkWeight > pieceWeight:
                break


            # AFTER MOVING PAST ALL OF THAT, add that shit to the list!

            possJumps.append(square)
            #for jump in possJumps:
            #    print(Hasher.IndexToAlg(jump), end=" ")
            #print("")

    '''
    if gameMode == 0 or (gameMode in [2, 3, 4] and isSlow):
        # prints the jump options out one by one.
        if len(possJumps):
            print("Jumps")
            for el in possJumps:
                if len(el) == 2: print(" " + coordToAlg(el))
    '''




    ###### ORTHO PUSH SUITE and BREAK SUITE ######
    # One range check per orthogonal direction, and getLegalPushLength's answer is the whole
    # of it: a non-zero length means the push is on offer, and which square it lands on is
    # the first entry of the ray it walked.
    # Breaks are a spy's trick -- a stack holding one can scatter. That counts a spy held
    # prisoner just as much as one standing: breaking is how it gets out, and the stack that
    # scatters is its captor's.
    canBreak = bool(cacheS[Hasher.SPY] or spyCap)
    pushRays = PUSHRAY[cacheSpace]

    for direction in range(0, len(pushDirs)):
        # a held spy doesn't push either -- see the jump loop above
        if not spyCap:
            ray = pushRays[direction]

            # An empty ray is a first step off the edge. Neither branch below can come to
            # anything then -- freeing is still a push, and it needs a square to land on --
            # so the whole check is skipped rather than measured twice to reach nothing.
            if ray:
                # the first square of the ray is the one getLegalPushLength checks first, so
                # the recorded destination is the square that was actually tested -- wrapped,
                # and 0-based to match possJumps.
                target = ray[0]

                rangeCheck = getLegalPushLength(cBoard, cacheSpace, cacheCode, direction, contr, movingPris, spyCap, spaces)
                if rangeCheck != 0:
                    possPushes.append(target)

                # The other branch. Only worth measuring where there is something to free,
                # which is why this asks wouldFree before paying for a second walk of the
                # line -- most pushes on most boards are into a square holding nobody.
                if wouldFree(cacheS, spaces[target], contr, movingPris):
                    freeCheck = getLegalPushLength(cBoard, cacheSpace, cacheCode, direction, contr,
                                                   movingPris, spyCap, spaces, True)
                    if freeCheck != 0:
                        possFrees.append(target)

        if canBreak:
            breakCheck = checkBreak(cBoard, cacheSpace, cacheCode, direction, contr, spaces)
            if breakCheck != 0:
                possBreaks.append(direction)

    '''
    if gameMode == 0 or (gameMode in [2, 3, 4] and isSlow):
        if len(possPushes) != 0:
            print("Pushes")
            for el in possPushes:
                if len(el) == 2: print(" " + coordToAlg(el))
    '''

    # possFrees counts towards being able to move at all. A stack whose only option is to
    # free somebody is not stuck, and leaving it out here reported it as such.
    possMoves = possJumps + possPushes + possFrees


    ##### Break zone. ######
    # possBreaks holds direction indices; HEADINGS spells them out. See it for why "down"
    # is the way it is.
    alphBreaks = []
    if len(possBreaks) != 0:
        for el in possBreaks:
            alphBreaks.append(HEADINGS[el])

        '''
        if gameMode == 0 or (gameMode in [2, 3, 4] and isSlow):
            print("Breaks:")
            for el in alphBreaks:
                print(" " + str(el))
        '''

        possMoves.append("break")

    ###### Are there any possible moves? ######
    if len(possMoves) == 0:
        return []
    else:
        return [possJumps, possPushes, possBreaks, possMoves, alphBreaks, possFrees]
