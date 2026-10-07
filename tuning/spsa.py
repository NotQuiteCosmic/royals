"""Tuning the weights by SPSA over paired games.

Simultaneous perturbation stochastic approximation, the way fishtest tunes Stockfish. Each
iteration flips a coin per parameter, nudges every weight up or down by its probe size c at
once, and plays the two nudged versions against each other on a fresh opening, both colours.
Whichever side won, every parameter is moved a little in the direction that side had it.
One pair of games moves all the parameters at once; a parameter that doesn't matter gets
pushed about at random and goes nowhere, one that does gets pushed the same way more often
than not. Thousands of iterations later the random pushes have cancelled and the real ones
have added up.

    pypy3 -m tuning.spsa --name retune --iterations 20000 --depth 3
    pypy3 -m tuning.spsa --name retune --resume

The schedules are the standard ones:

    c_k = c_i / k^0.101                    the probe size, shrinking slowly
    a_k = a_i / (A + k)^0.602               the step size, shrinking faster
    A   = 0.1 * iterations
    a_i chosen so that a_end / c_end^2 = R  (R = 0.002, fishtest's default)

with the update, for a pair that scored s for the plus side (0 to 1):

    theta_i += a_k * (2 s - 1) * delta_i / (2 c_k)

Per-parameter c_i defaults to about an eighth of the parameter's default value, never
below 2 -- below that the two nudged versions round to the same integers and the pair
measures nothing -- and can be set by hand with --c NAME=value, which is how a term that
defaults to 0 gets a probe size at all.

theta lives in floats here; the engine only ever sees it rounded to whole numbers and
clamped to ai.WEIGHT_RANGES. Workers run asynchronously -- a job is drawn from the theta of
the moment it is submitted and applied when it comes back -- which is what everybody does
and costs nothing measurable at eight workers.

State is checkpointed to tuning/runs/<name>/state.json after every batch of results and the
trajectory to trajectory.jsonl, so a run can be stopped and resumed and its path plotted.
The rounded final theta is written to theta.json as a weight file ready for match.py.
"""

import argparse
import json
import math
import os
import random
import sys
import time

import tuning  # noqa: F401
from tuning import match as M
from tuning import weights as W
from tuning import game as G

from royals_engine import ai as AI

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")

ALPHA = 0.602
GAMMA = 0.101
R_END = 0.002
C_FRACTION = 0.125
C_MIN = 2.0


def default_c(name, base):
    value = abs(base[name])
    return max(C_MIN, C_FRACTION * value)


