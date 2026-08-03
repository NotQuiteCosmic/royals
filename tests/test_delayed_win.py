"""The delayed win: gathering does not end the game, surviving a reply does.

The rule is stated as a property of the position rather than as an extra turn -- **you have
won if, at the start of your own turn, your spy, four pawns and royal are still on one
square** -- so there is no flag to carry and no counter to get wrong. What there is instead
is a moment that has to be checked in the right place, and the failure mode is that a caller
keeps testing for a completed stack straight after a move and quietly ends the game a reply
early. That is what these tests are shaped around.

The only answer a completed six actually has is a lone spy's shattering push. A break needs
the enemy's own spy standing in the stack; and nothing can land on the six at all, since it
holds the royal and nothing may land on a royal at any weight -- so matching it with six of
your own is not an answer either, which the prose here used to say it was. Every test here
therefore turns on whether a spy is beside the gathering square, which is most of the rule's
strategic content. The rest of it -- that the shatter also needs the square *behind* the
stack empty, because a spy has one point of strength and spends it on the square it shoves --
is in `test_win_horizon.py`, along with the search's handling of a gather made at a leaf.

`Check_For_Winner` is deliberately NOT part of this change. It stays a pure detector of
"there is a completed stack", which is what lets regress.py's move sweep go on calling it and
keeps golden_moves.txt byte-identical through a rules change.
"""

import pytest

from royals_engine import ai as AI
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher

BLUE, RED = 0, 1

# d4 is index 24; d5, directly below it, is index 31. Orthogonal neighbours, which is what a
# push needs.
D4, D5, E5 = 24, 31, 32


def gathered(spy_alongside):
    """Blue's six already on d4, optionally with a lone red spy beside them on d5."""
    board = list(Hasher.EMPTY_BOARD)
    board[D4] = Hasher.Build_Space(BLUE, 0, 1, 4, 1, 0, 0, 0)
    if spy_alongside:
        board[D5] = Hasher.Build_Space(RED, 0, 1, 0, 0, 0, 0, 0)
    return tuple(board)


def fresh(board):
    """One game's worth of engine state, and none of the previous test's."""
    Engine.koReset()
    AI.newGame()
    Engine.koRecord(board)


####### The detector is unchanged #######

def test_the_detector_still_reports_a_completed_stack():
    """The rule moved; Check_For_Winner did not.

    If this ever starts answering "no" for a gathered six, golden_moves.txt moves with it --
    regress.py's move sweep calls this function directly.
    """
    assert Hasher.Check_For_Winner(gathered(spy_alongside=False)) == [True, [1, 0]]
    assert Hasher.Check_For_Winner(gathered(spy_alongside=True)) == [True, [1, 0]]


####### The engine finds the answer #######

def test_a_lone_spy_shatters_a_gathered_six():
    board = gathered(spy_alongside=True)
    fresh(board)

    after, move, _score = AI.takeTurn(board, RED, 3)

    assert move is not None, "red has a reply to find"
    assert Hasher.Check_For_Winner(after)[1][BLUE] == 0, \
        "red's spy should have shattered the six rather than leaving it standing"


def test_without_a_spy_alongside_the_six_stands():
    """The other half, and the reason the rule is narrow: with no spy beside it, nothing red
    can play touches the stack, so the gather is as good as a win already."""
    board = gathered(spy_alongside=False)
    board = Hasher.Mod_Space(board, D5 + 1,
                             Hasher.Build_Space(RED, 0, 0, 1, 0, 0, 0, 0))   # a pawn, not a spy
    fresh(board)

    after, move, _score = AI.takeTurn(board, RED, 3)

    assert move is not None
    assert Hasher.Check_For_Winner(after)[1][BLUE] == 1, \
        "a lone pawn cannot break a six; the stack should still be standing"


####### The game loop ends the game at the right moment #######

# Imported rather than importorskip'd, and marked per test below. A module-level skip would
# take the four engine tests above out with it -- they need no server at all, and they are the
# ones that prove the search finds the shattering reply. Losing them in a no-web run would be
# invisible: the file would report one tidy skip and nobody would notice that the half of this
# file which tests the *rule* had stopped running.
try:
    from royals_web import game as G
