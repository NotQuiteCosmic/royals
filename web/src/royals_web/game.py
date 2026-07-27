"""The rules of a game in progress, with no HTTP anywhere in it.

This is the state MainPlay keeps on its call stack and RoyalsGUI writes down as fields.
A server can do neither: a request handler must be able to pick up a game it has never
seen, decide one thing, and put it back down. So the whole of a game is data --

    phase        "entering", "playing" or "over"
    enter_index  how far down Engine.enteringSequence() the entering has got
    turn         as MainPlay counts it; side to move is turn % 2
    ko_boards    every position the game has stood in, in order
    moves        the move list, in notation, entering placements included

-- and `advance` is the loop MainPlay runs, stopping wherever MainPlay would have
called input().

One thing worth knowing before changing anything here: **validating a move needs no
engine globals at all.** AI.listAllMoves and AI.performOneStep are pure, and the ko rule
is enforced by testing membership of a plain set that this module owns. Engine.koTrack
and the transposition table are touched in exactly one place in the whole web app --
inside an AI worker process, in ai_pool.py. That is what makes concurrent games safe
here without refactoring the engine.
"""

import uuid
from dataclasses import dataclass, field

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI
from royals_engine import notation as N


BLUE, RED = 0, 1
SIDE_NAMES = {BLUE: "blue", RED: "red"}

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
class Game:
    id: str
    human_side: int
    ai_depth: int
    entry_seed: int
    entry_noise: float

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

    # set while the entering phase is waiting on a human placement
    entry_piece: int = None
    entry_side: int = None

    _ko_set: set = field(default_factory=set, repr=False)

    # -- derived ------------------------------------------------------------

    @property
    def ai_side(self):
        return 1 - self.human_side

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
    def awaiting_human(self):
        """True when the game cannot go further without a person deciding something."""
        if self.finished:
            return False
        return self.side_to_move == self.human_side

    def ko_set(self):
        return self._ko_set

    def _record_ko(self, board):
        self.ko_boards.append(board)
        self._ko_set.add(board)


def new_game(human_side=BLUE, difficulty=DEFAULT_DIFFICULTY, entry_noise=0.5,
             entry_seed=None, rng=None):
    if human_side not in (BLUE, RED):
        raise ValueError("side must be 0 (blue) or 1 (red)")
    if difficulty not in DIFFICULTIES:
        raise ValueError("unknown difficulty %r" % (difficulty,))

    entry_noise = max(0.0, min(1.0, float(entry_noise)))
    if entry_seed is None:
        import random
        entry_seed = (rng or random).randrange(1 << 30)

    game = Game(
        id=uuid.uuid4().hex,
        human_side=human_side,
        ai_depth=DIFFICULTIES[difficulty],
        entry_seed=int(entry_seed),
        entry_noise=entry_noise,
        board=Hasher.Entering_Board(),
    )
    _seek_entry_step(game)
    return game


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


def to_json(game, include_legal=True):
    state = {
        "id": game.id,
        "phase": game.phase,
        "board": board_to_json(game.board),
        "humanSide": game.human_side,
        "aiSide": game.ai_side,
        "aiDepth": game.ai_depth,
        "sideToMove": game.side_to_move,
        "awaitingHuman": game.awaiting_human,
        "moves": list(game.moves),
        "ply": len(game.moves),
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
            if game.awaiting_human else [],
        }

    if game.phase == "playing" and include_legal:
        contr = game.turn % 2
        legal = legal_moves(game.board, contr, game.ko_set())
        state["mustPass"] = not legal
        if game.awaiting_human:
            state["origins"] = [N.square_to_alg(s)
                                for s in sorted({m[AI.MOVE_ORIGIN] for m in legal})]

    return state
