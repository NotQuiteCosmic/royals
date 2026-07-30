"""The optional accelerator, and the properties the goldens cannot see.

`golden_moves.txt` compares the two implementations on what they *compute*. These check the
things either implementation could get right while the other quietly got wrong in a way no
diff would show:

  - a board comes back hashable, so the ko set and the transposition table still work
  - ROYALS_NO_ACCEL really does take the Python path
  - clearing the game state reaches both sides

None of this requires the wheel to be installed. Where a test needs the compiled engine it
skips without it, because not having it is a supported configuration -- see engine/src/
royals_engine/_accel.py. What must never happen is these passing *because* it is absent.
"""

import ast
import os
import pathlib
import subprocess
import sys

import pytest

from royals_engine import _accel
from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher

ENGINE_SRC = pathlib.Path(__file__).resolve().parent.parent / "engine" / "src"

needs_accel = pytest.mark.skipif(
    not _accel.active(), reason="royals-accel is not installed; the Python path is in use"
)


def entered_board():
    """A mid-game position, reached the way regress.py reaches one."""
    AI.setEntryNoise(0.5, 7)
    board = Hasher.Entering_Board()
    for contr, piece in Engine.enteringSequence():
        is_spy = (piece == Hasher.SPY)
        if not Engine.enteringOptions(board, contr, is_spy):
            continue
        board = Engine.dropPiece(board, AI.chooseEntry(board, contr, piece, is_spy), contr, piece)
    return board


