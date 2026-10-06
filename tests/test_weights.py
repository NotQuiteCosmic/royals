"""The evaluator's weights are tunable, and the defaults are the evaluator it always was.

ai.evaluateSides used to multiply by literals. It now multiplies by module globals that
tuning/ swaps in and out, and the contract is that with nothing swapped in it computes the
exact same integers it did before -- golden.txt records the evaluation of every position it
visits and is the real check on that. These tests cover what the goldens cannot: that each
weight is wired to its own term and nothing else, that the API refuses what would break the
integer invariant, and that the spread penalty's rewrite is exact.
"""

import math

import pytest

from royals_engine import hasher as Hasher
from royals_engine import ai as AI


BLUE, RED = 0, 1

# The evaluator as it stood before tuning: the hand-set literals the arithmetic below was
# written against. The wiring tests put these in force so that they keep checking that each
# weight drives its own term whatever the shipped defaults become; what the defaults ARE is
# a separate test against tuning/RESULTS.md.
ORIGINAL = {
    "DIAG_WEIGHT": 250, "GROUP_PENALTY": 1500,
    "PRISONER_PAWN_WEIGHT": 800, "PRISONER_SPY_WEIGHT": 800,
    "STACK_1": 1000, "STACK_2": 4000, "STACK_3": 9000, "STACK_4": 16000, "STACK_5": 25000,
    "CAPTIVE_PCT": 200, "SPREAD_WEIGHT": 1500, "ROYAL_SPY_PENALTY": 5000,
    "SPY_DIST_WEIGHT": 0, "THREAT_PENALTY": 0, "SPY_STACK_WEIGHT": 0, "WRONG_COLOUR_PENALTY": 0,
}

# What shipped after the October 2026 tuning -- see tuning/RESULTS.md and the comments in ai.py.
TUNED = {
    "DIAG_WEIGHT": 250, "GROUP_PENALTY": 1480,
    "PRISONER_PAWN_WEIGHT": 830, "PRISONER_SPY_WEIGHT": 800,
    "STACK_1": 1020, "STACK_2": 4120, "STACK_3": 8740, "STACK_4": 15310, "STACK_5": 25370,
    "CAPTIVE_PCT": 205, "SPREAD_WEIGHT": 1530, "ROYAL_SPY_PENALTY": 5010,
    "SPY_DIST_WEIGHT": 5, "THREAT_PENALTY": 0, "SPY_STACK_WEIGHT": 0, "WRONG_COLOUR_PENALTY": 31,
}


@pytest.fixture(autouse=True)
def restore_weights():
    yield
    AI.setWeights(AI.DEFAULT_WEIGHTS)


@pytest.fixture
def original():
    AI.setWeights(ORIGINAL)
    yield


def square(alg):
    return Hasher.AlgebraToSquare(alg)


def place(board, alg, side, spy=0, pawns=0, royal=0, capSpy=0, capPawns=0, dragon=0):
    code = Hasher.Build_Space(side, dragon, spy, pawns, royal, capSpy, capPawns)
    return Hasher.Mod_Space(board, square(alg), code)


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------

def test_defaults_are_what_is_in_force_at_import():
    assert AI.getWeights() == AI.DEFAULT_WEIGHTS
    assert set(AI.DEFAULT_WEIGHTS) == set(AI.WEIGHT_NAMES)
    assert set(AI.WEIGHT_RANGES) == set(AI.WEIGHT_NAMES)


def test_defaults_are_the_tuned_set():
    assert AI.DEFAULT_WEIGHTS == TUNED
    assert AI.STACK_VALUE == (0, 1020, 4120, 8740, 15310, 25370, 0)
    # the gather tie-breakers are on, so the post-pass runs by default
    assert AI.ANY_POST is True


def test_original_weights_reproduce_the_old_literals(original):
    assert AI.getWeights() == ORIGINAL
    assert AI.STACK_VALUE == (0, 1000, 4000, 9000, 16000, 25000, 0)
    assert AI.ANY_POST is False


