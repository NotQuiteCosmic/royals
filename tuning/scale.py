"""A probe size for a term that defaults to 0.

SPSA needs a c for every parameter it tunes, and for the existing weights an eighth of the
default is a fine guess. A candidate term defaults to 0 and gives nothing to take an eighth
of. What it has instead is a natural size: how much, per unit of weight, it actually moves
the evaluation across the positions a game visits. This measures that and sets c so that a
one-c nudge shifts the evaluation by about a tenth of what the whole evaluation typically
comes to -- the same proportion the eighth-of-default rule gives the others.

The raw size of a term is read off the evaluator itself rather than from a second
implementation of it: evaluate every sampled position with the term at 1 and at 0, and the
difference IS the term's raw value, exactly, in whole numbers. Nothing to keep in step.

    pypy3 -m tuning.scale --from tuning/results/calibration.jsonl --terms SPY_DIST_WEIGHT THREAT_PENALTY
"""

import argparse
import json
import random
import sys

import tuning  # noqa: F401
from tuning import game as G

from royals_engine import ai as AI
from royals_engine import notation as N

TARGET_FRACTION = 0.1


def positions_from(path, limit=2000, seed=1, skip_first=4, skip_last=2):
    """Positions from recorded games, a few from each, not too near either end: the opening
    is the book's doing and the last plies are a won game."""
    rng = random.Random(seed)
    boards = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            pair = json.loads(line)
            for record in pair["games"]:
                board, contr = G._replay_opening(record)
                visited = []
                for token in record["moves"]:
                    move = N.decode_move(token)
                    if move is not None: board = AI.performOneStep(board, contr, move)
                    contr = 1 - contr
                    visited.append(board)
                usable = visited[skip_first:len(visited) - skip_last]
                if usable: boards.extend(rng.sample(usable, min(3, len(usable))))
    rng.shuffle(boards)
    return boards[:limit]


def raw_sizes(boards, term):
    """Mean absolute raw value of `term` per side across the boards."""
    AI.setWeights(AI.DEFAULT_WEIGHTS)
    off = [AI.evaluateSides(b) for b in boards]
    AI.setWeights({term: 1})
    on = [AI.evaluateSides(b) for b in boards]
    AI.setWeights(AI.DEFAULT_WEIGHTS)
    diffs = [abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(on, off)]
    return sum(diffs) / (2.0 * len(boards))


def typical_eval(boards):
    AI.setWeights(AI.DEFAULT_WEIGHTS)
    sizes = []
    for b in boards:
        blue, red = AI.evaluateSides(b)
        if abs(blue) >= AI.WIN_SCORE or abs(red) >= AI.WIN_SCORE: continue
        sizes.append((abs(blue) + abs(red)) / 2.0)
    return sum(sizes) / len(sizes)


def suggest(boards, terms, fraction=TARGET_FRACTION):
    typical = typical_eval(boards)
    out = {}
    for term in terms:
        size = raw_sizes(boards, term)
        low, high = AI.WEIGHT_RANGES[term]
        c = fraction * typical / size if size else float("nan")
        out[term] = {"raw_per_unit": size, "c": c, "range": (low, high)}
    return typical, out


def main(argv=None):
    parser = argparse.ArgumentParser(description="probe sizes for zero-default terms")
    parser.add_argument("--from", dest="source", required=True, help="a match results JSONL")
    parser.add_argument("--terms", nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--fraction", type=float, default=TARGET_FRACTION)
    args = parser.parse_args(argv)

    boards = positions_from(args.source, args.limit)
    typical, got = suggest(boards, args.terms, args.fraction)
    sys.stdout.write("%d positions, typical |eval| %.0f\n" % (len(boards), typical))
    for term, info in got.items():
        sys.stdout.write("%-22s raw/unit %8.2f   c = %8.1f   (range %d..%d)\n"
                         % (term, info["raw_per_unit"], info["c"], info["range"][0], info["range"][1]))


if __name__ == "__main__":
    main()
