//! What the port was for. Times one search from a real position, at increasing depths.
//!
//! ```text
//! cargo run --release --example bench
//! ```
//!
//! The position is the board `golden_search.txt`'s first game starts from -- an entered
//! board, nothing yet developed -- so the numbers are comparable against the Python engine
//! doing the same thing:
//!
//! ```python
//! # from the repo root, with the same board
//! import time; from royals_engine import ai as AI
//! t = time.time(); AI.chooseMove(board, 0, depth)
//! print(AI.calcCount, time.time() - t)
//! ```
//!
//! Node counts are identical between the two by construction -- `tests/golden_search.rs` is
//! what holds them to that -- so nodes per second is a like-for-like measurement and not an
//! artefact of one side searching less.

use std::time::Instant;

use royals_engine::board::{build_space, Board, EMPTY_BOARD};
use royals_engine::search::Search;

/// `golden_search.txt`, first line of the first game.
const START: &str = "g1:0,0,0,1,0,0,0,0|b2:1,0,0,1,0,0,0,0|d2:1,0,0,1,0,0,0,0|f2:1,0,0,0,1,0,0,0|\
d3:0,1,0,0,0,0,0,0|e3:1,0,1,0,0,0,0,0|b4:0,0,0,1,0,0,0,0|f4:1,0,0,1,0,0,0,0|c5:0,0,1,0,0,0,0,0|\
d5:1,1,0,0,0,0,0,0|b6:0,0,0,0,1,0,0,0|d6:0,0,0,1,0,0,0,0|f6:0,0,0,1,0,0,0,0|a7:1,0,0,1,0,0,0,0";

fn main() {
    let board = parse(START);

    // how deep to go; the Python engine takes minutes past 6
    let deepest: i32 = std::env::args().nth(1).and_then(|a| a.parse().ok()).unwrap_or(6);

    println!("{:>5}  {:>12}  {:>9}  {:>12}", "depth", "nodes", "seconds", "nodes/s");

    for depth in 1..=deepest {
        // a fresh state per depth, so each line is one search from cold rather than a search
        // that inherited the last one's table
        let mut search = Search::new();
        search.ko_record(&board);

        let started = Instant::now();
        let (score, mv) = search.choose_move(&board, 0, depth);
        let elapsed = started.elapsed().as_secs_f64();

        println!(
            "{:>5}  {:>12}  {:>9.3}  {:>12.0}   {:?} score {}",
            depth,
            search.calc_count,
            elapsed,
            search.calc_count as f64 / elapsed,
            mv,
            score
        );
    }
}

fn parse(text: &str) -> Board {
    let mut board = EMPTY_BOARD;

    for square in text.split('|') {
        let (alg, fields) = square.split_once(':').unwrap();
        let file = (alg.as_bytes()[0] - b'a') as usize;
        let rank = alg[1..].parse::<usize>().unwrap() - 1;

        let f: Vec<u8> = fields.split(',').map(|n| n.parse().unwrap()).collect();
        board[rank * 7 + file] =
            build_space(f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7]).unwrap();
    }

    board
}
