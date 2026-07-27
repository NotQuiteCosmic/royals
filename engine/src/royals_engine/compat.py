# The last two things the engine still needs from RoyalsLib.
#
# RoyalsLib.py is the original Python-2-era implementation: a whole second engine on a
# mutable nested list, kept alive only because a handful of small helpers were still
# imported out of it. Its game logic has been dead for years -- MainPlay hardcodes
# setting = 0, so the branch that used it never runs.
#
# Rather than have the engine package depend on 1,200 lines of dead code, the two
# functions that are genuinely still reachable were copied here verbatim. The original
# survives at apps/terminal/royals_lib.py for the terminal driver, and in git history.
#
# Verbatim matters: countPieces feeds Encode_From_2D_Array, which builds the starting
# position, so a "tidy-up" here would move golden.txt.


def alphToNum(letter):
    """File letter to 0-based column. -1 for anything that isn't a-g."""
    if   (letter.lower() == "a"): return 0
    elif (letter.lower() == "b"): return 1
    elif (letter.lower() == "c"): return 2
    elif (letter.lower() == "d"): return 3
    elif (letter.lower() == "e"): return 4
    elif (letter.lower() == "f"): return 5
    elif (letter.lower() == "g"): return 6
    else: return -1


def countPieces(spaceC):
    """How many pieces a square holds, in the legacy 2D-array representation.

    spaceC is one side's slot list: [spy, pawns, royal, dragon-marker]. The dragon is
    counted only when its marker is exactly 3, which is what the old encoding used.
    Reached from Hasher.Encode_From_2D_Array, and so from Entering_Board.
    """
    total = 0
    for i in range(0, 3):
        total += spaceC[i]
    if spaceC[3] == 3: total += 1
    return total
