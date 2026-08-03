"""What the desktop board is painted with, as data rather than as source.

The window's look used to be thirty-three colour constants at the top of royals_gui.py, which
meant a player who wanted a different board had to edit the program. This is the same
thirty-three, plus the two things that are not colours -- how heavy the lines are, and whether
the board sits on a gradient -- in a form that can be read from a file, handed round, and
checked before it is believed.

**No tkinter in here.** Not tidiness: it is what lets the schema be tested without a display,
on a headless runner where every other desktop test skips. The half of this feature that can
go quietly wrong is the schema drifting from the palette it claims to describe, and that is
exactly the half a machine with no screen can still check -- see tests/test_theme.py, whose
first test holds DEFAULT against royals_gui's own constants in both directions.

**DEFAULT is the current palette copied out, not computed from a formula.** Those values were
arrived at by measuring: the dark square is held at 3.0 to 1 against a black piece and 4.8
against the light square, and the comment above them in royals_gui.py explains what each step
either way would cost. A `derive()` clever enough to regenerate them would still be a second
opinion about them, and the first time it disagreed the board would quietly change. So the
default theme is a table, and derivation is used only for colours nobody has measured -- the
ones a player invents in the appearance screen.
"""

import colorsys
import json
import pathlib
import re


class ThemeError(ValueError):
    """A theme is missing something, carries something unknown, or is not a theme at all."""


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------

# Every colour royals_gui paints with, in the order the appearance screen shows them, grouped
# by the role a player would think in. `base` is the one the screen offers; the rest of each
# group follows it through derive() unless the file pins them.
#
# The grouping is the whole reason the screen is usable: thirty-three swatches is a
# spreadsheet, eight is a decision. Everything not named as a base is a shade *of* a base --
# a bevel highlight, a dimmer text, the tint a square takes when it was played from -- and
# none of them is a choice anybody wants to make one at a time.
ROLES = (
    ("background", "Background", "PANEL", ("PANEL_LT", "PANEL_DK", "WELL")),
    ("cabinet", "Cabinet", "EDGE", ("EDGE_DK", "EDGE_LT", "RULE", "RULE_LT", "RULE_DK")),
    ("boardLight", "Light squares", "LIGHT",
     ("LIGHT_HI", "LIGHT_LO", "LIGHT_LAST", "LIGHT_ENTRY")),
    ("boardDark", "Dark squares", "DARK",
     ("DARK_HI", "DARK_LO", "DARK_LAST", "DARK_ENTRY")),
    ("ink", "Ink and outlines", "INK", ()),
    ("whitePieces", "White pieces", "WHITE",
     ("WHITE_HI", "WHITE_LO", "WHITE_RIM", "PIECE_HALO")),
    ("blackPieces", "Black pieces", "BLACK", ("BLACK_HI", "BLACK_LO")),
    ("text", "Lettering", "TEXT", ("TEXT_DIM", "TEXT_KEY", "LABEL")),
)

# Every colour name a theme carries, derived from ROLES so the two cannot disagree.
KEYS = tuple(base for _, _, base, _ in ROLES) + tuple(
    key for _, _, _, deps in ROLES for key in deps)

# The three stroke weights, and what each is for. Named rather than numbered because the
# difference between them is a difference in kind: a hairline is a silhouette, a piece line
# is an edge you are meant to see, a mark line is a thing being said to you.
WEIGHTS = ("hair", "piece", "mark")

# What Fine / Normal / Heavy write. Normal is what the board has always drawn.
WEIGHT_PRESETS = {
    "fine": {"hair": 1, "piece": 1, "mark": 3},
    "normal": {"hair": 1, "piece": 2, "mark": 4},
    "heavy": {"hair": 2, "piece": 3, "mark": 6},
}

DIRECTIONS = ("vertical", "horizontal", "diagonal")

# Bands, not a gradient: the canvas has no gradient primitive and no alpha, so a wash is N
# rectangles of interpolated fill. Twenty-four is where the banding stops being visible at the
# sizes this window opens at; the ceiling is there because every band is one more canvas item
# competing with the two thousand the board already draws each frame.
BANDS_MIN, BANDS_MAX = 2, 64

