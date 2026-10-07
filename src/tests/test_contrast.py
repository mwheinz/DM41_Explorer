"""Tests for gui/contrast.py and for the colors the GUI actually uses
(GitHub issue #42, item 1: "Improve the contrast of the colors used both in
light and dark mode").

The math and theme tests need no display. The palette tests import the real
color constants from the GUI modules, so editing a color to something too
faint fails here instead of being noticed by eye.
"""
import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk

from gui import contrast
from gui.contrast import (
    MIN_DISABLED_CONTRAST,
    MIN_TEXT_CONTRAST,
    SECONDARY_TEXT,
    WARNING_TEXT,
    adjust_background,
    adjust_text,
    contrast_ratio,
    improve_pair,
    improve_theme_contrast,
    parse_color,
    resolve,
)

THEME_DIR = Path(ctk.__file__).parent / "assets" / "themes"
THEME_NAMES = sorted(p.stem for p in THEME_DIR.glob("*.json"))
MODES = ("Light", "Dark")


def _load_theme(name):
    return json.loads((THEME_DIR / f"{name}.json").read_text())


# -- the math ---------------------------------------------------------------


def test_parse_color_forms():
    assert parse_color("#3B8ED0") == (0x3B, 0x8E, 0xD0)
    assert parse_color("white") == (255, 255, 255)
    assert parse_color("gray50") == (128, 128, 128)  # Tk: 50% of 255, rounded
    assert parse_color("grey0") == (0, 0, 0)
    assert parse_color("gray100") == (255, 255, 255)
    assert parse_color("transparent") is None
    assert parse_color("gray101") is None
    assert parse_color(("gray10", "gray90")) is None


def test_contrast_ratio_matches_wcag_reference_values():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    # The WCAG tooling's well-known "just fails" gray on white: 4.48:1.
    assert contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)


def test_contrast_ratio_is_symmetric_and_rejects_unknown_colors():
    assert contrast_ratio("#123456", "#abcdef") == contrast_ratio("#abcdef", "#123456")
    with pytest.raises(ValueError):
        contrast_ratio("transparent", "#ffffff")


def test_resolve_picks_the_mode():
    assert resolve(("a", "b"), "Light") == "a"
    assert resolve(["a", "b"], "Dark") == "b"
    assert resolve("plain", "Dark") == "plain"


def test_adjust_background_leaves_a_passing_pair_alone():
    assert adjust_background("#ffffff", "#1f6aa5") == "#1f6aa5"
    assert adjust_background("white", "transparent") == "transparent"


def test_adjust_background_moves_just_far_enough():
    darker = adjust_background("#ffffff", "#3B8ED0")
    assert contrast_ratio("#ffffff", darker) >= MIN_TEXT_CONTRAST
    # One percent less movement would not have been enough.
    assert contrast_ratio("#ffffff", "#3B8ED0") < MIN_TEXT_CONTRAST
    lighter = adjust_background("#000000", "#3a3a3a")
    assert contrast_ratio("#000000", lighter) >= MIN_TEXT_CONTRAST


def test_adjust_text_goes_the_other_way_when_its_own_direction_cannot_work():
    # Near-white text on gold: whiter text can never reach 3:1 (white on
    # #efb951 is only 1.8:1), so the text has to go dark instead.
    fixed = adjust_text("gray98", "#EFB951", MIN_DISABLED_CONTRAST)
    assert contrast_ratio(fixed, "#EFB951") >= MIN_DISABLED_CONTRAST
    assert parse_color(fixed)[0] < 128


def test_improve_pair_keeps_light_text_when_a_small_shift_is_enough():
    text, bg = improve_pair("#DCE4EE", "#3B8ED0")  # the blue theme's button
    assert text == "#ffffff"
    assert contrast_ratio(text, bg) >= MIN_TEXT_CONTRAST


def test_improve_pair_flips_to_dark_text_on_a_bright_fill():
    text, bg = improve_pair("gray98", "#2CC985")  # the green theme's button
    assert text == "#000000"
    assert bg.lower() == "#2cc985", "the theme's own fill color should be kept"
    assert contrast_ratio(text, bg) >= MIN_TEXT_CONTRAST


