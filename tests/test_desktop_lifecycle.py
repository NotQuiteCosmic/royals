"""What happens to a game the player walks away from.

Every other test of the window plays one game in it. The failures here need two: they are
all one shape, which is a thing the abandoned game handed to the event loop coming back
after the next game has started, and writing into it.

That shape had exactly one symptom worth the name -- **the new game came up showing the old
game's position** -- and several that were quieter and worse. A late `commit` appends a ply
to a record nobody played, and puts a board the previous game stood in into the ko set this
one just cleared: the new game then refuses a legal move as a repetition, and the file it
writes will not load. So the assertions below are not only about the board. A board that
happens to look right while the record behind it is wrong is the state this whole file
exists to catch.

A search cannot be called off -- the thread has no interrupt -- so the window drops the
answer instead, by the generation token every deferred call carries. These tests block the
engine on an Event rather than racing a real search, which is what makes "a search is in
flight" a fact rather than a hope; a depth-10 search is about twenty seconds, and twenty
seconds is exactly the window a player gets bored in.
"""

import pathlib
import sys
import threading
import time

import pytest

tk = pytest.importorskip("tkinter")

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import record as R

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop"))

# Wanted at module scope by the hold tests below, which patch its messagebox from a helper.
royals_gui = pytest.importorskip("royals_gui")


# `gui` and `window` come from tests/conftest.py now, shared with the other two files that
# drive the real window. The reasoning that used to live here -- one Tk() per process, because
# a second root's update() blocks inside Tk itself on macOS -- moved there with them, since it
# turned out to be a rule about the whole test session rather than about this file.


def pump(root, seconds=2.0, until=None):
    """Run the event loop for a while, or until something is true."""
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None


def blocked(monkeypatch, name, released):
    """Make one engine call wait for the test's say-so, as a deep search would."""
    real = getattr(AI, name)

    def slow(*args, **kwargs):
        released.wait(20)
        # depth is the third positional argument to takeTurn; answer quickly once let go,
        # since what is being tested is when the answer arrives and not what it is.
        if name == "takeTurn":
            return real(args[0], args[1], 1)
        return real(*args, **kwargs)

    monkeypatch.setattr(AI, name, slow)
    return real


def newGameStartedOver(win, **choices):
    """NEW GAME, then START GAME, as the two buttons do it."""
    win.buildSetup()
    for var, value in choices.items():
        getattr(win, var).set(value)
    win.startGame()
    win.root.update()


####### The abandoned search #######

def test_an_abandoned_search_cannot_reach_the_next_game(window, monkeypatch):
    released = threading.Event()
    blocked(monkeypatch, "takeTurn", released)

    # A dealt opening so play -- and so the computer's first search -- begins at once.
    window.modeVar.set(1); window.sideVar.set(0)
    window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()
    assert window.aiBusy, "the computer should be thinking"

    newGameStartedOver(window, modeVar=0, entryVar=1)
    board, plies, ko = window.board, len(window.record), len(Engine.koTrack)

    released.set()
    pump(window.root, 3.0)

    assert window.board == board, "the previous game's position came back"
    assert len(window.record) == plies, "a ply nobody played was written down"
    assert len(Engine.koTrack) == ko, "an old board went into this game's ko set"
    assert window.errors == []


def test_an_abandoned_placement_cannot_reach_the_next_game(window, monkeypatch):
    """The entering half, which is the worse one: it drops a piece rather than a position,
    so the new game gains a second royal and stops being a game of Royals at all."""
    released = threading.Event()
    blocked(monkeypatch, "chooseEntry", released)

    window.modeVar.set(1); window.sideVar.set(0)
    window.entryVar.set(0); window.depthVar.set(1)
    window.startGame(); window.root.update()
    window.enterClick(window.entryOptions[0])     # white places; red's goes to the worker
    window.root.update()
    assert window.aiBusy

    newGameStartedOver(window, modeVar=0, entryVar=1)
    board, plies = window.board, len(window.record)

    released.set()
    pump(window.root, 3.0)

    assert window.board == board
    assert len(window.record) == plies
    assert window.errors == []


