//! Royals, in Rust.
//!
//! This crate is an accelerator, not a replacement. `royals_engine` (Python) keeps its exact
//! API and stays the reference implementation; `tests/golden_moves.txt` is the contract both
//! sides answer to, which makes it a permanent differential oracle rather than a one-shot
//! check. See `docs/PORTING.md` for the spec and CLAUDE.md for the rule that governs it:
//! **if the golden moves and you did not mean to change the rules, this side has a bug.**
//!
//! The module map, and who owns what:
//!
//! | module     | contents                                        |
//! |------------|-------------------------------------------------|
//! | `board`    | square encoding, geometry, winner check          |
//! | `tables`   | everything computed at startup: UNPACK, the rays |
//! | `movegen`  | `check_moves` and the legality helpers           |
//! | `exec`     | the executors: move, push, break, drop           |
//! | `eval`     | integer-only evaluation                          |
//! | `search`   | ordering, alpha-beta, the transposition table    |
//! | `py`       | PyO3 bindings (feature `python`)                 |
//! | `wasm`     | wasm-bindgen bindings (feature `wasm`)           |
//!
//! Nothing in this file changes after the skeleton commit -- everything below is either a
//! module declaration or the one type every workstream has to agree on.

pub mod board;
pub mod eval;
pub mod exec;
pub mod movegen;
pub mod search;
pub mod tables;

#[cfg(feature = "python")]
pub mod py;

#[cfg(feature = "wasm")]
pub mod wasm;

pub use board::{Board, Square, BOARD_SQUARES, EMPTY_BOARD};

/// What a move does. The Python side carries these as the strings `"jump"`, `"push"`,
/// `"break"` and `"free"`; anything crossing the FFI or reaching a golden emitter converts
/// with [`MoveKind::as_str`].
///
/// `Free` is a push and not a separate rule -- it is kept apart because the destination
/// alone no longer says what the push does once prisoners are being freed onto it.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum MoveKind {
    Jump,
    Push,
    Break,
    Free,
}

impl MoveKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            MoveKind::Jump => "jump",
            MoveKind::Push => "push",
            MoveKind::Break => "break",
            MoveKind::Free => "free",
        }
    }

    pub fn from_str(s: &str) -> Option<MoveKind> {
        match s {
            "jump" => Some(MoveKind::Jump),
            "push" => Some(MoveKind::Push),
            "break" => Some(MoveKind::Break),
            "free" => Some(MoveKind::Free),
            _ => None,
        }
    }
}

/// The move tuple, `(origin, kind, target, movingPris)`, with the numbering the Python side
/// uses and which nothing may quietly normalise:
///
/// * `origin` is **1-based** -- a square number, 1 to 49.
/// * `target` is **0-based** -- an index into a parsed board -- *except* for
///   [`MoveKind::Break`], where it is an index into `pushDirs` (0 to 3) and not a square at
///   all.
/// * `moving_pris` says the stack is carrying its prisoners along.
///
/// Deliberately **not** `Ord`. `orderMoves` sorts stably on the score alone and never on the
/// move itself, so that ties keep board order; making a move comparable would let that rule
/// be broken by an ordinary `sort_by_key` and the only symptom would be a search that works
/// and plays subtly differently.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct Move {
    pub origin: u8,
    pub kind: MoveKind,
    pub target: u8,
    pub moving_pris: bool,
}

impl Move {
    pub const fn new(origin: u8, kind: MoveKind, target: u8, moving_pris: bool) -> Move {
        Move { origin, kind, target, moving_pris }
    }

    /// The 1-based destination square, for the three kinds that have one. A break scatters
    /// along a direction instead, so it has none.
    pub const fn destination(&self) -> Option<u8> {
        match self.kind {
            MoveKind::Break => None,
            _ => Some(self.target + 1),
        }
    }
}
