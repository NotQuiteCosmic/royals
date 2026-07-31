"""What a stack carrying prisoners may jump onto -- which is nothing at all.

The golden file cannot speak to this, and not merely by accident of sampling. `+pris` occurs
**zero** times in all 13,051 lines of `golden_moves.txt`, because the move sweep's three
seeded walks never once reach a position where the side to move holds prisoners. The
carrying half of move generation has no coverage in the rules contract whatsoever -- not the
Python engine's, not the wheel's, not the browser's. So the bug this file was written for
sat in both engines at once, agreeing with itself, and the contract stayed byte-identical
through the fix.

The rule, from docs/RULES.md:

    a stack carrying prisoners may not land on ANY occupied square, either side's -- and
    the square stops its strand besides. Leave the prisoners behind and the landing is
    legal again.

The half that is easy to state backwards: a carrying stack cannot change size, but that is
a restriction on what it may *do*, not on what may be done to it. An allied stack jumping
onto a carrying one is legal, and the two merge with the prisoners still held. So size
changes are available from one side of the move only, which is the shape worth testing.

The way this failed is worth recording, because it is the way a port will fail again.
`checkMoves` short-circuits on friendly squares -- appends the landing and `continue`s --
and the carrying gate sat *below* that branch. Everything after the short-circuit therefore
only ever saw squares the mover does not control, so the gate read "no occupied enemy
square" however plainly its comment said "either side's". Ordering, not logic. The Rust port
copied the ordering faithfully and inherited the bug, so the two engines agreed and no
differential check could see it. Both gates now sit above the short-circuit; see
test_the_ray_is_blocked_not_merely_the_landing for the half that ordering decides.
"""

import random

import pytest

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher


# Boards here are built from nothing rather than from Entering_Board, so that what is on the
# board is exactly what the test put there -- the same reason test_break_rules.py keeps its
# own. Nothing under test counts armies, so a three-square board is a legitimate question.
def board_of(**squares):
    """A board from `alg=(side, dragon, spy, pawns, royal, capSpy, capPawns, prisFlag)`."""
    board = list(Hasher.EMPTY_BOARD)
    for alg, spec in squares.items():
        board[Hasher.AlgebraToSquare(alg) - 1] = Hasher.Build_Space(*spec)
    return tuple(board)


def occupied(board):
    """`{alg: (side, weight)}` for every square holding anything -- boards, readably."""
    out = {}
    for index in range(0, 49):
        if board[index]:
            s = Hasher.UNPACK[board[index]]
            out[Hasher.IndexToAlg(index)] = (s[Hasher.SIDE], s[Hasher.WEIGHT])
    return out


# Everything goes through listAllMoves rather than checkMoves directly, because that is the
# call the accelerator replaces. Asking the engine's own function would test the Python and
# quietly skip the wheel on a machine that has one -- and the wheel is where half of this
# rule now lives. CI runs this file twice, once with ROYALS_NO_ACCEL=1.
def jumps(board, alg, contr=0, pris=False):
    square = Hasher.AlgebraToSquare(alg)
    return set(Hasher.IndexToAlg(target)
               for (origin, kind, target, carrying) in AI.listAllMoves(board, contr)
               if origin == square and kind == "jump" and carrying == pris)


def along(board, alg, strand, contr=0, pris=False):
    """Which of `strand` this origin may jump to, in strand order -- one diagonal, readably.

    d4 has four strands and a three-square reach, so the full jump set is a dozen squares
    with the answer buried in it. Every test here puts its candidate on one strand and asks
    about that strand alone.
    """
    got = jumps(board, alg, contr, pris)
    return [square for square in strand if square in got]


# The up-right diagonal out of d4. Six squares before it laps the board; nothing here has
# the weight to travel that far.
UPRIGHT = ("e5", "f6", "g7")

# blue, three pawns, holding one red pawn captive.
#   captors 3  -- what it weighs when it leaves the prisoner behind, so a reach of 3
#   strength 2 -- what it weighs when it drags him along, so a reach of 2
# The two reaches differing is what makes "carrying" visible in the answer even before the
# landing rule bites: g7 is off the end of the carrying strand no matter what is on e5.
CARRIER = (0, 0, 0, 3, 0, 0, 1, 1)


# ---------------------------------------------------------------------------
# The landing table
# ---------------------------------------------------------------------------

