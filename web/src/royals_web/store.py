"""Where games live for M1: in memory, bounded, and forgetful.

There are no accounts yet, so there is nothing to persist a game against and no reason
to run a database. M2 replaces this with Postgres and the move list becomes the source
of truth; the interface here is deliberately the small one that will survive that swap.

Bounded matters even without accounts. A game holds every position it has stood in, so
an unattended endpoint that mints games is a memory attack that needs no cleverness at
all -- just a loop. Hence a hard ceiling, a TTL, and a per-client creation limit.
"""

import time
from collections import OrderedDict

MAX_GAMES = 2_000
GAME_TTL_SECONDS = 6 * 60 * 60

# Per-client game creation. Generous for a person, tight for a script.
CREATE_LIMIT = 30
CREATE_WINDOW_SECONDS = 60 * 10


class GameStore:
    def __init__(self, max_games=MAX_GAMES, ttl=GAME_TTL_SECONDS):
        self._games = OrderedDict()          # id -> (game, last_touched)
        self._max = max_games
        self._ttl = ttl
        self._creates = {}                   # client key -> [timestamps]

    # -- games ---------------------------------------------------------------

    def put(self, game):
        self._evict()
        self._games[game.id] = (game, time.monotonic())
        self._games.move_to_end(game.id)
        while len(self._games) > self._max:
            self._games.popitem(last=False)
        return game

    def get(self, game_id):
        entry = self._games.get(game_id)
        if entry is None:
            return None
        game, _ = entry
        self._games[game_id] = (game, time.monotonic())
        self._games.move_to_end(game_id)
        return game

    def drop(self, game_id):
        self._games.pop(game_id, None)

    def __len__(self):
        return len(self._games)

    def _evict(self):
        cutoff = time.monotonic() - self._ttl
        stale = [gid for gid, (_, seen) in self._games.items() if seen < cutoff]
        for gid in stale:
            del self._games[gid]

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
