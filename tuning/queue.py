"""A list of measurements that finishes itself, however many times it is interrupted.

The matches and SPSA runs in this package were always resumable one at a time: a match
skips the openings already in its results file, SPSA restarts from its last checkpoint.
What was not resumable was the ORDER of them -- a shell script in a scratch directory,
started from a session, gone when the machine went. This replaces the script.

    tuning/queue.jsonl                  the jobs, one per line, in order; tracked in git
    tuning/results/verdicts.jsonl       one line per finished job; tracked in git
    pypy3 -m tuning.queue run           do whatever is left, in order
    pypy3 -m tuning.queue status        say what is done and what is left
    pypy3 -m tuning.queue add '{...}'   append a job

Whether a job is finished is worked out from the results on disk every time -- the pairs in
the match's file, the SPRT verdict those pairs imply, the iteration count in the SPSA
checkpoint -- never from a marker that a crash could leave half-written. So `run` after a
crash, a reboot or a closed terminal continues from exactly where things stopped, and `run`
on a finished queue does nothing. Start it with nohup so the shell going away doesn't take
it along:

    nohup pypy3 -m tuning.queue run > tuning/results/queue.log 2>&1 &

Job lines:

    {"id": "d5", "mode": "match", "a": "tuning/pool/champ-001.json",
     "b": "tuning/pool/original.json", "depth": 5, "pairs": 400, "ply_cap": 600,
     "out": "tuning/results/depth5.jsonl"}
    {"id": "d4", "mode": "sprt", ..., "elo0": 0, "elo1": 5, "max_pairs": 1500}
    {"id": "run3", "mode": "spsa", "name": "run3", "iterations": 20000, "depth": 3,
     "params": [...], "c": {"NAME": 61}}

`tag` defaults to the mode name, which is what the match.py command line writes, so a job
can adopt pairs an earlier hand-run match left in the same file.
"""

import argparse
import json
import os
import sys
import time
import types

import tuning  # noqa: F401
from tuning import match as M
from tuning import weights as W
from tuning import stats as S
from tuning import spsa as P
from tuning import game as G

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
QUEUE_PATH = os.path.join(HERE, "queue.jsonl")
VERDICTS_PATH = os.path.join(HERE, "results", "verdicts.jsonl")


def _abs(path):
    return path if os.path.isabs(path) else os.path.join(REPO, path)


def load_jobs(path=QUEUE_PATH):
    jobs = []
    if not os.path.exists(path): return jobs
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"): continue
            job = json.loads(line)
            if "id" not in job or "mode" not in job:
                raise ValueError("%s line %d: a job needs an id and a mode" % (path, n))
            jobs.append(job)
    ids = [j["id"] for j in jobs]
    if len(ids) != len(set(ids)): raise ValueError("duplicate job ids in %s" % path)
    return jobs


def load_verdicts(path=VERDICTS_PATH):
    out = {}
    if not os.path.exists(path): return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                v = json.loads(line)
                out[v["id"]] = v
    return out


def _args(job, workers):
    """The namespace match.py's functions read their settings from."""
    return types.SimpleNamespace(
        book=_abs(job.get("book", M.DEFAULT_BOOK)),
        out=_abs(job["out"]),
        depth=job.get("depth", 3),
        ply_cap=job.get("ply_cap", G.PLY_CAP),
        workers=workers,
        pairs=job.get("pairs"),
        alpha=job.get("alpha", 0.05),
        beta=job.get("beta", 0.05),
    )


def _pairs_on_disk(job):
    a, b = W.load(_abs(job["a"])), W.load(_abs(job["b"]))
    tag = job.get("tag", job["mode"])
    return M.previous_pairs(_abs(job["out"]), W.weight_id(a), W.weight_id(b), tag)


def progress_of(job):
    """(finished?, summary dict) from the results on disk alone."""
    mode = job["mode"]
    if mode in ("match", "sprt"):
        pairs = _pairs_on_disk(job)
        pent = S.pentanomial([p["score_a"] for p in pairs])
        elo, low, high = S.elo_ci(pent)
        info = {"pairs": len(pairs), "pentanomial": pent, "elo": round(elo, 1),
                "ci": [round(low, 1), round(high, 1)]}
        if mode == "match":
            info["target"] = job["pairs"]
            return len(pairs) >= job["pairs"], info
        status = S.sprt_status(pent, job["elo0"], job["elo1"], job.get("alpha", 0.05), job.get("beta", 0.05))
        info["target"] = job.get("max_pairs", 6000)
        info["sprt"] = status
        if status in ("H1", "H0"): return True, info
        if len(pairs) >= info["target"]:
            info["sprt"] = "inconclusive"
            return True, info
        return False, info
    if mode == "spsa":
        state = os.path.join(P.RUNS_DIR, job["name"], "state.json")
        done = 0
        if os.path.exists(state):
            with open(state) as f: done = json.load(f)["done"]
        return done >= job["iterations"], {"iterations": done, "target": job["iterations"]}
    raise ValueError("unknown mode %r" % (mode,))


