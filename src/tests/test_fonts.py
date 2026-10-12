"""Tests for gui/fonts.py, the font roles (GitHub issue #61).

resolve_roles() is pure and needs no display; the apply()/font_for() tests
need a real Tk display (Xvfb in CI/sandboxes), like test_app.py.
"""

import tkinter.font as tkfont
from tkinter import ttk

import pytest

pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

import customtkinter as ctk

from config import FONT_ROLES, ProjectConfig
from gui import fonts
from gui.fonts import (
    MIN_FONT_SIZE,
    FontSpec,
    PlatformDefaults,
    font_for,
    font_spec,
    resolve_roles,
)
from gui.dialog_common import scaled, scaled_for
from gui.tab_common import build_tree_with_scrollbar, style_treeview, treeview_fonts
from gui.window_geometry import BASE_FONT_SIZE, scaled_width

DEFAULTS = PlatformDefaults(ui_family="Sans", mono_family="Mono", size=13)
INSTALLED = {"Sans", "Mono", "Helvetica", "Menlo"}


# ---- resolve_roles: the rule, no Tk ----


def test_every_role_at_its_default():
    assert resolve_roles({}, DEFAULTS, INSTALLED) == {
        "ui": FontSpec("Sans", 13, False),
        "small": FontSpec("Sans", 11, False),
        "title": FontSpec("Sans", 13, True),
        "mono": FontSpec("Mono", 13, False),
    }


def test_the_stored_defaults_resolve_like_no_stored_values():
    config = ProjectConfig()
    stored = {role: config.font_role(role) for role in FONT_ROLES}
    assert resolve_roles(stored, DEFAULTS, INSTALLED) == resolve_roles(
        {}, DEFAULTS, INSTALLED
    )


def test_small_title_and_mono_follow_a_chosen_ui_font():
    """Left at their defaults, the other roles are worked out from the ui
    role the user picked, the way the old ui_font(offset) followed the
    application font."""
    resolved = resolve_roles({"ui": ("Helvetica", 16, False)}, DEFAULTS, INSTALLED)
    assert resolved["small"] == FontSpec("Helvetica", 14, False)
    assert resolved["title"] == FontSpec("Helvetica", 16, True)
    assert resolved["mono"] == FontSpec("Mono", 16, False)


def test_each_role_can_be_set_on_its_own():
    stored = {
        "ui": ("Helvetica", 14, False),
        "small": ("Sans", 9, True),
        "title": ("Helvetica", 18, False),
        "mono": ("Menlo", 12, True),
    }
    assert resolve_roles(stored, DEFAULTS, INSTALLED) == {
        "ui": FontSpec("Helvetica", 14, False),
        "small": FontSpec("Sans", 9, True),
        "title": FontSpec("Helvetica", 18, False),
        "mono": FontSpec("Menlo", 12, True),
    }


def test_a_family_alone_keeps_the_default_size_and_the_other_way_round():
    stored = {"mono": ("Menlo", 0, False), "title": ("", 20, True)}
    resolved = resolve_roles(stored, DEFAULTS, INSTALLED)
    assert resolved["mono"] == FontSpec("Menlo", 13, False)
    assert resolved["title"] == FontSpec("Sans", 20, True)


def test_a_family_that_is_not_installed_falls_back_to_the_roles_default():
    stored = {"ui": ("Nowhere Sans", 14, False), "mono": ("Nowhere Mono", 0, False)}
    resolved = resolve_roles(stored, DEFAULTS, INSTALLED)
    assert resolved["ui"] == FontSpec("Sans", 14, False)
    assert resolved["mono"] == FontSpec("Mono", 14, False)  # the ui size


def test_installed_families_match_without_case():
    resolved = resolve_roles({"ui": ("helvetica", 0, False)}, DEFAULTS, INSTALLED)
    assert resolved["ui"].family == "helvetica"


