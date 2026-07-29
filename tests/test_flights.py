# Engine.moveFlights is what the desktop board draws its move arrows from, so the thing
# worth testing is not the shape of its answer but its agreement with the executors: every
# square a move actually changed should be somewhere in the flights, and no flight should
# name a square that isn't on the board.
#
# That invariant is what catches the two ways this can rot -- a push offset missed at the
# far end of the line, or a checkBreak asked of the wrong board -- and it catches them
# without a golden file, because it compares the helper against the engine rather than
# against a recording.

import random

from royals_engine import ai as artificialPlayer
from royals_engine import engine as Engine
from royals_engine import hasher as Hasher


# Seeded random play, which is how regress.py's move sweep reaches interesting positions and
# for the same reason: captures, prisoners and spies inside enemy stacks turn up far faster
# under random play than under a search steering for a tidy gather. The moves that exercise
# this helper hardest -- long pushes, breaks with prisoners in the queue -- only exist once
# the board is a mess.
def positions():
    boards = []
    for seed in (3, 11, 57):
        artificialPlayer.setEntryNoise(0.5, seed)
        board = Hasher.Entering_Board()
        for contr, piece in Engine.enteringSequence():
            isSpy = (piece == Hasher.SPY)
            if not Engine.enteringOptions(board, contr, isSpy): continue
            board = Engine.dropPiece(board, artificialPlayer.chooseEntry(board, contr, piece, isSpy),
                                     contr, piece)

        rng = random.Random(seed)
        boards.append(board)
        for turn in range(0, 24):
            moves = artificialPlayer.listAllMoves(board, turn % 2)
            if not moves: break
            board = artificialPlayer.performOneStep(board, turn % 2,
                                                    moves[rng.randrange(len(moves))])
            boards.append(board)
    return boards


def everyMove():
    for board in positions():
        for contr in (0, 1):
            for move in artificialPlayer.listAllMoves(board, contr):
                yield board, contr, move


def test_flights_cover_every_square_the_move_changed():
    checked = 0
    for board, contr, move in everyMove():
        after = artificialPlayer.performOneStep(board, contr, move)
        if after == board: continue          # a move the executor refused says nothing here

        flights = Engine.moveFlights(board, move, contr)
        touched = set()
        for fromSquare, toSquare, _dx, _dy, _steps in flights:
            touched.add(fromSquare)
            touched.add(toSquare)

        changed = set(n + 1 for n in range(49) if board[n] != after[n])
        missing = changed - touched
        assert not missing, (
            "%r changed %s but no flight names %s"
            % (move, sorted(changed), sorted(missing)))
        checked += 1

    # a guard on the guard: if the position builder ever stops producing moves, the loop
    # above passes by doing nothing at all
    assert checked > 200, "only %d moves exercised" % checked


def test_flights_stay_on_the_board():
    for board, contr, move in everyMove():
        for fromSquare, toSquare, dx, dy, steps in Engine.moveFlights(board, move, contr):
            assert 1 <= fromSquare <= 49
            assert 1 <= toSquare <= 49
            assert steps >= 1
            assert [dx, dy] in Engine.jumpDirs + Engine.pushDirs


def test_a_flight_never_stands_still():
    # every reported journey has to be one. A break drops a piece back onto the square it
    # left and that is deliberately not a flight; if one ever shows up here, the offset the
    # break ray starts at has moved.
    for board, contr, move in everyMove():
        for fromSquare, toSquare, _dx, _dy, _steps in Engine.moveFlights(board, move, contr):
            assert fromSquare != toSquare, "%r reports a flight going nowhere" % (move,)


def test_the_shatter_switch_changes_nothing_by_default():
    # moveFlights turns exePush's last argument off to see the board mid-move. Every caller
    # in the game leaves it alone, and this is what says those two agree.
    for board in positions():
        for contr in (0, 1):
            for move in artificialPlayer.listAllMoves(board, contr):
                if move[artificialPlayer.MOVE_KIND] not in ("push", "free"): continue
                origin = move[artificialPlayer.MOVE_ORIGIN]
                destination = move[artificialPlayer.MOVE_TARGET] + 1
                pris = move[artificialPlayer.MOVE_PRIS]
                free = move[artificialPlayer.MOVE_KIND] == "free"
                assert (Engine.exePush(board, origin, destination, contr, pris, free)
                        == Engine.exePush(board, origin, destination, contr, pris, free, True))
