"""Running the search without letting it hurt the server.

Two problems, one answer.

**Correctness.** Engine.koTrack, Engine.koGeneration and the AI's transposition table,
killer and history tables are module-level globals. Two games sharing a process share
those, so game B's positions land in game A's ko set and the ko rule starts rejecting
moves that are perfectly legal. Every job here therefore begins by loading the state of
exactly one game and ends by dropping it, and workers only ever handle one job at a time.
This is the same thing regress.py does between sweeps with koReset()/koRecord().

**Denial of service.** A depth-6 search is seconds of pinned CPU in the pure-Python engine,
and a fraction of a second through the compiled one -- and anybody can ask for it by clicking
a menu. The caps below are sized for the slow case on purpose: a deployment without the wheel
is a supported one, and it is the one where a handful of concurrent requests saturates a small
machine. Nothing about any individual request looks abusive either way. So: a hard cap on how many
searches run at once, a queue that refuses rather than grows, and a wall-clock deadline
on every search. A separate process pool also means a crash or a runaway on a malformed
position takes down a worker rather than the web server.

The engine's globals are touched in this file and nowhere else in the web app. Move
validation in game.py is pure and needs none of them.
"""

import asyncio
import os
import time
from concurrent.futures import ProcessPoolExecutor

from royals_engine import engine as Engine
from royals_engine import ai as AI


# A single game's transposition table is small -- a full depth-6 search fills about
# 22,000 entries -- but the shipped ceiling of 300,000 buys nothing here, because no
# single game ever approaches it, and it costs real memory on a small box.
#
# Measured, twelve turns at depth 6, peak RSS of one worker:
#
#     compiled engine, limit  50,000     54 MB
#     compiled engine, limit 300,000     79 MB
#
# So 25 MB per worker, 50 MB across the default two. The pure-Python engine is far
# hungrier again -- the figure this comment used to quote was 175 MB per generation,
# with tableOld meaning two are live at once -- which is what makes the cap worth
# having under either implementation.
#
# **Two things to know before raising the depth this server offers.**
#
# The limit is checked once per chooseMove, not per insertion, so it bounds the table
# *between* moves and not *during* a search. A single deep search is unbounded: the same
# twelve turns at depth 8 peak at roughly 500 MB whatever this is set to, because the
# growth all happens inside one call that never consults it. Both engines behave this
# way -- the Rust reproduces the Python faithfully, including here -- so it is a property
# of the design rather than a port regression. It is harmless at depth 6 and is not at
# depth 8.
#
# And the assignment below only reached the Python engine until the accelerator started
# taking TABLE_LIMIT across the FFI on every call. If a future binding stops passing it,
# this line goes back to being a comment with no effect.
SERVER_TABLE_LIMIT = 50_000

MAX_WORKERS = int(os.environ.get("ROYALS_AI_WORKERS", "2"))

# Beyond this many searches waiting, new requests are refused outright. A queue that
# grows without limit converts a CPU shortage into a memory one and makes every waiting
# player's game feel broken instead of one player's request failing honestly.
MAX_QUEUE = int(os.environ.get("ROYALS_AI_QUEUE", "8"))

SEARCH_DEADLINE = float(os.environ.get("ROYALS_AI_DEADLINE", "20"))


class AIBusy(Exception):
    """Too many searches in flight. The caller should return 503, not wait."""


class AITimeout(Exception):
    """A search overran its deadline."""


# ---------------------------------------------------------------------------
# Worker-side. These run in a child process; arguments and results must be picklable,
# which tuples of ints are.
# ---------------------------------------------------------------------------

def _worker_init():
    AI.TABLE_LIMIT = SERVER_TABLE_LIMIT


def _load_game_state(ko_boards):
    """Make this worker's engine globals describe exactly one game, and no other."""
    Engine.koTrack.clear()
    Engine.koTrack.update(ko_boards)
    Engine.koGeneration = len(ko_boards)
    AI.newGame()


def _clear_game_state():
    Engine.koTrack.clear()
    AI.newGame()


def take_turn(board, contr, depth, ko_boards):
    """Choose and play one move. Returns (board, move, score, nodes, seconds)."""
    AI.TABLE_LIMIT = SERVER_TABLE_LIMIT
    _load_game_state(ko_boards)
    try:
        started = time.monotonic()
        new_board, move, score = AI.takeTurn(board, contr, depth)
        return new_board, move, score, AI.calcCount, time.monotonic() - started
    finally:
        _clear_game_state()


def choose_entry(board, contr, piece, is_spy, seed, noise):
    """Pick a square to enter a piece on.

    setEntryNoise is deterministic in its seed -- it builds the Perlin fields from
    Perlin.field(seed) and field(seed + 1) -- so passing the game's stored seed on every
    call reproduces the same opening field each time, and makes a game replayable from
    (seed, moves) alone rather than depending on process-local state.
    """
    AI.setEntryNoise(noise, seed)
    return AI.chooseEntry(board, contr, piece, is_spy)


# ---------------------------------------------------------------------------
# Server-side
# ---------------------------------------------------------------------------

class AIPool:
    def __init__(self, max_workers=MAX_WORKERS, max_queue=MAX_QUEUE,
                 deadline=SEARCH_DEADLINE):
        self._executor = None
        self._max_workers = max_workers
        self._deadline = deadline
        # Permits = workers + the queue we are willing to hold. Acquired without
        # waiting, so an overloaded server says so immediately.
        self._permits = asyncio.Semaphore(max_workers + max_queue)
        self._inflight = 0

    def start(self):
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=self._max_workers, initializer=_worker_init)
        return self

    def shutdown(self):
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    @property
    def inflight(self):
        return self._inflight

    async def _run(self, fn, *args, deadline=None):
        if self._executor is None:
            raise RuntimeError("AI pool not started")

        if self._permits.locked():
            raise AIBusy("too many searches in flight")
        try:
            await asyncio.wait_for(self._permits.acquire(), timeout=0.001)
        except asyncio.TimeoutError:
            raise AIBusy("too many searches in flight")

        self._inflight += 1
        loop = asyncio.get_running_loop()
        try:
            fut = loop.run_in_executor(self._executor, fn, *args)
            return await asyncio.wait_for(fut, timeout=deadline or self._deadline)
        except asyncio.TimeoutError:
            raise AITimeout("the search overran its deadline")
        finally:
            self._inflight -= 1
            self._permits.release()

    async def take_turn(self, board, contr, depth, ko_boards):
        return await self._run(take_turn, board, contr, depth, list(ko_boards))

    async def choose_entry(self, board, contr, piece, is_spy, seed, noise):
        # Entering is cheap, but it goes through the same pool so there is one code path
        # and one place where concurrency is bounded.
        return await self._run(choose_entry, board, contr, piece, is_spy, seed, noise)


pool = AIPool()
