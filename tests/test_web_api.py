"""The server's half of the rules: who may ask, and what it accepts.

The thing worth testing here is not that a legal move works -- the engine's goldens
already cover what moves do. It is that the server refuses everything else: a move by
the wrong side, a move in the wrong phase, a well-formed move that isn't legal, a pass
when passing isn't allowed, and a board the client would like to substitute for the
real one.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from royals_engine import hasher as Hasher
from royals_engine import engine as Engine
from royals_engine import ai as AI
from royals_engine import notation as N

from royals_web import game as G
from royals_web.main import app
from royals_web.store import store


@pytest.fixture(autouse=True)
def fresh_limits():
    """Give every test its own rate-limit budget.

    Every request in this file arrives from the same client as far as the server is
    concerned, and the limits are per client -- so without this the suite spends one
    shared allowance and tests start failing in whatever order they happen to run in.
    That is a fact about running a thousand requests a second from one address, not about
    the limiter, which has its own tests.
    """
    store._creates.clear()
    yield
    store._creates.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def game(client):
    """A game against the computer, with `client` holding the human's seat.

    The seat token goes on the client as a default header, so every request this test
    file already made carries it without being rewritten. That is the whole of what
    authorization looks like from a player's side.
    """
    res = client.post("/api/games",
                      json={"mode": "ai", "side": 0, "difficulty": "novice", "noise": 0.5})
    assert res.status_code == 200, res.text
    body = res.json()
    client.headers["X-Royals-Seat"] = body["seatToken"]
    return body["state"]


def pure_game(**kwargs):
    """A Game object, with no HTTP involved. new_game also returns the tokens."""
    kwargs.setdefault("mode", "ai")
    kwargs.setdefault("side", 0)
    game, _seat, _invite = G.new_game(**kwargs)
    return game


def play_to_move_phase(client, state):
    """Enter pieces (choosing the first legal option each time) until play begins."""
    while state["phase"] == "entering":
        assert state["awaitingYou"], "the server should stop for the player"
        square = state["entering"]["options"][0]
        res = client.post(f"/api/games/{state['id']}/enter", json={"square": square})
        assert res.status_code == 200, res.text
        state = res.json()
    return state


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------

def test_new_game_starts_in_entering_and_waits_for_the_human(game):
    assert game["phase"] == "entering"
    assert game["awaitingYou"] is True
    assert game["yourSide"] == 0
    assert len(game["board"]) == 49
    assert game["entering"]["options"]


def test_playing_through_entering_reaches_play(client, game):
    state = play_to_move_phase(client, game)
    assert state["phase"] == "playing"
    assert state["awaitingYou"] is True
    assert state["origins"], "the human should have somewhere to move from"
    # 12 placements, and the computer has already replied to none of them yet
    assert len(state["moves"]) >= 12


def test_a_legal_move_is_accepted_and_the_computer_replies(client, game):
    state = play_to_move_phase(client, game)
    origin = state["origins"][0]

    listed = client.get(f"/api/games/{state['id']}/moves",
                        params={"origin": origin}).json()
    assert listed["moves"], "an origin the server offered must have moves"

    move = listed["moves"][0]
    body = {"kind": move["kind"], "origin": move["origin"], "pris": move.get("pris", False)}
    if move["kind"] == "break":
        body["dir"] = move["dir"]
    else:
        body["target"] = move["target"]

    after = client.post(f"/api/games/{state['id']}/move", json=body)
    assert after.status_code == 200, after.text
    after = after.json()

    # the human's move plus the computer's reply
    assert len(after["moves"]) > len(state["moves"])
    assert after["awaitingYou"] or after["phase"] == "over"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_unknown_game_is_404(client):
    assert client.get("/api/games/" + "0" * 32).status_code == 404


def test_move_during_entering_is_refused(client, game):
    res = client.post(f"/api/games/{game['id']}/move",
                      json={"kind": "jump", "origin": "d3", "target": "e4"})
    assert res.status_code == 409


def test_entering_on_an_illegal_square_is_refused(client, game):
    options = set(game["entering"]["options"])
    illegal = next(a for a in (f + str(r) for f in "abcdefg" for r in range(1, 8))
                   if a not in options)
    res = client.post(f"/api/games/{game['id']}/enter", json={"square": illegal})
    assert res.status_code == 422


ALL_SQUARES = [f + str(r) for r in range(1, 8) for f in "abcdefg"]


def test_wellformed_but_illegal_move_is_refused_and_changes_nothing(client, game):
    """A move with the right shape, from a square the player really owns, to a square
    the server did not offer. Guessing an illegal move by hand does not work -- the
    first attempt at this test picked Ja1b2, which turned out to be perfectly legal --
    so the illegal move is derived from the server's own list of the legal ones."""
    state = play_to_move_phase(client, game)
    before = client.get(f"/api/games/{state['id']}").json()

    origin = state["origins"][0]
    listed = client.get(f"/api/games/{state['id']}/moves",
                        params={"origin": origin}).json()
    offered = {m["target"] for m in listed["moves"] if m["kind"] == "jump"}

    illegal = next(sq for sq in ALL_SQUARES if sq != origin and sq not in offered)

    res = client.post(f"/api/games/{state['id']}/move",
                      json={"kind": "jump", "origin": origin, "target": illegal})
    assert res.status_code == 422, res.text

    after = client.get(f"/api/games/{state['id']}").json()
    assert after["board"] == before["board"]
    assert after["moves"] == before["moves"]
    assert after["ply"] == before["ply"]


def test_moving_a_piece_that_is_not_yours_is_refused(client, game):
    """An origin holding the opponent's stack is not a legal origin."""
    state = play_to_move_phase(client, game)
    human = state["yourSide"]

    enemy_squares = [ALL_SQUARES[i] for i, sq in enumerate(state["board"])
                     if sq and sq["side"] != human and not sq["capSpy"]]
    assert enemy_squares, "precondition: the opponent has pieces on the board"

    for origin in enemy_squares[:4]:
        res = client.post(f"/api/games/{state['id']}/move",
                          json={"kind": "jump", "origin": origin, "target": "d4"})
        assert res.status_code == 422, (origin, res.text)


def test_malformed_move_is_refused(client, game):
    state = play_to_move_phase(client, game)
    for body in [
        {"kind": "teleport", "origin": "d3", "target": "e4"},
        {"kind": "jump", "origin": "zz", "target": "e4"},
        {"kind": "jump", "origin": "d3", "target": "q9"},
        {"kind": "break", "origin": "d3", "dir": "q"},
        {"kind": "jump", "origin": "d3"},
    ]:
        res = client.post(f"/api/games/{state['id']}/move", json=body)
        assert res.status_code == 422, (body, res.status_code, res.text)


def test_passing_when_moves_exist_is_refused(client, game):
    state = play_to_move_phase(client, game)
    assert state["origins"], "precondition: the human has moves"
    res = client.post(f"/api/games/{state['id']}/pass")
    assert res.status_code == 422


