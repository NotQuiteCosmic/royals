//! Emits the MOVES sweep, in exactly the format `tests/regress.py` produces.
//!
//! ```text
//! cargo run --release --bin royals-golden | diff - ../tests/golden_moves.txt
//! ```
//!
//! Silence there is what "the rules are ported" means. `golden_moves.txt` states, for a
//! spread of reachable positions, every legal move both sides have and the board each one
//! produces -- in terms of parsed fields and square numbers, never in terms of how a board is
//! stored. Any implementation that plays the same game produces the file byte for byte.
//!
//! The sweep is not self-contained: Python walks it with `random.Random(seed)`, which no
//! other language reproduces without reimplementing MT19937. So the RNG's answers are read
//! out of `tests/fixtures/port_fixtures.json` instead, as move *indices* into
//! `list_all_moves`' output. That is a stricter test than it sounds: if the generation order
//! differs by one entry, index 7 is a different move, the walk diverges on the next line, and
//! the diff points straight at it.
//!
//! Every line, including the `eval` pair after each move: `fullCheck(child, 0)` then
//! `fullCheck(child, 1)`, printed as exact integers.

use std::io::{BufWriter, Write};

use royals_engine::board::{check_for_winner, index_to_alg, Board, EMPTY_BOARD, PUSH_DIRS};
use royals_engine::eval::full_check;
use royals_engine::exec::perform_one_step;
use royals_engine::movegen::{list_all_moves, parse_board};
use royals_engine::tables::UNPACK;
use royals_engine::{Move, MoveKind};

/// The seeds `sweepMoves` walks, in order.
const MOVE_SEEDS: [i64; 3] = [3, 11, 57];

/// Turns per seed, and the number of move indices each walk carries.
const TURNS: usize = 40;

fn main() {
    // `--push-range` emits the variant's contract, `tests/golden_push_moves.txt`, instead of
    // the standard one. Two rule sets, two goldens, and this binary answers to both.
    let variant = std::env::args().any(|a| a == "--push-range");
    royals_engine::set_push_range(variant);

    let fixtures = load_fixtures(if variant { "push_walks" } else { "walks" });
    let stdout = std::io::stdout();
    let mut out = BufWriter::new(stdout.lock());

    let mut emit = |line: String| {
        writeln!(out, "{line}").expect("stdout closed");
    };

    emit("### MOVES".to_string());

    for (i, walk) in fixtures.iter().enumerate() {
        assert_eq!(walk.seed, MOVE_SEEDS[i], "fixture walks are out of order");
        let final_board = sweep_seed(walk, &mut emit);

        // The walk's own end state, checked against what Python reached. A silent diff proves
        // the emitted lines; this proves the forty moves played *between* them, which the
        // file only records indirectly.
        assert_eq!(
            final_board, walk.final_board,
            "seed {}: the walk ended on a different board than the fixture records",
            walk.seed
        );
    }
}

fn sweep_seed(walk: &Walk, emit: &mut impl FnMut(String)) -> Board {
    let mut board = walk.start;

    for turn in 0..TURNS {
        let contr = (turn % 2) as u8;

        for side in 0..2u8 {
            let spaces = parse_board(&board);
            let mut moves = list_all_moves(&board, side, &spaces);
            emit(format!("s{} t{} side {} moves {}", walk.seed, turn, side, moves.len()));

            // sorted by the printed move, and stably -- Python's list.sort is stable too, so
            // two moves with the same text keep the order generation gave them
            moves.sort_by_key(move_text);

            for mv in moves {
                let child = perform_one_step(&board, side, mv);
                emit(format!("  {} => {}", move_text(&mv), board_text(&child)));

                emit(eval_line(&child));
            }
        }

        let (game_end, winner) = check_for_winner(&board);
        // Python's str() of a bool and of a two-element list
        emit(format!(
            "  winner {} [{}, {}]",
            if game_end { "True" } else { "False" },
            winner[0],
            winner[1]
        ));
        if game_end {
            break;
        }

        let spaces = parse_board(&board);
        let moves = list_all_moves(&board, contr, &spaces);
        if moves.is_empty() {
            break;
        }

        let pick = walk.choices[turn] as usize;
        assert!(
            pick < moves.len(),
            "seed {} turn {}: fixture picks move {} of {} -- move generation has diverged",
            walk.seed,
            turn,
            pick,
            moves.len()
        );
        board = perform_one_step(&board, contr, moves[pick]);
    }

    board
}

/// The evaluator's two scores for a position.
///
/// Whole numbers of thousandths, printed exactly -- a rounded print is what let a
/// cross-interpreter difference hide in this file in the first place.
fn eval_line(child: &Board) -> String {
    format!("    eval {} {}", full_check(child, 0), full_check(child, 1))
}

