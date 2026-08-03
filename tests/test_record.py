"""The record walker, held against the walk the server does.

`royals_engine.record` and `royals_web.game.positions` both turn a move list into the
boards it passed through, and they are deliberately different: the server's re-derives
legality as it goes, because it serves records uploaded by strangers, while this one only
applies plies, because reviewing a game needs none of the rules.

Two walks of one list is two chances to disagree about what a "--" means, or about which
side ply 12 belongs to -- and a disagreement there does not fail, it quietly shows a
different game. So the contract is that they agree ply for ply, which is what most of this
file checks. It is the same reason the two engines answer to golden_moves.txt.
"""

import pytest

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI
from royals_engine import notation as N
from royals_engine import record as R

from test_notation import recorded_game, RECORDED


# ---------------------------------------------------------------------------
# The walk itself
# ---------------------------------------------------------------------------

def test_a_record_yields_one_position_per_ply_and_one_before_them():
    spots = R.positions(RECORDED)
    assert len(spots) == len(RECORDED) + 1
    assert spots[0].board == Hasher.Entering_Board()
    assert spots[0].token is None
    assert [s.ply for s in spots] == list(range(len(RECORDED) + 1))
    assert [s.token for s in spots[1:]] == RECORDED


def test_the_entering_phase_fills_the_board():
    spots = R.positions(RECORDED)
    # Twelve placements, and something has arrived by the end of them.
    assert spots[len(R.ENTER_STEPS)].board != Hasher.Entering_Board()
    for spot in spots[1:len(R.ENTER_STEPS) + 1]:
        assert spot.token.startswith(N.ENTER_PREFIX) or spot.token == N.PASS


def test_the_side_of_a_ply_is_derived_and_not_guessed():
    """Entering comes from the sequence; play is the turn counter, opening at red."""
    for index, (contr, _piece) in enumerate(Engine.enteringSequence()):
        assert R.side_of_ply(index) == contr
    first_play = len(R.ENTER_STEPS)
    assert R.side_of_ply(first_play) == 1, "whoever entered second opens"
    assert R.side_of_ply(first_play + 1) == 0
    assert R.side_of_ply(first_play + 2) == 1


def test_highlights_and_flights_describe_the_move_that_was_played():
    spots = R.positions(RECORDED)
    for before, spot in zip(spots, spots[1:]):
        if spot.token in (N.PASS,) or spot.token.startswith(N.ENTER_PREFIX):
            assert spot.flights == ()
            continue
        move = N.decode_move(spot.token)
        assert spot.squares[0] == move[AI.MOVE_ORIGIN]
        if move[AI.MOVE_KIND] == "break":
            assert len(spot.squares) == 1
        else:
            assert spot.squares[1] == move[AI.MOVE_TARGET] + 1
        # moveFlights is measured against the board the move was made on.
        assert spot.flights == Engine.moveFlights(before.board, move, spot.side)


def test_a_prefix_of_a_record_is_a_record():
    """The property the whole feature rests on. Stepping *is* replaying a prefix."""
    full = R.positions(RECORDED)
    for cut in (0, 1, 5, 12, 13, len(RECORDED)):
        assert R.positions(RECORDED[:cut]) == full[:cut + 1]


def test_read_takes_a_file_and_gives_back_both_halves():
    moves, spots = R.read(N.encode_game(RECORDED, notes=["a game"]))
    assert moves == RECORDED
    assert len(spots) == len(RECORDED) + 1


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("moves, expect", [
    (["Jd3f5"], "ply 1"),                           # a move before anything is entered
    (["@Sd3"], "royal"),                            # the sequence places a royal first
    (RECORDED[:6] + ["Jd3f5"], "ply 7"),            # a move mid-entering
    (RECORDED + ["@Rd3"], "placement after"),       # a placement after entering
])
def test_a_record_that_is_not_playable_says_which_ply(moves, expect):
    with pytest.raises(R.RecordError) as caught:
        R.positions(moves)
    assert expect in str(caught.value), str(caught.value)


def test_legality_is_not_this_modules_business():
    """Both royals entered on one square is not a game anybody played. This draws it anyway.

    Worth pinning rather than leaving implicit, because it is the boundary the module rests
    on: what is refused here is a record that cannot be *drawn*, not one that could not
    have been *played*. Deciding the second is what `royals_web.game.positions` is for, and
    it is the walk an upload from a stranger goes through -- a file a person chose to open
    on their own machine has already been trusted more than that.
    """
    spots = R.positions(["@Rd3", "@Rd3"])
    assert len(spots) == 3

    web = pytest.importorskip("royals_web.game")
    with pytest.raises(web.ReplayError):
        web.positions(["@Rd3", "@Rd3"])


