//! wasm-bindgen bindings for the browser client. **Owned by W6.** Behind the `wasm` feature.
//!
//! Stub. Enough of the engine to answer "where can this piece go" in the page, so legality
//! highlighting stops costing a round trip and a finished game can be replayed offline.
//!
//! Single-threaded, so no `SharedArrayBuffer` and therefore no COOP/COEP headers -- the
//! search is serial anyway, so nothing is given up. **The server stays authoritative:**
//! `game.py`'s validation is what stops a hostile client and does not move.