/// A board as 49 parsed squares, empties collapsed so the line stays readable.
///
/// **Only the eight semantic fields** -- side through prisFlag, everything an arrangement of
/// pieces actually is. A parsed square carries derived scalars after those, but they are
/// answers the fields already contain, so printing them would put the same fact in the file
/// twice and make an optimisation look like a change.
fn board_text(board: &Board) -> String {
    let mut out: Vec<String> = Vec::new();

    for (i, &code) in board.iter().enumerate() {
        let s = UNPACK[code as usize];
        let fields = [
            s.side, s.dragon, s.spy, s.pawns, s.royal, s.cap_spy, s.cap_pawns, s.pris_flag,
        ];
        if fields.iter().all(|&f| f == 0) {
            continue;
        }

        let joined: Vec<String> = fields.iter().map(|f| f.to_string()).collect();
        out.push(format!("{}:{}", index_to_alg(i as u8), joined.join(",")));
    }

    if out.is_empty() {
        "-empty-".to_string()
    } else {
        out.join("|")
    }
}

fn move_text(mv: &Move) -> String {
    // A break's direction is carried as an index into PUSH_DIRS; spelled back out as the
    // pair, so what this records is the move and not how the move happens to be stored.
    // Python prints the tuple, hence the space after the comma.
    let what = match mv.kind {
        MoveKind::Break => {
            let d = PUSH_DIRS[mv.target as usize];
            format!("break ({}, {})", d[0], d[1])
        }
        kind => format!("{} {}", kind.as_str(), index_to_alg(mv.target)),
    };

    // How far a push travels, written only where the move carries it -- distance 1 is the
    // plain move, so golden_moves.txt is untouched by this existing. Without it two pushes
    // down the same ray render identically and the file records one move twice.
    let far = if mv.kind == MoveKind::Push && mv.travel > 1 {
        format!(" x{}", mv.travel)
    } else {
        String::new()
    };

    format!(
        "{} {}{}{}",
        index_to_alg(mv.origin - 1),
        what,
        far,
        if mv.moving_pris { " +pris" } else { "" }
    )
}

// ---- the fixtures -----------------------------------------------------------------------
// port_fixtures.json is machine-written by `python3 regress.py fixtures` with sorted keys, so
// the four fields of each walk arrive in a known order and a scanner does the job a JSON
// library would. Keeping this dependency-free is worth a few lines: the crate builds with
// nothing fetched, which is what makes `cargo test` usable offline.

struct Walk {
    seed: i64,
    start: Board,
    choices: Vec<i64>,
    final_board: Board,
}

/// `key` is `"walks"` for the standard rules and `"push_walks"` for the variant. They are
/// recorded separately because a walk is a list of *indices* into `list_all_moves`' output
/// and the variant's output is longer, so index 7 names a different move: one set of indices
/// cannot serve both rule sets.
fn load_fixtures(key: &str) -> Vec<Walk> {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../tests/fixtures/port_fixtures.json");
    let src = std::fs::read_to_string(path).unwrap_or_else(|e| {
        panic!("{path}: {e} -- run `cd tests && python3 regress.py fixtures`")
    });

    let quoted = format!("\"{key}\"");
    let mut at = find_key(&src, &quoted, 0);
    let mut walks = Vec::new();

    // sort_keys puts them in this order inside each walk object. Stop at the expected count:
    // "push_walks" sorts before "walks", so scanning past the end of one section runs
    // straight into the other and would read six walks where there are three.
    while walks.len() < MOVE_SEEDS.len() {
        let Some(next) = src[at..].find("\"choices\"").map(|i| at + i) else { break };
        let (choices, after) = read_array(&src, find_colon(&src, next));
        let (final_board, after) = read_array(&src, find_colon(&src, find_key(&src, "\"final\"", after)));
        let (seed, after) = read_number(&src, find_colon(&src, find_key(&src, "\"seed\"", after)));
        let (start, after) = read_array(&src, find_colon(&src, find_key(&src, "\"start\"", after)));

        walks.push(Walk {
            seed,
            start: to_board(&start),
            choices,
            final_board: to_board(&final_board),
        });
        at = after;
    }

    assert_eq!(walks.len(), MOVE_SEEDS.len(), "fixtures carry a different number of walks");
    walks
}

fn to_board(values: &[i64]) -> Board {
    assert_eq!(values.len(), 49, "a board is 49 squares");
    let mut board = EMPTY_BOARD;
    for (i, &v) in values.iter().enumerate() {
        board[i] = v as u16;
    }
    board
}

fn find_key(src: &str, key: &str, from: usize) -> usize {
    from + src[from..].find(key).unwrap_or_else(|| panic!("fixtures have no {key}"))
}

fn find_colon(src: &str, from: usize) -> usize {
    from + src[from..].find(':').expect("fixture key with no value") + 1
}

fn read_number(src: &str, from: usize) -> (i64, usize) {
    let start = from + src[from..].find(|c: char| c == '-' || c.is_ascii_digit()).unwrap();
    let end = start
        + src[start..].find(|c: char| !(c == '-' || c.is_ascii_digit())).unwrap_or(src.len() - start);
    (src[start..end].parse().unwrap(), end)
}

/// A flat array of integers. Everything after `"walks"` is one of these.
fn read_array(src: &str, from: usize) -> (Vec<i64>, usize) {
    let open = from + src[from..].find('[').expect("expected an array");
    let close = open + src[open..].find(']').expect("unterminated array");

    let values = src[open + 1..close]
        .split(',')
        .map(|piece| piece.trim())
        .filter(|piece| !piece.is_empty())
        .map(|piece| piece.parse().expect("non-integer in a fixture array"))
        .collect();

    (values, close + 1)
}
