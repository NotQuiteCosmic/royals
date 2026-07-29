//! Alpha-beta and the tables it carries. **Owned by W4.** All of the speed lives here.
//!
//! Stub. Ports `order_moves`, `minimax`, `choose_move`, `take_turn` and `new_game` from
//! `ai.py`, plus the two-generation transposition table, the killers and the history.
//!
//! Four things that produce a search which works and plays subtly worse if you get them
//! wrong:
//!
//! * `order_moves` sorts **stably on the score alone**, never on the move tuple, so ties
//!   keep board order. [`crate::Move`] is deliberately not `Ord` to keep that hard to break.
//! * Scores go into the table from the side-to-move's point of view and are negated on
//!   probe; the `LOWER`/`UPPER` bound flags swap with the sign.
//! * Root entries are stamped with `koGeneration`, everything below the root with -1.
//! * The staged-candidate optimisation -- try the stored move before generating anything --
//!   is safe *because* the table is keyed on the board itself rather than a Zobrist hash, so
//!   there are no collisions to guard against. Switching to Zobrist means adding move
//!   legality verification. Don't do both silently.
//!
//! The tables persist across moves within a game and are cleared by `new_game`, mirroring
//! the Python module-level globals -- that is what lets `ai_pool.py`'s load-one-game, run,
//! drop discipline keep working unchanged.

#![allow(unused_imports)]

use crate::board::Board;
use crate::eval::SCALE;
use crate::{Move, MoveKind};
