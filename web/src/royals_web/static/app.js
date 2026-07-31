// The click-to-move loop, and everything the page does.
//
// It mirrors the shape RoyalsGUI settled on: a window can never block, so the state of
// a turn is written down rather than kept on a call stack, and every click advances it
// by one step. Here the same is true twice over, because half the state lives on the
// server -- so the client holds only what it needs to know which click comes next:
//
//   state     the last game state the server sent
//   selected  the origin the player has clicked, or null while choosing one
//   moves     the legal moves out of that origin -- answered in the page by engine.js
//             and then confirmed by the server, which has the last word
//   carrying  whether the chosen move should drag prisoners along
//
// Nothing here decides legality. The server regenerates every legal move on submission
// whether or not this file asked first.
//
// Two players adds one idea and no more: the page has a *seat*, proved by a token, and
// everything it says is from that seat's point of view. `state.awaitingYou` replaces
// what used to be "is it the human's turn", and a page with no token is a spectator --
// which is a real thing to be, since anyone can be sent the link.

import { BoardView, looksWrapped, algToIndex, FILES } from "/static/board.js";
import * as engine from "/static/engine.js";

const $ = (id) => document.getElementById(id);

// Which engine is answering, in the corner of the header.
//
// `engine.ready` never rejects -- it resolves false when the module could not be loaded, and
// the page keeps working by asking the server -- so this needs no catch and has no failure
// path of its own. It resolves *after* the badge has already rendered in its muted state,
// which is the point: the honest answer is shown first and upgraded only once earned.
engine.ready.then((local) => {
  const badge = $("engine-badge");
  if (!badge) return;
  badge.classList.toggle("is-local", local);
  badge.querySelector(".engine-badge-tag").textContent = local ? "RS" : "PY";
  badge.title = local
    ? "Playing with the engine built into this page — squares light up instantly."
    : "Asking the server for legal moves — a little slower, exactly the same game.";
});

const el = {
  setup: $("setup"), game: $("game"), legend: $("legend"), share: $("share"),
  board: $("board"), marks: $("marks"), overlay: $("overlay"), overlayText: $("overlay-text"),
  status: $("status"), hint: $("hint"), movelist: $("movelist"),
  pass: $("pass"), carry: $("carry"), cancel: $("cancel"),
  resign: $("resign"), newgame: $("newgame"), start: $("start"),
  sideChoice: $("side-choice"), sideRandom: $("side-random"),
  diffChoice: $("difficulty-choice"), diffHint: $("difficulty-hint"), noise: $("noise"),
  opponentChoice: $("opponent-choice"), opponentHint: $("opponent-hint"),
  aiOptions: $("ai-options"), noiseField: $("noise-field"), setupError: $("setup-error"),
  entryChoice: $("entry-choice"), entryHint: $("entry-hint"),
  inviteUrl: $("invite-url"), copyInvite: $("copy-invite"), sendInvite: $("send-invite"),
  shareStatus: $("share-status"), resumeUrl: $("resume-url"),
  inviteAgain: $("invite-again"),
  invite: $("invite"), inviteWho: $("invite-who"), inviteSide: $("invite-side"),
  inviteAccept: $("invite-accept"), inviteNote: $("invite-note"),
  nameField: $("name-field"), playerName: $("player-name"),
  save: $("save"), reviewOpen: $("review-open"),
  reviewBar: $("review"), rvFirst: $("rv-first"), rvPrev: $("rv-prev"),
  rvSlider: $("rv-slider"), rvNext: $("rv-next"), rvLast: $("rv-last"),
  rvNumber: $("rv-number"), rvPly: $("rv-ply"), rvExit: $("rv-exit"),
  open: $("open"), openFile: $("open-file"), openStatus: $("open-status"),
};

const view = new BoardView(el.board);

let state = null;
let selected = null;
let moves = [];
let carrying = false;
let busy = false;
// Whether the break arrows are showing, and which square is currently asking push-or-free.
// Both are part of "which click comes next" and so live here with the rest of it; both are
// dropped by clearSelection, which every path out of a turn already goes through.
let breakOpen = false;
let chooser = null;
// Which pick the page is showing. The local engine answers in microseconds and the server in
// tens of milliseconds, so answers can now arrive after the question stopped being the one
// being asked; this is what lets a stale one be dropped. Bumped wherever a selection ends.
let pickId = 0;
let seatToken = null;
let inviteToken = null;
// The invitation being considered, while the reader decides. Nothing is claimed until
// they press Accept.
let pending = null;
// The game being walked through, or null while one is being played. Everything the review
// needs is in here -- the move list, and the board after every ply -- so stepping touches
// no other state and asks the server nothing. See "Saving and reviewing" below.
let review = null;
let setup = { mode: "ai", side: 0, difficulty: "strong", randomEntry: false };

const SIDE_NAME = ["blue", "red"];

// ---------------------------------------------------------------------------
// The seat token
// ---------------------------------------------------------------------------
// Held per game, because one browser can legitimately hold both ends of a game (you
// testing your own link) and several games at once. localStorage rather than a cookie:
// the server authenticates with a header specifically so that a browser never attaches
// this to a cross-site request on its own.

const seatKey = (id) => `royals:seat:${id}`;
const loadSeat = (id) => { try { return localStorage.getItem(seatKey(id)); } catch { return null; } };
const saveSeat = (id, token) => { try { localStorage.setItem(seatKey(id), token); } catch {} };

// The invitation is kept here too, and it has to be: the server stores only its hash, so
// there is no endpoint that can give it back. Without this, closing the tab before your
// opponent joins would lose the link and the only remedy would be a new game.
const inviteKey = (id) => `royals:invite:${id}`;
const loadInvite = (id) => { try { return localStorage.getItem(inviteKey(id)); } catch { return null; } };
const saveInvite = (id, token) => { try { localStorage.setItem(inviteKey(id), token); } catch {} };
const dropInvite = (id) => { try { localStorage.removeItem(inviteKey(id)); } catch {} };

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

