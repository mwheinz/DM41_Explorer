"""Tests for gui/window_geometry.py: the rules for the main window's and
the dialogs' sizes -- scaled with the application font, never below what the
content needs, never larger than the screen (GitHub issue #42). Pure
arithmetic, so no display is needed."""

import pytest

from gui.window_geometry import (
    BASE_DEFAULT_SIZE,
    BASE_FONT_SIZE,
    BASE_MINIMUM_SIZE,
    default_window_size,
    dialog_size,
    font_scale,
    geometry_string,
    initial_window_size,
    minimum_window_size,
    parse_geometry,
    parse_position,
    restored_position,
    scaled_row_height,
    scaled_width,
)

BIG_SCREEN = (3840, 2160)
SMALL_SCREEN = (1280, 800)
NEEDED = (600, 330)  # what a default-font window's tab bar and status bar ask for


# -- The main window --------------------------------------------------------


def test_font_scale():
    assert font_scale(BASE_FONT_SIZE) == 1.0
    assert font_scale(BASE_FONT_SIZE * 2) == 2.0
    assert font_scale(8) == 1.0, "a smaller font must not shrink the window"
    assert font_scale(None) == 1.0
    assert font_scale("junk") == 1.0


def test_parse_geometry():
    assert parse_geometry("1080x768+10+20") == (1080, 768)
    assert parse_geometry("640x480") == (640, 480)
    assert parse_geometry("1x1+0+0") == (1, 1)
    assert parse_geometry("junk") is None
    assert parse_geometry("") is None
    assert parse_geometry(None) is None


def test_default_size_at_the_default_font_is_the_base_size():
    assert default_window_size(BASE_FONT_SIZE, BIG_SCREEN) == BASE_DEFAULT_SIZE


def test_default_and_minimum_sizes_grow_with_the_font():
    big_font = BASE_FONT_SIZE * 1.5
    default = default_window_size(big_font, BIG_SCREEN)
    assert default[0] == int(BASE_DEFAULT_SIZE[0] * 1.5)
    assert default[1] == int(BASE_DEFAULT_SIZE[1] * 1.5)
    minimum = minimum_window_size(big_font, NEEDED, BIG_SCREEN)
    assert minimum[0] == int(BASE_MINIMUM_SIZE[0] * 1.5)
    assert minimum[1] == int(BASE_MINIMUM_SIZE[1] * 1.5)


def test_minimum_size_is_at_least_what_the_widgets_need():
    wide = (2000, 900)
    assert minimum_window_size(BASE_FONT_SIZE, wide, BIG_SCREEN) == wide
    assert minimum_window_size(BASE_FONT_SIZE, NEEDED, BIG_SCREEN) == BASE_MINIMUM_SIZE


def test_sizes_never_exceed_the_screen():
    for font in (BASE_FONT_SIZE, 24, 48):
        w, h = default_window_size(font, SMALL_SCREEN)
        assert w <= SMALL_SCREEN[0] * 0.95 and h <= SMALL_SCREEN[1] * 0.90
        w, h = minimum_window_size(font, (5000, 5000), SMALL_SCREEN)
        assert w <= SMALL_SCREEN[0] * 0.95 and h <= SMALL_SCREEN[1] * 0.90


def test_a_saved_size_is_used():
    assert initial_window_size((1000, 700), BASE_FONT_SIZE, NEEDED, BIG_SCREEN) == (
        1000,
        700,
    )


def test_a_saved_size_below_the_minimum_is_raised_to_it():
    assert (
        initial_window_size((300, 200), BASE_FONT_SIZE, NEEDED, BIG_SCREEN)
        == BASE_MINIMUM_SIZE
    )
    # e.g. the font was enlarged since the size was saved.
    big = BASE_FONT_SIZE * 2
    assert initial_window_size(
        (1000, 700), big, NEEDED, BIG_SCREEN
    ) == minimum_window_size(big, NEEDED, BIG_SCREEN)


def test_a_saved_size_larger_than_the_screen_is_reduced():
    w, h = initial_window_size((9000, 9000), BASE_FONT_SIZE, NEEDED, SMALL_SCREEN)
    assert w <= SMALL_SCREEN[0] * 0.95 and h <= SMALL_SCREEN[1] * 0.90


