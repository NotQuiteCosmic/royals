"""The harness in `tools/dragon_balance.py`, held to the things that would silently spoil it.

**No golden file can see any of this, and that is the point.** The harness measures a rules
*variant* -- White's dragon on d4, Black's placed last on a long diagonal -- so it must build
its own start boards and must never touch `Hasher.Entering_Board()`. A harness that quietly
reached for the engine's own opening would move `golden_enter.txt`; one that quietly diverged
from it in arm A would report a "baseline" that is not the game anybody plays. Neither failure
shows up as a diff in any of the four contracts, because the harness is not part of any of
them.

So what is tested here is the *measurement apparatus*, never the result. Every assertion below
is about a property the experiment needs in order to mean anything:

  - arm A really is the current game, byte for byte, board and entering alike;
  - the variant's geometry is derived from the engine's own diagonals rather than transcribed;
  - Black's thirteenth placement lands somewhere the variant actually permits;
  - Black moves first, which `regress.sweepGames` -- the only committed self-play loop in the
    repo -- gets the other way round, correctly for a node-count golden and catastrophically
    for a balance measurement;
  - a game is a pure function of its arguments, so a published number can be re-derived.

Games here run at depth 1. Depth is not what any of these properties depend on, and depth 1
keeps the file under a second per game.
"""

import pathlib
import sys

import pytest

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher
from royals_engine import notation as N

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dragon_balance as DB
import regress


WHITE, BLACK = 0, 1

# The thirteen squares docs/STRATEGY.md names as the only ones a jump can wrap from, written
# out here precisely because the harness derives them. A derivation that agrees with nothing
# is a derivation nobody checked.
DIAGONALS = ("a1", "b2", "c3", "d4", "e5", "f6", "g7",
             "a7", "b6", "c5", "e3", "f2", "g1")


@pytest.fixture(autouse=True)
def clean_engine():
    """The engine's ko set and search tables are module globals, and `play` leaves a finished
    game's state behind in them -- the pool's worker is what normally clears it. Without this
    one test's endgame becomes the next one's opening history, and the failure lands in
    whichever test happens to run next."""
    yield
    Engine.koReset()
    AI.newGame()


####### Arm A is the game as it stands #######

def test_the_baseline_start_board_is_the_engines_own_opening():
    # The whole claim that arm A is a baseline. If these ever differ, every delta measured
    # against it is a delta from something nobody plays.
    assert DB.start_board("A") == Hasher.Entering_Board(), (
        "arm A opened on %s, the engine opens on %s"
        % (N.encode_board(DB.start_board("A")), N.encode_board(Hasher.Entering_Board())))


def test_the_baseline_entering_matches_the_engines_own():
    # `DB.enter` is `regress.computeEnteredBoard` with the start board lifted out into an
    # argument. Given the same start board it must still be that function exactly -- otherwise
    # the harness is measuring its own opening rather than the game's.
    for seed in (0, 1, 7, 42):
        assert DB.enter(DB.start_board("A"), seed) == regress.computeEnteredBoard(seed), (
            "arm A's entering diverged from the engine's at seed %d" % seed)


def test_black_moves_first():
    # "Whoever entered second opens", and White enters first at every stage. regress.sweepGames
    # starts at turn 0 and so opens with White; copying it without noticing would hand the
    # first-move edge to the wrong colour and invert the headline number.
    assert DB.FIRST_TURN % 2 == BLACK


####### The variant's geometry #######

def test_the_diagonal_squares_are_the_thirteen_that_can_wrap():
    named = sorted(Hasher.AlgebraToSquare(alg) for alg in DIAGONALS)
    assert DB.DIAGONAL_SQUARES == named, (
        "derived %s" % [Hasher.IndexToAlg(s - 1) for s in DB.DIAGONAL_SQUARES])
    assert len(DB.DIAGONAL_SQUARES) == 13
    assert DB.D4 in DB.DIAGONAL_SQUARES, "d4 is on both diagonals and must appear once"


@pytest.mark.parametrize("arm", sorted(DB.ARMS))
def test_every_arms_start_board_is_one_the_engine_could_hold(arm):
    # validate_board is the check that a board is legal furniture -- 49 codes in range, and no
    # side holding two royals or two dragons. A start board that failed it would still play,
    # and would be a position the rules cannot produce.
    board = DB.start_board(arm)
    N.validate_board(board)

    dragons = [s for s in range(1, 50) if Hasher.UNPACK[board[s - 1]][Hasher.DRAGON]]
    expected = 1 if DB.ARMS[arm]["black_late"] else 2
    assert len(dragons) == expected


def test_a_placed_dragon_stands_alone():
    # Build_Space canonicalises a dragon square by dropping every other field, which is the
    # rule "a dragon's square holds nothing but the dragon". place_dragon must go through it
    # rather than around it -- and note dropPiece cannot be used at all, since it hard-codes
    # the dragon field to 0.
    board = DB.place_dragon(Hasher.EMPTY_BOARD, DB.D4, WHITE)
    s = Hasher.UNPACK[board[DB.D4 - 1]]
    assert s[Hasher.DRAGON] == 1
    assert (s[Hasher.SPY], s[Hasher.PAWNS], s[Hasher.ROYAL]) == (0, 0, 0)
    assert Hasher.spaceWeight(s) == 3, "a dragon is worth 3 by every count"


