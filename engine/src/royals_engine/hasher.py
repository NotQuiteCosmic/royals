# random/math/copy/builtins were imported here and never used; bcolors and the three
# terminal-printing functions that wanted it now live in apps/terminal/display.py. What
# is left is two helpers that outlived RoyalsLib -- see compat.py.
from royals_engine import compat as Lib

# A board is a tuple of 49 ints, one per square, indexed 0-based in reading order. Each int
# packs a square into 13 bits:
#
#   bit  0     occupied
#   bit  1     controlling side (0 blue, 1 red)
#   bit  2     dragon
#   bit  3     spy
#   bits 4-6   pawns, 0 to 4
#   bit  7     royal
#   bit  8     any prisoners
#   bit  9     captured spy
#   bits 10-12 captured pawns, 0 to 4
#
# The field order is the one the old variable-length bit string used, and Build_Space still
# zeroes everything past a dragon and everything past the prisoner flag -- so one arrangement
# of pieces still has exactly one encoding, which is what lets a board stand as its own name
# in the ko history and the transposition table.
#
# What changed is that the encoding is fixed-width and lives in a real array. The old one
# packed the squares end to end at 1, 3, 9 or 13 bits each, so finding square n meant
# measuring the n-1 squares in front of it: Get_Space_Data walked the whole board on every
# call and was the most-called function in the engine. Now it is a subscript. Nothing else
# needs a pointer either, which is why the pointer prefix and Find_Data_From_Index are gone.
#
# Boards are tuples rather than lists so they stay hashable and can't be edited from under
# a caller -- Mod_Space returns a new one, exactly as the splicing version did.

# Where each field starts, and how wide it is where that isn't one bit.
BIT_OCCUPIED = 1
BIT_SIDE = 2
BIT_DRAGON = 4
BIT_SPY = 8
SHIFT_PAWNS = 4
BIT_ROYAL = 128
BIT_PRIS = 256
BIT_CAPSPY = 512
SHIFT_CAPPAWNS = 10

# every code a square can hold, so tables over them can be built by index
CODE_RANGE = 1 << 13

EMPTY_BOARD = (0,) * 49

# UTILITIES
def AlgebraToSquare(algebra):
    # This gets handed whatever the player typed, so anything that isn't a letter followed
    # by a digit is simply not a square. Without this, "down" and "b" raised instead of
    # returning 0 -- int('o') and a one-character index respectively.
    if len(algebra) != 2 or not algebra[1].isdigit(): return 0

    if(Lib.alphToNum(algebra[0]) >= 0 and Lib.alphToNum(algebra[0]) < 7) :
        if (int(algebra[1]) > 0 and int(algebra[1]) < 8) :
            return (Lib.alphToNum(algebra[0]) + ((int(algebra[1]) - 1) * 7) + 1)
        else: return 0
    else: return 0

