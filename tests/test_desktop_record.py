"""The desktop's move recorder, checked by replaying what it writes.

The desktop is the front end that had no record of a game at all: it called the executors
directly and wrote down only boards, into the ko set. Teaching it to write its moves down
means teaching it, at six separate call sites, which things are plies -- and the one that
gets forgotten is the ply where nothing happened. A side with nowhere to enter and a side
with no legal move both consume a ply and both write "--".

**Leaving one out does not fail.** The record still reads, still replays, and replays every
move after the gap as the other side's, into a real position that nobody played to. So the
check cannot be "does it parse"; it has to be "does it replay into the game that was
actually played", which is what this file does two ways:

    the boards royals_engine.record walks it back to  ==  the boards the window drew
    royals_web.game.replay accepts it                     -- the server's own rules

The second is what makes a file saved here openable in the browser, and vice versa.

This drives the real window, because the recording decisions live in it and a test that
re-implemented them would be testing itself. That needs a display: tkinter is importable
everywhere but `Tk()` raises without one, so this file skips on a headless CI runner and
runs on a desktop. The count in CLAUDE.md says so.
"""

import pathlib
import re
import sys

import pytest

tk = pytest.importorskip("tkinter")

from royals_engine import engine as Engine
from royals_engine import hasher as Hasher
from royals_engine import notation as N
from royals_engine import record as R

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop"))


