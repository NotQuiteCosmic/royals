"""The push-range variant: an optional rule a game opts into.

A push travels a distance instead of the one square it has always moved, and **the mover
chooses how far** from 1 up to a ceiling of `strength - the weight of the shoved line + 1`.
That makes it a change to the move list rather than only to the executor: one push becomes up
to six moves.

Four things make this rule safe to have, and this file is about all four.

**It is off by default and the standard game is untouched.** `golden_moves.txt`,
`golden_enter.txt` and `golden_search.txt` are byte-identical with the rule off; the variant
answers to `golden_push_moves.txt` instead, which both engines emit. Those files are the
contract and are checked by `regress.py`, not here -- what is here is the arithmetic they are
made of, on positions small enough to read.

**The endgame survives.** A lone spy shoves anything and moves it exactly one. Without that
exception the formula makes a completed six untouchable -- strength 1 against weight 6 is -4 --
and the delayed-win rule becomes unreachable, since nothing else in the game can touch a
finished stack. See `test_win_horizon.py` for what depends on that.

**A game says which rules it was played under.** The record carries `rules push-range` and each
travelling push carries its distance, because both failures are silent: a distance dropped on
the way to disk replays as some other distance, the piece counts still add up, and nothing
downstream objects.

**Both engines agree.** The rule is implemented twice, and `regress.py check pushmoves` plus
`cargo test` are what hold them together. The tests here run on whichever engine is installed,
so CI runs them against both.
"""

import pytest

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher
from royals_engine import notation as N
from royals_engine import record as R


@pytest.fixture
def variant():
    """The rule on for one test, and off again afterwards however the test ends.

    The rule may change between games and never within one -- a search that saw it move would
    play half its tree by one rule set and half by another. A fixture is how that stays true
    of the test suite too: a test that raised part way through would otherwise leave the rule
    switched on for everything after it, and the failure would land somewhere else entirely.
    """
    was = Engine.PUSH_RANGE
    Engine.setPushRange(True)
    yield
    Engine.setPushRange(was)


def board_of(**squares):
    """A board from `alg=(side, dragon, spy, pawns, royal, capSpy, capPawns, prisFlag)`."""
    board = list(Hasher.EMPTY_BOARD)
    for alg, spec in squares.items():
        board[Hasher.AlgebraToSquare(alg) - 1] = Hasher.Build_Space(*spec)
    return tuple(board)


def occupied(board):
    """`{alg: weight}` for every square holding anything -- boards, readably."""
    return {Hasher.IndexToAlg(i): Hasher.UNPACK[c][Hasher.WEIGHT]
            for i, c in enumerate(board) if c}


def pushes(board, alg, contr=0):
    """`{distance: move}` for the pushes offered out of `alg`, by distance."""
    square = Hasher.AlgebraToSquare(alg)
    out = {}
    for m in AI.listAllMoves(board, contr):
        if m[0] == square and m[1] == "push":
            out[m[4] if len(m) > 4 else 1] = m
    return out


# ---------------------------------------------------------------------------
# Off, nothing changes
# ---------------------------------------------------------------------------

def test_with_the_rule_off_a_push_moves_one_square():
    """The compatibility claim, and the reason the goldens do not move. Asserted here as well
    as by `regress.py` because a file staying byte-identical says nothing about *why*."""
    board = board_of(b4=(0, 0, 0, 4, 0), c4=(1, 0, 0, 1, 0))
    offered = pushes(board, "b4")

    assert sorted(offered) == [1], "a push offered more than one distance with the rule off"
    assert len(offered[1]) == 4, "a standard push should be the four-tuple it always was"

    after = AI.performOneStep(board, 0, offered[1])
    assert occupied(after) == {"c4": 4, "d4": 1}


# ---------------------------------------------------------------------------
# The ceiling
# ---------------------------------------------------------------------------

def test_the_mover_picks_any_distance_up_to_the_ceiling(variant):
    """strength 4 against a lone pawn is 4 - 1 + 1 = 4, and all four are real moves."""
    board = board_of(b4=(0, 0, 0, 4, 0), c4=(1, 0, 0, 1, 0))
    offered = pushes(board, "b4")
    assert sorted(offered) == [1, 2, 3, 4]

    landings = {}
    for far, move in offered.items():
        landings[far] = occupied(AI.performOneStep(board, 0, move))
    assert landings[1] == {"c4": 4, "d4": 1}
    assert landings[2] == {"d4": 4, "e4": 1}
    assert landings[3] == {"e4": 4, "f4": 1}
    assert landings[4] == {"f4": 4, "g4": 1}


def test_the_ceiling_is_the_differential_and_not_the_strength(variant):
    """A heavy stack shoving something heavy goes nowhere in particular. This is why the rule
    turned out to change so little in play: the differential is usually small."""
    even = board_of(b4=(0, 0, 0, 3, 0), c4=(1, 0, 0, 3, 0))
    assert sorted(pushes(even, "b4")) == [1], "3 against 3 is one square"

    board = board_of(b4=(0, 0, 0, 4, 0), c4=(1, 0, 0, 2, 0))
    assert sorted(pushes(board, "b4")) == [1, 2, 3], "4 against 2 is three"


def test_a_push_it_cannot_afford_is_still_no_push(variant):
    """Legality is unchanged: a ceiling of at least one is exactly the old strength >= weight,
    so which directions are on offer does not move -- only how far each can go."""
    board = board_of(b4=(0, 0, 0, 1, 0), c4=(1, 0, 0, 3, 0))
    assert pushes(board, "b4") == {}


# ---------------------------------------------------------------------------
# What cuts it short
# ---------------------------------------------------------------------------

