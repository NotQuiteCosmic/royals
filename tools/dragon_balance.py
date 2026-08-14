#!/usr/bin/env python3
"""Measure what the d4 / long-diagonal dragon variant does to the balance of Royals.

    python3 tools/dragon_balance.py run --arms A,B,C,D --seeds 200 --depth 3 --out r.jsonl
    python3 tools/dragon_balance.py report r.jsonl

The variant under test changes where the dragons start:

  - **White's dragon starts on d4** instead of d3 -- the centre, the only square on both long
    diagonals, and the most mobile square on the board (12 jump destinations against d3's 10).
  - **Black's dragon is not pre-placed at all.** It enters as a thirteenth and final
    placement, after both spies, on any unoccupied square of the two long diagonals, chosen
    by BLACK -- an explicit exception to Royals' opponent-places convention -- and ignoring
    the own-side adjacency rule the way a spy does.

Nothing here changes the engine. The variant lives entirely in this file: start boards are
built from `Hasher.EMPTY_BOARD` rather than by touching `Hasher.Entering_Board()`, so all four
golden files are untouched by construction and the Rust engine needs no port. This is a
measurement, not a rules change. If the variant is ever adopted, that is when both engines
change together and docs/PORTING.md governs.


## The thing that shapes this whole file

**The engine's evaluator and its entering heuristic are both completely blind to the dragon.**
`AI.evaluateSides` skips dragon squares ("the dragon sits outside all of this", ai.py:430) and
`AI.entryScore` skips them too (ai.py:1064). Vary only a dragon's square and `fullCheck` and
`entryScore` return *the same number every time*; only the legal-move count moves. Three
consequences, and they are why the code below looks as it does:

  1. Black's "pick the best square" policy cannot use either scorer -- both would tie all
     twelve candidates and the pick would silently degenerate to lowest square number. The
     picker has to be a **search** (`AI.chooseMove`), which is the only thing in the engine
     that sees a dragon at all, and sees it through move generation.
  2. The eval trajectory recorded per ply measures the variant only indirectly, through what
     the six gatherable pieces end up doing. That is still worth having; it is just not a
     direct read on the dragon.
  3. Engine self-play systematically under-uses dragons, so **every number this harness
     produces is a lower bound** on the effect a dragon-aware player would extract. The
     report prints that caveat rather than leaving it to be remembered.

The depth ladder is the handle on (3): run the same seeds at several depths, and if the effect
grows with depth, that is evidence the blind evaluator is suppressing something real.


## Why this file is committed

docs/STRATEGY.md's appendix is candid that its numbers came from "throwaway scripts driving
`royals_engine.ai.takeTurn`" which were never committed -- so not one of them can be
re-derived. A game here is a pure function of `(arm, seed, depth, entry mode, noise)`, and
`run` twice over the same arguments produces byte-identical JSONL.
"""

import argparse
import concurrent.futures
import json
import math
import os
import platform
import random
import statistics
import sys
import time

from royals_engine import _accel
from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher
from royals_engine import notation as N


WHITE, BLACK = 0, 1
SIDE_NAMES = {WHITE: "white", BLACK: "black"}

# The engine's own turn counter starts at 1 once entering is done -- "whoever entered second
# opens", and blue/white enters first at every stage, so BLACK MOVES FIRST. See
# royals_web/game.py:_start_play. This is worth stating loudly because `regress.sweepGames`,
# the only committed self-play loop in the repo, starts at turn 0 and therefore opens with
# White. That is fine for a node-count golden and wrong for a balance measurement: it would
# hand the first-move edge to the wrong colour and invert the headline number.
FIRST_TURN = 1

# Long enough that it should essentially never bind: STRATEGY.md's 60-game sample had median
# 50, mean 57 and a longest of 194. The report prints how often it did bind anyway, because a
# cap that starts catching games stops being a safety net and starts being a truncation of
# the length distribution we are trying to measure.
PLY_CAP = 400

# The transposition table is bounded per search rather than per insertion, so a long run of
# games in one worker will otherwise sit on several hundred MB. ai_pool.py uses 50_000 for
# the same reason and records the measurement: 54 MB against 79 MB at the default 300_000.
TABLE_LIMIT = 50_000


####### Geometry #######

def _diagonal_squares():
    """The thirteen squares a jump can wrap from, as 1-based squares, sorted.

    Derived from the engine's own two diagonals rather than written out here, so a change to
    the board's geometry cannot leave this file quietly describing the old one. Engine.Diag1
    and Diag2 hold 0-based [x, y] pairs and intersect at d4, which is why this is a set.
    """
    return sorted({x + y * 7 + 1 for x, y in Engine.Diag1 + Engine.Diag2})


