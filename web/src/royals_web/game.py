"""The rules of a game in progress, with no HTTP anywhere in it.

This is the state MainPlay keeps on its call stack and RoyalsGUI writes down as fields.
A server can do neither: a request handler must be able to pick up a game it has never
seen, decide one thing, and put it back down. So the whole of a game is data --

    phase        "waiting", "entering", "playing" or "over"
    enter_index  how far down Engine.enteringSequence() the entering has got
    turn         as MainPlay counts it; side to move is turn % 2
    ko_boards    every position the game has stood in, in order
    moves        the move list, in notation, entering placements included

-- and `advance` is the loop MainPlay runs, stopping wherever MainPlay would have
called input().

**A game has two seats, and either may be a person.** Until M3 one of them was always
the computer, and the code said so: a single `human_side` field, with everything else
derived by subtracting it from one. Two people playing each other has no such field --
"the human" is whoever is asking -- so a seat is a thing in its own right, and the
question a request answers is not "is this the human's turn" but "is the side to move
the seat this token belongs to". Nothing about the rules changed; only who may ask.

One thing worth knowing before changing anything here: **validating a move needs no
engine globals at all.** AI.listAllMoves and AI.performOneStep are pure, and the ko rule
is enforced by testing membership of a plain set that this module owns. Engine.koTrack
and the transposition table are touched in exactly one place in the whole web app --
inside an AI worker process, in ai_pool.py. That is what makes concurrent games safe
here without refactoring the engine.
"""

import secrets
import time
import uuid
from dataclasses import dataclass, field

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI
from royals_engine import notation as N

from royals_web import seats as S


BLUE, RED = 0, 1
SIDE_NAMES = {BLUE: "blue", RED: "red"}

HUMAN, COMPUTER = "human", "ai"
MODES = ("ai", "human")

PIECE_NAMES = {Hasher.ROYAL: "royal", Hasher.PAWNS: "pawn", Hasher.SPY: "spy"}

# Depth is the whole of the difficulty setting. Times measured on a midgame position;
# a small cloud VM should be assumed to be roughly three times slower.
DIFFICULTIES = {
    "novice": 2,    # ~0.01s
    "casual": 3,    # ~0.04s
    "strong": 4,    # ~0.11s
    "expert": 5,    # ~0.53s
    "royal":  6,    # ~1.9s
}
DEFAULT_DIFFICULTY = "strong"


class IllegalMove(Exception):
    """The move is well-formed but not legal in this position, or not this player's to make."""


class GameOver(Exception):
    """The game has already finished."""


# ---------------------------------------------------------------------------
# Legality -- the single source of truth
# ---------------------------------------------------------------------------

def legal_moves(board, contr, ko_boards):
    """Every move `contr` may actually play here.

    The ko filter is not optional and not a detail. A side can have moves in the sense
    that listAllMoves returns some, while every one of them returns the game to a
    position it has already stood in -- in which case the side has no legal move and
    must pass. minimax handles that implicitly at its root; anything else asking
    "does this player have moves?" has to apply the same filter or it will refuse a
    legitimate pass and offer the UI moves the validator would then reject.

    So this function serves all three callers: the move validator, the UI's highlighting,
    and the pass/draw detector. Nothing else may decide what is legal.
    """
    return [m for m in AI.listAllMoves(board, contr)
            if AI.performOneStep(board, contr, m) not in ko_boards]


def entering_options(board, contr, piece):
    """1-based squares this piece may be entered on."""
    return Engine.enteringOptions(board, contr, piece == Hasher.SPY)


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

@dataclass
class Seat:
    """One side of a game, and the token that proves you are sitting in it.

    `token_hash` is None for a computer seat -- nobody holds it -- and `claimed` is what
    separates "this seat is yours" from "this seat is waiting for whoever opens the
    invite". A human seat is created unclaimed for the second player and claimed for
    whoever created the game.
    """
    kind: str                   # "human" | "ai"
    token_hash: str = None
    claimed: bool = False


