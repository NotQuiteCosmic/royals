//! The evaluator. **Owned by W4.**
//!
//! Stub. Ports `evaluate_sides`, `spread_penalty` and `full_check` from `ai.py`.
//!
//! **Integer only.** Floats already caused a real bug once: CPython and PyPy disagreed in
//! the last place on a square root, which flipped an alpha-beta cutoff and changed the move
//! played. `math.isqrt` maps to `u64::isqrt` (or a hand-rolled floor sqrt on older
//! toolchains); the floor divisions to watch are `spread_penalty`'s `// (2 * n)` and
//! `check_break`'s `(i / 7) + 1`.

#![allow(unused_imports)]

use crate::board::{Board, Square};
use crate::tables::{JUMPDIST, JUMPREACH, UNPACK};

/// Scores are fixed-point integers with three decimal places. Nothing here is ever a float.
pub const SCALE: i32 = 1000;
