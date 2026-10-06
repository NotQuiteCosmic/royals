"""Has an SPSA run settled? Read its trajectory and say.

    pypy3 -m tuning.trajectory --name retune

For each parameter: where it started, where it is, how far it moved in the last quarter of
the run against how far it moved in the quarter before. A parameter that is still drifting
at the end wants more iterations; one that wandered and came back was noise. The numbers
are the rounded values the engine actually saw, read from trajectory.jsonl, which spsa.py
appends to at every checkpoint.
"""

import argparse
import json
import os
import sys

import tuning  # noqa: F401
from tuning import spsa as P


def load(name):
    path = os.path.join(P.RUNS_DIR, name, "trajectory.jsonl")
    points = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line: points.append(json.loads(line))
    return points


def report(points, params=None):
    if len(points) < 4:
        return "only %d checkpoints: nothing to say yet" % len(points)
    first, last = points[0], points[-1]
    params = params or sorted(first["theta"])
    q = len(points) // 4
    mid, late = points[-2 * q], points[-q]
    lines = ["%-22s %8s %8s %8s  %10s %10s" % ("parameter", "start", "now", "moved", "last q", "q before")]
    for p in params:
        a, b = first["theta"][p], last["theta"][p]
        d_late = last["theta"][p] - late["theta"][p]
        d_mid = late["theta"][p] - mid["theta"][p]
        lines.append("%-22s %8d %8d %+8d  %+10d %+10d" % (p, a, b, b - a, d_late, d_mid))
    lines.append("%d checkpoints, %d iterations" % (len(points), last["done"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--params", nargs="*", default=None)
    args = parser.parse_args(argv)
    sys.stdout.write(report(load(args.name), args.params) + "\n")


if __name__ == "__main__":
    main()
