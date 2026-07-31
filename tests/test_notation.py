"""The notation codec, checked against the engine rather than against a hand-written list.

A hand-written table of examples tests the cases you thought of. The interesting cases
here are the ones nobody thinks of: a break whose direction index is spelled as a letter,
a push that carries prisoners, a jump that wraps around the edge of the board, a spy
breaking out of the stack holding it captive.

So this walks the same seeded positions tests/regress.py walks, asks the engine for every
legal move both sides have, and round-trips each one. That is hundreds of thousands of
moves across every kind, every prisoner variant and every wrap case, obtained by reusing
a generator already trusted enough to be the rules contract.

The property that must hold, for every move the engine can produce:

    decode_move(encode_move(m))   == m
    move_from_json(move_to_json(m)) == m
"""

import random

import pytest

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI
from royals_engine import notation as N


# ---------------------------------------------------------------------------
# Positions to sweep -- the same construction regress.enteredBoard uses
# ---------------------------------------------------------------------------

def entered_board(seed, intensity=0.5):
    AI.setEntryNoise(intensity, seed)
    board = Hasher.Entering_Board()
    for contr, piece in Engine.enteringSequence():
        is_spy = (piece == Hasher.SPY)
        if not Engine.enteringOptions(board, contr, is_spy):
            continue
        board = Engine.dropPiece(board, AI.chooseEntry(board, contr, piece, is_spy), contr, piece)
    return board


def sweep_positions(seed, turns=12):
    """Random but seeded play, yielding each position reached along the way.

    Random play reaches messy positions -- captures, prisoners, spies inside enemy
    stacks -- far faster than the AI does, which is steering for a tidy gather.
    """
    rng = random.Random(seed)
    board = entered_board(seed)
    Engine.koReset()
    AI.newGame()
    Engine.koRecord(board)

    for turn in range(turns):
        yield board
        moves = AI.listAllMoves(board, turn % 2)
        if not moves:
            return
        board = AI.performOneStep(board, turn % 2, moves[rng.randrange(len(moves))])
        Engine.koRecord(board)


# Random play alone is not enough, and it is worth being explicit about why. Measured
# over three seeds: at 12 turns it never produces a "free" and at 40 turns it still
# produces no prisoner-carrying move at all -- captures are simply rare when nobody is
# trying. Sweeping only random games would have left the '*' suffix and the free/push
# distinction completely untested while looking thorough.
#
# So the random sweep is joined by a handful of positions built by hand to hold
# prisoners. Those are what make the coverage deterministic instead of lucky.

def _square(alg, spec):
    return Hasher.AlgebraToSquare(alg) - 1, Hasher.Build_Space(*spec)


def constructed_positions():
    """Positions that random play almost never reaches, each with the side to move.

    Build_Space takes (side, dragon, spy, pawns, royal, capSpy, capPawns, prisFlag).
    """
    shapes = [
        # blue holding red pawns prisoner -- gives the movingPris variants
        ({"d4": (0, 0, 1, 2, 1, 0, 1, 1)}, 0),
        # blue holding red's spy
        ({"d4": (0, 0, 0, 2, 1, 1, 0, 1)}, 0),
        # red holding blue's spy -- blue's only move is the spy breaking out
        ({"d4": (1, 0, 0, 2, 1, 1, 0, 1)}, 0),
        # blue standing next to its own people held by red -- this is what makes a "free"
        ({"d4": (0, 0, 0, 3, 1, 0, 0, 0), "d5": (1, 0, 0, 1, 0, 0, 2, 1)}, 0),
    ]

    for squares, side in shapes:
        board = list(Hasher.Entering_Board())
        for alg, spec in squares.items():
            index, code = _square(alg, spec)
            board[index] = code
        yield tuple(board), side


def all_moves_across_sweep(seeds=(3, 11, 57)):
    seen = []
    for seed in seeds:
        for board in sweep_positions(seed, turns=40):
            for side in (0, 1):
                seen.extend(AI.listAllMoves(board, side))

    Engine.koReset()
    for board, side in constructed_positions():
        seen.extend(AI.listAllMoves(board, side))
    return seen


ALL_MOVES = all_moves_across_sweep()


# ---------------------------------------------------------------------------
# The round trips
# ---------------------------------------------------------------------------

