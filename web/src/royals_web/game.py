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
from royals_engine import record as R

from royals_web import seats as S


BLUE, RED = 0, 1
SIDE_NAMES = {BLUE: "blue", RED: "red"}

HUMAN, COMPUTER = "human", "ai"
MODES = ("ai", "human")

PIECE_NAMES = {Hasher.ROYAL: "royal", Hasher.PAWNS: "pawn", Hasher.SPY: "spy"}

# Depth is the whole of the difficulty setting. Times measured on a midgame position;
# a small cloud VM should be assumed to be roughly three times slower.
# Depths, and roughly what each costs with the compiled engine. Re-laddered when the Rust
# port landed: the search got about thirty-six times faster, so the whole previous ladder
# (2 through 6) had come to cost less than `novice` used to, and `royal` -- the setting a
# player picks when they want to be beaten -- was answering in under a tenth of a second.
#
# The speed is spent on strength rather than latency: every rung moved up about three plies,
# which is what a 36x search buys at an effective branching factor of ~3.4, and the top rung
# still costs roughly what the top rung always cost. A player who chose `royal` gets the same
# few seconds of waiting and a considerably better opponent for it.
#
# **These map a name to a depth at creation time and nowhere else.** A game stores its
# `ai_depth` as an integer and `replay()` takes that integer straight back, so an in-flight
# game started under the old ladder keeps playing at the depth it was created with -- a game
# saved as `royal` is still a depth-6 game and does not silently become something else.
# Nothing reverse-maps a depth to a name, so there is no display to go stale either.
DIFFICULTIES = {
    "novice": 3,    # ~0.001s
    "casual": 5,    # ~0.016s
    "strong": 7,    # ~0.19s
    "expert": 8,    # ~0.82s
    "royal":  9,    # ~3.1s
    "dragon": 10,   # ~18s
}
DEFAULT_DIFFICULTY = "strong"


