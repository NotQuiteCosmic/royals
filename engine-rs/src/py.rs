//! PyO3 bindings, built by maturin into `royals_engine._rust`. **Owned by W5.**
//! Behind the `python` feature.
//!
//! Stub. The Python side keeps its exact API: each accelerated function gets a shim at the
//! top and an untouched body underneath, so the pure-Python path stays the reference
//! implementation and the fallback when no wheel is installed. `ROYALS_NO_ACCEL=1` forces
//! that path even when the extension is present -- that switch is what the differential CI
//! job runs on.
//!
//! Release the GIL around the search: the desktop GUI runs the AI on a thread with a result
//! queue, and today that thread holds the GIL and competes with tkinter.
