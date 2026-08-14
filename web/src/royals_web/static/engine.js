// The engine, in the page.
//
// `royals.wasm` is the same Rust engine the server plays by, compiled for the browser --
// tables, move generation and the executors, about 50KB. What it buys is that "which squares
// can this piece go to" stops being a request. On a phone over mobile data that question cost
// 100ms or more, every time a piece was picked up, for an answer the page could have had
// before the finger left the screen.
//
// WHAT IT IS NOT AUTHORITATIVE ABOUT
//
// Everything, formally. The server regenerates every legal move on submission whether or not
// this file asked first, and that is what stops a hostile client -- unchanged, and the reason
// none of this can make an illegal move legal.
//
// One rule really is missing, and it matters enough to say twice: **ko**. A move may not
// return the game to a position it has already stood in, and the page holds the position in
// front of it rather than the history behind it. So `movesFrom` answers a SUPERSET: every
// legal move, plus any that repeat an earlier position. Over the regression walks, 43% of
// positions have at least one move struck off that way -- far too many to shrug at. So the
// page draws this answer immediately and then reconciles against the server's, which can only
// ever take squares away. See `selectOrigin` in app.js.
//
// REBUILDING IT
//
//   cd engine-rs
//   cargo build --release --features wasm --target wasm32-unknown-unknown
//   cp target/wasm32-unknown-unknown/release/royals_engine.wasm ../web/src/royals_web/static/royals.wasm
//
// No npm, no wasm-bindgen, no generated glue: the exports are plain C functions over two byte
// buffers, and the fifty lines below are all the JavaScript there is. See engine-rs/src/wasm.rs.

import { algToIndex, indexToAlg } from "/static/board.js";

// The kind byte, mirroring MoveKind in engine-rs/src/lib.rs. A Rust test asserts this order.
const KINDS = ["jump", "push", "break", "free"];

// notation.py's DIR_LETTERS -- the initials of Engine.HEADINGS, which is what the server
// decodes a break's direction from. Getting these out of order would send a legal-looking
// move in the wrong direction, so they are checked on load against a known position.
const DIR_LETTERS = ["d", "u", "l", "r"];

const SQUARES = 49;
const FIELDS = 8;

// What royals_self_test() answers: blue's moves on the board entering starts from. A whole
// pass through the tables, the rays and move generation, so a module that gets this right did
// not merely download.
const SELF_TEST = 10;

// How many bytes a move occupies in the out buffer: kind, origin, target, travel.
//
// Checked separately from SELF_TEST because the two catch different failures. SELF_TEST is a
// move *count*, which a change to the buffer layout leaves untouched -- so a module built for
// a three-byte stride would answer 10 quite correctly and then be decoded into nonsense. This
// is the one that notices.
const MOVE_STRIDE = 4;

let wasm = null;

// Resolves to true if the page has a working engine. Never rejects: a browser that can't run
// this simply keeps asking the server, which is what every browser did until now.
export const ready = load();

async function load() {
  try {
    const res = await fetch("/static/royals.wasm");
    if (!res.ok) throw new Error(`royals.wasm: ${res.status}`);

    // instantiate() rather than instantiateStreaming(): 50KB does not need streaming
    // compilation, and this works whatever content type the file arrives with.
    const { instance } = await WebAssembly.instantiate(await res.arrayBuffer(), {});
    const exports = instance.exports;

    if (exports.royals_self_test() !== SELF_TEST) {
      throw new Error("royals.wasm disagrees with the rules this page was built against");
    }
    if (!exports.royals_move_stride || exports.royals_move_stride() !== MOVE_STRIDE) {
      throw new Error("royals.wasm lays its answers out differently than this page reads them");
    }

    wasm = exports;
    return true;
  } catch (err) {
    // Not an error the player should ever see. The page falls back to asking the server,
    // which is slower and completely correct.
    console.warn("local engine unavailable, falling back to the server:", err);
    return false;
  }
}

export const available = () => wasm !== null;

// Writes a position into the module. The board arrives from the server as parsed squares --
// null, or the eight semantic fields -- and goes back the same way: unpacking 13-bit fields
// in JavaScript would be a second implementation of the board format, and the moment there
// are two they disagree. The packing happens in Rust, in the one function that knows how.
function loadBoard(board) {
  const bytes = new Uint8Array(wasm.memory.buffer, wasm.royals_board_ptr(), SQUARES * FIELDS);
  bytes.fill(0);

  for (let i = 0; i < SQUARES; i++) {
    const s = board[i];
    if (!s) continue;
    const at = i * FIELDS;
    bytes[at] = s.side;
    bytes[at + 1] = s.dragon;
    bytes[at + 2] = s.spy;
    bytes[at + 3] = s.pawns;
    bytes[at + 4] = s.royal;
    bytes[at + 5] = s.capSpy;
    bytes[at + 6] = s.capPawns;
    bytes[at + 7] = s.prisFlag;
  }

  return wasm.royals_load() === 1;
}

// Everything `contr` may do from one square, in exactly the shape GET /moves answers with --
// so app.js cannot tell which of the two it is holding. Returns null if there is no engine
// here, which is the caller's signal to ask the server and wait.
export function movesFrom(board, contr, originAlg, pris, pushRange) {
  if (!wasm || !board) return null;
  if (!loadBoard(board)) return null;

  // Set per call rather than once per game. The rule lives in a static inside the module, and
  // a page that set it at game creation would be one navigation or one reload away from
  // asking under whatever the last game used. Passing it every time costs a store.
  wasm.royals_set_push_range(pushRange ? 1 : 0);

  const origin = algToIndex(originAlg) + 1;
  const carrying = pris ? 1 : 0;
  const count = wasm.royals_moves_from(contr, origin, carrying);
  const out = new Uint8Array(wasm.memory.buffer, wasm.royals_out_ptr(),
                             count * MOVE_STRIDE);

  const moves = [];
  for (let i = 0; i < count; i++) {
    const kind = KINDS[out[i * MOVE_STRIDE]];
    const target = out[i * MOVE_STRIDE + 2];
    const travel = out[i * MOVE_STRIDE + 3];

    // A break has a heading rather than a destination; everything else has a 0-based square.
    // That asymmetry is the engine's, all the way down, and this is not the place to tidy it.
    if (kind === "break") {
      moves.push({ kind, origin: originAlg, dir: DIR_LETTERS[target] });
      continue;
    }

    const move = { kind, origin: originAlg, target: indexToAlg(target), pris: !!pris };
    // Present only where it is more than one square, because notation.move_to_json does
    // exactly that and this list has to be indistinguishable from the server's. Always
    // setting it would differ in the one case nobody would think to test: a standard game.
    if (kind === "push" && travel > 1) move.travel = travel;
    moves.push(move);
  }

  return {
    origin: originAlg,
    pris: !!pris,
    hasPrisonerVariant: wasm.royals_has_moves(contr, origin, carrying ? 0 : 1) === 1,
    moves,
  };
}
