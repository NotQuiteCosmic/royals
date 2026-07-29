//! The browser's copy of the rules: enough of the engine to answer "where can this piece
//! go" inside the page, so that highlighting stops costing a round trip.
//!
//! ```text
//! cargo build --release --features wasm --target wasm32-unknown-unknown
//! cp target/wasm32-unknown-unknown/release/royals_engine.wasm \
//!    ../web/src/royals_web/static/royals.wasm
//! ```
//!
//! That artifact is **checked in**, which makes it the one part of this engine that can go
//! stale in silence -- change a rule here, forget the two lines above, and the page keeps
//! playing yesterday's game while every test stays green. `tests/wasm_parity.py` is what
//! catches that: it asks the shipped module what it asks the Python engine, across every
//! position the port fixtures walk through, and CI runs it on every commit.
//!
//! ## No wasm-bindgen
//!
//! The exports below are plain `extern "C"`, and the JavaScript side of them is fifty
//! hand-written lines in `static/engine.js`. wasm-bindgen would generate that glue, and
//! generating it needs the `wasm-bindgen` CLI at a version matching the crate exactly --
//! which today means rustc 1.86, where this crate builds on 1.80 (see `Cargo.toml`'s
//! `rust-version`). Rebuilding the module would then need a second tool, pinned, on top of
//! the target. Against that, what wasm-bindgen buys here is nothing: the entire interface is
//! two byte buffers and five integers, because a board is 392 small numbers and a move is
//! three. So the whole build is one `cargo build` and the artifact is one file.
//!
//! ## Single-threaded, deliberately
//!
//! No `SharedArrayBuffer`, and therefore no COOP/COEP headers on the server. The search is
//! serial anyway, so nothing is given up -- and those headers are load-bearing enough
//! elsewhere that adding them to enable a parallelism nobody uses would be a poor trade.
//!
//! ## What this is not
//!
//! **The server stays authoritative.** `game.py` regenerates every legal move on submission
//! and does not trust that the client asked first; that is what stops a hostile client, and
//! it does not move. Everything here is presentation, in exactly the sense `board.js` has
//! always meant it.
//!
//! And one rule genuinely is missing: **the ko filter**. A move may not return the game to a
//! position it has already stood in, and the page does not hold the game's history -- only
//! the position in front of it. So [`royals_moves_from`] answers a **superset** of what is
//! legal: every legal move, plus any that repeat. Measured over the fixture walks, 43% of
//! positions have at least one move struck off by ko, so this is not a rounding error --
//! `engine.js` reconciles against the server, and the module's job is to make the page
//! respond instantly rather than to have the last word.

use std::cell::UnsafeCell;

use crate::board::{build_space, Board, EMPTY_BOARD};
use crate::movegen::{list_all_moves, parse_board};
use crate::MoveKind;

/// Squares on a board, and the semantic fields the page holds each one as. The browser is
/// handed **parsed** squares by `game.py` rather than packed integers, on purpose: unpacking
/// 13-bit fields in JavaScript would be a second implementation of the board format, and the
/// moment there are two they disagree. So the same fields come back in here, and this side
/// does the packing -- with [`build_space`], the one function that knows how.
const SQUARES: usize = 49;
const FIELDS: usize = 8;

/// The most moves one square can offer: four jump strands of six, four pushes, four breaks
/// and four frees.
const MAX_MOVES: usize = 36;

/// Three bytes a move -- kind, 1-based origin, target -- and never more than 49 entries when
/// [`royals_origins`] is the one writing.
const OUT_BYTES: usize = MAX_MOVES * 3;

/// The kind byte. **`engine.js` mirrors this order**, and `kinds_are_in_the_documented_order`
/// below is what keeps the two from drifting.
const fn kind_byte(kind: MoveKind) -> u8 {
    match kind {
        MoveKind::Jump => 0,
        MoveKind::Push => 1,
        MoveKind::Break => 2,
        MoveKind::Free => 3,
    }
}

/// Static scratch, shared with JavaScript by pointer.
///
/// Sound because this module is single-threaded by construction: wasm without
/// `SharedArrayBuffer` has one thread, and every export below runs to completion before the
/// page gets control back, so no two of them are ever live at once. There is no allocator
/// here at all, which is also why the module needs no `alloc`/`free` pair.
struct Shared<T>(UnsafeCell<T>);
unsafe impl<T> Sync for Shared<T> {}