async function api(path, options) {
  const headers = { "Content-Type": "application/json" };
  if (seatToken) headers["X-Royals-Seat"] = seatToken;

  const res = await fetch(path, { headers, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(describeError(body, res.status));
    err.status = res.status;
    throw err;
  }
  return body;
}

// The server's refusals are a sentence in `detail` -- except the schema's, which are a
// list of objects describing which field was wrong. Handing that list to `new Error`
// stringifies it to "[object Object]", which is what a player saw instead of being told
// anything at all. Worth rendering properly rather than hiding: if this ever shows up
// again it should name the field.
function describeError(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d) => {
        const where = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        return where ? `${where}: ${d.msg}` : d.msg;
      })
      .filter(Boolean)
      .join("; ") || `request failed (${status})`;
  }
  return `request failed (${status})`;
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------
// Three shapes of URL:  /  is the setup screen,  /g/<id>  is a game this browser may
// already have a seat in, and  /join/<token>  is an invitation.

async function route() {
  const path = location.pathname;

  const joining = path.match(/^\/join\/([A-Za-z0-9_.-]+)\/?$/);
  if (joining) return showInvitation(joining[1]);

  const playing = path.match(/^\/g\/([a-f0-9]{32})\/?$/);
  if (playing) return openGame(playing[1]);

  showSetup();
}

// A resume link carries the seat token in the fragment. Fragments are never sent to the
// server, so the token stays out of access logs, out of Referer headers, and out of
// anything downstream that records URLs. It is stashed and stripped immediately so a
// later copy of the address bar doesn't hand the seat to somebody.
function takeTokenFromFragment(gameId) {
  const match = location.hash.match(/^#s=([A-Za-z0-9_-]+)$/);
  if (!match) return;
  saveSeat(gameId, match[1]);
  history.replaceState(null, "", `/g/${gameId}`);
}

async function openGame(gameId) {
  takeTokenFromFragment(gameId);
  seatToken = loadSeat(gameId);
  inviteToken = loadInvite(gameId);
  try {
    const fresh = await api(`/api/games/${gameId}`);
    apply(fresh);
    showGame();
    // Reopened before the opponent arrived: the link is still the thing they need.
    if (fresh.phase === "waiting" && inviteToken) showShare(gameId);
  } catch (err) {
    showSetup();
    el.setupError.textContent =
      err.status === 404 ? "That game has expired or never existed." : err.message;
  }
}

// Showing an invitation, which is emphatically not the same as accepting one.
//
// This used to POST the moment the page loaded, and that was wrong twice over. Somebody
// who opens a link they were sent has no idea yet what it is -- an invitation that takes
// the seat before introducing itself is asking to be trusted without saying what for. And
// because a seat is held by one browser, glancing at the link on a laptop took it from
// the phone the reader meant to play on.
//
// So this fetches the game -- a public read, no token needed -- describes it, and waits.
async function showInvitation(linkToken) {
  showSetup();
  el.setup.classList.add("hidden");
  el.setupError.textContent = "";

  const [gameId, invite] = splitInvite(linkToken);
  if (!gameId || !invite) {
    el.setup.classList.remove("hidden");
    el.setupError.textContent = "That invitation link is incomplete — ask for a new one.";
    return;
  }

  let game;
  try {
    game = await api(`/api/games/${gameId}`);
  } catch (err) {
    el.setup.classList.remove("hidden");
    el.setupError.textContent = err.status === 404
      ? "That game has expired or never existed."
      : err.message;
    return;
  }

  // Already sitting here: nothing to accept, just carry on playing.
  if (loadSeat(gameId)) return openGame(gameId);

  if (game.phase !== "waiting") {
    el.setup.classList.remove("hidden");
    el.setupError.textContent =
      "Both seats in that game are taken. You can still watch it.";
    setTimeout(() => openGame(gameId), 1200);
    return;
  }

  pending = { gameId, invite };
  const host = game.seats["0"].name || game.seats["1"].name;
  const free = game.seats["0"].claimed ? 1 : 0;

  // textContent, never innerHTML: this is a name somebody else typed.
  el.inviteWho.textContent = host
    ? `${host} has invited you to play Royals.`
    : "Someone has invited you to play Royals.";
  el.inviteSide.textContent = free === 0 ? "Blue, who enters first" : "Red, who moves first";
  el.invite.classList.remove("hidden");
}

el.inviteAccept.addEventListener("click", async () => {
  if (!pending) return;
  const { gameId, invite } = pending;
  el.inviteAccept.disabled = true;
  el.inviteAccept.textContent = "Joining…";
  try {
    // Sent before claiming: the browser may already hold this seat, in which case the
    // server hands it straight back instead of spending the invitation on us.
    seatToken = loadSeat(gameId);
    const res = await api(`/api/games/${gameId}/join`, {
      method: "POST",
      body: JSON.stringify(withName({ invite })),
    });
    // A null seatToken is exactly that case -- keep the one already stored.
    if (res.seatToken) saveSeat(gameId, res.seatToken);
    seatToken = loadSeat(gameId);
    pending = null;
    history.replaceState(null, "", `/g/${gameId}`);
    el.invite.classList.add("hidden");
    apply(res.state);
    showGame();
  } catch (err) {
    el.inviteNote.textContent = err.status === 409
      ? "Somebody accepted this invitation first."
      : err.message;
  } finally {
    el.inviteAccept.disabled = false;
    el.inviteAccept.textContent = "Accept and play";
  }
});

const nameFromField = () => (el.playerName.value || "").trim().slice(0, 24);

// An absent name is an absent field, not a null one. Sending `name: null` is a different
// statement from not mentioning it, and the server is entitled to treat it as one.
function withName(body) {
  const name = nameFromField();
  return name ? { ...body, name } : body;
}

// A link says which game it is for and proves the right to join it, and those two things
// are kept apart on purpose. The game id goes in the path, because the server has to know
// which game to describe when a messenger asks it for a preview, and because an id is not
// a credential -- anyone holding one can already watch. The token goes in the fragment,
// which browsers never send, so the part that *is* a credential reaches no access log and
// no CDN. Older links put both in the path; those still work.
function splitInvite(linkToken) {
  const fragment = location.hash.replace(/^#/, "");
  if (fragment && !linkToken.includes(".")) return [linkToken, fragment];

  const dot = linkToken.indexOf(".");
  if (dot < 0) return [linkToken, null];
  return [linkToken.slice(0, dot), linkToken.slice(dot + 1)];
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

function showSetup() {
  stopPolling();
  state = null;
  review = null;
  el.reviewBar.classList.add("hidden");
  el.movelist.classList.remove("steppable");
  el.game.classList.add("hidden");
  el.legend.classList.add("hidden");
  el.share.classList.add("hidden");
  el.invite.classList.add("hidden");
  el.setup.classList.remove("hidden");
  el.overlay.classList.add("hidden");
}

function showGame() {
  el.setup.classList.add("hidden");
  el.invite.classList.add("hidden");
  el.game.classList.remove("hidden");
  el.legend.classList.remove("hidden");
  requestAnimationFrame(() => { view.resize(); render(); });
}

// ---------------------------------------------------------------------------
// Setup screen
// ---------------------------------------------------------------------------

// One line per rung of DIFFICULTIES, and the ply counts are the ones in that table -- they
// were each two short of it, describing a ladder the server had already climbed past.
const DIFFICULTY_BLURB = {
  novice: "Looks three moves ahead. A gentle introduction.",
  casual: "Five moves ahead. Will punish an obvious blunder.",
  strong: "Seven moves ahead. A real opponent.",
  expert: "Eight moves ahead. Takes a second to think.",
  royal: "Nine moves ahead, and slow about it. Good luck.",
  dragon: "Ten moves ahead. The deepest there is, and it will think for something like "
        + "twenty seconds a move.",
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

// Which of the setup controls apply to the game being described. Two settings can hide
// others, so this is one function both listeners call rather than each of them doing its own
// half: switching opponent used to be the only thing that hid anything, and doing it inline
// meant a variety slider that had been hidden for a random opening came back the moment the
// opponent changed.
function refreshSetup() {
  const human = setup.mode === "human";
  // Difficulty and opening variety are settings for an opponent that isn't there in a
  // game between two people. Hidden rather than ignored, so nothing on screen is a
  // control that quietly does nothing.
  el.aiOptions.classList.toggle("hidden", human);
  el.nameField.classList.toggle("hidden", !human);
  el.sideRandom.classList.toggle("hidden", !human);
  // Variety is how much the computer varies the formation it *chooses*. A random opening
  // never asks it to choose one, so the slider would do nothing at all.
  el.noiseField.classList.toggle("hidden", setup.randomEntry);
  el.opponentHint.textContent = human
    ? "You'll get a link to send them. No accounts, no sign-up."
    : "Play against the engine on this machine.";
  el.start.textContent = human ? "Create the game" : "Start";
}

el.opponentChoice.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-mode]");
  if (!b) return;
  setup.mode = b.dataset.mode;
  [...el.opponentChoice.children].forEach((c) => c.classList.toggle("selected", c === b));
  refreshSetup();
});

el.entryChoice.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-entry]");
  if (!b) return;
  setup.randomEntry = b.dataset.entry === "random";
  [...el.entryChoice.children].forEach((c) => c.classList.toggle("selected", c === b));
  el.entryHint.textContent = setup.randomEntry
    ? "Nobody chooses. All twelve pieces are placed for you, on squares drawn from the "
      + "legal ones, and the game opens with the board already full."
    : "Both sides enter their six pieces one at a time, royals first.";
  refreshSetup();
});

