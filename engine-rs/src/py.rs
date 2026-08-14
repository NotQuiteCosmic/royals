//! PyO3 bindings, built by maturin into the top-level module `royals_accel`.
//! Behind the `python` feature.
//!
//! # What this is, and what it deliberately is not
//!
//! An accelerator with no opinions. `royals_engine` keeps its exact Python API and stays the
//! reference implementation; every function here has a pure-Python twin that remains the
//! definition of what the answer should be. `tests/golden_moves.txt` is what both sides answer
//! to, and CI runs the suite twice -- once with this module importable and once with
//! `ROYALS_NO_ACCEL=1` -- so the two cannot drift apart without saying so.
//!
//! # Why `royals_accel` and not `royals_engine._rust`
//!
//! The obvious name was a submodule of the package it accelerates. It does not survive contact
//! with an editable install, which is how this repo is developed and how CI installs the
//! engine: `pip install -e ./engine` points `royals_engine.__path__` at the source tree, while
//! the wheel drops its extension into site-packages. Different directories, so
//! `import royals_engine._rust` fails in precisely the setup everybody actually uses -- and it
//! fails by falling back to Python, which is silent and looks like the accelerator merely
//! being slow.
//!
//! A top-level module has no such coupling, and it keeps two distributions from owning files
//! in one directory, which is the other thing that makes wheels fight.
//!
//! # The boundary
//!
//! The search crosses this boundary **once per move, not once per node**, so marshalling cost
//! is irrelevant where it would matter. A board is 49 small integers.
//!
//! Two of the shapes here are load-bearing rather than stylistic:
//!
//! * **Boards come back as tuples, never lists.** A board is its own key in the ko set and the
//!   transposition table. Hand Python a list and `koTrack` membership stops working -- not with
//!   an error, but by quietly accepting the repetitions the ko rule exists to forbid.
//! * **Moves keep the 1-based origin against the 0-based target**, and for a break the target
//!   is a direction index rather than a square. `performOneStep` adds one for three kinds and
//!   passes the fourth through untouched. Normalising that here would be invisible right up
//!   until the goldens moved everywhere at once.
//!
//! # The ko record
//!
//! The Rust side keeps one global `Search`, mirroring the Python module-level globals it stands
//! in for. The ko history is **passed in on every search call** rather than mirrored, because
//! `ai_pool.py` already owns that discipline: load one game's state, run, drop it. Leaving the
//! authority on the Python side means the pool needs no changes at all.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyTuple;

use crate::board::{self, Board, BOARD_SQUARES};
use crate::eval::{self, Score};
use crate::exec;
use crate::movegen;
use crate::search;
use crate::{Move, MoveKind};

/// A move as Python spells it: `(origin, kind, target, movingPris)`, and under the push-range
/// variant `(origin, kind, target, movingPris, travel)`.
///
/// **Two arities, and that is the contract rather than an accident.** `ai.listMoves` emits the
/// fifth element only where the ceiling is above one, so a standard game's move list is the
/// four-tuple it has always been and nothing reading one can tell this rule exists. The
/// variants are tried longest-first, because a four-tuple would also match a prefix of five.
#[derive(FromPyObject)]
enum PyMoveIn {
    Ranged((u8, String, u8, bool, u8)),
    Plain((u8, String, u8, bool)),
}

impl PyMoveIn {
    fn parts(&self) -> (u8, &str, u8, bool, u8) {
        match self {
            PyMoveIn::Ranged((o, k, t, p, v)) => (*o, k.as_str(), *t, *p, *v),
            PyMoveIn::Plain((o, k, t, p)) => (*o, k.as_str(), *t, *p, 1),
        }
    }
}

// ---- conversions -------------------------------------------------------------------------

fn to_board(cells: Vec<u16>) -> PyResult<Board> {
    if cells.len() != BOARD_SQUARES {
        return Err(PyValueError::new_err(format!(
            "a board is {BOARD_SQUARES} squares, got {}",
            cells.len()
        )));
    }
    let mut board: Board = [0; BOARD_SQUARES];
    for (i, code) in cells.iter().enumerate() {
        // A code past the table would index out of bounds deep inside move generation, where
        // the message would be about a slice rather than about the caller.
        if (*code as usize) >= board::CODE_RANGE {
            return Err(PyValueError::new_err(format!(
                "square {} holds {code}, which is not a 13-bit square code",
                i + 1
            )));
        }
        board[i] = *code;
    }
    Ok(board)
}

/// A board back to Python **as a tuple**. See the module docs: hashability is not a detail.
fn from_board<'py>(py: Python<'py>, board: &Board) -> Bound<'py, PyTuple> {
    PyTuple::new_bound(py, board.iter())
}

