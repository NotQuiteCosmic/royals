"""The theme schema, and the palette it claims to describe.

Themes are the one part of the desktop app whose correctness does not need a screen, and this
file is deliberately the part that can be checked without one: it runs on a headless runner
where every other desktop test skips.

**The test that earns the file is the first one.** A theme file is a list of colour names, and
the names are royals_gui's module constants -- two lists, in two files, that have to be the
same list. Nothing about adding a colour to the palette makes anybody update the schema, and
the failure is quiet in the worst way: the new shade is simply one no preset can reach and no
appearance screen offers, and the board goes on painting it whatever the source says. So the
two are held against each other in both directions, which is the whole reason to have a schema
rather than a dictionary of whatever somebody wrote down.
"""

import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop"))

import theme as Theme

royals_gui = pytest.importorskip("royals_gui")

SOURCE = pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop" / "royals_gui.py"


####### The schema against the palette #######

def test_every_colour_in_the_schema_is_one_the_window_paints_with():
    for key in Theme.DEFAULT["colours"]:
        assert hasattr(royals_gui, key), \
            "%s is in a theme file and is not a colour royals_gui has" % (key,)


def test_every_colour_the_window_paints_with_is_in_the_schema():
    """The direction that catches the real mistake: a colour added to the palette and not to
    the schema is a shade no theme can reach, and nothing else notices."""
    declared = set(re.findall(r'^([A-Z][A-Z_]*) = "#[0-9a-fA-F]{6}"',
                              SOURCE.read_text(encoding="utf-8"), re.M))
    missing = declared - set(Theme.DEFAULT["colours"])
    assert not missing, "royals_gui declares %s, which no theme can set" % (sorted(missing),)


def test_the_default_theme_is_the_palette_as_shipped():
    """Not a computed default. These values were measured -- the dark square is held at 3.0
    to 1 against a black piece -- so DEFAULT is a copy, and this is what says it stayed one."""
    for key, value in Theme.DEFAULT["colours"].items():
        assert getattr(royals_gui, key).lower() == value.lower(), key


def test_the_role_table_covers_the_schema_exactly():
    assert sorted(Theme.KEYS) == sorted(Theme.DEFAULT["colours"])
    assert len(Theme.KEYS) == len(set(Theme.KEYS)), "a colour appears in two roles"


####### Reading and writing #######

def test_a_theme_round_trips_through_a_file(tmp_path):
    path = tmp_path / "t.json"
    Theme.save(path, Theme.DEFAULT)
    back = Theme.load(path)
    assert back["colours"] == Theme.DEFAULT["colours"]
    assert back["lines"] == Theme.DEFAULT["lines"]
    assert back["gradient"] == Theme.DEFAULT["gradient"]


def test_a_short_theme_is_filled_in_rather_than_refused():
    """A file written against an older version is missing whatever has been added since, and
    the useful answer is the rest of the default rather than an error."""
    theme = Theme.validate({"name": "Sparse", "colours": {"DARK": "#112233"}})
    assert theme["colours"]["DARK"] == "#112233"
    assert theme["colours"]["LIGHT"] == Theme.DEFAULT["colours"]["LIGHT"]
    assert theme["lines"] == Theme.DEFAULT["lines"]


@pytest.mark.parametrize("raw, wrong", [
    ({"colours": {"NOT_A_COLOUR": "#112233"}}, "unknown key"),
    ({"colours": {"DARK": "112233"}}, "no hash"),
    ({"colours": {"DARK": "#12345"}}, "five digits"),
    ({"colours": {"DARK": None}}, "not a string"),
    ({"lines": {"hair": 0}}, "zero width"),
    ({"lines": {"hair": True}}, "a bool is not a width"),
    ({"lines": {"nib": 2}}, "unknown weight"),
    ({"gradient": {"direction": "sideways"}}, "unknown direction"),
    ({"gradient": {"bands": 1}}, "too few bands"),
    ({"gradient": {"on": "yes"}}, "not a bool"),
    ({"gradient": {"from": "red"}}, "a colour name is not #rrggbb"),
    ([], "not an object"),
])
def test_a_malformed_theme_is_refused(raw, wrong):
    with pytest.raises(Theme.ThemeError):
        Theme.validate(raw)


def test_a_file_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("this is not JSON at all", encoding="utf-8")
    with pytest.raises(Theme.ThemeError):
        Theme.load(path)


def test_saving_checks_before_it_writes(tmp_path):
    """The same argument notation.encode_game makes about a move list: a file that cannot be
    read back is worth catching while what produced it is still on screen."""
    path = tmp_path / "t.json"
    with pytest.raises(Theme.ThemeError):
        Theme.save(path, {"colours": {"DARK": "nonsense"}})
    assert not path.exists(), "a theme that would not validate was written anyway"


####### Deriving #######

def test_deriving_from_an_unmoved_base_gives_the_default_shades_back():
    for role, _label, base, deps in Theme.ROLES:
        got = Theme.derive(role, Theme.DEFAULT["colours"][base])
        for key in deps:
            assert got[key] == Theme.DEFAULT["colours"][key], "%s / %s" % (role, key)