el.sideChoice.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-side]");
  if (!b) return;
  setup.side = b.dataset.side === "random" ? "random" : Number(b.dataset.side);
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
  const label = el.start.textContent;
  el.start.disabled = true;
  el.start.textContent = "Setting up…";
  el.setupError.textContent = "";
  try {
    seatToken = null;
    const res = await api("/api/games", {
      method: "POST",
      body: JSON.stringify({
        mode: setup.mode,
        side: setup.side,
        difficulty: setup.difficulty,
        noise: Number(el.noise.value) / 100,
        randomEntry: setup.randomEntry,
        ...(setup.mode === "human" ? withName({}) : {}),
      }),
    });
    seatToken = res.seatToken;
    inviteToken = res.inviteToken;
    saveSeat(res.state.id, seatToken);
    if (inviteToken) saveInvite(res.state.id, inviteToken);
    history.replaceState(null, "", `/g/${res.state.id}`);
    apply(res.state);
    showGame();
    if (inviteToken) showShare(res.state.id);
  } catch (err) {
    el.setupError.textContent = err.message;
  } finally {
    el.start.disabled = false;
    el.start.textContent = label;
  }
});

// ---------------------------------------------------------------------------
// Sharing
// ---------------------------------------------------------------------------
// The URL is built here rather than sent by the server, so no Host header decides what
// link a person clicks.

function inviteLink(gameId) { return `${location.origin}/join/${gameId}#${inviteToken}`; }
function resumeLink(gameId) { return `${location.origin}/g/${gameId}#s=${seatToken}`; }

function showShare(gameId) {
  el.inviteUrl.value = inviteLink(gameId);
  el.resumeUrl.value = resumeLink(gameId);
  el.sendInvite.classList.toggle("hidden", !navigator.share);
  el.share.classList.remove("hidden");
}

el.copyInvite.addEventListener("click", async () => {
  const text = el.inviteUrl.value;
  try {
    // Only available on HTTPS and localhost; selecting the field is the fallback that
    // works everywhere, including a phone on your LAN over plain http.
    await navigator.clipboard.writeText(text);
    el.copyInvite.textContent = "Copied";
    setTimeout(() => { el.copyInvite.textContent = "Copy"; }, 1500);
  } catch {
    el.inviteUrl.focus();
    el.inviteUrl.select();
    el.shareStatus.textContent = "Press ⌘C or Ctrl+C to copy the selected link.";
  }
});

