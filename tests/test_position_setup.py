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

The other thing worth saying: a game begun from a position saves and reviews like any
other, and it did not always. A RAN record derives every ply's side from its index on the
assumption of exactly twelve entering plies (`record.turn_of_ply`), so a game that started
mid-board once had no place in the format -- saving one would have written a file that
replayed as a different game. The `turn` and `board` lines `notation.encode_game` writes are
what carry the two facts the move list cannot, and the tests below are what say the round
trip really closes.
"""

import pathlib
import random
import sys

import pytest

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher
from royals_engine import notation as N
from royals_engine import record as R

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
# Squares come out in board order, not in the order they were marked -- e4 before e5 -- so
# this doubles as a check that the encoder is ordering by square rather than by arrival.
STUDY = "d4:0,0,1,4,0,0,0,0|e4:1,0,1,0,0,0,0,0|e5:0,0,0,0,1,0,0,0"

# The panel's controls, by the name a test wants to call them. The window keys them by field
# index, which is right for code that reads a square back out and unreadable in a test.
FIELD = {"royal": Hasher.ROYAL, "spy": Hasher.SPY, "pawns": Hasher.PAWNS,
         "dragon": Hasher.DRAGON, "capSpy": Hasher.CAPSPY, "capPawns": Hasher.CAPPAWNS}


def setSquare(window, alg, side=None, **counts):
    """Choose a square and mark what is on it, the way a person works the panel.

    The click is the selection and nothing else; every control writes through applySquare,
    which is what the widgets' `command` is bound to.
    """
    window.setupClick(Hasher.AlgebraToSquare(alg))
    if side is not None:
        window.sqSideVar.set(side)
    for name, count in counts.items():
        window.sqVars[FIELD[name]].set(count)
    window.applySquare()


def buildStudy(window):
    """The STUDY position, marked out the way a person would."""
    window.buildPosition()
    window.clearPosition()
    setSquare(window, "d4", side=0, spy=1, pawns=4)
    setSquare(window, "e5", side=0, royal=1)
    setSquare(window, "e4", side=1, spy=1)
    window.root.update()


def playFrom(window, side, mode=0):
    """Editor -> menu -> game, which is the only way into a position game.

    mode 0 by default: two humans, so nothing runs on a worker thread and the test does not
    have to wait for a search it does not care about.
    """
    window.setupTurnVar.set(side)
    window.usePosition()                    # this rebuilds the menu, so modeVar comes after
    window.modeVar.set(mode)
    window.startGame()
    window.root.update()


####### The panel #######

def test_the_editor_opens_on_the_two_dragons(window):
    window.buildPosition()
    window.root.update()
    assert window.phase == "setup"
    assert window.setupBoard == Hasher.Entering_Board()
    assert window.selected is None
    assert window.errors == []


def test_a_square_shows_what_is_already_on_it(window):
    """The whole point of a panel over a palette: a square can be read as well as written."""
    buildStudy(window)
    window.setupClick(Hasher.AlgebraToSquare("d4"))
    window.root.update()

    assert window.selected == Hasher.AlgebraToSquare("d4")
    assert window.sqSideVar.get() == 0
    assert window.sqVars[Hasher.PAWNS].get() == 4
    assert window.sqVars[Hasher.SPY].get() == 1
    assert window.sqVars[Hasher.ROYAL].get() == 0
    assert "D4" in window.squareLabel.cget("text")
    assert window.errors == []


def test_marking_the_squares_builds_the_position(window):
    buildStudy(window)
    assert N.encode_board(window.setupBoard) == STUDY
    assert window.errors == []


def test_a_piece_can_be_taken_off_again(window):
    """A palette could only ever add: taking one pawn off a stack of four meant erasing the
    square and putting three back."""
    buildStudy(window)
    setSquare(window, "d4", pawns=2)
    window.root.update()

    s = Hasher.UNPACK[window.setupBoard[Hasher.AlgebraToSquare("d4") - 1]]
    assert s[Hasher.PAWNS] == 2
    assert s[Hasher.SPY] == 1, "the spy went with the two pawns"
    assert window.errors == []


def test_an_empty_square_keeps_the_colour_being_laid_out(window):
    """An empty square has no side, and adopting its 0 would flip the holder back to white
    every time somebody laying out black's pieces clicked the next empty square."""
    window.buildPosition()
    window.clearPosition()
    setSquare(window, "d4", side=1, pawns=1)
    window.setupClick(Hasher.AlgebraToSquare("f1"))
    assert window.sqSideVar.get() == 1

    window.setupClick(Hasher.AlgebraToSquare("d4"))
    assert window.sqSideVar.get() == 1, "a square that is held should say who by"
    assert window.errors == []


