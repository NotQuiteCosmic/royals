"""Whether a compiled accelerator is available, and the one switch that turns it off.

The engine has two implementations. The modules around this one are the reference: they are
pure standard library, they run under CPython and PyPy alike, and they are what
`tests/golden_moves.txt` was recorded from. `royals_accel` is an optional wheel carrying the
same rules compiled, and installing it is the only thing that turns it on. (PyPy never has
it: the wheel is abi3 CPython, so PyPy is always this path, which is one of the reasons it is
worth keeping fast.)

**Not installing it is a supported configuration, not a degraded one.** That is what lets
install-desktop.sh promise that nothing needs installing, and it is why this module fails
quietly rather than loudly -- an ImportError here means the game runs in Python, which is
correct, just slower.

    ROYALS_NO_ACCEL=1   force the pure-Python path even where the wheel is installed

That switch exists for the differential CI job, which runs the whole suite twice and requires
byte-identical goldens from both. Two implementations that are never compared are two
implementations that have already drifted; the switch is what makes the comparison possible.

Read it once, at import. Flipping the environment variable mid-process would otherwise let
one half of a search run compiled and the other half not, and any disagreement that produced
would be blamed on the rules rather than on the harness.
"""

import os

# The name is deliberately top-level rather than `royals_engine._rust`. An editable install
# points this package's __path__ at the source tree while the wheel installs into
# site-packages, so a submodule would be unimportable in exactly the layout this repo is
# developed in -- and it would fail by silently falling back, which is the failure mode
# hardest to notice. See engine-rs/src/py.rs.
_MODULE = "royals_accel"

# "1", "true", "yes" and friends all mean on; anything else, including empty, means off. An
# empty string is the shape a shell leaves behind after `unset`-adjacent mistakes, and reading
# it as true would disable the accelerator for people who thought they had cleared the flag.
_forced_off = os.environ.get("ROYALS_NO_ACCEL", "").strip().lower() in ("1", "true", "yes", "on")

accel = None
if not _forced_off:
    try:
        import royals_accel as accel  # noqa: F401
    except ImportError:
        accel = None


def active():
    """Whether calls are being served by the compiled engine."""
    return accel is not None


def describe():
    """One line for a banner or a test failure, saying which implementation answered.

    Worth printing somewhere a person will see. The two implementations are meant to agree
    exactly, so the only symptom of the wheel being absent is that everything is several times
    slower -- which is easy to mistake for the machine being busy.
    """
    if accel is not None:
        return "royals_engine: compiled accelerator (%s)" % _MODULE
    if _forced_off:
        return "royals_engine: pure Python (ROYALS_NO_ACCEL is set)"
    return "royals_engine: pure Python (no %s installed)" % _MODULE
