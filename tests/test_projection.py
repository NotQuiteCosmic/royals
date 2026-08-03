"""The camera: what a board position becomes on screen, and back again.

The desktop board has been drawn through one projection since it was written and has never
had a test. That was survivable while there was only one — the board either looked right or
it did not, and a person could see which. It stops being survivable with two, because now
"the board still looks right" has to be true in a mode nobody is looking at.

**The first test in this file is the one that matters.** `View` in orthographic mode has to go
on computing exactly what it computes today, to the last place, because the whole argument for
adding perspective is that it is *additive*. So the orthographic formulas are written out here
a second time, from the design comment at the top of the view section rather than from the
code, and the two are held against each other. A second implementation is only worth having
where it can disagree; this is one of those places.

The rest is the new mode. Perspective is easy to get subtly wrong in ways that look plausible
in a screenshot -- a projection that converges but does not invert, an inverse that is exact
only near the middle, a painter's order that is right until a stack leans past its neighbour --
so each of those is asserted rather than eyeballed.

No display is needed. `View` holds no Tk handles: it is eleven floats and some trigonometry,
so this file runs on a headless runner where the three `test_desktop_*` files skip.
"""

import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop"))

royals_gui = pytest.importorskip("royals_gui")

View = royals_gui.View
YAW_DEF, PITCH_DEF = royals_gui.YAW_DEF, royals_gui.PITCH_DEF
PITCH_MIN, PITCH_MAX = royals_gui.PITCH_MIN, royals_gui.PITCH_MAX


# The board's own coordinates: the playing surface is z = 0 and runs -3.5 .. 3.5 in both
# directions, so these are the corners, the middle, and a scatter in between.
SPOTS = [(x, y) for x in (-3.5, -2.0, 0.0, 1.5, 3.5)
                for y in (-3.5, -2.0, 0.0, 1.5, 3.5)]

# Yaws worth sweeping: the default, the four square-on ones, and two that are neither.
YAWS = [YAW_DEF, 0.0, math.pi / 2, math.pi, 0.3, 2.7]
PITCHES = [PITCH_DEF, PITCH_MIN, PITCH_MAX, math.radians(30.0)]


def orthographic(v, x, y, z=0.0):
    """The projection as the design comment states it, written from the comment.

        cx = x cos y - y sin y            sx = ox + S cx
        cy = x sin y + y cos y            sy = oy + S (cy sin p - z cos p)
    """
    cx = x * math.cos(v.yaw) - y * math.sin(v.yaw)
    cy = x * math.sin(v.yaw) + y * math.cos(v.yaw)
    return (v.ox + v.scale * cx,
            v.oy + v.scale * (cy * math.sin(v.pitch) - z * math.cos(v.pitch)))


def meet(p1, p2, p3, p4):
    """Where the line p1p2 crosses the line p3p4, or None if they are parallel."""
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = p1, p2, p3, p4
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < 1e-6: return None
    a, b = x1 * y2 - y1 * x2, x3 * y4 - y3 * x4
    return ((a * (x3 - x4) - (x1 - x2) * b) / d,
            (a * (y3 - y4) - (y1 - y2) * b) / d)


####### The mode that already existed #######

def test_orthographic_is_exactly_what_it_has_always_been():
    """The regression guard, and the reason this file was written before anything moved.

    Perspective is worth adding only if it is additive, and this is what says so: the
    orthographic path must agree with the formula in the design comment to the last place,
    at every angle, whatever else the class grows.
    """
    for yaw in YAWS:
        for pitch in PITCHES:
            v = View(yaw, pitch, scale=60.0, ox=400.0, oy=300.0)
            for x, y in SPOTS:
                for z in (0.0, 0.16, 0.96):
                    got = v.project(x, y, z)
                    want = orthographic(v, x, y, z)
                    assert got == pytest.approx(want, abs=1e-12), \
                        "(%.2f, %.2f, %.2f) at yaw %.2f pitch %.2f" % (x, y, z, yaw, pitch)


def test_orthographic_keeps_every_square_the_same_size():
    """What "no perspective divide" means, said as a property: a cell is the same number of
    pixels wherever it is on the board."""
    v = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    widths = set()
    for x, y in SPOTS:
        a, b = v.project(x - 0.5, y), v.project(x + 0.5, y)
        widths.add(round(math.hypot(b[0] - a[0], b[1] - a[1]), 6))
    assert len(widths) == 1, "a cell changed size across the board: %s" % (sorted(widths),)