el.sendInvite.addEventListener("click", () => {
  navigator.share({ title: "Royals", text: "Your move.", url: el.inviteUrl.value })
    .catch(() => {});
});

el.inviteAgain.addEventListener("click", () => {
  if (state) showShare(state.id);
});

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------
// Nobody pushes here, so the page asks. `since=<version>` makes an idle poll a version
// comparison and forty bytes rather than a full re-derivation of the position.
//
// Two things stop this being rude on a phone: it does not run at all while the tab is
// hidden -- a backgrounded tab polling every two seconds is a battery complaint -- and
// it slows down once a game has clearly gone quiet. Coming back to the tab refetches at
// once, so the pause is invisible.

const FAST_MS = 2000, SLOW_MS = 10000, SLOW_AFTER_MS = 120000;
let pollTimer = null;
let lastChange = Date.now();

function shouldPoll() {
  // Not while reviewing. A poll that landed mid-review would call apply() and put the live
  // position back on the board under somebody who is reading the game's history.
  return state && !review && state.phase !== "over" && !state.awaitingYou;
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

function schedulePoll() {
  stopPolling();
  if (!shouldPoll() || document.hidden) return;
  const quiet = Date.now() - lastChange > SLOW_AFTER_MS;
  pollTimer = setTimeout(poll, quiet ? SLOW_MS : FAST_MS);
}

async function poll() {
  pollTimer = null;
  if (!shouldPoll() || document.hidden) return;
  try {
    const fresh = await api(`/api/games/${state.id}?since=${state.version}`);
    if (!fresh.unchanged) { lastChange = Date.now(); apply(fresh); return; }
  } catch (err) {
    // A poll failing is not worth interrupting anybody over -- the next one may well
    // succeed, and the page is still showing a position that was true a moment ago.
  }
  schedulePoll();
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPolling();
  else if (shouldPoll()) poll();
});
window.addEventListener("focus", () => { if (shouldPoll() && !pollTimer) poll(); });

// ---------------------------------------------------------------------------
// Applying a new state
// ---------------------------------------------------------------------------

function apply(fresh) {
  const before = state;
  state = fresh;
  pickId++;
  selected = null;
  moves = [];
  carrying = false;
  // The selection is gone, so anything that was asking about it is stale. A badge left
  // over a square that is no longer picked up is a button that does nothing.
  breakOpen = false;
  chooser = null;
  if (!before || before.version !== fresh.version) lastChange = Date.now();

  // Once the other seat is taken there is nothing left to invite anybody to, and the
  // spent invitation is worth forgetting rather than leaving in storage.
  if (state.phase !== "waiting") {
    el.share.classList.add("hidden");
    if (inviteToken) { dropInvite(state.id); inviteToken = null; }
  }

  render();
  schedulePoll();
}

// ---------------------------------------------------------------------------
// Clicks
// ---------------------------------------------------------------------------

el.board.addEventListener("click", (event) => {
  // A reviewed board is a picture of a position somebody already played out of. There is
  // nothing to click on it, and no code path from here that could reach the game.
  if (review) return;
  if (busy || !state || state.phase === "over") return;
  const alg = view.hit(event.clientX, event.clientY);
  if (!alg) return;

  if (state.phase === "entering") return onEnterClick(alg);
  if (state.phase === "playing" && state.awaitingYou) return onPlayClick(alg);
});

