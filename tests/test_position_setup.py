"""Setting a position up by hand, saving it, and playing from it.

Two halves, and the file is split by what each needs rather than by what each is about.

The codec half runs everywhere: `notation.encode_board` / `decode_board` and the position
file they sit inside. The editor half drives the real window and takes the shared root from
`tests/conftest.py`, so it skips on a headless runner the way every other display test does.

**The format is not new, and the first test is the reason that matters.** The square syntax
`encode_board` writes is the one `tests/regress.py`'s `boardText` has always emitted into
every line of `golden_moves.txt`, and there has been a decoder for it since the Rust port --
`engine-rs/tests/golden_search.rs` parses it to check the search golden. What was missing was
the Python decoder, so the format could be written here and read only over there. The two
encoders are pinned together below, because a drift would surface as a failure in
`golden_search.rs` and nothing there would point back to this file.

The other thing worth saying: a game begun from a position can be neither saved nor
reviewed, and both refusals are tested. A RAN record derives every ply's side from its index
on the assumption of exactly twelve entering plies (`record.turn_of_ply`), so a game that
started mid-board has no place in the format. Saving one would write a file that replays as a
different game; reviewing one would walk it from `Entering_Board` and show a game nobody
played -- convincingly, which is worse than an error.
"""

import pathlib
import random
import sys

import pytest

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher
from royals_engine import notation as N

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import regress                                   # noqa: E402  -- for boardText, see below


def sweep_boards(seed=57, turns=8):
    """Positions from seeded random play -- captures, prisoners, stacks, the messy ones."""
    board = regress.enteredBoard(seed)
    rng = random.Random(seed)
    Engine.koReset()
    AI.newGame()
    yield board
    for turn in range(turns):
        moves = AI.listAllMoves(board, turn % 2)
        if not moves: return
        board = AI.performOneStep(board, turn % 2, moves[rng.randrange(len(moves))])
        yield board


def board_of(**squares):
    """A board from `alg=(side, dragon, spy, pawns, royal, capSpy, capPawns, prisFlag)`."""
    board = list(Hasher.EMPTY_BOARD)
    for alg, spec in squares.items():
        board[Hasher.AlgebraToSquare(alg) - 1] = Hasher.Build_Space(*spec)
    return tuple(board)


# ---------------------------------------------------------------------------
# The codec
# ---------------------------------------------------------------------------

def test_the_text_form_is_the_one_the_goldens_already_use():
    """encode_board must equal regress.boardText, square for square and byte for byte.

    This is the anti-drift pin. golden_moves.txt is written with boardText and read back in
    Rust with the parser in golden_search.rs; encode_board is a third party to the same
    format. If it drifts, positions saved here stop being readable by the tool that reads
    the goldens, and the failure shows up a long way from the change that caused it.
    """
    checked = 0
    for board in sweep_boards():
        assert N.encode_board(board) == regress.boardText(board)
        checked += 1
    assert checked > 5, "the sweep stopped producing boards"


def test_a_board_survives_the_round_trip():
    for board in sweep_boards():
        assert N.decode_board(N.encode_board(board)) == board


def test_an_empty_board_has_a_name_of_its_own():
    # A zero-length line is not obviously a board, so it is written as a sentinel.
    assert N.encode_board(Hasher.EMPTY_BOARD) == N.BOARD_EMPTY
    assert N.decode_board(N.BOARD_EMPTY) == Hasher.EMPTY_BOARD


def test_a_position_file_round_trips_with_its_turn():
    board = board_of(d4=(0, 0, 1, 4, 1), e4=(1, 0, 1, 0, 0))
    for side in (0, 1):
        text = N.encode_position(board, side, notes=["a study", "two lines"])
        assert text.startswith(N.POSITION_MAGIC)
        assert N.decode_position(text) == (board, side)


def test_notes_are_dropped_on_read():
    # Same contract encode_game's notes have: anything a reader must have goes in the data.
    board = Hasher.Entering_Board()
    plain = N.decode_position(N.encode_position(board, 0))
    noted = N.decode_position(N.encode_position(board, 0, notes=["something"]))
    assert plain == noted


