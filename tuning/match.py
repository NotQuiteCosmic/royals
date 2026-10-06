"""Playing many games at once, and deciding what they showed.

One process pool, four ways of feeding it:

    match       A against B over a fixed number of openings. Elo and a confidence interval.
    sprt        A against B until the evidence settles it, or a cap is reached.
    tournament  every pair of a field of weight sets, round robin. A Bradley-Terry table.
    gauntlet    a challenger against the pool of past champions, a non-regression SPRT each.

Everything is played in colour-swapped pairs on openings from the book (see openings.py),
one pair per task, so the two halves of a pair land in one line of the results file. The
file is JSONL, appended and flushed a line at a time, and a run that is interrupted can be
started again with the same arguments: pairs already on disk are counted, not replayed.

    pypy3 -m tuning.match sprt --a tuning/pool/champ-001.json --b cand.json --elo0 0 --elo1 10

Workers are long-lived -- PyPy needs a few games to warm its JIT, and the first game out
of each worker is about twice as slow as the rest. A sliding window of tasks keeps every
core busy without waiting for a whole wave to finish before the next starts; the SPRT
checks its bounds every time a pair comes back.
"""

import argparse
import glob
import itertools
import json
import os
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait

import tuning  # noqa: F401
from tuning import openings as O
from tuning import game as G
from tuning import weights as W
from tuning import stats as S

WORKERS = int(os.environ.get("ROYALS_TUNE_WORKERS", "8"))
DEFAULT_BOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "book.jsonl")
POOL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pool")


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------

# A worker that outlives its parent is useless and expensive. Killing a run -- Ctrl-C, a
# kill, a closed terminal -- takes the main process, but a spawned worker only finds out
# when it next touches the result pipe, which is after the pair it is playing: on this
# machine eight orphans ran for most of an hour at full CPU after their parent was gone,
# with nobody to hand their games to. So every worker watches its parent and exits the
# moment it is reparented to init. os._exit, because there is nothing to clean up and a
# normal exit would wait on the pool's own machinery, which is what has just vanished.
def _watch_parent(parent, interval = 2.0):
    while True:
        time.sleep(interval)
        if os.getppid() != parent:
            os._exit(0)


def _worker_init():
    threading.Thread(target = _watch_parent, args = (os.getppid(),), daemon = True).start()


def play_pair(job):
    """Both games of one opening. `job` is a dict; everything in it must pickle."""
    opening = O.Opening.from_row(job["opening"])
    first = G.play_game(opening, job["a"], job["b"], job["depth"], job["ply_cap"], keep_scores=False)
    second = G.play_game(opening, job["b"], job["a"], job["depth"], job["ply_cap"], keep_scores=False)
    return {
        "a_id": first["blue_id"], "b_id": first["red_id"],
        "seed": opening.seed, "opening": job["opening"],
        "depth": job["depth"],
        "games": [first, second],
        "score_a": S.pair_score(first["result_blue"], second["result_blue"]),
        "tag": job.get("tag"),
    }


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

class Runner:
    def __init__(self, workers=WORKERS, out=None):
        self.workers = workers
        self.out_path = out
        self.pool = None

    def __enter__(self):
        self.pool = ProcessPoolExecutor(max_workers=self.workers, initializer=_worker_init)
        return self

    def __exit__(self, *exc):
        self.pool.shutdown(wait=False, cancel_futures=True)

    def run(self, jobs, on_result, window=None):
        """Feed `jobs` (an iterator of job dicts) through the pool, calling on_result(pair)
        for each finished pair in completion order. on_result returning True stops the run:
        no more jobs are submitted and what is in flight is abandoned. Results are appended
        to the output file before on_result sees them."""
        window = window or 2 * self.workers
        out = open(self.out_path, "a") if self.out_path else None
        pending = set()
        stop = False
        try:
            for job in jobs:
                pending.add(self.pool.submit(play_pair, job))
                if len(pending) < window: continue
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    if self._deliver(fut.result(), out, on_result): stop = True
                if stop: break
            while pending and not stop:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    if self._deliver(fut.result(), out, on_result): stop = True
        finally:
            if out: out.close()
            for fut in pending: fut.cancel()

    @staticmethod
    def _deliver(pair, out, on_result):
        if out:
            out.write(json.dumps(pair, sort_keys=True) + "\n")
            out.flush()
        return bool(on_result(pair))


def previous_pairs(path, a_id, b_id, tag=None):
    """Pairs already on disk for this match-up, so a restarted run carries on."""
    found = []
    if not path or not os.path.exists(path): return found
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            pair = json.loads(line)
            if pair["a_id"] == a_id and pair["b_id"] == b_id and pair.get("tag") == tag:
                found.append(pair)
    return found


def jobs_for(book_rows, a, b, depth, ply_cap, skip_seeds=(), tag=None):
    for row in book_rows:
        if row["seed"] in skip_seeds: continue
        yield {"opening": row, "a": a, "b": b, "depth": depth, "ply_cap": ply_cap, "tag": tag}