async function onEnterClick(alg) {
  if (!state.awaitingYou) return;
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
  // A click on the board answers whatever the board was last asking, so anything already
  // open is dismissed first rather than left hanging behind the next question.
  const wasAsking = chooser !== null;
  chooser = null;

  // second click: a destination?
  if (selected) {
    // Every move landing here, not the first one found. A square is not a move: a push and
    // a free go to the same square and are different moves at different prices, and taking
    // the first match silently played the push every time -- which is what made freeing
    // your own people impossible whenever shoving them was legal too. Breaks are not in
    // here at all; they have a heading rather than a destination, and the arrows ask for
    // them.
    const here = moves.filter((m) => m.kind !== "break" && m.target === alg);
    if (here.length === 1) return submit(here[0]);
    if (here.length > 1) { chooser = { alg, options: here }; return render(); }
    if (alg === selected) return clearSelection();
    if (wasAsking) return render();
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

// Picking a square up used to be a request, and the player waited through it -- 100ms or
// more on mobile data, every time, for an answer about a position the page was already
// holding. The engine is in the page now (engine.js), so the squares light up on the click
// and the server's answer arrives afterwards to have the last word.
//
// It really is the last word, and it has to be: the local engine cannot know the game's
// history, so it offers ko-breaking moves the server strikes off. Its answer is a superset
// of the truth, so reconciling only ever takes squares away -- never adds one that was
// missing, and never changes what a square means.
async function selectOrigin(alg, pris) {
  const mine = ++pickId;

  const local = engine.available()
    ? engine.movesFrom(state.board, state.sideToMove, alg, pris)
    : null;
  if (local) showOptions(alg, pris, local, true);

  try {
    // Only block the page while there is nothing on screen to click yet.
    busy = !local;
    const res = await api(
      `/api/games/${state.id}/moves?origin=${encodeURIComponent(alg)}&pris=${pris}`);
    // A pick the player has already moved on from -- or a move since played -- must not be
    // put back up by an answer that was in flight while they did it.
    if (mine === pickId) showOptions(alg, pris, res, !local);
  } catch (err) {
    if (mine === pickId) setHint(err.message);
  } finally {
    busy = false;
    render();
  }
}

// `fresh` says this is the first answer about this pick rather than the server confirming
// one already on screen. Only a first answer puts the arrows and the push-or-free question
// away: the player may have opened either of them in the moments since, and closing it under
// them would be the reconciliation reaching somewhere it has no business.
function showOptions(alg, pris, res, fresh) {
  selected = alg;
  carrying = pris;
  moves = res.moves;
  if (fresh) {
    breakOpen = false;
    chooser = null;
  }
  el.carry.classList.toggle("hidden", !res.hasPrisonerVariant);
  el.carry.textContent = pris ? "Leave prisoners" : "Bring prisoners";
  el.cancel.classList.remove("hidden");
  setHint(describeOptions(moves));
  render();
}

function describeOptions(list) {
  if (!list.length) return "Nothing legal from there.";
  const counts = list.reduce((acc, m) => (acc[m.kind] = (acc[m.kind] || 0) + 1, acc), {});
  const parts = Object.entries(counts)
    .filter(([k]) => k !== "break")
    .map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`);

  const text = parts.length ? "Click a highlighted square. " + parts.join(", ") + "." : "";
  // The breaks are counted separately because they are not on any of those squares --
  // saying "3 breaks" beside squares that offer none is how the old hint misled.
  if (!counts.break) return text || "Nothing legal from there.";
  return (text + " Press BREAK to scatter the stack instead.").trim();
}

function clearSelection() {
  pickId++;
  selected = null;
  moves = [];
  carrying = false;
  breakOpen = false;
  chooser = null;
  el.carry.classList.add("hidden");
  el.cancel.classList.add("hidden");
  setHint("");
  render();
}

async function submit(move) {
  const body = { kind: move.kind, origin: move.origin, pris: !!move.pris };
  if (move.kind === "break") body.dir = move.dir;
  else body.target = move.target;
  // If this request is a retry of one that already landed, the server refuses it rather
  // than playing the move a second time.
  body.expectedVersion = state.version;

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
    // 409 means this page's idea of the position is behind. Refetching is the whole fix.
    if (err.status === 409 && state) refresh();
  } finally {
    busy = false;
    render();
  }
}

async function refresh() {
  try { apply(await api(`/api/games/${state.id}`)); } catch {}
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
  stopPolling();
  seatToken = inviteToken = null;
  history.pushState(null, "", "/");
  showSetup();
});

// Escape backs out one question at a time -- the chooser, then the arrows, then the
// selection -- rather than dropping the whole turn at the first press.
window.addEventListener("keydown", (event) => {
  if (review) return reviewKey(event);
  if (event.key !== "Escape" || !selected) return;
  if (chooser) { chooser = null; setHint(describeOptions(moves)); return render(); }
  if (breakOpen) { breakOpen = false; setHint(describeOptions(moves)); return render(); }
  clearSelection();
});

// ---------------------------------------------------------------------------
// Saving and reviewing
// ---------------------------------------------------------------------------
// A game that can be played but not re-read leaves nothing anybody can keep, send on, or
// learn from. Two halves: a file goes out, and a file -- or a game still on the server --
// comes back as a position per ply that can be walked through.
//
// Stepping is "replay a prefix", and a prefix of a legal game is a legal game. The server
// does that walk once and sends every position, so dragging the scrubber across a game
// makes no requests at all and the page needs no rules of its own to do it.

const RECORD_MAGIC = "# Royals 1";

// Mirrors notation.WRAP. Purely how the file looks -- decode_game strips comments and
// splits the rest on whitespace, so where the lines break cannot change what is read back.
// It is here so that a record saved from the browser looks like one saved from the desktop.
const RECORD_WRAP = 72;

// Mirrors REVIEW_MAX_PLIES in main.py, which is MAX_BODY_BYTES / 7 -- the longest record
// that fits in one request. Checking here means an oversized file is explained instantly
// and is never uploaded; the server enforces it regardless. tests/test_web_api.py holds
// this number to the server's, so the two cannot drift apart unnoticed.
const REVIEW_MAX_PLIES = 292;

// -- writing one out ---------------------------------------------------------
// Built here rather than fetched, because the page already holds the whole move list --
// `moves` is in every state the server sends -- so a game in progress can be saved without
// asking anybody, and without the server doing work for a file the player already has.
//
// This is not a second implementation of the record format, and the distinction is worth
// being exact about: the body is `moves.join(" ")`, which is the string the database column
// already stores, and everything above it is comments that decode_game throws away. There
// is no packing here to disagree about. notation.decode_game remains the only reader.

function recordText() {
  const when = new Date().toISOString().slice(0, 10);
  const who = state.mode === "ai"
    ? `against the computer at depth ${state.aiDepth}`
    : "two players";
  const how = !state.result ? "unfinished"
    : state.result === "draw" ? `drawn by ${state.termination}`
    : `${state.result} wins by ${state.termination}`;

  return [
    RECORD_MAGIC,
    `# ${when}  ${who}  ${how}  ${state.moves.length} plies`,
    ...wrapTokens(state.moves, RECORD_WRAP),
    "",
  ].join("\n");
}

function wrapTokens(tokens, columns) {
  const lines = [];
  let line = "";
  for (const token of tokens) {
    if (line && line.length + 1 + token.length > columns) { lines.push(line); line = ""; }
    line = line ? `${line} ${token}` : token;
  }
  if (line) lines.push(line);
  return lines;
}

function recordName() {
  const when = new Date().toISOString().slice(0, 10);
  return `royals-${when}-${(state.id || "game").slice(0, 8)}.txt`;
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked on the next turn of the loop rather than immediately: revoking synchronously
  // can beat the download starting, and the file arrives empty.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

el.save.addEventListener("click", () => {
  if (!state || !state.moves.length) return;
  download(recordName(), recordText());
});

// -- reading one back --------------------------------------------------------

// The same two rules notation.decode_game applies: everything from a '#' to the end of its
// line is a comment, and what is left is split on whitespace. Nothing is *validated* here
// -- the server decodes every token and names the first bad one. This only counts, so that
// a file too long to upload is explained without being uploaded.
function countPlies(text) {
  return text.split("\n").map((line) => line.split("#")[0]).join(" ")
             .split(/\s+/).filter(Boolean).length;
}

async function beginReview(request, live) {
  const body = await request();
  if (!body.positions || !body.positions.length) throw new Error("There is nothing to review.");

  // Any half-made selection belongs to a game that is no longer on the board.
  pickId++;
  selected = null;
  moves = [];
  carrying = false;
  breakOpen = false;
  chooser = null;
  el.carry.classList.add("hidden");
  el.cancel.classList.add("hidden");

  review = {
    moves: body.moves,
    positions: body.positions,
    result: body.result,
    termination: body.termination,
    enterSteps: body.enterSteps,
    // Opens at the end, which is the position somebody who has just finished a game is
    // already looking at, and is one keypress from the start either way.
    at: body.positions.length - 1,
    // Whether there is a live game behind this to go back to, or whether Done means
    // returning to the setup screen.
    live,
  };

  stopPolling();
  showGame();
  renderReview();
}

el.reviewOpen.addEventListener("click", async () => {
  if (!state) return;
  el.reviewOpen.disabled = true;
  try {
    await beginReview(() => api(`/api/games/${state.id}/positions`), true);
  } catch (err) {
    setHint(err.message);
  } finally {
    el.reviewOpen.disabled = false;
  }
});

el.open.addEventListener("click", () => el.openFile.click());

el.openFile.addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0];
  // Cleared straight away, so that choosing the same file a second time still fires a
  // change event -- the obvious thing to do after a failed read is to try it again.
  event.target.value = "";
  if (!file) return;

  el.setupError.textContent = "";
  el.openStatus.textContent = `Reading ${file.name}…`;
  try {
    const text = await file.text();
    const plies = countPlies(text);
    if (!plies) throw new Error("There are no moves in that file.");
    if (plies > REVIEW_MAX_PLIES) {
      throw new Error(`That record is ${plies} moves long, and one of more than ` +
                      `${REVIEW_MAX_PLIES} does not fit in a single request. ` +
                      `A played game rarely passes 150.`);
    }
    await beginReview(() => api("/api/review", {
      method: "POST",
      body: JSON.stringify({ record: text }),
    }), false);
    el.openStatus.textContent = "";
  } catch (err) {
    el.openStatus.textContent = err.message;
  }
});