@dataclass
class Game:
    id: str
    mode: str                   # "ai" | "human"
    ai_depth: int
    entry_seed: int
    entry_noise: float

    # side -> Seat. Always both sides; for an ai game one of them is the computer.
    seats: dict = field(default_factory=dict)

    # sha256 of the single-use token that claims the empty seat, or None once claimed.
    invite_hash: str = None

    board: tuple = ()
    phase: str = "entering"
    enter_index: int = 0
    turn: int = 0
    passes: int = 0

    # Every position the game has stood in, from the one entering left behind onwards.
    # A list, not a set, so it can be replayed and stored in order; membership tests go
    # through ko_set.
    ko_boards: list = field(default_factory=list)
    moves: list = field(default_factory=list)

    result: str = None          # "blue" | "red" | "draw"
    termination: str = None     # "gather" | "resign" | "double_pass" | "no_moves"
    last_move: list = field(default_factory=list)   # squares to highlight, 1-based

    # set while the entering phase is waiting on a placement
    entry_piece: int = None
    entry_side: int = None

    # Bumped by every mutation. The client polls on this, so it has to move for things
    # `ply` cannot see -- a seat being claimed, a resignation -- not just for moves.
    version: int = 0

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    _ko_set: set = field(default_factory=set, repr=False)

    # -- derived ------------------------------------------------------------

    @property
    def side_to_move(self):
        if self.phase == "entering":
            return self.entry_side
        if self.phase == "playing":
            return self.turn % 2
        return None

    @property
    def finished(self):
        return self.phase == "over"

    @property
    def ai_to_move(self):
        """True when the server can go further on its own. This is what drives _advance."""
        side = self.side_to_move
        return side is not None and self.seats[side].kind == COMPUTER

    def awaits(self, side):
        """True when the game is waiting on this particular seat."""
        return side is not None and self.side_to_move == side

    def seat_of(self, token):
        """Which side this token sits in, or None. Constant-time against both seats.

        Both seats are always checked, and the result is not short-circuited on the
        first match, so the time taken says nothing about which seat a wrong token
        nearly matched.
        """
        found = None
        for side, seat in self.seats.items():
            if S.verify(token, seat.token_hash):
                found = side
        return found

    def touch(self):
        self.version += 1
        self.updated_at = time.time()

    def ko_set(self):
        return self._ko_set

    def _record_ko(self, board):
        self.ko_boards.append(board)
        self._ko_set.add(board)


def new_game(mode="ai", side=BLUE, difficulty=DEFAULT_DIFFICULTY, entry_noise=0.5,
             entry_seed=None, rng=None):
    """Start a game and return (game, seat_token, invite_token).

    `side` is the side the *creator* takes, or "random". The tokens are returned here and
    nowhere else, ever again: the game stores only their hashes. `invite_token` is None
    for a game against the computer, which has no empty seat to claim.
    """
    if mode not in MODES:
        raise ValueError("mode must be one of %s" % (MODES,))
    if side == "random":
        side = secrets.choice((BLUE, RED))
    if side not in (BLUE, RED):
        raise ValueError("side must be 0 (blue), 1 (red) or 'random'")
    if difficulty not in DIFFICULTIES:
        raise ValueError("unknown difficulty %r" % (difficulty,))

    entry_noise = max(0.0, min(1.0, float(entry_noise)))
    if entry_seed is None:
        import random
        entry_seed = (rng or random).randrange(1 << 30)

    seat_token = S.mint()
    invite_token = S.mint() if mode == "human" else None

    game = Game(
        id=uuid.uuid4().hex,
        mode=mode,
        # A human game has no computer in it, so there is no depth to speak of. Leaving
        # a number here would be a setting that silently does nothing.
        ai_depth=DIFFICULTIES[difficulty] if mode == "ai" else None,
        entry_seed=int(entry_seed),
        entry_noise=entry_noise,
        board=Hasher.Entering_Board(),
        invite_hash=S.hash_token(invite_token) if invite_token else None,
        seats={
            side: Seat(kind=HUMAN, token_hash=S.hash_token(seat_token), claimed=True),
            1 - side: Seat(kind=HUMAN, claimed=False) if mode == "human"
                      else Seat(kind=COMPUTER, claimed=True),
        },
    )

    if mode == "human":
        # Nothing may happen until somebody is sitting in the other seat -- not even the
        # first placement, which would otherwise commit one player to a formation while
        # the game is still, as far as anyone knows, an unanswered message.
        game.phase = "waiting"
    else:
        _seek_entry_step(game)

    return game, seat_token, invite_token


class InviteError(Exception):
    """The invite is spent, wrong, or there is no seat left to claim."""