####### What the board has no room for #######

def test_the_panel_greys_what_the_board_has_no_room_for(window):
    buildStudy(window)
    window.setupClick(Hasher.AlgebraToSquare("f1"))     # an empty square
    window.sqSideVar.set(0)
    window.holderChanged()
    window.root.update()

    pawns = window.sqButtons[Hasher.PAWNS]
    assert pawns[0].enabled, "0 is always on -- it is what taking pawns off is"
    assert not any(b.enabled for b in pawns[1:]), \
        "white has all four pawns on d4 and was offered a fifth"
    assert not window.sqButtons[Hasher.ROYAL][0].enabled
    assert not window.sqButtons[Hasher.SPY][0].enabled
    assert window.sqButtons[Hasher.DRAGON][0].enabled, "the study has no dragons"

    # The same square for black, which has spent one spy and nothing else.
    window.sqSideVar.set(1)
    window.holderChanged()
    assert all(b.enabled for b in window.sqButtons[Hasher.PAWNS])
    assert window.sqButtons[Hasher.ROYAL][0].enabled
    assert not window.sqButtons[Hasher.SPY][0].enabled
    assert window.errors == []


def test_what_the_square_already_holds_does_not_count_against_it(window):
    """The count the greying works from leaves the selected square out, or the button that is
    switched on would be the one greyed."""
    buildStudy(window)
    window.setupClick(Hasher.AlgebraToSquare("d4"))     # white's four pawns are here
    window.root.update()

    assert all(b.enabled for b in window.sqButtons[Hasher.PAWNS]), \
        "the square's own pawns were counted against it"
    assert window.sqButtons[Hasher.SPY][0].enabled, "and so was its own spy"
    assert window.errors == []


def test_the_board_is_still_the_backstop_behind_the_greying(window):
    """validate_board has the last word, because the greying is computed from a board a LOAD
    or a CLEAR may have moved under it."""
    buildStudy(window)
    before = window.setupBoard

    window.setupClick(Hasher.AlgebraToSquare("f1"))
    window.sqSideVar.set(0)
    window.sqVars[Hasher.PAWNS].set(4)                  # past the greyed button
    window.applySquare()
    window.root.update()

    assert window.setupBoard == before, "the board took four pawns white does not have"
    assert "too many" in window.setupHint.cget("text")
    assert window.sqVars[Hasher.PAWNS].get() == 0, \
        "the panel went on showing a square that is not there"
    assert window.errors == []


def test_a_dragon_stands_alone(window):
    window.buildPosition()
    window.clearPosition()
    setSquare(window, "d4", side=0, pawns=3, royal=1)
    setSquare(window, "d4", dragon=1)
    window.root.update()

    s = Hasher.UNPACK[window.setupBoard[Hasher.AlgebraToSquare("d4") - 1]]
    assert s[Hasher.DRAGON] == 1
    assert (s[Hasher.PAWNS], s[Hasher.ROYAL]) == (0, 0), "a dragon shared its square"
    # Build_Space drops them without saying so, so the test that matters is that the panel
    # ends up showing what the square ended up holding.
    assert window.sqVars[Hasher.PAWNS].get() == 0
    assert not window.sqButtons[Hasher.PAWNS][1].enabled
    assert window.errors == []


def test_a_prisoner_needs_a_captor(window):
    window.buildPosition()
    window.clearPosition()

    window.setupClick(Hasher.AlgebraToSquare("d4"))     # empty: nobody to hold anybody
    assert not any(b.enabled for b in window.sqButtons[Hasher.CAPPAWNS])
    assert not window.sqButtons[Hasher.CAPSPY][0].enabled

    setSquare(window, "d4", side=0, pawns=1)            # a white captor
    assert all(b.enabled for b in window.sqButtons[Hasher.CAPPAWNS])

    setSquare(window, "d4", capPawns=1)
    window.root.update()

    s = Hasher.UNPACK[window.setupBoard[Hasher.AlgebraToSquare("d4") - 1]]
    assert s[Hasher.CAPPAWNS] == 1 and s[Hasher.PRISFLAG]
    assert window.errors == []


