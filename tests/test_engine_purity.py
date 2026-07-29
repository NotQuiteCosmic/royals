"""The engine must stay importable by things that are not a terminal.

royals_engine is imported by a tkinter window, a terminal driver, and (soon) a web
worker serving many games at once. Those have almost nothing in common, and the only
reason one package can serve all of them is that it assumes nothing about who is
calling: no widgets, no stdin, no stdout.

That property was true by accident and is now checked. Before the package split the
engine held Engine.getOrigin (which blocked on input()), three ANSI-printing display
functions in Hasher, and a stray print("SHIT") on a backstop in getLegalPushLength.
All three would have been live bugs in a web worker -- a request that hangs forever
waiting on a stdin that isn't there, and debug text in the server log.

These tests read the source rather than importing and monkeypatching, so they catch a
violation on any code path, including ones no test exercises.
"""

import ast
import pathlib

import pytest

ENGINE_DIR = pathlib.Path(__file__).resolve().parent.parent / "engine" / "src" / "royals_engine"

# Modules a browser (Pyodide), a server worker, or a GUI process may not have, may not
# have meaningfully, or should never see the engine reach for.
BANNED_IMPORTS = {
    "tkinter",
    "curses",
    "socket",
    "subprocess",
    "http",
    "urllib",
    "pickle",       # never deserialize a board with this -- see the security plan
    "shelve",       # pickle in a trenchcoat
}

# print is banned outright rather than warned about: a library that prints has decided
# where its output goes, and in a server that decision is wrong.
BANNED_CALLS = {"input", "print", "breakpoint", "eval", "exec", "compile"}


def engine_modules():
    return sorted(ENGINE_DIR.glob("*.py"))


def test_engine_directory_is_where_we_think():
    mods = engine_modules()
    assert mods, f"no engine modules found under {ENGINE_DIR}"
    names = {m.name for m in mods}
    assert {"hasher.py", "engine.py", "ai.py", "perlin.py"} <= names, names


@pytest.mark.parametrize("path", engine_modules(), ids=lambda p: p.name)
def test_no_banned_imports(path):
    tree = ast.parse(path.read_text(), filename=str(path))

    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_IMPORTS:
                    bad.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_IMPORTS:
                bad.append((node.lineno, node.module))

    assert not bad, (
        f"{path.name} imports something the engine must not depend on: "
        + ", ".join(f"{name} (line {line})" for line, name in bad)
    )


@pytest.mark.parametrize("path", engine_modules(), ids=lambda p: p.name)
def test_no_io_calls(path):
    tree = ast.parse(path.read_text(), filename=str(path))

    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # bare name: print(...), input(...)
        if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
            bad.append((node.lineno, func.id))
        # attribute: sys.stdout.write(...), os.system(...)
        elif isinstance(func, ast.Attribute):
            if func.attr in ("system", "popen", "write") and isinstance(func.value, ast.Attribute):
                if getattr(func.value, "attr", None) in ("stdout", "stderr"):
                    bad.append((node.lineno, f"sys.{func.value.attr}.{func.attr}"))

    assert not bad, (
        f"{path.name} performs I/O the engine must leave to its caller: "
        + ", ".join(f"{name}() at line {line}" for line, name in bad)
        + ".\nMove it to apps/terminal/ (see display.py and prompt.py) or return the "
          "value and let the caller decide where it goes."
    )


@pytest.mark.parametrize("path", engine_modules(), ids=lambda p: p.name)
def test_only_stdlib_and_self_imports(path):
    """The engine has zero third-party dependencies. That is what lets the same source
    run under CPython, PyPy, and Pyodide without a compatibility matrix, and it keeps
    third-party code out of the path that validates moves."""
    allowed_stdlib = {
        "math", "random", "operator", "copy", "struct", "itertools",
        "functools", "collections", "typing", "dataclasses", "enum",
        # _accel.py reads ROYALS_NO_ACCEL. Reading an environment variable is not a
        # dependency and not I/O the caller should have to own -- it is how a deployment
        # says which of the two implementations it wants.
        "os",
    }

    # The one module the engine is allowed to reach outside itself for, and only when the
    # reach is guarded. royals_accel is the optional compiled wheel; the whole design rests
    # on it being absent being a supported configuration rather than a broken one, which is
    # exactly what a try/except ImportError expresses and an unguarded import does not.
    OPTIONAL_ACCELERATOR = "royals_accel"

    tree = ast.parse(path.read_text(), filename=str(path))

    # Import nodes sitting inside a `try` whose handlers catch ImportError. Collected up
    # front so the check below can tell a guarded optional import from a hard dependency --
    # the distinction the rule actually cares about.
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import_error = any(
            h.type is not None
            and (
                (isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "ModuleNotFoundError"))
                or (isinstance(h.type, ast.Tuple) and any(
                    isinstance(e, ast.Name) and e.id in ("ImportError", "ModuleNotFoundError")
                    for e in h.type.elts))
            )
            for h in node.handlers
        )
        if not catches_import_error:
            continue
        for stmt in node.body:
            for inner in ast.walk(stmt):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    guarded.add(id(inner))

    foreign = []
    for node in ast.walk(tree):
        roots = []
        if isinstance(node, ast.Import):
            roots = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:      # relative import, always our own
                continue
            roots = [(node.module or "").split(".")[0]]

        for root in roots:
            if not root or root in allowed_stdlib or root == "royals_engine":
                continue
            if root == OPTIONAL_ACCELERATOR and id(node) in guarded:
                continue
            foreign.append((node.lineno, root))

    assert not foreign, (
        f"{path.name} imports a non-stdlib, non-engine module: "
        + ", ".join(f"{name} (line {line})" for line, name in foreign)
        + ".\nKeep royals-engine dependency-free -- see engine/pyproject.toml."
    )
