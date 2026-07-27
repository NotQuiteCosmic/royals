import math
import random

# 2D Perlin noise, written out rather than imported. numpy has no noise generator -- it is
# arrays and maths, nothing else -- and the packages that do carry one aren't installed. A
# field here is 49 samples taken once a game, so there is nothing worth vectorising anyway.
#
# What this is for: tilting the computer's entering scores so it doesn't open the same way
# every game. Perlin rather than plain jitter because the noise has to be spatially
# coherent. Entering is a race to gather six pieces onto one square, so per-square random
# noise would pull the placements apart and the formation would stop cohering. A smooth
# field tilts a whole region instead, and the six still gather -- just somewhere new.


# How many squares a hill or valley spans. Measured over 200 fields, counting how often the
# value changes sign walking along a rank or file (84 steps in all) and comparing how much
# neighbouring squares differ against how much any two squares differ:
#   scale 2 -- 37 sign changes, neighbours 0.91 as different as strangers. White noise.
#   scale 3 -- 27 sign changes, 0.70.
#   scale 5 -- 17 sign changes, 0.48. About one hill and one valley across the board.
#   scale 8 -- 11 sign changes, 0.38. Nearly a flat tilt, so the field only picks a
#              direction and stops saying anything about where in that direction to go.
# Five keeps some shape while staying smooth enough that neighbouring squares still agree,
# which is the property the whole idea rests on.
DEFAULT_SCALE = 5.0


# Perlin's own smoothstep, 6t^5 - 15t^4 + 10t^3. Flat gradient at both ends, so values
# don't visibly kink as they cross a lattice line.
def fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def lerp(a, b, t):
    return a + t * (b - a)


# The four diagonal gradients, which is the usual 2D reduction of Perlin's twelve.
def grad(h, x, y):
    h &= 3
    if h == 0: return x + y
    if h == 1: return -x + y
    if h == 2: return x - y
    return -x - y


# The doubled permutation table. Doubled so the +1 lookups below can't run off the end.
def permutation(seed):
    table = list(range(256))
    random.Random(seed).shuffle(table)
    return table + table


# One noise sample at a point. Interpolates the four corner gradients of the lattice cell
# the point falls in.
def sample(perm, x, y):
    xi = math.floor(x)
    yi = math.floor(y)
    xf = x - xi
    yf = y - yi
    xi &= 255
    yi &= 255

    u = fade(xf)
    v = fade(yf)

    aa = perm[perm[xi] + yi]
    ab = perm[perm[xi] + yi + 1]
    ba = perm[perm[xi + 1] + yi]
    bb = perm[perm[xi + 1] + yi + 1]

    x1 = lerp(grad(aa, xf, yf), grad(ba, xf - 1, yf), u)
    x2 = lerp(grad(ab, xf, yf - 1), grad(bb, xf - 1, yf - 1), u)
    return lerp(x1, x2, v)


# A noise value per square, keyed by 1-based square number the way JUMPREACH is, normalised
# so the strongest square reads 1 or -1.
def field(seed, scale = DEFAULT_SCALE):
    perm = permutation(seed)
    rng = random.Random(seed)

    # Perlin is exactly zero at every integer lattice point, and stepping 1/scale across a
    # seven-wide board lands several squares on one -- at scale 3, every square in columns
    # a, d and g. Offsetting the whole board by a fraction of a cell moves it off the
    # lattice. The offset is drawn from the same seed, so a seed still names one field.
    offX = rng.uniform(0.0, 256.0)
    offY = rng.uniform(0.0, 256.0)

    raw = {}
    for square in range(1, 50):
        x = (square - 1) % 7
        y = (square - 1) // 7
        raw[square] = sample(perm, offX + x / scale, offY + y / scale)

    # Normalised against what this field actually reached rather than Perlin's theoretical
    # bound. Forty-nine samples of a smooth field rarely come near that bound, and a field
    # that happened to come out flat would quietly make the intensity setting mean
    # something different from one game to the next.
    peak = max(abs(v) for v in raw.values())
    if peak == 0: return flatField()

    return {square: raw[square] / peak for square in range(1, 50)}


# What the field looks like with the noise turned off.
def flatField():
    return {square: 0.0 for square in range(1, 50)}