def test_white_and_black_are_accepted_for_blue_and_red():
    # The desktop's buttons say White and Black; the engine and the record say blue and red.
    # A person hand-editing a file should not have to know that the window renamed them.
    body = "d4:0,0,1,4,1,0,0,0"
    assert N.decode_position("turn white\n" + body)[1] == 0
    assert N.decode_position("turn black\n" + body)[1] == 1
    assert N.encode_position(N.decode_board(body), 1).count("turn red") == 1


@pytest.mark.parametrize("what, text", [
    ("no turn line",        "d4:0,0,1,4,1,0,0,0"),
    ("no board",            "turn blue"),
    ("an unknown side",     "turn green\nd4:0,0,1,4,1,0,0,0"),
    ("a turn with no side", "turn\nd4:0,0,1,4,1,0,0,0"),
    ("two turn lines",      "turn blue\nturn red\nd4:0,0,1,4,1,0,0,0"),
    ("two boards",          "turn blue\nd4:0,0,1,4,1,0,0,0\ne4:1,0,1,0,0,0,0,0"),
    ("a square off the board", "turn blue\nz9:0,0,1,4,1,0,0,0"),
    ("too few fields",      "turn blue\nd4:0,0,1,4,1"),
    ("too many fields",     "turn blue\nd4:0,0,1,4,1,0,0,0,0"),
    ("a field that is not a number", "turn blue\nd4:0,0,x,4,1,0,0,0"),
    ("a negative field",    "turn blue\nd4:0,0,-1,4,1,0,0,0"),
    ("the same square twice", "turn blue\nd4:0,0,1,0,0,0,0,0|d4:0,0,0,1,0,0,0,0"),
    ("an overfull square",  "turn blue\nd4:0,0,1,5,1,0,0,0"),
    ("five pawns for a side", "turn blue\nd4:0,0,0,4,0,0,0,0|e5:0,0,0,1,0,0,0,0"),
    ("no colon",            "turn blue\nd4 0,0,1,4,1,0,0,0"),
])
def test_a_broken_position_is_refused_by_name(what, text):
    with pytest.raises(N.NotationError):
        N.decode_position(text)


def test_the_refusal_names_the_square():
    # These files get hand-edited, so "square overfull" with a dozen squares on the line is
    # not much help. Every square-level complaint carries the square.
    with pytest.raises(N.NotationError, match="d4"):
        N.decode_board("d4:0,0,1,5,1,0,0,0")


def test_a_position_may_be_a_study_rather_than_a_game():
    # Permissive on purpose: four pieces, no dragons, no black royal. validate_board's job is
    # to refuse what the engine could not hold, not what a game could not have reached.
    board = N.decode_board("d4:0,0,1,4,0,0,0,0|e5:0,0,0,0,1,0,0,0|e4:1,0,1,0,0,0,0,0")
    assert N.decode_board(N.encode_board(board)) == board


def test_prisoners_survive_the_round_trip():
    # The half of the encoding a standing-pieces-only editor would have lost.
    board = board_of(d4=(0, 0, 0, 3, 0, 0, 1, 1), e4=(1, 0, 1, 0, 0))
    text = N.encode_position(board, 0)
    back, _side = N.decode_position(text)
    assert back == board
    s = Hasher.UNPACK[back[Hasher.AlgebraToSquare("d4") - 1]]
    assert s[Hasher.CAPPAWNS] == 1 and s[Hasher.PRISFLAG]


# ---------------------------------------------------------------------------
# The editor
# ---------------------------------------------------------------------------
# Below this line everything takes the shared window and so skips headless, while the codec
# tests above go on running there. Same file, because they are the same feature.

royals_gui = pytest.importorskip("royals_gui")