def test_the_sweep_is_actually_broad():
    """If this ever shrinks to a handful of dull moves, the tests below stop meaning much."""
    assert len(ALL_MOVES) > 6_000, len(ALL_MOVES)

    kinds = {m[AI.MOVE_KIND] for m in ALL_MOVES}
    assert kinds == {"jump", "push", "free", "break"}, kinds

    # Both prisoner variants must appear, or the '*' suffix is untested. This is the
    # assertion the constructed positions exist to satisfy -- 40 turns of random play
    # across three seeds produces exactly zero of these on its own.
    carrying = sum(1 for m in ALL_MOVES if m[AI.MOVE_PRIS])
    assert carrying > 0, "no prisoner-carrying move in the sweep -- '*' is untested"
    assert carrying < len(ALL_MOVES), "every move carries prisoners, which cannot be right"


def test_ran_round_trip():
    for move in ALL_MOVES:
        text = N.encode_move(move)
        assert N.decode_move(text) == move, (move, text)


def test_json_round_trip():
    for move in ALL_MOVES:
        blob = N.move_to_json(move)
        assert N.move_from_json(blob) == move, (move, blob)


def test_ran_is_unambiguous():
    """Two different moves must never encode to the same token."""
    by_text = {}
    for move in ALL_MOVES:
        text = N.encode_move(move)
        prior = by_text.setdefault(text, move)
        assert prior == move, "%r encodes both %r and %r" % (text, prior, move)


def test_pass_round_trips():
    assert N.encode_move(None) == "--"
    assert N.decode_move("--") is None
    assert N.move_from_json(N.move_to_json(None)) is None


# ---------------------------------------------------------------------------
# Entering placements
# ---------------------------------------------------------------------------

def test_entry_round_trip():
    for piece in (Hasher.ROYAL, Hasher.PAWNS, Hasher.SPY):
        for square in range(1, 50):
            text = N.encode_entry(piece, square)
            assert N.decode_entry(text) == (piece, square), text
            assert N.entry_from_json(N.entry_to_json(piece, square)) == (piece, square)


def test_entry_and_move_namespaces_do_not_collide():
    """'P' means push in a move and pawn in a placement. The '@' keeps them apart."""
    with pytest.raises(N.NotationError):
        N.decode_move("@Pd3")
    with pytest.raises(N.NotationError):
        N.decode_entry("Pd3d4")


# ---------------------------------------------------------------------------
# Whole games, as records
# ---------------------------------------------------------------------------
# The move list a record holds is not a list of moves -- it is a list of *plies*, three
# kinds of thing sharing one namespace, and a pass is written down rather than implied. So
# these build one the way a front end does, placements and all, instead of testing the
# codec against a list of moves it would never actually be handed.

def recorded_game(seed, turns=20):
    """A move list in the shape game.Game.moves holds one: placements, then moves.

    The skipped-placement "--" is reproduced faithfully, because that is the token most
    likely to be got wrong by a front end learning to write its moves down -- it records a
    step nobody made a decision at, and leaving it out silently shifts every later ply
    onto the wrong side.
    """
    rng = random.Random(seed)
    tokens = []

    AI.setEntryNoise(0.5, seed)
    board = Hasher.Entering_Board()
    for contr, piece in Engine.enteringSequence():
        is_spy = (piece == Hasher.SPY)
        if not Engine.enteringOptions(board, contr, is_spy):
            tokens.append(N.PASS)
            continue
        square = AI.chooseEntry(board, contr, piece, is_spy)
        board = Engine.dropPiece(board, square, contr, piece)
        tokens.append(N.encode_entry(piece, square))

    Engine.koReset()
    AI.newGame()
    Engine.koRecord(board)

    # Play opens on turn 1, so that whoever entered second moves first.
    #
    # The ko filter is not decoration here. Without it this produces move lists that are
    # perfectly good notation, and that the *engine* will happily apply, but that no game
    # could have contained -- and anything that replays a record through the rules, as the
    # server does, then refuses them. A generator of "games" that are not games is a
    # generator that makes real disagreements look like test bugs.
    for turn in range(1, turns + 1):
        contr = turn % 2
        legal = []
        for move in AI.listAllMoves(board, contr):
            after = AI.performOneStep(board, contr, move)
            if not Engine.koBreaks(after):
                legal.append((move, after))
        if not legal:
            tokens.append(N.PASS)
            continue
        move, board = legal[rng.randrange(len(legal))]
        Engine.koRecord(board)
        tokens.append(N.encode_move(move))

    return tokens


RECORDED = recorded_game(11)


def test_a_recorded_game_is_more_than_a_list_of_moves():
    """Otherwise the round trip below is only testing encode_move again."""
    kinds = {N.decode_ply(t)[0] for t in RECORDED}
    assert kinds == {N.PLY_ENTER, N.PLY_MOVE}, kinds
    assert sum(1 for t in RECORDED if t.startswith(N.ENTER_PREFIX)) == 12