def test_improve_pair_returns_a_passing_pair_unchanged():
    assert improve_pair("gray10", "#F9F9FA") == ("gray10", "#F9F9FA")
    assert improve_pair("white", "transparent") == ("white", "transparent")


# -- CustomTkinter's own themes -----------------------------------------------


def _text_pairs(theme):
    """Every (description, text, background, minimum) the pass promises."""
    for widget, text_key, primaries, secondaries, quiet, quiet_min in contrast._THEME_GROUPS:
        entry = theme[widget]
        for mode in MODES:
            text = resolve(entry[text_key], mode)
            for key in primaries + secondaries:
                if key in entry:
                    yield (
                        f"{widget}.{key} ({mode})",
                        text,
                        resolve(entry[key], mode),
                        MIN_TEXT_CONTRAST,
                    )
            if quiet:
                yield (
                    f"{widget}.{quiet} ({mode})",
                    resolve(entry[quiet], mode),
                    resolve(entry[primaries[0]], mode),
                    quiet_min,
                )


@pytest.mark.parametrize("name", THEME_NAMES)
def test_every_builtin_theme_reaches_the_minimum_after_the_pass(name):
    theme = _load_theme(name)
    improve_theme_contrast(theme)
    failures = [
        f"{what}: {text} on {bg} = {contrast_ratio(text, bg):.2f} (< {minimum})"
        for what, text, bg, minimum in _text_pairs(theme)
        if contrast_ratio(text, bg) < minimum
    ]
    assert not failures


def test_the_builtin_themes_do_fail_without_the_pass():
    """Guards the guard: if CustomTkinter's themes ever all pass on their
    own, this pass (and the app calling it) is dead code."""
    theme = _load_theme("blue")
    assert any(
        contrast_ratio(text, bg) < minimum for _, text, bg, minimum in _text_pairs(theme)
    )


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_pass_is_idempotent_and_touches_only_colors(name):
    theme = _load_theme(name)
    improve_theme_contrast(theme)
    once = copy.deepcopy(theme)
    improve_theme_contrast(theme)
    assert theme == once

    untouched = _load_theme(name)
    for widget in ("CTkFont", "CTkFrame", "CTkLabel", "CTkScrollbar"):
        assert theme[widget] == untouched[widget]
    for widget, entry in theme.items():
        for key, value in entry.items():
            if not key.endswith("color") and "color" not in key:
                assert value == untouched[widget][key], (widget, key)


@pytest.mark.parametrize("name", THEME_NAMES)
def test_hover_colors_stay_distinct_from_the_resting_color(name):
    theme = _load_theme(name)
    improve_theme_contrast(theme)
    button = theme["CTkButton"]
    for mode in MODES:
        assert resolve(button["fg_color"], mode) != resolve(button["hover_color"], mode)


def test_an_unparseable_theme_color_is_left_alone():
    theme = {"CTkButton": {"text_color": "gray98", "fg_color": "transparent",
                           "hover_color": ["#ffffff", "#000000"]}}
    improve_theme_contrast(theme)
    assert theme["CTkButton"]["fg_color"] == "transparent"
    assert theme["CTkButton"]["text_color"] == "gray98"


# -- The app's own palette ----------------------------------------------------

# Backgrounds that ordinary text sits on, resolved per mode: the window
# (gray92/gray14), a frame or tab body (gray86/gray17), a card
# (CARD_FG = gray92/gray17), and an unassigned key cell.
TEXT_BACKGROUNDS = {
    "window": ("gray92", "gray14"),
    "frame": ("gray86", "gray17"),
    "card": ("gray92", "gray17"),
    "unassigned key": ("gray85", "gray24"),
}


@pytest.mark.parametrize("name", sorted(TEXT_BACKGROUNDS))
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "color_name,color", [("SECONDARY_TEXT", SECONDARY_TEXT), ("WARNING_TEXT", WARNING_TEXT)]
)
def test_shared_palette_text_is_readable_on_every_background(name, mode, color_name, color):
    bg = resolve(TEXT_BACKGROUNDS[name], mode)
    assert contrast_ratio(resolve(color, mode), bg) >= MIN_TEXT_CONTRAST


def test_card_background_constants_still_match_this_test():
    from gui.tab_common import CARD_FG

    assert tuple(CARD_FG) == TEXT_BACKGROUNDS["card"]