def test_the_game_after_an_abandoned_one_still_writes_a_record_that_replays(window, monkeypatch):
    """The assertion the board alone cannot make.

    A stale placement lands in the record as an entry token after entering is over, which
    `record.positions` refuses outright -- so this fails even in the case where the stale
    write happened to leave a position that looked plausible.
    """
    released = threading.Event()
    blocked(monkeypatch, "takeTurn", released)

    window.modeVar.set(1); window.sideVar.set(0)
    window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()

    newGameStartedOver(window, modeVar=0, entryVar=1)
    released.set()
    pump(window.root, 3.0)

    spots = R.positions(window.record)
    assert len(spots) == len(window.record) + 1
    assert spots[-1].board == window.board


####### One position, one search #######

def test_two_searches_never_run_on_one_position(window, monkeypatch):
    """`advance` is reachable twice for the same turn -- a pause firing late, a click while
    the computer thinks -- and twice used to mean two searches and two moves for one side."""
    released = threading.Event()
    calls = []
    real = AI.takeTurn

    def counted(board, contr, depth, *a, **kw):
        calls.append(1)
        released.wait(20)
        return real(board, contr, 1)

    monkeypatch.setattr(AI, "takeTurn", counted)

    window.modeVar.set(2); window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()
    assert len(calls) == 1 and window.aiBusy

    window.advance()
    window.root.update()
    assert len(calls) == 1, "a second search was started on the same position"

    released.set()
    pump(window.root, 2.0)


####### The token itself #######

def test_a_deferred_call_belongs_to_the_game_that_asked_for_it(window):
    fired = []
    window.later(1, lambda: fired.append("same game"))
    pump(window.root, 0.5, until=lambda: fired)
    assert fired == ["same game"]

    window.later(1, lambda: fired.append("old game"))
    window.gameGen += 1                 # what NEW GAME does
    pump(window.root, 0.5)
    assert fired == ["same game"], "a callback outlived the game that scheduled it"


def test_starting_and_abandoning_a_game_both_move_the_generation(window):
    first = window.gameGen
    window.modeVar.set(0); window.entryVar.set(1)
    window.startGame(); window.root.update()
    assert window.gameGen > first

    playing = window.gameGen
    window.buildSetup()
    assert window.gameGen > playing, "walking away from a game must move it too"


####### What the setup screen still has bound #######

def test_the_review_keys_do_nothing_on_the_setup_screen(window):
    """Home, End and the arrows are bound to the root, so they are live on a screen with no
    board on it. They used to find `self.review` still set from the game just left and walk
    into the widgets that game took with it."""
    window.modeVar.set(0); window.entryVar.set(1)
    window.startGame(); window.root.update()
    window.startReview(); window.root.update()
    assert window.review is not None

    window.buildSetup(); window.root.update()
    assert window.review is None, "the review outlived the game it was of"

    for key in ("<Home>", "<End>", "<Left>", "<Right>"):
        window.root.event_generate(key, when="now")
        window.root.update()

    assert window.errors == []


####### What a new game inherits #######

def test_a_new_game_does_not_inherit_the_drag(window):
    """redraw reads `dragging` as "draw this cheaply, it is moving". Carried into the next
    game it is never cleared, and the board stays in the low-detail form."""
    window.modeVar.set(0); window.entryVar.set(1)
    window.startGame(); window.root.update()
    window.dragging = True
    window.swallowRelease = True

    newGameStartedOver(window, modeVar=0, entryVar=1)
    assert window.dragging is False
    assert window.swallowRelease is False