DEFAULT = {
    "name": "Default",
    "colours": {
        "INK": "#0d0d0c",

        "EDGE_DK": "#a7a299",
        "EDGE": "#dbd7ce",
        "EDGE_LT": "#f7f5f0",
        "RULE": "#2a2a28",
        "RULE_LT": "#12120f",
        "RULE_DK": "#8d8880",

        "PANEL": "#e9e6de",
        "PANEL_LT": "#f4f2ec",
        "PANEL_DK": "#b9b4aa",
        "WELL": "#d8d4ca",

        "TEXT": "#161614",
        "TEXT_DIM": "#6f6a62",
        "TEXT_KEY": "#0d0d0c",
        "LABEL": "#3a3833",

        "LIGHT": "#e2ded5",
        "LIGHT_HI": "#f8f6f2",
        "LIGHT_LO": "#b4afa5",
        "DARK": "#615e56",
        "DARK_HI": "#847f76",
        "DARK_LO": "#3b3933",

        "LIGHT_LAST": "#f6f3ec",
        "DARK_LAST": "#7e7a6e",
        "LIGHT_ENTRY": "#c6c2b8",
        "DARK_ENTRY": "#48453e",

        "WHITE": "#f6f4ef",
        "WHITE_HI": "#ffffff",
        "WHITE_LO": "#b6b1a7",
        "WHITE_RIM": "#6f6a62",
        "BLACK": "#0d0d0c",
        "BLACK_HI": "#38352f",
        "BLACK_LO": "#000000",

        "PIECE_HALO": "#f4f1ea",
    },
    "lines": dict(WEIGHT_PRESETS["normal"]),
    "gradient": {
        "on": False,
        "from": "#e9e6de",
        "to": "#c9c4b8",
        "direction": "vertical",
        "bands": 24,
    },
}


# ---------------------------------------------------------------------------
# Colours as numbers
# ---------------------------------------------------------------------------

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def rgb(colour):
    return tuple(int(colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def hexOf(r, g, b):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v * 255.0))))
                                   for v in (r, g, b))