def test_resigning_ends_the_game_and_the_opponent_wins(client, game):
    state = play_to_move_phase(client, game)
    res = client.post(f"/api/games/{state['id']}/resign").json()
    assert res["phase"] == "over"
    assert res["termination"] == "resign"
    assert res["result"] == "red"     # human is blue
    assert client.post(f"/api/games/{state['id']}/move",
                       json={"kind": "jump", "origin": "d3", "target": "e4"}).status_code == 409


def test_oversized_body_is_rejected_before_parsing(client, game):
    res = client.post(f"/api/games/{game['id']}/enter",
                      content=b'{"square":"' + b"a" * 4000 + b'"}',
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_security_headers_are_set(client):
    res = client.get("/api/health")
    csp = res.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "unsafe-inline" not in csp, "the whole point of having no third-party JS"
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"


def test_the_client_cannot_supply_a_board(client, game):
    """Extra fields must be ignored, not honoured."""
    state = play_to_move_phase(client, game)
    before = client.get(f"/api/games/{state['id']}").json()["board"]

    listed = client.get(f"/api/games/{state['id']}/moves",
                        params={"origin": state["origins"][0]}).json()
    move = listed["moves"][0]
    body = {"kind": move["kind"], "origin": move["origin"],
            "pris": move.get("pris", False),
            "board": [0] * 49, "yourSide": 1, "result": "blue"}
    if move["kind"] == "break":
        body["dir"] = move["dir"]
    else:
        body["target"] = move["target"]

    res = client.post(f"/api/games/{state['id']}/move", json=body)
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["board"] != [None] * 49
    assert after["yourSide"] == 0
    assert after["result"] is None or after["termination"] == "gather"


# ---------------------------------------------------------------------------
# The ko/pass trap, tested against the state machine directly
# ---------------------------------------------------------------------------

def test_legal_moves_filters_ko_and_that_is_what_permits_a_pass():
    """A side can have moves that all break ko. It must then be allowed to pass.

    This is the trap: AI.listAllMoves is non-empty, so anything asking "do you have
    moves?" without the ko filter concludes the player must move, and then rejects the
    pass it should have accepted.
    """
    board = Hasher.Entering_Board()
    contr = 0
    raw = AI.listAllMoves(board, contr)
    assert raw, "precondition: the position has moves at all"

    # Poison the ko set with every position those moves lead to.
    ko = {AI.performOneStep(board, contr, m) for m in raw}

    assert G.legal_moves(board, contr, ko) == []
    assert G.legal_moves(board, contr, set()) == raw


def test_pass_is_accepted_when_every_move_breaks_ko():
    game = pure_game(difficulty="novice", entry_seed=1)
    while game.phase == "entering":
        options = G.entering_options(game.board, game.entry_side, game.entry_piece)
        G.place(game, options[0], side=game.entry_side)

    contr = game.turn % 2
    for m in AI.listAllMoves(game.board, contr):
        game._ko_set.add(AI.performOneStep(game.board, contr, m))

    assert G.legal_moves(game.board, contr, game.ko_set()) == []
    G.play_move(game, None, side=contr)          # must not raise
    assert game.moves[-1] == "--"


def test_a_move_that_repeats_a_position_is_rejected():
    game = pure_game(difficulty="novice", entry_seed=7)
    while game.phase == "entering":
        options = G.entering_options(game.board, game.entry_side, game.entry_piece)
        G.place(game, options[0], side=game.entry_side)

    contr = game.turn % 2
    move = AI.listAllMoves(game.board, contr)[0]
    game._ko_set.add(AI.performOneStep(game.board, contr, move))

    with pytest.raises(G.IllegalMove):
        G.play_move(game, move, side=contr)


# ---------------------------------------------------------------------------
# Concurrency: the regression test for the engine-globals hazard
# ---------------------------------------------------------------------------

def test_two_interleaved_games_do_not_poison_each_others_ko():
    """Game B's positions must never land in game A's ko set.

    With module-level engine globals this is exactly what would go wrong, and the
    symptom is a legal move being refused in an unrelated game. Validation here is pure
    -- the ko set is per-Game -- so interleaving must be indistinguishable from playing
    each game alone.
    """
    def fresh(seed):
        g = pure_game(difficulty="novice", entry_seed=seed)
        while g.phase == "entering":
            options = G.entering_options(g.board, g.entry_side, g.entry_piece)
            G.place(g, options[0], side=g.entry_side)
        return g

    def solo(seed, turns):
        g = fresh(seed)
        played = []
        for _ in range(turns):
            contr = g.turn % 2
            legal = G.legal_moves(g.board, contr, g.ko_set())
            if not legal:
                break
            G.play_move(g, legal[0], side=contr)
            played.append(g.moves[-1])
        return played

    alone_a = solo(11, 8)
    alone_b = solo(23, 8)

    a, b = fresh(11), fresh(23)
    together_a, together_b = [], []
    for _ in range(8):
        for g, log in ((a, together_a), (b, together_b)):
            contr = g.turn % 2
            legal = G.legal_moves(g.board, contr, g.ko_set())
            if not legal:
                continue
            G.play_move(g, legal[0], side=contr)
            log.append(g.moves[-1])

    assert together_a == alone_a
    assert together_b == alone_b


def test_validation_touches_no_engine_globals():
    """The web process must never depend on Engine.koTrack. Only AI workers do."""
    game = pure_game(difficulty="novice", entry_seed=3)
    while game.phase == "entering":
        options = G.entering_options(game.board, game.entry_side, game.entry_piece)
        G.place(game, options[0], side=game.entry_side)

    Engine.koTrack.clear()
    Engine.koTrack.add(game.board)     # a hostile global that must not matter
    before = Engine.koGeneration

    contr = game.turn % 2
    legal = G.legal_moves(game.board, contr, game.ko_set())
    assert legal, "the global should not have suppressed anything"
    G.play_move(game, legal[0], side=contr)

    assert Engine.koGeneration == before, "validation moved the engine's ko generation"
    Engine.koTrack.clear()


# ---------------------------------------------------------------------------
# The whole loop
# ---------------------------------------------------------------------------

def test_a_whole_game_can_be_played_through_the_api(client):
    """Play to a finish, only ever making moves the server itself offered.

    This is the test that says the pieces fit together: entering, the computer's replies,
    ko filtering, passing, and the win check. Two invariants matter as much as reaching
    the end -- the server must never offer an origin it will then refuse a move from,
    and it must never leave the game with nothing to do and no way to say so.
    """
    import random
    rng = random.Random(4)

    created = client.post("/api/games",
                          json={"mode": "ai", "side": 0,
                                "difficulty": "novice", "noise": 0.5}).json()
    client.headers["X-Royals-Seat"] = created["seatToken"]
    state = created["state"]

    while state["phase"] == "entering":
        square = rng.choice(state["entering"]["options"])
        state = client.post(f"/api/games/{state['id']}/enter",
                            json={"square": square}).json()

    for _ in range(400):
        if state["phase"] != "playing":
            break

        if state["mustPass"]:
            assert not state.get("origins"), "mustPass with origins offered is contradictory"
            state = client.post(f"/api/games/{state['id']}/pass").json()
            continue

        origins = state["origins"]
        assert origins, "not passing, but nowhere to move from"

        body = None
        for origin in rng.sample(origins, len(origins)):
            listed = client.get(f"/api/games/{state['id']}/moves",
                                params={"origin": origin}).json()["moves"]
            assert listed, f"the server offered {origin} as an origin but lists no moves"
            m = rng.choice(listed)
            body = {"kind": m["kind"], "origin": m["origin"], "pris": m.get("pris", False)}
            if m["kind"] == "break":
                body["dir"] = m["dir"]
            else:
                body["target"] = m["target"]
            break

        res = client.post(f"/api/games/{state['id']}/move", json=body)
        assert res.status_code == 200, (body, res.text)
        state = res.json()

    assert state["phase"] == "over", "the game never finished"
    assert state["result"] in ("blue", "red", "draw")
    assert state["termination"] in ("gather", "double_pass", "resign", "no_moves")
    # 12 placements plus real play
    assert state["ply"] > 12


# ---------------------------------------------------------------------------
# Store limits
# ---------------------------------------------------------------------------

def test_game_creation_is_rate_limited():
    from royals_web.store import GameStore
    s = GameStore()
    assert all(s.may_create("1.2.3.4") for _ in range(30))
    assert not s.may_create("1.2.3.4")
    assert s.may_create("5.6.7.8"), "the limit must be per client, not global"


def test_store_evicts_beyond_its_ceiling():
    from royals_web.store import GameStore
    s = GameStore(max_games=5)
    made = []
    for i in range(8):
        g = pure_game(difficulty="novice", entry_seed=i)
        s.put(g)
        made.append(g.id)
    assert len(s) == 5
    assert s.get(made[0]) is None
    assert s.get(made[-1]) is not None


# ---------------------------------------------------------------------------
# Two people, one game
# ---------------------------------------------------------------------------
# Knowing a game's id is not permission to move in it. The id travels in a URL that
# people paste into messages, so if holding it were enough, "send your friend a link"
# would mean "send anyone who sees it control of both sides".

@pytest.fixture
def duel(client):
    """A human-vs-human game: (state, blue_headers, red_headers).

    Returned as headers rather than as clients because what is being tested is exactly
    that the token decides who you are -- the same client can be either player, and the
    server must not be able to tell the difference by any other means.
    """
    made = client.post("/api/games", json={"mode": "human", "side": 0}).json()
    state, invite = made["state"], made["inviteToken"]
    blue = {"X-Royals-Seat": made["seatToken"]}

    assert state["phase"] == "waiting", "no placements until both seats are taken"
    assert invite

    joined = client.post(f"/api/games/{state['id']}/join", json={"invite": invite}).json()
    red = {"X-Royals-Seat": joined["seatToken"]}
    return joined["state"], blue, red


def seat_of(state):
    """Whichever set of headers is to move."""
    return state["sideToMove"]


def enter_all(client, state, headers_for):
    """Both players place their pieces, first legal option each time."""
    while state["phase"] == "entering":
        headers = headers_for[state["entering"]["side"]]
        fresh = client.get(f"/api/games/{state['id']}", headers=headers).json()
        square = fresh["entering"]["options"][0]
        res = client.post(f"/api/games/{state['id']}/enter",
                          json={"square": square}, headers=headers)
        assert res.status_code == 200, res.text
        state = res.json()
    return state


# A well-formed body per endpoint. Malformed bodies are rejected by schema validation
# before the handler runs, which would make this test pass for the wrong reason -- 422
# because the body was empty, not 403 because nobody proved a seat.
WELL_FORMED = {
    "/api/games/{game_id}/enter": {"square": "d4"},
    "/api/games/{game_id}/move": {"kind": "jump", "origin": "d3", "target": "e4"},
    "/api/games/{game_id}/pass": {},
    "/api/games/{game_id}/resign": {},
}


def test_a_game_with_no_token_refuses_every_move(client, duel):
    state, blue, _red = duel
    gid = state["id"]

    # Enumerated from the app itself rather than hand-listed, so an endpoint added later
    # without authorization fails this test instead of quietly shipping.
    mutating = [(r.path, list(r.methods)[0]) for r in client.app.routes
                if getattr(r, "methods", None)
                and {"POST"} & r.methods
                and "{game_id}" in getattr(r, "path", "")
                and not r.path.endswith("/join")]
    assert len(mutating) >= 4, "expected enter/move/pass/resign"

    for path, method in mutating:
        assert path in WELL_FORMED, (
            f"{path} is a new game-changing endpoint -- give it a body here so this "
            f"test checks its authorization rather than its schema")
        res = client.request(method, path.replace("{game_id}", gid),
                             json=WELL_FORMED[path])
        assert res.status_code == 403, (path, res.status_code, res.text)


def test_the_opponents_token_cannot_move_for_you(client, duel):
    state, blue, red = duel
    state = enter_all(client, state, {0: blue, 1: red})
    assert state["phase"] == "playing"

    to_move = state["sideToMove"]
    wrong = red if to_move == 0 else blue

    mine = client.get(f"/api/games/{state['id']}",
                      headers=blue if to_move == 0 else red).json()
    origin = mine["origins"][0]
    listed = client.get(f"/api/games/{state['id']}/moves",
                        params={"origin": origin},
                        headers=blue if to_move == 0 else red).json()["moves"]
    m = listed[0]
    body = {"kind": m["kind"], "origin": m["origin"], "pris": m.get("pris", False)}
    body["dir" if m["kind"] == "break" else "target"] = \
        m["dir"] if m["kind"] == "break" else m["target"]

    # A real seat, a real move, the wrong turn. The side is taken from the token, so
    # this is not "a move for blue submitted by red" -- it is red trying to move at all.
    res = client.post(f"/api/games/{state['id']}/move", json=body, headers=wrong)
    assert res.status_code == 409, res.text


def test_a_wrong_token_is_not_a_player(client, duel):
    state, _blue, _red = duel
    for bogus in ("", "x", "0" * 22, "not-a-token"):
        res = client.post(f"/api/games/{state['id']}/pass",
                          headers={"X-Royals-Seat": bogus})
        assert res.status_code == 403, (bogus, res.status_code)


def test_a_spectator_may_watch_but_not_play(client, duel):
    state, blue, red = duel
    state = enter_all(client, state, {0: blue, 1: red})

    watching = client.get(f"/api/games/{state['id']}").json()
    assert watching["board"] == state["board"], "the position is not a secret"
    assert watching["yourSide"] is None
    assert watching["awaitingYou"] is False
    assert "origins" not in watching, "a spectator has no squares to click"

    assert client.post(f"/api/games/{state['id']}/resign").status_code == 403


def test_the_invite_is_single_use(client):
    made = client.post("/api/games", json={"mode": "human", "side": 0}).json()
    gid, invite = made["state"]["id"], made["inviteToken"]

    first = client.post(f"/api/games/{gid}/join", json={"invite": invite})
    assert first.status_code == 200
    assert first.json()["seatToken"]

    # A third person with the same link -- forwarded, or screenshotted -- gets nothing.
    second = client.post(f"/api/games/{gid}/join", json={"invite": invite})
    assert second.status_code == 409, second.text


def test_joining_your_own_game_returns_your_own_seat(client):
    """The creator opening their own invite link must not spend it on themselves."""
    made = client.post("/api/games", json={"mode": "human", "side": 0}).json()
    gid, invite, mine = made["state"]["id"], made["inviteToken"], made["seatToken"]

    res = client.post(f"/api/games/{gid}/join", json={"invite": invite},
                      headers={"X-Royals-Seat": mine})
    assert res.status_code == 200, res.text
    assert res.json()["seatToken"] is None, "no new seat -- keep the one you have"
    assert res.json()["state"]["yourSide"] == 0
    assert res.json()["state"]["phase"] == "waiting", "the seat is still open"

    # and the invitation still works for the person it was meant for
    other = client.post(f"/api/games/{gid}/join", json={"invite": invite})
    assert other.status_code == 200
    assert other.json()["state"]["yourSide"] == 1


def test_opening_the_invite_page_does_not_claim_the_seat(client):
    """Messengers fetch links to build previews. A preview must not take the seat.

    This is not a hypothetical: iMessage, WhatsApp and Slack all GET a URL before any
    human has seen it. If that claimed the seat, the person the link was sent to would
    open it and be told the invitation was already used -- over exactly the channels
    this feature exists to be used on.
    """
    made = client.post("/api/games", json={"mode": "human", "side": 0}).json()
    gid, invite = made["state"]["id"], made["inviteToken"]

    preview = client.get(f"/join/{gid}.{invite}")
    assert preview.status_code == 200
    assert b"<html" in preview.content.lower(), "the page itself, not an API answer"

    assert client.get(f"/api/games/{gid}").json()["phase"] == "waiting"
    assert client.post(f"/api/games/{gid}/join", json={"invite": invite}).status_code == 200


def test_a_whole_game_between_two_people(client, duel):
    """An empty seat to a result, every request carrying its own seat token.

    Twenty plies and then a resignation, rather than playing to a natural finish: two
    players choosing at random take some two thousand plies to gather six pieces and
    sometimes never do, which is a statement about random play and not about this
    server. That a game reaches a result by itself is what the vs-computer game test
    covers. What is worth twenty plies of HTTP here is that the turn alternates strictly
    between two tokens and neither can act out of turn.
    """
    import random
    rng = random.Random(11)

    state, blue, red = duel
    headers_for = {0: blue, 1: red}
    gid = state["id"]
    state = enter_all(client, state, headers_for)
    assert state["phase"] == "playing"

    seen = []
    for _ in range(20):
        if state["phase"] != "playing":
            break
        mover = state["sideToMove"]
        seen.append(mover)
        me, other = headers_for[mover], headers_for[1 - mover]

        # The player who is not to move is refused before anything else is considered.
        assert client.post(f"/api/games/{gid}/pass", headers=other).status_code == 409

        state = client.get(f"/api/games/{gid}", headers=me).json()
        assert state["awaitingYou"] is True

        if state["mustPass"]:
            state = client.post(f"/api/games/{gid}/pass", headers=me).json()
            continue

        origin = rng.choice(state["origins"])
        listed = client.get(f"/api/games/{gid}/moves", params={"origin": origin},
                            headers=me).json()["moves"]
        assert listed, f"the server offered {origin} but lists no moves"
        m = rng.choice(listed)
        body = {"kind": m["kind"], "origin": m["origin"], "pris": m.get("pris", False)}
        body["dir" if m["kind"] == "break" else "target"] = \
            m["dir"] if m["kind"] == "break" else m["target"]

        res = client.post(f"/api/games/{gid}/move", json=body, headers=me)
        assert res.status_code == 200, (body, res.text)
        state = res.json()

    assert seen == [seen[0]] + [1 - s for s in seen[:-1]], "the turn must alternate"
    assert state["mode"] == "human"

    # Blue resigns; red wins. The result is the opponent's, taken from the token.
    over = client.post(f"/api/games/{gid}/resign", headers=blue).json()
    assert over["phase"] == "over"
    assert over["termination"] == "resign"
    assert over["result"] == "red"
    assert over["ply"] > 12


# ---------------------------------------------------------------------------
# Polling and double submission
# ---------------------------------------------------------------------------

def test_an_unchanged_poll_answers_without_rebuilding_the_position(client, game):
    state = play_to_move_phase(client, game)

    same = client.get(f"/api/games/{state['id']}",
                      params={"since": state["version"]}).json()
    assert same == {"id": state["id"], "version": state["version"], "unchanged": True}

    stale = client.get(f"/api/games/{state['id']}",
                       params={"since": state["version"] - 1}).json()
    assert stale["board"] == state["board"], "a stale cursor gets the whole state"


def test_version_moves_for_things_ply_cannot_see(client):
    """A seat being claimed changes nothing about the move list, and everything about
    what the page should show. Polling on ply alone would miss it."""
    made = client.post("/api/games", json={"mode": "human", "side": 0}).json()
    gid, before = made["state"]["id"], made["state"]["version"]
    assert made["state"]["ply"] == 0

    client.post(f"/api/games/{gid}/join", json={"invite": made["inviteToken"]})
    after = client.get(f"/api/games/{gid}").json()
    assert after["version"] > before


def test_a_replayed_move_is_refused_rather_than_played_twice(client, game):
    state = play_to_move_phase(client, game)
    origin = state["origins"][0]
    listed = client.get(f"/api/games/{state['id']}/moves",
                        params={"origin": origin}).json()["moves"]
    m = listed[0]
    body = {"kind": m["kind"], "origin": m["origin"], "pris": m.get("pris", False),
            "expectedVersion": state["version"]}
    body["dir" if m["kind"] == "break" else "target"] = \
        m["dir"] if m["kind"] == "break" else m["target"]

    first = client.post(f"/api/games/{state['id']}/move", json=body)
    assert first.status_code == 200, first.text

    # The same request again -- a phone that retried after a timeout it never saw answered.
    again = client.post(f"/api/games/{state['id']}/move", json=body)
    assert again.status_code == 409, again.text
    assert client.get(f"/api/games/{state['id']}").json()["ply"] == first.json()["ply"]


# ---------------------------------------------------------------------------
# Rate limiting behind a proxy
# ---------------------------------------------------------------------------

def test_forwarded_addresses_are_honoured_only_behind_a_trusted_proxy(monkeypatch):
    """The bug this prevents is invisible locally and total once deployed.

    Behind a proxy every request arrives from the same address, so without this the whole
    internet shares one rate-limit bucket. Trusting the header unconditionally is the
    opposite failure: anyone can then set it and have a bucket per made-up address.
    """
    from royals_web import main as M

    class FakeRequest:
        def __init__(self, headers, peer):
            self.headers = headers
            self.client = type("C", (), {"host": peer})()

    headers = {"fly-client-ip": "9.9.9.9", "x-forwarded-for": "8.8.8.8, 10.0.0.1"}
    from_proxy = FakeRequest(headers, "127.0.0.1")
    from_stranger = FakeRequest(headers, "10.0.0.7")

    monkeypatch.setattr(M, "TRUSTED_PROXY", False)
    assert M.client_key(from_proxy) == "127.0.0.1", "an untrusted header is not an identity"

    monkeypatch.setattr(M, "TRUSTED_PROXY", True)
    assert M.client_key(from_proxy) == "9.9.9.9"

    only_xff = FakeRequest({"x-forwarded-for": "8.8.8.8, 10.0.0.1"}, "127.0.0.1")
    assert M.client_key(only_xff) == "8.8.8.8", "the client is the leftmost entry"

    # Trusting the header is not the same as trusting whoever sent it. The proxy connects
    # from loopback; anything else claiming to speak for a client is some other machine on
    # the network handing itself a fresh rate-limit bucket per request.
    assert M.client_key(from_stranger) == "10.0.0.7", "a stranger may not name the client"

    # ...unless the deployment says its proxy lives elsewhere.
    monkeypatch.setattr(M, "PROXY_PEERS", frozenset({"10.0.0.7"}))
    assert M.client_key(from_stranger) == "9.9.9.9"


def test_join_is_rate_limited_separately_from_creation():
    from royals_web.store import GameStore
    s = GameStore()
    assert all(s.may_act("join:1.2.3.4", 60, 600) for _ in range(60))
    assert not s.may_act("join:1.2.3.4", 60, 600)
    assert s.may_create("1.2.3.4"), "a spent join budget is not a spent create budget"


# ---------------------------------------------------------------------------
# Seat tokens
# ---------------------------------------------------------------------------

def test_tokens_are_stored_only_as_hashes():
    from royals_web import seats as S
    game, seat_token, invite_token = G.new_game(mode="human", side=0)

    stored = {s.token_hash for s in game.seats.values()} | {game.invite_hash}
    assert seat_token not in stored and invite_token not in stored
    assert S.hash_token(seat_token) in stored

    assert game.seat_of(seat_token) == 0
    assert game.seat_of(invite_token) is None, "an invite is not a seat"
    assert game.seat_of(None) is None
    assert game.seat_of("") is None


# ---------------------------------------------------------------------------
# The per-game lock
# ---------------------------------------------------------------------------

def test_the_lock_serialises_and_does_not_leak():
    """One game at a time, and no entry left behind once nobody wants it.

    The refcount is what makes both true. Dropping the entry while a coroutine is still
    queued for it would let the next arrival build a second lock for the same game --
    two locks for one game being, exactly, no lock at all.
    """
    import asyncio
    from royals_web.main import _LockRegistry

    reg = _LockRegistry()
    inside, overlapped = 0, False

    async def worker():
        nonlocal inside, overlapped
        async with reg.hold("g"):
            inside += 1
            if inside > 1:
                overlapped = True
            await asyncio.sleep(0)      # hand control back mid-section
            inside -= 1

    async def main():
        await asyncio.gather(*(worker() for _ in range(8)))
        # A different game is not held up by this one.
        async with reg.hold("g"):
            assert len(reg) == 1
            async with reg.hold("h"):
                assert len(reg) == 2

    asyncio.run(main())
    assert not overlapped, "two coroutines were inside the same game at once"
    assert len(reg) == 0, "the registry kept an entry nobody is waiting on"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
# A game between two people is played over hours. Until it survived a restart, every
# deploy silently destroyed one -- which is a different order of problem from losing a
# session against the computer, and the reason this exists.

@pytest.fixture
def db(tmp_path):
    from royals_web.persist import Database
    d = Database(str(tmp_path / "games.db"))
    yield d
    d.close()


def played_out(seed=2, plies=20, **kwargs):
    """A game taken some way in, by both sides, with no HTTP involved."""
    import random
    rng = random.Random(seed)
    game = pure_game(entry_seed=seed, **kwargs)
    while game.phase == "entering":
        options = G.entering_options(game.board, game.entry_side, game.entry_piece)
        G.place(game, rng.choice(options), side=game.entry_side)
    for _ in range(plies):
        if game.phase != "playing":
            break
        contr = game.turn % 2
        legal = G.legal_moves(game.board, contr, game.ko_set())
        G.play_move(game, rng.choice(legal) if legal else None, side=contr)
    return game


def test_a_game_survives_being_written_and_read_back(db):
    game = played_out()
    db.save(game)
    back = db.load(game.id)

    assert back is not None
    assert back.board == game.board
    assert back.moves == game.moves
    assert (back.turn, back.phase, back.passes) == (game.turn, game.phase, game.passes)
    assert back.last_move == game.last_move
    assert back.version == game.version
    assert G.to_json(back, 0) == G.to_json(game, 0)

    # The ko history is the reason this is a replay rather than a board being unpacked.
    # It is the whole of the game's past, the rule depends on all of it, and nothing
    # writes it down -- it is rebuilt by playing the moves again.
    assert back.ko_boards == game.ko_boards
    assert len(back.ko_boards) > 5
    assert back.ko_set() == game.ko_set()


def test_seats_and_the_invitation_survive_too(db):
    game, seat_token, invite = G.new_game(mode="human", side=1)
    db.save(game)
    back = db.load(game.id)

    assert back.phase == "waiting", "an unclaimed game did not start and must not"
    assert back.seat_of(seat_token) == 1, "the token still opens the same seat"
    assert back.seats[0].claimed is False
    assert back.moves == []

    # and the invitation still works after a restart
    side, _new_token = G.claim_seat(back, invite)
    assert side == 0
    assert back.phase == "entering"


def test_a_resignation_survives_although_it_is_not_a_move(db):
    """Gathering and a double pass are in the move list; a resignation is not."""
    game = played_out()
    G.resign(game, G.BLUE)
    db.save(game)
    back = db.load(game.id)

    assert back.phase == "over"
    assert (back.result, back.termination) == ("red", "resign")


def test_a_tampered_record_refuses_to_load(db):
    """Refusing is the point. A board that replays differently from its own record is
    worse than a lost game: both players would be told it was the real position."""
    game = played_out()
    db.save(game)

    def corrupt(sql, *args):
        db._conn.execute(sql, args)
        db._conn.commit()
        return db.load(game.id)

    original = " ".join(game.moves)

    assert corrupt("UPDATE games SET moves = ? WHERE id = ?",
                   " ".join(game.moves[:-1] + ["Ja1b2"]), game.id) is None, "substituted"
    assert corrupt("UPDATE games SET moves = ? WHERE id = ?",
                   " ".join(reversed(game.moves)), game.id) is None, "reordered"
    assert corrupt("UPDATE games SET moves = ? WHERE id = ?",
                   " ".join(game.moves[3:]), game.id) is None, "beheaded"

    # A placement records which piece was placed, so claiming a spy went where a pawn
    # went re-encodes differently and is caught -- without replay checking pieces itself.
    swapped = game.moves[:]
    first = next(i for i, m in enumerate(swapped) if m.startswith("@"))
    swapped[first] = "@S" + swapped[first][2:]
    assert corrupt("UPDATE games SET moves = ? WHERE id = ?",
                   " ".join(swapped), game.id) is None, "a piece swapped for another"

    # and the untouched record still loads, so the checks are not simply always failing
    assert corrupt("UPDATE games SET moves = ? WHERE id = ?",
                   original, game.id) is not None


def test_a_truncated_record_is_caught_by_its_stored_length(db):
    """The one corruption replaying cannot see, and the only reason `ply` is a column.

    Lopping moves off the end leaves a legal game -- a prefix of one is one -- so it
    replays perfectly, into a position that is real and simply is not the current one.
    Nothing about the move list is wrong, so nothing about the move list can notice.
    """
    game = played_out()
    db.save(game)

    shortened = " ".join(game.moves[:-2])
    db._conn.execute("UPDATE games SET moves = ? WHERE id = ?", (shortened, game.id))
    db._conn.commit()
    assert db.load(game.id) is None, "two moves short and it loaded anyway"

    # Proof that it is the length doing the work and not the replay: with ply corrected
    # to match, the same shortened record is a perfectly good game two moves ago.
    db._conn.execute("UPDATE games SET ply = ? WHERE id = ?",
                     (len(game.moves) - 2, game.id))
    db._conn.commit()
    earlier = db.load(game.id)
    assert earlier is not None
    assert earlier.moves == game.moves[:-2]


def test_an_impossible_ending_is_refused(db):
    """Only a resignation may end a game from outside its move list."""
    game = played_out()
    db.save(game)
    db._conn.execute(
        "UPDATE games SET result = 'blue', termination = 'gather' WHERE id = ?",
        (game.id,))
    db._conn.commit()
    assert db.load(game.id) is None


def test_the_store_reloads_a_game_it_has_evicted(db):
    """Eviction stops being data loss, which is what lets the cache stay small."""
    from royals_web.store import GameStore
    s = GameStore(max_games=2, db=db)

    first = played_out(seed=3, plies=6)
    s.put(first)
    for seed in (4, 5, 6):
        s.put(played_out(seed=seed, plies=4))

    assert len(s) == 2, "the cache is bounded"
    back = s.get(first.id)
    assert back is not None, "evicted from memory is not gone"
    assert back.board == first.board
    assert back.ko_boards == first.ko_boards


def test_the_sweep_deletes_only_what_is_long_dead(db):
    import time as _time
    fresh, stale = played_out(seed=7, plies=4), played_out(seed=8, plies=4)
    stale.updated_at = _time.time() - 400 * 24 * 60 * 60
    db.save(fresh)
    db.save(stale)
    assert db.count() == 2

    assert db.sweep() == 1
    assert db.load(stale.id) is None
    assert db.load(fresh.id) is not None


def test_a_game_played_over_http_survives_a_restart(tmp_path):
    """The whole point, end to end: two people, a move each, and the server restarts.

    Two TestClient blocks are two runs of the process -- lifespan opens the database on
    the way in and closes it on the way out -- pointed at the same file.
    """
    import os
    from royals_web.store import store as live

    os.environ["ROYALS_DB"] = str(tmp_path / "restart.db")
    try:
        with TestClient(app) as c:
            made = c.post("/api/games", json={"mode": "human", "side": 0}).json()
            gid, blue = made["state"]["id"], {"X-Royals-Seat": made["seatToken"]}
            joined = c.post(f"/api/games/{gid}/join",
                            json={"invite": made["inviteToken"]}).json()
            red = {"X-Royals-Seat": joined["seatToken"]}
            state = enter_all(c, joined["state"], {0: blue, 1: red})
            assert state["phase"] == "playing"
            # Whoever entered second opens, so the side to move is not the creator's.
            mover = {0: blue, 1: red}[state["sideToMove"]]
            before = c.get(f"/api/games/{gid}", headers=mover).json()
            assert before["origins"]

        # The process is gone: nothing of this game is in memory any more.
        live._games.clear()

        with TestClient(app) as c:
            after = c.get(f"/api/games/{gid}", headers=mover).json()
            assert after["board"] == before["board"]
            assert after["moves"] == before["moves"]
            assert after["yourSide"] == before["yourSide"], "the token names the same seat"
            assert after["origins"] == before["origins"]

            # and it is still playable, which needs the ko history to have come back
            m = c.get(f"/api/games/{gid}/moves",
                      params={"origin": after["origins"][0]}, headers=mover
                      ).json()["moves"][0]
            body = {"kind": m["kind"], "origin": m["origin"], "pris": m.get("pris", False)}
            body["dir" if m["kind"] == "break" else "target"] = \
                m["dir"] if m["kind"] == "break" else m["target"]
            played = c.post(f"/api/games/{gid}/move", json=body, headers=mover)
            assert played.status_code == 200, played.text
            assert played.json()["ply"] == before["ply"] + 1
    finally:
        os.environ.pop("ROYALS_DB", None)
        live._games.clear()


# ---------------------------------------------------------------------------
# Facing the internet
# ---------------------------------------------------------------------------

def test_the_search_depth_can_be_capped(client, monkeypatch):
    """A depth-6 search is seconds of pinned CPU that anyone can ask for by clicking a
    menu. On a laptop nobody else can reach, that is fine; on a public address it is the
    one request that costs meaningfully more than it takes to make."""
    from royals_web import main as M

    monkeypatch.setattr(M, "MAX_DEPTH", None)
    assert set(M.allowed_difficulties()) == set(G.DIFFICULTIES), "uncapped by default"

    monkeypatch.setattr(M, "MAX_DEPTH", 4)
    offered = M.allowed_difficulties()
    assert "royal" not in offered and "expert" not in offered
    assert "strong" in offered and max(offered.values()) == 4

    # what the menu offers and what the server accepts are the same list
    listed = {d["name"] for d in client.get("/api/difficulties").json()["difficulties"]}
    assert listed == set(offered)

    refused = client.post("/api/games", json={"mode": "ai", "difficulty": "royal"})
    assert refused.status_code == 422, refused.text
    assert client.post("/api/games",
                       json={"mode": "ai", "difficulty": "strong"}).status_code == 200


def test_a_cap_never_leaves_an_empty_menu(monkeypatch):
    """A nonsensical ceiling should still leave a playable game, not a server that
    refuses everything and a menu with nothing in it."""
    from royals_web import main as M
    monkeypatch.setattr(M, "MAX_DEPTH", 1)
    offered = M.allowed_difficulties()
    assert len(offered) == 1
    assert offered == {"novice": G.DIFFICULTIES["novice"]}, "the shallowest survives"


def test_the_default_difficulty_falls_back_when_capped_away(client, monkeypatch):
    from royals_web import main as M
    monkeypatch.setattr(M, "MAX_DEPTH", 2)
    body = client.get("/api/difficulties").json()
    assert body["default"] in {d["name"] for d in body["difficulties"]}, \
        "the menu must not default to an option it does not list"


def test_a_tunnels_forwarded_address_is_honoured(monkeypatch):
    """Cloudflare's header, because a quick tunnel is the first thing this runs behind.

    Every request through one arrives from 127.0.0.1, so without reading a forwarded
    header the whole internet shares one rate-limit bucket.
    """
    from royals_web import main as M

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "127.0.0.1"})()

    monkeypatch.setattr(M, "TRUSTED_PROXY", True)
    assert M.client_key(FakeRequest({"cf-connecting-ip": "203.0.113.7"})) == "203.0.113.7"
    # Fly's header still wins where both are present, and XFF remains the fallback.
    assert M.client_key(FakeRequest({"fly-client-ip": "1.1.1.1",
                                     "cf-connecting-ip": "2.2.2.2"})) == "1.1.1.1"
    assert M.client_key(FakeRequest({"x-forwarded-for": "3.3.3.3, 10.0.0.1"})) == "3.3.3.3"

    monkeypatch.setattr(M, "TRUSTED_PROXY", False)
    assert M.client_key(FakeRequest({"cf-connecting-ip": "203.0.113.7"})) == "127.0.0.1"