DIAGONAL_SQUARES = _diagonal_squares()
D3 = Hasher.AlgebraToSquare("d3")
D4 = Hasher.AlgebraToSquare("d4")
D5 = Hasher.AlgebraToSquare("d5")


def place_dragon(board, square, side):
    """A dragon on `square`, 1-based.

    `Engine.dropPiece` cannot do this: it handles the spy, the pawns and the royal, and passes
    a hard-coded 0 for the dragon field. So a dragon is set the way the tests' `board_of`
    helper sets one -- through Build_Space, which canonicalises a dragon square by dropping
    every other field on it, which is the rule that a dragon stands alone.
    """
    return Hasher.Mod_Space(board, square, Hasher.Build_Space(side, 1, 0, 0, 0))


####### The arms #######

# Baseline against variant alone would give one number confounding three separate changes, so
# the variant is taken apart into the pieces that can be measured on their own:
#
#   C - A   what moving White's dragon to the centre is worth
#   D - C   what removing Black's dragon from the entering phase and re-siting it on a
#           diagonal is worth. This is where the placement-freedom effect lands: with no
#           dragon on the board Black's royal and pawns average ~35.5 legal squares per
#           placement against the baseline's ~28.5 -- and because the OPPONENT places your
#           army, those seven extra squares are seven more squares White can choose from to
#           hurt Black. The variant's compensation for Black may well be a handicap.
#   B - D   what Black's *choice* is worth, over being dropped on a diagonal at random
#   B - A   the headline
ARMS = {
    "A": {"white": D3, "black": D5, "black_late": None,
          "what": "baseline: white d3, black d5"},
    "B": {"white": D4, "black": None, "black_late": "search",
          "what": "variant: white d4, black picks a diagonal square last"},
    "C": {"white": D4, "black": D5, "black_late": None,
          "what": "geometry only: white d4, black d5 as usual"},
    "D": {"white": D4, "black": None, "black_late": "random",
          "what": "choice removed: white d4, black dropped on a random diagonal square"},
}


def start_board(arm):
    """The board the entering phase begins on, for one arm."""
    spec = ARMS[arm]
    board = place_dragon(Hasher.EMPTY_BOARD, spec["white"], WHITE)
    if spec["black"] is not None:
        board = place_dragon(board, spec["black"], BLACK)
    return board


####### Entering #######

def enter(board, seed, mode = "heuristic", noise = 0.5):
    """Run the twelve placements onto `board`. Returns the entered board.

    The body of `regress.computeEnteredBoard` with the start board as an argument instead of
    a hard-coded `Hasher.Entering_Board()` -- which is the whole of what this harness needed
    and the reason it could not simply call it. `tests/test_dragon_balance.py` pins arm A's
    result against `regress.enteredBoard`, so the two cannot drift apart.

    `mode`:
      heuristic -- `AI.chooseEntry`, varied by the Perlin field `setEntryNoise` draws from the
                   seed. This is what the front ends play and what the goldens record.
      random    -- `Engine.randomEntry`, drawn uniformly from the legal squares. The
                   robustness arm: the entering heuristic is one of the two dragon-blind
                   scorers, so a result that only holds under it is a result about the
                   heuristic.

    A side with nowhere legal to go sits the step out, which is the same skip every driver in
    the repo performs.
    """
    if mode == "heuristic": AI.setEntryNoise(noise, seed)

    for index, (contr, piece) in enumerate(Engine.enteringSequence()):
        isSpy = (piece == Hasher.SPY)
        if not Engine.enteringOptions(board, contr, isSpy): continue

        if mode == "heuristic":
            square = AI.chooseEntry(board, contr, piece, isSpy)
        else:
            square = Engine.randomEntry(board, contr, isSpy, Engine.entryRng(seed, index))

        if square is None: continue
        board = Engine.dropPiece(board, square, contr, piece)

    return board


####### The thirteenth placement #######

def free_diagonal_squares(board):
    """Where Black's dragon may go: unoccupied, on a long diagonal.

    d4 is on both diagonals and holds White's dragon in every arm that uses this, so it drops
    out here for being occupied rather than by being named -- which keeps this function
    honest if the arm definitions ever move White somewhere else.

    The own-side adjacency rule is deliberately NOT applied: the variant places this dragon
    spy-style, ignoring what Black already controls.
    """
    return [square for square in DIAGONAL_SQUARES
            if not Hasher.UNPACK[board[square - 1]][Hasher.OCCUPIED]]