// -- stepping ----------------------------------------------------------------

function goTo(at) {
  if (!review) return;
  review.at = Math.max(0, Math.min(review.positions.length - 1, at));
  renderReview();
}

function exitReview() {
  const wasLive = review && review.live;
  review = null;
  el.reviewBar.classList.add("hidden");
  el.movelist.classList.remove("steppable");
  if (wasLive && state) {
    render();
    schedulePoll();
    return;
  }
  history.pushState(null, "", "/");
  showSetup();
}

function reviewKey(event) {
  const last = review.positions.length - 1;
  const to = { ArrowLeft: review.at - 1, ArrowRight: review.at + 1,
               Home: 0, End: last }[event.key];
  if (to !== undefined) {
    // Or the slider, if it happens to have focus, would take the keypress as well and
    // step twice.
    event.preventDefault();
    return goTo(to);
  }
  if (event.key === "Escape") exitReview();
}

// goTo is a no-op without a review, so these need no guard of their own beyond reading
// `review.at` -- which is why each one that does reads it inside the handler.
el.rvFirst.addEventListener("click", () => goTo(0));
el.rvPrev.addEventListener("click", () => review && goTo(review.at - 1));
el.rvNext.addEventListener("click", () => review && goTo(review.at + 1));
el.rvLast.addEventListener("click", () => review && goTo(review.positions.length - 1));
el.rvSlider.addEventListener("input", () => goTo(Number(el.rvSlider.value)));
el.rvExit.addEventListener("click", exitReview);

// The move list is a way of getting somewhere as well as a record of what happened.
el.movelist.addEventListener("click", (event) => {
  if (!review) return;
  const li = event.target.closest("li[data-ply]");
  if (li) goTo(Number(li.dataset.ply));
});

function renderReview() {
  const last = review.positions.length - 1;
  const spot = review.positions[review.at];

  // No marks and nothing selected: the same renderer the live game uses, handed a board
  // and the squares the last move touched.
  view.draw({ board: spot.board }, new Map(), null, spot.lastMove);
  el.marks.replaceChildren();
  renderMoveList();

  el.reviewBar.classList.remove("hidden");
  el.rvSlider.max = String(last);
  el.rvSlider.value = String(review.at);
  // The move number is what somebody stepping through a game is keeping their place by,
  // so it sits beside the slider at reading size; the ply is the smaller line under it,
  // because it is what the slider is actually indexed by and the two are not the same
  // number. Both, rather than either: one is what you say, the other is where you are.
  el.rvNumber.textContent = turnLabel(spot);
  el.rvPly.textContent = `ply ${review.at} of ${last}`;
  el.rvFirst.disabled = el.rvPrev.disabled = review.at === 0;
  el.rvLast.disabled = el.rvNext.disabled = review.at === last;

  // Nothing here is playable, so nothing that plays is on screen.
  for (const b of [el.pass, el.carry, el.cancel, el.resign, el.inviteAgain,
                   el.save, el.reviewOpen]) {
    b.classList.add("hidden");
  }
  el.overlay.classList.add("hidden");

  const ran = review.at > 0 ? review.moves[review.at - 1] : null;
  setStatus(review.at === 0 ? "Before the first piece was entered"
                            : `${turnLabel(spot)}   ${ran}`,
            plySide(review.at - 1));

  const ending = review.at === last && review.result ? describeEnd(review) + "  " : "";
  setHint(`${ending}← and → step through the game; click any move to jump to it.`);
}

// What a person calls a position: "Move 7", or "Entering 3 of 12" during the opening.
//
// Neither is the ply number, and that is the whole reason this exists. A record counts the
// twelve placements as plies 1 to 12, so the seventh move of the game is ply 19 -- a number
// that is right, useful to the code, and no help at all to somebody trying to say where
// they have got to. The server sends `phase` and `turn` per position rather than the page
// deriving them, so this and `record.turn_of_ply` cannot disagree.
function turnLabel(spot) {
  if (!spot || !spot.turn) return "Start";
  return spot.phase === "entering"
    ? `Entering ${spot.turn} of ${review.enterSteps}`
    : `Move ${spot.turn}`;
}