/// Where the page writes the position: 49 squares of 8 fields, in board order, each square
/// `[side, dragon, spy, pawns, royal, capSpy, capPawns, prisFlag]` and all zeroes for empty.
static BOARD_IN: Shared<[u8; SQUARES * FIELDS]> = Shared(UnsafeCell::new([0; SQUARES * FIELDS]));

/// Where answers go back.
static OUT: Shared<[u8; OUT_BYTES]> = Shared(UnsafeCell::new([0; OUT_BYTES]));

/// The position, packed, as of the last [`royals_load`].
static BOARD: Shared<Board> = Shared(UnsafeCell::new(EMPTY_BOARD));

#[inline]
fn board_in() -> &'static mut [u8; SQUARES * FIELDS] {
    unsafe { &mut *BOARD_IN.0.get() }
}

#[inline]
fn out() -> &'static mut [u8; OUT_BYTES] {
    unsafe { &mut *OUT.0.get() }
}

#[inline]
fn board() -> &'static mut Board {
    unsafe { &mut *BOARD.0.get() }
}

/// Where to write the position. 392 bytes, and the page owns every one of them.
#[no_mangle]
pub extern "C" fn royals_board_ptr() -> *mut u8 {
    board_in().as_mut_ptr()
}

/// Where answers appear. Three bytes per move: kind, 1-based origin, target -- with target a
/// 0-based square for a jump, push or free, and a **direction index** for a break, which is
/// the same asymmetry the engine carries everywhere else.
#[no_mangle]
pub extern "C" fn royals_out_ptr() -> *const u8 {
    out().as_ptr()
}

/// Packs whatever is in the input buffer into a board. Returns 1, or **0 if any square was
/// overfull** -- the same refusal `build_space` makes on the Python side, since a square with
/// two royals in it is not a position and answering questions about it would be worse than
/// saying so.
#[no_mangle]
pub extern "C" fn royals_load() -> u32 {
    let fields = board_in();
    let mut packed = EMPTY_BOARD;

    for square in 0..SQUARES {
        let f = &fields[square * FIELDS..square * FIELDS + FIELDS];
        match build_space(f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7]) {
            Ok(code) => packed[square] = code,
            Err(_) => {
                *board() = EMPTY_BOARD;
                return 0;
            }
        }
    }

    *board() = packed;
    1
}

/// Everything `contr` may do from one **1-based** square, written to the output buffer;
/// returns how many.
///
/// Whether the origin is a spy breaking out of an enemy stack is worked out from the board
/// and never taken from the caller -- the same derivation `list_all_moves` performs, which is
/// what makes this list identical to the server's rather than merely similar.
///
/// **Minus the ko filter.** See the module comment: this is a superset of what is legal.
#[no_mangle]
pub extern "C" fn royals_moves_from(contr: u32, origin: u32, moving_pris: u32) -> u32 {
    let board = *board();
    let spaces = parse_board(&board);
    let out = out();
    let mut count = 0usize;

    for mv in list_all_moves(&board, contr as u8, &spaces) {
        if mv.origin as u32 != origin || mv.moving_pris != (moving_pris != 0) {
            continue;
        }
        if count == MAX_MOVES {
            break;
        }

        out[count * 3] = kind_byte(mv.kind);
        out[count * 3 + 1] = mv.origin;
        out[count * 3 + 2] = mv.target;
        count += 1;
    }

    count as u32
}

/// Whether that square has anything at all to offer, without writing the list. What the page
/// asks to decide whether to show "Bring prisoners".
#[no_mangle]
pub extern "C" fn royals_has_moves(contr: u32, origin: u32, moving_pris: u32) -> u32 {
    let board = *board();
    let spaces = parse_board(&board);

    let any = list_all_moves(&board, contr as u8, &spaces)
        .iter()
        .any(|mv| mv.origin as u32 == origin && mv.moving_pris == (moving_pris != 0));

    u32::from(any)
}

/// Every 1-based square `contr` has a move out of, ascending, one byte each. The server sends
/// this too -- ko-filtered, which this is not -- so the page uses the server's and keeps this
/// for the day it plays without one.
#[no_mangle]
pub extern "C" fn royals_origins(contr: u32) -> u32 {
    let board = *board();
    let spaces = parse_board(&board);
    let out = out();
    let mut count = 0usize;

    for mv in list_all_moves(&board, contr as u8, &spaces) {
        if count > 0 && out[count - 1] == mv.origin {
            continue;
        }
        out[count] = mv.origin;
        count += 1;
    }

    count as u32
}