@pytest.mark.parametrize(
    "bad", [None, (), (0, 0), (-5, 700), ("a", "b"), (1000,), "junk", 7]
)
def test_an_unusable_saved_size_falls_back_to_the_default(bad):
    assert (
        initial_window_size(bad, BASE_FONT_SIZE, NEEDED, BIG_SCREEN)
        == BASE_DEFAULT_SIZE
    )


def test_scaled_row_height():
    assert scaled_row_height(BASE_FONT_SIZE) == 22
    assert scaled_row_height(BASE_FONT_SIZE * 2) == 44
    assert scaled_row_height(9) == 22


def test_scaled_width():
    assert scaled_width(100, BASE_FONT_SIZE) == 100
    assert scaled_width(100, BASE_FONT_SIZE * 2) == 200
    assert scaled_width(100, 9) == 100


# -- Dialogs --------------------------------------------------------------


def test_dialog_size_at_the_default_font_is_the_base_size():
    assert dialog_size(BASE_FONT_SIZE, (460, 300), (100, 100), BIG_SCREEN) == (460, 300)


def test_dialog_size_grows_with_the_font():
    assert dialog_size(BASE_FONT_SIZE * 2, (460, 300), (0, 0), BIG_SCREEN) == (920, 600)


def test_dialog_size_is_never_smaller_than_the_content():
    assert dialog_size(BASE_FONT_SIZE, (460, 300), (700, 500), BIG_SCREEN) == (700, 500)


def test_dialog_size_with_no_base_is_the_content_size():
    assert dialog_size(BASE_FONT_SIZE, (0, 0), (333, 222), BIG_SCREEN) == (333, 222)


def test_dialog_size_never_exceeds_the_screen():
    width, height = dialog_size(48, (760, 560), (5000, 5000), SMALL_SCREEN)
    assert width <= SMALL_SCREEN[0] * 0.95
    assert height <= SMALL_SCREEN[1] * 0.90


def test_a_smaller_font_does_not_shrink_a_dialog():
    assert dialog_size(8, (460, 300), (0, 0), BIG_SCREEN) == (460, 300)


# -- Position (GitHub issue #63) ------------------------------------------------


@pytest.mark.parametrize(
    "geometry, expected",
    [
        ("1080x768+10+20", (10, 20)),
        ("1080x768+-1200+100", (-1200, 100)),
        ("1080x768+40+-30", (40, -30)),
        ("1080x768", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_position(geometry, expected):
    assert parse_position(geometry) == expected


def test_geometry_string():
    assert geometry_string((1080, 768)) == "1080x768"
    assert geometry_string((1080, 768), (10, 20)) == "1080x768+10+20"
    assert geometry_string((1080, 768), (-1200, 100)) == "1080x768+-1200+100"
    assert parse_position(geometry_string((900, 600), (-1200, -40))) == (-1200, -40)


MAIN = (0, 0, 1920, 1080)
LEFT = (-1600, 0, 1600, 900)  # a second monitor left of the main one
ABOVE = (0, -1080, 1920, 1080)  # and one above it
SIZE = (1080, 768)


@pytest.mark.parametrize(
    "saved, screens, expected",
    [
        ((100, 50), [MAIN], (100, 50)),
        ((-1200, 100), [MAIN, LEFT], (-1200, 100)),
        ((200, -900), [MAIN, ABOVE], (200, -900)),
        # Mostly off the right edge, but its title bar still reachable.
        ((1800, 200), [MAIN], (1800, 200)),
        # The monitor it was on has gone.
        ((-1200, 100), [MAIN], None),
        ((200, -900), [MAIN], None),
        # Off every edge of the one screen.
        ((1900, 200), [MAIN], None),
        ((-1050, 200), [MAIN], None),
        ((100, 1070), [MAIN], None),
        ((100, -5), [MAIN], None),
        (None, [MAIN], None),
    ],
)
def test_restored_position(saved, screens, expected):
    assert restored_position(saved, SIZE, screens) == expected


def test_a_narrow_window_needs_only_its_own_width_on_screen():
    assert restored_position((1850, 10), (60, 400), [MAIN]) == (1850, 10)