def test_the_page_can_be_installed(client):
    """Everything a home-screen icon needs, served and permitted by the CSP."""
    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["name"] == "Royals" and body["display"] == "standalone"

    for icon in body["icons"]:
        got = client.get(icon["src"])
        assert got.status_code == 200, icon["src"]
        assert got.headers["content-type"] == "image/png"

    csp = client.get("/").headers["Content-Security-Policy"]
    assert "manifest-src 'self'" in csp, "the browser will refuse to fetch it otherwise"
    assert "unsafe-inline" not in csp


# ---------------------------------------------------------------------------
# Bounds on what a stranger can make this process do
# ---------------------------------------------------------------------------

def test_an_oversized_body_is_refused_even_without_a_content_length(client, game):
    """The probe that found this, kept as the test that would have caught it.

    The limit used to be enforced by reading Content-Length, which a chunked request
    simply does not send -- so the check was skipped and the whole body was buffered.
    Measured against the running server: 5 MB reached the JSON parser and came back 422
    rather than 413. Unauthenticated, no game id needed, and no upper bound.
    """
    url = f"/api/games/{game['id']}/enter"
    huge = b'{"square":"' + b"a" * 200_000 + b'"}'

    declared = client.post(url, content=huge,
                           headers={"Content-Type": "application/json"})
    assert declared.status_code == 413, "an honest Content-Length must still be refused"

    def chunks():
        for i in range(0, len(huge), 8192):
            yield huge[i:i + 8192]

    streamed = client.post(url, content=chunks(),
                           headers={"Content-Type": "application/json"})
    assert streamed.status_code == 413, (
        "a body that declines to declare its size is still a body", streamed.status_code)

    # and a real request is untouched by any of it
    assert client.get(f"/api/games/{game['id']}").status_code == 200