@pytest.fixture
def window():
    """A real RoyalsWindow, or a skip where there is no display to put one on."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("no display for tkinter: %s" % (exc,))

    root.withdraw()
    import royals_gui

    win = royals_gui.RoyalsWindow(root)
    try:
        yield win
    finally:
        root.destroy()


def play_two_handed(win, plies=40):
    """Play a whole game through the window's own click handlers, both sides.

    Two players rather than one, so nothing runs on the AI's worker thread and every ply
    is committed by the time the call returns -- a test that had to pump an event loop
    would be testing the pump.

    Boards are collected as the window draws them, which is the account the record has to
    reproduce. The padding matters: `enterStep` writes a "--" for a skipped placement
    without anybody clicking anything, so one click can add more than one ply.
    """
    win.modeVar.set(0)              # 2 player
    win.startGame()

    boards = [Hasher.Entering_Board()]

    def catch_up():
        while len(boards) <= len(win.record):
            boards.append(win.board)

    catch_up()

    while win.phase == "entering":
        win.enterClick(win.entryOptions[0])
        catch_up()

    for _ in range(plies):
        if win.phase != "play":
            break
        # A pass is taken by playStep itself and leaves nothing to click.
        if not win.legalOrigins:
            break
        origin = sorted(win.legalOrigins)[0]
        win.select(origin)
        before = len(win.record)
        played = offer_a_move(win, origin)
        catch_up()
        if not played or len(win.record) == before:
            # Ko took the move back, or the square had only a break this test did not
            # reach. Either way nothing was recorded, which is itself the thing to check.
            assert len(win.record) == before
            break

    return boards


def offer_a_move(win, origin):
    """Play the first thing the selected square can do. True if something was played."""
    jumps, pushes = win.moveArray[0], win.moveArray[1]
    frees, breaks = win.moveArray[5], win.moveArray[2]

    if jumps:
        win.playClick(jumps[0] + 1)
        return True
    if frees:
        win.freeVar.set(1)
        win.playClick(frees[0] + 1)
        return True
    if pushes:
        win.freeVar.set(0)
        win.playClick(pushes[0] + 1)
        return True
    if breaks:
        win.playBreak(breaks[0])
        return True
    return False


# ---------------------------------------------------------------------------

def test_the_window_records_every_ply_it_plays(window):
    boards = play_two_handed(window)

    assert window.record, "a played game must leave a record"
    assert len(boards) == len(window.record) + 1
    # Twelve entering steps, and one token for each of them whether or not a piece landed.
    assert len(window.record) >= len(Engine.enteringSequence())


def test_the_record_walks_back_to_the_boards_the_window_drew(window):
    """The one that catches a missing pass: a gap shifts every later ply onto the wrong side."""
    boards = play_two_handed(window)
    spots = R.positions(window.record)

    assert len(spots) == len(boards)
    for spot, drawn in zip(spots, boards):
        assert spot.board == drawn, "ply %d of %d" % (spot.ply, len(boards) - 1)


def test_a_desktop_file_loads_on_the_server(window):
    """A game saved here is a game the browser can open, and the other way about."""
    web = pytest.importorskip("royals_web.game")

    play_two_handed(window)
    text = N.encode_game(window.record, notes=[window.recordNote()])

    # Through the file, not around it -- the point is that what lands on disk is what the
    # server takes.
    moves = N.decode_game(text)
    assert moves == window.record

    # Both seats claimed: a game whose second seat was never taken never started, and
    # replay is right to refuse a move list for one.
    seats = {web.BLUE: web.Seat(kind=web.HUMAN, claimed=True),
             web.RED: web.Seat(kind=web.HUMAN, claimed=True)}
    replayed = web.replay(
        id="d" * 32, mode="human", ai_depth=None, entry_seed=1, entry_noise=0.5,
        moves=moves, seats=seats, ply=len(moves))

    assert replayed.moves == window.record
    assert replayed.board == window.board


def test_a_random_opening_needs_no_clicks_and_is_still_recorded(window):
    """The dealt opening, which is the one entering nobody clicks through.

    A placement made for you is a ply exactly like one you chose, and this is the check
    that says so -- the failure it exists for is a branch that drops pieces on the board
    and forgets to write them down, which does not look wrong until every later move
    replays as the other side's.
    """
    window.modeVar.set(0)               # 2 player, so nothing runs on a worker thread
    window.entryVar.set(1)              # squares chosen at random
    window.startGame()

    # startGame -> advance -> enterStep places all twelve without returning to the loop.
    assert window.phase == "play"
    assert window.entryOptions == [], "nothing was ever clickable"
    assert len(window.record) == len(Engine.enteringSequence())
    assert all(token.startswith("@") for token in window.record)

    spots = R.positions(window.record)
    assert spots[-1].board == window.board


def test_a_dealt_game_is_a_file_the_server_reads(window):
    web = pytest.importorskip("royals_web.game")

    window.modeVar.set(0)
    window.entryVar.set(1)
    window.startGame()

    moves = N.decode_game(N.encode_game(window.record, notes=[window.recordNote()]))
    seats = {web.BLUE: web.Seat(kind=web.HUMAN, claimed=True),
             web.RED: web.Seat(kind=web.HUMAN, claimed=True)}
    # random_entry deliberately left false: the placements are in the move list, and the
    # server must arrive at this position by replaying them rather than by dealing its own.
    replayed = web.replay(
        id="e" * 32, mode="human", ai_depth=None, entry_seed=1, entry_noise=0.5,
        moves=moves, seats=seats, ply=len(moves))

    assert replayed.board == window.board


def test_a_move_the_ko_rule_takes_back_is_not_recorded(window):
    """A move that was taken back never happened, and a record holding it is a wrong game."""
    play_two_handed(window, plies=6)
    before = list(window.record)

    # Hand commit() a board the game has already stood in. That is exactly what the ko
    # check is for, and the record must not grow.
    stood_in = R.positions(window.record)[-2].board
    assert window.commit(stood_in, [1, 2], (1, "jump", 1, False)) is False
    assert window.record == before


def test_saving_validates_what_it_is_about_to_write(window, tmp_path, monkeypatch):
    """A recorder with a bug is caught at save time, with the ply named."""
    play_two_handed(window, plies=4)
    window.record.append("Zq9q9")

    shown = []
    import royals_gui
    monkeypatch.setattr(royals_gui.filedialog, "asksaveasfilename",
                        lambda **kw: str(tmp_path / "game.txt"))
    monkeypatch.setattr(royals_gui.messagebox, "showerror",
                        lambda *a, **kw: shown.append(a))

    window.saveGame()
    assert shown, "an unwritable record must say so rather than land on disk"
    assert not (tmp_path / "game.txt").exists()


def test_save_and_open_round_trip_through_a_real_file(window, tmp_path, monkeypatch):
    boards = play_two_handed(window)
    path = tmp_path / "game.txt"

    import royals_gui
    monkeypatch.setattr(royals_gui.filedialog, "asksaveasfilename", lambda **kw: str(path))
    window.saveGame()
    assert path.exists()

    monkeypatch.setattr(royals_gui.filedialog, "askopenfilename", lambda **kw: str(path))
    window.openGame()

    assert window.review is not None
    assert window.record == window.review["moves"]
    # Opens at the end of the game, which is the position it left off at.
    assert window.review["at"] == len(window.record)
    assert window.board == boards[-1]


def test_review_steps_without_touching_the_ko_set(window):
    """Reviewing is a slideshow. Nothing in it may reach the state a live game runs on."""
    play_two_handed(window)
    ko_before = set(Engine.koTrack)
    boards = R.positions(window.record)

    window.startReview()
    assert window.phase == "review"

    for at in (0, 1, len(boards) // 2, len(boards) - 1, 0):
        window.reviewGoTo(at)
        assert window.board == boards[at].board

    # Stepping past either end clamps rather than raising.
    window.reviewGoTo(-5)
    assert window.review["at"] == 0
    window.reviewGoTo(10 ** 6)
    assert window.review["at"] == len(boards) - 1

    assert set(Engine.koTrack) == ko_before, "review must not touch the ko set"


def test_leaving_a_review_puts_the_game_back_as_it_was(window):
    play_two_handed(window)
    before = (window.phase, window.board, list(window.lastMove),
              set(window.legalOrigins), window.turn)

    window.startReview()
    window.reviewGoTo(0)
    window.exitReview()

    assert window.review is None
    assert (window.phase, window.board, list(window.lastMove),
            set(window.legalOrigins), window.turn) == before


# ---------------------------------------------------------------------------
# The game log's numbering
# ---------------------------------------------------------------------------

KO_NOTE = "Ko — move taken back."


def logged_marks(win, keep_retaken=False):
    """(number, text) for every log line that is filed under a turn number.

    The asides -- the score line, the ko note, the instructions at the top -- carry no
    mark, and must not: a number in front of them would claim they were plies.

    One numbered line is not a ply, and it is deliberate. `playHuman` logs a move *before*
    `commit` gets to refuse it, so that a move the ko rule takes back reads as the move
    followed by the note taking it back rather than the other way about. The number is
    then reused by whatever is played instead, which is what a person would expect to see
    and is not a ply of the game. Dropped here unless a caller asks for it.
    """
    marks = []
    lines = [line for line in win.logText.get("1.0", "end").splitlines() if line.strip()]

    for index, line in enumerate(lines):
        found = re.match(r"\s*(\d+)\.\s+(.*)", line)
        if not found:
            continue
        retaken = index + 1 < len(lines) and lines[index + 1].strip() == KO_NOTE
        if retaken and not keep_retaken:
            continue
        marks.append((int(found.group(1)), found.group(2)))

    return marks


def test_the_log_files_every_ply_under_a_number(window):
    play_two_handed(window)

    assert len(logged_marks(window)) == len(window.record), (
        "every ply is logged exactly once, and only plies are numbered")


def test_a_move_the_ko_rule_takes_back_keeps_its_number_for_the_next_try(window):
    """The one numbered line that is not a ply, and why it reads correctly anyway."""
    play_two_handed(window, plies=6)
    before = logged_marks(window, keep_retaken=True)
    turn = window.turn

    # Exactly what playHuman does: write the move down, then let commit decide. Calling
    # commit on its own would leave the ko note under a move that really was played, which
    # is not a sequence the window can produce.
    window.log(window.turnMark() + "White: a move the ko rule will refuse", "white")
    stood_in = R.positions(window.record)[-2].board
    assert window.commit(stood_in, [1, 2], (1, "jump", 1, False)) is False

    kept = logged_marks(window, keep_retaken=True)
    assert len(kept) == len(before) + 1
    assert kept[-1][0] == turn, "the refused move is filed under the turn it tried to be"
    assert window.turn == turn, "and the turn does not advance, so the number is reused"

    # The record is what a saved game is made of, and it did not move.
    assert logged_marks(window) == before
    assert len(logged_marks(window)) == len(window.record)


def test_the_log_numbers_are_the_ones_a_player_would_say(window):
    """Two runs, restarting at the first move -- not one count through the whole record.

    A game's seventh move is its nineteenth ply. Numbering the log by ply would put "19"
    beside it, which is right about the record and wrong about the game.
    """
    play_two_handed(window)
    numbers = [n for n, _ in logged_marks(window)]
    steps = len(R.ENTER_STEPS)

    assert numbers[:steps] == list(range(1, steps + 1)), "the placements, one to twelve"
    assert numbers[steps:] == list(range(1, len(numbers) - steps + 1)), "then the moves"

    # And the same numbering the review UI shows for the same plies.
    for index, (number, _text) in enumerate(logged_marks(window)):
        assert R.turn_of_ply(index)[1] == number, "ply %d" % (index + 1)


def test_the_log_marks_the_side_the_record_says_played(window):
    """A mark against the wrong side is how a missing ply would first show itself."""
    play_two_handed(window)
    for index, (_number, text) in enumerate(logged_marks(window)):
        expected = ("White", "Black")[R.side_of_ply(index)]
        assert text.startswith(expected), "ply %d: %r" % (index + 1, text)


def test_the_review_labels_a_position_by_its_move_number(window):
    import royals_gui

    play_two_handed(window)
    window.startReview()
    steps = len(R.ENTER_STEPS)

    window.reviewGoTo(0)
    assert window.reviewMark.cget("text") == "Start"

    window.reviewGoTo(3)
    assert window.reviewMark.cget("text") == "Entering 3 of %d" % steps

    window.reviewGoTo(steps + 1)
    assert window.reviewMark.cget("text") == "Move 1"
    # The ply and the move are different numbers, and both are on screen.
    assert window.reviewPly.cget("text") == "ply %d of %d" % (steps + 1, len(window.record))

    window.reviewGoTo(steps + 7)
    assert window.reviewMark.cget("text") == "Move 7"
    assert royals_gui.reviewLabel(window.review["spots"][steps + 7], steps) == "Move 7"