# The candidate sits on e5, one step out. Carrying, the strand is e5 and f6; a block at e5
# shows up as [] and a pass as ["e5", "f6"]. Not carrying, the strand is e5, f6 and g7.
@pytest.mark.parametrize("what, on_e5, carrying, unencumbered", [
    ("an empty lane",                  None,                       ["e5", "f6"], ["e5", "f6", "g7"]),
    ("one of ours",                    (0, 0, 0, 1, 0),            [],           ["e5", "f6", "g7"]),
    ("a stack of ours",                (0, 0, 0, 3, 0),            [],           ["e5", "f6", "g7"]),
    ("a heavier stack of ours",        (0, 0, 0, 4, 0),            [],           ["e5", "f6", "g7"]),
    ("one of ours holding a prisoner", (0, 0, 0, 1, 0, 0, 1, 1),   [],           ["e5", "f6", "g7"]),
    ("a lone enemy",                   (1, 0, 0, 1, 0),            [],           ["e5", "f6", "g7"]),
    ("an enemy stack we outweigh",     (1, 0, 0, 2, 0),            [],           ["e5", "f6", "g7"]),
    ("an enemy stack we don't",        (1, 0, 0, 4, 0),            [],           []),
    ("our own royal",                  (0, 0, 0, 0, 1),            [],           []),
    ("our own dragon",                 (0, 1, 0, 0, 0),            [],           []),
    ("an enemy holding one of ours",   (1, 0, 0, 1, 0, 0, 1, 1),   [],           []),
])
def test_what_a_carrying_stack_may_land_on(what, on_e5, carrying, unencumbered):
    squares = {"d4": CARRIER}
    if on_e5 is not None: squares["e5"] = on_e5
    board = board_of(**squares)

    assert along(board, "d4", UPRIGHT, pris=True) == carrying, "carrying, onto " + what
    assert along(board, "d4", UPRIGHT, pris=False) == unencumbered, "unencumbered, onto " + what


# The first four rows above are the whole point of the file: every one of them is a square
# the mover controls, every one was a legal carrying landing before the fix, and the
# unencumbered column shows the move is not gone -- only the prisoners' passage on it.
def test_leaving_the_prisoners_behind_restores_the_landing():
    # our own spy on e5, not another pawn: this test runs the executor, so its board has to
    # be one a side could actually own. Three pawns onto two would be five, and a side has
    # four. (The table above never builds the merged square, which is why it can ask about
    # weights no army could field.)
    board = board_of(d4=CARRIER, e5=(0, 0, 1, 0, 0))

    assert "e5" not in jumps(board, "d4", pris=True)
    assert "e5" in jumps(board, "d4", pris=False)

    # and the move that comes back is a real merge, not a listing artefact
    after = AI.performOneStep(board, 0, (Hasher.AlgebraToSquare("d4"), "jump",
                                         Hasher.AlgebraToSquare("e5") - 1, False))
    assert occupied(after) == {
        "d4": (1, 1),        # the prisoner left standing, his own side again
        "e5": (0, 4),        # three pawns arrived on the spy already there
    }


# ---------------------------------------------------------------------------
# Blocked, not merely refused
# ---------------------------------------------------------------------------

# This is the half that the gate's *position* decides rather than its condition, so it is
# the half a port gets wrong. Putting the check inside the friendly branch would forbid the
# landing and still let the stack fly over; putting it above, where it now is, stops the
# strand dead. Enemy squares have always stopped a carrying stack, and there is no reading
# of the rule under which your own pieces are more transparent than theirs.
def test_the_ray_is_blocked_not_merely_the_landing():
    board = board_of(d4=CARRIER, e5=(0, 0, 0, 1, 0))

    assert along(board, "d4", UPRIGHT, pris=True) == []          # not e5, and not f6 beyond it
    assert along(board, "d4", UPRIGHT, pris=False) == ["e5", "f6", "g7"]

    # the other three strands are untouched -- one ally on e5 blocks one diagonal, not the stack
    assert along(board, "d4", ("c3", "b2"), pris=True) == ["c3", "b2"]


def test_an_empty_square_is_still_open_while_carrying():
    # the rule narrowed the move list; it did not empty it. A carrying stack that could go
    # nowhere at all would be a different bug wearing this one's clothes.
    board = board_of(d4=CARRIER)
    assert jumps(board, "d4", pris=True) == {"e5", "f6", "e3", "f2", "c5", "b6", "c3", "b2"}


# ---------------------------------------------------------------------------
# The other side of the move
# ---------------------------------------------------------------------------

# A carrying stack cannot change size. An ALLY may still change it for him, and that
# asymmetry is deliberate rather than an oversight in the gate: `friendly` is asked of the
# destination, so it is the mover's cargo that is restricted, never the destination's.
def test_an_ally_may_still_jump_onto_a_carrying_stack():
    # one blue pawn on e5 -- reach 1, so d4 is the whole of its down-left strand
    board = board_of(d4=CARRIER, e5=(0, 0, 0, 1, 0))
    assert "d4" in jumps(board, "e5", pris=False)

    after = AI.performOneStep(board, 0, (Hasher.AlgebraToSquare("e5"), "jump",
                                         Hasher.AlgebraToSquare("d4") - 1, False))
    d4 = Hasher.UNPACK[after[Hasher.AlgebraToSquare("d4") - 1]]
    assert d4[Hasher.PAWNS] == 4          # the four blue pawns, now on one square
    assert d4[Hasher.CAPPAWNS] == 1       # the red prisoner, still held
    assert d4[Hasher.PRISFLAG]
    assert occupied(after) == {"d4": (0, 5)}


# ---------------------------------------------------------------------------
# The three kinds that were never the problem
# ---------------------------------------------------------------------------