def load_rows(path, limit=None):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line: rows.append(json.loads(line))
            if limit and len(rows) >= limit: break
    return rows


# ---------------------------------------------------------------------------
# Tallies and reports
# ---------------------------------------------------------------------------

class Tally:
    def __init__(self, pairs=()):
        self.scores = []
        self.plies = 0
        self.games = 0
        self.caps = 0
        self.passes = 0
        self.seconds = 0.0
        self.started = time.monotonic()
        for pair in pairs: self.add(pair)

    def add(self, pair):
        self.scores.append(pair["score_a"])
        for g in pair["games"]:
            self.games += 1
            self.plies += g["plies"]
            self.seconds += g["seconds"]
            if g["termination"] == "ply_cap": self.caps += 1
            elif g["termination"] == "double_pass": self.passes += 1

    @property
    def pent(self):
        return S.pentanomial(self.scores)

    def summary(self, elo0=None, elo1=None):
        n = len(self.scores)
        e, low, high = S.elo_ci(self.pent)
        text = "%d pairs  pent %s  elo %+.1f [%+.1f, %+.1f]" % (n, self.pent, e, low, high)
        if elo0 is not None:
            llr = S.sprt_llr(self.pent, elo0, elo1)
            text += "  llr %s" % ("n/a" if llr is None else "%+.2f" % llr)
        if self.games:
            text += "  caps %d/%d  passes %d  mean plies %.0f" % (
                self.caps, self.games, self.passes, self.plies / self.games)
        return text


def progress(text):
    sys.stderr.write("\r" + text[:160].ljust(160))
    sys.stderr.flush()


def line(text):
    sys.stderr.write("\n" if sys.stderr.isatty() else "")
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# The four modes
# ---------------------------------------------------------------------------

def run_match(args, a, b, tag=None, elo0=None, elo1=None, max_pairs=None, stop_rule=None):
    """The common loop: resume, play, tally, report. stop_rule(tally) -> True ends it."""
    a_id, b_id = W.weight_id(a), W.weight_id(b)
    if a_id == b_id:
        line("both sides are the same weights (%s): nothing to measure" % a_id)
        return None

    rows = load_rows(args.book)
    earlier = previous_pairs(args.out, a_id, b_id, tag)
    tally = Tally(earlier)
    if earlier: line("resuming: %d pairs already on disk" % len(earlier))

    want = max_pairs if max_pairs is not None else args.pairs
    if len(tally.scores) >= want:
        line(tally.summary(elo0, elo1))
        return tally
    if want > len(rows):
        line("book has %d openings, run asks for %d: capped" % (len(rows), want))
        want = len(rows)

    jobs = itertools.islice(
        jobs_for(rows, a, b, args.depth, args.ply_cap, {p["seed"] for p in earlier}, tag),
        want - len(tally.scores))

    def on_result(pair):
        tally.add(pair)
        n = len(tally.scores)
        if n % 8 == 0 or n == want:
            rate = n / max(time.monotonic() - tally.started, 1e-9)
            progress("%s  (%.2f pairs/s)" % (tally.summary(elo0, elo1), rate))
        return stop_rule(tally) if stop_rule else False

    with Runner(args.workers, args.out) as runner:
        runner.run(jobs, on_result)
    line(tally.summary(elo0, elo1))
    return tally


def cmd_match(args):
    a, b = W.load(args.a), W.load(args.b)
    run_match(args, a, b, tag="match")


def cmd_sprt(args):
    a, b = W.load(args.a), W.load(args.b)
    return sprt(args, a, b, args.elo0, args.elo1, args.max_pairs, tag="sprt")


def sprt(args, a, b, elo0, elo1, max_pairs, tag):
    verdict = {"status": None}

    def stop_rule(tally):
        status = S.sprt_status(tally.pent, elo0, elo1, args.alpha, args.beta)
        if status in ("H1", "H0"):
            verdict["status"] = status
            return True
        return False

    tally = run_match(args, a, b, tag=tag, elo0=elo0, elo1=elo1, max_pairs=max_pairs,
                      stop_rule=stop_rule)
    if tally is None: return None
    status = verdict["status"] or S.sprt_status(tally.pent, elo0, elo1, args.alpha, args.beta)
    if status not in ("H1", "H0"): status = "inconclusive"
    line("SPRT [%g, %g]: %s after %d pairs" % (elo0, elo1, status, len(tally.scores)))
    return status, tally