def test_a_game_opened_from_a_file_knows_how_it_was_entered(window, tmp_path, monkeypatch):
    """`openGame` has no menu to read, so the entering settings have to have defaults --
    they used to exist only in startGame, and a file opened first thing left them unset."""
    import royals_gui

    window.modeVar.set(0); window.entryVar.set(1)
    window.startGame(); window.root.update()
    path = tmp_path / "game.txt"
    monkeypatch.setattr(royals_gui.filedialog, "asksaveasfilename", lambda **kw: str(path))
    window.saveGame()

    window.buildSetup()
    monkeypatch.setattr(royals_gui.filedialog, "askopenfilename", lambda **kw: str(path))
    window.openGame(); window.root.update()

    assert window.randomEntry is False
    assert window.entrySeed is None
    assert window.errors == []


####### A search that falls over #######
# The generation token above drops an answer that arrives too late. This is the other half:
# an answer that never arrives at all, because the search raised instead of returning.
#
# pollAI reschedules until the queue has something in it, so the worker owes it a value on
# every path. A failure that escapes the worker's handler is therefore not one error but a
# permanent one -- the queue stays empty, aiBusy stays set, and the window waits on a search
# that finished long ago with nothing to show for it.


class FakePanic(BaseException):
    """Stands in for pyo3_runtime.PanicException.

    A panic in the compiled engine derives from BaseException *on purpose*, so that it cannot
    be swallowed by a passing `except Exception` -- which is precisely what the worker used to
    catch. Deriving this from BaseException rather than Exception is the whole test: an
    `except Exception` worker leaves it uncaught, the thread dies mid-`put`, and nothing ever
    reaches the queue.
    """


@pytest.mark.parametrize("failure", [FakePanic, RuntimeError],
                         ids=["panic-from-the-compiled-engine", "an-ordinary-exception"])
def test_a_search_that_raises_is_reported_rather_than_left_hanging(window, monkeypatch, failure):
    def explode(*args, **kwargs):
        raise failure("the search fell over")

    monkeypatch.setattr(AI, "takeTurn", explode)

    # A dealt opening, so the computer's first search starts as soon as the game does.
    window.modeVar.set(2); window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()

    settled = pump(window.root, 3.0, until=lambda: not window.aiBusy)
    assert settled, "the window is still waiting on a search that already failed"

    # aiBusy is what boardClick's guard reads, so a stuck one is a window that ignores the
    # mouse as well as one that never moves.
    assert window.aiBusy is False
    assert window.phase == "over"
    assert "the search fell over" in window.logText.get("1.0", "end"), \
        "the failure never reached the log"
    assert window.errors == []


####### A side's own depth #######
# A 0 player game used to hand one depth to whichever side was to move, which made the mode a
# mirror match by construction -- the same search answering both sides of the same position.
# `aiDepths` is indexed by `contr`, and the tests below are about the two halves of that: that
# the number actually reaches the search under the right side's name, and that the equal case
# stays equal, since every other mode relies on it.


def test_both_sides_share_one_depth_unless_asked_otherwise(window):
    """Black's control holds a value whether or not it is in use, so the check that keeps a
    0 player game playing as it always has is that the value is *ignored* until it is asked
    for. Nothing else in the window looks at splitDepthVar."""
    window.modeVar.set(2); window.entryVar.set(1)
    window.depthVar.set(1); window.blackDepthVar.set(2)      # set, but the box is not ticked
    window.startGame(); window.root.update()

    assert window.aiDepths == [1, 1], "black's depth was used without being asked for"
    assert window.errors == []


