# The random opening: the mode where nobody picks their squares and the twelve pieces land
# wherever the rules allow. There is no golden for it and there should not be one -- a
# recording of what one RNG did is exactly the kind of file docs/PORTING.md warns a second
# implementation not to try to reproduce, and golden_enter.txt already carries that burden
# for the entering heuristic.
#
# What is worth testing is the two properties a dealt position has to have: every square it
# produces is one a player could have clicked, and the same seed deals the same opening. The
# first is what makes it a legal game of Royals rather than pieces on a board; the second is
# what lets the desktop and the server agree, and what lets an opening worth seeing again be
# played again.

import random

from royals_engine import engine as Engine
from royals_engine import hasher as Hasher


def dealOpening(seed):
    """A whole random opening, exactly as the front ends run it. Returns (board, squares)."""
    board = Hasher.Entering_Board()
    squares = []

    for index, step in enumerate(Engine.enteringSequence()):
        contr, piece = step
        isSpy = (piece == Hasher.SPY)
        square = Engine.randomEntry(board, contr, isSpy, Engine.entryRng(seed, index))
        # a side with nowhere to go sits the step out, the same skip every driver performs
        if square is None: continue
        board = Engine.dropPiece(board, square, contr, piece)
        squares.append(square)

    return board, squares


####### The draw #######

def test_a_drawn_square_is_always_one_a_player_could_have_clicked():
    # The whole legality claim, and it is checked against enteringOptions rather than
    # against a list of squares written down here -- so a change to the entering rules
    # cannot leave this passing on the old ones.
    for seed in range(0, 40):
        board = Hasher.Entering_Board()
        for index, step in enumerate(Engine.enteringSequence()):
            contr, piece = step
            isSpy = (piece == Hasher.SPY)
            options = Engine.enteringOptions(board, contr, isSpy)
            square = Engine.randomEntry(board, contr, isSpy, Engine.entryRng(seed, index))
            if not options:
                assert square is None
                continue
            assert square in options
            board = Engine.dropPiece(board, square, contr, piece)


def test_nowhere_to_go_answers_none():
    # Blue's dragon starts on d3, and a royal may not enter touching something its own side
    # controls -- so filling the rest of the board with blue leaves blue nowhere at all.
    board = Hasher.Entering_Board()
    for square in range(1, 50):
        if not Hasher.Check_For_Occupancy(Hasher.Get_Space_Data(board, square)):
            board = Hasher.Mod_Space(board, square,
                                     Hasher.Build_Space(0, 0, 0, 1, 0))

    assert Engine.enteringOptions(board, 0, False) == []
    assert Engine.randomEntry(board, 0, False, Engine.entryRng(1, 0)) is None


def test_the_draw_is_uniform_over_the_legal_squares():
    # Not a distribution test -- it asks only that the die is rolled over the whole list and
    # not over a prefix of it, which is what a plausible-looking off-by-one would leave.
    board = Hasher.Entering_Board()
    options = Engine.enteringOptions(board, 0, False)
    seen = {Engine.randomEntry(board, 0, False, Engine.entryRng(seed, 0))
            for seed in range(0, 400)}

    assert seen == set(options)


####### The seed #######

def test_the_rng_is_a_pure_function_of_seed_and_step():
    a = Engine.entryRng(7, 3)
    b = Engine.entryRng(7, 3)
    assert [a.randrange(1000) for _ in range(5)] == [b.randrange(1000) for _ in range(5)]

    # Different steps must not share a stream, or every side's first pawn would land in
    # step with its royal.
    firsts = {Engine.entryRng(7, step).randrange(1 << 20) for step in range(0, 12)}
    assert len(firsts) == 12


def test_two_seeds_cannot_collide_across_steps():
    # The seed is (seed << 6) | step, so the only way two games share a stream is by sharing
    # a seed. Worth pinning: an arithmetic derivation instead of a bijective one would make
    # game 2 step 0 and game 1 step 64 the same opening, quietly.
    streams = {}
    for seed in range(0, 64):
        for step in range(0, 12):
            key = Engine.entryRng(seed, step).randrange(1 << 30)
            assert key not in streams, (seed, step, streams.get(key))
            streams[key] = (seed, step)