def test_every_default_sits_inside_its_range():
    for name, value in AI.DEFAULT_WEIGHTS.items():
        low, high = AI.WEIGHT_RANGES[name]
        assert low <= value <= high, name


def test_set_then_get_round_trips_and_rebuilds_the_stack_table():
    AI.setWeights({"STACK_3": 12345, "DIAG_WEIGHT": 0})
    assert AI.getWeights()["STACK_3"] == 12345
    assert AI.getWeights()["DIAG_WEIGHT"] == 0
    assert AI.STACK_VALUE[3] == 12345
    # anything not named is left alone
    assert AI.getWeights()["GROUP_PENALTY"] == AI.DEFAULT_WEIGHTS["GROUP_PENALTY"]


def test_setting_the_defaults_is_a_no_op():
    board = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=3), "a1", RED, spy=1, capPawns=2)
    before = AI.evaluateSides(board)
    AI.setWeights(AI.DEFAULT_WEIGHTS)
    assert AI.evaluateSides(board) == before


@pytest.mark.parametrize("bad", [250.0, True, "250", None])
def test_only_plain_ints_are_accepted(bad):
    with pytest.raises(TypeError):
        AI.setWeights({"DIAG_WEIGHT": bad})
    # a refused set changes nothing, not even the names before the bad one
    assert AI.getWeights() == AI.DEFAULT_WEIGHTS


def test_unknown_names_and_out_of_range_values_are_refused():
    with pytest.raises(KeyError):
        AI.setWeights({"TEMPO": 1})
    low, high = AI.WEIGHT_RANGES["CAPTIVE_PCT"]
    with pytest.raises(ValueError):
        AI.setWeights({"CAPTIVE_PCT": high + 1})
    with pytest.raises(ValueError):
        AI.setWeights({"CAPTIVE_PCT": low - 1})
    assert AI.getWeights() == AI.DEFAULT_WEIGHTS


def test_set_weights_clears_the_search_tables_unless_told_not_to():
    AI.table[("marker",)] = [1, 0, AI.EXACT, None]
    AI.setWeights({"DIAG_WEIGHT": 251}, clear=False)
    assert ("marker",) in AI.table
    AI.setWeights({"DIAG_WEIGHT": AI.DEFAULT_WEIGHTS["DIAG_WEIGHT"]})
    assert ("marker",) not in AI.table


# ---------------------------------------------------------------------------
# The spread penalty rewrite is exact
# ---------------------------------------------------------------------------

def test_spread_penalty_matches_the_old_formula_for_every_reachable_q():
    # n groups each contribute one coordinate in 0..6, so n*s2 - s*s is at most 9*n*n
    for n in range(1, 13):
        for q in range(0, 9 * n * n + 1):
            old = math.isqrt(9 * AI.SCALE * AI.SCALE * q) // (2 * n)
            new = math.isqrt(1500 * 1500 * q) // n
            assert old == new, (n, q)


def test_spread_penalty_scales_with_its_weight(original):
    # two lone pieces three files apart: stdev 1.5 on x, 0 on y
    board = place(place(Hasher.EMPTY_BOARD, "a1", BLUE, pawns=1), "d1", BLUE, pawns=1)
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"SPREAD_WEIGHT": 0})
    assert AI.evaluateSides(board)[BLUE] == base + 1500 * 3 // 2


# ---------------------------------------------------------------------------
# Each weight drives its own term and nothing else
# ---------------------------------------------------------------------------

def jumpreach(alg):
    return AI.JUMPREACH[square(alg)]


def test_a_lone_standing_stack_scores_stack_plus_mobility_minus_group(original):
    board = place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=3)
    blue, red = AI.evaluateSides(board)
    assert blue == 9000 + 250 * jumpreach("d4") * 3 - 1500
    assert red == 0