def run_job(job, workers):
    mode = job["mode"]
    if mode in ("match", "sprt"):
        args = _args(job, workers)
        a, b = W.load(_abs(job["a"])), W.load(_abs(job["b"]))
        tag = job.get("tag", mode)
        if mode == "match":
            M.run_match(args, a, b, tag=tag)
        else:
            M.sprt(args, a, b, job["elo0"], job["elo1"], job.get("max_pairs", 6000), tag)
        return
    if mode == "spsa":
        state = os.path.join(P.RUNS_DIR, job["name"], "state.json")
        if os.path.exists(state):
            run = P.SPSA.load(job["name"])
        else:
            base = W.load(_abs(job["base"])) if job.get("base") else W.default()
            run = P.SPSA(job["name"], base, job.get("params") or list(P.AI.WEIGHT_NAMES),
                         job["iterations"], job.get("depth", 3), job.get("c"),
                         job.get("r_end", P.R_END), job.get("seed", 1),
                         _abs(job.get("book", M.DEFAULT_BOOK)), job.get("ply_cap", G.PLY_CAP))
        run.run(workers)
        return
    raise ValueError("unknown mode %r" % (mode,))


def record_verdict(job, info, path=VERDICTS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    verdict = {"id": job["id"], "mode": job["mode"],
               "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    verdict.update(info)
    with open(path, "a") as f:
        f.write(json.dumps(verdict, sort_keys=True) + "\n")
    return verdict


def run(queue_path=QUEUE_PATH, verdicts_path=VERDICTS_PATH, workers=M.WORKERS):
    """Finish every unfinished job, in order. Returns the verdicts recorded this time."""
    recorded = []
    for job in load_jobs(queue_path):
        verdicts = load_verdicts(verdicts_path)
        finished, info = progress_of(job)
        if not finished:
            M.line("=== %s: %s, continuing from %s" % (job["id"], job["mode"], json.dumps(info)))
            run_job(job, workers)
            finished, info = progress_of(job)
            if not finished:
                M.line("=== %s: stopped before finishing (%s)" % (job["id"], json.dumps(info)))
                break
        if job["id"] not in verdicts:
            recorded.append(record_verdict(job, info, verdicts_path))
            M.line("=== %s: done %s" % (job["id"], json.dumps(info)))
    return recorded


def status(queue_path=QUEUE_PATH, verdicts_path=VERDICTS_PATH):
    verdicts = load_verdicts(verdicts_path)
    for job in load_jobs(queue_path):
        finished, info = progress_of(job)
        state = "done" if finished else "pending"
        if finished and job["id"] not in verdicts: state = "done, verdict not yet recorded"
        M.line("%-18s %-6s %-8s %s" % (job["id"], job["mode"], state, json.dumps(info)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); p.add_argument("--workers", type=int, default=M.WORKERS)
    p.add_argument("--queue", default=QUEUE_PATH); p.add_argument("--verdicts", default=VERDICTS_PATH)
    p = sub.add_parser("status")
    p.add_argument("--queue", default=QUEUE_PATH); p.add_argument("--verdicts", default=VERDICTS_PATH)
    p = sub.add_parser("add"); p.add_argument("job", help="one job as JSON")
    p.add_argument("--queue", default=QUEUE_PATH)
    args = parser.parse_args(argv)

    if args.cmd == "run":
        run(args.queue, args.verdicts, args.workers)
    elif args.cmd == "status":
        status(args.queue, args.verdicts)
    else:
        job = json.loads(args.job)
        if "id" not in job or "mode" not in job: sys.exit("a job needs an id and a mode")
        if job["id"] in {j["id"] for j in load_jobs(args.queue)}: sys.exit("id %r is already queued" % job["id"])
        with open(args.queue, "a") as f: f.write(json.dumps(job, sort_keys=True) + "\n")
        M.line("queued %s" % job["id"])


if __name__ == "__main__":
    main()
