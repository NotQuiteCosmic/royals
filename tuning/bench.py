"""Node rate of the search on a fixed position, for before/after comparisons.

    python3 tuning/bench.py            # CPython is the one that gates: its rate is stable
    pypy3 tuning/bench.py              # informational -- PyPy's rate swung 56k to 64k
                                       # between two identical runs on this machine

The position is reached the same way every time: the seed-5 entering at intensity 0.5
(the one golden_search.txt uses), then eight plies of depth-3 self-play. From there the
search runs at depth 4 five times over, each from an empty table so the runs don't feed
one another, and the median nodes/second is what gets printed.

The node count is printed too, and is the more useful number of the two: it is a pure
function of the engine, so an edit that was meant to change nothing but speed must leave
it exactly where it was. A changed count means a changed search, whatever the clock says.
"""

import os
import statistics
import sys
import time

# run by path (python3 tuning/bench.py) the repo root isn't importable; run as a module
# (python3 -m tuning.bench) it already is. Either way, the package puts the engine on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tuning  # noqa: E402,F401

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI

SEED = 5
WARM_PLIES = 8
DEPTH = 5
RUNS = 5


def position():
    AI.setEntryNoise(0.5, SEED)
    board = Hasher.Entering_Board()
    for contr, piece in Engine.enteringSequence():
        isSpy = (piece == Hasher.SPY)
        if not Engine.enteringOptions(board, contr, isSpy): continue
        board = Engine.dropPiece(board, AI.chooseEntry(board, contr, piece, isSpy), contr, piece)

    Engine.koReset()
    AI.newGame()
    Engine.koRecord(board)
    for ply in range(WARM_PLIES):
        board, move, score = AI.takeTurn(board, (ply + 1) % 2, 3)
        if move is not None: Engine.koRecord(board)
    return board, (WARM_PLIES + 1) % 2


def main():
    # --set NAME=value puts a weight in force for the timing, so that a candidate term can be
    # costed on its own: python3 tuning/bench.py --set THREAT_PENALTY=1
    settings = {}
    args = sys.argv[1:]
    while args:
        flag = args.pop(0)
        if flag != "--set" or not args: sys.exit("usage: bench.py [--set NAME=value ...]")
        name, value = args.pop(0).split("=")
        settings[name] = int(value)

    board, contr = position()
    if settings: AI.setWeights(settings)

    rates = []
    nodes = None
    for run in range(RUNS):
        AI.newGame()
        started = time.perf_counter()
        score, move = AI.chooseMove(board, contr, DEPTH)
        elapsed = time.perf_counter() - started
        if nodes is None: nodes = AI.calcCount
        elif nodes != AI.calcCount:
            # the same search from the same empty table cannot visit a different tree
            sys.exit("node count changed between runs: %d then %d" % (nodes, AI.calcCount))
        rates.append(AI.calcCount / elapsed)

    label = " ".join("%s=%d" % kv for kv in sorted(settings.items())) or "defaults"
    print("%s depth %d %s: %d nodes, median %d nodes/s (runs: %s)"
          % (sys.implementation.name, DEPTH, label, nodes, statistics.median(rates),
             " ".join(str(int(r)) for r in rates)))


if __name__ == "__main__":
    main()
