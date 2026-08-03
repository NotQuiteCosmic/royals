"""What a break may and may not fall onto.

The golden file cannot speak to this. Measured across the whole `regress.py` move sweep --
three seeds, forty turns, 6,345 moves -- making the obstacle test side-aware changed not one
resulting board, because the positions random play reaches never happen to put a stack of
your own in the path of your own break. `golden_moves.txt` stayed byte-identical through the
change, which is worth knowing and is exactly why this file exists: the contract has zero
coverage of the rule, so these are the only tests holding it.

So this is hand-built positions, not a sweep. Each one is the smallest board that asks a
single question, and they are written to read like the rule they encode:

    obstacles are royals, dragons, an enemy square holding prisoners, and enemy squares
    heavier than one piece. Everything else the scatter falls straight through, joining
    whatever is standing there.

with "enemy" measured against the piece that is falling -- see the block comment above
test_the_prisoners_falling_out_first_are_the_other_side.
"""

import random

import pytest

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher
from royals_engine import notation as N


DOWN, UP, LEFT, RIGHT = 0, 1, 2, 3


# Boards here are built from nothing rather than from Entering_Board, so that what is on the
# board is exactly what the test put there. Nothing under test counts pieces -- checkBreak
# and exeBreak read squares, not armies -- so a two-square board is a legitimate question to
# ask them, and it is a far more legible one than a full army with the answer buried in it.
def board_of(**squares):
    """A board from `alg=(side, dragon, spy, pawns, royal, capSpy, capPawns, prisFlag)`."""
    board = list(Hasher.EMPTY_BOARD)
    for alg, spec in squares.items():
        board[Hasher.AlgebraToSquare(alg) - 1] = Hasher.Build_Space(*spec)
    return tuple(board)


def reach(board, alg, direction, contr=0):
    square = Hasher.AlgebraToSquare(alg)
    return Engine.checkBreak(board, square, board[square - 1], direction, contr)


def occupied(board):
    """`{alg: (side, weight)}` for every square holding anything -- boards, readably."""
    out = {}
    for index in range(0, 49):
        if board[index]:
            s = Hasher.UNPACK[board[index]]
            out[Hasher.IndexToAlg(index)] = (s[Hasher.SIDE], s[Hasher.WEIGHT])
    return out


# blue spy + 3 pawns: four pieces, so a clear lane reaches four squares and any obstacle
# short of that shortens it visibly.
BREAKER = (0, 0, 1, 3, 0)


# ---------------------------------------------------------------------------
# The obstacle table
# ---------------------------------------------------------------------------

# Breaking rightward out of b4 over c4, d4, e4. The candidate obstacle sits on d4, two
# squares along, so a block shows up as reach 2 and a pass as the full reach 4.
@pytest.mark.parametrize("what, on_d4, wanted", [
    ("an empty lane",                     None,                          4),
    ("one of ours",                       (0, 0, 0, 1, 0),               4),
    ("a stack of ours",                   (0, 0, 0, 2, 0),               4),
    ("a big stack of ours",               (0, 0, 0, 4, 0),               4),
    ("one of ours holding a prisoner",    (0, 0, 0, 1, 0, 0, 1, 1),      4),
    ("our own royal",                     (0, 0, 0, 0, 1),               2),
    ("our own dragon",                    (0, 1, 0, 0, 0),               2),
    ("a lone enemy",                      (1, 0, 0, 1, 0),               4),
    ("an enemy pair",                     (1, 0, 0, 2, 0),               2),
    ("the enemy royal",                   (1, 0, 0, 0, 1),               2),
    ("the enemy dragon",                  (1, 1, 0, 0, 0),               2),
    ("an enemy holding a prisoner",       (1, 0, 0, 1, 0, 0, 1, 1),      2),
])
def test_what_stops_a_break(what, on_d4, wanted):
    squares = {"b4": BREAKER}
    if on_d4 is not None: squares["d4"] = on_d4
    assert reach(board_of(**squares), "b4", RIGHT) == wanted, what