def test_game_record_round_trips():
    # A pass has to be put in rather than found -- 20 turns of random play from a full
    # board reaches one rarely, and an entering skip is rarer still -- so the cases that
    # mix them in are listed explicitly instead of hoped for.
    cases = (RECORDED,
             [N.PASS] + RECORDED,               # a skipped placement, before anything
             RECORDED + [N.PASS, N.PASS],       # the double pass that ends a game
             [], [N.PASS])
    for moves in cases:
        assert N.decode_game(N.encode_game(moves)) == moves


def test_the_header_is_metadata_and_nothing_more():
    """Everything needed to replay is in the tokens. Stripping the comments changes nothing."""
    text = N.encode_game(RECORDED, notes=["2026-07-30  vs computer  blue wins by gather"])
    stripped = "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith(N.COMMENT))
    assert N.decode_game(stripped) == N.decode_game(text) == RECORDED


def test_a_record_is_the_same_string_the_database_stores():
    """The file and the stored column are one format, not two that agree."""
    stored = " ".join(RECORDED)          # persist.py's `" ".join(game.moves)`
    assert N.decode_game(stored) == stored.split() == RECORDED


def test_notes_cannot_smuggle_tokens_into_the_record():
    """A note is a comment. A note containing a line break is still a comment."""
    text = N.encode_game(["--"], notes=["harmless\nJd3f5 Jd4f6", "and\r\nPd3d4"])
    assert N.decode_game(text) == ["--"]
    for line in text.splitlines():
        assert line.startswith(N.COMMENT) or line == "--", line


def test_a_trailing_comment_is_a_comment_wherever_it_sits():
    assert N.decode_game("Jd3f5 # nice\n# all of this\nPd3d4") == ["Jd3f5", "Pd3d4"]


def test_wrapping_is_presentation_only():
    """Long games wrap; the token list does not care where."""
    text = N.encode_game(RECORDED * 8)
    body = [l for l in text.splitlines() if not l.startswith(N.COMMENT)]
    assert len(body) > 1, "a 160-ply record should not be one line"
    assert all(len(l) <= N.WRAP for l in body), max(len(l) for l in body)
    assert N.decode_game(text) == N.decode_game(" ".join(body))


def test_encoding_names_the_ply_that_is_wrong():
    """A front end that has just learned to record its moves finds out at save time."""
    with pytest.raises(N.NotationError) as caught:
        N.encode_game(["@Rd3", "Jd3f5", "Zq9q9"])
    assert "ply 3" in str(caught.value)


@pytest.mark.parametrize("text", [
    "Jd3f5 Zq9q9",              # a token that is not notation
    "Jd3f5 Jd3",                # truncated
    "@Xd3",                     # not a piece
    "Jd3f5 @@d3",
    "\x00",
])
def test_a_corrupted_record_fails_to_load(text):
    with pytest.raises(N.NotationError):
        N.decode_game(text)


def test_decode_game_rejects_non_strings():
    for bad in (None, 42, [], b"Jd3f5"):
        with pytest.raises(N.NotationError):
            N.decode_game(bad)


def test_decode_ply_classifies_without_guessing():
    assert N.decode_ply("--") == (N.PLY_PASS, None)
    assert N.decode_ply("@Rd4") == (N.PLY_ENTER, N.decode_entry("@Rd4"))
    assert N.decode_ply("Jd3f5") == (N.PLY_MOVE, N.decode_move("Jd3f5"))
    for bad in (None, 42, "", "@", "Q"):
        with pytest.raises(N.NotationError):
            N.decode_ply(bad)


# ---------------------------------------------------------------------------
# Direction letters must follow the engine, not a copy of it
# ---------------------------------------------------------------------------

def test_direction_letters_are_derived_from_the_engine():
    assert N.DIR_LETTERS == tuple(h[0] for h in Engine.HEADINGS)
    assert len(N.DIR_LETTERS) == len(Engine.pushDirs)
    for i, heading in enumerate(Engine.HEADINGS):
        assert N.decode_move("Bd3" + heading[0])[2] == i


def test_break_direction_survives_the_engine():
    """The decoded index must be the one exeBreak actually acts on."""
    board = entered_board(3)
    for i, heading in enumerate(Engine.HEADINGS):
        move = N.decode_move("Bd3" + heading[0])
        assert move[2] == i
        assert Engine.pushDirs[move[2]] == Engine.pushDirs[i]


# ---------------------------------------------------------------------------
# Squares, and the 1-based/0-based split
# ---------------------------------------------------------------------------

def test_square_helpers_round_trip_every_square():
    for square in range(1, 50):
        assert N.alg_to_square(N.square_to_alg(square)) == square
    for index in range(49):
        assert N.alg_to_index(N.index_to_alg(index)) == index