def claim_seat(game, invite_token):
    """Take the empty seat. Returns (side, seat_token).

    Single use: the invite hash is cleared here, so a link that has been used is a link
    that no longer does anything. Whoever holds the resulting seat token is the player.
    """
    if game.invite_hash is None:
        raise InviteError("that invitation has already been used")
    if not S.verify(invite_token, game.invite_hash):
        raise InviteError("that invitation is not valid for this game")

    open_seats = [side for side, seat in game.seats.items() if not seat.claimed]
    if not open_seats:
        raise InviteError("both seats are taken")

    side = open_seats[0]
    seat_token = S.mint()
    game.seats[side] = Seat(kind=HUMAN, token_hash=S.hash_token(seat_token), claimed=True)
    game.invite_hash = None

    if game.phase == "waiting":
        game.phase = "entering"
        _seek_entry_step(game)
    game.touch()

    return side, seat_token


# ---------------------------------------------------------------------------
# The entering phase
# ---------------------------------------------------------------------------

ENTER_STEPS = Engine.enteringSequence()


def _seek_entry_step(game):
    """Walk forward to the next placement that actually has somewhere to go.

    A side whose every square is hemmed in sits the step out -- the same skip
    RoyalsGUI.enterStep performs. When the steps run out, play begins.
    """
    while game.enter_index < len(ENTER_STEPS):
        contr, piece = ENTER_STEPS[game.enter_index]
        if entering_options(game.board, contr, piece):
            game.entry_side, game.entry_piece = contr, piece
            return
        game.moves.append("--")     # skipped placement, recorded so replay stays aligned
        game.enter_index += 1

    _start_play(game)


def place(game, square, side=None):
    """Enter the current piece on `square` (1-based). Raises IllegalMove if it isn't legal."""
    if game.phase != "entering":
        raise IllegalMove("the entering phase is over")

    contr, piece = game.entry_side, game.entry_piece
    if side is not None and side != contr:
        raise IllegalMove("it is %s's turn to enter" % SIDE_NAMES[contr])

    if square not in entering_options(game.board, contr, piece):
        raise IllegalMove("a %s may not be entered on %s"
                          % (PIECE_NAMES[piece], N.square_to_alg(square)))

    game.board = Engine.dropPiece(game.board, square, contr, piece)
    game.moves.append(N.encode_entry(piece, square))
    game.last_move = [square]
    game.enter_index += 1
    _seek_entry_step(game)
    game.touch()
    return game


def _start_play(game):
    """Entering is done. The history starts on the position it left behind, so the first
    move cannot undo back into it either."""
    game.phase = "playing"
    game.entry_side = game.entry_piece = None
    game._ko_set = set()
    game.ko_boards = []
    game._record_ko(game.board)
    # whoever entered second opens -- the side that placed the last spy moves now
    game.turn = 1
    game.passes = 0


# ---------------------------------------------------------------------------
# Play
# ---------------------------------------------------------------------------

def play_move(game, move, side=None):
    """Apply one move. `move` is an engine move tuple, or None to pass."""
    if game.finished:
        raise GameOver("this game has finished")
    if game.phase != "playing":
        raise IllegalMove("the game is still in the entering phase")

    contr = game.turn % 2
    if side is not None and side != contr:
        raise IllegalMove("it is %s's turn" % SIDE_NAMES[contr])

    legal = legal_moves(game.board, contr, game.ko_set())

    if move is None:
        # A pass is legal only when there is genuinely nothing to play.
        if legal:
            raise IllegalMove("you have %d legal moves, so you may not pass" % len(legal))
        game.moves.append("--")
        game.last_move = []
        game.passes += 1
        game.touch()
        if game.passes > 1:
            _finish(game, None, "double_pass")
            return game
        game.turn += 1
        return game

    if move not in legal:
        # Deliberately not explaining which rule it broke: the client should be asking
        # for legal moves, not guessing, and a validator that narrates is a validator
        # that can be used to probe the position.
        raise IllegalMove("that is not a legal move")

    game.passes = 0
    new_board = AI.performOneStep(game.board, contr, move)

    game.board = new_board
    game.moves.append(N.encode_move(move))
    game.last_move = _highlight(move)
    game._record_ko(new_board)
    game.touch()

    end, winner = Hasher.Check_For_Winner(new_board)
    if end:
        # winner is indexed [blue, red]
        side_won = BLUE if winner[BLUE] else RED
        _finish(game, side_won, "gather")
        return game

    game.turn += 1
    return game


