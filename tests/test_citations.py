from src.pipeline.citations import apply_citations


def test_keeps_valid_citations():
    content, used = apply_citations("Rost otnoshenij [1] i spad [2].", {1, 2, 3})
    assert content == "Rost otnoshenij [1] i spad [2]."
    assert used == {1, 2}


def test_strips_phantom_citations():
    content, used = apply_citations("Fakt [1], vydumka [9].", {1, 2})
    assert content == "Fakt [1], vydumka ."
    assert used == {1}


def test_no_citations_passthrough():
    content, used = apply_citations("Prosto tekst bez snosok.", {1})
    assert content == "Prosto tekst bez snosok."
    assert used == set()


def test_multi_digit_and_adjacent():
    content, used = apply_citations("Sobytie [10][11], eshche [2]", {2, 10, 11})
    assert content == "Sobytie [10][11], eshche [2]"
    assert used == {2, 10, 11}


def test_phantom_strip_collapses_spaces():
    content, used = apply_citations("A [1] B [99] C", {1})
    assert content == "A [1] B C"
    assert used == {1}


def test_zero_is_always_phantom():
    content, used = apply_citations("X [0] Y", {0})
    assert content == "X Y"
    assert used == set()


def test_cyrillic_content():
    content, used = apply_citations("Рост напряжённости [1] и спад [7].", {1})
    assert content == "Рост напряжённости [1] и спад ."
    assert used == {1}