def test_no_size_goes_below_the_floor():
    resolved = resolve_roles(
        {"ui": ("", 8, False), "mono": ("", 3, False)}, DEFAULTS, INSTALLED
    )
    assert resolved["ui"].size == MIN_FONT_SIZE
    assert resolved["small"].size == MIN_FONT_SIZE  # 8 - 2, held at the floor
    assert resolved["mono"].size == MIN_FONT_SIZE


def test_tk_tuple_names_bold_only_when_bold():
    assert FontSpec("Sans", 12, False).tk_tuple() == ("Sans", 12)
    assert FontSpec("Sans", 12, True).tk_tuple() == ("Sans", 12, "bold")


# ---- apply / font_for: against a live Tk ----


@pytest.fixture
def root():
    """A root window with CustomTkinter's theme font restored afterwards,
    since apply() writes into the process-wide theme."""
    theme_font = ctk.ThemeManager.theme["CTkFont"]
    saved = dict(theme_font)
    fonts.reset()
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()
    theme_font.clear()
    theme_font.update(saved)
    fonts.reset()


def test_apply_uses_tks_own_default_and_fixed_fonts(root):
    size = ctk.ThemeManager.theme["CTkFont"]["size"]
    resolved = fonts.apply(None, root)
    assert resolved["ui"] == FontSpec(
        tkfont.nametofont("TkDefaultFont", root=root).actual("family"), size, False
    )
    assert resolved["mono"] == FontSpec(
        tkfont.nametofont("TkFixedFont", root=root).actual("family"), size, False
    )


def test_apply_makes_the_ui_role_the_theme_font(root, tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", tmp_path / "prefs.json")
    family = sorted(f for f in tkfont.families(root) if not f.startswith("@"))[0]
    config = ProjectConfig()
    config.set_font_role("ui", family, 17, False)
    fonts.apply(config, root)
    theme_font = ctk.ThemeManager.theme["CTkFont"]
    assert (theme_font["family"], theme_font["size"]) == (family, 17)
    assert font_spec("ui") == FontSpec(family, 17, False)


def test_font_for_builds_the_roles_font(root):
    fonts.apply(None, root)
    spec = font_spec("title")
    font = font_for("title")
    assert (font.cget("family"), font.cget("size"), font.cget("weight")) == (
        spec.family,
        spec.size,
        "bold",
    )


def test_font_for_overrides_pass_through(root):
    fonts.apply(None, root)
    font = font_for("small", weight="bold")
    assert font.cget("weight") == "bold"
    assert font.cget("size") == font_spec("small").size


def test_font_spec_applies_the_defaults_if_nothing_has(root):
    assert font_spec("mono").family == tkfont.nametofont(
        "TkFixedFont", root=root
    ).actual("family")


def test_an_unknown_role_is_an_error(root):
    with pytest.raises(ValueError):
        font_spec("huge")


# ---- what follows the mono role ----


@pytest.fixture
def large_mono(root, tmp_path, monkeypatch):
    """The fonts with only the mono role set, to twice the base size."""
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", tmp_path / "prefs.json")
    config = ProjectConfig()
    config.set_font_role("mono", "", 2 * BASE_FONT_SIZE, False)
    fonts.apply(config, root)
    return root


def test_table_cells_use_mono_and_headings_use_title(large_mono):
    assert treeview_fonts() == (
        font_spec("mono").tk_tuple(),
        font_spec("title").tk_tuple(),
    )


def test_table_rows_and_columns_grow_with_the_mono_font(large_mono):
    style_treeview("Fonts.Treeview")
    assert int(ttk.Style().lookup("Fonts.Treeview", "rowheight")) == 44
    tree, _ = build_tree_with_scrollbar(
        ctk.CTkFrame(large_mono), [("value", "Value", 100, False)]
    )
    assert tree.column("value", "width") == 200


def test_scaled_for_follows_the_role_and_scaled_the_ui_font(large_mono):
    assert scaled_for("mono", 100) == 200
    assert scaled(100) == scaled_width(100, font_spec("ui").size)
