"""One Tk root for the whole test session, and why there has to be exactly one.

Three files here drive the real desktop window. Each used to make its own `Tk()`, which works
right up until two of them run in the same process: **on macOS a second root's `update()`
blocks inside Tk itself** once a game screen has been built on it. No Python callback runs, no
exception is raised, the call simply does not return -- measured with a counter on
`redraw`/`onResize`/`flushRedraw`, which stays at zero through the whole hang.

It was invisible while only `test_desktop_lifecycle.py` pumped the event loop and it happened
to sort first. It stops being invisible the moment a fourth display file is added, or the
files are named differently, or somebody runs two of them on one command line -- which is a
test suite that hangs depending on the alphabet.

So the root is made once, here, and every display test shares it. `window` hands back the same
`RoyalsWindow` reset to its opening screen, because NEW GAME is how this application resets
itself and there is no reason for a test to have a different way.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop"))


@pytest.fixture(scope="session")
def tkRoot():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("no display for tkinter: %s" % (exc,))
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture(scope="session")
def gui(tkRoot):
    """The one window. Session-scoped for the reason in this file's docstring."""
    import royals_gui
    return royals_gui.RoyalsWindow(tkRoot)


@pytest.fixture
def window(gui):
    """`gui`, back at its opening screen, with a place to collect callback exceptions.

    Tk swallows an exception raised inside a callback and prints it to stderr, so a test that
    only looked at the board would pass while the log filled with TclErrors. `window.errors`
    is where they go instead.
    """
    import royals_gui
    import theme

    # Whatever the last test was wearing, this one starts in the default look -- and looking
    # from the default place. The camera deliberately outlives a game (yaw, pitch and the
    # projection are all kept when NEW GAME is pressed), which is right for a player and
    # wrong for a test: it makes one test's last drag or last toggle the next one's starting
    # state, and the failure surfaces as an assertion in a file that never mentioned cameras.
    royals_gui.applyTheme(theme.DEFAULT)
    gui.view.persp = False
    gui.view.setAngles(royals_gui.YAW_DEF, royals_gui.PITCH_DEF)

    # A position built on the editor screen outlives a game on purpose -- NEW GAME then START
    # GAME plays the same study again -- which means it also outlives a *test*, and the window
    # is session-scoped. One test's study would be the next one's opening, and the failure
    # would surface as a board nobody in that file had ever mentioned. Only DISCARD clears it
    # in the application; here the fixture does the discarding.
    gui.pendingBoard = None
    gui.pendingTurn = 0

    gui.buildSetup()
    gui.errors = []
    gui.root.report_callback_exception = lambda *a: gui.errors.append(a)
    yield gui

    # Leave nothing outstanding, and nothing half-dressed, for the next test.
    gui.buildSetup()
    gui.root.update()
    royals_gui.applyTheme(theme.DEFAULT)
