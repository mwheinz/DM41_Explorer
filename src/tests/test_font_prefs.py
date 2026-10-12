"""Tests for the Fonts tab of the Preferences dialog (GitHub issue #61).

Requires a real Tk display (Xvfb in CI/sandboxes), like test_app.py.
"""

import json
from unittest import mock

import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk

from config import FONT_ROLES, ProjectConfig
from gui import fonts
from gui.preferences_dialog import (
    FONT_DEFAULT_LABEL,
    FONT_SIZE_DEFAULT_LABEL,
    FONT_SIZES,
    PreferencesDialog,
)


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    path = tmp_path / ".dm41_test_prefs.json"
    path.write_text(json.dumps({"log_directory": str(tmp_path / "logs")}))
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", path)
    return path


@pytest.fixture
def root():
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


@pytest.fixture
def config(prefs_file):
    c = ProjectConfig()
    c.load()
    return c


@pytest.fixture
def family(root):
    """An installed family that is not anyone's default."""
    defaults = fonts.platform_defaults(root)
    return next(
        f
        for f in fonts.installed_families(root)
        if f not in (defaults.ui_family, defaults.mono_family)
    )


def _dialog(root, config, on_saved=None):
    serial = mock.Mock()
    serial.get_available_ports.return_value = []
    dialog = PreferencesDialog(root, config, serial, on_saved=on_saved)
    dialog.withdraw()
    return dialog


def _choices(dialog, role):
    family_var, size_var, bold_var = dialog._font_vars[role]
    return (family_var.get(), size_var.get(), bold_var.get())


def _sample(dialog, role):
    font = dialog._font_samples[role]
    return (font.cget("family"), font.cget("size"), font.cget("weight"))


def test_every_role_has_a_row_showing_its_defaults(root, config):
    dialog = _dialog(root, config)
    assert set(dialog._font_vars) == set(FONT_ROLES)
    for role in FONT_ROLES:
        bold = role == "title"
        assert _choices(dialog, role) == (
            FONT_DEFAULT_LABEL,
            FONT_SIZE_DEFAULT_LABEL,
            bold,
        )
    dialog.destroy()


def test_the_rows_show_the_saved_roles(root, config, family):
    config.set_font_role("mono", family, 16, True)
    dialog = _dialog(root, config)
    assert _choices(dialog, "mono") == (family, "16", True)
    dialog.destroy()


def test_a_family_or_size_not_offered_stays_selectable(root, config):
    config.set_font_role("ui", "Nowhere Sans", 15, False)
    dialog = _dialog(root, config)
    assert _choices(dialog, "ui") == ("Nowhere Sans", "15", False)
    dialog.destroy()


def test_save_stores_every_role_in_the_prefs_file(root, config, prefs_file, family):
    saved = mock.Mock()
    dialog = _dialog(root, config, on_saved=saved)
    family_var, size_var, bold_var = dialog._font_vars["title"]
    family_var.set(family)
    size_var.set("18")
    bold_var.set(False)
    dialog._on_save()

    on_disk = json.loads(prefs_file.read_text())
    assert on_disk["font_title"] == [family, 18, False]
    assert on_disk["font_ui"] == ["", 0, False]
    assert "font_family" not in on_disk and "font_size" not in on_disk
    saved.assert_called_once()


def test_restore_defaults_resets_every_row_but_saves_nothing(
    root, config, prefs_file, family
):
    for role in FONT_ROLES:
        config.set_font_role(role, family, 20, role != "title")
    config.save()
    before = prefs_file.read_text()

    dialog = _dialog(root, config)
    dialog._restore_default_fonts()
    for role in FONT_ROLES:
        assert _choices(dialog, role) == (
            FONT_DEFAULT_LABEL,
            FONT_SIZE_DEFAULT_LABEL,
            role == "title",
        )
    assert prefs_file.read_text() == before
    dialog.destroy()


def test_each_sample_is_drawn_in_the_font_chosen(root, config, family):
    dialog = _dialog(root, config)
    family_var, size_var, bold_var = dialog._font_vars["mono"]
    family_var.set(family)
    size_var.set("20")
    bold_var.set(True)
    assert _sample(dialog, "mono") == (family, 20, "bold")
    dialog.destroy()


def test_samples_left_at_their_defaults_follow_the_ui_choice(root, config, family):
    """The small role's default is the ui font two points smaller, so its
    sample changes with the ui row -- as the font will after a restart."""
    dialog = _dialog(root, config)
    family_var, size_var, _ = dialog._font_vars["ui"]
    family_var.set(family)
    size_var.set("18")
    assert _sample(dialog, "small") == (family, 16, "normal")
    assert _sample(dialog, "title") == (family, 18, "bold")
    dialog.destroy()


def test_the_offered_sizes_reach_down_to_the_floor():
    assert int(FONT_SIZES[0]) == fonts.MIN_FONT_SIZE


# ---- the family picker (a list, since Tk menus can't scroll on Linux) ----


def test_the_mono_row_offers_only_monospace_families(root, config):
    dialog = _dialog(root, config)
    picker = dialog._choose_font_family("mono")
    assert picker._families == fonts.monospace_families(root)
    picker._close()
    dialog.destroy()


def test_the_other_rows_offer_every_installed_family(root, config):
    dialog = _dialog(root, config)
    for role in ("ui", "small", "title"):
        picker = dialog._choose_font_family(role)
        assert picker._families == fonts.installed_families(root)
        picker._close()
    dialog.destroy()


def test_a_saved_family_not_installed_here_is_still_listed(root, config):
    config.set_font_role("mono", "Nowhere Mono", 0, False)
    dialog = _dialog(root, config)
    picker = dialog._choose_font_family("mono")
    assert "Nowhere Mono" in picker._families
    picker._close()
    dialog.destroy()


def test_picking_a_family_sets_the_row_and_redraws_its_sample(root, config):
    dialog = _dialog(root, config)
    family = fonts.monospace_families(root)[0]
    picker = dialog._choose_font_family("mono")
    picker._tree.selection_set(family)
    picker.update()
    picker._choose()
    assert _choices(dialog, "mono")[0] == family
    assert _sample(dialog, "mono")[0] == family
    dialog.destroy()