####### Clearing a piece type #######

def test_clearing_a_piece_type_takes_the_prisoners_too(window):
    """A prisoner is still that side's piece -- it is what PIECE_LIMITS counts and what the
    greying counts -- so one button means white has no pawns left anywhere."""
    window.buildPosition()
    window.clearPosition()
    setSquare(window, "d4", side=0, pawns=2)                    # two of white's, standing
    setSquare(window, "e4", side=1, spy=1, capPawns=1)          # a third, held by black
    window.root.update()
    assert window.pieceCounts(window.setupBoard)[0]["pawns"] == 3

    window.sqSideVar.set(0)
    window.clearField(Hasher.PAWNS, Hasher.CAPPAWNS, False)     # what the CLEAR button calls
    window.root.update()

    assert window.pieceCounts(window.setupBoard)[0]["pawns"] == 0, "white kept pawns somewhere"
    e4 = Hasher.UNPACK[window.setupBoard[Hasher.AlgebraToSquare("e4") - 1]]
    assert e4[Hasher.CAPPAWNS] == 0 and not e4[Hasher.PRISFLAG]
    assert e4[Hasher.SPY] == 1, "black's spy went with white's pawns"
    assert window.errors == []


def test_clearing_one_side_leaves_the_other(window):
    window.buildPosition()                              # opens on the two dragons
    window.sqSideVar.set(1)
    window.clearField(Hasher.DRAGON, None, False)
    window.root.update()

    counts = window.pieceCounts(window.setupBoard)
    assert counts[1]["dragon"] == 0
    assert counts[0]["dragon"] == 1, "white's dragon went with black's"
    assert window.errors == []


def test_emptying_one_square_leaves_the_rest(window):
    buildStudy(window)
    window.setupClick(Hasher.AlgebraToSquare("d4"))
    window.emptySquare()
    window.root.update()

    assert window.setupBoard[Hasher.AlgebraToSquare("d4") - 1] == 0
    assert window.pieceCounts(window.setupBoard)[0]["royal"] == 1, "e5 went with d4"
    assert window.errors == []


def test_warnings_describe_a_study_without_refusing_it(window):
    buildStudy(window)
    notes = " ".join(window.positionWarnings(window.setupBoard))
    assert "dragon" in notes
    assert window.setupWarn.cget("text"), "the panel said nothing about an odd position"
    assert window.errors == []


####### Back to the menu, and into a game #######

def test_a_position_goes_back_to_the_menu_to_be_played(window):
    buildStudy(window)
    window.setupTurnVar.set(1)
    window.usePosition()
    window.root.update()

    assert N.encode_board(window.pendingBoard) == STUDY
    assert window.pendingTurn == 1
    assert "black to move" in window.positionNote.cget("text")

    # A board that is already laid out has nothing to enter.
    packed = window.leftColumn.pack_slaves()
    assert window.positionBox in packed
    assert window.entryBox not in packed
    assert window.errors == []


def test_the_editor_opens_on_the_position_the_menu_is_holding(window):
    """EDIT comes back to the position rather than to a fresh board."""
    buildStudy(window)
    window.usePosition()
    window.buildPosition()
    window.root.update()

    assert N.encode_board(window.setupBoard) == STUDY
    assert window.errors == []


@pytest.mark.parametrize("side, mover", [(0, 0), (1, 1)])
def test_a_game_begins_from_the_position_with_the_chosen_side_to_move(window, side, mover):
    buildStudy(window)
    playFrom(window, side)

    assert window.phase == "play"
    assert window.fromPosition is True
    assert N.encode_board(window.board) == STUDY
    assert window.turn % 2 == mover
    # The history starts on the position, so a first move cannot be taken back into it.
    assert window.board in Engine.koTrack
    assert window.errors == []


def test_the_position_game_is_playable(window):
    buildStudy(window)
    playFrom(window, 0)

    moves = AI.listAllMoves(window.board, 0)
    assert moves, "white had nothing to play from a position it should have moves in"
    before = window.board
    window.commit(AI.performOneStep(window.board, 0, moves[0]), [], moves[0])
    window.root.update()

    assert window.board != before
    assert window.turn % 2 == 1, "the turn did not pass"
    assert window.errors == []