def test_a_body_at_the_limit_still_works(client, game):
    """The limit has to admit the requests it exists to permit."""
    res = client.post(f"/api/games/{game['id']}/enter",
                      json={"square": game["entering"]["options"][0]})
    assert res.status_code == 200


def test_api_responses_vary_on_the_seat_header(client, duel):
    """`GET /api/games/{id}` answers differently per seat, so a cache must be told.

    Nothing caches it today. The header is what keeps that harmless the first time
    something is put in front of this.
    """
    state, blue, red = duel
    res = client.get(f"/api/games/{state['id']}", headers=blue)
    assert res.headers["Vary"] == "X-Royals-Seat"
    assert res.headers["Cache-Control"] == "no-store"

    # and it is not decorative: the two seats really do get different answers
    state = enter_all(client, state, {0: blue, 1: red})
    mover = blue if state["sideToMove"] == 0 else red
    waiter = red if state["sideToMove"] == 0 else blue
    assert "origins" in client.get(f"/api/games/{state['id']}", headers=mover).json()
    assert "origins" not in client.get(f"/api/games/{state['id']}", headers=waiter).json()


def test_the_api_is_rate_limited_as_a_whole(client, game, monkeypatch):
    """Reads were bounded by nothing, and a read regenerates every legal move."""
    from royals_web import main as M
    monkeypatch.setattr(M, "API_LIMIT", 5)

    codes = [client.get(f"/api/games/{game['id']}").status_code for _ in range(12)]
    assert 429 in codes, "a client can hammer move generation forever"
    assert codes[0] == 200, "and the limit is not so tight it refuses the first request"