def test_each_side_searches_to_its_own_depth(window, monkeypatch):
    """The one that matters. `aiDepths` being right is not the claim -- what is being tested
    is that the right entry of it reaches `takeTurn`, under the side that is to move."""
    asked = []
    real = AI.takeTurn

    def counted(board, contr, depth, *a, **kw):
        asked.append((contr, depth))
        # answered shallowly, since which move comes back is not what is being asked
        return real(board, contr, 1)

    monkeypatch.setattr(AI, "takeTurn", counted)

    # A dealt opening, so play begins as soon as the game does.
    window.modeVar.set(2); window.entryVar.set(1)
    window.splitDepthVar.set(1)
    window.depthVar.set(3); window.blackDepthVar.set(5)
    window.startGame(); window.root.update()

    # Four searches take about a second even with no compiled engine, since `counted`
    # answers at depth 1. The budget is enormous next to that on purpose: it is only ever
    # spent by a test that is going to fail anyway, and a tighter one turns a busy machine
    # into a red build. This ran out at fifteen seconds exactly once, in a full-suite run
    # that passed on repeat.
    pump(window.root, 60.0, until=lambda: len(asked) >= 4)

    assert len(asked) >= 4, "the game never got as far as four searches: %r" % (asked,)
    assert {contr for contr, _ in asked} == {0, 1}, "only one side ever moved"
    assert all(depth == (3 if contr == 0 else 5) for contr, depth in asked), \
        "a side searched to the other one's depth: %r" % (asked,)
    assert window.errors == []


def test_the_split_depth_offer_belongs_to_the_0_player_game(window):
    """In a 1 player game the second control would be asking how deep the human thinks."""
    window.modeVar.set(1); window.splitDepthVar.set(1); window.refreshSetup()
    assert not window.splitDepthCheck.enabled
    assert not any(b.enabled for b in window.blackDepthButtons)

    window.modeVar.set(2); window.refreshSetup()
    assert window.splitDepthCheck.enabled
    assert all(b.enabled for b in window.blackDepthButtons)

    window.splitDepthVar.set(0); window.refreshSetup()
    assert not any(b.enabled for b in window.blackDepthButtons), \
        "black's depth stayed live with nothing asking for it"

    # And a 1 player game left ticked still plays the one depth, since startGame reads the
    # mode rather than trusting the box to have been greyed.
    window.buildSetup()
    window.modeVar.set(1); window.sideVar.set(0); window.entryVar.set(1)
    window.splitDepthVar.set(1)
    window.depthVar.set(1); window.blackDepthVar.set(2)
    window.startGame(); window.root.update()
    assert window.aiDepths == [1, 1]


def test_a_handicap_match_says_so_in_the_file_it_saves(window):
    """The header is the only place a saved game can say how it was produced -- the move list
    cannot, since a move found at depth 5 looks exactly like one found at depth 2."""
    window.modeVar.set(2); window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()
    assert "the computer against itself at depth 1" in window.recordNote()

    window.buildSetup()
    window.modeVar.set(2); window.entryVar.set(1)
    window.splitDepthVar.set(1)
    window.depthVar.set(1); window.blackDepthVar.set(2)
    window.startGame(); window.root.update()
    assert "white at depth 1 and black at depth 2" in window.recordNote()
    assert "White searches to depth 1, black to depth 2." \
        in window.logText.get("1.0", "end"), "the game log never mentioned the handicap"


####### Holding play, and taking a move back #######
# `paused` is one flag read by `advance`, which every turn goes through. The tests that earn
# their place are the two where something is already in flight: a search that has to have its
# answer dropped, and a take-back that has to leave the ko history right.


def playTwoHanded(win, moves=3):
    """Play a few moves through the window's own handlers, both sides, no worker thread."""
    win.modeVar.set(0)              # 2 player
    win.entryVar.set(1)             # dealt opening, so play starts at once
    win.startGame()
    win.root.update()
    for _ in range(moves):
        if win.phase != "play" or not win.legalOrigins:
            break
        origin = sorted(win.legalOrigins)[0]
        win.select(origin)
        jumps, pushes = win.moveArray[0], win.moveArray[1]
        if jumps: win.playClick(jumps[0] + 1)
        elif pushes: win.freeVar.set(0); win.playClick(pushes[0] + 1)
        else: break
        win.root.update()


def test_a_two_player_game_has_no_pause_button(window):
    """Nothing runs between turns, so there would be nothing for a hold to stop."""
    playTwoHanded(window, moves=0)
    assert window.pauseButton is None
    assert window.rewindButton is not None