def test_the_same_seed_deals_the_same_opening():
    assert dealOpening(4242)[1] == dealOpening(4242)[1]


def test_different_seeds_deal_different_openings():
    openings = {tuple(dealOpening(seed)[1]) for seed in range(0, 20)}
    assert len(openings) == 20


####### The position it leaves #######

def test_a_dealt_opening_is_a_full_position():
    # Twelve placements, and the same census a hand-picked opening leaves: one royal, four
    # pawns and one spy a side, plus the two dragons that were there to begin with.
    for seed in (1, 7, 99, 4242):
        board, squares = dealOpening(seed)
        assert len(squares) == 12
        assert len(set(squares)) == 12, "two pieces on one square"

        # [dragon, spy, pawns, royal] a side. DRAGON is a flag, not the weight 3 the
        # entering board's 2D form carries -- see hasher.Build_Space.
        census = [[0, 0, 0, 0], [0, 0, 0, 0]]
        for square in range(1, 50):
            code = Hasher.Get_Space_Data(board, square)
            if not Hasher.Check_For_Occupancy(code): continue
            s = Hasher.Parse_Space(code)
            side = census[s[Hasher.SIDE]]
            side[0] += s[Hasher.DRAGON]
            side[1] += s[Hasher.SPY]
            side[2] += s[Hasher.PAWNS]
            side[3] += s[Hasher.ROYAL]

        assert census == [[1, 1, 4, 1], [1, 1, 4, 1]]


def test_a_dealt_opening_leaves_a_playable_game():
    # Red opens, having entered second. A position nobody designed is still a position both
    # sides have moves in.
    from royals_engine import ai as artificialPlayer

    for seed in (1, 7, 99, 4242):
        board, _squares = dealOpening(seed)
        assert artificialPlayer.listAllMoves(board, 1)
        assert artificialPlayer.listAllMoves(board, 0)


####### Against the choosing opening #######

def test_dealing_does_not_disturb_the_entering_heuristic():
    # randomEntry and chooseEntry share nothing but enteringOptions, and this is what says
    # so: a dealt opening drawn in between two heuristic ones must leave the second
    # identical to the first. The heuristic's own RNG lives in ai.setEntryNoise, and a
    # random opening never touches it -- if it ever did, golden_enter.txt would move.
    artificialPlayer = __import__("royals_engine.ai", fromlist=["ai"])

    def heuristicFirstMove():
        artificialPlayer.setEntryNoise(0.5, 11)
        board = Hasher.Entering_Board()
        return artificialPlayer.chooseEntry(board, 0, Hasher.ROYAL, False)

    before = heuristicFirstMove()
    dealOpening(11)
    random.Random(11).random()          # and neither does the module-level RNG
    assert heuristicFirstMove() == before


####### The variety setting #######
# The 0-100 slider every front end shows, as `intensity` 0.0-1.0. What is asserted here is
# the two ends and the shape between them -- never a particular square, which would be
# golden_enter.txt written somewhere it cannot be re-recorded from.

def partFilled(seed, upTo):
    """A board some way through a dealt opening, so the tests are not all first placements."""
    board = Hasher.Entering_Board()
    for index, step in enumerate(Engine.enteringSequence()[:upTo]):
        contr, piece = step
        square = Engine.randomEntry(board, contr, piece == Hasher.SPY,
                                    Engine.entryRng(seed, index))
        if square is None: continue
        board = Engine.dropPiece(board, square, contr, piece)
    return board


