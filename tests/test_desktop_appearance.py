"""The appearance screen, driven through the real window.

The schema is checked without a display in test_theme.py. What needs a screen is everything
this file is about: that the miniature draws what the theme says rather than what the window
is wearing, that applying one actually re-dresses the window, and that the third screen obeys
the same contract the other two do -- because the ways a screen can be wrong here are all
ways that leave the program running and looking almost right.
"""

import pathlib
import sys

import pytest

tk = pytest.importorskip("tkinter")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop"))

import theme as Theme

royals_gui = pytest.importorskip("royals_gui")


LOUD = Theme.validate({
    "name": "Loud",
    "colours": {"PANEL": "#2b1d3a", "TEXT": "#f7e9ff", "DARK": "#3d2a52",
                "LIGHT": "#c9b6dd", "INK": "#120a1c"},
    "lines": {"hair": 2, "piece": 3, "mark": 6},
    "gradient": {"on": True, "from": "#120a1c", "to": "#4a3163",
                 "direction": "diagonal", "bands": 16},
})


def widgetColours(widget, out=None):
    """Every background colour in a widget tree, as hex, for spotting a stale one."""
    out = [] if out is None else out
    try:
        raw = widget.cget("bg")
        if raw:
            r, g, b = widget.winfo_rgb(raw)
            out.append("#%02x%02x%02x" % (r // 257, g // 257, b // 257))
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        widgetColours(child, out)
    return out


####### The screen itself #######

def test_the_screen_builds_and_leads_back_to_a_playable_game(window):
    window.buildAppearance()
    window.root.update()
    assert window.previewCanvas.find_all(), "the preview drew nothing"

    window.buildSetup()
    window.modeVar.set(0)
    window.entryVar.set(1)
    window.startGame()
    window.root.update()
    assert window.phase == "play"
    assert window.errors == []


def test_the_screen_lets_the_window_shrink_again(window):
    """buildGame sets a floor of 820x620 and nothing else ever lifts it, so a window that has
    held a game could not shrink for the rest of the session.

    Asserted against the menu's own floor rather than against 1: `wm minsize` is a request,
    and macOS Tk answers 72 whatever it is asked for. What matters is that this screen is as
    free to shrink as the screen it is a sibling of.
    """
    window.root.update()
    menu = window.root.minsize()

    window.modeVar.set(0); window.entryVar.set(1)
    window.startGame(); window.root.update()
    assert window.root.minsize()[0] >= 820, "the game screen should set a floor"

    window.buildAppearance(); window.root.update()
    assert window.root.minsize() == menu


def test_leaving_a_game_for_the_screen_drops_what_the_game_left_running(window):
    """The same contract buildSetup keeps: the generation moves, so an in-flight search
    cannot land, and the review goes, because the arrow keys are bound to the root and are
    live on every screen."""
    window.modeVar.set(0); window.entryVar.set(1)
    window.startGame(); window.root.update()
    window.startReview(); window.root.update()
    assert window.review is not None

    generation = window.gameGen
    window.buildAppearance(); window.root.update()
    assert window.gameGen > generation
    assert window.review is None
    assert window.viewReady is False

    for key in ("<Home>", "<End>", "<Left>", "<Right>"):
        window.root.event_generate(key, when="now")
        window.root.update()
    assert window.errors == []


####### The preview #######

def test_the_preview_draws_the_theme_being_edited_and_not_the_one_in_use(window):
    """The point of the whole screen. polyPlate, chip and obelisk take their colours as
    arguments, so the miniature can show a theme that has been applied to nothing -- which is
    what makes it a preview rather than a picture of what you already have."""
    window.buildAppearance()
    window.pending = dict(LOUD)
    window.pending["colours"] = dict(LOUD["colours"])
    window.refreshAppearance()
    window.root.update()

    assert window.previewCanvas.find_all()
    assert royals_gui.PANEL == Theme.DEFAULT["colours"]["PANEL"], \
        "editing the pending theme changed the live palette"
    assert royals_gui.LINE_W == Theme.DEFAULT["lines"]


def test_the_preview_does_not_disturb_the_board_it_is_a_miniature_of(window):
    window.modeVar.set(0); window.entryVar.set(1)
    window.startGame(); window.root.update()
    yaw, pitch, scale = window.view.yaw, window.view.pitch, window.view.scale

    window.buildAppearance(); window.root.update()
    window.pending["gradient"]["on"] = True
    window.refreshAppearance(); window.root.update()

    assert (window.view.yaw, window.view.pitch, window.view.scale) == (yaw, pitch, scale)
    assert window.previewView is not window.view


def test_the_preview_follows_every_control(window):
    window.buildAppearance(); window.root.update()
    flat = len(window.previewCanvas.find_all())

    window.gradVar.set(1)
    window.onGradientToggle()
    window.root.update()
    assert len(window.previewCanvas.find_all()) > flat, "the gradient drew no bands"

    for direction in Theme.DIRECTIONS:
        window.setDirection(direction)
        window.root.update()
        assert window.previewCanvas.find_all()

    window.weightVar.set("heavy")
    window.onWeight()
    assert window.pending["lines"] == Theme.WEIGHT_PRESETS["heavy"]
    assert window.errors == []


####### Applying #######

def test_applying_dresses_the_window_and_the_board(window, tmp_path, monkeypatch):
    monkeypatch.setattr(Theme, "userDir", lambda: tmp_path)

    window.buildAppearance()
    window.pending = dict(LOUD)
    window.pending["colours"] = dict(LOUD["colours"])
    window.applyPending()
    window.root.update()

    assert royals_gui.PANEL == LOUD["colours"]["PANEL"]
    assert royals_gui.LINE_W["mark"] == LOUD["lines"]["mark"]

    # and the board, which reads its colours at draw time
    window.buildSetup(); window.modeVar.set(0); window.entryVar.set(1)
    window.startGame(); window.root.update()
    assert window.canvas.find_all()
    assert window.errors == []


def test_applying_leaves_no_widget_wearing_the_old_colour(window, tmp_path, monkeypatch):
    """A tk widget copies its colours in when it is made, so the only way to re-dress a
    screen is to build it again. This is what says the screen does."""
    monkeypatch.setattr(Theme, "userDir", lambda: tmp_path)
    stale = Theme.DEFAULT["colours"]["PANEL"]

    window.buildAppearance()
    window.pending = dict(LOUD)
    window.pending["colours"] = dict(LOUD["colours"])
    window.applyPending()
    window.root.update()

    assert stale not in widgetColours(window.frame), \
        "something is still painted in the previous theme's panel colour"


def test_applying_remembers_the_theme_for_next_time(window, tmp_path, monkeypatch):
    monkeypatch.setattr(Theme, "userDir", lambda: tmp_path)

    window.buildAppearance()
    window.pending["colours"]["DARK"] = "#123456"
    window.applyPending()
    window.root.update()

    assert Theme.readConfig()["colours"]["DARK"] == "#123456"


def test_revert_puts_back_what_was_there_when_the_screen_opened(window):
    window.buildAppearance()
    window.pending["colours"]["DARK"] = "#123456"
    window.pending["gradient"]["on"] = True
    window.refreshAppearance()

    window.revertPending()
    window.root.update()
    assert window.pending["colours"]["DARK"] == Theme.DEFAULT["colours"]["DARK"]
    assert window.pending["gradient"]["on"] is False
    assert royals_gui.DARK == Theme.DEFAULT["colours"]["DARK"]


####### Files #######

def test_a_preset_can_be_loaded_and_shows_in_the_preview(window):
    window.buildAppearance()
    night = [p for n, p in Theme.presets() if n == "Night"][0]
    window.loadPreset(night)
    window.root.update()

    assert window.pending["name"] == "Night"
    assert window.previewCanvas.find_all()
    assert royals_gui.PANEL == Theme.DEFAULT["colours"]["PANEL"], "loading applied it"


def test_an_unreadable_theme_is_reported_and_changes_nothing(window, tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    shown = []
    monkeypatch.setattr(royals_gui.messagebox, "showerror",
                        lambda *a, **k: shown.append(a))

    window.buildAppearance()
    before = dict(window.pending["colours"])
    window.loadPreset(bad)

    assert shown, "a broken theme file was opened in silence"
    assert window.pending["colours"] == before


def test_saving_writes_a_theme_that_loads_back(window, tmp_path, monkeypatch):
    path = tmp_path / "mine.json"
    monkeypatch.setattr(royals_gui.filedialog, "asksaveasfilename", lambda **k: str(path))
    monkeypatch.setattr(Theme, "userDir", lambda: tmp_path)

    window.buildAppearance()
    window.pending["colours"]["DARK"] = "#123456"
    window.saveTheme()
    window.root.update()

    assert Theme.load(path)["colours"]["DARK"] == "#123456"
    assert Theme.load(path)["name"] == "mine"


####### The contrast readout #######

def test_the_readout_stays_quiet_for_a_theme_that_reads(window):
    window.buildAppearance()
    window.root.update()
    assert "Hard to make out" not in window.contrastLabel.cget("text")


def test_the_readout_says_so_when_a_colour_cannot_be_read(window):
    window.buildAppearance()
    window.pending["colours"]["TEXT"] = window.pending["colours"]["PANEL"]
    window.refreshAppearance()
    window.root.update()
    assert "Hard to make out" in window.contrastLabel.cget("text")


####### Randomize #######

def test_randomize_moves_the_pending_theme_and_nothing_else(window):
    """Like every other control on this screen it edits `pending`. If it ever applied as well,
    REVERT would have nothing true to go back to."""
    window.buildAppearance()
    before = dict(window.pending["colours"])
    wearing = royals_gui.PANEL

    window.randomPending()
    window.root.update()

    assert window.pending["colours"] != before
    assert window.pending["name"].startswith("Random ")
    assert royals_gui.PANEL == wearing, "randomizing dressed the window"


def test_randomize_leaves_the_controls_describing_what_it_rolled(window):
    """syncControls is the whole of the wiring, and forgetting it is invisible: the board
    preview would show the roll while the weight and band controls described the theme
    before it."""
    window.buildAppearance()
    window.randomPending()
    window.root.update()

    assert window.weightVar.get() == window.weightPreset()
    assert window.bandsVar.get() == window.pending["gradient"]["bands"]
    assert window.dirVar.get() == window.pending["gradient"]["direction"]
    assert bool(window.gradVar.get()) == window.pending["gradient"]["on"]


def test_a_rolled_theme_is_never_the_thing_the_readout_warns_about(window):
    """Twenty presses, because one legible roll proves nothing about the next."""
    window.buildAppearance()
    for _ in range(20):
        window.randomPending()
        window.root.update()
        assert "Hard to make out" not in window.contrastLabel.cget("text")


####### The preset list #######

def test_every_preset_on_disk_is_listed(window):
    """It used to show the first eight of whatever it found, which was fine at five themes
    and silently wrong at seventeen -- the ninth onwards were simply not drawn."""
    window.buildAppearance()
    window.root.update()

    listed = [b.cget("text") for b in window.presetList.winfo_children()]
    assert listed == [name for name, _path in Theme.presets()]
    assert len(listed) > 8, "the point of the scrolling list is that it outgrew eight"


def test_the_preset_list_scrolls_without_making_the_screen_taller(window):
    """The canvas is a fixed height and the frame inside it is not: that is what lets the
    list grow without the window growing with it."""
    window.buildAppearance()
    window.root.update()

    visible = window.presetView.winfo_reqheight()
    assert visible == royals_gui.PRESET_ROWS * royals_gui.PRESET_ROW_H

    inner = window.presetList.winfo_reqheight()
    assert inner > visible, "nothing to scroll -- this test is not testing anything"

    window.presetView.yview_moveto(1.0)
    window.root.update()
    assert window.presetView.yview()[1] == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# The setup screen has to fit on a screen
# ---------------------------------------------------------------------------

# buildSetup calls resizable(False, False) and never sets an explicit geometry, so the window
# is exactly as big as its widgets ask for. That is the right behaviour for a column of
# controls and it has one failure mode: nothing stops the screen growing, and when it grows
# past the display there is no scrollbar, no clipping and no dragging it bigger -- the bottom
# is simply unreachable and START GAME may be the thing you cannot reach.
#
# It got there. Before the two-column rebuild the screen asked for **644 x 1039** on a 1050
# tall display, which is off the bottom once a menu bar and a title bar are counted. The
# ceilings below are deliberately loose: this is here to catch the screen walking off the
# display again, not to pin a layout to the pixel.
SETUP_MAX_H = 800
SETUP_MAX_W = 820


def setupSize(window):
    window.root.update_idletasks()
    return window.frame.winfo_reqwidth(), window.frame.winfo_reqheight()


@pytest.mark.parametrize("mode, name", [(0, "2 player"), (1, "1 player"), (2, "0 player")])
def test_the_setup_screen_fits_on_a_screen(window, mode, name):
    window.buildSetup()
    window.modeVar.set(mode)
    window.refreshSetup()

    width, height = setupSize(window)
    assert height <= SETUP_MAX_H, "%s: the menu is %dpx tall and heading off the display" % (
        name, height)
    assert width <= SETUP_MAX_W, "%s: the menu is %dpx wide" % (name, width)
    assert window.errors == []


def test_the_tallest_the_setup_screen_gets_is_still_short_enough(window):
    """0 player with a separate black depth: every group on the screen showing at once."""
    window.buildSetup()
    window.modeVar.set(2)
    window.splitDepthVar.set(1)
    window.entryVar.set(0)
    window.refreshSetup()

    _width, height = setupSize(window)
    assert height <= SETUP_MAX_H, "the fullest the menu gets is %dpx tall" % height
    assert window.errors == []


def test_a_mode_that_cannot_use_a_control_does_not_reserve_room_for_it(window):
    """Greying keeps a control's space; hiding gives it back. Both happen here, and the
    point of the second is that the screen is shorter for it -- if these two ever measure
    the same, the collapsing has silently stopped working."""
    window.buildSetup()

    window.modeVar.set(2)
    window.splitDepthVar.set(1)
    window.refreshSetup()
    _w, tallest = setupSize(window)

    window.modeVar.set(0)          # two humans: the whole COMPUTER box is beside the point
    window.refreshSetup()
    _w, shortest = setupSize(window)

    assert shortest < tallest - 100, (
        "2 player should be much shorter than 0 player with a split depth, got %d vs %d"
        % (shortest, tallest))
    assert window.errors == []


def test_the_controls_come_back_when_the_mode_comes_back(window):
    """pack() re-adds at the end of its parent's order, so a group that is hidden and shown
    again lands at the bottom unless something puts it back in place. showRows repacks the
    whole list in order, and this is what says so: the boxes have to read down the column in
    the same order after a round trip through a mode that hid one of them."""
    window.buildSetup()
    window.modeVar.set(1)
    window.refreshSetup()
    window.root.update_idletasks()
    # pack_slaves, not winfo_ismapped: the test root is withdrawn, so nothing on it is ever
    # mapped and a check that asked would pass on two empty lists without looking at anything.
    before = [w.cget("text") for w in window.leftColumn.pack_slaves()]

    window.modeVar.set(0)          # hides YOUR SIDE
    window.refreshSetup()
    window.modeVar.set(1)          # and brings it back
    window.refreshSetup()
    window.root.update_idletasks()
    after = [w.cget("text") for w in window.leftColumn.pack_slaves()]

    assert after == before, "the boxes came back in a different order"
    assert "YOUR SIDE" in " ".join(after)
    assert window.errors == []