# The doomed-gather study from test_win_horizon.py: blue is one royal away from six on d4,
# and red's lone spy on e4 shatters it the moment it lands. A good position to set up by
# hand precisely because no game would hand it to you.
#
# Squares come out in board order, not in the order they were clicked -- e4 before e5 -- so
# this doubles as a check that the encoder is ordering by square rather than by arrival.
STUDY = "d4:0,0,1,4,0,0,0,0|e4:1,0,1,0,0,0,0,0|e5:0,0,0,0,1,0,0,0"


def place(window, alg, side, field, prisoner=False):
    window.setupSideVar.set(side)
    window.setupPieceVar.set(field)
    window.setupPrisVar.set(1 if prisoner else 0)
    window.setupClick(Hasher.AlgebraToSquare(alg))


def buildStudy(window):
    """The STUDY position, clicked in the way a person would."""
    window.buildPosition()
    window.clearPosition()
    place(window, "d4", 0, Hasher.SPY)
    for _ in range(4):
        place(window, "d4", 0, Hasher.PAWNS)
    place(window, "e5", 0, Hasher.ROYAL)
    place(window, "e4", 1, Hasher.SPY)
    window.root.update()


def test_the_editor_opens_on_the_two_dragons(window):
    window.buildPosition()
    window.root.update()
    assert window.phase == "setup"
    assert window.setupBoard == Hasher.Entering_Board()
    assert window.errors == []


def test_clicking_builds_the_position(window):
    buildStudy(window)
    assert N.encode_board(window.setupBoard) == STUDY
    assert window.errors == []


def test_an_impossible_square_is_refused_and_said(window):
    buildStudy(window)
    before = window.setupBoard

    place(window, "d4", 0, Hasher.PAWNS)          # a fifth pawn
    window.root.update()

    assert window.setupBoard == before, "the board took a piece it cannot hold"
    assert "overfull" in window.setupHint.cget("text")
    # The point of catching Build_Space's ValueError rather than letting it fly: a person
    # clicking gets told, and Tk's callback handler never sees it.
    assert window.errors == []


def test_a_prisoner_needs_a_captor_of_the_other_side(window):
    window.buildPosition()
    window.clearPosition()

    place(window, "d4", 1, Hasher.PAWNS, prisoner=True)      # nobody there to hold him
    assert window.setupBoard == Hasher.EMPTY_BOARD
    assert "nobody" in window.setupHint.cget("text").lower()

    place(window, "d4", 0, Hasher.PAWNS)                     # a blue captor
    place(window, "d4", 0, Hasher.PAWNS, prisoner=True)      # blue cannot hold his own
    assert "does not hold the square" in window.setupHint.cget("text")

    place(window, "d4", 1, Hasher.PAWNS, prisoner=True)      # a red captive, held by blue
    window.root.update()
    s = Hasher.UNPACK[window.setupBoard[Hasher.AlgebraToSquare("d4") - 1]]
    assert s[Hasher.CAPPAWNS] == 1 and s[Hasher.PRISFLAG]
    assert window.errors == []


def test_warnings_describe_a_study_without_refusing_it(window):
    buildStudy(window)
    notes = " ".join(window.positionWarnings(window.setupBoard))
    assert "dragon" in notes
    assert window.setupWarn.cget("text"), "the panel said nothing about an odd position"
    assert window.errors == []


@pytest.mark.parametrize("side, mover", [(0, 0), (1, 1)])
def test_a_game_begins_from_the_position_with_the_chosen_side_to_move(window, side, mover):
    buildStudy(window)
    window.setupTurnVar.set(side)
    window.modeVar.set(0)                                    # two humans: no worker thread
    window.startPosition()
    window.root.update()

    assert window.phase == "play"
    assert window.fromPosition is True
    assert N.encode_board(window.board) == STUDY
    assert window.turn % 2 == mover
    # The history starts on the position, so a first move cannot be taken back into it.
    assert window.board in Engine.koTrack
    assert window.errors == []