def cmd_tournament(args):
    players = [(os.path.splitext(os.path.basename(p))[0], W.load(p)) for p in args.players]
    rows = load_rows(args.book, args.openings)
    games = []
    tallies = {}

    for (name_a, a), (name_b, b) in itertools.combinations(players, 2):
        a_id, b_id = W.weight_id(a), W.weight_id(b)
        tag = "tournament"
        earlier = previous_pairs(args.out, a_id, b_id, tag)
        tally = Tally(earlier)
        jobs = jobs_for(rows, a, b, args.depth, args.ply_cap, {p["seed"] for p in earlier}, tag)

        def on_result(pair, tally=tally, name_a=name_a, name_b=name_b):
            tally.add(pair)
            progress("%s v %s  %s" % (name_a, name_b, tally.summary()))
            return False

        with Runner(args.workers, args.out) as runner:
            runner.run(jobs, on_result)
        line("%-16s v %-16s %s" % (name_a, name_b, tally.summary()))
        tallies[(name_a, name_b)] = tally
        for score in tally.scores:
            # a pair is two games; split its score back into two results for the model
            games.append((name_a, name_b, min(1.0, score * 2)))
            games.append((name_a, name_b, max(0.0, score * 2 - 1.0)))

    ratings = S.bradley_terry(games)
    line("")
    line("%-16s %8s %6s %7s" % ("player", "elo", "games", "score"))
    for name, (elo, played, frac) in sorted(ratings.items(), key=lambda kv: -kv[1][0]):
        line("%-16s %+8.1f %6d %6.1f%%" % (name, elo, played, 100 * frac))
    if args.table:
        with open(args.table, "w") as f:
            json.dump({name: {"elo": r[0], "games": r[1], "score": r[2]}
                       for name, r in ratings.items()}, f, indent=2, sort_keys=True)


def champions(pool_dir, recent=3):
    """The evaluator as it stood before any tuning (original.json), which stays in every
    gauntlet forever, plus the most recent champions by name. champ-001.json is what shipped
    after the first tuning and is also what ai.DEFAULT_WEIGHTS now holds."""
    files = [os.path.join(pool_dir, "original.json")]
    champs = sorted(glob.glob(os.path.join(pool_dir, "champ-*.json")))
    files += champs[-recent:]
    return [f for f in files if os.path.exists(f)]


def cmd_gauntlet(args):
    challenger = W.load(args.challenger)
    opponents = args.opponents or champions(args.pool_dir)
    if not opponents:
        line("no opponents: nothing in %s" % args.pool_dir)
        return
    results = {}
    for path in opponents:
        name = os.path.splitext(os.path.basename(path))[0]
        line("--- gauntlet: challenger v %s" % name)
        got = sprt(args, challenger, W.load(path), args.elo0, args.elo1, args.max_pairs,
                   tag="gauntlet:" + name)
        results[name] = got[0] if got else "same"
    failed = [n for n, s in results.items() if s == "H0"]
    open_ = [n for n, s in results.items() if s == "inconclusive"]
    line("")
    for name, status in results.items(): line("%-20s %s" % (name, status))
    if failed: line("GAUNTLET FAILED: regressed against %s" % ", ".join(failed))
    elif open_: line("GAUNTLET OPEN: inconclusive against %s" % ", ".join(open_))
    else: line("GAUNTLET PASSED")


# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    def common(p):
        p.add_argument("--book", default=DEFAULT_BOOK)
        p.add_argument("--out", default=None, help="JSONL results file (default: tuning/results/<mode>.jsonl)")
        p.add_argument("--depth", type=int, default=3)
        p.add_argument("--ply-cap", type=int, default=G.PLY_CAP)
        p.add_argument("--workers", type=int, default=WORKERS)

    def sprt_args(p):
        p.add_argument("--elo0", type=float, default=0.0)
        p.add_argument("--elo1", type=float, default=10.0)
        p.add_argument("--alpha", type=float, default=0.05)
        p.add_argument("--beta", type=float, default=0.05)
        p.add_argument("--max-pairs", type=int, default=6000)

    p = sub.add_parser("match"); common(p)
    p.add_argument("--a", required=True); p.add_argument("--b", required=True)
    p.add_argument("--pairs", type=int, default=500)
    p.set_defaults(fn=cmd_match)

    p = sub.add_parser("sprt"); common(p); sprt_args(p)
    p.add_argument("--a", required=True); p.add_argument("--b", required=True)
    p.set_defaults(fn=cmd_sprt, pairs=None)

    p = sub.add_parser("tournament"); common(p)
    p.add_argument("--players", nargs="+", required=True)
    p.add_argument("--openings", type=int, default=200)
    p.add_argument("--table", default=None, help="write the rating table here as JSON")
    p.set_defaults(fn=cmd_tournament, pairs=None)

    p = sub.add_parser("gauntlet"); common(p); sprt_args(p)
    p.set_defaults(elo0=-10.0, elo1=0.0)
    p.add_argument("--challenger", required=True)
    p.add_argument("--opponents", nargs="*", default=None)
    p.add_argument("--pool-dir", default=POOL_DIR)
    p.set_defaults(fn=cmd_gauntlet, pairs=None)

    args = parser.parse_args(argv)
    if args.out is None:
        results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
        os.makedirs(results_dir, exist_ok=True)
        args.out = os.path.join(results_dir, args.mode + ".jsonl")
    args.fn(args)


if __name__ == "__main__":
    main()