def test_orthographic_keeps_the_vertical_axis_vertical():
    v = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    for x, y in SPOTS:
        xs = {round(v.project(x, y, z)[0], 9) for z in (0.0, 0.5, 1.0)}
        assert len(xs) == 1, "a stack leaned in orthographic at (%.1f, %.1f)" % (x, y)


####### Getting back out again #######

@pytest.mark.parametrize("persp", [False, True])
def test_a_screen_point_comes_back_to_the_square_it_came_from(persp):
    """`ground` is the inverse of `project` on the playing surface, and hit-testing falls
    through to it whenever a click lands on bare floor rather than on a drawn item."""
    for yaw in YAWS:
        for pitch in PITCHES:
            v = View(yaw, pitch, scale=60.0, ox=400.0, oy=300.0)
            v.persp = persp
            for x, y in SPOTS:
                back = v.ground(*v.project(x, y, 0.0))
                assert back == pytest.approx((x, y), abs=1e-9), \
                    "%s at yaw %.2f pitch %.2f persp=%s" % ((x, y), yaw, pitch, persp)


@pytest.mark.parametrize("persp", [False, True])
def test_a_click_in_a_square_names_that_square(persp):
    v = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    v.persp = persp
    for square in range(1, 50):
        x, y = (square - 1) % 7 - 3.0, (square - 1) // 7 - 3.0
        assert v.square(*v.project(x, y, 0.0)) == square


def test_a_click_off_the_board_names_no_square():
    v = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    v.persp = True
    assert v.square(*v.project(-9.0, 0.0, 0.0)) is None
    assert v.square(*v.project(0.0, 9.0, 0.0)) is None


####### The new mode #######

def test_perspective_makes_near_squares_larger_than_far_ones():
    """The whole visible point of the mode. Orthographic must not do this and perspective
    must, so the two are asserted together."""
    def cellWidth(v, x, y):
        a, b = v.project(x - 0.5, y), v.project(x + 0.5, y)
        return math.hypot(b[0] - a[0], b[1] - a[1])

    flat = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    deep = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    deep.persp = True

    assert cellWidth(flat, 3.0, 3.0) == pytest.approx(cellWidth(flat, -3.0, -3.0))
    assert cellWidth(deep, 3.0, 3.0) > cellWidth(deep, -3.0, -3.0) * 1.2, \
        "perspective should make the near rank markedly larger"


def test_all_three_families_of_parallels_converge():
    """Three-point, stated as the three vanishing points it is named for. The rows and the
    columns share a horizon; the verticals have a point of their own, and that third point is
    exactly what distinguishes this from the two-point construction."""
    v = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    v.persp = True

    rows = meet(v.project(-3.5, -3.5), v.project(3.5, -3.5),
                v.project(-3.5, 3.5), v.project(3.5, 3.5))
    cols = meet(v.project(-3.5, -3.5), v.project(-3.5, 3.5),
                v.project(3.5, -3.5), v.project(3.5, 3.5))
    verticals = meet(v.project(3.0, -3.0, 0.0), v.project(3.0, -3.0, 2.0),
                     v.project(-1.0, 3.0, 0.0), v.project(-1.0, 3.0, 2.0))

    assert rows is not None and cols is not None and verticals is not None
    assert rows[1] == pytest.approx(cols[1], abs=1.0), "the rows and columns share a horizon"
    assert abs(verticals[1] - rows[1]) > 100.0, "the verticals need a point of their own"


def test_stacks_splay_outward_and_the_middle_one_stands_up():
    """Which way a stack leans, and it is the opposite of the first guess.

    The eye is above the board, so the *top* of a stack is nearer to it than the foot and is
    magnified more -- which pushes the top further from the centre of the picture, not closer.
    Stacks therefore splay outward, exactly as the tops of buildings do in a photograph taken
    looking down, and the vertical vanishing point is the nadir underneath the camera rather
    than a point up in the sky. The column directly under the eye leans neither way, which is
    the cheapest check that the sign is right at all.
    """
    v = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    v.persp = True

    def lean(x, y):
        return v.project(x, y, 1.0)[0] - v.project(x, y, 0.0)[0]

    assert lean(0.0, 0.0) == pytest.approx(0.0, abs=1e-9)
    assert lean(3.0, -3.0) > 1e-6, "a stack right of centre should splay further right"
    assert lean(-3.0, 3.0) < -1e-6, "a stack left of centre should splay further left"