def _highlight(move):
    """The squares a client should flash for this move: origin and destination."""
    origin = move[AI.MOVE_ORIGIN]
    if move[AI.MOVE_KIND] == "break":
        return [origin]
    return [origin, move[AI.MOVE_TARGET] + 1]


def resign(game, side):
    if game.finished:
        raise GameOver("this game has finished")
    _finish(game, 1 - side, "resign")
    return game


def _finish(game, winner_side, termination):
    game.phase = "over"
    game.result = "draw" if winner_side is None else SIDE_NAMES[winner_side]
    game.termination = termination
    game.touch()


# ---------------------------------------------------------------------------
# Rebuilding a game from its move list
# ---------------------------------------------------------------------------

class ReplayError(Exception):
    """A stored move list does not describe the game it claims to."""


def replay(*, id, mode, ai_depth, entry_seed, entry_noise, moves, seats,
           invite_hash=None, result=None, termination=None, ply=None,
           version=0, created_at=None, updated_at=None):
    """Rebuild a game by playing its moves again through `place` and `play_move`.

    This is the load path, and it is a replay rather than a deserialization on purpose.
    A Game carries `ko_boards` -- every position it has ever stood in, because the ko
    rule is about the whole history and not a window of it. Storing the board alone loses
    that; storing the history alongside the board means maintaining a second way of
    building it, which is a second way of being wrong. Replaying rebuilds it as a side
    effect of the same `_record_ko` that built it the first time, so there is only ever
    one construction of a game's history and it is the one the rules use.

    It also makes the record largely self-checking: the regenerated move list is compared
    against the stored one, so a reordered, edited or unplayable row fails to load rather
    than quietly producing a board nobody played to. That comparison does more than it
    looks -- a placement records which *piece* was placed, so a row claiming a spy went
    where a pawn went re-encodes differently and is caught, without this function having
    to check pieces itself.

    **Self-consistency has exactly one blind spot, and `ply` is here to cover it.** A
    prefix of a legal game is a legal game: lop the last two moves off a record and it
    replays perfectly, into a real position that simply is not the current one. Nothing
    intrinsic to the move list can notice, because nothing is wrong with it. So the
    length is stored separately and checked here, which turns "these moves are playable"
    into "these moves are playable *and* there are as many of them as there were".
    """
    stored = moves.split() if isinstance(moves, str) else list(moves)

    game = Game(
        id=id, mode=mode, ai_depth=ai_depth,
        entry_seed=int(entry_seed), entry_noise=float(entry_noise),
        board=Hasher.Entering_Board(), seats=dict(seats), invite_hash=invite_hash,
    )

    # A game whose second seat was never claimed never started, and has no moves to
    # replay -- claim_seat is what walks it into the entering phase.
    waiting = mode == "human" and not all(s.claimed for s in game.seats.values())
    if waiting:
        game.phase = "waiting"
    else:
        _seek_entry_step(game)

    try:
        for token in stored:
            if game.phase == "entering":
                # A "--" during entering is a step some side had nowhere to make, and
                # _seek_entry_step has already written it down again. Only placements are
                # decisions anybody made.
                if token == N.PASS:
                    continue
                _piece, square = N.decode_entry(token)
                place(game, square, side=game.entry_side)
            elif game.phase == "playing":
                play_move(game, None if token == N.PASS else N.decode_move(token),
                          side=game.turn % 2)
            else:
                raise ReplayError("%r comes after the game was already over" % (token,))
    except (IllegalMove, GameOver, N.NotationError) as exc:
        raise ReplayError("the stored move list is not playable: %s" % (exc,)) from exc

    if game.moves != stored:
        raise ReplayError("the move list does not replay to itself")
    if ply is not None and len(game.moves) != ply:
        raise ReplayError("the record says %d moves and carries %d"
                          % (ply, len(game.moves)))

    _restore_ending(game, result, termination)

    game.version = int(version)
    if created_at is not None:
        game.created_at = float(created_at)
    if updated_at is not None:
        game.updated_at = float(updated_at)
    return game