def test_the_centre_dragon_is_more_mobile_than_the_one_it_replaces():
    # The variant's stated premise. Asked of the engine rather than asserted from the prose,
    # so a change to jump legality cannot leave the premise standing unexamined.
    def destinations(alg):
        board = DB.place_dragon(Hasher.EMPTY_BOARD, Hasher.AlgebraToSquare(alg), WHITE)
        square = Hasher.AlgebraToSquare(alg)
        return len(Engine.checkMoves(board, Engine.makeOrigin(board, square), WHITE)[0])

    assert destinations("d4") > destinations("d3") == destinations("d5"), (
        "d3 %d, d4 %d, d5 %d" % (destinations("d3"), destinations("d4"), destinations("d5")))


####### The thirteenth placement #######

def test_the_centre_is_never_offered_to_blacks_dragon():
    # d4 drops out for being occupied rather than by being named, so this stays true however
    # the arms move White about.
    board = DB.enter(DB.start_board("B"), 3)
    assert DB.D4 not in DB.free_diagonal_squares(board)


@pytest.mark.parametrize("how", ("search", "random"))
def test_blacks_dragon_lands_where_the_variant_allows(how):
    import random
    for seed in range(4):
        board = DB.enter(DB.start_board("B"), seed)
        square = DB.pick_dragon_square(board, how, 1, random.Random(seed))

        assert square in DB.DIAGONAL_SQUARES, "%s is off the diagonals" % Hasher.IndexToAlg(square - 1)
        assert square != DB.D4, "took the square White's dragon is standing on"
        assert not Hasher.UNPACK[board[square - 1]][Hasher.OCCUPIED], "landed on an occupied square"


def test_the_search_picker_ignores_the_order_it_tries_candidates_in():
    # The picker resets the engine per candidate for exactly this reason: a shared
    # transposition table would make the answer depend on which square was scored first, and
    # the harness would be measuring evaluation order.
    import random
    board = DB.enter(DB.start_board("B"), 5)
    first = DB.pick_dragon_square(board, "search", 1, random.Random(0))
    for _ in range(3):
        assert DB.pick_dragon_square(board, "search", 1, random.Random(0)) == first


def test_the_own_side_adjacency_rule_is_not_applied_to_the_late_dragon():
    # The variant places this dragon spy-style. So it must be free to land beside Black's own
    # army -- which `Engine.enteringOptions` for a royal or pawn would forbid. A picker that
    # reached for enteringOptions would quietly reimpose the rule the variant removes.
    board = DB.enter(DB.start_board("B"), 2)
    free = DB.free_diagonal_squares(board)
    guarded = set(Engine.enteringOptions(board, BLACK, False))
    assert [s for s in free if s not in guarded], (
        "no candidate square touches Black's army, so this seed cannot tell the rules apart")


####### Results #######

@pytest.mark.parametrize("arm", sorted(DB.ARMS))
def test_a_game_is_a_pure_function_of_its_arguments(arm):
    # The property that makes a published number re-derivable, which is the whole reason this
    # harness is committed rather than thrown away like the ones STRATEGY.md's appendix
    # describes. Note the fixture's reset does NOT run between these two calls, so this also
    # catches a game leaking state into the next one.
    first = DB.play(arm, 1, 1)
    second = DB.play(arm, 1, 1)
    first.pop("seconds"), second.pop("seconds")
    assert first == second


@pytest.mark.parametrize("arm", sorted(DB.ARMS))
def test_a_game_ends_and_says_how(arm):
    result = DB.play(arm, 0, 1)
    assert result["termination"] in ("gather", "double_pass", "ply_cap")
    assert 0 < result["plies"] <= DB.PLY_CAP
    assert len(result["evals"]) == result["plies"], "one evaluation per ply, or the trajectory lies"

    # Only a gather has a winner, and only the arms that place a dragon late record a square.
    assert (result["winner"] is not None) == (result["termination"] == "gather")
    assert (result["dragon_square"] is not None) == bool(DB.ARMS[arm]["black_late"])


def test_a_win_really_is_six_on_a_square_at_the_start_of_a_turn():
    # The delayed win is the rule the search and the server both implement and
    # regress.sweepGames does not. A harness using the raw detector would award games to
    # stacks that were about to be shattered, and would do it silently.
    result = DB.play("A", 0, 1)
    assert result["termination"] == "gather", "seed 0 is meant to finish by gathering"

    board = N.decode_board(result["entered"])
    assert not Hasher.Check_For_Winner(board)[0], "nobody has gathered before a move is played"
    assert abs(result["evals"][-1]) > AI.WIN_SCORE // 2, (
        "the last evaluation should be a won one, got %d" % result["evals"][-1])