def test_the_far_half_of_the_board_stays_behind_the_near_half():
    """Painter's order is the whole hidden-surface algorithm here: squares are drawn far to
    near and each draws its own stack. That is only sound if a far stack can never reach past
    a nearer square's foot -- which leaning columns could have broken."""
    for yaw in YAWS:
        v = View(yaw, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
        v.persp = True
        squares = [((s - 1) % 7 - 3.0, (s - 1) // 7 - 3.0) for s in range(1, 50)]
        for ax, ay in squares:
            for bx, by in squares:
                if v.depth(ax, ay) >= v.depth(bx, by): continue      # a is not the far one
                assert v.project(ax, ay, 1.0)[1] <= v.project(bx, by, 0.0)[1] + 1e-9, \
                    "a far stack reached past a nearer foot at yaw %.2f" % (yaw,)


@pytest.mark.parametrize("persp", [False, True])
def test_the_whole_cabinet_fits_on_the_canvas(persp):
    """`fit` has one job. It is worth pinning in both modes because the perspective extent is
    not the orthographic one scaled -- the near edge grows faster than the far edge shrinks."""
    for pitch in PITCHES:
        v = View(YAW_DEF, pitch, scale=60.0)
        v.persp = persp
        v.fit(900.0, 740.0)

        half = royals_gui.E_HALF
        for x in (-half, half):
            for y in (-half, half):
                for z in (-royals_gui.SLAB_T, royals_gui.H_MAX):
                    sx, sy = v.project(x, y, z)
                    assert -1.0 <= sx <= 901.0, "x=%.1f at pitch %.2f" % (sx, pitch)
                    assert -1.0 <= sy <= 741.0, "y=%.1f at pitch %.2f" % (sy, pitch)


####### The chip, which stopped being an ellipse #######

def test_a_tessellated_chip_is_within_a_pixel_of_the_circle_it_replaced():
    """The board's pieces used to be `create_oval`, exactly. They are polygons now, because
    perspective gives a cylinder a wider lid than its foot and a leaning silhouette, and no
    canvas oval can draw that. One renderer serves both cameras -- so the question this test
    answers is the one that asks itself: did the *old* board change?

    The polygon is inscribed and its corners sit exactly on the true circle, so the whole
    error is the sagitta of one chord. Measured at the largest radius the board ever draws a
    chip at, which is what a full-screen window gives.
    """
    sides = royals_gui.CHIP_SIDES
    scale = 120.0                       # generous: the board fits ~60 on a 900px canvas
    for r in (royals_gui.R_CHIP, royals_gui.R_DRAGON, royals_gui.STUD_R):
        radius = r * scale
        sagitta = radius * (1.0 - math.cos(math.pi / sides))
        assert sagitta < 1.0, \
            "a %d-sided ring at radius %.1fpx is %.2fpx inside the curve" % (
                sides, radius, sagitta)


def test_the_lid_icons_land_exactly_where_the_pixel_version_put_them():
    """The icons painted on a chip's lid were laid out in screen offsets and are laid out in
    the world now. In orthographic the two have to agree to the last place, distortions and
    all -- both the swell as the board flattens and the squash floor that stops an icon
    becoming a line at a low pitch.

    The old arithmetic is written out here rather than referred to, because it no longer
    exists to call: `centre + du·S, r·S·grow` against `r·S·squash·grow`.
    """
    for pitch in (PITCH_DEF, PITCH_MIN, PITCH_MAX, math.radians(20.0)):
        for yaw in (YAW_DEF, 0.0, 2.2):
            v = View(yaw, pitch, scale=60.0, ox=400.0, oy=300.0)
            grow, stretch = v.iconGrow(), v.squash / v.sinP

            for ax, ay, az, du, dv, r in ((2.0, -1.0, 0.5, 0.0, -0.15, 0.12),
                                          (-3.0, 3.0, 0.0, 0.2, 0.33, 0.052)):
                px, py = v.project(ax, ay, az)
                sx, sy = v.scale, v.scale * v.squash

                for u, t in royals_gui.SHAPE_ROYAL + royals_gui.SHAPE_SPY:
                    cu, ct = du + u * r * grow, (dv + t * r * grow) * stretch
                    got = v.project(ax + cu * v.cosY + ct * v.sinY,
                                    ay - cu * v.sinY + ct * v.cosY, az)
                    want = (px + du * sx + u * r * sx * grow,
                            py + dv * sy + t * r * sy * grow)
                    assert got == pytest.approx(want, abs=1e-9), \
                        "an icon corner moved at yaw %.2f pitch %.2f" % (yaw, pitch)


def test_a_chip_drawn_flat_still_stands_straight_up():
    """The tessellation must not have introduced a lean of its own: in orthographic the
    foot and the lid of a chip are the same ring, one directly above the other."""
    v = View(YAW_DEF, PITCH_DEF, scale=60.0, ox=400.0, oy=300.0)
    r, h = royals_gui.R_CHIP, royals_gui.H_CHIP
    for t in (0.0, 1.0, 2.5, 4.0):
        x, y = 2.0 + r * math.cos(t), -1.0 + r * math.sin(t)
        foot, lid = v.project(x, y, 0.0), v.project(x, y, h)
        assert foot[0] == pytest.approx(lid[0], abs=1e-9)
        assert lid[1] < foot[1], "the lid should sit above the foot"


def test_the_projection_mode_is_off_until_it_is_asked_for():
    v = View()
    assert v.persp is False
    assert v.factorAt(3.0, 3.0) == 1.0, "orthographic has no perspective factor"
    v.persp = True
    assert v.factorAt(3.0, 3.0) > 1.0 > v.factorAt(-3.0, -3.0), \
        "near should be magnified and far reduced"


####### The board itself #######
# These take the shared window from tests/conftest.py and so skip on a headless runner, while
# everything above this line goes on running there. Same file, because they are the same
# feature and the geometry is only worth having if the board it draws still works.

def dealtGame(window):
    """A two-player game with a dealt opening: a full board, and no worker thread."""
    window.modeVar.set(0)
    window.entryVar.set(1)
    window.startGame()
    window.root.update()


def test_the_board_draws_in_both_projections(window):
    dealtGame(window)
    assert window.canvas.find_all()

    window.perspVar.set(1)
    window.onProjection()
    window.root.update()
    assert window.view.persp is True
    assert window.canvas.find_all()
    assert window.errors == []


def test_every_square_can_still_be_clicked_in_both_projections(window):
    """The one thing a projection change can break without looking broken. `squareAt` reads
    canvas tags first and only falls back to the inverse on bare floor, so this exercises
    both paths at once -- a wrong inverse would show up as a square naming its neighbour."""
    dealtGame(window)

    for persp in (False, True):
        window.perspVar.set(1 if persp else 0)
        window.onProjection()
        window.root.update()

        for square in range(1, 50):
            x, y = (square - 1) % 7 - 3.0, (square - 1) // 7 - 3.0
            assert window.squareAt(*window.view.project(x, y, 0.0)) == square, \
                "square %d in %s" % (square, "perspective" if persp else "orthographic")


def test_perspective_actually_moves_the_board(window):
    """Guards against the toggle being wired to nothing -- a check that passes whether or not
    the mode does anything is the one failure a screenshot would not show either."""
    dealtGame(window)

    # Set explicitly rather than assumed: the camera outlives a game on purpose, so "which
    # projection is in force" is not something a test may inherit.
    window.perspVar.set(0)
    window.onProjection()
    window.root.update()
    flat = window.canvas.coords(window.canvas.find_all()[10])

    window.perspVar.set(1)
    window.onProjection()
    window.root.update()
    deep = window.canvas.coords(window.canvas.find_all()[10])

    assert flat != deep, "the board drew identically in both projections"


def test_the_camera_keeps_its_projection_across_a_new_game(window):
    """Yaw and pitch survive a new game; this sits beside them and does too."""
    dealtGame(window)
    window.perspVar.set(1)
    window.onProjection()

    window.buildSetup()
    window.modeVar.set(0)
    window.entryVar.set(1)
    window.startGame()
    window.root.update()

    assert window.view.persp is True
    assert window.perspVar.get() == 1, "the box should come back showing what is in force"
    assert window.errors == []