def test_the_position_game_is_playable(window):
    buildStudy(window)
    window.setupTurnVar.set(0)
    window.modeVar.set(0)
    window.startPosition()
    window.root.update()

    moves = AI.listAllMoves(window.board, 0)
    assert moves, "white had nothing to play from a position it should have moves in"
    before = window.board
    window.commit(AI.performOneStep(window.board, 0, moves[0]), [], moves[0])
    window.root.update()

    assert window.board != before
    assert window.turn % 2 == 1, "the turn did not pass"
    assert window.errors == []


def test_a_position_game_can_be_neither_saved_nor_reviewed(window):
    buildStudy(window)
    window.setupTurnVar.set(0)
    window.modeVar.set(0)
    window.startPosition()
    window.root.update()

    assert window.saveButton.enabled is False
    assert window.reviewButton.enabled is False

    # And the methods refuse on their own account, because a greyed button is what a screen
    # does and this rule is not the screen's to keep.
    window.saveGame()
    assert "set-up position" in window.hintLabel.cget("text")
    window.startReview()
    assert window.review is None
    assert window.errors == []


def test_an_ordinary_game_still_saves_and_reviews(window):
    # The control: the gate is on position games and nothing else.
    window.buildSetup()
    window.modeVar.set(0)
    window.entryVar.set(1)
    window.startGame()
    window.root.update()

    assert window.fromPosition is False
    assert window.saveButton.enabled is True
    assert window.reviewButton.enabled is True
    assert window.errors == []


def test_a_position_survives_a_file(window, tmp_path, monkeypatch):
    buildStudy(window)
    window.setupTurnVar.set(1)
    path = tmp_path / "study.txt"

    monkeypatch.setattr(royals_gui.filedialog, "asksaveasfilename", lambda **kw: str(path))
    window.savePosition()
    assert path.exists()

    window.clearPosition()
    assert window.setupBoard == Hasher.EMPTY_BOARD

    monkeypatch.setattr(royals_gui.filedialog, "askopenfilename", lambda **kw: str(path))
    window.loadPosition()
    window.root.update()

    assert N.encode_board(window.setupBoard) == STUDY
    assert window.setupTurnVar.get() == 1
    assert window.errors == []


def test_an_unreadable_file_is_reported_rather_than_raised(window, tmp_path, monkeypatch):
    path = tmp_path / "broken.txt"
    path.write_text("turn sideways\nd4:0,0,1,4,1,0,0,0\n", encoding="utf-8")

    window.buildPosition()
    before = window.setupBoard

    seen = []
    monkeypatch.setattr(royals_gui.filedialog, "askopenfilename", lambda **kw: str(path))
    monkeypatch.setattr(royals_gui.messagebox, "showerror",
                        lambda *a, **kw: seen.append(a))
    window.loadPosition()
    window.root.update()

    assert seen, "a broken file was read without a word"
    assert window.setupBoard == before
    assert window.errors == []


def test_leaving_the_editor_drops_what_it_left_running(window):
    # The contract every screen keeps, asserted for the fourth one -- the same shape
    # test_desktop_appearance.py holds buildAppearance to.
    buildStudy(window)
    generation = window.gameGen

    window.buildSetup()
    window.root.update()
    assert window.gameGen > generation
    assert window.review is None
    assert window.viewReady is False

    for key in ("<Home>", "<End>", "<Left>", "<Right>"):
        window.root.event_generate(key, when="now")
        window.root.update()
    assert window.errors == []


def test_the_editor_and_the_menu_do_not_share_stale_controls(window):
    """Going menu -> editor -> menu -> editor must leave no destroyed widget listening.

    StoneChoice traces the variable it is handed for the life of the process and never
    untraces it, so a variable that outlives its widgets fires callbacks into destroyed
    labels the next time anything sets it. buildSetup rebuilds its variables for exactly
    this reason and the editor has to as well -- the symptom is a handful of TclErrors from
    a screen that is no longer on the window, which lands in `window.errors` and nowhere a
    person would look.
    """
    window.buildPosition()
    window.buildSetup()
    window.buildPosition()
    window.root.update()

    for value in (0, 1, 2):
        window.modeVar.set(value)
    window.sideVar.set(1)
    window.depthVar.set(3)
    window.root.update()

    assert window.errors == []
