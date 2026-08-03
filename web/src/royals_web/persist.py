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
server that must be one process anyway: two workers would mean two in-memory caches
disagreeing about the same row, because `store.GameStore.get` answers from its cache
without asking whether the row has moved under it. `serve.py` runs a single uvicorn
process with no `workers=` for that reason. (This used to say "see the Dockerfile". There
is no Dockerfile in this repo and there never was; the reasoning was right and the
citation was not.)
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

# A hard ceiling on rows, so the disk is a fixed cost rather than a function of how
# patient somebody is. A game is a few kilobytes, so this is tens of megabytes -- far more
# than this will ever hold in real use, and far less than a script can produce in a day.
MAX_ROWS = int(os.environ.get("ROYALS_MAX_GAMES", "20000"))

# How often the sweep runs. It was once, at startup, which on a server that stays up is
# indistinguishable from never.
SWEEP_INTERVAL = 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id           TEXT PRIMARY KEY,
    version      INTEGER NOT NULL,
    mode         TEXT    NOT NULL,
    ai_depth     INTEGER,
    entry_seed   INTEGER NOT NULL,
    entry_noise  REAL    NOT NULL,
    random_entry INTEGER NOT NULL DEFAULT 0,
    moves        TEXT    NOT NULL,
    ply          INTEGER NOT NULL,
    seat0_kind   TEXT    NOT NULL,
    seat0_hash   TEXT,
    seat0_claimed INTEGER NOT NULL,
    seat0_name   TEXT,
    seat1_kind   TEXT    NOT NULL,
    seat1_hash   TEXT,
    seat1_claimed INTEGER NOT NULL,
    seat1_name   TEXT,
    invite_hash  TEXT,
    result       TEXT,
    termination  TEXT,
    created_at   REAL    NOT NULL,
    updated_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS games_updated ON games (updated_at);