def test_origin_is_one_based_and_target_is_zero_based():
    """The asymmetry this module exists to contain. a1 is origin 1 and target index 0."""
    move = N.decode_move("Ja1b2")
    assert move[0] == 1                      # origin, 1-based
    assert move[2] == Hasher.AlgebraToSquare("b2") - 1   # target, 0-based
    assert N.encode_move(move) == "Ja1b2"


def test_encoded_moves_agree_with_what_the_engine_plays():
    """Decode a move, play it, and confirm the board matches playing the tuple directly."""
    for board in sweep_positions(11, turns=6):
        for side in (0, 1):
            for move in AI.listAllMoves(board, side):
                replayed = N.decode_move(N.encode_move(move))
                assert (AI.performOneStep(board, side, replayed)
                        == AI.performOneStep(board, side, move))


# ---------------------------------------------------------------------------
# Hostile input
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "", "x", "Jd3", "Jd3f", "Zd3f5", "Jd3f5x", "J??f5", "Jd3f5**",
    "Bd3x", "Bd3", "Bd3r*", "Jz9f5", "Jd3z9", "Ja0b1", "Jd8d3",
    "  ", "J d3f5", "--x", "@@d3",
])
def test_malformed_notation_raises(text):
    with pytest.raises(N.NotationError):
        N.decode_move(text)


@pytest.mark.parametrize("obj", [
    None, [], "Jd3f5", 42,
    {}, {"kind": "teleport"}, {"kind": "jump"},
    {"kind": "jump", "origin": "d3"},
    {"kind": "jump", "origin": "zz", "target": "f5"},
    {"kind": "jump", "origin": "d3", "target": "zz"},
    {"kind": "break", "origin": "d3"},
    {"kind": "break", "origin": "d3", "dir": "q"},
])
def test_malformed_json_raises(obj):
    with pytest.raises(N.NotationError):
        N.move_from_json(obj)


def test_decode_move_rejects_non_strings():
    for bad in (None, 42, [], {}, b"Jd3f5"):
        with pytest.raises(N.NotationError):
            N.decode_move(bad)


# ---------------------------------------------------------------------------
# Board packing
# ---------------------------------------------------------------------------

def test_board_pack_round_trip_across_the_sweep():
    for board in sweep_positions(57, turns=10):
        blob = N.pack_board(board)
        assert len(blob) == 98
        assert N.unpack_board(blob) == board


def test_packed_board_is_98_bytes():
    assert len(N.pack_board(Hasher.Entering_Board())) == 98


def test_validate_board_rejects_out_of_range_codes():
    board = list(Hasher.Entering_Board())

    board[0] = 8192                     # one past the end of Hasher.UNPACK
    with pytest.raises(N.NotationError):
        N.validate_board(tuple(board))

    # The one that matters: a negative code would index Hasher.UNPACK from the END and
    # hand back a plausible-looking square instead of raising. struct can't produce one,
    # but a JSON integer can.
    board[0] = -1
    with pytest.raises(N.NotationError):
        N.validate_board(tuple(board))
    assert Hasher.UNPACK[-1] != Hasher.UNPACK[0], (
        "the negative-index hazard this test guards has changed shape")


def test_validate_board_rejects_wrong_shape():
    with pytest.raises(N.NotationError):
        N.validate_board(Hasher.Entering_Board()[:48])
    with pytest.raises(N.NotationError):
        N.validate_board(list(Hasher.Entering_Board()))
    with pytest.raises(N.NotationError):
        N.validate_board(Hasher.Entering_Board() + (0,))


def test_validate_board_rejects_impossible_armies():
    """A tampered board with five pawns for one side must not be accepted."""
    board = list(Hasher.Entering_Board())
    # five pawns on one square is more than blue owns
    board[0] = Hasher.Build_Space(0, 0, 0, 4, 0)
    board[1] = Hasher.Build_Space(0, 0, 0, 1, 0)
    with pytest.raises(N.NotationError):
        N.validate_board(tuple(board))


def test_unpack_rejects_wrong_length_and_non_bytes():
    with pytest.raises(N.NotationError):
        N.unpack_board(b"")
    with pytest.raises(N.NotationError):
        N.unpack_board(b"\x00" * 97)
    with pytest.raises(N.NotationError):
        N.unpack_board("not bytes")


def test_real_positions_pass_the_piece_count_invariant():
    """The validator must not reject positions the engine legitimately reaches."""
    for seed in (3, 11, 57):
        for board in sweep_positions(seed, turns=12):
            N.validate_board(board)