def test_a_lone_enemy_in_the_path_is_taken_prisoner():
    # the flip side of "a lone enemy is not an obstacle" -- it isn't merely stepped over
    board = board_of(b4=BREAKER, d4=(1, 0, 0, 1, 0))
    after = Engine.exeBreak(board, Hasher.AlgebraToSquare("b4"), RIGHT, 0)

    d4 = Hasher.UNPACK[after[Hasher.AlgebraToSquare("d4") - 1]]
    assert d4[Hasher.SIDE] == 0
    assert d4[Hasher.PAWNS] == 1          # the pawn that fell
    assert d4[Hasher.CAPPAWNS] == 1       # the enemy it landed on
    assert d4[Hasher.PRISFLAG]


def test_a_break_falls_through_our_own_stacks_and_joins_them():
    board = board_of(b4=BREAKER, d4=(0, 0, 0, 2, 0))
    after = Engine.exeBreak(board, Hasher.AlgebraToSquare("b4"), RIGHT, 0)

    # spy back on the origin, a pawn each on c4 and e4, and the third pawn merged into the
    # two already standing on d4
    assert occupied(after) == {"b4": (0, 1), "c4": (0, 1), "d4": (0, 3), "e4": (0, 1)}


# ---------------------------------------------------------------------------
# Where the pieces land
# ---------------------------------------------------------------------------

def test_the_remainder_piles_onto_the_last_unblocked_square():
    # six pieces, blocked at d4, so c4 has to catch everything still falling
    board = board_of(b4=(0, 0, 1, 4, 1), d4=(1, 0, 0, 2, 0))
    assert reach(board, "b4", RIGHT) == 2

    after = Engine.exeBreak(board, Hasher.AlgebraToSquare("b4"), RIGHT, 0)
    assert occupied(after) == {"b4": (0, 1), "c4": (0, 5), "d4": (1, 2)}


def test_a_break_wraps_around_the_edge():
    # six pieces from f4 rightward: g4, then over the edge to a4, b4, c4, d4
    board = board_of(f4=(0, 0, 1, 4, 1))
    assert reach(board, "f4", RIGHT) == 6

    after = Engine.exeBreak(board, Hasher.AlgebraToSquare("f4"), RIGHT, 0)
    assert sorted(occupied(after)) == ["a4", "b4", "c4", "d4", "f4", "g4"]
    assert all(w == 1 for _side, w in occupied(after).values())


def test_an_obstacle_beyond_the_edge_still_blocks():
    # the wrap is not a gap in the rules: a4 is two steps past f4 and stops it like any other
    board = board_of(f4=(0, 0, 1, 4, 1), a4=(1, 0, 0, 2, 0))
    assert reach(board, "f4", RIGHT) == 2


# ---------------------------------------------------------------------------
# Whose "enemy"
# ---------------------------------------------------------------------------

# The pieces a break drops are not all the breaker's. Prisoners fall first and stand back up
# as their own side, so for those early steps the obstacle test has to be asked of the piece
# in the air rather than of the player whose turn it is. Measuring it against the breaker
# instead would let a freed captive land on a stack of its captor's -- and dropPiece would
# then have that one freed piece take the whole stack prisoner.

def test_the_prisoners_falling_out_first_are_the_other_side():
    # blue spy + 1 pawn holding two red pawns, breaking right. The first piece to fall on c4
    # is a red captive, so blue's own pair standing there is an enemy stack to it, and stops
    # the break before it starts.
    board = board_of(b4=(0, 0, 1, 1, 0, 0, 2, 1), c4=(0, 0, 0, 2, 0))
    assert reach(board, "b4", RIGHT) == 0

    # and the mirror: a red pair on c4 is home for that same falling captive, so it passes
    board = board_of(b4=(0, 0, 1, 1, 0, 0, 2, 1), c4=(1, 0, 0, 2, 0))
    assert reach(board, "b4", RIGHT) == 4


