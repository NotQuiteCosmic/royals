"""Turning game results into numbers that mean something.

Games are played in colour-swapped PAIRS: the same opening twice, each side taking blue
once. A pair's outcome for side A is its points over the two games, 0 to 2, which we keep in
[0, 1] units as 0, .25, .5, .75 or 1 -- the five "pentanomial" buckets. Working in pairs
rather than games takes the opening's own bias out of the variance: a lopsided opening makes
one win and one loss, a .5, and says nothing about the players, which is right.

Elo here is the logistic kind, 400 * log10(s / (1 - s)) for a score fraction s. Self-play
Elo between two versions of one program runs two or three times hotter than Elo against
the world, so the numbers are for comparing candidates with each other, not for bragging.

The SPRT is the simplified generalised one (Van den Bergh, as used by fishtest): a normal
approximation to the log-likelihood ratio between "the true Elo is elo0" and "it is elo1",
read off the running mean and variance of the pair scores. It stops as soon as the evidence
is strong enough either way, which is what makes it affordable: a fixed-size match has to be
big enough for its hardest case every time.
"""

import math

PAIR_BUCKETS = (0.0, 0.25, 0.5, 0.75, 1.0)


def pair_score(result_a_as_blue, result_a_as_red):
    """A's score over a pair in [0, 1] units. The two arguments are each game's result_blue:
    in the second game A was red, so its points there are 1 - result_blue."""
    return (result_a_as_blue + (1.0 - result_a_as_red)) / 2.0


def pentanomial(pair_scores):
    counts = [0, 0, 0, 0, 0]
    for score in pair_scores:
        counts[PAIR_BUCKETS.index(score)] += 1
    return counts


def pair_stats(pent):
    """(number of pairs, mean pair score, variance of the pair score)."""
    n = sum(pent)
    if n == 0: return 0, 0.5, 0.0
    mean = sum(count * x for count, x in zip(pent, PAIR_BUCKETS)) / n
    var = sum(count * (x - mean) ** 2 for count, x in zip(pent, PAIR_BUCKETS)) / n
    return n, mean, var


def logistic(elo):
    return 1.0 / (1.0 + 10.0 ** (-elo / 400.0))


def elo(score):
    score = min(max(score, 1e-9), 1.0 - 1e-9)
    return 400.0 * math.log10(score / (1.0 - score))


def elo_ci(pent, z=1.96):
    """(elo, low, high) for the pair scores in `pent`. The interval is in score space, from
    the pair variance, and mapped through the logistic curve; with no variance it collapses
    onto the point."""
    n, mean, var = pair_stats(pent)
    if n == 0: return 0.0, -math.inf, math.inf
    se = math.sqrt(var / n)
    return elo(mean), elo(mean - z * se), elo(mean + z * se)


def sprt_bounds(alpha=0.05, beta=0.05):
    return math.log(beta / (1.0 - alpha)), math.log((1.0 - beta) / alpha)


def sprt_llr(pent, elo0, elo1):
    """The log-likelihood ratio of H1 (elo1) against H0 (elo0), or None when the pairs so far
    carry no information (none played, or every pair the same)."""
    n, mean, var = pair_stats(pent)
    if n == 0 or var == 0.0: return None
    s0 = logistic(elo0)
    s1 = logistic(elo1)
    return n * (s1 - s0) * (2.0 * mean - s0 - s1) / (2.0 * var)


def sprt_status(pent, elo0, elo1, alpha=0.05, beta=0.05):
    """'H1', 'H0', 'continue' or 'undecidable' (no variance yet)."""
    llr = sprt_llr(pent, elo0, elo1)
    if llr is None: return "undecidable"
    low, high = sprt_bounds(alpha, beta)
    if llr >= high: return "H1"
    if llr <= low: return "H0"
    return "continue"


def bradley_terry(games, iterations=500):
    """Ratings for a round-robin. `games` is a list of (a, b, score_a) with score_a in
    {0, .5, 1}. Returns {player: (elo, games played, score fraction)}, Elo centred on 0.

    Minorisation-maximisation for the Bradley-Terry model: each player's strength p is set
    to its total score over the sum, across opponents, of games against them divided by the
    pair's combined strength, and this is repeated until it stops moving. Draws count as
    half a win each way, which is the usual approximation."""
    players = sorted({a for a, b, s in games} | {b for a, b, s in games})
    index = {p: i for i, p in enumerate(players)}
    k = len(players)
    wins = [0.0] * k
    played = [0] * k
    against = [[0] * k for _ in range(k)]
    for a, b, score in games:
        i, j = index[a], index[b]
        wins[i] += score
        wins[j] += 1.0 - score
        played[i] += 1
        played[j] += 1
        against[i][j] += 1
        against[j][i] += 1

    strength = [1.0] * k
    for _ in range(iterations):
        new = []
        for i in range(k):
            denom = 0.0
            for j in range(k):
                if against[i][j]: denom += against[i][j] / (strength[i] + strength[j])
            # a player who never scored stays at the floor rather than vanishing
            new.append(max(wins[i], 1e-9) / denom if denom else strength[i])
        total = sum(new) / k
        strength = [s / total for s in new]

    ratings = {}
    for p, i in index.items():
        ratings[p] = (400.0 * math.log10(strength[i]), played[i],
                      wins[i] / played[i] if played[i] else 0.5)
    return ratings