def test_the_sweep_bounds_the_database_by_count_as_well_as_age(db, monkeypatch):
    """Age alone bounds nothing: the create limit allows thousands of games a day and
    the youngest of them is a month from expiring."""
    from royals_web import persist
    monkeypatch.setattr(persist, "MAX_ROWS", 3)

    made = []
    for seed in range(6):
        g = played_out(seed=seed + 40, plies=2)
        g.updated_at = 1_000_000 + seed        # oldest first
        db.save(g)
        made.append(g.id)
    assert db.count() == 6

    db.sweep(now=1_000_010)
    assert db.count() == 3, "the ceiling is what makes the disk a fixed cost"
    assert db.load(made[0]) is None, "oldest-by-last-touched goes first"
    assert db.load(made[-1]) is not None, "and the most recent survives"


def test_a_game_cannot_run_forever(monkeypatch):
    """The ko rule makes a game finite, but only in the sense that chess is."""
    import random
    monkeypatch.setattr(G, "MAX_PLIES", 20)

    rng = random.Random(5)
    game = pure_game(entry_seed=5)
    while game.phase == "entering":
        options = G.entering_options(game.board, game.entry_side, game.entry_piece)
        G.place(game, rng.choice(options), side=game.entry_side)

    for _ in range(200):
        if game.phase != "playing":
            break
        contr = game.turn % 2
        legal = G.legal_moves(game.board, contr, game.ko_set())
        G.play_move(game, rng.choice(legal) if legal else None, side=contr)

    assert game.phase == "over"
    assert len(game.moves) == 20
    assert (game.result, game.termination) == ("draw", "ply_limit")

    # and it survives a round trip, since replay reaches the cap by itself
    back = G.replay(id=game.id, mode=game.mode, ai_depth=game.ai_depth,
                    entry_seed=game.entry_seed, entry_noise=game.entry_noise,
                    moves=" ".join(game.moves), ply=len(game.moves), seats=game.seats,
                    result=game.result, termination=game.termination)
    assert (back.result, back.termination) == ("draw", "ply_limit")