"""

COLUMNS = (
    "id", "version", "mode", "ai_depth", "entry_seed", "entry_noise", "random_entry",
    "moves", "ply",
    "seat0_kind", "seat0_hash", "seat0_claimed", "seat0_name",
    "seat1_kind", "seat1_hash", "seat1_claimed", "seat1_name",
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
        # The one setting the move list cannot speak for. A random game that nobody has
        # joined yet has an empty move list, and an empty move list looks the same either
        # way -- so whether the pieces are to be dealt has to be written down.
        "random_entry": int(game.random_entry),
        "moves": " ".join(game.moves),
        # Stored, not derived, and that is the point: a prefix of a legal game replays
        # perfectly, so the length is the one thing the move list cannot check itself.
        "ply": len(game.moves),
        "seat0_kind": blue.kind, "seat0_hash": blue.token_hash,
        "seat0_claimed": int(blue.claimed), "seat0_name": blue.name,
        "seat1_kind": red.kind, "seat1_hash": red.token_hash,
        "seat1_claimed": int(red.claimed), "seat1_name": red.name,
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
        random_entry=bool(row["random_entry"]),
        moves=row["moves"], ply=row["ply"],
        seats={
            G.BLUE: G.Seat(kind=row["seat0_kind"], token_hash=row["seat0_hash"],
                           claimed=bool(row["seat0_claimed"]),
                           name=row["seat0_name"]),
            G.RED: G.Seat(kind=row["seat1_kind"], token_hash=row["seat1_hash"],
                          claimed=bool(row["seat1_claimed"]),
                          name=row["seat1_name"]),
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
            self._run("PRAGMA journal_mode=WAL")
            self._run("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    # Every statement in this class goes through one of the two below, and the reason is
    # the .close(). CPython frees the cursor `execute` hands back the moment the last
    # reference to it goes, and freeing it finalises the statement underneath -- so a
    # commit on the next line sees a connection with nothing in flight. PyPy does not
    # refcount; the statement is still open when the commit runs, and sqlite refuses:
    #
    #     sqlite3.OperationalError: cannot commit transaction - SQL statements in progress
    #
    # It is not a fussy difference. Every write here failed under PyPy, which the engine
    # bends over backwards to stay compatible with: it runs the search several times faster
    # than CPython does, and it is what a deployment has for speed when the compiled wheel
    # is not an option -- that wheel is abi3 CPython, so PyPy never gets it. Keeping the door
    # open in the engine is worth nothing if the server around it cannot open a database.
    #
    # fetchone() is not enough either: a SELECT that matched a row is only part-read, so
    # the statement stays open exactly the same way.
    def _run(self, sql, params = ()):
        """Execute a statement, finish with it, and report how many rows it touched."""
        cur = self._conn.execute(sql, params)
        try:
            return cur.rowcount
        finally:
            cur.close()

    def _query(self, sql, params = (), one = False):
        """Execute a query and read it out completely before letting go of it."""
        cur = self._conn.execute(sql, params)
        try:
            rows = cur.fetchall()
        finally:
            cur.close()
        if not one: return rows
        return rows[0] if rows else None

    def _migrate(self):
        """Bring an older database up to the current shape, in place.

        `CREATE TABLE IF NOT EXISTS` does exactly nothing to a table that already exists,
        so a column added to SCHEMA appears only in databases created after it -- and the
        first person to notice is whoever is holding a half-finished game in the one that
        already existed. Every column added from here on needs a line in this table.

        Adding a column is the only migration this needs so far, and SQLite does it without
        rewriting the table -- including a NOT NULL one, so long as it carries a constant
        default for the rows that already exist. Anything that ever needs more than that
        should be written as a numbered step rather than bolted on here.
        """
        have = {row["name"] for row in self._query("PRAGMA table_info(games)")}
        for column, ddl in (("seat0_name", "TEXT"), ("seat1_name", "TEXT"),
                            # The default is what an older row means: every game written
                            # before this column existed had its squares picked by hand.
                            ("random_entry", "INTEGER NOT NULL DEFAULT 0")):
            if column not in have:
                log.info("migrating %s: adding games.%s", self.path, column)
                self._run(f"ALTER TABLE games ADD COLUMN {column} {ddl}")

    def save(self, game):
        row = to_row(game)
        columns = ", ".join(COLUMNS)
        placeholders = ", ".join(":" + c for c in COLUMNS)
        with self._lock:
            self._run(
                f"INSERT INTO games ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET " +
                ", ".join(f"{c}=excluded.{c}" for c in COLUMNS if c != "id"),
                row)
            self._conn.commit()

    def version_of(self, game_id):
        """What the row says its version is, or None if there is no row.

        One indexed read of one integer, and deliberately not a `load`: this is what lets the
        cache in front of this database ask "has the row moved under me?" on every read
        without replaying a move list to find out. `store.GameStore.get` calls it on the poll
        path, which is the busiest thing this server does when nothing is happening, so the
        cost of answering has to stay a primary-key lookup.
        """
        with self._lock:
            row = self._query("SELECT version FROM games WHERE id = ?", (game_id,), one=True)
        return None if row is None else row[0]

    def load(self, game_id):
        with self._lock:
            row = self._query("SELECT * FROM games WHERE id = ?", (game_id,), one=True)
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
            self._run("DELETE FROM games WHERE id = ?", (game_id,))
            self._conn.commit()

    def sweep(self, now=None):
        """Delete what has aged out, then whatever is over the ceiling. Returns the count.

        Two rules, because age alone does not bound anything. The per-client creation
        limit permits a few thousand games a day from one address, so a server left
        running accumulates for a month before the first of them is old enough to expire.
        The ceiling is what makes the disk a fixed cost; the ages are what make a game
        somebody might come back to outlive one nobody will.
        """
        now = time.time() if now is None else now
        with self._lock:
            gone = self._run(
                "DELETE FROM games WHERE (mode = 'human' AND updated_at < ?) "
                "                     OR (mode <> 'human' AND updated_at < ?)",
                (now - TTL_HUMAN, now - TTL_AI))

            # Oldest-by-last-touched first: a game being played is the last to go, and a
            # game abandoned an hour after it was made is the first.
            gone += self._run(
                "DELETE FROM games WHERE id IN ("
                "  SELECT id FROM games ORDER BY updated_at DESC LIMIT -1 OFFSET ?)",
                (MAX_ROWS,))

            self._conn.commit()
            return gone

    def count(self):
        with self._lock:
            return self._query("SELECT COUNT(*) FROM games", one=True)[0]

    def close(self):
        with self._lock:
            self._conn.close()


def open_default():
    """The database named by ROYALS_DB, or an unshared in-memory one.

    Defaulting to memory rather than to a file is deliberate. A path that appears by
    itself would put a royals.db wherever anyone happened to run the server from, and
    would make the test suite share state between runs. Persistence is a thing the
    deployment asks for -- serve.py asks for it, and a public deployment sets ROYALS_DB to
    a path on its persistent disk -- and when nothing asks, the behaviour is exactly what
    it was before this file existed.
    """
    path = os.environ.get("ROYALS_DB", "").strip()
    if not path:
        log.warning("ROYALS_DB is not set: games are held in memory and will not "
                    "survive a restart")
        return Database(":memory:")
    return Database(path)