def test_zero_is_the_square_the_computer_would_have_played():
    """The non-random end, pinned against the function it has to agree with.

    Not against a recorded square: that is golden_enter.txt's job, and a second copy of it
    here would be a second thing to re-record whenever the heuristic is tuned.
    """
    artificialPlayer = __import__("royals_engine.ai", fromlist=["ai"])
    artificialPlayer.setEntryNoise(0.5, 7)

    for upTo in (0, 3, 7, 11):
        board = partFilled(7, upTo)
        contr, piece = Engine.enteringSequence()[upTo]
        isSpy = (piece == Hasher.SPY)
        if not Engine.enteringOptions(board, contr, isSpy): continue

        best = artificialPlayer.chooseEntry(board, contr, piece, isSpy)
        for trial in range(0, 8):
            assert artificialPlayer.enterVaried(board, contr, piece, isSpy, 0.0,
                                                random.Random(trial)) == best


def test_one_hundred_is_uniform_over_every_legal_square():
    """The random end. Both halves matter: that it can reach every square, and that it
    does not favour one -- a draw off a truncated shortlist would pass the first."""
    artificialPlayer = __import__("royals_engine.ai", fromlist=["ai"])
    artificialPlayer.setEntryNoise(0.5, 7)

    board = Hasher.Entering_Board()
    options = Engine.enteringOptions(board, 0, False)
    picks = [artificialPlayer.enterVaried(board, 0, Hasher.ROYAL, False, 1.0,
                                          random.Random(trial))
             for trial in range(0, 4000)]

    assert set(picks) == set(options)
    share = 1.0 / len(options)
    for square in options:
        seen = picks.count(square) / len(picks)
        assert 0.5 * share < seen < 1.8 * share, "square %d came up %.3f" % (square, seen)


def test_the_middle_leans_on_the_computer_without_obeying_it():
    """Every pick is legal, the best square stays the single likeliest, and the setting has
    somewhere to travel -- the failure the old Perlin-only slider had, where 0.1 and 1.0 did
    much the same thing."""
    artificialPlayer = __import__("royals_engine.ai", fromlist=["ai"])
    artificialPlayer.setEntryNoise(0.5, 7)

    board = Hasher.Entering_Board()
    options = set(Engine.enteringOptions(board, 0, False))
    best = artificialPlayer.chooseEntry(board, 0, Hasher.ROYAL, False)

    # 150 a rung: every call runs the entering search, so this is the one test here with a
    # cost worth minding. The three shares are far enough apart (roughly 3/4, 1/2, 1/3) that
    # the ordering below is not a coin toss at this many.
    shares = []
    for intensity in (0.25, 0.5, 0.75):
        picks = [artificialPlayer.enterVaried(board, 0, Hasher.ROYAL, False, intensity,
                                              random.Random(trial))
                 for trial in range(0, 150)]
        assert set(picks) <= options, "an illegal square was drawn"
        shares.append(picks.count(best) / len(picks))
        assert set(picks) != {best}, "intensity %.2f never left the best square" % intensity

    assert shares[0] > shares[1] > shares[2], "the setting does not travel: %s" % (shares,)


def test_a_placement_is_reproducible_from_the_seed_and_the_step():
    artificialPlayer = __import__("royals_engine.ai", fromlist=["ai"])
    artificialPlayer.setEntryNoise(0.5, 7)
    board = Hasher.Entering_Board()

    for intensity in (0.0, 0.4, 1.0):
        again = [artificialPlayer.enterVaried(board, 0, Hasher.ROYAL, False, intensity,
                                              Engine.entryRng(99, step))
                 for step in range(0, 12) for _ in (0, 1)]
        assert again[0::2] == again[1::2], "the same (seed, step) drew differently"


def test_the_variety_setting_leaves_the_heuristic_alone():
    """enterVaried reads the noise fields; it must not set them. If it did, a game would
    change what the *next* one opens with, and golden_enter.txt would move underneath it."""
    artificialPlayer = __import__("royals_engine.ai", fromlist=["ai"])
    artificialPlayer.setEntryNoise(0.5, 11)
    noise, fields, seed = (artificialPlayer.ENTRY_NOISE,
                           artificialPlayer.ENTRY_FIELDS,
                           artificialPlayer.ENTRY_SEED)

    board = Hasher.Entering_Board()
    for intensity in (0.0, 0.5, 1.0):
        artificialPlayer.enterVaried(board, 0, Hasher.ROYAL, False, intensity,
                                     random.Random(3))

    assert artificialPlayer.ENTRY_NOISE == noise
    assert artificialPlayer.ENTRY_FIELDS is fields
    assert artificialPlayer.ENTRY_SEED == seed


