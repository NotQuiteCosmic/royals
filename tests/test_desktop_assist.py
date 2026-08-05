"""The assistant: asking the engine for a move, and being free to ignore it.

Two properties carry this feature, and they pull in opposite directions, which is why they are
both written down here.

**A suggestion changes nothing until it is taken.** It is a search result drawn on the board,
not a move; the position, the record, the turn and the ko history are all exactly as they were
while it is showing, and stay that way if it is dismissed. The failure this guards against is
the quiet one -- a preview that has already half-played itself, so declining leaves the game
somewhere nobody chose.

**A suggestion that is taken is a move like any other.** It goes through `commit`, so it is
ko-checked, recorded, counted and handed on exactly as a clicked move is. There is no second
path into the game.

The rest is about who may ask. The rule is not a list of modes: the assistant belongs to
whoever is on move, and is offered only when that is a person. A 0 player game therefore never
offers it, and that falls out rather than being special-cased.

Needs a display, and takes the shared window from tests/conftest.py.
"""

import pathlib
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop"))

from royals_engine import record as R

royals_gui = pytest.importorskip("royals_gui")


def settle(window, seconds=40.0):
    """Let the worker thread answer. The search runs off the event loop like any other."""
    end = time.time() + seconds
    while window.aiBusy and time.time() < end:
        window.root.update()
        time.sleep(0.02)
    window.root.update()
    return not window.aiBusy


def assistedGame(window, mode=0, depth=1):
    """A game with the assistant on. Dealt opening, so play starts at once."""
    window.modeVar.set(mode)
    window.entryVar.set(1)
    window.assistVar.set(1)
    window.depthVar.set(depth)
    window.startGame()
    settle(window)
    return window


def showing(row):
    return bool(row.winfo_manager())


####### Who may ask #######

def test_the_assistant_is_offered_when_a_person_is_on_move(window):
    assistedGame(window)
    assert window.assist is True
    assert showing(window.askRow)
    assert window.errors == []


def test_a_game_the_computer_plays_by_itself_never_offers_it(window):
    """Not a mode check but a consequence: there is no turn at which a person is on move, so
    there is nobody the question could be coming from."""
    assistedGame(window, mode=2)

    assert window.assist is False, "the setting is ignored rather than obeyed here"
    assert not showing(window.askRow)

    window.askDepth()
    window.root.update()
    assert not showing(window.depthRow), "the depth picker opened with nobody to use it"


def test_two_people_may_both_use_it(window):
    """A shared screen, so a suggestion is not a secret from the opponent -- which is the
    whole reason it is allowed here."""
    assistedGame(window, mode=0)
    first = window.contr

    window.askEngine(1)
    assert settle(window)
    window.takeHint()
    settle(window)

    assert window.contr != first, "the turn should have passed"
    assert showing(window.askRow), "and the other player should be offered it too"


def test_it_is_not_offered_when_it_was_not_asked_for(window):
    window.modeVar.set(0)
    window.entryVar.set(1)
    window.assistVar.set(0)
    window.startGame()
    window.root.update()

    assert window.assist is False
    assert not showing(window.askRow)


####### A suggestion changes nothing #######

def test_a_suggestion_leaves_the_game_exactly_where_it_was(window):
    assistedGame(window)
    before = (window.board, list(window.record), window.turn, window.contr)

    window.askDepth()
    window.root.update()
    assert showing(window.depthRow)

    window.askEngine(2)
    assert settle(window), "the search never came back"

    assert window.hint is not None, "nothing was suggested"
    assert (window.board, list(window.record), window.turn, window.contr) == before, \
        "the suggestion moved the game before anybody accepted it"
    assert showing(window.hintRow)
    assert window.errors == []


def test_declining_leaves_it_where_it_was_too(window):
    assistedGame(window)
    before = (window.board, list(window.record), window.turn, window.hintsTaken)

    window.askEngine(2)
    assert settle(window)
    window.dropHint()
    window.root.update()

    assert window.hint is None
    assert (window.board, list(window.record), window.turn, window.hintsTaken) == before
    assert showing(window.askRow), "and it can be asked again"


def test_picking_a_piece_up_puts_the_suggestion_away(window):
    """Starting your own move is an answer to the suggestion, so it stops arguing with it."""
    assistedGame(window)
    window.askEngine(2)
    assert settle(window)
    assert window.hint is not None

    window.select(sorted(window.legalOrigins)[0])
    window.root.update()
    assert window.hint is None


def test_a_suggestion_does_not_outlive_the_turn_it_was_about(window):
    assistedGame(window)
    window.askEngine(2)
    assert settle(window)

    # play something else entirely, by hand
    origin = sorted(window.legalOrigins)[0]
    window.select(origin)
    jumps = window.moveArray[0]
    if not jumps: pytest.skip("this opening's first origin has no jump to play")
    window.playClick(jumps[0] + 1)
    settle(window)

    assert window.hint is None, "last turn's answer was about last turn's position"


####### A suggestion that is taken is a move #######

def test_taking_it_plays_the_move_and_records_it(window):
    assistedGame(window)
    plies = len(window.record)

    window.askEngine(2)
    assert settle(window)
    suggested = window.hint["board"]
    window.takeHint()
    settle(window)

    assert window.board == suggested
    assert len(window.record) == plies + 1, "an accepted suggestion is a ply like any other"
    assert window.hintsTaken == 1
    assert window.errors == []


def test_the_record_of_an_assisted_game_still_replays(window):
    """The move list cannot tell an assisted move from a played one -- which is the point,
    and also why it has to keep replaying like any other record."""
    assistedGame(window)
    for _ in range(3):
        window.askEngine(1)
        if not settle(window): break
        window.takeHint()
        settle(window)
        if window.phase != "play": break

    spots = R.positions(window.record)
    assert len(spots) == len(window.record) + 1
    assert spots[-1].board == window.board


####### What the file says about itself #######

def test_the_header_says_the_engine_was_asked(window):
    assistedGame(window)
    assert "engine" not in window.recordNote(), "nothing has been asked for yet"

    window.askEngine(2)
    assert settle(window)
    window.takeHint()
    settle(window)

    note = window.recordNote()
    assert "1 move from the engine" in note, note


def test_the_header_counts_them(window):
    assistedGame(window)
    taken = 0
    for _ in range(2):
        window.askEngine(1)
        if not settle(window) or window.hint is None: break
        window.takeHint()
        settle(window)
        taken += 1
        if window.phase != "play": break

    if taken < 2: pytest.skip("the game ended before two suggestions could be taken")
    assert "2 moves from the engine" in window.recordNote()


def test_a_game_played_without_help_says_nothing_about_it(window):
    assistedGame(window)
    origin = sorted(window.legalOrigins)[0]
    window.select(origin)
    jumps = window.moveArray[0]
    if jumps:
        window.playClick(jumps[0] + 1)
        settle(window)

    assert window.hintsTaken == 0
    assert "engine" not in window.recordNote()