def test_a_collision_stops_the_line(variant):
    """Beyond the shoved line is the empty square that ended it; past that there may be
    anything, and at range the front of the line reaches it."""
    board = board_of(b4=(0, 0, 0, 4, 0), c4=(1, 0, 0, 1, 0), e4=(1, 0, 0, 1, 0))
    assert sorted(pushes(board, "b4")) == [1], "the piece on e4 leaves room for one"

    after = AI.performOneStep(board, 0, pushes(board, "b4")[1])
    assert occupied(after) == {"c4": 4, "d4": 1, "e4": 1}, "e4 was not part of the push"


def test_the_pusher_stops_at_the_edge_and_what_it_shoves_wraps(variant):
    """The one asymmetry in the rule: the clip is on the pusher's landing square alone."""
    board = board_of(e4=(0, 0, 0, 4, 0), f4=(1, 0, 0, 1, 0))
    offered = pushes(board, "e4")
    assert sorted(offered) == [1, 2], "e4 can reach f4 and g4, and no further"

    after = AI.performOneStep(board, 0, offered[2])
    assert occupied(after) == {"g4": 4, "a4": 1}, "the pusher stopped; the pawn wrapped"


def test_the_line_is_rigid(variant):
    """Everything travels the same distance. A two-square line shoved two squares stays a
    two-square line -- it does not concertina, and it does not leave anybody behind."""
    board = board_of(a4=(0, 0, 0, 4, 0), b4=(1, 0, 0, 1, 0), c4=(1, 0, 0, 1, 0))
    offered = pushes(board, "a4")
    assert sorted(offered) == [1, 2, 3], "strength 4 against a weight-2 line is three"

    after = AI.performOneStep(board, 0, offered[3])
    assert occupied(after) == {"d4": 4, "e4": 1, "f4": 1}, \
        "all three squares should have moved three, keeping the line contiguous"


# ---------------------------------------------------------------------------
# The two exceptions
# ---------------------------------------------------------------------------

def test_a_lone_spy_shoves_anything_and_moves_it_one(variant):
    """The exception the endgame rests on. The formula would make this illegal -- strength 1
    against weight 6 is -4 -- and a completed six would be untouchable."""
    board = board_of(d4=(0, 0, 1, 4, 1), e4=(1, 0, 1, 0, 0), a1=(1, 0, 0, 0, 1))
    offered = pushes(board, "e4", contr=1)

    assert sorted(offered) == [1], "a spy's shove is one square whatever it is shoving"
    after = AI.performOneStep(board, 1, offered[1])
    assert not Hasher.Check_For_Winner(after)[0], "the shatter stopped working"


def test_freeing_stays_a_one_square_move(variant):
    """The freed pieces stand up into the square the pusher walks onto, and at range there is
    no such square."""
    board = board_of(d4=(0, 0, 0, 3, 0), e4=(1, 0, 0, 1, 0, 0, 1, 1), a1=(1, 0, 0, 0, 1))
    frees = [m for m in AI.listAllMoves(board, 0)
             if m[0] == Hasher.AlgebraToSquare("d4") and m[1] == "free"]
    assert frees, "no freeing push on offer"
    assert all(len(m) == 4 for m in frees), "a free carried a distance"


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def test_a_variant_game_survives_the_round_trip(variant):
    """The failure this guards is silent in both directions: a distance dropped on the way to
    disk replays as some other distance, and the piece counts still add up."""
    board = board_of(b4=(0, 0, 0, 4, 0), c4=(1, 0, 0, 1, 0),
                     a1=(0, 0, 0, 0, 1), g7=(1, 0, 0, 0, 1))
    move = pushes(board, "b4")[3]
    played = AI.performOneStep(board, 0, move)

    text = N.encode_game([N.encode_move(move)], board=board, turn=0,
                         rules=(N.RULE_PUSH_RANGE,))
    assert "Pb4c4%s3" % (N.TRAVEL_SUFFIX,) in text
    assert "%s %s" % (N.RULES_KEY, N.RULE_PUSH_RANGE) in text

    # read it back in a world where the rule is off, the way a fresh process would
    Engine.setPushRange(False)
    _moves, spots = R.read(text)
    assert spots[-1].board == played
    assert Engine.PUSH_RANGE is False, "the walk left the rule switched on"


def test_the_same_record_without_its_rules_line_does_not_replay_the_same_game(variant):
    """Why the line exists. Strip it and every ranged push clamps to one square -- and the
    result is a board that validates, which is what makes it worth refusing rather than
    trusting."""
    board = board_of(b4=(0, 0, 0, 4, 0), c4=(1, 0, 0, 1, 0),
                     a1=(0, 0, 0, 0, 1), g7=(1, 0, 0, 0, 1))
    move = pushes(board, "b4")[3]
    played = AI.performOneStep(board, 0, move)

    text = N.encode_game([N.encode_move(move)], board=board, turn=0,
                         rules=(N.RULE_PUSH_RANGE,))
    stripped = "\n".join(l for l in text.splitlines()
                         if not l.startswith(N.RULES_KEY + " "))

    Engine.setPushRange(False)
    _moves, spots = R.read(stripped)
    assert spots[-1].board != played, "the rules line made no difference, so it guards nothing"


def test_the_log_says_where_the_stack_actually_landed(variant):
    """A push's target names the square it shoves, which is where the stack lands only when it
    travels one. Without this a three-square push reads as a one-square push into a square the
    stack is not standing on."""
    board = board_of(b4=(0, 0, 0, 4, 0), c4=(1, 0, 0, 1, 0))
    said = AI.describeMove(pushes(board, "b4")[3])
    assert "E4" in said and "3 squares" in said
    assert "C4" not in said, "the log named the square shoved rather than the square reached"
