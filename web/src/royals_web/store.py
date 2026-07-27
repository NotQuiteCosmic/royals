"""Where games live: a bounded cache in front, a database behind.

Until two people could share a game this was the whole story -- an in-memory dictionary,
because there were no accounts and so nothing to persist a game against. What changed is
not accounts but *duration*. One person against the computer plays in a sitting, and
losing that to a restart costs a few minutes. Two people play a game over an evening or a
week, and losing it costs the game.

So the dictionary is now a cache and `persist.Database` is the copy that matters. The
interface is the same one the rest of the app already used -- put, get, drop, len -- which
is what made the swap a change to this file and almost nothing else.

Bounding still matters, and for the same reason as before: a game holds every position it
has stood in, so an endpoint that mints games is a memory attack that needs no cleverness.
What changed is the consequence of hitting the bound. Evicting used to mean losing a game;
now it means the next request for it reads the row and replays it, which costs microseconds
and no correctness at all.
"""

import time
from collections import OrderedDict

# How many games are held in memory. Not how many exist -- that is the database's
# business, and its limit is the disk.
MAX_CACHED = 2_000

# How long a game stays cached after its last use. Purely a memory policy: dropping a
# cached game does not delete it, and deletion is Database.sweep's job on a much longer
# clock.
CACHE_TTL_SECONDS = 6 * 60 * 60

# Per-client game creation. Generous for a person, tight for a script.
CREATE_LIMIT = 30
CREATE_WINDOW_SECONDS = 60 * 10


class GameStore:
    def __init__(self, max_games=MAX_CACHED, ttl=CACHE_TTL_SECONDS, db=None):
        self._games = OrderedDict()          # id -> (game, last_touched)
        self._max = max_games
        self._ttl = ttl
        self._creates = {}                   # client key -> [timestamps]
        self._db = db

    def attach(self, db):
        """Point at a database. Called once at startup; before it, this is memory only."""
        self._db = db
        return self

    # -- games ---------------------------------------------------------------

    def put(self, game):
        """Write through. The row is the record; the cache is an optimisation."""
        if self._db is not None:
            self._db.save(game)
        self._cache(game)
        return game

    def get(self, game_id):
        entry = self._games.get(game_id)
        if entry is not None:
            game, _ = entry
            self._games[game_id] = (game, time.monotonic())
            self._games.move_to_end(game_id)
            return game

        if self._db is None:
            return None

        # A miss is a replay, not a failure: the row holds the move list and the game is
        # rebuilt by playing it. Returns None if the row does not replay to itself.
        game = self._db.load(game_id)
        if game is not None:
            self._cache(game)
        return game

    def drop(self, game_id):
        self._games.pop(game_id, None)
        if self._db is not None:
            self._db.delete(game_id)

    def __len__(self):
        """Games in memory. Deliberately not a row count: this is read by the health
        check every few seconds, and it should stay a constant-time answer."""
        return len(self._games)

    def _cache(self, game):
        self._evict()
        self._games[game.id] = (game, time.monotonic())
        self._games.move_to_end(game.id)
        while len(self._games) > self._max:
            self._games.popitem(last=False)

    def _evict(self):
        cutoff = time.monotonic() - self._ttl
        stale = [gid for gid, (_, seen) in self._games.items() if seen < cutoff]
        for gid in stale:
            del self._games[gid]

    def sweep(self):
        """Delete games nobody has come back to. Returns how many rows went."""
        return self._db.sweep() if self._db is not None else 0

    # -- rate limiting -------------------------------------------------------

    def may_act(self, key, limit, window):
        """A sliding-window count of anything, keyed by a caller-chosen string.

        Creating games is not the only thing worth bounding -- joining is a second --
        and the two want different limits, so the key carries its own namespace
        ("join:1.2.3.4") rather than there being a table per endpoint.
        """
        now = time.monotonic()
        stamps = [t for t in self._creates.get(key, ()) if now - t < window]
        if len(stamps) >= limit:
            self._creates[key] = stamps
            return False
        stamps.append(now)
        self._creates[key] = stamps

        # keep the tracking table from becoming its own leak
        if len(self._creates) > 10_000:
            for k, v in list(self._creates.items()):
                if not any(now - t < window for t in v):
                    del self._creates[k]
        return True

    def may_create(self, client_key, limit=CREATE_LIMIT, window=CREATE_WINDOW_SECONDS):
        return self.may_act(client_key, limit, window)


store = GameStore()