/// A side is 0 or 1.
///
/// `full_check` computes `adv[contr] - adv[1 - contr]` over a two-element array, so an
/// unchecked value here is an out-of-bounds index -- which surfaces to Python as a
/// `PanicException` and a line of `thread '<unnamed>' panicked` on stderr. That is the wrong
/// type to catch and the wrong thing to find in a server log, and the pure-Python engine
/// raises `ValueError` for the same input. The two implementations agreeing on what a refusal
/// looks like is part of what makes them interchangeable.
fn check_side(contr: u8) -> PyResult<u8> {
    if contr > 1 {
        return Err(PyValueError::new_err(format!(
            "side must be 0 (blue) or 1 (red), got {contr}"
        )));
    }
    Ok(contr)
}

/// Reject a move that is not one, matching `ai.checkMove` exactly -- including which of
/// `ValueError` and `IndexError` each failure earns, since a caller that catches one and not
/// the other must not care which engine answered.
fn to_move(m: &PyMoveIn) -> PyResult<Move> {
    let (origin, kind_str, target, moving_pris, travel) = m.parts();

    let kind = MoveKind::from_str(kind_str)
        .ok_or_else(|| PyValueError::new_err(format!("{:?} is not a move kind", kind_str)))?;

    if origin < 1 || origin > 49 {
        return Err(pyo3::exceptions::PyIndexError::new_err(format!(
            "origin {} is off the board (squares run 1 to 49)",
            origin
        )));
    }

    // A break's target is a direction index into pushDirs, not a square. The asymmetry is the
    // engine's oldest trap -- see docs/PORTING.md -- so the bound depends on the kind.
    if kind == MoveKind::Break {
        if target as usize >= board::PUSH_DIRS.len() {
            return Err(pyo3::exceptions::PyIndexError::new_err(format!(
                "break direction {} is not one of 0 to {}",
                target,
                board::PUSH_DIRS.len() - 1
            )));
        }
    } else if target > 48 {
        return Err(pyo3::exceptions::PyIndexError::new_err(format!(
            "target {} is off the board (0-based squares run 0 to 48)",
            target
        )));
    }

    Ok(Move { origin, kind, target, moving_pris, travel: travel.max(1) })
}

/// Back to Python, with the fifth element present only where it means something. See
/// [`PyMoveIn`] -- a standard game's moves come back the shape they always have.
fn from_move(py: Python<'_>, m: Move) -> PyObject {
    if m.travel <= 1 {
        (m.origin, m.kind.as_str(), m.target, m.moving_pris).into_py(py)
    } else {
        (m.origin, m.kind.as_str(), m.target, m.moving_pris, m.travel).into_py(py)
    }
}

// ---- the pure ones -----------------------------------------------------------------------
// Nothing below touches the global search state, so none of it needs the ko record and none
// of it has to be serialised against anything.

#[pyfunction]
fn list_all_moves(py: Python<'_>, board: Vec<u16>, contr: u8) -> PyResult<Vec<PyObject>> {
    let contr = check_side(contr)?;
    let board = to_board(board)?;
    let spaces = movegen::parse_board(&board);
    Ok(movegen::list_all_moves(&board, contr, &spaces)
        .into_iter()
        .map(|m| from_move(py, m))
        .collect())
}

#[pyfunction]
fn perform_one_step<'py>(
    py: Python<'py>,
    board: Vec<u16>,
    contr: u8,
    mv: PyMoveIn,
) -> PyResult<Bound<'py, PyTuple>> {
    let contr = check_side(contr)?;
    let board = to_board(board)?;
    let mv = to_move(&mv)?;
    Ok(from_board(py, &exec::perform_one_step(&board, contr, mv)))
}

#[pyfunction]
fn evaluate_sides(board: Vec<u16>) -> PyResult<(Score, Score)> {
    let board = to_board(board)?;
    let adv = eval::evaluate_sides(&board);
    Ok((adv[0], adv[1]))
}

#[pyfunction]
fn full_check(board: Vec<u16>, contr: u8) -> PyResult<Score> {
    let contr = check_side(contr)?;
    let board = to_board(board)?;
    Ok(eval::full_check(&board, contr))
}

#[pyfunction]
fn check_for_winner(board: Vec<u16>) -> PyResult<(bool, (u8, u8))> {
    let board = to_board(board)?;
    let (over, winner) = board::check_for_winner(&board);
    Ok((over, (winner[0], winner[1])))
}

// ---- the search --------------------------------------------------------------------------

