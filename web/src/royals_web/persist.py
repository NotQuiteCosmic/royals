"""Games on disk: one row each, holding the move list rather than the position.

The store above this is a cache; this is the copy that matters. It exists because a game
between two people is played over hours or days, and until now a restart -- a deploy, a
crash, an idle machine being recycled -- silently destroyed every game in progress. That
was survivable when a game was one person and one sitting against the computer. It is not
survivable for "send your friend a link".

**What is stored is the move list, not the board.** The board is a derived thing: replay
the moves and you have it, along with `ko_boards`, `turn`, `passes`, `last_move` and the
phase, all rebuilt by the same functions that built them the first time. Storing the
position instead would mean either losing the ko history -- and the ko rule is about the
whole history, so that is losing the rules -- or writing a second way to serialise and
restore it, which is a second way to be wrong. See `game.replay`.

It also means the row is checkable. A move list either replays to itself or it does not,
so a corrupted or edited row fails to load rather than producing a board that looks
plausible and is not. That is worth more here than it sounds: nobody would notice a
subtly wrong board, and both players would be told it was the real one.

With one exception, which is why `ply` is a column and not something derived on read. A
*prefix* of a legal game is a legal game -- drop the last two moves and the record still
replays perfectly, just into a position two moves stale. Self-consistency cannot see
that, because there is nothing inconsistent about it. Storing the length is what closes
it, and it is the only reason that column exists.

**No pickle.** A board is a tuple and pickling it into a BLOB is the obvious shortcut and
is remote code execution: `pickle.loads` on bytes from a database runs whatever the bytes
say. Everything written here is text and numbers, and the only thing that turns them back
into a game is a function that plays moves.

sqlite3 is in the standard library, which keeps `web`'s dependency list as short as its
comment in pyproject.toml promises, and a single-file database is the right shape for a
server that must be one process anyway (see the Dockerfile: two workers would mean two
in-memory caches disagreeing about the same row).
"""

import logging
import os
import sqlite3
import threading
import time

from royals_web import game as G

log = logging.getLogger("royals.persist")

# Correspondence games are slow, and a game is a few kilobytes. Being generous costs
# almost nothing; being stingy loses somebody's game while they were asleep.
TTL_HUMAN = 30 * 24 * 60 * 60
TTL_AI = 7 * 24 * 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id           TEXT PRIMARY KEY,
    version      INTEGER NOT NULL,
    mode         TEXT    NOT NULL,
    ai_depth     INTEGER,
    entry_seed   INTEGER NOT NULL,
    entry_noise  REAL    NOT NULL,
    moves        TEXT    NOT NULL,
    ply          INTEGER NOT NULL,
    seat0_kind   TEXT    NOT NULL,
    seat0_hash   TEXT,
    seat0_claimed INTEGER NOT NULL,
    seat1_kind   TEXT    NOT NULL,
    seat1_hash   TEXT,
    seat1_claimed INTEGER NOT NULL,
    invite_hash  TEXT,
    result       TEXT,
    termination  TEXT,
    created_at   REAL    NOT NULL,
    updated_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS games_updated ON games (updated_at);
"""

COLUMNS = (
    "id", "version", "mode", "ai_depth", "entry_seed", "entry_noise", "moves", "ply",
    "seat0_kind", "seat0_hash", "seat0_claimed",
    "seat1_kind", "seat1_hash", "seat1_claimed",
    "invite_hash", "result", "termination", "created_at", "updated_at",
)


def to_row(game):
    blue, red = game.seats[G.BLUE], game.seats[G.RED]
    return {
        "id": game.id,
        "version": game.version,
        "mode": game.mode,
        "ai_depth": game.ai_depth,
        "entry_seed": game.entry_seed,
        "entry_noise": game.entry_noise,
        "moves": " ".join(game.moves),
        # Stored, not derived, and that is the point: a prefix of a legal game replays
        # perfectly, so the length is the one thing the move list cannot check itself.
        "ply": len(game.moves),
        "seat0_kind": blue.kind, "seat0_hash": blue.token_hash,
        "seat0_claimed": int(blue.claimed),
        "seat1_kind": red.kind, "seat1_hash": red.token_hash,
        "seat1_claimed": int(red.claimed),
        "invite_hash": game.invite_hash,
        "result": game.result,
        "termination": game.termination,
        "created_at": game.created_at,
        "updated_at": game.updated_at,
    }


def from_row(row):
    """Rebuild a game. Raises game.ReplayError if the row does not describe one."""
    return G.replay(
        id=row["id"], mode=row["mode"], ai_depth=row["ai_depth"],
        entry_seed=row["entry_seed"], entry_noise=row["entry_noise"],
        moves=row["moves"], ply=row["ply"],
        seats={
            G.BLUE: G.Seat(kind=row["seat0_kind"], token_hash=row["seat0_hash"],
                           claimed=bool(row["seat0_claimed"])),
            G.RED: G.Seat(kind=row["seat1_kind"], token_hash=row["seat1_hash"],
                          claimed=bool(row["seat1_claimed"])),
        },
        invite_hash=row["invite_hash"],
        result=row["result"], termination=row["termination"],
        version=row["version"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Database:
    """One connection, one lock, synchronous writes.

    A game is a few kilobytes of text and a write takes well under a millisecond, so
    handing these to a thread pool would add more machinery and more failure modes than
    it removes latency. The lock is there because the connection is shared across
    threads (`check_same_thread=False`), not because writes contend.

    WAL matters for a different reason than throughput: it lets a reader proceed while a
    write is in flight, which is what keeps a poll from blocking behind somebody's move.
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # WAL is not available for :memory:, and asking for it there is harmless.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def save(self, game):
        row = to_row(game)
        columns = ", ".join(COLUMNS)
        placeholders = ", ".join(":" + c for c in COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO games ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET " +
                ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "id"),
                row)
            self._conn.commit()

    def load(self, game_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if row is None:
            return None
        try:
            return from_row(row)
        except G.ReplayError:
            # Refusing to load is the whole point of checking. Handing back a board that
            # replays differently from its own record would be worse than a lost game,
            # because both players would be told it was the real position.
            log.error("game %s does not replay from its stored moves", game_id,
                      exc_info=True)
            return None

    def delete(self, game_id):
        with self._lock:
            self._conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
            self._conn.commit()

    def sweep(self, now=None):
        """Delete games nobody has touched in a long time. Returns how many went."""
        now = time.time() if now is None else now
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM games WHERE (mode = 'human' AND updated_at < ?) "
                "                     OR (mode <> 'human' AND updated_at < ?)",
                (now - TTL_HUMAN, now - TTL_AI))
            self._conn.commit()
            return cur.rowcount

    def count(self):
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]

    def close(self):
        with self._lock:
            self._conn.close()


def open_default():
    """The database named by ROYALS_DB, or an unshared in-memory one.

    Defaulting to memory rather than to a file is deliberate. A path that appears by
    itself would put a royals.db wherever anyone happened to run the server from, and
    would make the test suite share state between runs. Persistence is a thing the
    deployment asks for -- serve.py asks for it, the Dockerfile asks for it -- and when
    nothing asks, the behaviour is exactly what it was before this file existed.
    """
    path = os.environ.get("ROYALS_DB", "").strip()
    if not path:
        log.warning("ROYALS_DB is not set: games are held in memory and will not "
                    "survive a restart")
        return Database(":memory:")
    return Database(path)