def test_a_position_game_saves_and_reviews_like_any_other(window):
    """It could do neither until the record learned to carry the board it began from.

    The refusal was real while it stood: a record derives every ply's side from its index
    and from a twelve-ply opening, so a game that began mid-board replayed as somebody
    else's. `turn` and `board` lines are what fixed it -- see notation.encode_game.
    """
    buildStudy(window)
    playFrom(window, 0)

    assert window.saveButton.enabled is True
    assert window.reviewButton.enabled is True

    moves = AI.listAllMoves(window.board, 0)
    window.commit(AI.performOneStep(window.board, 0, moves[0]), [], moves[0])
    window.root.update()

    window.startReview()
    assert window.review is not None
    # The review starts on the position the game began from, not on an entering board.
    assert N.encode_board(window.review["spots"][0].board) == STUDY
    assert window.review["spots"][0].phase == R.PHASE_PLAYING
    window.exitReview()
    assert window.errors == []


def test_a_saved_position_game_carries_the_board_it_began_from(window, tmp_path, monkeypatch):
    buildStudy(window)
    playFrom(window, 1)                                  # black to move

    moves = AI.listAllMoves(window.board, 1)
    window.commit(AI.performOneStep(window.board, 1, moves[0]), [], moves[0])
    window.root.update()

    path = tmp_path / "game.txt"
    monkeypatch.setattr(royals_gui.filedialog, "asksaveasfilename", lambda **kw: str(path))
    window.saveGame()

    text = path.read_text(encoding="utf-8")
    assert "turn red" in text and "board " in text

    walked, board, turn, _rules = N.decode_record(text)
    assert walked == window.record and turn == 1
    assert N.encode_board(board) == STUDY

    # And it replays to the board the window is actually showing.
    spots = R.positions(walked, board, turn)
    assert spots[-1].board == window.board
    assert window.errors == []


def test_opening_a_position_game_keeps_it_one(window, tmp_path, monkeypatch):
    """Saving it again has to write the same two lines, or the second file is a different
    game from the first."""
    buildStudy(window)
    playFrom(window, 1)
    moves = AI.listAllMoves(window.board, 1)
    window.commit(AI.performOneStep(window.board, 1, moves[0]), [], moves[0])
    window.root.update()

    first = tmp_path / "one.txt"
    monkeypatch.setattr(royals_gui.filedialog, "asksaveasfilename", lambda **kw: str(first))
    window.saveGame()

    monkeypatch.setattr(royals_gui.filedialog, "askopenfilename", lambda **kw: str(first))
    window.openGame()
    window.root.update()
    assert window.fromPosition is True
    assert window.startTurn == 1
    assert N.encode_board(window.startBoard) == STUDY

    window.exitReview()
    assert window.errors == []


def test_a_position_game_can_ask_the_engine(window):
    """The assistant was dead in every position game, because the editor started its own and
    never set `assist`. Going through startGame is what fixes it, and this is the assertion
    that says so."""
    buildStudy(window)
    window.setupTurnVar.set(0)
    window.usePosition()
    window.modeVar.set(0)
    window.assistVar.set(1)
    window.startGame()
    window.root.update()

    assert window.assist is True
    assert window.errors == []


def test_the_position_is_kept_until_it_is_discarded(window):
    """It outlives a game on purpose: trying one study at two depths is the point."""
    buildStudy(window)
    playFrom(window, 0)
    assert window.fromPosition is True

    window.buildSetup()                     # NEW GAME
    window.modeVar.set(0)
    window.startGame()
    window.root.update()
    assert window.fromPosition is True
    assert N.encode_board(window.board) == STUDY, "the second game was not the same position"

    window.buildSetup()
    window.discardPosition()
    assert window.pendingBoard is None
    assert window.entryBox in window.leftColumn.pack_slaves(), "ENTERING never came back"

    window.modeVar.set(0)
    window.entryVar.set(1)
    window.startGame()
    window.root.update()
    assert window.fromPosition is False
    assert N.encode_board(window.board) != STUDY
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


####### Files, and leaving #######

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
    window.sqSideVar.set(1)
    window.setupTurnVar.set(1)
    window.root.update()

    assert window.errors == []
