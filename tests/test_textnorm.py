from fslang import textnorm


def test_strict_unifies_spaces_and_invisible_chars():
    assert textnorm.strict("  a\u00a0b\u202fc\u3000d\u200b e\n f ") == "a b c d e f"


def test_strict_keeps_case_and_punctuation():
    assert textnorm.strict("Débloquez le jeu !") == "Débloquez le jeu !"


def test_loose_ignores_case_width_quotes_and_spaces():
    assert textnorm.loose("ＷＩＬＤ’s  Game！") == textnorm.loose("wild's game!")


def test_loose_with_map_points_back_to_original():
    original = "Get FREE Spins"
    low, mapping = textnorm.loose_with_map(original)
    assert low == "getfreespins"
    start = low.index("free")
    assert original[mapping[start]:mapping[start + 3] + 1] == "FREE"


def test_numbers_handles_thousand_separators():
    assert textnorm.numbers("2,000 ways on reels 1 and 5; reels 2, 3 and 4") == {2000, 1, 5, 2, 3, 4}
    assert textnorm.numbers("2\u00a0916 façons") == {2916}
    assert textnorm.numbers("2.916 modi") == {2916}
    assert textnorm.numbers("x1 multiplier") == {1}
