"""Dispatch tuning workflows on GitHub Actions without a shell in the way, and see what
they did.

    python3 -m tuning.ci.dispatch match --depth 4 --pairs 250 --shards 20 --tag d4-5000
    python3 -m tuning.ci.dispatch gauntlet --challenger tuning/pool/champ-002.json --depth 3
    python3 -m tuning.ci.dispatch spsa --name run3 --iterations 20000 --params SPY_DIST_WEIGHT ...
    python3 -m tuning.ci.dispatch book --count 500 --shards 20
    python3 -m tuning.ci.dispatch status            # recent tune-* runs and their verdicts

Why a script: the first batch of Step-6 runs was dispatched from a zsh loop, and zsh does
not word-split an unquoted variable, so every run received "4 250 d4-5000" as its depth and
eighty shards failed on argument parsing. Arguments here are named, typed and passed to
`gh` as a list, and the exact command is printed before it runs. GitHub's API also fell
over for ten minutes that day, so a dispatch is retried on a 5xx.

`status` reads the run list from `gh` and, for finished runs, the report the workflow
committed to the tuning-results branch, so a result can be read without opening a browser.
"""

import argparse
import json
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_A = "tuning/pool/champ-001.json"
DEFAULT_B = "tuning/pool/original.json"
RESULTS_BRANCH = "tuning-results"


def gh(*args, retries=5, capture=True):
    """Run gh, retrying on server errors. Returns stdout."""
    cmd = ["gh"] + list(args)
    for attempt in range(1, retries + 1):
        proc = subprocess.run(cmd, cwd=REPO_ROOT, text=True,
                              stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            return proc.stdout or ""
        err = (proc.stderr or "").strip()
        transient = ("HTTP 5" in err or "Internal Server Error" in err or "502" in err or "503" in err
                     or "error connecting" in err or "connection reset" in err.lower() or "timeout" in err.lower())
        if not transient or attempt == retries:
            sys.exit("gh failed: %s\n%s" % (" ".join(cmd), err))
        wait = 30 * attempt
        print("gh: server error, retrying in %ds (%d/%d)" % (wait, attempt, retries), file=sys.stderr)
        time.sleep(wait)


def current_branch():
    return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT,
                          text=True, stdout=subprocess.PIPE, check=True).stdout.strip()


def dispatch(workflow, ref, inputs):
    args = ["workflow", "run", workflow, "--ref", ref]
    for key, value in inputs.items():
        args += ["-f", "%s=%s" % (key, value)]
    print("gh " + " ".join(args))
    gh(*args)
    # the run takes a moment to appear; find the newest run of this workflow on this ref
    time.sleep(8)
    out = gh("run", "list", "--workflow", workflow, "--branch", ref, "--limit", "1",
             "--json", "databaseId,url,status")
    runs = json.loads(out or "[]")
    if runs:
        print("run %s %s %s" % (runs[0]["databaseId"], runs[0]["status"], runs[0]["url"]))
        return runs[0]
    print("dispatched (run not yet listed)")
    return None


def cmd_match(args):
    return dispatch("tune-match.yml", args.ref, {
        "a": args.a, "b": args.b, "depth": args.depth, "pairs_per_shard": args.pairs,
        "shards": args.shards, "ply_cap": args.ply_cap, "elo0": args.elo0, "elo1": args.elo1,
        "tag": args.tag or "d%d-%d" % (args.depth, args.pairs * args.shards),
    })


def cmd_gauntlet(args):
    return dispatch("tune-gauntlet.yml", args.ref, {
        "challenger": args.challenger, "depth": args.depth, "pairs_per_shard": args.pairs,
        "shards": args.shards, "ply_cap": args.ply_cap,
    })


def cmd_spsa(args):
    return dispatch("tune-spsa.yml", args.ref, {
        "name": args.name, "base": args.base, "params": " ".join(args.params or []),
        "c": " ".join(args.c or []), "iterations": args.iterations, "depth": args.depth,
        "shards": args.shards, "pairs_per_shard": args.pairs, "ply_cap": args.ply_cap,
        "book": args.book,
    })