// The marker beside a line in the move list, for the same reason: placements counted
// within the opening, moves counted from the first one. Derived from the index here
// because the live game holds only its move list -- `enterSteps` is what the server sends
// so the boundary between the two is not a 12 written into this file.
function plyMarker(index) {
  const enterSteps = review ? review.enterSteps : (state && state.enterSteps);
  if (!enterSteps) return String(index + 1);
  return index < enterSteps ? String(index + 1) : `${index - enterSteps + 1}.`;
}

// Which side played ply `i`, or null when nothing sensible can be said.
//
// The parity holds because the entering phase is exactly twelve plies whatever happens in
// it -- a side with nowhere to place still writes down a "--" -- so the first move of play
// is always ply 12 and always red's. It does NOT hold *within* the entering phase, where
// blue places first, so a placement is left uncoloured rather than confidently miscoloured.
function plySide(i) {
  if (i < 0 || !review || (review.moves[i] || "").startsWith("@")) return null;
  return i % 2 === 0 ? 1 : 0;
}

window.addEventListener("popstate", () => { route(); });

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function setStatus(text, side) {
  el.status.textContent = text;
  el.status.className = "status" + (side === null || side === undefined ? "" : " " + SIDE_NAME[side]);
}

function setHint(text) { el.hint.textContent = text || " "; }

function marksFor() {
  const marks = new Map();
  if (!state) return marks;

  if (state.phase === "entering" && state.awaitingYou) {
    state.entering.options.forEach((a) => marks.set(a, "enter"));
    return marks;
  }

  if (state.phase !== "playing" || !state.awaitingYou) return marks;

  if (selected) {
    // While the arrows are out they are the question being asked, and they stand on these
    // very squares. Two families of mark over the same ground is unreadable, so the
    // destinations stand down until the break is put away again.
    if (breakOpen) return marks;

    for (const m of moves) {
      // a break has a heading and no destination; it is drawn by the arrows, not here
      if (m.kind === "break") continue;

      // A square that is more than one move gets its own mark, because it is its own
      // question -- clicking it opens the chooser rather than playing anything. Painting
      // it as a push while it plays a free, or the other way about, is the bug this
      // whole layer exists to remove.
      const already = marks.get(m.target);
      if (already) { marks.set(m.target, "choice"); continue; }
      marks.set(m.target, m.kind === "jump" && looksWrapped(m.origin, m.target)
        ? "wrap" : m.kind);
    }
  } else {
    (state.origins || []).forEach((a) => marks.set(a, "origin"));
  }
  return marks;
}

// ---------------------------------------------------------------------------
// The mark layer -- breaks, and push-or-free
// ---------------------------------------------------------------------------

// Screen steps, as [column, row]. These are not the engine's headings turned into files and
// ranks, and the difference has bitten once already: rank 1 is drawn at the BOTTOM, so the
// engine's "down" -- which is rank + 1 -- travels UP the screen. Working in rows and
// columns says that once, here, instead of leaving every reader to rediscover it.
const SCREEN_STEP = { d: [0, -1], u: [0, 1], l: [-1, 0], r: [1, 0] };
const ARROW_GLYPH = { d: "↑", u: "↓", l: "←", r: "→" };

function cellOf(alg) {
  const index = algToIndex(alg);
  return { col: index % 7, row: 6 - Math.floor(index / 7) };
}

// The square one step along a heading, wrapping like the board does. The old code returned
// nothing here and the break simply vanished: on an edge square a break is often legal
// where no push is, so those were exactly the breaks a player could never reach.
function neighbour(alg, dir) {
  const { col, row } = cellOf(alg);
  const [dc, dr] = SCREEN_STEP[dir];
  const wrapped = col + dc < 0 || col + dc > 6 || row + dr < 0 || row + dr > 6;
  return { col: (col + dc + 7) % 7, row: (row + dr + 7) % 7, wrapped };
}

function place(node, col, row) {
  node.style.left = `${(col * 100) / 7}%`;
  node.style.top = `${(row * 100) / 7}%`;
  return node;
}

function button(className, text) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.textContent = text;
  return b;
}

// The one answer to "can this selection break", asked by the badge, the arrows and the
// hint alike. They have to agree, or the board grows a button that does nothing.
function breakMoves() {
  if (!selected || busy) return [];
  if (!state || state.phase !== "playing" || !state.awaitingYou) return [];
  return moves.filter((m) => m.kind === "break");
}

function renderMarks() {
  const nodes = [];
  const breaks = breakMoves();

  if (breaks.length) {
    const badge = button("mark-badge" + (breakOpen ? " open" : ""), "BREAK");
    badge.title = "Scatter this stack along a heading";
    badge.addEventListener("click", () => {
      breakOpen = !breakOpen;
      setHint(breakOpen
        ? "Click an arrow to scatter that way, or BREAK again to put it away."
        : describeOptions(moves));
      render();
    });
    const at = cellOf(selected);
    nodes.push(place(badge, at.col, at.row));

    if (breakOpen) {
      for (const m of breaks) {
        const to = neighbour(selected, m.dir);
        const arrow = button("mark-arrow" + (to.wrapped ? " wrapped" : ""),
                             ARROW_GLYPH[m.dir]);
        arrow.title = to.wrapped
          ? "Scatter this way — it wraps around the edge"
          : "Scatter this way";
        // Checked against the live list rather than trusted from the button, the same way
        // the desktop does: the list is what the server will be asked to play.
        arrow.addEventListener("click", () => {
          if (breakMoves().some((b) => b.dir === m.dir)) submit(m);
        });
        nodes.push(place(arrow, to.col, to.row));
      }
    }
  }

  if (chooser) {
    const box = document.createElement("div");
    box.className = "chooser";
    for (const m of chooser.options) {
      const b = button("", cap(m.kind));
      b.title = m.kind === "free"
        ? "Step onto the square and stand your people back up"
        : "Shove the whole square along, prisoners and all";
      b.addEventListener("click", () => submit(m));
      box.appendChild(b);
    }
    const at = cellOf(chooser.alg);
    // nudged left off the far files so a two-button box can't hang off the board edge
    place(box, Math.min(at.col, 5), at.row);
    nodes.push(box);
  }

  el.marks.replaceChildren(...nodes);
}

