"""The HTTP layer. It decides who may ask; game.py decides what is legal.

The rule the whole design rests on: **the board never travels from the client to the
server.** A move request carries a kind, an origin, a destination and a prisoner flag --
four small values -- and the server loads the position from its own store and
independently regenerates every legal move before accepting one. AI.listAllMoves is the
same generator the computer plays by, so a person and the machine are held to literally
the same rules, and "is this legal?" is a tuple membership test rather than a second,
subtly different implementation of the rulebook.

The second rule, new with two players: **every endpoint that changes a game asks who is
asking.** Knowing a game id is not permission to move in it -- the id is in a URL people
paste into messages, and a game anyone holding the link can move in is not a game between
two people. `_require_player` turns a seat token into a side, and the side it returns is
the side the move is played as. Nothing takes a side from the request body, so there is
no way to phrase a request that moves for your opponent.
"""

import asyncio
import contextlib
import html
import logging
import os
import pathlib
from typing import Literal, Optional, Union

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from royals_engine import notation as N
from royals_engine import _accel

from royals_web import game as G
from royals_web import persist
from royals_web.ai_pool import pool, AIBusy, AITimeout
from royals_web.limits import BodyLimit
from royals_web.store import store

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"

# A move is four small fields. Anything bigger is not a move.
MAX_BODY_BYTES = 2_048

# A seat token is 22 characters. This bounds what we are willing to hash.
MAX_TOKEN_CHARS = 128

# Joining is cheap and legitimate a handful of times (a reload, a second device), so this
# is loose. Its job is not to stop token guessing -- 128 bits does that -- but to stop
# somebody using this endpoint as a free hashing service.
JOIN_LIMIT, JOIN_WINDOW = 60, 600

# A blanket ceiling on API requests per client. A page polls every two seconds and makes a
# handful of calls per move, so a player sits around 40 a minute and several tabs are still
# nowhere near this. It exists to stop a stranger with a game id from calling move
# generation as fast as the network allows.
API_LIMIT, API_WINDOW = 600, 60

# Reviewing is asked for once and then scrubbed through locally, so it wants a much tighter
# limit than the blanket one. It is by some way the most expensive thing this server will
# do for an unauthenticated caller: a full replay, and then board_to_json for *every* ply
# rather than the one the position is at. A hundred-ply game is a hundred times the work of
# the read that already sits behind the blanket limit, so the blanket limit is the wrong
# order of magnitude here rather than merely loose.
REVIEW_LIMIT, REVIEW_WINDOW = 30, 60

# The longest record this server will accept as an upload.
#
# It is not a rule about Royals -- MAX_PLIES lets a game reach a thousand -- it is
# arithmetic on MAX_BODY_BYTES, which is global and stays global (see limits.py). A ply is
# at most six characters and a separator, so this is what fits with room for the header:
#
#     2048 bytes / 7 bytes per ply  ~=  290 plies
#
# Real games run 60 to 150, so this refuses essentially nothing anybody has played. What
# matters is that a record between this and the raw byte limit is refused *here*, with a
# sentence saying how long it was and how long it may be, rather than by the middleware
# with a number of bytes the reader would have to divide by seven themselves.
REVIEW_MAX_PLIES = MAX_BODY_BYTES // 7