class SPSA:
    def __init__(self, name, base, params, iterations, depth, c_overrides=None, r_end=R_END,
                 seed=1, book=M.DEFAULT_BOOK, ply_cap=G.PLY_CAP):
        self.name = name
        self.base = W.complete(base)
        self.params = list(params)
        self.iterations = iterations
        self.depth = depth
        self.book = book
        self.ply_cap = ply_cap
        self.A = 0.1 * iterations
        self.c = {p: float((c_overrides or {}).get(p, default_c(p, self.base))) for p in self.params}
        # a_end / c_end^2 = R  with  a_end = a_i / (A + N)^alpha  and  c_end = c_i / N^gamma
        self.a = {p: r_end * (self.c[p] / iterations ** GAMMA) ** 2 * (self.A + iterations) ** ALPHA
                  for p in self.params}
        self.theta = {p: float(self.base[p]) for p in self.params}
        self.k = 0            # iterations submitted
        self.done = 0         # iterations whose result has been applied
        self.rng = random.Random(seed)
        self.seed = seed
        self.dir = os.path.join(RUNS_DIR, name)

    # -- the schedule -------------------------------------------------------

    def c_k(self, p, k):
        return self.c[p] / k ** GAMMA

    def a_k(self, p, k):
        return self.a[p] / (self.A + k) ** ALPHA

    def rounded(self, theta=None):
        theta = self.theta if theta is None else theta
        out = dict(self.base)
        for p in self.params:
            low, high = AI.WEIGHT_RANGES[p]
            out[p] = int(min(high, max(low, round(theta[p]))))
        return out

    # -- jobs and updates --------------------------------------------------

    def jobs(self, rows):
        while self.k < self.iterations:
            self.k += 1
            k = self.k
            delta = {p: self.rng.choice((-1, 1)) for p in self.params}
            plus = dict(self.theta)
            minus = dict(self.theta)
            for p in self.params:
                plus[p] += self.c_k(p, k) * delta[p]
                minus[p] -= self.c_k(p, k) * delta[p]
            plus, minus = self.rounded(plus), self.rounded(minus)
            if plus == minus:
                # every probe rounded away; counts as an iteration that learned nothing
                self.done += 1
                continue
            row = rows[(k - 1) % len(rows)]
            yield {"opening": row, "a": plus, "b": minus, "depth": self.depth,
                   "ply_cap": self.ply_cap, "tag": {"k": k, "delta": delta}}

    def apply(self, pair):
        k = pair["tag"]["k"]
        delta = pair["tag"]["delta"]
        r = 2.0 * pair["score_a"] - 1.0
        for p in self.params:
            low, high = AI.WEIGHT_RANGES[p]
            step = self.a_k(p, k) * r * delta[p] / (2.0 * self.c_k(p, k))
            self.theta[p] = min(float(high), max(float(low), self.theta[p] + step))
        self.done += 1

    # -- persistence -----------------------------------------------------

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        state = {
            "name": self.name, "base": self.base, "params": self.params,
            "iterations": self.iterations, "depth": self.depth, "book": self.book,
            "ply_cap": self.ply_cap, "c": self.c, "a": self.a, "theta": self.theta,
            "k": self.k, "done": self.done, "seed": self.seed, "rng": self.rng.getstate(),
        }
        tmp = os.path.join(self.dir, "state.json.tmp")
        with open(tmp, "w") as f: json.dump(state, f, indent=1, sort_keys=True)
        os.replace(tmp, os.path.join(self.dir, "state.json"))
        W.save(self.rounded(), os.path.join(self.dir, "theta.json"))
        with open(os.path.join(self.dir, "trajectory.jsonl"), "a") as f:
            f.write(json.dumps({"done": self.done, "theta": self.rounded()}, sort_keys=True) + "\n")

    @classmethod
    def load(cls, name):
        path = os.path.join(RUNS_DIR, name, "state.json")
        with open(path) as f: state = json.load(f)
        run = cls(state["name"], state["base"], state["params"], state["iterations"],
                  state["depth"], state["c"], seed=state["seed"], book=state["book"],
                  ply_cap=state["ply_cap"])
        run.a = state["a"]
        run.theta = state["theta"]
        run.k = state["k"]
        run.done = state["done"]
        rng_state = state["rng"]
        # json turns the tuple-of-tuples into lists; Random wants it back as tuples
        run.rng.setstate((rng_state[0], tuple(rng_state[1]), rng_state[2]))
        # anything submitted but not applied when the run stopped is replayed
        run.k = run.done
        return run

    # -- running ----------------------------------------------------------

    def run(self, workers=M.WORKERS, checkpoint_every=50):
        rows = M.load_rows(self.book)
        os.makedirs(self.dir, exist_ok=True)
        pairs_path = os.path.join(self.dir, "pairs.jsonl")
        started = time.monotonic()
        start_done = self.done
        last_save = [self.done]

        def on_result(pair):
            self.apply(pair)
            if self.done - last_save[0] >= checkpoint_every:
                self.save()
                last_save[0] = self.done
            rate = (self.done - start_done) / max(time.monotonic() - started, 1e-9)
            left = (self.iterations - self.done) / max(rate, 1e-9)
            M.progress("%d/%d  %.2f it/s  eta %dm  %s" % (
                self.done, self.iterations, rate, left / 60, self.describe()))
            return False

        with M.Runner(workers, pairs_path) as runner:
            runner.run(self.jobs(rows), on_result)
        self.save()
        M.line("")
        M.line(self.report())

    def describe(self):
        return " ".join("%s=%d" % (p[:8], round(self.theta[p])) for p in self.params)

    def report(self):
        lines = ["%-22s %8s %8s %8s" % ("parameter", "start", "now", "c")]
        for p in self.params:
            lines.append("%-22s %8d %8d %8.1f" % (p, self.base[p], round(self.theta[p]), self.c[p]))
        lines.append("theta written to %s" % os.path.join(self.dir, "theta.json"))
        return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="tune evaluation weights by SPSA")
    parser.add_argument("--name", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--base", default=None, help="weights to start from (default: ai.DEFAULT_WEIGHTS)")
    parser.add_argument("--params", nargs="*", default=None, help="weights to tune (default: all)")
    parser.add_argument("--iterations", type=int, default=20000)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--c", nargs="*", default=[], help="probe sizes, NAME=value")
    parser.add_argument("--r-end", type=float, default=R_END)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--book", default=M.DEFAULT_BOOK)
    parser.add_argument("--ply-cap", type=int, default=G.PLY_CAP)
    parser.add_argument("--workers", type=int, default=M.WORKERS)
    args = parser.parse_args(argv)

    if args.resume:
        run = SPSA.load(args.name)
        M.line("resuming %s at iteration %d of %d" % (args.name, run.done, run.iterations))
    else:
        if os.path.exists(os.path.join(RUNS_DIR, args.name, "state.json")):
            sys.exit("run %r exists: pass --resume to continue it or pick another name" % args.name)
        params = args.params or list(AI.WEIGHT_NAMES)
        overrides = {}
        for item in args.c:
            name, value = item.split("=")
            overrides[name] = float(value)
        base = W.load(args.base) if args.base else W.default()
        run = SPSA(args.name, base, params, args.iterations, args.depth, overrides,
                   args.r_end, args.seed, args.book, args.ply_cap)
        M.line(run.report())
    run.run(args.workers)




# ---------------------------------------------------------------------------
# Batched SPSA for many machines that cannot talk to each other
# ---------------------------------------------------------------------------
# The loop above is one process feeding one pool. On GitHub Actions there are twenty jobs
# and no shared memory, so the loop is cut in three: `init` writes the starting state;
# `wave` is what one job does -- load theta, draw its own perturbations, play its pairs,
# write them out, touch nothing; `apply` folds every wave's pairs into theta and advances
# the checkpoint. Every probe in a wave is drawn from the same theta, which is batched
# SPSA with a batch of N x k; the sequential version differs only in using each pair's
# result before drawing the next, and with twenty asynchronous workers it was already
# most of a batch behind. Waves are tagged with the iteration they were drawn from, so a
# stale wave (one drawn before an `apply` it did not know about) is refused rather than
# applied to a theta it was not probing.

def wave_jobs(run, rows, pairs, rng):
    """`pairs` jobs drawn from run.theta as it stands, with iteration indices starting at
    run.done + 1. The opening offset moves with run.done so successive waves on the same
    shard do not replay the same openings."""
    jobs = []
    for j in range(pairs):
        k = run.done + 1 + j
        delta = {p: rng.choice((-1, 1)) for p in run.params}
        plus = dict(run.theta)
        minus = dict(run.theta)
        for p in run.params:
            plus[p] += run.c_k(p, k) * delta[p]
            minus[p] -= run.c_k(p, k) * delta[p]
        plus, minus = run.rounded(plus), run.rounded(minus)
        if plus == minus: continue
        row = rows[(run.done + j) % len(rows)]
        jobs.append({"opening": row, "a": plus, "b": minus, "depth": run.depth,
                     "ply_cap": run.ply_cap, "tag": {"k": k, "delta": delta, "from": run.done}})
    return jobs


def cmd_init(args):
    if os.path.exists(os.path.join(RUNS_DIR, args.name, "state.json")):
        sys.exit("run %r exists" % args.name)
    overrides = {}
    for item in args.c:
        name, value = item.split("=")
        overrides[name] = float(value)
    base = W.load(args.base) if args.base else W.default()
    run = SPSA(args.name, base, args.params or list(AI.WEIGHT_NAMES), args.iterations, args.depth,
               overrides, args.r_end, args.seed, args.book, args.ply_cap)
    run.save()
    M.line(run.report())


def cmd_wave(args):
    run = SPSA.load(args.name)
    if run.done >= run.iterations:
        M.line("run %s is complete (%d iterations): nothing to do" % (args.name, run.done))
        return
    rows = M.shard_rows(M.load_rows(run.book), args.shard)
    # seeded by the wave, the shard and the checkpoint, so no two shards -- and no two
    # waves -- draw the same perturbations
    rng = random.Random("%s:%s:%d" % (args.seed, args.shard or "0/1", run.done))
    pairs = min(args.pairs, run.iterations - run.done)
    jobs = wave_jobs(run, rows, pairs, rng)
    played = [0]

    def on_result(pair):
        played[0] += 1
        M.progress("wave %s: %d/%d pairs" % (args.shard or "0/1", played[0], len(jobs)))
        return False

    with M.Runner(args.workers, args.out) as runner:
        runner.run(iter(jobs), on_result)
    M.line("")
    M.line("wave %s from iteration %d: %d pairs -> %s" % (args.shard or "0/1", run.done, played[0], args.out))


def cmd_apply(args):
    run = SPSA.load(args.name)
    pairs = M.merge_pairs(args.inputs)
    fresh = [p for p in pairs if p["tag"].get("from") == run.done]
    stale = len(pairs) - len(fresh)
    if stale:
        M.line("ignoring %d pairs drawn from iteration %d or earlier (state is at %d)"
               % (stale, max(p["tag"].get("from", -1) for p in pairs), run.done))
    before = run.done
    for pair in sorted(fresh, key=lambda p: p["tag"]["k"]):
        run.apply(pair)
    run.k = run.done
    run.save()
    M.line("applied %d pairs: iteration %d -> %d of %d" % (len(fresh), before, run.done, run.iterations))
    M.line(run.report())
    return len(fresh)


def main_batched(argv):
    parser = argparse.ArgumentParser(description="batched SPSA: init, wave, apply")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--name", required=True)
    p.add_argument("--base", default=None)
    p.add_argument("--params", nargs="*", default=None)
    p.add_argument("--iterations", type=int, default=20000)
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--c", nargs="*", default=[])
    p.add_argument("--r-end", type=float, default=R_END)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--book", default=M.DEFAULT_BOOK)
    p.add_argument("--ply-cap", type=int, default=G.PLY_CAP)
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("wave")
    p.add_argument("--name", required=True)
    p.add_argument("--shard", default=None)
    p.add_argument("--pairs", type=int, default=50)
    p.add_argument("--seed", default="0", help="the wave's seed, e.g. the workflow run id")
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=M.WORKERS)
    p.set_defaults(fn=cmd_wave)

    p = sub.add_parser("apply")
    p.add_argument("--name", required=True)
    p.add_argument("inputs", nargs="+", help="wave pair files")
    p.set_defaults(fn=cmd_apply)

    args = parser.parse_args(argv)
    args.fn(args)


_sequential_main = main


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("init", "wave", "apply"):
        return main_batched(argv)
    return _sequential_main(argv)


if __name__ == "__main__":
    main()
