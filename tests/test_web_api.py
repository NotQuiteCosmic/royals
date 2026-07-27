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
    """A game at the point where the human has a move to make."""
    res = client.post("/api/games", json={"side": 0, "difficulty": "novice", "noise": 0.5})
    assert res.status_code == 200, res.text
    return res.json()


def play_to_move_phase(client, state):
    """Enter pieces (choosing the first legal option each time) until play begins."""
    while state["phase"] == "entering":
        assert state["awaitingHuman"], "the server should stop for the human"
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
    assert game["awaitingHuman"] is True
    assert game["humanSide"] == 0
    assert len(game["board"]) == 49
    assert game["entering"]["options"]


def test_playing_through_entering_reaches_play(client, game):
    state = play_to_move_phase(client, game)
    assert state["phase"] == "playing"
    assert state["awaitingHuman"] is True
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
    assert after["awaitingHuman"] or after["phase"] == "over"


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
    human = state["humanSide"]

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
            "board": [0] * 49, "humanSide": 1, "result": "blue"}
    if move["kind"] == "break":
        body["dir"] = move["dir"]
    else:
        body["target"] = move["target"]

    res = client.post(f"/api/games/{state['id']}/move", json=body)
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["board"] != [None] * 49
    assert after["humanSide"] == 0
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
    game = G.new_game(human_side=0, difficulty="novice", entry_seed=1)
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
    game = G.new_game(human_side=0, difficulty="novice", entry_seed=7)
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
        g = G.new_game(human_side=0, difficulty="novice", entry_seed=seed)
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
    game = G.new_game(human_side=0, difficulty="novice", entry_seed=3)
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

    state = client.post("/api/games",
                        json={"side": 0, "difficulty": "novice", "noise": 0.5}).json()

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
        g = G.new_game(human_side=0, difficulty="novice", entry_seed=i)
        s.put(g)
        made.append(g.id)
    assert len(s) == 5
    assert s.get(made[0]) is None
    assert s.get(made[-1]) is not None