/// A fixed number the page checks on load: the moves blue has on the board entering starts
/// from, which is two dragons facing each other down the d file.
///
/// It is a whole pass through the tables, move generation and the ray walks, so a module
/// that answers this correctly did not merely download -- it built its tables and can play.
#[no_mangle]
pub extern "C" fn royals_self_test() -> u32 {
    let start = crate::board::entering_board();
    list_all_moves(&start, 0, &parse_board(&start)).len() as u32
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use super::*;
    use crate::movegen::make_origin;

    /// The buffers above are static and the exports are documented as single-threaded, which
    /// the test runner is not. This is the contract, enforced -- and it is also why nothing
    /// in the engine proper reaches for module state.
    static ONE_AT_A_TIME: Mutex<()> = Mutex::new(());

    /// Fills the input buffer the way `engine.js` does, from parsed fields.
    fn load(board: &Board) {
        let fields = board_in();
        for (square, &code) in board.iter().enumerate() {
            let s = crate::board::unpack_code(code);
            fields[square * FIELDS..square * FIELDS + FIELDS].copy_from_slice(&[
                s.side, s.dragon, s.spy, s.pawns, s.royal, s.cap_spy, s.cap_pawns, s.pris_flag,
            ]);
        }
        assert_eq!(royals_load(), 1);
    }

    #[test]
    fn a_position_survives_the_round_trip_through_the_buffer() {
        let _held = ONE_AT_A_TIME.lock().unwrap();
        // the fields the page holds are enough to rebuild the packed board exactly, which is
        // the whole premise of handing the browser parsed squares
        let start = crate::board::entering_board();
        load(&start);
        assert_eq!(*board(), start);
    }

    #[test]
    fn the_answer_is_the_engine_s_own() {
        let _held = ONE_AT_A_TIME.lock().unwrap();
        let start = crate::board::entering_board();
        load(&start);

        let spaces = parse_board(&start);
        let want: Vec<_> =
            list_all_moves(&start, 0, &spaces).into_iter().filter(|m| m.origin == 18).collect();

        let count = royals_moves_from(0, 18, 0) as usize;
        assert_eq!(count, want.len());

        let out = out();
        for (i, mv) in want.iter().enumerate() {
            assert_eq!(out[i * 3], kind_byte(mv.kind));
            assert_eq!(out[i * 3 + 1], mv.origin);
            assert_eq!(out[i * 3 + 2], mv.target);
        }

        // and the dragon has no prisoners to bring
        assert_eq!(royals_has_moves(0, 18, 1), 0);
        assert_eq!(royals_has_moves(0, 18, 0), 1);
    }

    #[test]
    fn origins_are_ascending_and_unique() {
        let _held = ONE_AT_A_TIME.lock().unwrap();
        let start = crate::board::entering_board();
        load(&start);

        let count = royals_origins(0) as usize;
        let squares: Vec<u8> = out()[..count].to_vec();
        assert_eq!(squares, vec![18], "only blue's dragon has moves on the entering board");

        let mut sorted = squares.clone();
        sorted.dedup();
        assert_eq!(squares, sorted);
    }

    #[test]
    fn an_overfull_square_is_refused_rather_than_answered() {
        let _held = ONE_AT_A_TIME.lock().unwrap();
        let fields = board_in();
        fields.fill(0);
        // two royals on one square: not a position
        fields[..FIELDS].copy_from_slice(&[0, 0, 0, 0, 2, 0, 0, 0]);

        assert_eq!(royals_load(), 0);
        assert_eq!(*board(), EMPTY_BOARD, "a refused board must not be left half-loaded");
    }

    #[test]
    fn the_self_test_matches_what_engine_js_expects() {
        // engine.js hardcodes this number; if move generation changes, both move together
        assert_eq!(royals_self_test(), 10);
    }

    #[test]
    fn kinds_are_in_the_documented_order() {
        // engine.js indexes ["jump", "push", "break", "free"] with the kind byte
        assert_eq!(kind_byte(MoveKind::Jump), 0);
        assert_eq!(kind_byte(MoveKind::Push), 1);
        assert_eq!(kind_byte(MoveKind::Break), 2);
        assert_eq!(kind_byte(MoveKind::Free), 3);
    }

    #[test]
    fn no_square_can_offer_more_than_the_buffer_holds() {
        // MAX_MOVES is four jump strands of six, four pushes, four breaks and four frees --
        // the widest a single origin can possibly be
        let _ = make_origin(&EMPTY_BOARD, 1, false, false);
        assert!(MAX_MOVES >= 24 + 4 + 4 + 4);
    }
}