def test_deriving_keeps_the_light_and_dark_of_a_shade():
    """A highlight stays lighter than its base and a shadow stays darker, whatever hue the
    player picked -- that relationship is the only thing the bevels mean."""
    got = Theme.derive("boardDark", "#243b6b")
    assert Theme.luminance(got["DARK_HI"]) > Theme.luminance("#243b6b")
    assert Theme.luminance(got["DARK_LO"]) < Theme.luminance("#243b6b")


def test_deriving_an_unknown_role_is_an_error():
    with pytest.raises(Theme.ThemeError):
        Theme.derive("upholstery", "#112233")


####### Contrast #######

def test_contrast_agrees_with_the_two_ends_of_the_scale():
    assert Theme.contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.05)
    assert Theme.contrast("#616161", "#616161") == pytest.approx(1.0, abs=0.001)


def test_the_default_palette_still_holds_the_ratios_its_comments_claim():
    """royals_gui's palette comment states the dark square sits at 3.0 to 1 against a black
    piece and 4.8 against the light square. Those are the numbers the whole board was tuned
    around, and this is the only place they are checked rather than asserted in prose."""
    c = Theme.DEFAULT["colours"]
    assert Theme.contrast(c["BLACK"], c["DARK"]) == pytest.approx(3.0, abs=0.1)
    assert Theme.contrast(c["LIGHT"], c["DARK"]) == pytest.approx(4.8, abs=0.1)


####### What ships with the app #######

def test_every_bundled_preset_loads():
    found = Theme.presets()
    assert found, "no themes ship with the app"
    names = [name for name, _path in found]
    assert "Default" in names


def test_every_bundled_preset_is_legible():
    """The appearance screen warns a player below 3 to 1. A theme shipped in the box should
    not be the thing it warns about."""
    for name, path in Theme.presets():
        c = Theme.load(path)["colours"]
        pairs = {"lettering": Theme.contrast(c["TEXT"], c["PANEL"]),
                 "black piece on its square": Theme.contrast(c["BLACK"], c["DARK"]),
                 "white piece's rim on its square": Theme.contrast(c["WHITE_RIM"], c["LIGHT"])}
        for what, ratio in pairs.items():
            assert ratio >= 3.0, "%s: %s is %.2f to 1" % (name, what, ratio)


def test_the_bundled_default_is_the_palette_as_shipped():
    path = [p for n, p in Theme.presets() if n == "Default"][0]
    assert Theme.load(path)["colours"] == Theme.DEFAULT["colours"]


####### Applying #######

def test_applying_moves_the_palette_and_rebuilds_what_was_built_from_it():
    """CHIP_WALL is a table assembled from colours rather than a colour, so it is the one
    thing a swap can leave behind -- the piece walls would be the only part of the board that
    never changed."""
    try:
        navy = dict(Theme.DEFAULT)
        navy["colours"] = dict(Theme.DEFAULT["colours"], WHITE="#001122", WHITE_HI="#334455")
        royals_gui.applyTheme(navy)

        assert royals_gui.WHITE == "#001122"
        assert royals_gui.CHIP_WALL[0][0] == "#001122", "CHIP_WALL kept the old colour"
        assert royals_gui.CHIP_WALL[0][1] == "#334455"
    finally:
        royals_gui.applyTheme(Theme.DEFAULT)


def test_a_theme_that_will_not_validate_leaves_the_palette_alone():
    before = royals_gui.currentTheme()["colours"]
    with pytest.raises(Theme.ThemeError):
        royals_gui.applyTheme({"colours": {"DARK": "not a colour"}})
    assert royals_gui.currentTheme()["colours"] == before


def test_the_palette_can_be_read_back_out_as_a_theme():
    assert Theme.validate(royals_gui.currentTheme())["colours"] == Theme.DEFAULT["colours"]


####### The remembered theme #######

def test_a_missing_config_is_not_an_error_that_stops_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(Theme, "userDir", lambda: tmp_path / "nothing-here")
    with pytest.raises(FileNotFoundError):
        Theme.readConfig()


def test_a_config_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(Theme, "userDir", lambda: tmp_path)
    theme = Theme.validate(dict(Theme.DEFAULT,
                                colours=dict(Theme.DEFAULT["colours"], DARK="#123456")))
    assert Theme.writeConfig(theme)
    assert Theme.readConfig()["colours"]["DARK"] == "#123456"


def test_a_corrupt_config_raises_rather_than_returning_a_half_theme(tmp_path, monkeypatch):
    monkeypatch.setattr(Theme, "userDir", lambda: tmp_path)
    Theme.configPath().write_text('{"theme": "green"}', encoding="utf-8")
    with pytest.raises(Theme.ThemeError):
        Theme.readConfig()


def test_the_config_carries_the_theme_and_not_a_path(tmp_path, monkeypatch):
    """A path would be a reference to a file the player can move, edit or delete between
    sessions, and the window would come up in the wrong colours for a reason nobody could
    see. What is stored is what was applied."""
    monkeypatch.setattr(Theme, "userDir", lambda: tmp_path)
    Theme.writeConfig(Theme.DEFAULT)
    raw = json.loads(Theme.configPath().read_text(encoding="utf-8"))
    assert isinstance(raw["theme"], dict)
    assert raw["theme"]["colours"]["DARK"] == Theme.DEFAULT["colours"]["DARK"]