def test_stack_table_entries_are_independent(original):
    board = place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=3)
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"STACK_3": 10000})
    assert AI.evaluateSides(board)[BLUE] == base + 1000
    AI.setWeights({"STACK_2": 0, "STACK_4": 0})
    assert AI.evaluateSides(board)[BLUE] == base + 1000


def test_captives_are_split_by_kind_and_charged_by_percentage(original):
    # red holds blue's spy and two pawns on a1, with one red pawn as jailer
    board = place(Hasher.EMPTY_BOARD, "a1", RED, pawns=1, capSpy=1, capPawns=2)
    blue, red = AI.evaluateSides(board)
    assert red == 1000 + 250 * jumpreach("a1") * 1 - 1500 + 2 * 800 + 800
    # blue: a group of three, charged 200% of it, no mobility (captives don't stand)
    assert blue == 9000 - 2 * 9000 - 1500

    AI.setWeights({"PRISONER_SPY_WEIGHT": 0})
    assert AI.evaluateSides(board)[RED] == red - 800
    assert AI.evaluateSides(board)[BLUE] == blue

    AI.setWeights({"PRISONER_PAWN_WEIGHT": 0})
    assert AI.evaluateSides(board)[RED] == red - 800 - 1600

    AI.setWeights({"CAPTIVE_PCT": 100})
    assert AI.evaluateSides(board)[BLUE] == 9000 - 9000 - 1500


def test_group_penalty_counts_captive_groups_too(original):
    board = place(place(Hasher.EMPTY_BOARD, "a1", RED, pawns=1, capPawns=1), "g7", BLUE, pawns=1)
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"GROUP_PENALTY": 0})
    assert AI.evaluateSides(board)[BLUE] == base + 2 * 1500


def test_royal_with_spy_is_penalised_by_group_count_squared(original):
    board = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, spy=1, royal=1), "d2", BLUE, pawns=1)
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"ROYAL_SPY_PENALTY": 0})
    assert AI.evaluateSides(board)[BLUE] == base + 4 * 5000


def test_diag_weight_pays_per_piece_and_only_for_standing_stacks(original):
    board = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=2), "a1", RED, pawns=1, capPawns=2)
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"DIAG_WEIGHT": 0})
    assert AI.evaluateSides(board)[BLUE] == base - 250 * jumpreach("d4") * 2


def test_a_win_is_scored_as_win_score_whatever_the_weights():
    board = place(Hasher.EMPTY_BOARD, "d4", BLUE, spy=1, pawns=4, royal=1)
    assert AI.evaluateSides(board)[BLUE] == AI.WIN_SCORE
    AI.setWeights({"STACK_5": 0, "GROUP_PENALTY": 5000})
    assert AI.evaluateSides(board)[BLUE] == AI.WIN_SCORE


# ---------------------------------------------------------------------------
# The candidate terms: off by default, and each measures what it says
# ---------------------------------------------------------------------------

def test_post_pass_runs_only_when_a_candidate_term_is_on(original):
    for name in ("SPY_DIST_WEIGHT", "THREAT_PENALTY", "SPY_STACK_WEIGHT", "WRONG_COLOUR_PENALTY"):
        assert AI.getWeights()[name] == 0
    assert AI.ANY_POST is False
    AI.setWeights({"SPY_STACK_WEIGHT": 1})
    assert AI.ANY_POST is True
    AI.setWeights({"SPY_STACK_WEIGHT": 0})
    assert AI.ANY_POST is False


def test_the_rejected_terms_are_off_by_default():
    assert AI.DEFAULT_WEIGHTS["THREAT_PENALTY"] == 0
    assert AI.DEFAULT_WEIGHTS["SPY_STACK_WEIGHT"] == 0


def test_spy_stack_weight_counts_pawns_on_the_spys_square_only(original):
    board = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, spy=1, pawns=2), "b2", BLUE, pawns=2)
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"SPY_STACK_WEIGHT": 100})
    assert AI.evaluateSides(board)[BLUE] == base + 200
    # no standing spy, nothing to count
    held = place(place(Hasher.EMPTY_BOARD, "d4", RED, pawns=1, capSpy=1), "b2", BLUE, pawns=2)
    before = AI.evaluateSides(held)[BLUE]
    AI.setWeights({"SPY_STACK_WEIGHT": 0})
    assert AI.evaluateSides(held)[BLUE] == before


