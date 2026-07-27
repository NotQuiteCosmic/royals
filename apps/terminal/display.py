# Terminal rendering for Royals.
#
# These three functions used to live in Hasher.py, next to the board representation they
# print. They were moved here unchanged when the engine became an importable package,
# because the engine is now imported by a web worker as well as by a terminal, and a
# module that prints ANSI escapes to stdout has no business inside it. Nothing about what
# they draw has changed -- only where they live.
#
# tests/test_engine_purity.py is what keeps them from drifting back.

from royals_engine.hasher import (
    Parse_Space, IndexToAlg,
    SIDE, DRAGON, SPY, PAWNS, ROYAL, CAPSPY, CAPPAWNS, PRISFLAG,
)

from royals_lib import bcolors


# Each rank prints twice: once showing what blue has standing there, once what red has.
# The two passes used to be separate blocks indexing the same bit offsets against different
# constants, which is how they drifted apart. They are one block now, because the question
# is the same either way -- a side sees its own stack where it holds the square, and its
# people in captivity where the other side does.
def displaySquare(code, who, spaceColor, sideColor):
    # a wholly empty square, which is the only thing a code of 0 can be
    if not code:
        print(spaceColor + ". . ." + bcolors.CEND, end=' ')
        return

    s = Parse_Space(code)

    if s[SIDE] == who:
        # our own stack
        if s[DRAGON]:
            print(sideColor + "DRAGN" + bcolors.CEND, end=' ')
            return

        spy, pawns, royal = s[SPY], s[PAWNS], s[ROYAL]
        tail = None
    elif s[PRISFLAG]:
        # the other side holds this square, and our people in it
        spy, pawns, royal = s[CAPSPY], s[CAPPAWNS], 0
        tail = ".x"
    else:
        # theirs, with nothing of ours in it
        print(spaceColor + ". . ." + bcolors.CEND, end=' ')
        return

    if spy: print(sideColor + "S" + bcolors.CEND, end=' ')
    else: print(spaceColor + "." + bcolors.CEND, end=' ')

    if pawns: print(sideColor + str(pawns) + bcolors.CEND, end=' ')
    else: print(spaceColor + "." + bcolors.CEND, end=' ')

    # prisoners can't include a royal, so that column is the marker instead
    if tail is not None: print(spaceColor + tail + bcolors.CEND, end='')
    elif royal: print(sideColor + "R" + bcolors.CEND, end=' ')
    else: print(spaceColor + "." + bcolors.CEND, end=' ')


def DisplayHashBoard(hashBoard):
    print("     A       B       C       D       E       F       G   \n")
    blue = True
    spaceNum = 1
    rowC = 0

    while spaceNum < 50:
        # hopefully loops the little rows. DOESNT
        if spaceNum % 7 == 1:
            if blue: print(spaceNum // 7 + 1, end=' ')
            else: print(' ', end=' ')


        print(" ", end=' ')

        # sets square color
        if spaceNum % 2 != 0:
            spaceColor = bcolors.CGREY
        else:
            spaceColor = bcolors.CWHITE

        # the space in question
        if blue: displaySquare(hashBoard[spaceNum - 1], 0, spaceColor, bcolors.CBLUE)
        else: displaySquare(hashBoard[spaceNum - 1], 1, spaceColor, bcolors.CRED)

        spaceNum += 1
        rowC += 1
        if rowC == 7:
            print("\n", end='')
            if blue: spaceNum -= 7
            if not blue: print("\n", end='')
            blue = not blue
            rowC = 0

    pass


# Prints the legal moves out of a chosen space.
#   moveArray    -- exactly what Engine.checkMoves hands back:
#                   [possJumps, possPushes, possBreaks, possMoves, alphBreaks],
#                   or [] when nothing is legal. Jumps and pushes are 0-based square
#                   indices; breaks are direction words, already spelled out in alphBreaks.
#   originSquare -- 1-based, the way Get_Space_Data and AlgebraToSquare number things,
#                   hence the -1 before handing it to IndexToAlg. 0 means "don't name it".
def DisplayMoves(moveArray, originSquare = 0, contr = False):
    if contr: sideColor = bcolors.CRED
    else: sideColor = bcolors.CBLUE

    header = "LEGAL MOVES"
    if originSquare: header += " FROM " + IndexToAlg(originSquare - 1).upper()
    print(sideColor + header + bcolors.CEND)

    # checkMoves returns a bare [] when the piece is stuck.
    if not moveArray:
        print("  " + bcolors.CGREY + "none -- this piece has nowhere to go." + bcolors.CEND)
        print("~~~~~~")
        return

    possJumps = moveArray[0]
    possPushes = moveArray[1]
    alphBreaks = moveArray[4]
    possFrees = moveArray[5]

    def squareList(squares):
        if not squares: return bcolors.CGREY + "NONE" + bcolors.CEND
        return ", ".join(IndexToAlg(s).upper() for s in sorted(squares))

    print("  Jumps:  " + squareList(possJumps))
    print("  Pushes: " + squareList(possPushes))
    # listed on their own line because a square can be under both: pushing the whole
    # square along and freeing the allies held on it are two moves, not one.
    print("  Frees:  " + squareList(possFrees))

    if alphBreaks: print("  Breaks: " + ", ".join(alphBreaks))
    else: print("  Breaks: " + bcolors.CGREY + "NONE" + bcolors.CEND)

    print("~~~~~~")
