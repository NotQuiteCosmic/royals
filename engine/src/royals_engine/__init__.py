"""Royals -- the game engine.

Pure standard library, no third-party dependencies, and no user interface of any kind.
That is deliberate and enforced: tests/test_engine_purity.py fails the build if anything
here imports tkinter, calls input(), or prints. The same modules are imported by a tkinter
window, a terminal driver and a web worker serving many games at once, so the one thing
they must not do is assume which.

These are also not the only implementation of these rules. engine-rs/ is a Rust port of
them, and it arrives here as an optional compiled wheel that _accel finds and ai delegates
to -- see that module. Nothing below changes when it is present: the Python is the
reference implementation, the fallback, and the half of a differential check that makes the
other half falsifiable.

    hasher   board representation -- a board is a tuple of 49 ints, each packing a
             square into 13 bits. Immutable and hashable, which is what makes the ko
             set and the transposition table cheap.
    engine   move generation, validation and execution, plus the entering phase and the
             ko rule. Every executor is pure: board in, new board out.
    ai       alpha-beta search with iterative deepening, a transposition table, killer
             moves and a history heuristic. Integer-only scoring.
    notation moves and boards as text and JSON. The only module allowed to do arithmetic
             on a square, which is where the 1-based origin and 0-based target live.
    perlin   2D Perlin noise, used only to vary the computer's opening.
    compat   two helpers that outlived the legacy RoyalsLib.
    _accel   which engine is answering, and the ROYALS_NO_ACCEL switch that forces this one.

Modules keep their original CamelCase function names. Import them aliased so existing
call sites read unchanged:

    from royals_engine import hasher as Hasher
    from royals_engine import engine as Engine
    from royals_engine import ai as artificialPlayer
"""
