"""Smoke tests for the tuning harness: it plays a game the engine would play, writes it down
in a form that replays, and gets its statistics right on known inputs. The real checks --
a default-vs-default game matching golden_search.txt ply for ply, the calibration losses --
take minutes and live outside pytest; this is what keeps the harness importable and honest
at the two-second scale."""

import math

import pytest

import tuning  # noqa: F401
from tuning import weights as W
from tuning import openings as O
from tuning import game as G
from tuning import stats as S

from royals_engine import hasher as Hasher
from royals_engine import ai as AI


@pytest.fixture(autouse=True)
def engine_left_clean():
    yield
    assert AI.getWeights() == AI.DEFAULT_WEIGHTS, "a game left non-default weights in force"


def test_weight_ids_depend_on_values_not_on_which_keys_were_given():
    assert W.weight_id({}) == W.weight_id(W.default())
    assert W.weight_id({"DIAG_WEIGHT": 250}) == W.weight_id({})
    assert W.weight_id({"DIAG_WEIGHT": 251}) != W.weight_id({})


def test_opening_replays_from_its_tokens():
    opening = O.make_opening(3, noise=0.5)
    assert len(opening.entries) == 12
    assert opening.to_move == O.FIRST_TO_MOVE
    board, to_move = O.replay(opening.entries, opening.moves)
    assert board == opening.board
    assert to_move == opening.to_move


def test_random_plies_replay_and_keep_red_to_move():
    opening = O.make_opening(3, noise=0.5, random_plies=2, rng_seed=9)
    assert opening is not None
    assert len(opening.moves) == 2
    assert opening.to_move == O.FIRST_TO_MOVE
    assert O.replay(opening.entries, opening.moves)[0] == opening.board
    # the same seed and rng_seed give the same opening, every time
    again = O.make_opening(3, noise=0.5, random_plies=2, rng_seed=9)
    assert again.moves == opening.moves and again.board == opening.board


def test_a_short_game_records_and_replays():
    opening = O.make_opening(3, noise=0.5)
    record = G.play_game(opening, W.default(), {"DIAG_WEIGHT": 0}, depth=1, ply_cap=6)
    assert record["plies"] <= 6
    assert record["blue_id"] != record["red_id"]
    assert record["termination"] in ("ply_cap", "gather", "double_pass")
    assert len(record["moves"]) == record["plies"]
    assert record["first"] == O.FIRST_TO_MOVE
    # the tokens are the game
    final = G.replay_game(record)
    assert Hasher.Check_For_Winner(final)[0] == (record["termination"] == "gather")


def test_same_weights_share_one_state_and_a_game_is_deterministic():
    opening = O.make_opening(3, noise=0.5)
    one = G.play_game(opening, W.default(), W.default(), depth=1, ply_cap=4)
    two = G.play_game(opening, W.default(), W.default(), depth=1, ply_cap=4)
    assert one["moves"] == two["moves"]
    assert one["blue_id"] == one["red_id"]


def test_pair_scores_and_pentanomial():
    # A wins as blue (1), and as red the game's result_blue is 0, so A won both: a full point
    assert S.pair_score(1.0, 0.0) == 1.0
    assert S.pair_score(0.0, 1.0) == 0.0
    assert S.pair_score(1.0, 1.0) == 0.5
    assert S.pentanomial([0.0, 0.5, 0.5, 1.0, 0.75]) == [1, 0, 2, 1, 1]


def test_elo_and_its_interval():
    assert S.elo(0.5) == 0.0
    assert abs(S.elo(S.logistic(37.0)) - 37.0) < 1e-9
    e, low, high = S.elo_ci([10, 20, 40, 20, 10])
    assert low < e < high and abs(e) < 1e-9
    e2, low2, high2 = S.elo_ci([5, 10, 40, 30, 15])
    assert e2 > 0 and low2 < e2 < high2
    # four times the pairs, same shape: the interval halves
    e3, low3, high3 = S.elo_ci([40, 80, 160, 80, 40])
    assert abs(e3) < 1e-9 and abs((high3 - low3) - (high - low) / 2) < 0.5


def test_sprt_moves_the_right_way_and_refuses_to_divide_by_nothing():
    assert S.sprt_llr([0, 0, 100, 0, 0], 0, 10) is None
    assert S.sprt_status([], 0, 10) == "undecidable"
    winning = [0, 10, 40, 60, 40]
    losing = [40, 60, 40, 10, 0]
    assert S.sprt_llr(winning, 0, 10) > 0
    assert S.sprt_llr(losing, 0, 10) < 0
    assert S.sprt_status([0, 100, 400, 900, 600], 0, 10) == "H1"
    assert S.sprt_status([600, 900, 400, 100, 0], 0, 10) == "H0"
    low, high = S.sprt_bounds()
    assert math.isclose(high, -low)


def test_bradley_terry_orders_a_transitive_field():
    games = []
    for _ in range(20):
        games += [("a", "b", 1.0), ("b", "c", 1.0), ("a", "c", 1.0), ("b", "a", 0.5)]
    ratings = S.bradley_terry(games)
    assert ratings["a"][0] > ratings["b"][0] > ratings["c"][0]
    assert ratings["a"][1] == 60


# ---------------------------------------------------------------------------
# match.py and spsa.py, at the scale of one or two depth-1 games
# ---------------------------------------------------------------------------

from tuning import match as M  # noqa: E402
from tuning import spsa as P  # noqa: E402