def test_pausing_mid_search_drops_the_answer(window, monkeypatch):
    """The one with a thread in it.

    A search cannot be called off, so a hold has to drop its answer instead -- by the same
    generation token that drops one from a game the player walked away from. `aiBusy` has to
    come down with it, or the hold would never lift: advance returns on that guard first.
    """
    released = threading.Event()
    blocked(monkeypatch, "takeTurn", released)

    window.modeVar.set(1); window.sideVar.set(0)
    window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()
    assert window.aiBusy, "the computer should be thinking"

    board, plies = window.board, len(window.record)
    window.togglePause(); window.root.update()

    assert window.paused is True
    assert window.aiBusy is False, "a hold that left this set would never lift"
    assert window.pauseButton.cget("text") == "RESUME"

    released.set()
    pump(window.root, 3.0)

    assert window.board == board, "the dropped answer moved the board anyway"
    assert len(window.record) == plies
    assert window.errors == []


def test_a_held_game_plays_nothing_and_resuming_plays_on(window, monkeypatch):
    released = threading.Event()
    blocked(monkeypatch, "takeTurn", released)

    window.modeVar.set(2); window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()
    window.togglePause(); window.root.update()
    released.set()

    plies = len(window.record)
    pump(window.root, 1.5)
    assert len(window.record) == plies, "a held game played on"

    window.togglePause(); window.root.update()
    assert window.paused is False
    assert window.pauseButton.cget("text") == "PAUSE"
    assert pump(window.root, 10.0, until=lambda: len(window.record) > plies), \
        "resuming did not start the game again"
    assert window.errors == []


def test_a_held_game_can_be_reviewed_and_played_on_from(window, monkeypatch):
    """Reviewing needs the search to be down, which is exactly what a hold does. And PLAY ON
    out of a held review lifts the hold, because carrying on is what it is for."""
    released = threading.Event()
    blocked(monkeypatch, "takeTurn", released)

    window.modeVar.set(2); window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()
    window.togglePause(); window.root.update()
    released.set()
    pump(window.root, 0.5)

    window.startReview()
    assert window.review is not None, "a held game would not open for review"
    assert not window.rewindButton.enabled, "REWIND should stand down for the review"

    monkeypatch.setattr(royals_gui.messagebox, "askyesno", lambda *a, **kw: True)
    window.playOn()
    window.root.update()

    assert window.review is None
    assert window.paused is False, "PLAY ON left the game held"
    assert window.errors == []


def test_rewind_takes_back_one_ply_and_leaves_the_game_held(window, monkeypatch):
    """1 player rather than 0, and that is about the test harness rather than the feature.

    A 0 player game at depth 1 runs to the end inside a single `root.update()` -- each
    answer schedules the next search, and update() drains the whole cascade before `pump`
    gets to look at its condition. A 1 player game stops on its own when it reaches the
    human's turn, which is the only place a test can reliably take hold of one.
    """
    released = threading.Event()
    blocked(monkeypatch, "takeTurn", released)
    monkeypatch.setattr(royals_gui.messagebox, "askyesno", lambda *a, **kw: True)

    # White is the human, so black -- the computer -- moves first and the game comes back to
    # rest after exactly one move.
    window.modeVar.set(1); window.sideVar.set(0)
    window.entryVar.set(1); window.depthVar.set(1)
    window.startGame(); window.root.update()
    released.set()
    assert pump(window.root, 10.0,
                until=lambda: len(window.record) > len(window.enterSteps)), \
        "the computer never played its move"
    window.togglePause(); window.root.update()
    assert window.paused is True

    before = list(window.record)
    assert len(before) > len(window.enterSteps), "no move to take back"

    window.rewind(); window.root.update()

    assert window.record == before[:-1]
    assert window.paused is True, "a take-back that resumed would be played over at once"
    plies = len(window.record)
    pump(window.root, 1.0)
    assert len(window.record) == plies, "the game carried on after a take-back"
    assert window.errors == []


