"""Play one recorded pair again and demand the identical moves.

Run by CI under CPython and PyPy. The evaluator is in whole numbers precisely so that two
interpreters, or two machines, play the same game; this is the two-minute check that they
still do. The expectation was recorded on a macOS laptop; CI replays it on Linux.

    PYTHONPATH=engine/src python3 tests/determinism/check.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import tuning  # noqa: E402,F401
from tuning import match as M, weights as W  # noqa: E402

expect = json.load(open(os.path.join(HERE, "depth2-pair.json")))
rows = [r for r in M.load_rows(os.path.join(ROOT, "tuning", "book.jsonl")) if r["seed"] == expect["seed"]]
pair = M.play_pair({"opening": rows[0], "a": W.load(os.path.join(ROOT, expect["a"])),
                    "b": W.load(os.path.join(ROOT, expect["b"])), "depth": expect["depth"],
                    "ply_cap": expect["ply_cap"], "tag": "determinism"})
for want, got in zip(expect["games"], pair["games"]):
    if want["moves"] != got["moves"]:
        first = next(i for i, (x, y) in enumerate(zip(want["moves"], got["moves"])) if x != y) \
            if any(x != y for x, y in zip(want["moves"], got["moves"])) else min(len(want["moves"]), len(got["moves"]))
        sys.exit("determinism broken: moves differ from ply %d (%s vs %s)" % (
            first, want["moves"][first:first + 1], got["moves"][first:first + 1]))
print("determinism ok: %s plays the recorded pair move for move (%d and %d plies)"
      % (sys.implementation.name, len(pair["games"][0]["moves"]), len(pair["games"][1]["moves"])))