# An upper bound on how long one game may run.
#
# The ko rule forbids returning to a position the game has already stood in, which makes a
# game finite but not short: the bound it implies is the number of reachable positions,
# which is astronomical. So a game can be pushed to an arbitrary length by two players who
# want to, and a game's whole move list is replayed on every load and stored in one row --
# meaning one game's length is a cost the server carries, not just its players.
#
# A thousand is far beyond real play. The longest game I could produce was two sides moving
# at random, which took about two thousand plies to gather six pieces; a game with anyone
# thinking is over in a small fraction of that. Reaching this is a draw, on the same
# reasoning as the double pass: neither side has demonstrated they can finish.
#
# This is a rule the desktop and terminal front ends do not have. It is a property of
# serving a game to strangers rather than of Royals, which is why it lives here and not in
# the engine -- golden_moves.txt is untouched by it.
MAX_PLIES = 1_000


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

    The page does now compute an answer of its own, in wasm, so that highlighting need not
    wait for a round trip -- but it holds a position and not a history, so what it produces
    is a superset of this: every legal move, plus any that the ko filter above strikes off.
    It is drawn immediately and then reconciled against this one, which can only ever take
    squares away. Deciding and answering are different jobs; this is the one that decides.
    """
    return [m for m in AI.listAllMoves(board, contr)
            if AI.performOneStep(board, contr, m) not in ko_boards]


def entering_options(board, contr, piece):
    """1-based squares this piece may be entered on."""
    return Engine.enteringOptions(board, contr, piece == Hasher.SPY)


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

# The one piece of attacker-controlled text in the whole application.
#
# Everything else a player sends is a square, a kind, a bounded number or a token that is
# either a known hash or nothing. A display name is free text that ends up on somebody
# else's screen and, more dangerously, inside a <meta> tag in a page rendered for whoever
# opens an invitation -- so it is filtered here, at the point it enters the system, rather
# than trusted to be escaped correctly at each of the several places it leaves.
#
# The charset is an allowlist because a denylist of dangerous characters is a list you
# discover you got wrong. Letters and digits of any script are welcome; the punctuation is
# what people actually put in a name.
NAME_MAX = 24
NAME_EXTRA = set(" '-._")


def clean_name(raw):
    """A display name, or None. Never raises -- an unusable name is simply absent."""
    if not isinstance(raw, str):
        return None
    # Unicode categories, so accents and non-Latin scripts survive while control
    # characters, bidirectional overrides and zero-width joiners do not.
    kept = [ch for ch in raw if ch.isalnum() or ch in NAME_EXTRA]
    name = " ".join("".join(kept).split())[:NAME_MAX].strip()
    return name or None


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
    name: str = None            # display name, already through clean_name


@dataclass
class Game:
    id: str
    mode: str                   # "ai" | "human"
    ai_depth: int
    entry_seed: int
    entry_noise: float

    # Nobody picks their squares: the twelve placements are drawn from the legal ones and the
    # game opens with the board already full. Stored rather than derived from the move list,
    # because a game between two people is created before either of them has entered anything
    # -- there is nothing in an empty move list to tell the two openings apart.
    random_entry: bool = False

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
    # "gather" | "resign" | "double_pass" | "no_moves" | "ply_limit"
    termination: str = None
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
             entry_seed=None, rng=None, name=None, random_entry=False):
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
        random_entry=bool(random_entry),
        board=Hasher.Entering_Board(),
        invite_hash=S.hash_token(invite_token) if invite_token else None,
        seats={
            side: Seat(kind=HUMAN, token_hash=S.hash_token(seat_token), claimed=True,
                       name=clean_name(name)),
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
        if game.random_entry:
            fill_entering(game)

    return game, seat_token, invite_token


class InviteError(Exception):
    """The invite is spent, wrong, or there is no seat left to claim."""


def claim_seat(game, invite_token, name=None):
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
    game.seats[side] = Seat(kind=HUMAN, token_hash=S.hash_token(seat_token), claimed=True,
                            name=clean_name(name))
    game.invite_hash = None

    if game.phase == "waiting":
        game.phase = "entering"
        _seek_entry_step(game)
        # A random opening is generated the moment the second player sits down, not when the
        # invitation was written: until somebody has claimed the seat there is no game to
        # deal a position to, and the same rule that keeps the first placement waiting keeps
        # this waiting too.
        if game.random_entry:
            fill_entering(game)
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


def fill_entering(game):
    """Place every remaining piece at random -- both sides -- and leave the game in play.

    The opening of a random game, in one go. Each square is drawn by `Engine.randomEntry`
    from the same list `place` validates against, so a placement made here is legal for
    exactly the reason a clicked one is, and it goes into `moves` as an ordinary entry token.

    **Never called from `replay`.** The load path re-applies those tokens through `place`,
    which is what makes a stored random game load back into the position it was played from
    rather than into a fresh deal. The RNG is a convenience for producing an opening, not the
    record of one: the record is the move list, as it is for every other game.
    """
    while game.phase == "entering":
        # _seek_entry_step only ever stops on a step that has somewhere to go, so this
        # cannot come back None.
        square = Engine.randomEntry(game.board, game.entry_side,
                                    game.entry_piece == Hasher.SPY,
                                    Engine.entryRng(game.entry_seed, game.enter_index))
        place(game, square, side=game.entry_side)

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
        if _out_of_plies(game):
            return game
        game.turn += 1
        return game

    if move not in legal:
        # Deliberately not explaining which rule it broke: a validator that narrates is a
        # validator that can be used to probe the position. A well-behaved client rarely
        # arrives here -- it asks what is legal first -- but it legitimately can, because the
        # copy of the engine in the page cannot see the ko history and will offer a repeat.
        # That is a move to refuse plainly, not to explain.
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

    if _out_of_plies(game):
        return game

    game.turn += 1
    return game


def _out_of_plies(game):
    """End the game as a draw if it has gone on long enough. True if it did.

    Checked after a move rather than before, so the cap is the length a game may reach
    and not the length past which a legal move is refused -- a player is never told their
    move is illegal when the real answer is that the game is over.
    """
    if len(game.moves) < MAX_PLIES:
        return False
    _finish(game, None, "ply_limit")
    return True


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
           version=0, created_at=None, updated_at=None, random_entry=False):
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
        random_entry=bool(random_entry),
        board=Hasher.Entering_Board(), seats=dict(seats), invite_hash=invite_hash,
    )

    # A game whose second seat was never claimed never started, and has no moves to
    # replay -- claim_seat is what walks it into the entering phase.
    #
    # `fill_entering` is deliberately not called here, random or not. The placements a random
    # game made are in `moves` like anybody else's, and _walk below puts them back through
    # `place`; dealing a fresh position on load would replace the game that was played with
    # one that merely could have been. The flag is carried so that a game which has not
    # opened yet still opens the way it was asked to.
    waiting = mode == "human" and not all(s.claimed for s in game.seats.values())
    if waiting:
        game.phase = "waiting"
    else:
        _seek_entry_step(game)

    _walk(game, stored)

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


def _walk(game, stored, on_ply=None):
    """Play a stored move list into `game`, one token at a time.

    Two callers want this loop: `replay`, which wants the game at the end of it, and
    `positions`, which wants the board after every ply. They get the same loop rather than
    one each, because a move list has exactly one subtlety in it -- what a "--" means
    depends on the phase, and during entering it is a step nobody made a decision at -- and
    a second walk is a second chance to get that wrong. Getting it wrong there does not
    fail; it shifts every later ply onto the other side and replays into a real position
    that is not the one anybody played to.

    `on_ply` is called after each token with the game as it then stands, including for a
    skipped placement, which changes nothing but is still a ply and must still be counted.
    """
    try:
        for token in stored:
            if game.phase == "entering":
                # A "--" during entering is a step some side had nowhere to make, and
                # _seek_entry_step has already written it down again. Only placements are
                # decisions anybody made.
                if token != N.PASS:
                    _piece, square = N.decode_entry(token)
                    place(game, square, side=game.entry_side)
            elif game.phase == "playing":
                play_move(game, None if token == N.PASS else N.decode_move(token),
                          side=game.turn % 2)
            else:
                raise ReplayError("%r comes after the game was already over" % (token,))
            if on_ply is not None:
                on_ply(game)
    except (IllegalMove, GameOver, N.NotationError) as exc:
        raise ReplayError("the stored move list is not playable: %s" % (exc,)) from exc


@dataclass
class Position:
    """One board a game stood in, and what had just moved to reach it.

    `phase` and `turn` are what a person calls the ply -- placement 3 of 12, or move 7 --
    as against `ply`, which is the index into the record and counts the placements as moves
    one to twelve. They come from `record.turn_of_ply` rather than from this game's own
    `turn` field, so the number a reviewer sees and the number the engine counts by are one
    rule and not two that happen to agree.
    """
    ply: int
    board: tuple
    last_move: list             # squares to highlight, 1-based
    phase: str                  # "entering" | "playing"
    turn: int                   # 1-based within the phase; 0 before anything


def positions(moves):
    """Walk a move list and return (every position it passed through, the game it made).

    `len(moves) + 1` positions: the board before any ply, then one after each.

    This is the whole of stepping through a game, and it needs nothing that live play
    needs. Replay applies moves that were already found legal when they were played, so
    nothing here has to decide whether a move *is* legal -- and it therefore touches no ko
    state at all. `place` and `play_move` do consult this game's own `ko_boards`, which
    they rebuild from scratch as they go; `Engine.koTrack` is not involved, here or
    anywhere else outside an AI worker. A review of one game cannot disturb another.

    The returned game is the one the moves alone describe, which is not quite the one that
    was played: a resignation leaves no trace in a move list, so a caller holding the
    stored game should report *its* ending rather than this one's.
    """
    stored = list(moves)

    game = Game(id="", mode="human", ai_depth=None, entry_seed=0, entry_noise=0.5,
                board=Hasher.Entering_Board(),
                seats={BLUE: Seat(kind=HUMAN, claimed=True),
                       RED: Seat(kind=HUMAN, claimed=True)})
    _seek_entry_step(game)

    spots = [Position(ply=0, board=game.board, last_move=[],
                      phase=R.PHASE_ENTERING, turn=0)]

    def note(g):
        phase, turn = R.turn_of_ply(len(spots) - 1)
        spots.append(Position(ply=len(spots), board=g.board, last_move=list(g.last_move),
                              phase=phase, turn=turn))

    _walk(game, stored, note)

    # The same self-check replay makes, and for the same reason: a record that replays to
    # a different move list than the one it claims to be is not a record of this game.
    if game.moves != stored:
        raise ReplayError("the move list does not replay to itself")

    return spots, game


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
    and the moment there are two, they disagree.

    That is still true, and it is why this shape survived the page gaining an engine. The
    wasm module is handed these same fields and does its own packing with the one function
    that knows how -- so the format has exactly two implementations, both compiled from the
    rules, and engine-rs/tests/wasm_parity.py is what holds them to each other. JavaScript
    has none, which is the point.
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
        "seats": {str(side): {"kind": seat.kind, "claimed": seat.claimed,
                              "name": seat.name}
                  for side, seat in game.seats.items()},
        "aiDepth": game.ai_depth,
        "randomEntry": game.random_entry,
        "sideToMove": game.side_to_move,
        "awaitingYou": awaiting_you,
        "moves": list(game.moves),
        "ply": len(game.moves),
        # How many plies the entering phase takes, which is how the page turns a ply index
        # into a move number. Sent rather than assumed: the client would otherwise carry a
        # hardcoded 12 that nothing holds to enteringSequence().
        "enterSteps": len(ENTER_STEPS),
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