def test_rewind_rebuilds_the_ko_history(window, monkeypatch):
    """The half of a take-back that fails without saying so."""
    monkeypatch.setattr(royals_gui.messagebox, "askyesno", lambda *a, **kw: True)
    playTwoHanded(window, moves=3)
    assert len(window.record) > len(window.enterSteps), "no move to take back"

    dropped = window.board
    # Said before the rewind so this cannot pass by accident: the board it is about to check
    # the absence of is in the set right now, put there by the move being taken back.
    assert dropped in Engine.koTrack

    window.rewind(); window.root.update()

    assert dropped not in Engine.koTrack, "the taken-back board stayed in the ko history"
    assert window.board in Engine.koTrack, "the board it went back to is not in it"
    assert window.errors == []


def test_rewind_asks_once_a_game(window, monkeypatch):
    asked = []
    monkeypatch.setattr(royals_gui.messagebox, "askyesno",
                        lambda *a, **kw: asked.append(a) or True)
    playTwoHanded(window, moves=3)

    window.rewind(); window.root.update()
    assert len(asked) == 1, "the first take-back should ask"
    window.rewind(); window.root.update()
    assert len(asked) == 1, "it asked again in the same game"

    # A new game is a new decision.
    playTwoHanded(window, moves=3)
    window.rewind(); window.root.update()
    assert len(asked) == 2
    assert window.errors == []


def test_a_declined_rewind_changes_nothing(window, monkeypatch):
    monkeypatch.setattr(royals_gui.messagebox, "askyesno", lambda *a, **kw: False)
    playTwoHanded(window, moves=3)
    before, board, ko = list(window.record), window.board, set(Engine.koTrack)

    window.rewind(); window.root.update()

    assert window.record == before
    assert window.board == board
    assert set(Engine.koTrack) == ko
    assert window.rewindWarned is False, "a refusal should not count as being warned"
    assert window.errors == []


def test_rewind_needs_no_pause_in_a_two_player_game(window, monkeypatch):
    monkeypatch.setattr(royals_gui.messagebox, "askyesno", lambda *a, **kw: True)
    playTwoHanded(window, moves=3)

    assert window.paused is False
    assert window.rewindButton.enabled, "nothing runs between turns, so nothing to hold first"
    before = list(window.record)
    window.rewind(); window.root.update()

    assert window.record == before[:-1]
    # And the turn really was handed back: there is somebody to move and squares to move from.
    assert window.legalOrigins
    assert window.errors == []


def test_rewind_is_dark_with_nothing_to_give_back(window):
    """The opening is not a move, so rewinding into it would land on a placement."""
    playTwoHanded(window, moves=0)
    assert len(window.record) == len(window.enterSteps)
    assert not window.rewindButton.enabled

    playTwoHanded(window, moves=1)
    assert window.rewindButton.enabled


def test_rewind_is_refused_while_reviewing(window, monkeypatch):
    monkeypatch.setattr(royals_gui.messagebox, "askyesno", lambda *a, **kw: True)
    playTwoHanded(window, moves=3)
    before = list(window.record)

    window.startReview()
    window.rewind()

    assert window.record == before
    assert "reviewing" in window.hintLabel.cget("text")
    window.exitReview()
    assert window.errors == []


def test_the_assistant_is_dark_while_held(window, monkeypatch):
    """askEngine sets aiBusy, and a hint asked for during a hold would leave RESUME with
    nothing to do -- advance returns on that guard before it reaches the one for the hold."""
    released = threading.Event()
    blocked(monkeypatch, "takeTurn", released)

    window.modeVar.set(1); window.sideVar.set(0)
    window.entryVar.set(1); window.depthVar.set(1)
    window.assistVar.set(1)
    window.startGame(); window.root.update()
    window.togglePause(); window.root.update()
    released.set()
    pump(window.root, 0.5)

    window.askDepth()
    window.askEngine(1)
    assert window.aiBusy is False, "the assistant started a search during a hold"

    window.togglePause()
    assert window.paused is False
    assert window.errors == []
