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

    request = FakeRequest({"fly-client-ip": "9.9.9.9",
                           "x-forwarded-for": "8.8.8.8, 10.0.0.1"}, "10.0.0.7")

    monkeypatch.setattr(M, "TRUSTED_PROXY", False)
    assert M.client_key(request) == "10.0.0.7", "an untrusted header is not an identity"

    monkeypatch.setattr(M, "TRUSTED_PROXY", True)
    assert M.client_key(request) == "9.9.9.9"

    only_xff = FakeRequest({"x-forwarded-for": "8.8.8.8, 10.0.0.1"}, "10.0.0.7")
    assert M.client_key(only_xff) == "8.8.8.8", "the client is the leftmost entry"


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