####### Reversed entering: you lay out your opponent's army #######
# The rule is that the player who picks a square is not the player who owns the piece, and
# the whole risk in it is that those two are the same type -- a side index -- so swapping
# them produces something that runs perfectly and plays the game backwards. These tests are
# all about keeping the two apart.

from royals_engine import ai as AI


def test_the_chooser_is_always_the_other_side():
    assert Engine.enteringChooser(0) == 1
    assert Engine.enteringChooser(1) == 0
    for contr in (0, 1):
        assert Engine.enteringChooser(Engine.enteringChooser(contr)) == contr


def test_the_sequence_itself_is_unchanged():
    """Only the hand on the piece moved; the order did not.

    This is what lets the record format alone: a `@Rd3` token carries the piece and the
    square and derives the side from its index, so an entering order that still lists the
    same owner at the same step needs no notation change and no migration of stored games.
    """
    steps = Engine.enteringSequence()
    assert len(steps) == 12
    assert [side for side, _piece in steps] == [0, 1] * 6
    assert steps[0] == [0, Hasher.ROYAL]
    assert steps[-1] == [1, Hasher.SPY]


def test_legality_is_still_the_owner_s_and_not_the_chooser_s():
    """The rule a royal or pawn may not touch "anything you already control" is about the
    army being placed, not the hand placing it. If this ever starts asking about the chooser
    the openings stay legal-looking and are quietly a different game."""
    board = Hasher.Entering_Board()
    for index, (contr, piece) in enumerate(Engine.enteringSequence()):
        isSpy = (piece == Hasher.SPY)
        options = Engine.enteringOptions(board, contr, isSpy)
        chooser = Engine.enteringChooser(contr)

        assert options == Engine.enteringOptions(board, contr, isSpy), "owner's legality"
        if not isSpy and options:
            # the two really are different questions -- if they were not, this test proves
            # nothing at all, so assert they diverge somewhere in a normal opening
            assert set(options) != set(Engine.enteringOptions(board, chooser, isSpy)) \
                or index == 0

        if not options: continue
        board = Engine.dropPiece(board, options[0], contr, piece)


def test_the_heuristic_places_a_piece_badly_for_its_owner():
    """The point of the rule, and the assertion that a sign error would fail.

    chooseEntry is handed the OWNER, and must pick the square that leaves that owner worst
    off -- so its choice should score at or below the median of the legal squares, measured
    from the owner's point of view. Under the old rule it picked the maximum.
    """
    AI.setEntryNoise(0.0, 11)
    board = Hasher.Entering_Board()
    verdicts = []

    for contr, piece in Engine.enteringSequence():
        isSpy = (piece == Hasher.SPY)
        options = Engine.enteringOptions(board, contr, isSpy)
        if len(options) < 4:
            continue

        scored = {}
        for square in options:
            after = Hasher.Parse_Board(Engine.dropPiece(board, square, contr, piece))
            scored[square] = AI.entryDiff(after, contr)

        chosen = AI.chooseEntry(board, contr, piece, isSpy)
        ordered = sorted(scored.values())
        median = ordered[len(ordered) // 2]
        verdicts.append((scored[chosen], median, max(ordered)))

        board = Engine.dropPiece(board, chosen, contr, piece)

    assert verdicts, "the opening should offer some placements with real choice in them"
    assert all(got <= median for got, median, _best in verdicts), \
        "every placement should be at or below the median for the side receiving it"
    assert any(got < best for got, _median, best in verdicts), \
        "and at least one should be strictly worse than that side's own best square"