def run_isolated(code, no_accel):
    """Run a snippet in a fresh interpreter, with or without the accelerator forced off.

    A subprocess rather than monkeypatching because _accel decides once, at import. That is
    deliberate -- flipping it mid-process would let one half of a search run compiled and the
    other half not -- so the only honest way to test the other path is a new process.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    if no_accel:
        env["ROYALS_NO_ACCEL"] = "1"
    else:
        env.pop("ROYALS_NO_ACCEL", None)
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


# ---- boards stay hashable -----------------------------------------------------------------
# The single most damaging thing an accelerator could get wrong. A board is its own key in the
# ko set and the transposition table; hand back a list and nothing raises -- ko membership
# silently stops matching and the rule against repeating a position stops being enforced.

def test_perform_one_step_returns_a_hashable_board():
    board = entered_board()
    moves = AI.listAllMoves(board, 0)
    assert moves, "the entered position should have legal moves"
    child = AI.performOneStep(board, 0, moves[0])
    assert isinstance(child, tuple), type(child)
    hash(child)
    assert len(child) == 49


def test_take_turn_returns_a_hashable_board():
    board = entered_board()
    Engine.koReset()
    AI.newGame()
    played, move, score = AI.takeTurn(board, 0, 2)
    assert isinstance(played, tuple), type(played)
    hash(played)


def test_a_played_board_can_be_recorded_in_the_ko_set():
    """The property the two tests above exist to protect, exercised end to end."""
    board = entered_board()
    Engine.koReset()
    AI.newGame()
    Engine.koRecord(board)
    played, _, _ = AI.takeTurn(board, 0, 2)
    Engine.koRecord(played)
    assert Engine.koBreaks(played), "a recorded position must read back as a repetition"


# ---- the switch ---------------------------------------------------------------------------

def test_no_accel_env_var_forces_the_python_path():
    said = run_isolated(
        "from royals_engine import _accel; print(_accel.active())", no_accel=True)
    assert said == "False", said


@needs_accel
def test_without_the_switch_the_accelerator_is_used():
    """Guards against the differential CI job comparing the fallback against itself."""
    said = run_isolated(
        "from royals_engine import _accel; print(_accel.active())", no_accel=False)
    assert said == "True", said


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false"])
def test_falsey_values_leave_the_accelerator_alone(value):
    """An empty ROYALS_NO_ACCEL is what a shell leaves behind after a half-cleared variable.
    Reading it as "on" would disable the accelerator for someone who thought they had
    cleared the flag -- silently, and with no symptom but slowness.

    The reference is a run with the variable *unset*, not this process: these must all mean
    the same thing as not setting it, whichever way the test session itself was started.
    """
    unset = run_isolated(
        "from royals_engine import _accel; print(_accel.active())", no_accel=False)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["ROYALS_NO_ACCEL"] = value
    out = subprocess.run(
        [sys.executable, "-c", "from royals_engine import _accel; print(_accel.active())"],
        env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == unset, (
        "ROYALS_NO_ACCEL=%r should mean the same as leaving it unset" % value)


# ---- both sides are cleared ---------------------------------------------------------------

@needs_accel
def test_new_game_reaches_the_compiled_side():
    """ai_pool.py calls AI.newGame() between jobs so that game B's positions never land in
    game A's tables. A worker that cleared only the Python half would carry the compiled
    transposition table into the next game -- not a crash, just a search answering
    confidently about a tree that belongs to somewhere else."""
    board = entered_board()
    Engine.koReset()
    AI.newGame()
    AI.takeTurn(board, 0, 3)
    first = AI.calcCount

    AI.newGame()
    Engine.koReset()
    AI.takeTurn(board, 0, 3)
    # With the tables genuinely cleared, the same search from the same position visits the
    # same number of nodes. A table that survived would make the second pass cheaper.
    assert AI.calcCount == first, (
        "the second search saw %d nodes against %d; the compiled tables did not clear"
        % (AI.calcCount, first))


@needs_accel
def test_table_limit_reaches_the_compiled_side():
    """ai_pool.py caps the transposition table because a few hundred MB per generation is
    fatal on the box it runs on. For a while that cap reached only the Python engine: the
    compiled side used a hard-coded constant, so installing the wheel silently tripled a
    worker's memory and the knob still looked set.

    Nothing about that failure was visible -- same moves, same scores, more memory -- so it
    gets a test rather than a comment. A tiny table means less reuse between moves, and less
    reuse means more nodes; if the two runs agree exactly, the limit is being ignored.
    """
    board = entered_board()
    original = AI.TABLE_LIMIT
    try:
        totals = {}
        for limit in (1, 300_000):
            AI.TABLE_LIMIT = limit
            AI.newGame()
            Engine.koReset()
            played, total = board, 0
            for turn in range(6):
                played, move, _ = AI.takeTurn(played, turn % 2, 6)
                total += AI.calcCount
                Engine.koRecord(played)
                if move is None:
                    break
            totals[limit] = total
    finally:
        AI.TABLE_LIMIT = original

    assert totals[1] > totals[300_000], (
        "a one-entry table searched %d nodes and a 300k table searched %d; equal counts mean "
        "TABLE_LIMIT never reached the compiled engine" % (totals[1], totals[300_000]))


# ---- the two implementations refuse the same things the same way --------------------------
# The goldens compare what the engines *compute*, which means they only ever exercise valid
# input. That left a gap: for a while the two disagreed on every malformed call. Rust raised
# PanicException where Python raised IndexError, and worse, Python *silently returned a board*
# for an unknown move kind -- performOneStep was an if-chain ending in an unconditional
# `return Engine.exeBreak(...)`, so "wobble" was executed as a break.
#
# Neither behaviour is reachable from a client (game.py gates on `move not in legal`), which
# is exactly why it needed a test rather than a bug report: nothing in normal play would ever
# have shown it.

BAD_CALLS = [
    ("side too high",      lambda: AI.fullCheck(entered_board(), 2),                          ValueError),
    ("side negative",      lambda: AI.fullCheck(entered_board(), -1),                         ValueError),
    ("side on listAll",    lambda: AI.listAllMoves(entered_board(), 7),                       ValueError),
    ("side on takeTurn",   lambda: AI.takeTurn(entered_board(), 2, 1),                        ValueError),
    ("unknown kind",       lambda: AI.performOneStep(entered_board(), 0, (25, "wobble", 3, False)), ValueError),
    ("origin 0",           lambda: AI.performOneStep(entered_board(), 0, (0, "jump", 3, False)),    IndexError),
    ("origin 50",          lambda: AI.performOneStep(entered_board(), 0, (50, "jump", 3, False)),   IndexError),
    ("target off board",   lambda: AI.performOneStep(entered_board(), 0, (25, "jump", 99, False)),  IndexError),
    ("break dir too high", lambda: AI.performOneStep(entered_board(), 0, (25, "break", 9, False)),  IndexError),
]


@pytest.mark.parametrize("label,call,expected", BAD_CALLS, ids=[c[0] for c in BAD_CALLS])
def test_malformed_input_is_refused(label, call, expected):
    """Whichever engine is loaded, the refusal is the same type.

    Parametrised over the exception rather than just 'it raises', because the failure this
    replaced was two engines raising *different* types for the same mistake -- so a caller
    that caught one broke the moment a wheel was installed.
    """
    with pytest.raises(expected):
        call()


def test_no_malformed_move_silently_returns_a_board():
    """The sharp end of the above, stated on its own because it is the one that lost data.

    A board coming back from a move that isn't a move is worse than any exception: the caller
    carries on with a position that has nothing to do with what it asked for.
    """
    board = entered_board()
    for bad in [(25, "wobble", 3, False), (0, "jump", 3, False), (25, "jump", 99, False)]:
        try:
            got = AI.performOneStep(board, 0, bad)
        except (ValueError, IndexError):
            continue
        pytest.fail("performOneStep%r returned a board instead of refusing: %r" % (bad, got))


# ---- the purity carve-out stays narrow ----------------------------------------------------

def test_the_accelerator_import_is_guarded():
    """test_engine_purity.py allows exactly one non-stdlib import, and only inside a
    try/except ImportError. That guard is the whole basis of the exception: it is what makes
    the wheel's absence a supported configuration rather than a broken install. An unguarded
    import would still be a hard dependency wearing the same name."""
    tree = ast.parse((ENGINE_SRC / "royals_engine" / "_accel.py").read_text())

    guarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(isinstance(h.type, ast.Name) and h.type.id == "ImportError"
                   for h in node.handlers):
            continue
        for stmt in node.body:
            for inner in ast.walk(stmt):
                if isinstance(inner, ast.Import):
                    guarded += [a.name for a in inner.names]

    assert "royals_accel" in guarded, (
        "_accel.py must import royals_accel inside a try/except ImportError")
