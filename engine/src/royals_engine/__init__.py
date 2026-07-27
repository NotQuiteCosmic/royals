"""Royals -- the game engine.

Pure standard library, no third-party dependencies, and no user interface of any kind.
That is deliberate and enforced: tests/test_engine_purity.py fails the build if anything
here imports tkinter, calls input(), or prints. The same modules are imported by a
tkinter window, a terminal driver, a web worker, and (via Pyodide) a browser, so the one
thing they must not do is assume which.

    hasher   board representation -- a board is a tuple of 49 ints, each packing a
             square into 13 bits. Immutable and hashable, which is what makes the ko
             set and the transposition table cheap.
    engine   move generation, validation and execution, plus the entering phase and the
             ko rule. Every executor is pure: board in, new board out.
    ai       alpha-beta search with iterative deepening, a transposition table, killer
             moves and a history heuristic. Integer-only scoring.
    perlin   2D Perlin noise, used only to vary the computer's opening.
    compat   two helpers that outlived the legacy RoyalsLib.

Modules keep their original CamelCase function names. Import them aliased so existing
call sites read unchanged:

    from royals_engine import hasher as Hasher
    from royals_engine import engine as Engine
    from royals_engine import ai as artificialPlayer
"""