def test_a_freed_captive_never_captures_on_its_way_past():
    # the consequence the test above is protecting: whatever a break does to blue's stack on
    # c4, it must not hand it to red
    board = board_of(b4=(0, 0, 1, 1, 0, 0, 2, 1), c4=(0, 0, 0, 2, 0))
    after = Engine.exeBreak(board, Hasher.AlgebraToSquare("b4"), RIGHT, 0)
    assert after == board          # no legal break, so no board change at all


# ---------------------------------------------------------------------------
# A break never sets anybody free except its own prisoners
# ---------------------------------------------------------------------------

# dropPiece's capture branch releases whoever the square it lands on was holding. That is
# right for a jump and wrong for a break, and the only thing keeping exeBreak out of it is
# checkBreak's refusal to fall on an enemy jailer. The origin is the exception and the whole
# point: breaking is how the prisoners in the breaking stack get out.

# Counting prisoners per square is not enough and the near-miss is worth recording: drop the
# PRISFLAG test and a break falls on a lone red jailer holding a blue pawn, frees that pawn
# and takes two red pieces captive in the same step. The square's prisoner count goes UP,
# from one to two, while the prisoner the rule is about walks away. So this asks whose.
def held_by_side(board, index, side):
    """How many of `side`'s pieces are being held captive on this square, as (spy, pawns)."""
    s = Hasher.UNPACK[board[index]]
    if not s[Hasher.PRISFLAG]: return (0, 0)
    if int(not s[Hasher.SIDE]) != side: return (0, 0)
    return (s[Hasher.CAPSPY], s[Hasher.CAPPAWNS])


def assert_no_bystander_freed(board, origin, after):
    for index in range(0, 49):
        if index == origin - 1: continue      # the origin's own captives are what a break frees
        for side in (0, 1):
            before = held_by_side(board, index, side)
            now = held_by_side(after, index, side)
            assert now >= before, (
                "a break out of %s let side %d out of %s"
                % (Hasher.IndexToAlg(origin - 1), side, Hasher.IndexToAlg(index)))


def test_a_lone_jailer_is_an_obstacle_on_the_second_lap_too():
    # Eleven pieces -- blue's whole army holding red's -- so the ray laps the seven-wide
    # board and the weight tolerance rises to 2 on the way round. A lone jailer weighs
    # exactly 2, so weight alone stops caring about it here and the prisoner test is the
    # only thing left. Without it the break falls on the jailer and frees its captive.
    origin = (0, 0, 1, 4, 1, 1, 4, 1)
    assert Hasher.UNPACK[Hasher.Build_Space(*origin)][Hasher.WEIGHT] == 11

    clear = board_of(a4=origin)
    assert reach(clear, "a4", RIGHT) == 11

    # b4 is offset 1 and again offset 8; a plain enemy pair would have stopped it at 1, so
    # this has to be the jailer, which is light enough to pass first time round
    blocked = board_of(a4=origin, b4=(1, 0, 0, 1, 0, 0, 1, 1))
    assert reach(blocked, "a4", RIGHT) == 8

    after = Engine.exeBreak(blocked, Hasher.AlgebraToSquare("a4"), RIGHT, 0)
    assert_no_bystander_freed(blocked, Hasher.AlgebraToSquare("a4"), after)


def positions():
    """The seeded random walk regress.py records, which is where messy boards come from."""
    boards = []
    for seed in range(1, 11):
        board = Hasher.Entering_Board()
        for index, (contr, piece) in enumerate(Engine.enteringSequence()):
            is_spy = (piece == Hasher.SPY)
            if not Engine.enteringOptions(board, contr, is_spy): continue
            square = Engine.randomEntry(board, contr, is_spy, Engine.entryRng(seed, index))
            board = Engine.dropPiece(board, square, contr, piece)

        rng = random.Random(seed)
        boards.append(board)
        # Forty turns, the length regress.py's sweep runs to, over ten seeds rather than the
        # three it uses. Three was enough while the opening came from the entering heuristic,
        # which packs pieces together; a random opening scatters them, and three seeds reach
        # only 29 breaks where ten reach 358. The guard at the bottom of the sweep tests
        # wants a margin over 100, and widening the sample is the way to get it back --
        # lowering the guard would keep the tests green by agreeing to test less.
        for turn in range(0, 40):
            moves = AI.listAllMoves(board, turn % 2)
            if not moves: break
            board = AI.performOneStep(board, turn % 2, moves[rng.randrange(len(moves))])
            boards.append(board)
    return boards


