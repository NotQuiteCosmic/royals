"""Reading a game back: the board after every ply of a record.

Walking through a finished game is applying its plies in order, and that is all this does.
What is worth saying is what it deliberately does *not* do.

**Reviewing a game needs none of the rules.** A record holds moves that were already found
legal at the moment they were played, so nothing here has to decide whether a move *is*
legal. There is no move generation, no ko set, no winner check and no question of whether
a side may pass -- it is an animated slideshow of a game somebody has already played. That
is why this is a short module rather than a second copy of the game loop.

It is also why a review cannot disturb a game in progress, on any front end. `dropPiece`
and `performOneStep` are pure -- they take a board and return a new one -- and neither they
nor `moveFlights` touch `Engine.koTrack`, `Engine.koGeneration` or the AI's tables.
`koRecord` and `koReset` are not called from anywhere in this package; ko is the driver's
business, and a driver that is only showing what happened has none.

    The one thing that would change that: showing *the alternatives that were available*
    at each ply, which is a genuinely useful thing for a viewer to do. That needs move
    generation, and move generation needs the ko history to be right. It should build a
    throwaway ko set from the prefix rather than reach for the module globals -- which is
    what web/src/royals_web/ai_pool.py exists to say about the search.

**The side of every ply is derived, not stored.** The entering phase is exactly as many
plies as `enteringSequence()` is long -- a side with nowhere to place still writes a "--"
-- so the first ply of play is always at a known index, and the parity from there is the
turn counter. That invariant is the whole reason a pass has to be written down at all, and
a front end that leaves one out produces a record that replays every later move as the
wrong side's, into a real position nobody played to.

The web has a second walk of the same list, in `royals_web.game.positions`, which goes
through `place` and `play_move` and so re-derives legality as it goes. That one serves
uploads from strangers and has to; this one serves a file its reader chose to open. They
must agree ply for ply, and tests/test_record.py holds them to it.
"""

import collections

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI
from royals_engine import notation as N


# `squares` is what a front end should highlight -- origin and destination, or just the
# origin for a break, or the square a piece was entered on. `flights` is what
# Engine.moveFlights reports and is empty for anything that is not a move.
#
# `phase` and `turn` are what a *person* calls this ply. `ply` is the index into the
# record, which is the right handle for code and the wrong one to show anybody: it counts
# the twelve placements as moves one to twelve, so "ply 19" is the seventh move of a game
# whose first move was ply 13. See turn_of_ply.
Position = collections.namedtuple(
    "Position", "ply board token side squares flights phase turn")


class RecordError(ValueError):
    """Well-formed notation that does not describe a game anybody could have played."""


ENTER_STEPS = Engine.enteringSequence()

PHASE_ENTERING, PHASE_PLAYING = "entering", "playing"


def turn_of_ply(index):
    """What a person calls ply `index`: (phase, number), both counting from one.

        ("entering", 1..12)     the placements
        ("playing",  1..N)      the moves

    Two numberings rather than one running count, because they answer different questions
    -- "which of the twelve placements is this" and "how many moves into the game are we"
    -- and a single count answers the second one wrong by twelve for the whole game.

    The move number is the engine's own `turn`, not a new invention: it opens at 1 with
    whoever entered second, which is why `turn % 2` gives the side and why "move 1" is
    red's. `royals_web.game.Game.turn` counts identically, and tests/test_record.py holds
    this function to a real game's to keep that true.
    """
    if index < len(ENTER_STEPS):
        return PHASE_ENTERING, index + 1
    return PHASE_PLAYING, index - len(ENTER_STEPS) + 1


def side_of_ply(index):
    """Which side played ply `index`, counting from zero.

    During entering it comes from the sequence itself. Afterwards it is the turn counter,
    which opens at 1 -- whoever entered second moves first -- so the first ply of play
    belongs to red.
    """
    if index < len(ENTER_STEPS):
        return ENTER_STEPS[index][0]
    return (index - len(ENTER_STEPS) + 1) % 2


def positions(moves):
    """Every board a record passes through, as `Position`s. `len(moves) + 1` of them.

    The first is the empty board the entering phase starts from; after that there is one
    per ply, including the plies where nothing happened.

    Raises `RecordError` for a record that is readable but not playable. Deciding legality
    is not this module's job, but a record that has slipped out of alignment produces
    boards with five pawns on them, and handing those to a renderer is worse than saying
    so: `validate_board` catches exactly that, for the cost of counting the pieces.
    """
    tokens = list(moves)

    board = Hasher.Entering_Board()
    # Ply 0 is the board before anybody did anything, so it is not a turn of any kind.
    out = [Position(0, board, None, None, (), (), PHASE_ENTERING, 0)]

    for index, token in enumerate(tokens):
        try:
            board, spot = _apply(board, index, token)
        except (N.NotationError, RecordError) as exc:
            raise RecordError("ply %d (%r): %s" % (index + 1, token, exc)) from exc
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            # The executors assume they are being handed a move that was legal. One that
            # was not can index off the end of a table rather than politely refusing, and
            # a reader that crashes on a truncated file is a reader nobody trusts.
            raise RecordError("ply %d (%r) is not playable here: %s"
                              % (index + 1, token, exc)) from exc
        out.append(spot)

    return out


def _apply(board, index, token):
    """One ply. Returns (the board after it, the Position describing it)."""
    kind, payload = N.decode_ply(token)
    side = side_of_ply(index)
    phase, turn = turn_of_ply(index)
    entering = phase == PHASE_ENTERING

    def spot(board, squares=(), flights=()):
        return Position(index + 1, board, token, side, squares, flights, phase, turn)

    if kind == N.PLY_PASS:
        # Entering: a side with nowhere to place. Playing: a side with no legal move.
        # Neither changes the board, and both consume a ply.
        return board, spot(board)

    if entering:
        if kind != N.PLY_ENTER:
            raise RecordError("a move during the entering phase")
        piece, square = payload
        if piece != ENTER_STEPS[index][1]:
            raise RecordError("the entering sequence places a %s here, not a %s"
                              % (_piece_name(ENTER_STEPS[index][1]), _piece_name(piece)))
        board = N.validate_board(Engine.dropPiece(board, square, side, piece))
        return board, spot(board, (square,))

    if kind != N.PLY_ENTER:
        move = payload
        # Measured against the board the move was made on, so this has to happen first.
        flights = Engine.moveFlights(board, move, side)
        origin = move[AI.MOVE_ORIGIN]
        squares = ((origin,) if move[AI.MOVE_KIND] == "break"
                   else (origin, move[AI.MOVE_TARGET] + 1))
        board = N.validate_board(AI.performOneStep(board, side, move))
        return board, spot(board, squares, flights)

    raise RecordError("a placement after the entering phase was over")


def _piece_name(piece):
    return {Hasher.ROYAL: "royal", Hasher.PAWNS: "pawn", Hasher.SPY: "spy"}.get(piece, "?")


def read(text):
    """The text of a record -> (its move list, every position it passes through)."""
    moves = N.decode_game(text)
    return moves, positions(moves)