def IndexToAlg(index) :
	alph = "abcdefg"
	return(alph[int(index % 7)] + str(index//7 + 1))

# HASHING

# Field positions inside a space's payload, i.e. what sits after the pointer.
SIDE = 0; DRAGON = 1; SPY = 2; PAWNS = 3; ROYAL = 4; CAPSPY = 5; CAPPAWNS = 6; PRISFLAG = 7

# How many of those are fields proper. A parsed square carries derived scalars after
# them (see UNPACK), so anything wanting "the arrangement of pieces" and nothing else --
# printing a board, comparing two squares -- takes s[:FIELDS].
FIELDS = 8

# Unpacks a square code into plain numbers so move code doesn't have to touch bits at all:
#   (side, dragon, spy, pawns, royal, capSpy, capPawns, prisFlag)
# with every field 0 on an empty square. Inverse of Build_Space.
# prisFlag is carried separately because Encode_From_2D_Array can set it on a square that
# holds nobody -- a side marked captured with no pieces left -- and dropping it would both
# change what Check_For_Pris answers and break the Build_Space round trip.
def unpackCode(code):
    if not (code & BIT_OCCUPIED): return (0, 0, 0, 0, 0, 0, 0, 0)

    side = (code & BIT_SIDE) >> 1
    # a dragon carries nothing else
    if code & BIT_DRAGON: return (side, 1, 0, 0, 0, 0, 0, 0)

    spy = (code & BIT_SPY) >> 3
    pawns = (code >> SHIFT_PAWNS) & 7
    royal = (code & BIT_ROYAL) >> 7

    # no prisoners, so nothing past the flag is set
    if not (code & BIT_PRIS): return (side, 0, spy, pawns, royal, 0, 0, 0)

    return (side, 0, spy, pawns, royal,
            (code & BIT_CAPSPY) >> 9, (code >> SHIFT_CAPPAWNS) & 7, 1)


# The weights, read off an unpacked square. A dragon is worth 3 whichever way you count it.
# These live here rather than in Engine because the table below is built from them, and
# Engine imports this module -- so this is the one place both can reach. Engine re-exports
# them under the names it has always used.
def spaceWeight(s):
    # everything standing on the square, prisoners included
    if s[DRAGON]: return 3
    return s[SPY] + s[PAWNS] + s[ROYAL] + s[CAPSPY] + s[CAPPAWNS]

def spaceStrength(s):
    # carried prisoners count against the stack
    if s[DRAGON]: return 3
    return s[SPY] + s[PAWNS] + s[ROYAL] - s[CAPSPY] - s[CAPPAWNS]

def spaceCaptors(s):
    # just the pieces standing, ignoring anyone they are holding
    if s[DRAGON]: return 3
    return s[SPY] + s[PAWNS] + s[ROYAL]


# Positions of the derived scalars, which sit after the fields proper.
#
# These are not new facts -- every one of them is something the eight fields above already
# say, and each is computed here by the same function that used to be called to ask. What
# they buy is that asking stops being a function call. Move generation reads a square's
# weight, or whether it is occupied, several times per step of every strand it walks; in a
# depth-6 search that came to 1.28 million calls to Space_Occupied and 740,000 to
# spaceWeight, each one a call and a re-derivation to answer a question the code already
# knew at import. Precomputing them for all 8192 codes turns every one of those into a
# subscript on a tuple that had to be fetched anyway.
OCCUPIED = 8; WEIGHT = 9; STRENGTH = 10; CAPTORS = 11; PRISCOUNT = 12; MYPIECES = 13
WIDTH = 14


def unpackRow(code):
    s = unpackCode(code)
    return s + (
        bool(s[DRAGON] or s[SPY] or s[PAWNS] or s[ROYAL] or s[CAPSPY] or s[CAPPAWNS]),
        spaceWeight(s),
        spaceStrength(s),
        spaceCaptors(s),
        s[CAPSPY] + s[CAPPAWNS],
        # the dragon counts as one piece here, not three -- this is a head count, and it is
        # what the evaluator means by the size of a stack
        s[DRAGON] + s[SPY] + s[PAWNS] + s[ROYAL],
    )


# Every code unpacked once, at import. Parse_Space is then a subscript rather than a parse,
# which matters because move generation and the evaluator ask for whole boards at every node
# of the search. 8192 rows is small enough to build eagerly and covers every bit pattern,
# including ones no legal position produces -- a table with holes would only move the test
# somewhere hotter.
UNPACK = tuple(unpackRow(code) for code in range(CODE_RANGE))


def Parse_Space(tSpace):
    return UNPACK[tSpace]


# All 49 squares as Parse_Space field tuples, in board order.
# This used to be the way to avoid Get_Space_Data's 49 full re-walks of the board. Both are
# cheap now, but everything that sweeps the board still comes through here -- one lookup per
# square beats one per field read.
def Parse_Board(hBoard):
    return [UNPACK[code] for code in hBoard]


# Packs those same numbers back into a square code, ready for Mod_Space.
# Zeroes everything past a dragon, and everything past the prisoner flag when there are no
# prisoners, so one arrangement of pieces has exactly one code. The old variable-length
# version got that by emitting a shorter string; the reason is the same either way, and it
# is what lets a board stand as its own name in the ko history and the transposition table.
def Build_Space(side, dragon, spy, pawns, royal, capSpy = 0, capPawns = 0, prisFlag = 0):
    if not (dragon or spy or pawns or royal or capSpy or capPawns or prisFlag): return 0

    # A side has one spy and one royal, and four pawns, so nothing here should ever run over
    # its field. When the string version was handed a 2 it built a bit string with a '2' in
    # it and bitstring raised on the spot; shifting an oversized value would instead quietly
    # bleed it into the next field, and the board would go wrong somewhere else entirely.
    if spy > 1 or royal > 1 or pawns > 4 or capSpy > 1 or capPawns > 4:
        raise ValueError("square overfull: spy %s pawns %s royal %s capSpy %s capPawns %s"
                         % (spy, pawns, royal, capSpy, capPawns))

    # occupied, controlling side
    code = BIT_OCCUPIED
    if side: code |= BIT_SIDE

    if dragon: return code | BIT_DRAGON

    if spy: code |= BIT_SPY
    code |= pawns << SHIFT_PAWNS
    if royal: code |= BIT_ROYAL

    if not (capSpy or capPawns or prisFlag): return code

    code |= BIT_PRIS
    if capSpy: code |= BIT_CAPSPY
    return code | (capPawns << SHIFT_CAPPAWNS)


# Returns a board with one square replaced. tSpace is a 1-based square number -- it used to
# be the square's data, because the caller had no other way to say where in a packed bit
# string the square began.
def Mod_Space(hBoard, tSpace, result):
    cells = list(hBoard)
    cells[tSpace - 1] = result
    return tuple(cells)


# Builds a board from the backup's 2D array, where each cell is [blue, red] and each of
# those is [spy, pawns, royal, flag] -- flag 3 marking a dragon and flag -1 marking that
# side's entry as the prisoners rather than the stack.
def Encode_From_2D_Array(ArrayBoard):
    cells = []

    for x in ArrayBoard:
        for y in x:
            if Lib.countPieces(y[0]) == 0 and Lib.countPieces(y[1]) == 0:
                cells.append(0)
                continue

            # whoever's entry isn't the prisoners holds the square
            if Lib.countPieces(y[0]) != 0 and y[0][3] != -1: contr = 0
            else: contr = 1

            held = y[contr]
            other = y[1 - contr]
            prisoners = (other[3] == -1)

            cells.append(Build_Space(contr, held[3] == 3, held[0], held[1], held[2],
                                     other[0] if prisoners else 0,
                                     other[1] if prisoners else 0,
                                     prisoners))

    return tuple(cells)


# The code sitting on a 1-based square. This was the most-called function in the engine and
# the most expensive, because reaching square n meant measuring the n-1 squares in front of
# it in the packed bit string. Fixed-width squares in an array make it a subscript.
def Get_Space_Data(hBoard, tSpace):
    tSpace = int(tSpace)

    # squares are numbered from 1. Anything else is a caller bug, and it used to surface
    # as this walking off the end of the board -- keep it loud, just say why.
    if tSpace < 1 or tSpace > 49:
        raise IndexError("square " + str(tSpace) + " is off the board (squares run 1 to 49)")

    return hBoard[tSpace - 1]


# The board the Entering phase starts from: empty but for the two dragons facing each other
# down the d file. Blue holds d3 and red d5 -- the backup's placement masks block blue
# around d3 and red around d5, which is what settles which dragon is whose.
def Entering_Board():
    cells = []
    for space in range(0, 49):
        cells.append([[0, 0, 0, 0], [0, 0, 0, 0]])

    cells[AlgebraToSquare("d3") - 1][0][3] = 3
    cells[AlgebraToSquare("d5") - 1][1][3] = 3

    return Encode_From_2D_Array([[cells[r * 7 + c] for c in range(7)] for r in range(7)])


# Scans the whole board for a finished game. A side wins by gathering its spy, all four of
# its pawns and its royal onto a single square.
# Returns [gameEnd, winner] with winner indexed by side: winner[0] is blue, winner[1] red.
# (The backup's checkFinished crossed those two over, which is why its own callers disagree
# about which list means which colour.)
# Which codes are a won square, worked out once from the field rules rather than restated as
# bit patterns. Prisoners can't include a royal, so only a standing stack can ever be in here.
WIN_CODES = frozenset(code for code in range(CODE_RANGE)
                      if UNPACK[code][SPY] == 1
                      and UNPACK[code][PAWNS] == 4
                      and UNPACK[code][ROYAL] == 1)


def Check_For_Winner(hBoard):
    winner = [0, 0]
    # the search asks this at every node, so it reads codes straight off the board rather
    # than unpacking all 49 squares into fields to look at three of them
    for code in hBoard:
        if code in WIN_CODES: winner[(code & BIT_SIDE) >> 1] = 1
    return [winner != [0, 0], winner]


# The Check_For_ family, reading a Parse_Space field tuple rather than a raw square code.
# Move generation asks these thousands of times per search, so the hot paths work from
# already-parsed fields and the code-taking versions below just look the code up and hand
# over -- one table lookup per square rather than one per field read.
# Parse_Space zeroes every other field on a dragon, so no dragon test is needed here.
#
# These are the readable way to ask, and everything outside the search uses them. The hot
# loops in Engine and artificialPlayer subscript the same values directly -- s[OCCUPIED]
# for this one -- because at a million calls a search the call itself is the cost.
def Space_Occupied(s):
    return s[OCCUPIED]

def Space_Has_Dragon(s):
    return bool(s[DRAGON])

def Space_Has_Spy(s):
    return bool(s[SPY])

def Space_Has_Royal(s):
    return bool(s[ROYAL])

def Space_Has_Pris(s):
    return bool(s[PRISFLAG])


def Check_For_Occupancy(tSpace):
    return Space_Occupied(Parse_Space(tSpace))

def Check_For_Dragon(tSpace):
    return Space_Has_Dragon(Parse_Space(tSpace))

def Check_For_Spy(tSpace):
    return Space_Has_Spy(Parse_Space(tSpace))

def Check_For_Royal(tSpace):
    return Space_Has_Royal(Parse_Space(tSpace))

def Check_For_Pris(tSpace):
    return Space_Has_Pris(Parse_Space(tSpace))