def test_a_misaligned_record_is_caught_rather_than_drawn():
    """Dropping one ply shifts every later move onto the wrong side.

    That is the failure this feature can actually produce -- a front end that forgets to
    write down a pass -- and it is the one that does not announce itself. What catches it
    is that the wrong side's move produces a board with somebody's pieces counted twice.
    """
    broken = RECORDED[:12] + RECORDED[13:]
    with pytest.raises(R.RecordError):
        R.positions(broken)


def test_notation_errors_are_still_notation_errors():
    with pytest.raises(N.NotationError):
        R.read("Zq9q9")


# ---------------------------------------------------------------------------
# The two walks must agree
# ---------------------------------------------------------------------------

def test_the_engines_walk_agrees_with_the_servers():
    web = pytest.importorskip("royals_web.game")

    for seed in (3, 11, 57):
        moves = recorded_game(seed, turns=30)
        mine = R.positions(moves)
        theirs, _game = web.positions(moves)

        assert len(mine) == len(theirs)
        for a, b in zip(mine, theirs):
            assert a.board == b.board, "ply %d" % a.ply
            assert list(a.squares) == b.last_move, "ply %d" % a.ply


# ---------------------------------------------------------------------------
# What a person calls a ply
# ---------------------------------------------------------------------------
# `ply` is the index into the record and `turn` is what gets shown to somebody. They differ
# by the whole entering phase, which is exactly why both exist -- and why the rule for
# turning one into the other is written down once rather than in each front end.

def test_a_move_number_is_not_a_ply_number():
    """The distinction the whole thing rests on. Move 7 is ply 19."""
    steps = len(R.ENTER_STEPS)
    assert R.turn_of_ply(0) == (R.PHASE_ENTERING, 1)
    assert R.turn_of_ply(steps - 1) == (R.PHASE_ENTERING, steps)
    assert R.turn_of_ply(steps) == (R.PHASE_PLAYING, 1)
    assert R.turn_of_ply(steps + 6) == (R.PHASE_PLAYING, 7)


def test_positions_carry_the_number_a_reader_is_shown():
    spots = R.positions(RECORDED)
    steps = len(R.ENTER_STEPS)

    # Ply 0 is not a turn of anything -- nobody has done anything yet.
    assert (spots[0].phase, spots[0].turn) == (R.PHASE_ENTERING, 0)
    for spot in spots[1:]:
        assert (spot.phase, spot.turn) == R.turn_of_ply(spot.ply - 1)

    assert spots[steps].turn == steps and spots[steps].phase == R.PHASE_ENTERING
    assert spots[steps + 1].turn == 1 and spots[steps + 1].phase == R.PHASE_PLAYING


def test_the_move_number_is_the_engines_own_turn_counter():
    """Not a display invention. `turn` here is the same `turn` a live game counts by.

    That is what makes `turn % 2` give the side, and it is why move 1 is red's: whoever
    entered second opens. If these two ever part company, the number a reviewer reads and
    the number the game is played by become different things that look the same.
    """
    web = pytest.importorskip("royals_web.game")

    game, _seat, _invite = web.new_game(mode="ai", side=web.BLUE, entry_seed=5)
    while game.phase == "entering":
        options = web.entering_options(game.board, game.entry_side, game.entry_piece)
        web.place(game, options[0], side=Engine.enteringChooser(game.entry_side))

    for _ in range(12):
        if game.phase != "playing":
            break
        # game.turn is the move about to be played; the ply it will occupy is the next
        # index in the move list.
        phase, turn = R.turn_of_ply(len(game.moves))
        assert phase == R.PHASE_PLAYING
        assert turn == game.turn, "ply %d" % len(game.moves)
        assert R.side_of_ply(len(game.moves)) == game.turn % 2

        legal = web.legal_moves(game.board, game.turn % 2, game.ko_set())
        web.play_move(game, legal[0] if legal else None, side=game.turn % 2)


def test_the_servers_positions_agree_about_the_numbering_too():
    web = pytest.importorskip("royals_web.game")

    moves = recorded_game(3, turns=20)
    mine = R.positions(moves)
    theirs, _game = web.positions(moves)

    for a, b in zip(mine, theirs):
        assert (a.phase, a.turn) == (b.phase, b.turn), "ply %d" % a.ply
