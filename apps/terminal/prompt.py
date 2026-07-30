# The terminal's "which square?" prompt.
#
# This was Engine.getOrigin. It lived in the engine and blocked on input(), which is fine
# for a terminal driver and impossible for anything else -- a window can never block, and
# a web worker has no stdin at all. The GUI already bypassed it entirely, building its
# origin object from a click instead (see apps/desktop/royals_gui.py, "Picks up a square,
# working out for itself what getOrigin would have asked about").
#
# Moved here verbatim when the engine became an importable package. tests/test_engine_purity.py
# is what stops input() reappearing inside royals_engine.

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine


#   takes:    cBoard (a board -- 49 square codes)
#             contr (bool, whose turn – right now, blue = False and red = True.).
#   returns:  an origin object, (movingPris, spyBreak, square, code)
def getOrigin(cBoard, contr):
    movingPris = False
    moveCheck = False
    spyBreak = False

    while not moveCheck:
        # RESETS the tSpaceIndex
        move = input("Space: ")
        tSpaceIndex = Hasher.AlgebraToSquare(move)

        #did they even put in a space
        if tSpaceIndex == 0: continue

        code = Hasher.Get_Space_Data(cBoard, tSpaceIndex)
        #at all occupied?
        if not code: continue

        s = Hasher.Parse_Space(code)

        #controlled by the correct side?
        if s[Hasher.SIDE] == contr :
            moveCheck = True
        # If controlled by other side: this player's spy? A captured spy is only ever
        # recorded on a square that has the prisoner flag, so asking for it covers the
        # length test the bit-string version needed before it could read that far.
        elif s[Hasher.CAPSPY]:
            moveCheck = True
            spyBreak = True
        else: continue

        if not moveCheck: continue
        else:
            # NOTE this reads as "a dragon can't be chosen", but moveCheck is already True
            # by now, so the continue leaves the loop rather than going round it and the
            # dragon is returned anyway. Left as it was: checkMoves has a full dragon case
            # (dragonBool, flight over occupied squares), so what comes back is playable,
            # and changing it here would quietly remove a move players have.
            if s[Hasher.DRAGON]: continue
            if s[Hasher.SIDE] == contr and s[Hasher.PRISFLAG]:
                prisCheck = False
                while prisCheck == False:
                    pIn = input("Bring prisoners? (y/n) ")
                    if pIn.lower() == "y" or pIn.lower() == "yes":
                        prisCheck = True
                        movingPris = True
                    elif pIn.lower() == "n" or pIn.lower() == "no":
                        prisCheck = True

    return Engine.makeOrigin(cBoard, tSpaceIndex, movingPris, spyBreak)