# ---------------------------------------------------------------------------
# Invitations, and the one string a stranger writes
# ---------------------------------------------------------------------------

def test_a_name_is_filtered_at_the_door():
    """The only attacker-controlled text in the application."""
    assert G.clean_name("Emerson") == "Emerson"
    assert G.clean_name("  Emerson   Jeffery  ") == "Emerson Jeffery"
    assert G.clean_name("Zoë O'Neill-Smith") == "Zoë O'Neill-Smith"

    # markup loses everything that makes it markup
    cleaned = G.clean_name("<script>alert(1)</script>")
    assert "<" not in cleaned and ">" not in cleaned and "/" not in cleaned

    assert len(G.clean_name("a" * 500)) == G.NAME_MAX
    assert G.clean_name("\u202Eevil") == "evil", "no bidirectional overrides"
    assert G.clean_name("\x00\x07") is None
    assert G.clean_name("") is None and G.clean_name(None) is None
    assert G.clean_name(12345) is None, "not every client sends a string"


def test_a_name_reaches_the_invitation_escaped(client):
    made = client.post("/api/games", json={"mode": "human", "side": 0,
                                           "name": '<b>Em"erson</b>'}).json()
    gid = made["state"]["id"]

    assert made["state"]["seats"]["0"]["name"] == "bEmersonb"

    page = client.get(f"/join/{gid}").text
    assert "bEmersonb has invited you to play Royals" in page
    assert "<b>" not in page.split("<body")[0], "no attacker markup in the head"