# Jump was the only hole, and these say so rather than assuming it. Each of the other three
# already refuses to change a carrying stack's size, for its own reason, and a fix to the
# jump gate is exactly the kind of edit that could disturb one of them.

def test_a_carrying_push_is_still_legal_and_changes_no_sizes():
    # a push lands on a square the shuffle has just vacated, so there is nobody to merge
    # with however heavy the cargo. e4 is orthogonally adjacent; the red pawn there is
    # shoved to f4 and the whole carrying square steps into the gap.
    board = board_of(d4=CARRIER, e4=(1, 0, 0, 1, 0))
    pushes = set(Hasher.IndexToAlg(target)
                 for (origin, kind, target, carrying) in AI.listAllMoves(board, 0)
                 if origin == Hasher.AlgebraToSquare("d4") and kind == "push" and carrying)
    assert "e4" in pushes

    after = AI.performOneStep(board, 0, (Hasher.AlgebraToSquare("d4"), "push",
                                         Hasher.AlgebraToSquare("e4") - 1, True))
    assert occupied(after) == {"e4": (0, 4), "f4": (1, 1)}      # the carrier, intact, one square on


def test_a_carrying_stack_frees_nobody():
    # e4 is a red pawn holding a blue pawn -- a freeing push for anyone entitled to one, and
    # canFreePrisoners refuses a stack that is already carrying its own.
    board = board_of(d4=CARRIER, e4=(1, 0, 0, 1, 0, 0, 1, 1))
    kinds = set(kind for (origin, kind, _target, carrying) in AI.listAllMoves(board, 0)
                if origin == Hasher.AlgebraToSquare("d4") and carrying)
    assert "free" not in kinds

    # and the same stack, unencumbered, is offered it -- so the absence above is the rule
    # and not an unreachable position
    kinds = set(kind for (origin, kind, _target, carrying) in AI.listAllMoves(board, 0)
                if origin == Hasher.AlgebraToSquare("d4") and not carrying)
    assert "free" in kinds


def test_a_carrying_stack_breaks_nothing():
    # a break scatters the whole square, so there is no carrying variant of one to generate
    board = board_of(d4=CARRIER)
    kinds = set(kind for (origin, kind, _target, carrying) in AI.listAllMoves(board, 0)
                if origin == Hasher.AlgebraToSquare("d4") and carrying)
    assert "break" not in kinds
    # nothing orthogonally adjacent to shove on this board, so jumping is the whole of it
    assert kinds == {"jump"}


# ---------------------------------------------------------------------------
# The rule, over positions nobody chose
# ---------------------------------------------------------------------------

# The hand-built boards above each ask one question, which is what makes them readable and
# also what makes them incomplete: they only test the squares somebody thought to put a
# piece on. This sweeps seeded random play instead and asserts the rule over every carrying
# move that turns up, which is how the bug was found in the first place.
#
# Twelve turns of random play is not enough -- prisoners are rare and a side that holds one
# usually drops it again before its next move. The sweep runs long games and varies the rng
# rather than the opening, because that is what reaches carrying positions at all: the same
# walk at 24 turns finds zero, and at 120 finds a few hundred.

def positions():
    """Seeded random play, long enough to reach positions where somebody holds a prisoner."""
    for seed in range(1, 9):
        AI.setEntryNoise(0.5, seed % 7 + 1)
        board = Hasher.Entering_Board()
        for contr, piece in Engine.enteringSequence():
            isSpy = (piece == Hasher.SPY)
            if not Engine.enteringOptions(board, contr, isSpy): continue
            board = Engine.dropPiece(board, AI.chooseEntry(board, contr, piece, isSpy),
                                     contr, piece)

        rng = random.Random(seed)
        for _turn in range(0, 120):
            yield board
            contr = _turn % 2
            moves = AI.listAllMoves(board, contr)
            if not moves: break
            board = AI.performOneStep(board, contr, moves[rng.randrange(len(moves))])


def test_no_carrying_jump_ever_targets_an_occupied_square():
    carrying = 0
    for board in positions():
        spaces = Hasher.Parse_Board(board)
        for contr in (0, 1):
            for (origin, kind, target, pris) in AI.listAllMoves(board, contr):
                if not pris: continue
                carrying += 1
                if kind != "jump": continue
                check = spaces[target]
                assert not check[Hasher.OCCUPIED], (
                    "%s jump %s +pris lands on %s, held by side %d"
                    % (Hasher.IndexToAlg(origin - 1), Hasher.IndexToAlg(target),
                       Hasher.IndexToAlg(target), check[Hasher.SIDE]))

    # A sweep that generated no carrying moves would pass this test having checked nothing,
    # and that is exactly how golden_moves.txt came to have no coverage of the rule. The
    # floor is well under the 269 these seeds actually find, so it guards the sweep going
    # empty without pinning a number that random play is entitled to move.
    assert carrying > 150, "the sweep found only %d carrying moves -- it has stopped reaching them" % carrying
