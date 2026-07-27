// The click-to-move loop, and everything the page does.
//
// It mirrors the shape RoyalsGUI settled on: a window can never block, so the state of
// a turn is written down rather than kept on a call stack, and every click advances it
// by one step. Here the same is true twice over, because half the state lives on the
// server -- so the client holds only what it needs to know which click comes next:
//
//   state     the last game state the server sent
//   selected  the origin the player has clicked, or null while choosing one
//   moves     the legal moves out of that origin, as the server listed them
//   carrying  whether the chosen move should drag prisoners along
//
// Nothing here decides legality. The server regenerates every legal move on submission
// whether or not this file asked first.

import { BoardView, looksWrapped } from "/static/board.js";

const $ = (id) => document.getElementById(id);

const el = {
  setup: $("setup"), game: $("game"), legend: $("legend"),
  board: $("board"), overlay: $("overlay"), overlayText: $("overlay-text"),
  status: $("status"), hint: $("hint"), movelist: $("movelist"),
  pass: $("pass"), carry: $("carry"), cancel: $("cancel"),
  resign: $("resign"), newgame: $("newgame"), start: $("start"),
  sideChoice: $("side-choice"), diffChoice: $("difficulty-choice"),
  diffHint: $("difficulty-hint"), noise: $("noise"),
};

const view = new BoardView(el.board);

let state = null;
let selected = null;
let moves = [];
let carrying = false;
let busy = false;
let setup = { side: 0, difficulty: "strong" };

const SIDE_NAME = ["blue", "red"];

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `request failed (${res.status})`);
  return body;
}

// ---------------------------------------------------------------------------
// Setup screen
// ---------------------------------------------------------------------------

const DIFFICULTY_BLURB = {
  novice: "Looks two moves ahead. A gentle introduction.",
  casual: "Three moves ahead. Will punish an obvious blunder.",
  strong: "Four moves ahead. A real opponent.",
  expert: "Five moves ahead. Takes a second to think.",
  royal: "Six moves ahead, and slow about it. Good luck.",
};

async function buildSetup() {
  const { difficulties, default: fallback } = await api("/api/difficulties");
  setup.difficulty = fallback;

  el.diffChoice.replaceChildren(...difficulties.map(({ name }) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (name === fallback ? " selected" : "");
    b.dataset.difficulty = name;
    b.textContent = name[0].toUpperCase() + name.slice(1);
    return b;
  }));
  el.diffHint.textContent = DIFFICULTY_BLURB[fallback] || "";
}

el.sideChoice.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-side]");
  if (!b) return;
  setup.side = Number(b.dataset.side);
  [...el.sideChoice.children].forEach((c) => c.classList.toggle("selected", c === b));
});

el.diffChoice.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-difficulty]");
  if (!b) return;
  setup.difficulty = b.dataset.difficulty;
  [...el.diffChoice.children].forEach((c) => c.classList.toggle("selected", c === b));
  el.diffHint.textContent = DIFFICULTY_BLURB[setup.difficulty] || "";
});

el.start.addEventListener("click", async () => {
  el.start.disabled = true;
  el.start.textContent = "Setting up…";
  try {
    const fresh = await api("/api/games", {
      method: "POST",
      body: JSON.stringify({
        side: setup.side,
        difficulty: setup.difficulty,
        noise: Number(el.noise.value) / 100,
      }),
    });
    el.setup.classList.add("hidden");
    el.game.classList.remove("hidden");
    el.legend.classList.remove("hidden");
    apply(fresh);
    requestAnimationFrame(() => { view.resize(); render(); });
  } catch (err) {
    el.diffHint.textContent = err.message;
  } finally {
    el.start.disabled = false;
    el.start.textContent = "Start";
  }
});

// ---------------------------------------------------------------------------
// Applying a new state
// ---------------------------------------------------------------------------

function apply(fresh) {
  state = fresh;
  selected = null;
  moves = [];
  carrying = false;
  render();
}