def _restore_ending(game, result, termination):
    """Put back an ending that the moves alone cannot describe.

    Gathering and a double pass are in the move list, so replay reaches them by itself
    and all that is left is to check the record agrees. A resignation is not a move and
    leaves no trace in the list, so it is the one ending that has to be reapplied -- and
    the only one this will accept from outside the moves.
    """
    if game.finished:
        if (game.result, game.termination) != (result, termination):
            raise ReplayError(
                "the moves end %s by %s but the record says %s by %s"
                % (game.result, game.termination, result, termination))
        return

    if termination is None:
        return
    if termination != "resign":
        raise ReplayError("only a resignation ends a game outside its move list, not %r"
                          % (termination,))
    if result not in SIDE_NAMES.values():
        raise ReplayError("a resignation needs a winner, not %r" % (result,))

    winner = BLUE if result == SIDE_NAMES[BLUE] else RED
    _finish(game, winner, "resign")


# ---------------------------------------------------------------------------
# Serialising for the client
# ---------------------------------------------------------------------------

def board_to_json(board):
    """49 squares, each as its semantic fields.

    The browser gets parsed squares rather than packed integers on purpose: unpacking
    13-bit fields in JavaScript would be a second implementation of the board format,
    and the moment there are two, they disagree. The client draws what it is told.
    """
    out = []
    for code in board:
        if not code:
            out.append(None)
            continue
        s = Hasher.Parse_Space(code)
        out.append({
            "side": s[Hasher.SIDE],
            "dragon": s[Hasher.DRAGON],
            "spy": s[Hasher.SPY],
            "pawns": s[Hasher.PAWNS],
            "royal": s[Hasher.ROYAL],
            "capSpy": s[Hasher.CAPSPY],
            "capPawns": s[Hasher.CAPPAWNS],
            "prisFlag": s[Hasher.PRISFLAG],
        })
    return out


def moves_from(game, origin, moving_pris=False):
    """Everything the side to move may do from one square, for highlighting.

    Whether the origin is a spy breaking out of an enemy stack is worked out from the
    board, never taken from the client -- the same derivation listAllMoves performs.
    """
    if game.phase != "playing" or game.finished:
        return []

    contr = game.turn % 2
    legal = legal_moves(game.board, contr, game.ko_set())
    return [m for m in legal
            if m[AI.MOVE_ORIGIN] == origin and bool(m[AI.MOVE_PRIS]) == bool(moving_pris)]


def movable_origins(game):
    """Squares the side to move has at least one legal move from."""
    if game.phase != "playing" or game.finished:
        return []
    contr = game.turn % 2
    return sorted({m[AI.MOVE_ORIGIN]
                   for m in legal_moves(game.board, contr, game.ko_set())})


def to_json(game, viewer_side=None, include_legal=True):
    """The game as one particular viewer sees it.

    `viewer_side` is the seat the request proved it holds, or None for a spectator --
    anybody with the link, since Royals is perfect-information and there is nothing in a
    position to hide. What being a player changes is not what you can *see* but what the
    page should offer you: `origins` and the entering options are the squares *you* may
    click, so they are sent only to the side actually to move. A spectator's client then
    has nothing to render as clickable without having to know it is a spectator.
    """
    yours = viewer_side if viewer_side in (BLUE, RED) else None
    awaiting_you = game.awaits(yours)

    state = {
        "id": game.id,
        "mode": game.mode,
        "phase": game.phase,
        "board": board_to_json(game.board),
        "yourSide": yours,
        "seats": {str(side): {"kind": seat.kind, "claimed": seat.claimed}
                  for side, seat in game.seats.items()},
        "aiDepth": game.ai_depth,
        "sideToMove": game.side_to_move,
        "awaitingYou": awaiting_you,
        "moves": list(game.moves),
        "ply": len(game.moves),
        "version": game.version,
        "lastMove": [N.square_to_alg(s) for s in game.last_move],
        "result": game.result,
        "termination": game.termination,
    }

    if game.phase == "entering":
        state["entering"] = {
            "piece": PIECE_NAMES[game.entry_piece],
            "side": game.entry_side,
            "step": game.enter_index + 1,
            "total": len(ENTER_STEPS),
            "options": [N.square_to_alg(s)
                        for s in entering_options(game.board, game.entry_side, game.entry_piece)]
            if awaiting_you else [],
        }

    if game.phase == "playing" and include_legal:
        contr = game.turn % 2
        legal = legal_moves(game.board, contr, game.ko_set())
        state["mustPass"] = not legal
        if awaiting_you:
            state["origins"] = [N.square_to_alg(s)
                                for s in sorted({m[AI.MOVE_ORIGIN] for m in legal})]

    return state