def pick_dragon_square(board, how, depth, rng):
    """Black's own choice of dragon square, or a uniform draw. Returns a 1-based square.

    The search is asked how good the position is for Black *with Black to move*, which is
    exactly the real question -- Black opens. It has to be a search rather than an evaluation
    because `fullCheck` and `entryScore` are both blind to dragons and would tie every
    candidate; see the module docstring.

    Two details that are about determinism rather than strength:

      - the engine state is reset per candidate, so a shared transposition table cannot make
        the answer depend on the order the candidates were tried in;
      - `>` rather than `>=` keeps the first of any tie, and the candidate list is sorted, so
        a tie resolves to the lowest square number rather than to whatever came last.

    `depth` is the game's depth, not more. A deeper picker would make Black's dragon choice
    sharper than Black's actual play -- an agent that does not exist -- and would inflate the
    B - D contrast, which is precisely the quantity that difference is supposed to measure.
    """
    free = free_diagonal_squares(board)
    if not free: return None
    if how == "random": return free[rng.randrange(len(free))]

    best = None
    for square in free:
        candidate = place_dragon(board, square, BLACK)
        fresh(candidate)
        score = AI.chooseMove(candidate, BLACK, depth)[0]
        if best is None or score > best[0]: best = (score, square)

    return best[1]


####### One game #######

def fresh(board):
    """One game's worth of engine state, and none of the previous game's.

    `Engine.koTrack`, `Engine.koGeneration` and the AI's transposition, killer and history
    tables are module-level globals -- fine for one game per process, a correctness hazard for
    two. `AI.newGame()` clears the compiled side as well as the Python one; a reset that
    cleared only Python would carry the wheel's transposition table into the next game and
    answer confidently about a tree belonging to a different game.

    Recording the starting board matters as much as the reset: it is what makes a first move
    that undoes back into the opening position illegal.
    """
    Engine.koReset()
    AI.newGame()
    Engine.koRecord(board)


def play(arm, seed, depth, mode = "heuristic", noise = 0.5, pick_depth = None):
    """Play one game out. Returns a result dict, and is a pure function of its arguments."""
    started = time.monotonic()
    spec = ARMS[arm]
    rng = random.Random((seed << 8) | 0xD6)

    board = enter(start_board(arm), seed, mode, noise)

    dragon_square = None
    if spec["black_late"] is not None:
        dragon_square = pick_dragon_square(board, spec["black_late"],
                                           depth if pick_depth is None else pick_depth, rng)
        if dragon_square is not None:
            board = place_dragon(board, dragon_square, BLACK)

    entered = board
    fresh(board)

    turn = FIRST_TURN
    plies = passes = 0
    evals = []
    winner = None
    termination = None

    while True:
        mover = turn % 2

        # The delayed win, stated as a property of the position exactly the way
        # royals_web/game.py:_advance_turn states it: you have won if, at the start of your
        # OWN turn, your spy, four pawns and royal are still on one square. Gathering does not
        # end the game; surviving the reply does. Checking the raw detector after a move
        # instead -- which is what regress.sweepGames does -- would award the game to a stack
        # that is about to be shattered by a lone spy's push.
        if Hasher.Check_For_Winner(board)[1][mover]:
            winner, termination = mover, "gather"
            break

        board, move, score = AI.takeTurn(board, mover, depth)
        plies += 1
        evals.append(AI.fullCheck(board, WHITE))

        if move is None:
            # takeTurn answers None when every move it has breaks ko. That is a pass, and two
            # in a row is a game neither side can move in.
            passes += 1
            if passes > 1:
                termination = "double_pass"
                break
            turn += 1
            continue

        passes = 0
        Engine.koRecord(board)
        turn += 1

        if plies >= PLY_CAP:
            termination = "ply_cap"
            break

    return {
        "arm": arm, "seed": seed, "depth": depth, "mode": mode, "noise": noise,
        "winner": None if winner is None else SIDE_NAMES[winner],
        "termination": termination,
        "plies": plies,
        "dragon_square": None if dragon_square is None else Hasher.IndexToAlg(dragon_square - 1),
        "evals": evals,
        "entered": N.encode_board(entered),
        "seconds": round(time.monotonic() - started, 3),
    }


####### Running many #######

def _worker(args):
    # TABLE_LIMIT is set here rather than in an initializer because an initializer runs once
    # per worker and this has to hold for every game the worker is handed -- the same reason
    # ai_pool.take_turn sets it on every call.
    AI.TABLE_LIMIT = TABLE_LIMIT
    try:
        return play(*args)
    finally:
        Engine.koReset()
        AI.newGame()