# The deepest search this server will agree to run, or None for no ceiling.
#
# The argument is unchanged and the arithmetic is not. A `royal` search is about three
# seconds of pinned CPU on this laptop and rather more on a small shared machine, and it is
# available to anybody who can click a menu. That is survivable while the only person who can
# reach the server is sitting at it, and stops being survivable the moment the address is
# public: the AI pool bounds how many searches run at once and how long each may take, but
# nothing else bounds how *expensive* the ones that do run are allowed to be.
#
# What moved is which depth is the expensive one. This used to say "a depth-6 search is about
# two seconds"; with the compiled engine depth 6 is under a tenth of a second and the ladder
# now runs to 9. Anyone reasoning about load from the old number would have been out by more
# than thirty times, in the direction that matters.
#
# Unset means no ceiling, so playing at home keeps every difficulty. A public deployment sets
# it, and the useful settings moved with the ladder: **ROYALS_MAX_DEPTH=8 drops `royal` and
# leaves everything else**, 7 drops `expert` too. A value of 5 or 6 now leaves only the two
# shallowest rungs rather than trimming one -- it is a much blunter instrument than it was.
def _max_depth():
    raw = os.environ.get("ROYALS_MAX_DEPTH", "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


MAX_DEPTH = _max_depth()


def allowed_difficulties():
    """The difficulties this server is willing to play at, in the order declared.

    One function, used both by the menu and by the validator, so what is offered and what
    is accepted cannot drift apart -- a client showing an option the server then refuses
    is a worse bug than the option simply not being there.
    """
    if MAX_DEPTH is None:
        return dict(G.DIFFICULTIES)
    kept = {name: depth for name, depth in G.DIFFICULTIES.items() if depth <= MAX_DEPTH}
    # Never offer nothing: a nonsensically low ceiling should still leave a playable game
    # rather than an empty menu and a server that refuses every request.
    if not kept:
        shallowest = min(G.DIFFICULTIES, key=G.DIFFICULTIES.get)
        kept = {shallowest: G.DIFFICULTIES[shallowest]}
    return kept


async def _sweep_forever():
    """Reclaim expired and over-ceiling games for as long as the server runs.

    Doing this once at startup was the same as not doing it: nothing expires for a week
    at the earliest, so only a server restarted more often than its own TTL ever collected
    anything. A single indexed DELETE on an hourly tick costs nothing and means the disk
    stops being a function of uptime.
    """
    while True:
        await asyncio.sleep(persist.SWEEP_INTERVAL)
        try:
            gone = store.sweep()
            if gone:
                logging.getLogger("royals").info("swept %d finished games", gone)
        except Exception:
            # A failed sweep is a disk that fills slower than it would have; it is not a
            # reason to take the games nobody asked about down with it.
            logging.getLogger("royals").exception("sweep failed")


@contextlib.asynccontextmanager
async def lifespan(app):
    # Which engine the AI workers will use, said once, out loud.
    #
    # The compiled and pure-Python engines play identically -- that is the whole design, and
    # CI enforces it -- so the only symptom of a missing wheel is that searches take some
    # thirty-six times longer. On a server that is indistinguishable from the box being busy,
    # and it is the kind of thing found by accident weeks later while investigating something
    # else. One line at startup turns it into a fact in the log.
    logging.getLogger("royals").info("%s | difficulties: %s", _accel.describe(),
                                     ", ".join("%s=%d" % kv for kv in G.DIFFICULTIES.items()))

    pool.start()
    db = persist.open_default()
    store.attach(db)
    store.sweep()
    sweeper = asyncio.create_task(_sweep_forever())
    try:
        yield
    finally:
        sweeper.cancel()
        pool.shutdown()
        # Detached before closing, so nothing can reach a closed connection through the
        # module-level store afterwards.
        store.attach(None)
        db.close()


app = FastAPI(title="Royals", lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# Body size is bounded below this, in ASGI middleware, and not here. An HTTP middleware
# receives a Request whose body has already been read, so a check at this level can only
# ever describe memory that is already spent -- and reading Content-Length, which is what
# used to happen here, checks nothing at all when a request declines to send one. See
# limits.BodyLimit.
app.add_middleware(BodyLimit, max_bytes=MAX_BODY_BYTES)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    api = request.url.path.startswith("/api/")

    # Everything else here is bounded per-endpoint -- games created, invitations claimed,
    # searches in flight -- and reads were bounded by nothing at all. A read without
    # `?since=` regenerates every legal move in the position, so it is the cheapest
    # request to make and among the more expensive to answer. The limit is loose enough
    # that a polling page (one request per two seconds) and several open tabs never
    # approach it.
    if api and not store.may_act("req:" + client_key(request), API_LIMIT, API_WINDOW):
        return JSONResponse({"detail": "too many requests -- slow down"}, status_code=429)

    response = await call_next(request)

    # This app serves no third-party JavaScript and never will, which makes a genuinely
    # strict policy possible -- no 'unsafe-inline', no CDN origins. That single header
    # neutralises most of what an XSS bug could otherwise do.
    #
    # 'wasm-unsafe-eval' is what lets the page compile static/royals.wasm, which is the
    # engine's own move generator and is what makes picking a piece up instant instead of a
    # request (see static/engine.js). Despite the name it is the NARROW token: it permits
    # WebAssembly compilation and nothing else. 'unsafe-eval' -- eval(), new Function(),
    # setTimeout on a string -- stays refused, which is the part that matters. Without it
    # Chrome blocks the module outright; the page then falls back to asking the server, so
    # this is what the feature needs rather than what it needs to not break.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
        "style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    if api:
        # `GET /api/games/{id}` answers *differently* depending on this header -- origins
        # and awaitingYou are the asking player's, not the game's. Without Vary, any cache
        # between here and a browser is entitled to hand one player the other's view of
        # the position. Nothing caches it today; the header is what makes that still true
        # the first time something is put in front of this.
        response.headers["Vary"] = "X-Royals-Seat"
        response.headers["Cache-Control"] = "no-store"
    return response


# Behind a proxy, request.client.host is the *proxy*. Every visitor would then share one
# rate-limit bucket -- thirty games per ten minutes for the entire internet, and 429 for
# everybody after that. It works perfectly in development and fails the moment it is
# deployed, which is the worst way for a bug to behave, so the forwarded address is read
# here. It is read *only* when the deployment says it is behind a proxy: an unconditional
# X-Forwarded-For is a header anyone can set, and trusting it turns a rate limit into a
# suggestion.
TRUSTED_PROXY = os.environ.get("ROYALS_TRUSTED_PROXY", "").strip().lower() in (
    "1", "true", "yes", "on")

# *Who* may speak for a client, not just whether anybody may.
#
# Trusting a forwarded header from whoever happens to send it is trusting the internet:
# with the server bound to a LAN address, any machine on the network could name itself
# 8.8.8.8 and get a fresh rate-limit bucket per request. A tunnel and a sidecar proxy both
# connect from loopback, which is the default here; a deployment where the proxy is a
# separate host sets ROYALS_PROXY_PEERS to its address.
#
# This is a code-level check rather than a warning about a flag combination because it
# cannot then be got wrong at the command line.
PROXY_PEERS = frozenset(
    p.strip() for p in os.environ.get("ROYALS_PROXY_PEERS", "127.0.0.1,::1").split(",")
    if p.strip())


# In order of how much the proxy in front of us is telling us. The first two are set by
# one specific edge and contain one address; X-Forwarded-For is the generic chain and the
# fallback. Cloudflare's is here because a tunnel is the first thing this will run behind:
# every request through one arrives from 127.0.0.1, so without reading a forwarded header
# the entire internet shares a single rate-limit bucket -- the same bug this function was
# written to prevent on Fly, arriving through a different door.
FORWARDED_HEADERS = ("fly-client-ip", "cf-connecting-ip", "x-forwarded-for")


def client_key(request: Request):
    peer = request.client.host if request.client else "unknown"
    if TRUSTED_PROXY and peer in PROXY_PEERS:
        for header in FORWARDED_HEADERS:
            value = request.headers.get(header)
            if value:
                # X-Forwarded-For is a chain; the client is the leftmost entry.
                return value.split(",")[0].strip()[:64]
    return peer


# ---------------------------------------------------------------------------
# One game at a time
# ---------------------------------------------------------------------------

class _LockRegistry:
    """A lock per game, alive only while somebody wants it.

    Two people sharing a game means two requests can genuinely arrive at once, and a move
    is read-modify-write across several awaits: validate, apply, then run the computer.
    Without this, two submissions can both pass their turn check before either commits.

    The lock deliberately does not live on the Game object. A game can be evicted from the
    store and rebuilt while a request is in flight, and two coroutines holding two
    different lock objects for the same game is a lock that is not locking anything. Keyed
    by id, refcounted, and dropped when the last waiter leaves so this cannot grow.
    """

    def __init__(self):
        self._entries = {}          # game_id -> [lock, waiters]

    @contextlib.asynccontextmanager
    async def hold(self, game_id):
        entry = self._entries.get(game_id)
        if entry is None:
            entry = self._entries[game_id] = [asyncio.Lock(), 0]
        # Counted before the await, so the entry cannot be collected out from under a
        # coroutine that is queued for it but not yet holding it.
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._entries.pop(game_id, None)

    def __len__(self):
        return len(self._entries)


locks = _LockRegistry()


# ---------------------------------------------------------------------------
# Schemas -- everything here is untrusted
# ---------------------------------------------------------------------------

class NewGame(BaseModel):
    mode: Literal["ai", "human"] = "ai"
    side: Union[int, Literal["random"]] = G.BLUE
    difficulty: str = Field(default=G.DEFAULT_DIFFICULTY, max_length=20)
    noise: float = Field(default=0.5, ge=0.0, le=1.0)
    # Nobody picks their squares; the twelve placements are dealt. Nothing to validate beyond
    # the coercion -- it changes who chooses, not what is legal, and every square it produces
    # goes through the same `place` a clicked one does.
    randomEntry: bool = False
    # The push-range variant. Like randomEntry there is nothing to validate beyond the
    # coercion: it changes what a push may do, and every push it produces still goes through
    # the same `legal_moves` an ordinary one does.
    pushRange: bool = False
    # Optional[str], not `str = None`. Pydantic treats the latter as a required string
    # that happens to have a default: leaving the field out is fine, but *sending* null
    # is a validation error. A browser form with an empty box sends null, so the shape
    # that looks optional was rejecting every request the client actually made.
    name: Optional[str] = Field(default=None, max_length=200)


class JoinIn(BaseModel):
    invite: str = Field(max_length=MAX_TOKEN_CHARS)
    name: Optional[str] = Field(default=None, max_length=200)


class Placement(BaseModel):
    square: str = Field(max_length=2)


class ReviewIn(BaseModel):
    # The text of a game record, comments and all -- exactly what the download button
    # produced. Bounded here as well as by the middleware so that an oversized field in an
    # otherwise small body is caught by the schema rather than by the replay.
    record: str = Field(max_length=MAX_BODY_BYTES)


class MoveIn(BaseModel):
    kind: str = Field(max_length=8)
    # Optional for the same reason as above: a move carries a target or a direction, not
    # both, and a client that spells the absent one out as null is not making an error.
    origin: Optional[str] = Field(default=None, max_length=2)
    target: Optional[str] = Field(default=None, max_length=2)
    dir: Optional[str] = Field(default=None, max_length=1)
    pris: bool = False
    # How far a push travels, under the push-range rule. **Declared with no constraints on
    # purpose**: notation.move_from_json already validates it hostilely -- push-only, an int
    # and not a bool, 2 to 6, absent meaning one square -- and one rule in one place is the
    # whole point of that function. A second, looser copy here would be the one that drifted.
    #
    # It has to exist, though. Without the field pydantic drops it before move_from_json ever
    # sees it, and every ranged push is played as a one-square push: legal, accepted, and the
    # wrong move.
    travel: Optional[int] = None
    # Optional, and only ever a safety net. A client that retries a move it already
    # landed -- a flaky phone connection is enough -- would otherwise play twice if the
    # position happens to make the same move legal again.
    expectedVersion: Optional[int] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch(game_id):
    game = store.get(game_id)
    if game is None:
        # Same answer for "never existed" and "expired". Game ids are 128-bit random, so
        # there is nothing to enumerate, but there is also no reason to confirm anything.
        raise HTTPException(404, "no such game")
    return game


def _require_player(game_id, seat_token):
    """(game, side) for the holder of this token, or 403.

    This is the whole of authorization, and every endpoint that changes a game begins
    with it. The side it returns is the side the action is taken as -- there is no code
    path that takes a side from the request body, so "move for my opponent" is not a
    request that can be phrased, rather than one that is checked for and refused.
    """
    game = _fetch(game_id)
    side = game.seat_of(seat_token)
    if side is None:
        raise HTTPException(403, "you are not a player in this game")
    return game, side


def _viewer_side(game, seat_token):
    """The asker's side, or None. Never raises: anyone with the link may watch."""
    return game.seat_of(seat_token)


def SeatHeader():
    """The seat token header.

    A header rather than a cookie, and that is a design decision rather than a taste one:
    a browser attaches cookies to cross-site requests on its own, which is what makes
    CSRF possible and CSRF tokens necessary. It will not attach this. A cross-origin page
    cannot set a custom header without a preflight, and we grant no CORS, so a
    state-changing request forged from another site cannot carry a seat token at all.
    """
    return Header(default=None, alias="X-Royals-Seat", max_length=MAX_TOKEN_CHARS)


async def _advance(game):
    """Run the computer's turns until the game needs a person again, or ends.

    The AI's reply is computed inside the request that provoked it and returned in the
    same response. At the measured depths that is a normal request, and it removes a
    whole category of "where did the computer's move go" bugs.
    """
    guard = 0
    # `ai_to_move`, not "not the human's turn": in a game between two people this is
    # false from the first placement onwards, and _advance correctly does nothing at all.
    while not game.finished and game.ai_to_move:
        guard += 1
        if guard > 64:      # entering is 12 placements; play alternates. Never legitimate.
            raise HTTPException(500, "the game failed to make progress")

        if game.phase == "entering":
            square = await pool.choose_entry(
                game.board, game.entry_side, game.entry_piece,
                game.entry_piece == G.Hasher.SPY, game.entry_seed, game.entry_noise,
                game.enter_index)
            if square is None:
                # _seek_entry_step already established there is somewhere to go.
                raise HTTPException(500, "the computer failed to place a piece")
            # choose_entry is asked about the OWNER, because that is whose piece it is and
            # whose legality applies; the seat entitled to place it is the other side. Both
            # arguments are game.entry_side-derived and they are not the same value.
            G.place(game, square, side=G.Engine.enteringChooser(game.entry_side))
            continue

        contr = game.turn % 2
        # The one call in the web app that hands legal_moves a board rather than a game, so
        # the one that has to name the rule set itself. Everything else in game.py already
        # holds the Game and reads game.push_range from it.
        legal = G.legal_moves(game.board, contr, game.ko_set(), push_range=game.push_range)
        if not legal:
            G.play_move(game, None, side=contr)
            continue

        _, move, _, _, _ = await pool.take_turn(
            game.board, contr, game.ai_depth, game.ko_boards, game.push_range)
        # takeTurn answers None when every move it has breaks ko; that is a pass.
        G.play_move(game, move, side=contr)

    return game


async def _persist_then_advance(game):
    """Write the move down, *then* let the computer answer it.

    **The order is the whole point, and getting it wrong cost the player their move.** Every
    endpoint that changes a game used to play the move, hand the position to `_advance`, and
    persist afterwards. `G.play_move` mutates the game object, and that object *is* the one in
    the cache -- so on the ordinary path nobody noticed. On the path where the AI pool is full
    (`AIBusy`, a 503) or the search overran (`AITimeout`, 504), the `store.put` was never
    reached: the move was live in memory and absent from the row. Whether it survived depended
    on which of the two some later reader happened to consult, and a retry met a version that
    had already moved.

    A person's move is a decision they made and is durable the moment it is legal. The
    computer's reply is durable when it exists. `create_game` has always done it this way --
    put, advance, put -- and these are the three endpoints that did not.

    The second write is in a `finally` rather than after the await, and that is the case worth
    naming: `_advance` can make *several* moves before it fails, since entering is a dozen
    placements. Persisting only on success would leave the cache holding four placements the
    row had never heard of, which is the same split-brain one step further in. What the row
    owes the cache is whatever the cache actually holds.
    """
    store.put(game)
    try:
        await _advance(game)
    finally:
        store.put(game)
    return game


def _state(game, side):
    return G.to_json(game, side)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.exception_handler(G.IllegalMove)
async def _illegal(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(G.GameOver)
async def _over(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(N.NotationError)
async def _notation(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(G.InviteError)
async def _invite(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(G.ReplayError)
async def _unplayable(request, exc):
    # A record that is well-formed notation but does not describe a game anybody could have
    # played. Only reachable from the review endpoint -- a stored game that failed to replay
    # would have failed on load, long before a request saw it.
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(AIBusy)
async def _busy(request, exc):
    return JSONResponse({"detail": "the computer is thinking about too many games at "
                                   "once -- try again in a moment"}, status_code=503)


@app.exception_handler(AITimeout)
async def _slow(request, exc):
    return JSONResponse({"detail": "the computer took too long and gave up"},
                        status_code=504)


@app.get("/api/difficulties")
async def difficulties():
    offered = allowed_difficulties()
    return {"difficulties": [{"name": name, "depth": depth}
                             for name, depth in offered.items()],
            "default": G.DEFAULT_DIFFICULTY if G.DEFAULT_DIFFICULTY in offered
                       else max(offered, key=offered.get)}


@app.post("/api/games")
async def create_game(body: NewGame, request: Request):
    if body.difficulty not in allowed_difficulties():
        # Same answer for "no such difficulty" and "deeper than this server offers".
        # A client should be reading /api/difficulties, not guessing.
        raise HTTPException(422, "unknown difficulty")
    if body.side not in (G.BLUE, G.RED, "random"):
        raise HTTPException(422, "side must be 0, 1 or 'random'")
    if not store.may_create(client_key(request)):
        raise HTTPException(429, "too many games started -- slow down")

    game, seat_token, invite_token = G.new_game(
        mode=body.mode, side=body.side, difficulty=body.difficulty,
        entry_noise=body.noise, name=body.name, random_entry=body.randomEntry,
        push_range=body.pushRange)
    store.put(game)
    await _advance(game)
    store.put(game)     # again after advancing: the store is write-through from M3.2 on

    # The only time either token is ever sent. The game keeps their hashes; there is no
    # endpoint that can hand them out again -- which is why the creating browser holds on
    # to the invitation itself rather than expecting to ask for it later.
    return {"state": _state(game, game.seat_of(seat_token)),
            "seatToken": seat_token,
            "inviteToken": invite_token}


@app.post("/api/games/{game_id}/join")
async def join_game(game_id: str, body: JoinIn, request: Request,
                    x_royals_seat: str = SeatHeader()):
    if not store.may_act("join:" + client_key(request), JOIN_LIMIT, JOIN_WINDOW):
        raise HTTPException(429, "too many attempts -- slow down")

    async with locks.hold(game_id):
        game = _fetch(game_id)

        # Idempotent for a browser that already sits here. The creator clicking their own
        # invite link -- out of curiosity, from history, or to check it works -- is a
        # thing that will happen, and it must give them their own seat back rather than
        # spending the invite on them and locking their opponent out.
        already = _viewer_side(game, x_royals_seat)
        if already is not None:
            return {"state": _state(game, already), "seatToken": None}

        side, seat_token = G.claim_seat(game, body.invite, name=body.name)
        store.put(game)
        return {"state": _state(game, side), "seatToken": seat_token}


@app.get("/api/games/{game_id}")
async def read_game(game_id: str, since: int = None, x_royals_seat: str = SeatHeader()):
    game = _fetch(game_id)

    # This is the poll, and it runs every couple of seconds per open page, so what it
    # costs when nothing has happened is the server's entire idle load. Answering before
    # to_json matters: to_json regenerates every legal move in the position.
    if since is not None and since == game.version:
        return {"id": game.id, "version": game.version, "unchanged": True}

    return _state(game, _viewer_side(game, x_royals_seat))


@app.post("/api/games/{game_id}/enter")
async def enter_piece(game_id: str, body: Placement, x_royals_seat: str = SeatHeader()):
    async with locks.hold(game_id):
        game, side = _require_player(game_id, x_royals_seat)
        if game.phase != "entering":
            raise HTTPException(409, "the entering phase is over")
        if not game.awaits(side):
            raise HTTPException(409, "it is not your turn")

        G.place(game, N.alg_to_square(body.square), side=side)
        await _persist_then_advance(game)
        return _state(game, side)


@app.get("/api/games/{game_id}/moves")
async def moves_from(game_id: str, origin: str, pris: bool = False,
                     x_royals_seat: str = SeatHeader()):
    """What the player may do from one square. For highlighting only -- the server
    re-derives this on submission and does not trust that the client asked first."""
    game, side = _require_player(game_id, x_royals_seat)
    if not game.awaits(side):
        return {"origin": origin, "moves": []}

    square = N.alg_to_square(origin)
    moves = G.moves_from(game, square, moving_pris=pris)
    return {
        "origin": origin,
        "pris": pris,
        # Offered separately so the client can show that dragging prisoners is an option
        # from this square without having to work the rule out for itself.
        "hasPrisonerVariant": bool(G.moves_from(game, square, moving_pris=not pris)),
        "moves": [dict(N.move_to_json(m), ran=N.encode_move(m)) for m in moves],
    }


@app.post("/api/games/{game_id}/move")
async def submit_move(game_id: str, body: MoveIn, x_royals_seat: str = SeatHeader()):
    async with locks.hold(game_id):
        game, side = _require_player(game_id, x_royals_seat)
        if game.phase != "playing":
            raise HTTPException(409, "the game is not in play")
        if body.expectedVersion is not None and body.expectedVersion != game.version:
            raise HTTPException(409, "the game has moved on -- reload before playing")
        if not game.awaits(side):
            raise HTTPException(409, "it is not your turn")

        # move_from_json raises NotationError on anything malformed; play_move then
        # decides whether a well-formed move is a legal one.
        move = N.move_from_json(body.model_dump(exclude_none=True,
                                                exclude={"expectedVersion"}))
        G.play_move(game, move, side=side)
        await _persist_then_advance(game)
        return _state(game, side)


@app.post("/api/games/{game_id}/pass")
async def pass_turn(game_id: str, x_royals_seat: str = SeatHeader()):
    async with locks.hold(game_id):
        game, side = _require_player(game_id, x_royals_seat)
        if not game.awaits(side):
            raise HTTPException(409, "it is not your turn")
        G.play_move(game, None, side=side)
        await _persist_then_advance(game)
        return _state(game, side)


@app.post("/api/games/{game_id}/resign")
async def resign_game(game_id: str, x_royals_seat: str = SeatHeader()):
    async with locks.hold(game_id):
        game, side = _require_player(game_id, x_royals_seat)
        G.resign(game, side)
        store.put(game)
        return _state(game, side)


# ---------------------------------------------------------------------------
# Reviewing a game, move by move
# ---------------------------------------------------------------------------
# Every position at once, in one request, rather than a request per ply. Stepping is then
# instant and entirely local -- a scrubber dragged across a game makes no requests at all --
# and the server does the replay once instead of once per step. A hundred-ply game is a few
# hundred kilobytes, which is a cost worth paying once and not worth paying a hundred times.
#
# It is the same board_to_json the live game sends, so board.js draws a reviewed position
# with the renderer it already has and does not learn a second board format.

def _review_json(moves, spots, game, start_side=None):
    """`game` is whoever knows how it ended -- see the note in G.positions.

    `start_side` is set only for a record that began from a position set up by hand, which
    has no entering phase; `enterSteps` goes to 0 with it. The page needs both: the first
    for whose ply is whose, the second for where the opening ends. It cannot work either out
    on its own, and the two together are what let it colour and number a record it did not
    play.
    """
    entering = start_side is None
    return {
        "ply": len(moves),
        "moves": list(moves),
        "result": game.result,
        "termination": game.termination,
        # Which optional rules this game was played under, so the page can say so. Taken
        # from the game rather than passed in separately because both callers already hand
        # one over -- the stored game, or the throwaway `positions` walked -- and the walked
        # one carries the rule set it was replayed with. One source, and it cannot disagree
        # with the boards above it.
        "rules": [N.RULE_PUSH_RANGE] if game.push_range else [],
        "enterSteps": len(G.ENTER_STEPS) if entering else 0,
        # Which side made the first ply of *play*. Red for an ordinary game -- whoever
        # entered second opens -- and whatever the position said otherwise.
        "firstSide": 1 if entering else start_side,
        # `turn` is what a reviewer is shown -- placement 3 of 12, or move 7 -- and `ply`
        # is where that sits in the record. They differ by the whole entering phase, which
        # is exactly why both are sent rather than the client deriving one from the other.
        "positions": [{"board": G.board_to_json(spot.board),
                       "lastMove": [N.square_to_alg(s) for s in spot.last_move],
                       "phase": spot.phase,
                       "turn": spot.turn}
                      for spot in spots],
    }


@app.get("/api/games/{game_id}/positions")
async def game_positions(game_id: str, request: Request):
    """Every position a stored game has stood in.

    Public, like reading the game itself: Royals is perfect-information and anybody with
    the link can already watch. What it is not is cheap, hence its own rate limit.
    """
    if not store.may_act("review:" + client_key(request), REVIEW_LIMIT, REVIEW_WINDOW):
        raise HTTPException(429, "too many review requests -- slow down")

    game = _fetch(game_id)
    spots, _walked = G.positions(game.moves)
    # The stored game, not the walked one: a resignation is not a move and the move list
    # cannot describe it.
    return _review_json(game.moves, spots, game)


@app.post("/api/review")
async def review_record(body: ReviewIn, request: Request):
    """Replay a record somebody uploaded. Stateless -- nothing is stored, nothing is created.

    This is the other half of the download button: a file that leaves here can come back
    and be walked through. It creates no game, so it needs no seat and no rate limit on
    creation; what it needs is the same ceiling on cost as the endpoint above.
    """
    if not store.may_act("review:" + client_key(request), REVIEW_LIMIT, REVIEW_WINDOW):
        raise HTTPException(429, "too many review requests -- slow down")

    # decode_record rather than decode_game, because a record may begin from a position
    # somebody set up. The board comes off the wire from a stranger and is bounded by
    # notation.validate_board on the way through -- 49 squares, codes in range, no side
    # holding more pieces than it owns -- which is the same ceiling a position file has
    # always been read under.
    # The fourth value is the optional rules the game was played under, and it is passed
    # through to the walk rather than merely accepted. Replaying a push-range game with the
    # variant off clamps every ranged push to one square, and what comes back is a plausible
    # board and a different game -- which is why this used to refuse such records outright.
    # decode_record has already rejected any rule name the engine does not know.
    moves, board, turn, rules = N.decode_record(body.record)  # NotationError -> 422, naming the ply
    if len(moves) > REVIEW_MAX_PLIES:
        raise HTTPException(
            413, "that record is %d moves long and this server will review up to %d -- "
                 "long enough that it no longer fits in one request"
                 % (len(moves), REVIEW_MAX_PLIES))

    spots, walked = G.positions(moves, board, turn, rules)     # ReplayError -> 422
    return _review_json(moves, spots, walked, start_side=turn)


@app.get("/api/health")
async def health():
    return {"ok": True, "games": len(store), "aiInflight": pool.inflight}


# ---------------------------------------------------------------------------
# The page itself
# ---------------------------------------------------------------------------

# Where the preview tags go. A placeholder and a string replace rather than a template
# engine: the whole substitution is one escaped block in one place, and web/pyproject.toml
# makes a point of how short its dependency list is.
SOCIAL_PLACEHOLDER = "<!--SOCIAL-->"
SHELL = None


def _shell():
    """The page source, read once and kept.

    Read at first use rather than at import so a `--reload` run picks up an edit, and kept
    afterwards because the alternative is a disk read on every page view for a file that
    changes when the server restarts.
    """
    global SHELL
    if SHELL is None or os.environ.get("ROYALS_RELOAD_SHELL"):
        SHELL = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return SHELL


# The origin this server is reached at, for the absolute URLs a preview card needs.
# Open Graph requires them: a relative og:image is ignored by most scrapers.
#
# Falling back to the request's own host means trusting the Host header, which is normally
# worth avoiding -- but the worst an attacker achieves by forging it is a wrong picture in
# a preview of a link they sent themselves. Set PUBLIC_ORIGIN on a real deployment and the
# question does not arise.
PUBLIC_ORIGIN = os.environ.get("ROYALS_PUBLIC_ORIGIN", "").strip().rstrip("/")


def _origin(request: Request):
    return PUBLIC_ORIGIN or str(request.base_url).rstrip("/")


def _social(request: Request, title, description, url):
    """The preview card, as escaped meta tags.

    `description` can contain a player's chosen name, which is the only text in this
    application that someone else wrote. It is filtered on the way in by `clean_name` and
    escaped again here, because the cost of doing both is nothing and the cost of
    discovering that one of them was insufficient is an injected tag in a page served to
    whoever opened an invitation.
    """
    origin = _origin(request)
    image = origin + "/static/icon-512.png"
    tags = [
        ("og:type", "website"),
        ("og:site_name", "Royals"),
        ("og:title", title),
        ("og:description", description),
        ("og:url", url),
        ("og:image", image),
        ("twitter:card", "summary_large_image"),
        ("twitter:title", title),
        ("twitter:description", description),
        ("twitter:image", image),
    ]
    out = ['<meta name="description" content="%s">' % html.escape(description, quote=True)]
    for name, content in tags:
        key = "property" if name.startswith("og:") else "name"
        out.append('<meta %s="%s" content="%s">'
                   % (key, name, html.escape(content, quote=True)))
    return "\n".join(out)


TAGLINE = ("Royals is a two-player strategy game on a board that wraps around. "
           "Gather your spy, four pawns and royal onto one square to win.")


def _page(request: Request, social=None):
    # no-store on the shell, because the shell is how a deploy reaches anybody. The
    # bundle it loads is fingerprinted by nothing at all, so a browser that heuristically
    # caches this page can pin a player to an old client indefinitely, and there is no
    # mechanism to tell them otherwise. It also keeps one game's preview card from being
    # served for another's.
    body = _shell().replace(
        SOCIAL_PLACEHOLDER,
        social or _social(request, "Royals", TAGLINE, _origin(request) + "/"))
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


@app.get("/")
async def index(request: Request):
    return _page(request)


@app.get("/g/{game_id}")
async def game_page(game_id: str, request: Request):
    return _page(request)


@app.get("/join/{invite_token}")
async def join_page(invite_token: str, request: Request):
    """Serve the invitation page, and describe the game for a preview card.

    **This must never claim the seat.** iMessage, WhatsApp, Slack and every other
    messenger fetches a URL to build a preview before a human has seen it, let alone
    clicked it. A GET that claimed the seat would hand the game to a preview bot and greet
    the person the link was sent to with "that invitation has already been used" -- over
    exactly the channels this feature exists to be used on. The claim is a POST the page
    makes when the reader presses a button, which no previewer will issue.

    The path segment is a game id, optionally followed by ".<token>" in the older link
    format. The id is enough to describe the game and is not a credential -- anyone with
    one can already watch -- while the token, which is, now travels in the fragment and
    never arrives here at all.
    """
    game_id = invite_token.split(".", 1)[0]
    game = store.get(game_id) if _looks_like_id(game_id) else None

    if game is None or game.mode != "human":
        title, description = "Royals", TAGLINE
    else:
        inviter = game.seats[G.BLUE].name or game.seats[G.RED].name
        title = ("%s has invited you to play Royals" % inviter if inviter
                 else "You have been invited to play Royals")
        description = (TAGLINE + " No account needed — open the link and play."
                       if game.invite_hash
                       else "This invitation has already been used.")

    url = "%s/join/%s" % (_origin(request), game_id)
    return _page(request, social=_social(request, title, description, url))


def _looks_like_id(value):
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