@pytest.mark.parametrize("mode", MODES)
def test_key_assignments_tab_colors(mode):
    from gui import key_assignments_tab as kat

    pairs = {
        "unassigned": (kat.UNASSIGNED_TEXT, kat.UNASSIGNED_FG),
        "flag clear": (kat.FLAG_CLEAR_TEXT, kat.FLAG_CLEAR_FG),
    }
    for label, bg in kat.STATIC_CELL_BG_COLORS.items():
        pairs[f"static cell {label}"] = (kat.STATIC_CELL_TEXT_COLORS[label], bg)
    for what, (text, bg) in pairs.items():
        ratio = contrast_ratio(resolve(text, mode), resolve(bg, mode))
        assert ratio >= MIN_TEXT_CONTRAST, f"{what} ({mode}): {ratio:.2f}"


@pytest.mark.parametrize("mode", MODES)
def test_treeview_text_is_readable_on_stripes_and_selection(mode):
    from gui import tab_common as tc

    fg = resolve(tc.TREEVIEW_FG, mode)
    for bg in (
        resolve(tc.TREEVIEW_FIELD_BG, mode),
        tc.STRIPE_BG_DARK if mode == "Dark" else tc.STRIPE_BG_LIGHT,
    ):
        assert contrast_ratio(fg, bg) >= MIN_TEXT_CONTRAST
    assert contrast_ratio(tc.SELECTED_ROW_FG, tc.SELECTED_ROW_BG) >= MIN_TEXT_CONTRAST


@pytest.mark.parametrize("mode", MODES)
def test_hex_view_region_tints_keep_the_row_text_readable(mode):
    from gui import tab_common as tc
    from gui.hex_view_tab import REGIONS

    fg = resolve(tc.TREEVIEW_FG, mode)
    for key, _label, light_bg, dark_bg in REGIONS:
        bg = dark_bg if mode == "Dark" else light_bg
        assert contrast_ratio(fg, bg) >= MIN_TEXT_CONTRAST, f"{key} ({mode})"


@pytest.mark.parametrize("mode", MODES)
def test_red_delete_buttons_do_not_depend_on_the_themes_text_color(mode):
    """The Remove buttons in the Programs, Alarms and XM Files tabs share
    DANGER_BUTTON_KWARGS. The gold and green themes' own button text is
    black, which would be 3.3:1 on this red."""
    from gui.tab_common import DANGER_BUTTON_KWARGS as kw

    assert contrast_ratio(kw["text_color"], kw["fg_color"]) >= MIN_TEXT_CONTRAST
    assert contrast_ratio(kw["text_color"], kw["hover_color"]) >= MIN_TEXT_CONTRAST
    assert contrast_ratio(kw["text_color_disabled"], kw["fg_color"]) >= MIN_DISABLED_CONTRAST


def test_every_remove_button_uses_the_shared_danger_style():
    src = Path(__file__).parent.parent / "gui"
    for name in ("alarms_tab.py", "program_tab.py", "xm_files_tab.py"):
        text = (src / name).read_text()
        assert "**DANGER_BUTTON_KWARGS" in text, name
        assert '"#a03e3e"' not in text, f"{name} still hard-codes the red"


# -- The running app ------------------------------------------------------------


def test_the_app_applies_the_pass_before_building_widgets(tmp_path, monkeypatch):
    from config import ProjectConfig
    from gui.app import DM41ExplorerApp

    prefs = tmp_path / ".prefs.json"
    prefs.write_text(json.dumps({"log_directory": str(tmp_path / "logs")}))
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", prefs)

    app = DM41ExplorerApp()
    try:
        theme = ctk.ThemeManager.theme
        for mode in MODES:
            assert (
                contrast_ratio(
                    resolve(theme["CTkButton"]["text_color"], mode),
                    resolve(theme["CTkButton"]["fg_color"], mode),
                )
                >= MIN_TEXT_CONTRAST
            )
        # And the widgets really were built from the adjusted theme.
        probe = ctk.CTkButton(app)
        mode = ctk.get_appearance_mode()
        assert (
            contrast_ratio(
                resolve(probe.cget("text_color"), mode), resolve(probe.cget("fg_color"), mode)
            )
            >= MIN_TEXT_CONTRAST
        )
    finally:
        app.destroy()