// ---------------------------------------------------------------------------
// Clicks
// ---------------------------------------------------------------------------

el.board.addEventListener("click", (event) => {
  if (busy || !state || state.phase === "over") return;
  const alg = view.hit(event.clientX, event.clientY);
  if (!alg) return;

  if (state.phase === "entering") return onEnterClick(alg);
  if (state.phase === "playing" && state.awaitingHuman) return onPlayClick(alg);
});

async function onEnterClick(alg) {
  if (!state.awaitingHuman) return;
  if (!state.entering.options.includes(alg)) {
    setHint("You cannot enter a piece there — it must not touch anything you already control.");
    return;
  }
  await withBusy(() => api(`/api/games/${state.id}/enter`, {
    method: "POST",
    body: JSON.stringify({ square: alg }),
  }));
}

async function onPlayClick(alg) {
  // second click: a destination?
  if (selected) {
    const chosen = moves.find((m) => m.target === alg || (m.kind === "break" && m.at === alg));
    if (chosen) return submit(chosen);
    if (alg === selected) return clearSelection();
  }

  // first click: pick up a square
  if (!state.origins || !state.origins.includes(alg)) {
    setHint(selected
      ? "Not a legal destination. Click the same square again to put it down."
      : "Nothing of yours there with a move to make.");
    return;
  }
  await selectOrigin(alg, false);
}

async function selectOrigin(alg, pris) {
  try {
    busy = true;
    const res = await api(
      `/api/games/${state.id}/moves?origin=${encodeURIComponent(alg)}&pris=${pris}`);
    selected = alg;
    carrying = pris;
    moves = res.moves.map(decorate);
    el.carry.classList.toggle("hidden", !res.hasPrisonerVariant);
    el.carry.textContent = pris ? "Leave prisoners" : "Bring prisoners";
    el.cancel.classList.remove("hidden");
    setHint(describeOptions(moves));
  } catch (err) {
    setHint(err.message);
  } finally {
    busy = false;
    render();
  }
}

// A break has no destination square: it scatters the stack along a heading. Give each
// one the adjacent square in that direction so there is something to click.
function decorate(m) {
  if (m.kind !== "break") return m;
  const DELTA = { d: [0, -1], u: [0, 1], l: [-1, 0], r: [1, 0] };
  const FILES = "abcdefg";
  const [df, dr] = DELTA[m.dir];
  const f = FILES.indexOf(m.origin[0]) + df;
  const r = parseInt(m.origin[1], 10) + dr;
  return { ...m, at: (f >= 0 && f < 7 && r >= 1 && r <= 7) ? FILES[f] + r : null };
}

