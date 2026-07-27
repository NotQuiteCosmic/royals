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
import os
import pathlib
from typing import Literal, Union

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from royals_engine import notation as N

from royals_web import game as G
from royals_web import persist
from royals_web.ai_pool import pool, AIBusy, AITimeout
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

# The deepest search this server will agree to run, or None for no ceiling.
#
# A depth-6 search is about two seconds of pinned CPU on this laptop and rather more on a
# small shared machine, and it is available to anybody who can click a menu. That is
# survivable while the only person who can reach the server is sitting at it, and stops
# being survivable the moment the address is public: the AI pool bounds how many searches
# run at once and how long each may take, but nothing else bounds how *expensive* the ones
# that do run are allowed to be.
#
# Unset means no ceiling, so playing at home keeps every difficulty. A public deployment
# sets it -- ROYALS_MAX_DEPTH=5 drops `royal` and leaves everything else.
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


@contextlib.asynccontextmanager
async def lifespan(app):
    pool.start()
    db = persist.open_default()
    store.attach(db)
    # Deleting long-dead games is not urgent enough to want a scheduler for. Once at
    # startup is enough on a server that gets deployed more often than a month.
    store.sweep()
    try:
        yield
    finally:
        pool.shutdown()
        # Detached before closing, so nothing can reach a closed connection through the
        # module-level store afterwards.
        store.attach(None)
        db.close()


app = FastAPI(title="Royals", lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Reject oversized bodies before parsing rather than after.
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "request body too large"}, status_code=413)

    response = await call_next(request)

    # This app serves no third-party JavaScript and never will, which makes a genuinely
    # strict policy possible -- no 'unsafe-inline', no CDN origins. That single header
    # neutralises most of what an XSS bug could otherwise do.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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


# In order of how much the proxy in front of us is telling us. The first two are set by
# one specific edge and contain one address; X-Forwarded-For is the generic chain and the
# fallback. Cloudflare's is here because a tunnel is the first thing this will run behind:
# every request through one arrives from 127.0.0.1, so without reading a forwarded header
# the entire internet shares a single rate-limit bucket -- the same bug this function was
# written to prevent on Fly, arriving through a different door.
FORWARDED_HEADERS = ("fly-client-ip", "cf-connecting-ip", "x-forwarded-for")


def client_key(request: Request):
    if TRUSTED_PROXY:
        for header in FORWARDED_HEADERS:
            value = request.headers.get(header)
            if value:
                # X-Forwarded-For is a chain; the client is the leftmost entry.
                return value.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


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


class JoinIn(BaseModel):
    invite: str = Field(max_length=MAX_TOKEN_CHARS)


class Placement(BaseModel):
    square: str = Field(max_length=2)


class MoveIn(BaseModel):
    kind: str = Field(max_length=8)
    origin: str = Field(default=None, max_length=2)
    target: str = Field(default=None, max_length=2)
    dir: str = Field(default=None, max_length=1)
    pris: bool = False
    # Optional, and only ever a safety net. A client that retries a move it already
    # landed -- a flaky phone connection is enough -- would otherwise play twice if the
    # position happens to make the same move legal again.
    expectedVersion: int = None


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
                game.entry_piece == G.Hasher.SPY, game.entry_seed, game.entry_noise)
            if square is None:
                # _seek_entry_step already established there is somewhere to go.
                raise HTTPException(500, "the computer failed to place a piece")
            G.place(game, square, side=game.entry_side)
            continue

        contr = game.turn % 2
        legal = G.legal_moves(game.board, contr, game.ko_set())
        if not legal:
            G.play_move(game, None, side=contr)
            continue

        _, move, _, _, _ = await pool.take_turn(
            game.board, contr, game.ai_depth, game.ko_boards)
        # takeTurn answers None when every move it has breaks ko; that is a pass.
        G.play_move(game, move, side=contr)

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
        entry_noise=body.noise)
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

        side, seat_token = G.claim_seat(game, body.invite)
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
        await _advance(game)
        store.put(game)
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
        await _advance(game)
        store.put(game)
        return _state(game, side)


@app.post("/api/games/{game_id}/pass")
async def pass_turn(game_id: str, x_royals_seat: str = SeatHeader()):
    async with locks.hold(game_id):
        game, side = _require_player(game_id, x_royals_seat)
        if not game.awaits(side):
            raise HTTPException(409, "it is not your turn")
        G.play_move(game, None, side=side)
        await _advance(game)
        store.put(game)
        return _state(game, side)


@app.post("/api/games/{game_id}/resign")
async def resign_game(game_id: str, x_royals_seat: str = SeatHeader()):
    async with locks.hold(game_id):
        game, side = _require_player(game_id, x_royals_seat)
        G.resign(game, side)
        store.put(game)
        return _state(game, side)


@app.get("/api/health")
async def health():
    return {"ok": True, "games": len(store), "aiInflight": pool.inflight}


# ---------------------------------------------------------------------------
# The page itself
# ---------------------------------------------------------------------------

def _page():
    # no-store on the shell, because the shell is how a deploy reaches anybody. The
    # bundle it loads is fingerprinted by nothing at all, so a browser that heuristically
    # caches this page can pin a player to an old client indefinitely, and there is no
    # mechanism to tell them otherwise.
    return FileResponse(STATIC_DIR / "index.html",
                        headers={"Cache-Control": "no-store"})


@app.get("/")
async def index():
    return _page()


@app.get("/g/{game_id}")
async def game_page(game_id: str):
    return _page()


@app.get("/join/{invite_token}")
async def join_page(invite_token: str):
    """Serving the page is all this does. **It must never claim the seat.**

    iMessage, WhatsApp, Slack and every other messenger fetches a URL to build a link
    preview before a human has seen it, let alone clicked it. A GET that claimed the seat
    would hand the game to a preview bot and greet the person the link was sent to with
    "that invitation has already been used" -- and it would do it over exactly the
    channels this feature exists to be used on. The claim is the POST the page then
    makes, which no previewer will issue.
    """
    return _page()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