def luminance(colour):
    """Relative luminance, WCAG's definition."""
    out = []
    for channel in rgb(colour):
        out.append(channel / 12.92 if channel <= 0.03928
                   else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast(a, b):
    """The ratio between two colours, 1.0 (identical) to 21.0 (black on white).

    Here because the appearance screen reports it, and it reports it because this window has
    already shipped two unreadable things -- a side selector lettered in white on an off-white
    panel at 1.06 to 1, and a disabled marker drawn a shade *lighter* than the panel behind
    it. Both were invisible rather than merely subtle, and neither was noticed by anyone
    reading the code. A number on the screen is what turns that into something a player can
    see before they save it.
    """
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def derive(role, colour):
    """The shades that follow a role's base colour, when a player moves it.

    Each dependent keeps the *lightness distance* it has from the base in DEFAULT, and takes
    its hue and saturation from the new colour. That is the right relationship for this
    palette because the palette is achromatic: every one of these pairs differs in value and
    in nothing else, so preserving the value gap and inheriting the tint is exactly what
    "the same shade, in this colour instead" means.

    Returns DEFAULT's own values untouched when the base has not moved, so resetting and
    re-deriving is lossless rather than nearly lossless.
    """
    for name, _label, base, deps in ROLES:
        if name != role: continue

        if colour.lower() == DEFAULT["colours"][base].lower():
            return {key: DEFAULT["colours"][key] for key in deps}

        baseL = colorsys.rgb_to_hls(*rgb(DEFAULT["colours"][base]))[1]
        newH, newL, newS = colorsys.rgb_to_hls(*rgb(colour))

        out = {}
        for key in deps:
            depL = colorsys.rgb_to_hls(*rgb(DEFAULT["colours"][key]))[1]
            wanted = max(0.0, min(1.0, newL + (depL - baseL)))
            out[key] = hexOf(*colorsys.hls_to_rgb(newH, wanted, newS))
        return out

    raise ThemeError("no such role: %r" % (role,))


# ---------------------------------------------------------------------------
# Reading one in
# ---------------------------------------------------------------------------

def validate(raw):
    """Return a complete, checked theme, or raise ThemeError saying what is wrong.

    Checked rather than trusted, and completed rather than merely checked: a file written
    against an older version is missing whatever has been added since, and the useful
    behaviour is to fill those in from DEFAULT rather than to refuse the file. An *unknown*
    key is the opposite case and is refused, because it is either a typo -- a setting the
    player believes they made and which does nothing -- or a file from a newer version that
    this one would silently paint wrong.
    """
    if not isinstance(raw, dict):
        raise ThemeError("a theme is a JSON object, and this is a %s"
                         % type(raw).__name__)

    theme = {
        "name": str(raw.get("name") or "Untitled")[:40],
        "colours": dict(DEFAULT["colours"]),
        "lines": dict(DEFAULT["lines"]),
        "gradient": dict(DEFAULT["gradient"]),
    }

    colours = raw.get("colours", {})
    if not isinstance(colours, dict):
        raise ThemeError("`colours` is a JSON object of name to #rrggbb")
    for key, value in colours.items():
        if key not in DEFAULT["colours"]:
            raise ThemeError("%r is not a colour this version paints with" % (key,))
        if not isinstance(value, str) or not HEX.match(value):
            raise ThemeError("%s is %r, and a colour is #rrggbb" % (key, value))
        theme["colours"][key] = value.lower()

    lines = raw.get("lines", {})
    if not isinstance(lines, dict):
        raise ThemeError("`lines` is a JSON object of %s" % (", ".join(WEIGHTS),))
    for key, value in lines.items():
        if key not in DEFAULT["lines"]:
            raise ThemeError("%r is not a line weight" % (key,))
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 8:
            raise ThemeError("%s is %r, and a line weight is a whole number of pixels "
                             "from 1 to 8" % (key, value))
        theme["lines"][key] = value

    gradient = raw.get("gradient", {})
    if not isinstance(gradient, dict):
        raise ThemeError("`gradient` is a JSON object")
    for key, value in gradient.items():
        if key not in DEFAULT["gradient"]:
            raise ThemeError("%r is not part of a gradient" % (key,))
        if key == "on":
            if not isinstance(value, bool):
                raise ThemeError("gradient.on is true or false")
        elif key == "direction":
            if value not in DIRECTIONS:
                raise ThemeError("gradient.direction is one of %s"
                                 % (", ".join(DIRECTIONS),))
        elif key == "bands":
            if (not isinstance(value, int) or isinstance(value, bool)
                    or not BANDS_MIN <= value <= BANDS_MAX):
                raise ThemeError("gradient.bands is a whole number from %d to %d"
                                 % (BANDS_MIN, BANDS_MAX))
        elif not isinstance(value, str) or not HEX.match(value):
            raise ThemeError("gradient.%s is %r, and a colour is #rrggbb" % (key, value))
        theme["gradient"][key] = value.lower() if key in ("from", "to") else value

    return theme


def load(path):
    """Read a theme file. Raises ThemeError for anything that is not one."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as error:
        raise ThemeError("that file is not JSON (%s)" % (error,))
    except UnicodeDecodeError:
        raise ThemeError("that file is not text")

    theme = validate(raw)
    if theme["name"] == "Untitled":
        theme["name"] = pathlib.Path(path).stem
    return theme


def save(path, theme):
    """Write a theme, having first checked it is one.

    Validated on the way *out* as well as on the way in, and that is the same argument
    notation.encode_game makes about a move list: a file that cannot be read back is worth
    catching while the thing that produced it is still on screen, rather than the next time
    somebody opens it.
    """
    theme = validate(theme)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(theme, handle, indent=1, sort_keys=True)
        handle.write("\n")
    return theme


# ---------------------------------------------------------------------------
# Where they live
# ---------------------------------------------------------------------------
# Two directories and one file. The bundled presets ship with the program and are read-only
# as far as this is concerned; the player's own live under their home directory, which is
# also where the one line of configuration goes.

BUNDLED = pathlib.Path(__file__).resolve().parent / "themes"


def userDir():
    return pathlib.Path.home() / ".royals"


def themeDir():
    return userDir() / "themes"


def configPath():
    return userDir() / "config.json"


def presets():
    """Every theme file on disk, bundled first, as [(name, path)] sorted within each source.

    A file that will not parse is left out rather than raised over: this runs to fill a list
    on a screen, and one bad file in a directory should cost that file and nothing else.
    """
    found = []
    for directory in (BUNDLED, themeDir()):
        try:
            paths = sorted(directory.glob("*.json"))
        except OSError:
            continue
        for path in paths:
            try:
                found.append((load(path)["name"], path))
            except (ThemeError, OSError):
                continue
    return found


def readConfig():
    """The theme to open with, or None if there is nothing usable to open with.

    **The theme is stored in here whole, not as a path to a file.** A path would be a
    reference to something a player can rename, move, edit or delete between one session and
    the next, and the failure it produces is the worst kind: the window opens in the wrong
    colours and the reason is a file the player has forgotten about. What is stored is what
    was applied.

    Never raises. This runs before the window exists, and a configuration file that cannot be
    read is a reason to open in the default look rather than a reason not to open. The window
    says so once, in its log, where somebody can see it and nobody is stopped by it.
    """
    with open(configPath(), encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict) or not isinstance(raw.get("theme"), dict):
        raise ThemeError("the configuration file carries no theme")
    return validate(raw["theme"])


def writeConfig(theme):
    """Remember what to open with next time. Returns True if it was written.

    Failure here is silent on purpose: not being able to write a preference is a thing to
    shrug at, and a player who has just pressed APPLY can see that it worked.
    """
    try:
        userDir().mkdir(parents=True, exist_ok=True)
        with open(configPath(), "w", encoding="utf-8") as handle:
            json.dump({"theme": validate(theme)}, handle, indent=1, sort_keys=True)
            handle.write("\n")
        return True
    except (OSError, ThemeError):
        return False