def test_the_invitation_page_describes_the_game_and_claims_nothing(client):
    made = client.post("/api/games",
                       json={"mode": "human", "side": 0, "name": "Emerson"}).json()
    gid, invite = made["state"]["id"], made["inviteToken"]

    page = client.get(f"/join/{gid}")
    assert page.status_code == 200
    assert 'property="og:title" content="Emerson has invited you to play Royals"' in page.text
    assert 'property="og:image"' in page.text
    assert "twitter:card" in page.text

    # a previewer fetching it takes nothing
    assert client.get(f"/api/games/{gid}").json()["phase"] == "waiting"
    assert client.post(f"/api/games/{gid}/join",
                       json={"invite": invite}).status_code == 200


def test_a_used_invitation_says_so_in_its_preview(client):
    made = client.post("/api/games", json={"mode": "human", "side": 0}).json()
    gid = made["state"]["id"]
    client.post(f"/api/games/{gid}/join", json={"invite": made["inviteToken"]})

    page = client.get(f"/join/{gid}").text
    assert "already been used" in page


def test_the_ordinary_pages_still_carry_a_card(client):
    for path in ("/", "/g/" + "0" * 32):
        page = client.get(path)
        assert page.status_code == 200
        assert 'property="og:title" content="Royals"' in page.text
        assert page.headers["Cache-Control"] == "no-store"