def run(arms, seeds, depths, mode, noise, out, workers, pick_depth):
    jobs = [(arm, seed, depth, mode, noise, pick_depth)
            for depth in depths for arm in arms for seed in range(seeds)]

    header = {
        "kind": "header",
        "accel": _accel.active(),
        "accel_describe": _accel.describe(),
        "arms": {arm: ARMS[arm]["what"] for arm in arms},
        "seeds": seeds, "depths": depths, "mode": mode, "noise": noise,
        "pick_depth": pick_depth, "ply_cap": PLY_CAP, "table_limit": TABLE_LIMIT,
        "python": platform.python_version(), "games": len(jobs),
    }

    started = time.monotonic()
    done = 0
    with open(out, "w") as f:
        f.write(json.dumps(header) + "\n")
        f.flush()

        # One game per task. That is what keeps the engine's module globals honest -- a worker
        # runs a game start to finish and resets around it -- and it is why the result does
        # not depend on how many workers there are: every game is a pure function of its
        # arguments, so the pool only decides what order the lines would come out in. They are
        # written back in job order regardless, so two runs produce identical files.
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            for row in pool.map(_worker, jobs, chunksize=1):
                f.write(json.dumps(row) + "\n")
                done += 1
                if done % 10 == 0 or done == len(jobs):
                    rate = done / max(time.monotonic() - started, 1e-9)
                    left = (len(jobs) - done) / max(rate, 1e-9)
                    sys.stderr.write("\r%d/%d games  %.1f/s  ~%.0fs left   "
                                     % (done, len(jobs), rate, left))
                    sys.stderr.flush()
                    f.flush()

    sys.stderr.write("\n")
    print("wrote %s -- %d games in %.0fs" % (out, len(jobs), time.monotonic() - started))


####### Reading the numbers #######