def test_no_break_anywhere_frees_a_bystander():
    checked = 0
    for board in positions():
        for contr in (0, 1):
            for move in AI.listAllMoves(board, contr):
                if move[1] != "break": continue
                origin = move[0]
                after = AI.performOneStep(board, contr, move)
                assert_no_bystander_freed(board, origin, after)
                checked += 1

    # a guard on the guard -- an assertion that never runs proves nothing
    assert checked > 100, "only %d breaks exercised" % checked


# ---------------------------------------------------------------------------
# Merging can't produce a square the encoding can't hold
# ---------------------------------------------------------------------------

def test_a_break_never_builds_an_impossible_square():
    # Falling onto your own stacks means squares now merge mid-scatter, which they never did
    # before. A side's own pieces cap at spy + 4 pawns + royal so this cannot overflow the
    # 3-bit pawn fields -- but that is an argument, and this is the check.
    for board in positions():
        for contr in (0, 1):
            for move in AI.listAllMoves(board, contr):
                if move[1] != "break": continue
                after = AI.performOneStep(board, contr, move)
                for code in after:
                    s = Hasher.UNPACK[code]
                    assert s[Hasher.SPY] <= 1 and s[Hasher.CAPSPY] <= 1
                    assert s[Hasher.PAWNS] <= 4 and s[Hasher.CAPPAWNS] <= 4
                    assert s[Hasher.ROYAL] <= 1
                    # and the packing is still canonical, which is what lets a board stand
                    # as its own name in the ko set and the transposition table
                    assert Hasher.Build_Space(s[Hasher.SIDE], s[Hasher.DRAGON], s[Hasher.SPY],
                                              s[Hasher.PAWNS], s[Hasher.ROYAL],
                                              s[Hasher.CAPSPY], s[Hasher.CAPPAWNS]) == code


# ---------------------------------------------------------------------------
# Rescue
# ---------------------------------------------------------------------------

# "free" has seven recorded instances in the 13,051-line golden file, and test_notation.py
# measured that random play across three seeds produces none at all. So the rule that a
# stack can walk in and stand its own people back up is very nearly untested by the
# contract. These pin the case worth pinning.

def test_two_pawns_free_a_spy_held_by_two_pawns():
    board = board_of(d4=(0, 0, 0, 2, 0), e4=(1, 0, 0, 2, 0, 1, 0, 1))
    origin = Hasher.AlgebraToSquare("d4")
    moves = [m for m in AI.listAllMoves(board, 0) if m[0] == origin and m[1] in ("push", "free")]

    # free but not push, and the difference is the whole rule: a free pays the jailers'
    # captors (2), a push pays their weight (3, the captive spy included)
    assert [N.encode_move(m) for m in moves] == ["Fd4e4"]

    after = AI.performOneStep(board, 0, moves[0])
    e4 = Hasher.UNPACK[after[Hasher.AlgebraToSquare("e4") - 1]]
    assert (e4[Hasher.SIDE], e4[Hasher.SPY], e4[Hasher.PAWNS]) == (0, 1, 2)
    assert not e4[Hasher.PRISFLAG]                        # the spy is standing, not held
    assert occupied(after) == {"e4": (0, 3), "f4": (1, 2)}   # jailers shoved on alone


def test_freeing_still_has_to_pay_for_the_line_behind_the_jailers():
    # the same rescue with somebody standing behind the jailers: nowhere to shove them, so
    # the free is not on offer either
    board = board_of(d4=(0, 0, 0, 2, 0), e4=(1, 0, 0, 2, 0, 1, 0, 1), f4=(1, 0, 0, 1, 0))
    origin = Hasher.AlgebraToSquare("d4")
    assert [m for m in AI.listAllMoves(board, 0) if m[0] == origin and m[1] == "free"] == []