def test_play_pair_is_two_games_on_one_opening_with_colours_swapped():
    row = O.make_opening(3, noise=0.5).to_row()
    job = {"opening": row, "a": W.default(), "b": W.complete({"DIAG_WEIGHT": 0}),
           "depth": 1, "ply_cap": 4, "tag": "t"}
    pair = M.play_pair(job)
    first, second = pair["games"]
    assert first["blue_id"] == pair["a_id"] == second["red_id"]
    assert first["red_id"] == pair["b_id"] == second["blue_id"]
    assert pair["score_a"] == S.pair_score(first["result_blue"], second["result_blue"])
    assert pair["tag"] == "t"


def test_tally_and_resume_read_back_what_was_written(tmp_path):
    path = str(tmp_path / "r.jsonl")
    pairs = [{"a_id": "A", "b_id": "B", "seed": s, "tag": "match", "score_a": sc,
              "games": [{"plies": 10, "seconds": 0.1, "termination": "gather", "result_blue": 1.0},
                        {"plies": 12, "seconds": 0.1, "termination": "ply_cap", "result_blue": 0.5}]}
             for s, sc in ((1, 0.75), (2, 0.5), (3, 1.0))]
    with open(path, "w") as f:
        for p in pairs: f.write(__import__("json").dumps(p) + "\n")
    assert len(M.previous_pairs(path, "A", "B", "match")) == 3
    assert M.previous_pairs(path, "A", "B", "sprt") == []
    assert M.previous_pairs(path, "B", "A", "match") == []
    tally = M.Tally(pairs)
    assert tally.pent == [0, 0, 1, 1, 1]
    assert tally.games == 6 and tally.caps == 3
    assert "3 pairs" in tally.summary()


def test_spsa_probes_bracket_theta_and_a_result_moves_it_the_right_way():
    run = P.SPSA("unit", W.default(), ["DIAG_WEIGHT", "SPREAD_WEIGHT"], iterations=100, depth=1)
    assert run.c["DIAG_WEIGHT"] == max(P.C_MIN, P.C_FRACTION * 250)
    rows = [O.make_opening(3, noise=0.5).to_row()]
    job = next(run.jobs(rows))
    delta = job["tag"]["delta"]
    for p in ("DIAG_WEIGHT", "SPREAD_WEIGHT"):
        assert (job["a"][p] - job["b"][p]) * delta[p] > 0
        assert job["a"]["GROUP_PENALTY"] == job["b"]["GROUP_PENALTY"] == AI.DEFAULT_WEIGHTS["GROUP_PENALTY"]
    before = dict(run.theta)
    run.apply({"tag": job["tag"], "score_a": 1.0})      # the plus side won both games
    for p in ("DIAG_WEIGHT", "SPREAD_WEIGHT"):
        assert (run.theta[p] - before[p]) * delta[p] > 0
    run.apply({"tag": job["tag"], "score_a": 0.5})      # a split pair moves nothing
    after = dict(run.theta)
    run.apply({"tag": job["tag"], "score_a": 0.0})      # the minus side won: back the other way
    for p in ("DIAG_WEIGHT", "SPREAD_WEIGHT"):
        assert (run.theta[p] - after[p]) * delta[p] < 0
    assert run.done == 3
    # the engine's weights never see a float
    for value in run.rounded().values(): assert type(value) is int


def test_spsa_step_schedule_ends_at_r_end():
    run = P.SPSA("unit2", W.default(), ["GROUP_PENALTY"], iterations=1000, depth=1)
    n = run.iterations
    assert math.isclose(run.a_k("GROUP_PENALTY", n) / run.c_k("GROUP_PENALTY", n) ** 2, P.R_END)


# ---------------------------------------------------------------------------
# queue.py: finishes what is left, records each job once, does nothing twice
# ---------------------------------------------------------------------------

from tuning import queue as Q  # noqa: E402


def test_queue_runs_to_completion_and_is_idempotent(tmp_path):
    import json
    book = tmp_path / "book.jsonl"
    with open(book, "w") as f:
        for seed in (3, 11):
            f.write(json.dumps(O.make_opening(seed, noise=0.5).to_row()) + "\n")
    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    W.save(W.default(), str(a)); W.save({"DIAG_WEIGHT": 0}, str(b))
    out = tmp_path / "r.jsonl"
    queue = tmp_path / "queue.jsonl"
    verdicts = tmp_path / "verdicts.jsonl"
    with open(queue, "w") as f:
        f.write(json.dumps({"id": "m", "mode": "match", "a": str(a), "b": str(b), "depth": 1,
                            "pairs": 2, "ply_cap": 4, "book": str(book), "out": str(out)}) + "\n")
        f.write(json.dumps({"id": "s", "mode": "sprt", "a": str(a), "b": str(b), "depth": 1,
                            "elo0": 0, "elo1": 10, "max_pairs": 2, "ply_cap": 4,
                            "book": str(book), "out": str(out)}) + "\n")

    recorded = Q.run(str(queue), str(verdicts), workers=2)
    assert [v["id"] for v in recorded] == ["m", "s"]
    assert recorded[0]["pairs"] == 2 and recorded[0]["target"] == 2
    assert recorded[1]["sprt"] in ("H0", "H1", "inconclusive")
    # the two jobs share a results file but not a tag, so neither borrowed the other's pairs
    assert sum(1 for _ in open(out)) == 4
    # a second run has nothing to do and records nothing
    assert Q.run(str(queue), str(verdicts), workers=2) == []
    assert len(Q.load_verdicts(str(verdicts))) == 2
    for job in Q.load_jobs(str(queue)):
        assert Q.progress_of(job)[0] is True