except ImportError:                    # the web package is a separate, optional install
    G = None

needs_web = pytest.mark.skipif(G is None, reason="royals_web is not installed")


def playing_game(board, turn):
    """A game in the play phase on a given position, with no HTTP and no entering."""
    game, _seat, _invite = G.new_game(mode="human", random_entry=True)
    game.board = board
    game.phase = "playing"
    game.turn = turn
    game.passes = 0
    game.moves = list(game.moves)
    game._ko_set = set()
    game._record_ko(board)
    return game


def one_move_from_gathering(answerer):
    """Blue's spy and four pawns on d4 with the royal a jump away on e5.

    `answerer` is the red piece placed on d5, beside the gathering square: a spy can shatter
    the six and a pawn cannot, which is the whole difference between a won game and a lost
    tempo.
    """
    board = list(Hasher.EMPTY_BOARD)
    board[D4] = Hasher.Build_Space(BLUE, 0, 1, 4, 0, 0, 0, 0)
    board[E5] = Hasher.Build_Space(BLUE, 0, 0, 0, 1, 0, 0, 0)
    spy = 1 if answerer == "spy" else 0
    board[D5] = Hasher.Build_Space(RED, 0, spy, 1 - spy, 0, 0, 0, 0)
    return tuple(board)


def gathering_move(board):
    """Blue's royal jumping in to complete the six."""
    for move in G.legal_moves(board, BLUE, set()):
        child = AI.performOneStep(board, BLUE, move)
        if Hasher.Check_For_Winner(child)[1][BLUE]:
            return move
    raise AssertionError("the fixture should offer a move that completes the six")


@needs_web
def test_the_gathering_move_itself_does_not_end_the_game():
    """The discriminating test, and the one the whole change is for.

    Under the old rule this move ended the game on the spot. Now it hands the turn to red,
    who is owed a reply -- so the assertion is not about who won but about the game still
    being live and red being on move.
    """
    game = playing_game(one_move_from_gathering("spy"), turn=BLUE)

    G.play_move(game, gathering_move(game.board), side=BLUE)

    assert Hasher.Check_For_Winner(game.board)[1][BLUE] == 1, "the six really is complete"
    assert not game.finished, "gathering must not end the game by itself"
    assert game.result is None
    assert game.side_to_move == RED, "red is owed the reply"


@needs_web
def test_the_spy_takes_the_reply_and_the_game_goes_on():
    game = playing_game(one_move_from_gathering("spy"), turn=BLUE)
    G.play_move(game, gathering_move(game.board), side=BLUE)

    legal = G.legal_moves(game.board, RED, game.ko_set())
    shatter = [m for m in legal
               if Hasher.Check_For_Winner(AI.performOneStep(game.board, RED, m))[1][BLUE] == 0]
    assert shatter, "the spy's push should be among red's legal moves"

    G.play_move(game, shatter[0], side=RED)

    assert not game.finished, "the six was shattered, so nobody has won"
    assert game.result is None


@needs_web
def test_a_gather_wins_once_the_reply_has_come_and_gone():
    """The same arc with a pawn where the spy was: red has a move but no answer."""
    game = playing_game(one_move_from_gathering("pawn"), turn=BLUE)
    G.play_move(game, gathering_move(game.board), side=BLUE)
    assert not game.finished

    legal = G.legal_moves(game.board, RED, game.ko_set())
    assert legal, "red needs a move to spend"
    G.play_move(game, legal[0], side=RED)

    assert game.finished, "blue's turn began on a completed six, so blue has won"
    assert game.result == "blue"
    assert game.termination == "gather"


@needs_web
def test_the_win_is_settled_even_when_the_reply_is_a_forced_pass():
    """A side with nothing to play still hands the turn back, and the win still lands.

    The pass path and the move path are different routes out of play_move, and an
    implementation that only settles the win on one of them looks completely fine until
    somebody is stuck.
    """
    game = playing_game(gathered(spy_alongside=False), turn=RED)
    assert not G.legal_moves(game.board, RED, game.ko_set()), "red should have nothing to play"

    G.play_move(game, None, side=RED)

    assert game.finished
    assert game.result == "blue"
    assert game.termination == "gather"