def test_spy_dist_charges_each_other_group_its_jump_distance_per_piece(original):
    # spy on d4; two pawns on b2, same colour, two diagonal steps away; royal on c4, other colour
    board = place(place(place(Hasher.EMPTY_BOARD, "d4", BLUE, spy=1), "b2", BLUE, pawns=2), "c4", BLUE, royal=1)
    d4, b2, c4 = square("d4"), square("b2"), square("c4")
    assert AI.JUMPDIST[b2][d4] == 2
    assert AI.JUMPDIST[c4][d4] == AI.UNREACHABLE
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"SPY_DIST_WEIGHT": 10})
    assert AI.evaluateSides(board)[BLUE] == base - 10 * (2 * 2 + AI.UNREACHABLE * 1)


def test_wrong_colour_charges_pieces_off_the_spys_colour(original):
    board = place(place(place(Hasher.EMPTY_BOARD, "d4", BLUE, spy=1), "b2", BLUE, pawns=2), "c4", BLUE, royal=1)
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"WRONG_COLOUR_PENALTY": 7})
    assert AI.evaluateSides(board)[BLUE] == base - 7 * 1
    assert AI.squareColour(square("d4")) == AI.squareColour(square("b2"))
    assert AI.squareColour(square("d4")) != AI.squareColour(square("c4"))


def test_threat_penalty_sees_a_heavier_enemy_in_jump_reach_and_nothing_else(original):
    # blue pawn pair on d4; red three-stack on b2 reaches d4 (two diagonal steps, weight 3)
    board = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=2), "b2", RED, pawns=3)
    base = AI.evaluateSides(board)[BLUE]
    AI.setWeights({"THREAT_PENALTY": 100})
    assert AI.evaluateSides(board)[BLUE] == base - 200
    red_base = AI.evaluateSides(board)[RED]
    # the red stack is not threatened back: blue's pair is too light
    AI.setWeights({"THREAT_PENALTY": 0})
    assert AI.evaluateSides(board)[RED] == red_base

    AI.setWeights({"THREAT_PENALTY": 100})
    # too far: a lone red pawn one step away on c3 can reach d4; on a1 it cannot
    assert AI.evaluateSides(place(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=1), "c3", RED, pawns=1))[BLUE] \
        == AI.evaluateSides(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=1))[BLUE] - 100
    assert AI.evaluateSides(place(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=1), "a1", RED, pawns=1))[BLUE] \
        == AI.evaluateSides(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=1))[BLUE]
    # a spy-bearing enemy stack lands on nothing; a royal is landed on by nothing
    spy_stack = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=2), "b2", RED, spy=1, pawns=2)
    assert AI.evaluateSides(spy_stack)[BLUE] == AI.evaluateSides(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=2))[BLUE]
    royal = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, royal=1, pawns=1), "b2", RED, pawns=3)
    assert AI.evaluateSides(royal)[BLUE] == AI.evaluateSides(place(Hasher.EMPTY_BOARD, "d4", BLUE, royal=1, pawns=1))[BLUE]
    # a square holding the enemy's own captives is off limits to them
    jailer = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=1, capPawns=1), "b2", RED, pawns=3)
    assert AI.evaluateSides(jailer)[BLUE] == AI.evaluateSides(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=1, capPawns=1))[BLUE]
    # weight includes prisoners carried: a 1+1 square needs a 2-stack to take it
    carrying = place(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=1, capPawns=1), "b2", RED, pawns=1)
    assert AI.evaluateSides(carrying)[BLUE] == AI.evaluateSides(place(Hasher.EMPTY_BOARD, "d4", BLUE, pawns=1, capPawns=1))[BLUE]