def cmd_book(args):
    return dispatch("tune-book.yml", args.ref, {
        "count": args.count, "shards": args.shards, "noise": args.noise,
        "random_plies": args.random_plies, "start_seed": args.start_seed,
    })


def report_for(run_id):
    """The report the workflow committed for this run, if it has."""
    subprocess.run(["git", "fetch", "-q", "origin",
                    "%s:refs/remotes/origin/%s" % (RESULTS_BRANCH, RESULTS_BRANCH)],
                   cwd=REPO_ROOT, stderr=subprocess.DEVNULL)
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", "origin/" + RESULTS_BRANCH],
                             cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    for path in listing.split():
        if ("/%s-" % run_id in path or "/%s/" % run_id in path or path.endswith("-%s.txt" % run_id)) \
                and path.endswith("report.txt"):
            return subprocess.run(["git", "show", "origin/%s:%s" % (RESULTS_BRANCH, path)],
                                  cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE).stdout.strip()
    return None


def cmd_status(args):
    for workflow in ("tune-match.yml", "tune-gauntlet.yml", "tune-spsa.yml", "tune-book.yml"):
        out = gh("run", "list", "--workflow", workflow, "--limit", str(args.limit),
                 "--json", "databaseId,status,conclusion,createdAt,displayTitle")
        runs = json.loads(out or "[]")
        if not runs: continue
        print("== %s" % workflow)
        for run in runs:
            print("  %s  %s  %-11s %s" % (run["databaseId"], run["createdAt"][:16].replace("T", " "),
                                           run["status"], run.get("conclusion") or ""))
            if run["status"] == "completed" and run.get("conclusion") == "success" and args.reports:
                report = report_for(run["databaseId"])
                if report:
                    for line in report.splitlines(): print("      " + line)


def main(argv=None):
    parser = argparse.ArgumentParser(description="dispatch tuning workflows on GitHub Actions")
    parser.add_argument("--ref", default=None, help="branch to run on (default: the current branch)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("match")
    p.add_argument("--a", default=DEFAULT_A); p.add_argument("--b", default=DEFAULT_B)
    p.add_argument("--depth", type=int, default=3); p.add_argument("--pairs", type=int, default=50)
    p.add_argument("--shards", type=int, default=20); p.add_argument("--ply-cap", type=int, default=600)
    p.add_argument("--elo0", type=float, default=0.0); p.add_argument("--elo1", type=float, default=5.0)
    p.add_argument("--tag", default=None)
    p.set_defaults(fn=cmd_match)

    p = sub.add_parser("gauntlet")
    p.add_argument("--challenger", required=True)
    p.add_argument("--depth", type=int, default=3); p.add_argument("--pairs", type=int, default=50)
    p.add_argument("--shards", type=int, default=5); p.add_argument("--ply-cap", type=int, default=600)
    p.set_defaults(fn=cmd_gauntlet)

    p = sub.add_parser("spsa")
    p.add_argument("--name", required=True); p.add_argument("--base", default=DEFAULT_A)
    p.add_argument("--params", nargs="*", default=None); p.add_argument("--c", nargs="*", default=None)
    p.add_argument("--iterations", type=int, default=20000); p.add_argument("--depth", type=int, default=3)
    p.add_argument("--shards", type=int, default=20); p.add_argument("--pairs", type=int, default=50)
    p.add_argument("--ply-cap", type=int, default=600)
    p.add_argument("--book", default="tuning/book.jsonl")
    p.set_defaults(fn=cmd_spsa)

    p = sub.add_parser("book")
    p.add_argument("--count", type=int, default=500); p.add_argument("--shards", type=int, default=20)
    p.add_argument("--noise", type=float, default=1.0); p.add_argument("--random-plies", type=int, default=2)
    p.add_argument("--start-seed", type=int, default=1000000)
    p.set_defaults(fn=cmd_book)

    p = sub.add_parser("status")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--no-reports", dest="reports", action="store_false")
    p.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    if args.ref is None: args.ref = current_branch()
    args.fn(args)


if __name__ == "__main__":
    main()
