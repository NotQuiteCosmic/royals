"""The HTTP layer. It decides who may ask; game.py decides what is legal.

The rule the whole design rests on: **the board never travels from the client to the
server.** A move request carries a kind, an origin, a destination and a prisoner flag --
four small values -- and the server loads the position from its own store and
independently regenerates every legal move before accepting one. AI.listAllMoves is the
same generator the computer plays by, so a person and the machine are held to literally
the same rules, and "is this legal?" is a tuple membership test rather than a second,
subtly different implementation of the rulebook.
"""

import asyncio
import contextlib
import pathlib

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from royals_engine import notation as N

from royals_web import game as G
from royals_web.ai_pool import pool, AIBusy, AITimeout
from royals_web.store import store

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"

# A move is four small fields. Anything bigger is not a move.
MAX_BODY_BYTES = 2_048


@contextlib.asynccontextmanager
async def lifespan(app):
    pool.start()
    try:
        yield
    finally:
        pool.shutdown()


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
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def client_key(request: Request):
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Schemas -- everything here is untrusted
# ---------------------------------------------------------------------------

class NewGame(BaseModel):
    side: int = Field(default=G.BLUE, ge=0, le=1)
    difficulty: str = Field(default=G.DEFAULT_DIFFICULTY, max_length=20)
    noise: float = Field(default=0.5, ge=0.0, le=1.0)


class Placement(BaseModel):
    square: str = Field(max_length=2)


class MoveIn(BaseModel):
    kind: str = Field(max_length=8)
    origin: str = Field(default=None, max_length=2)
    target: str = Field(default=None, max_length=2)
    dir: str = Field(default=None, max_length=1)
    pris: bool = False


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


async def _advance(game):
    """Run the computer's turns until the game needs a person again, or ends.

    The AI's reply is computed inside the request that provoked it and returned in the
    same response. At the measured depths that is a normal request, and it removes a
    whole category of "where did the computer's move go" bugs.
    """
    guard = 0
    while not game.finished and not game.awaiting_human:
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


def _state(game):
    return G.to_json(game)


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
    return {"difficulties": [{"name": name, "depth": depth}
                             for name, depth in G.DIFFICULTIES.items()],
            "default": G.DEFAULT_DIFFICULTY}


@app.post("/api/games")
async def create_game(body: NewGame, request: Request):
    if body.difficulty not in G.DIFFICULTIES:
        raise HTTPException(422, "unknown difficulty")
    if not store.may_create(client_key(request)):
        raise HTTPException(429, "too many games started -- slow down")

    game = G.new_game(human_side=body.side, difficulty=body.difficulty,
                      entry_noise=body.noise)
    store.put(game)
    await _advance(game)
    return _state(game)


@app.get("/api/games/{game_id}")
async def read_game(game_id: str):
    return _state(_fetch(game_id))


@app.post("/api/games/{game_id}/enter")
async def enter_piece(game_id: str, body: Placement):
    game = _fetch(game_id)
    if game.phase != "entering":
        raise HTTPException(409, "the entering phase is over")
    if not game.awaiting_human:
        raise HTTPException(409, "it is not your turn")

    G.place(game, N.alg_to_square(body.square), side=game.human_side)
    await _advance(game)
    return _state(game)


@app.get("/api/games/{game_id}/moves")
async def moves_from(game_id: str, origin: str, pris: bool = False):
    """What the player may do from one square. For highlighting only -- the server
    re-derives this on submission and does not trust that the client asked first."""
    game = _fetch(game_id)
    if not game.awaiting_human:
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
async def submit_move(game_id: str, body: MoveIn):
    game = _fetch(game_id)
    if game.phase != "playing":
        raise HTTPException(409, "the game is not in play")
    if not game.awaiting_human:
        raise HTTPException(409, "it is not your turn")

    # move_from_json raises NotationError on anything malformed; play_move then decides
    # whether a well-formed move is a legal one.
    move = N.move_from_json(body.model_dump(exclude_none=True))
    G.play_move(game, move, side=game.human_side)
    await _advance(game)
    return _state(game)


@app.post("/api/games/{game_id}/pass")
async def pass_turn(game_id: str):
    game = _fetch(game_id)
    if not game.awaiting_human:
        raise HTTPException(409, "it is not your turn")
    G.play_move(game, None, side=game.human_side)
    await _advance(game)
    return _state(game)


@app.post("/api/games/{game_id}/resign")
async def resign_game(game_id: str):
    game = _fetch(game_id)
    G.resign(game, game.human_side)
    return _state(game)


@app.get("/api/health")
async def health():
    return {"ok": True, "games": len(store), "aiInflight": pool.inflight}


# ---------------------------------------------------------------------------
# The page itself
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