def test_an_unknown_or_malformed_game_id_is_still_a_page(client):
    """A preview must not be able to make the server answer with an error."""
    for path in ("/join/not-a-game-id", "/join/" + "0" * 32, "/join/../../etc/passwd"):
        res = client.get(path)
        assert res.status_code in (200, 404), (path, res.status_code)
        if res.status_code == 200:
            assert "og:title" in res.text


# ---------------------------------------------------------------------------
# Portability: the sqlite cursor rule
# ---------------------------------------------------------------------------

def test_persist_never_executes_without_closing():
    """No bare `self._conn.execute` in persist.py outside the two helpers.

    A source check rather than a behavioural one, because the behaviour it guards only
    misbehaves on an interpreter this suite may not be running under. CPython frees the
    cursor `execute` returns as soon as the last reference to it goes, and that finalises
    the statement, so a commit on the next line sees a quiet connection. PyPy does not
    refcount. Every write in this file failed under it with

        sqlite3.OperationalError: cannot commit transaction - SQL statements in progress

    which is a whole server that will not start, discovered only by running it there. The
    fix was to route everything through `_run` and `_query`, which close their cursors;
    this is what stops the next `execute` from quietly undoing that under CPython, where
    it would pass every test in this file.
    """
    import inspect
    import re

    from royals_web import persist

    source = inspect.getsource(persist)
    # The helpers are the two places allowed to touch the connection directly, and they
    # are found by name so that renaming one fails here rather than silently exempting it.
    helpers = re.findall(r"\n    def (_run|_query)\(.*?(?=\n    def |\Z)", source, re.S)
    assert len(helpers) == 2, "persist._run/_query have been renamed or removed"

    offenders = []
    for number, line in enumerate(source.splitlines(), 1):
        if "_conn.execute(" not in line: continue
        # executescript is a different call with no cursor to leak, and the helpers'
        # own two lines are the point of the exercise
        stripped = line.strip()
        if stripped.startswith("cur = self._conn.execute("): continue
        offenders.append((number, stripped))

    assert not offenders, (
        "persist.py calls _conn.execute directly at %s -- use _run or _query, which "
        "close the cursor. See the docstring above this test." % offenders)


# ---------------------------------------------------------------------------
# The payloads the browser really sends
# ---------------------------------------------------------------------------
# Every test above builds its request by hand, and hand-built requests omit the fields a
# form leaves empty. A browser does not: an empty name box sends `name: null`, which is a
# different statement from not mentioning it, and `name: str = Field(default=None)` --
# which reads as optional -- rejects it. Both creating a game and accepting an invitation
# failed for every real visitor while all of the above passed.

def test_an_empty_optional_field_may_be_sent_as_null(client):
    """A form with nothing typed in it sends null, and that is not an error."""
    made = client.post("/api/games", json={"mode": "human", "side": 0, "name": None})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["state"]["seats"]["0"]["name"] is None

    joined = client.post(f"/api/games/{body['state']['id']}/join",
                         json={"invite": body["inviteToken"], "name": None})
    assert joined.status_code == 200, joined.text
    assert joined.json()["state"]["phase"] == "entering"


def test_a_game_against_the_computer_starts_with_a_null_name(client):
    res = client.post("/api/games", json={"mode": "ai", "side": 0,
                                          "difficulty": "novice", "noise": 0.5,
                                          "name": None})
    assert res.status_code == 200, res.text


def test_a_move_may_spell_out_the_field_it_is_not_using(client, game):
    """A move carries a target or a direction. Naming the other one as null is not an
    error, and a client that serialises its whole shape should not be refused."""
    state = play_to_move_phase(client, game)
    origin = state["origins"][0]
    m = client.get(f"/api/games/{state['id']}/moves",
                   params={"origin": origin}).json()["moves"][0]

    body = {"kind": m["kind"], "origin": m["origin"], "pris": m.get("pris", False),
            "target": None, "dir": None, "expectedVersion": None}
    body["dir" if m["kind"] == "break" else "target"] = \
        m["dir"] if m["kind"] == "break" else m["target"]

    res = client.post(f"/api/games/{state['id']}/move", json=body)
    assert res.status_code == 200, res.text


def test_a_schema_refusal_is_a_sentence_and_not_a_shrug(client, game):
    """What the client renders when the server refuses on shape.

    FastAPI puts a list of objects in `detail`, and handing that to `new Error` prints
    "[object Object]" -- which is what a player was shown instead of anything useful.
    The client formats it now, so the least this must do is name the field.
    """
    res = client.post(f"/api/games/{game['id']}/enter", json={"square": 12345})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert isinstance(detail, list)
    assert any("square" in (d.get("loc") or []) for d in detail), (
        "the refusal must say which field, or the client cannot say anything useful")