def wilson(hits, n, z = 1.96):
    """A 95% interval for a proportion. Wilson rather than normal-approximate because the
    interesting arms may sit near 0 or 1 on a subsample and the normal one goes out of range
    there and reports impossible bounds with a straight face."""
    if n == 0: return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar(b, c):
    """Two-sided exact p for a paired binary contrast, from the discordant pairs alone.

    `b` and `c` are the two kinds of disagreement between an arm and the baseline on the same
    seed. Concordant pairs carry no information about a difference and are correctly ignored.
    Exact binomial rather than the chi-square approximation because a pilot can easily produce
    single-digit discordant counts, where the approximation is not to be trusted -- and it is
    a stdlib one-liner, so there is nothing to trade off.
    """
    n = b + c
    if n == 0: return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def report(path):
    rows = []
    header = None
    with open(path) as f:
        for line in f:
            row = json.loads(line)
            if row.get("kind") == "header": header = row
            else: rows.append(row)

    if header:
        print("accel: %s" % header["accel_describe"])
        print("%d games, seeds 0..%d, depths %s, entering %s(%s), ply cap %d"
              % (header["games"], header["seeds"] - 1, header["depths"],
                 header["mode"], header["noise"], header["ply_cap"]))
        print()

    depths = sorted({row["depth"] for row in rows})
    arms = sorted({row["arm"] for row in rows})

    for depth in depths:
        at = [row for row in rows if row["depth"] == depth]
        print("=== depth %d ===" % depth)
        print("%-4s %-7s %-7s %-7s %-18s %-8s %-8s %s"
              % ("arm", "white", "black", "draw", "black win 95% CI",
                 "median", "mean", "capped"))

        base = {row["seed"]: row for row in at if row["arm"] == "A"}

        for arm in arms:
            games = [row for row in at if row["arm"] == arm]
            if not games: continue
            n = len(games)
            w = sum(1 for row in games if row["winner"] == "white")
            b = sum(1 for row in games if row["winner"] == "black")
            d = n - w - b
            lo, hi = wilson(b, n)
            plies = [row["plies"] for row in games]
            capped = sum(1 for row in games if row["termination"] == "ply_cap")
            print("%-4s %-7s %-7s %-7s %-18s %-8s %-8.1f %s"
                  % (arm, "%d (%.1f%%)" % (w, 100 * w / n), "%d (%.1f%%)" % (b, 100 * b / n),
                     d, "%.1f%%-%.1f%%" % (100 * lo, 100 * hi),
                     statistics.median(plies), statistics.mean(plies),
                     "%d (%.1f%%)" % (capped, 100 * capped / n)))

        # Paired against the baseline on the same seed. The seed fixes the Perlin entering
        # field, which is the only source of variance in a deterministic engine, so the pairing
        # is exact rather than approximate and an unpaired test here would be throwing away
        # most of the power the design was built to have.
        if base:
            print()
            print("%-4s %-14s %-14s %s" % ("arm", "d black win", "discordant", "McNemar p"))
            for arm in arms:
                if arm == "A": continue
                games = [row for row in at if row["arm"] == arm]
                pairs = [(row, base[row["seed"]]) for row in games if row["seed"] in base]
                if not pairs: continue
                bb = sum(1 for x, y in pairs if x["winner"] == "black" and y["winner"] != "black")
                cc = sum(1 for x, y in pairs if x["winner"] != "black" and y["winner"] == "black")
                delta = 100 * (bb - cc) / len(pairs)
                print("%-4s %-14s %-14s %.4f"
                      % (arm, "%+.1fpp" % delta, "%d/%d" % (bb, cc), mcnemar(bb, cc)))

        # Where the eval sat, in points, from White's side. Only a shadow of the dragon --
        # the evaluator cannot see one -- so read it as what the six gatherable pieces were
        # doing, which is the thing the dragon is supposed to be influencing.
        print()
        marks = [1, 5, 10, 20, 30, 40]
        print("%-4s %s" % ("arm", "  ".join("ply%-6d" % m for m in marks)))
        for arm in arms:
            games = [row for row in at if row["arm"] == arm]
            if not games: continue
            cells = []
            for m in marks:
                vals = [row["evals"][m - 1] for row in games if len(row["evals"]) >= m]
                cells.append("%-9.2f" % (statistics.mean(vals) / AI.SCALE) if vals else "%-9s" % "-")
            print("%-4s %s" % (arm, " ".join(cells)))

        picks = {}
        for row in at:
            if row["dragon_square"]:
                key = (row["arm"], row["dragon_square"])
                picks.setdefault(key, [0, 0])
                picks[key][0] += 1
                if row["winner"] == "black": picks[key][1] += 1
        if picks:
            print()
            print("black's dragon square (secondary -- thin per square at pilot scale)")
            for arm in arms:
                chosen = sorted(((sq, v) for (a, sq), v in picks.items() if a == arm),
                                key=lambda kv: -kv[1][0])
                if not chosen: continue
                print("  %s  %s" % (arm, "  ".join("%s %d(%d%%)" % (sq, v[0], round(100 * v[1] / v[0]))
                                                   for sq, v in chosen)))
        print()

    print("Read these with three caveats, which are properties of the engine and not of the run:")
    print("  1. The evaluator cannot see dragons, so engine self-play under-uses them and every")
    print("     figure above is a LOWER BOUND on the effect against a dragon-aware player. If the")
    print("     effect grows across the depth ladder, that is the blindness being compensated for")
    print("     by search, and the true effect is larger still.")
    print("  2. Black already wins the baseline on the first-move edge (~56%% at depth 3 in")
    print("     STRATEGY.md). 'Balanced' means the paired delta moves arm A's rate toward 50%%,")
    print("     not that any raw rate is 50%%.")
    print("  3. No significant shift does not mean no effect -- it bounds the effect at roughly")
    print("     the resolution of the seed count above.")


####### CLI #######

def main(argv = None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    subs = parser.add_subparsers(dest="cmd", required=True)

    r = subs.add_parser("run", help="play games and write JSONL")
    r.add_argument("--arms", default="A,B,C,D")
    r.add_argument("--seeds", type=int, default=200)
    r.add_argument("--depth", default="3", help="comma-separated, e.g. 2,3,5")
    r.add_argument("--mode", default="heuristic", choices=("heuristic", "random"))
    r.add_argument("--noise", type=float, default=0.5)
    r.add_argument("--pick-depth", type=int, default=None,
                   help="depth of Black's dragon picker; defaults to the game depth")
    r.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    r.add_argument("--out", required=True)

    p = subs.add_parser("report", help="read a JSONL back")
    p.add_argument("path")

    args = parser.parse_args(argv)

    if args.cmd == "report":
        report(args.path)
        return 0

    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    for arm in arms:
        if arm not in ARMS: parser.error("unknown arm %r; have %s" % (arm, ",".join(sorted(ARMS))))

    if not _accel.active():
        # Not fatal: the pure-Python path is a supported configuration and answers identically.
        # It is about thirty-six times slower, which turns a pilot into an afternoon, so it
        # should be a decision rather than a surprise noticed halfway through.
        sys.stderr.write("warning: the compiled engine is not active -- this will be ~36x slower.\n")

    run(arms, args.seeds, [int(d) for d in args.depth.split(",")],
        args.mode, args.noise, args.out, args.workers, args.pick_depth)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