function describeOptions(list) {
  if (!list.length) return "Nothing legal from there.";
  const counts = list.reduce((acc, m) => (acc[m.kind] = (acc[m.kind] || 0) + 1, acc), {});
  const parts = Object.entries(counts).map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`);
  return "Click a highlighted square. " + parts.join(", ") + ".";
}

function clearSelection() {
  selected = null;
  moves = [];
  carrying = false;
  el.carry.classList.add("hidden");
  el.cancel.classList.add("hidden");
  setHint("");
  render();
}

async function submit(move) {
  const body = { kind: move.kind, origin: move.origin, pris: !!move.pris };
  if (move.kind === "break") body.dir = move.dir;
  else body.target = move.target;

  await withBusy(() => api(`/api/games/${state.id}/move`, {
    method: "POST",
    body: JSON.stringify(body),
  }));
}

async function withBusy(fn) {
  busy = true;
  setStatus("Thinking…", null);
  try {
    apply(await fn());
  } catch (err) {
    setHint(err.message);
  } finally {
    busy = false;
    render();
  }
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

el.cancel.addEventListener("click", clearSelection);

el.carry.addEventListener("click", () => {
  if (selected) selectOrigin(selected, !carrying);
});

el.pass.addEventListener("click", () =>
  withBusy(() => api(`/api/games/${state.id}/pass`, { method: "POST" })));

el.resign.addEventListener("click", () => {
  if (!state || state.phase === "over") return;
  if (!confirm("Resign this game?")) return;
  withBusy(() => api(`/api/games/${state.id}/resign`, { method: "POST" }));
});

el.newgame.addEventListener("click", () => {
  state = null;
  el.game.classList.add("hidden");
  el.legend.classList.add("hidden");
  el.setup.classList.remove("hidden");
  el.overlay.classList.add("hidden");
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function setStatus(text, side) {
  el.status.textContent = text;
  el.status.className = "status" + (side === null || side === undefined ? "" : " " + SIDE_NAME[side]);
}

function setHint(text) { el.hint.textContent = text || " "; }

function marksFor() {
  const marks = new Map();
  if (!state) return marks;

  if (state.phase === "entering" && state.awaitingHuman) {
    state.entering.options.forEach((a) => marks.set(a, "enter"));
    return marks;
  }

  if (state.phase !== "playing" || !state.awaitingHuman) return marks;

  if (selected) {
    for (const m of moves) {
      if (m.kind === "break") { if (m.at) marks.set(m.at, "break"); continue; }
      marks.set(m.target, m.kind === "jump" && looksWrapped(m.origin, m.target)
        ? "wrap" : m.kind);
    }
  } else {
    (state.origins || []).forEach((a) => marks.set(a, "origin"));
  }
  return marks;
}

function render() {
  view.draw(state, marksFor(), selected, state ? state.lastMove : []);
  if (!state) return;

  renderMoveList();

  el.pass.classList.toggle("hidden",
    !(state.phase === "playing" && state.awaitingHuman && state.mustPass));

  if (state.phase === "over") {
    el.overlay.classList.remove("hidden");
    el.overlayText.textContent = describeEnd();
    setStatus(describeEnd(), null);
    setHint("");
    el.carry.classList.add("hidden");
    el.cancel.classList.add("hidden");
    return;
  }
  el.overlay.classList.add("hidden");

  if (state.phase === "entering") {
    const e = state.entering;
    setStatus(`${cap(SIDE_NAME[e.side])} ${e.piece}`, e.side);
    setHint(state.awaitingHuman
      ? `Click a highlighted square to enter your ${e.piece}.  (${e.step} of ${e.total})`
      : `The computer is placing its ${e.piece}…  (${e.step} of ${e.total})`);
    return;
  }

  const side = state.sideToMove;
  setStatus(`${cap(SIDE_NAME[side])} to move`, side);
  if (!state.awaitingHuman) { setHint("The computer is thinking…"); return; }
  if (state.mustPass) { setHint("You have no legal move — every one would repeat an earlier position. You must pass."); return; }
  if (!selected) setHint("Click one of your highlighted squares.");
}

function describeEnd() {
  const r = state.result;
  const how = {
    gather: "by gathering all six pieces",
    resign: "by resignation",
    double_pass: "— neither side could move",
    no_moves: "— no legal moves remained",
  }[state.termination] || "";

  if (r === "draw") return `Draw ${how}`.trim() + ".";
  const won = SIDE_NAME.indexOf(r) === state.humanSide;
  return `${cap(r)} wins ${how}`.trim() + `. ${won ? "That is you." : ""}`.trimEnd();
}

function renderMoveList() {
  const items = state.moves.map((ran, i) => {
    const li = document.createElement("li");
    li.textContent = ran;
    if (ran.startsWith("@")) li.className = "enter";
    else li.className = SIDE_NAME[i % 2 === 0 ? 1 : 0];
    return li;
  });
  el.movelist.replaceChildren(...items);
  el.movelist.scrollTop = el.movelist.scrollHeight;
}

const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;

// ---------------------------------------------------------------------------

window.addEventListener("resize", () => { view.resize(); render(); });
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => render());
}

buildSetup().catch((err) => { el.diffHint.textContent = err.message; });