// Who the page is waiting for, said from the point of view of whoever is reading it.
// A spectator is a real case: anyone can be sent the game's address.
function waitingOn() {
  const side = state.sideToMove;
  if (state.yourSide === null) return `${cap(SIDE_NAME[side])} to move`;
  if (state.awaitingYou) return "Your move";
  if (state.mode === "ai") return "The computer is thinking…";
  return `Waiting for ${SIDE_NAME[side]}`;
}

function render() {
  // While reviewing, the board is a position out of the record rather than the live one,
  // and everything below is about a turn nobody is taking.
  if (review) return renderReview();

  view.draw(state, marksFor(), selected, state ? state.lastMove : []);
  renderMarks();
  if (!state) return;

  renderMoveList();

  el.pass.classList.toggle("hidden",
    !(state.phase === "playing" && state.awaitingYou && state.mustPass));
  // Only offered when this browser actually still holds the invitation to offer.
  el.inviteAgain.classList.toggle("hidden",
    !(state.phase === "waiting" && inviteToken));
  el.resign.classList.toggle("hidden", state.yourSide === null);
  // A game with no plies yet is one with nothing to save and nothing to walk through.
  el.save.classList.toggle("hidden", !state.moves.length);
  el.reviewOpen.classList.toggle("hidden", !state.moves.length);

  if (state.phase === "over") {
    el.overlay.classList.remove("hidden");
    el.overlayText.textContent = describeEnd(state);
    setStatus(describeEnd(state), null);
    setHint("");
    el.carry.classList.add("hidden");
    el.cancel.classList.add("hidden");
    return;
  }
  el.overlay.classList.add("hidden");

  if (state.phase === "waiting") {
    setStatus("Waiting for your opponent", null);
    setHint(state.randomEntry
      ? "Send them the invite link. The pieces are placed at random the moment they open it."
      : "Send them the invite link. The game starts the moment they open it.");
    return;
  }

  if (state.phase === "entering") {
    const e = state.entering;
    setStatus(`${cap(SIDE_NAME[e.side])} ${e.piece}`, e.side);
    setHint(state.awaitingYou
      ? `Click a highlighted square to enter your ${e.piece}.  (${e.step} of ${e.total})`
      : `${waitingOn()}  (${e.step} of ${e.total})`);
    return;
  }

  const side = state.sideToMove;
  setStatus(`${cap(SIDE_NAME[side])} to move`, side);
  if (!state.awaitingYou) { setHint(waitingOn()); return; }
  if (state.mustPass) { setHint("You have no legal move — every one would repeat an earlier position. You must pass."); return; }
  if (chooser) {
    setHint(`${chooser.alg} is more than one move. Pick which, or click elsewhere to think again.`);
    return;
  }
  if (!selected) setHint("Click one of your highlighted squares.");
}

// `src` is whatever holds a result and a termination -- the live state, or a record being
// reviewed. A record has no seat in it, so `yourSide` is absent and the "that is you" is
// simply not said, which is right: a game read out of a file was not necessarily yours.
function describeEnd(src) {
  const r = src.result;
  const how = {
    gather: "by gathering all six pieces",
    resign: "by resignation",
    double_pass: "— neither side could move",
    no_moves: "— no legal moves remained",
    ply_limit: "— the game reached its move limit",
  }[src.termination] || "";

  if (r === "draw") return `Draw ${how}`.trim() + ".";
  const won = SIDE_NAME.indexOf(r) === src.yourSide;
  return `${cap(r)} wins ${how}`.trim() + `. ${won ? "That is you." : ""}`.trimEnd();
}

function renderMoveList() {
  const list = review ? review.moves : state.moves;
  const items = list.map((ran, i) => {
    const li = document.createElement("li");
    const mark = document.createElement("span");
    mark.className = "ply-mark";
    mark.textContent = plyMarker(i);
    li.append(mark, document.createTextNode(ran));
    if (ran.startsWith("@")) li.className = "enter";
    else li.className = SIDE_NAME[i % 2 === 0 ? 1 : 0];
    if (review) {
      // Ply n produced position n, so the datum is the index into `positions`.
      li.dataset.ply = String(i + 1);
      if (i + 1 === review.at) li.classList.add("at");
    }
    return li;
  });
  el.movelist.replaceChildren(...items);
  el.movelist.classList.toggle("steppable", !!review);

  // Follow the position being shown rather than always running to the bottom. In review
  // the line worth seeing is the one on the board, and scrolling past it to the end of the
  // game leaves the list at exactly the wrong place.
  const here = review ? items[review.at - 1] : null;
  if (here) {
    // offsetTop of both, subtracted: neither is positioned, so they share an offsetParent
    // and the difference is the offset within the list.
    el.movelist.scrollTop = Math.max(
      0, here.offsetTop - el.movelist.offsetTop - el.movelist.clientHeight / 2);
  } else {
    el.movelist.scrollTop = el.movelist.scrollHeight;
  }
}

const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;

// ---------------------------------------------------------------------------

window.addEventListener("resize", () => { view.resize(); render(); });
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => render());
}

// Route first, and fill the New Game form in the background.
//
// These used to be sequential, which meant somebody opening an invitation waited on a
// list of difficulty settings they were never going to see before their invitation was
// so much as looked at. Nothing in routing needs that list; the two are independent, and
// the one the visitor is waiting for should go first.
route().catch((err) => { el.setupError.textContent = err.message; });
buildSetup().catch((err) => { el.diffHint.textContent = err.message; });