/// Load one game's ko history, run `f` against the global search state, hand back the result.
///
/// The GIL is released for the duration. That is not a micro-optimisation: the desktop GUI
/// runs the AI on a `threading.Thread` with a result queue, and while that thread holds the
/// GIL it competes with tkinter for it -- the window stops repainting for the length of the
/// search. Releasing it costs nothing here, because no Python object is touched inside, and it
/// is the difference between a frozen window and a live one.
fn with_game<T, F>(
    py: Python<'_>,
    ko_boards: Vec<Vec<u16>>,
    table_limit: usize,
    f: F,
) -> PyResult<T>
where
    F: FnOnce(&mut search::Search) -> T + Send,
    T: Send,
{
    let mut ko = Vec::with_capacity(ko_boards.len());
    for cells in ko_boards {
        ko.push(to_board(cells)?);
    }

    Ok(py.allow_threads(move || {
        let mut game = search::game();
        game.set_ko_track(ko);
        // Pushed across on every call rather than set once through a separate function, so that
        // `AI.TABLE_LIMIT = N` keeps meaning what it has always meant and nobody has to remember
        // a setter. ai_pool.py drops it to 50,000 because a few hundred MB per generation is
        // fatal on the box it runs on; before this it assigned to a Python global the compiled
        // engine never read, and the cap silently stopped applying the moment a wheel was
        // installed. One integer per search is not a cost worth optimising away.
        game.set_table_limit(table_limit);
        // The table itself is deliberately not cleared afterwards. It is meant to persist across
        // the moves of one game -- that is most of its value -- and ai_pool.py is what decides
        // when a worker stops being this game's worker, via AI.newGame().
        f(&mut game)
    }))
}

#[pyfunction]
#[pyo3(signature = (board, contr, depth, ko_boards, table_limit))]
fn choose_move(
    py: Python<'_>,
    board: Vec<u16>,
    contr: u8,
    depth: i32,
    ko_boards: Vec<Vec<u16>>,
    table_limit: usize,
) -> PyResult<(Score, Option<PyObject>, u64)> {
    let contr = check_side(contr)?;
    let board = to_board(board)?;
    let (score, mv, nodes) = with_game(py, ko_boards, table_limit, move |game| {
        let (score, mv) = game.choose_move(&board, contr, depth);
        (score, mv, game.calc_count)
    })?;
    Ok((score, mv.map(|m| from_move(py, m)), nodes))
}

#[pyfunction]
#[pyo3(signature = (board, contr, depth, ko_boards, table_limit))]
fn take_turn<'py>(
    py: Python<'py>,
    board: Vec<u16>,
    contr: u8,
    depth: i32,
    ko_boards: Vec<Vec<u16>>,
    table_limit: usize,
) -> PyResult<(Bound<'py, PyTuple>, Option<PyObject>, Score, u64)> {
    let contr = check_side(contr)?;
    let board = to_board(board)?;
    let (next, mv, score, nodes) = with_game(py, ko_boards, table_limit, move |game| {
        let (next, mv, score) = game.take_turn(&board, contr, depth);
        (next, mv, score, game.calc_count)
    })?;
    Ok((from_board(py, &next), mv.map(|m| from_move(py, m)), score, nodes))
}

/// Forget everything learned about the game just played.
///
/// `ai_pool.py` calls the Python `AI.newGame()` between jobs precisely so that game B's
/// positions never land in game A's tables. That call has to reach this side too, or a worker
/// carries one game's transposition table into the next -- not a crash, just a search
/// answering questions about a tree belonging to a different game.
#[pyfunction]
fn new_game() {
    search::new_game();
}

/// Choose the rule set for the game about to be played, mirroring `Engine.setPushRange`.
///
/// **Between games and never within one.** `Engine.setPushRange` calls this so the two halves
/// cannot disagree: without it the Python would play the variant and this side the standard
/// game, and the only symptom would be a computer answering a four-square push as though it
/// had been one square.
#[pyfunction]
fn set_push_range(on: bool) {
    crate::set_push_range(on);
}

// ---- module ------------------------------------------------------------------------------

#[pymodule]
fn royals_accel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("SCALE", eval::SCALE)?;
    m.add("WIN_SCORE", eval::WIN_SCORE)?;

    m.add_function(wrap_pyfunction!(list_all_moves, m)?)?;
    m.add_function(wrap_pyfunction!(perform_one_step, m)?)?;
    m.add_function(wrap_pyfunction!(evaluate_sides, m)?)?;
    m.add_function(wrap_pyfunction!(full_check, m)?)?;
    m.add_function(wrap_pyfunction!(check_for_winner, m)?)?;
    m.add_function(wrap_pyfunction!(choose_move, m)?)?;
    m.add_function(wrap_pyfunction!(take_turn, m)?)?;
    m.add_function(wrap_pyfunction!(new_game, m)?)?;
    m.add_function(wrap_pyfunction!(set_push_range, m)?)?;
    Ok(())
}
