//! The search, checked against `tests/golden_search.txt` — same moves, same scores, and
//! **the same node counts**.
//!
//! `sweepGames` plays four AI-vs-AI games and records, per move, what was played, what it
//! scored and how many boards the search looked at getting there. The first two say the port
//! plays the same game; the third says it walks the tree in the same order, which is the
//! claim that actually pins down move ordering, the killers, the history and every branch of
//! the transposition table. A port can play identical moves with a badly ported table and
//! only the node counts will tell you.
//!
//! The four games start from `enteredBoard(seed)`, which runs the entering search — Perlin
//! noise over floats through CPython's Mersenne Twister, and staying in Python forever (see
//! docs/PORTING.md §1). So the starting boards are read back out of the golden's own `seed`
//! lines rather than derived. That makes this file self-contained: `golden_search.txt` says
//! where each game began, and everything after that is this engine's.
//!
//! Node counts are *expected* to move when the search changes — `regress.py write search` is
//! a routine thing to do. What they must not do is move **because of the port**. If this
//! fails, compare against Python before re-recording anything.
//!
//! (`board_text` and `move_text` are also in `src/bin/royals-golden.rs`. They are duplicated
//! rather than shared because the binary and this test can't import each other, and because
//! moving them into the library would put a formatter for a test harness into the engine.)

use royals_engine::board::{build_space, index_to_alg, Board, EMPTY_BOARD, PUSH_DIRS};
use royals_engine::search::Search;
use royals_engine::tables::UNPACK;
use royals_engine::{Move, MoveKind};

const GOLDEN: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../tests/golden_search.txt");

/// Turns a game runs for before it is called off, and how many consecutive passes end it.
const TURNS: usize = 30;

#[test]
fn the_search_reproduces_the_golden() {
    let want: Vec<String> =
        std::fs::read_to_string(GOLDEN).expect("golden_search.txt").lines().map(String::from).collect();

    let mut got: Vec<String> = vec!["### GAMES".to_string()];

    for (seed, depth, start) in games(&want) {
        // a game at a time, so what one game's search learned doesn't carry into the next
        // one's numbers -- the table is meant to persist within a game, not beyond
        let mut search = Search::new();
        search.ko_reset();
        search.ko_record(&start);

        got.push(format!("seed {} depth {} {}", seed, depth, board_text(&start)));

        let mut board = start;
        let mut passes = 0;

        for turn in 0..TURNS {
            let contr = (turn % 2) as u8;
            let (next, mv, score) = search.take_turn(&board, contr, depth);
            board = next;

            got.push(format!(
                "  t{} side {} {} score {} nodes {}",
                turn,
                contr,
                move_text(mv.as_ref()),
                score,
                search.calc_count
            ));
            got.push(format!("  {}", board_text(&board)));

            if mv.is_none() {
                passes += 1;
                if passes > 1 {
                    break;
                }
                continue;
            }
            passes = 0;

            search.ko_record(&board);
            let (game_end, winner) = royals_engine::board::check_for_winner(&board);
            if game_end {
                got.push(format!("  finished [{}, {}]", winner[0], winner[1]));
                break;
            }
        }
    }

    for (i, (a, b)) in want.iter().zip(got.iter()).enumerate() {
        assert_eq!(a, b, "line {} differs\n  want {}\n  got  {}", i + 1, a, b);
    }
    assert_eq!(want.len(), got.len(), "the search produced a different number of lines");
}

/// The `seed N depth D <board>` lines, which is where each game starts.
fn games(golden: &[String]) -> Vec<(i32, i32, Board)> {
    let mut out = Vec::new();

    for line in golden {
        if !line.starts_with("seed ") {
            continue;
        }
        let mut parts = line.splitn(5, ' ');
        parts.next(); // "seed"
        let seed: i32 = parts.next().unwrap().parse().unwrap();
        parts.next(); // "depth"
        let depth: i32 = parts.next().unwrap().parse().unwrap();
        out.push((seed, depth, parse_board_text(parts.next().unwrap())));
    }

    assert_eq!(out.len(), 4, "golden_search.txt should hold four games");
    out
}

/// `board_text` backwards. Only the eight semantic fields are recorded, which is exactly what
/// `build_space` takes -- the derived scalars are answers those fields already contain.
fn parse_board_text(text: &str) -> Board {
    let mut board = EMPTY_BOARD;
    if text == "-empty-" {
        return board;
    }

    for square in text.split('|') {
        let (alg, fields) = square.split_once(':').expect("a square is alg:fields");
        let file = alg.as_bytes()[0] - b'a';
        let rank = alg[1..].parse::<usize>().unwrap() - 1;

        let f: Vec<u8> = fields.split(',').map(|n| n.parse().unwrap()).collect();
        assert_eq!(f.len(), 8, "a square is eight fields");

        board[rank * 7 + file as usize] =
            build_space(f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7]).unwrap();
    }

    board
}

fn board_text(board: &Board) -> String {
    let mut out: Vec<String> = Vec::new();

    for (i, &code) in board.iter().enumerate() {
        let s = UNPACK[code as usize];
        let fields =
            [s.side, s.dragon, s.spy, s.pawns, s.royal, s.cap_spy, s.cap_pawns, s.pris_flag];
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

fn move_text(mv: Option<&Move>) -> String {
    let mv = match mv {
        None => return "none".to_string(),
        Some(mv) => mv,
    };

    let what = match mv.kind {
        MoveKind::Break => {
            let d = PUSH_DIRS[mv.target as usize];
            format!("break ({}, {})", d[0], d[1])
        }
        kind => format!("{} {}", kind.as_str(), index_to_alg(mv.target)),
    };

    format!(
        "{} {}{}",
        index_to_alg(mv.origin - 1),
        what,
        if mv.moving_pris { " +pris" } else { "" }
    )
}
