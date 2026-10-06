"""Self-play tournament tuning for the Royals evaluator.

Everything in here is stdlib-only, like the engine, so the whole thing runs under PyPy --
which is roughly twice as fast on this search once its JIT has warmed, and is where the
tens of thousands of games a tuning run needs should be played.

The engine is imported from THIS checkout, whatever happens to be pip-installed. On this
machine the editable install points at a different, diverged checkout, and PyPy has no
install at all; running the goldens against the wrong engine fails at line 13 of
golden_search.txt, which is how that was discovered. So the package puts its own repo's
engine/src at the front of sys.path before anything imports royals_engine -- the same
thing install-desktop.sh does with PYTHONPATH, for the same reason.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENGINE = os.path.join(_REPO, "engine", "src")

if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)
