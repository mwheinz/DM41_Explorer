"""Tests for GitHub issue #47: the color theme and high-contrast choices in
Preferences, and how the app applies them at start-up.

Requires a real Tk display (Xvfb in CI/sandboxes), like test_app.py.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk

from config import ProjectConfig
from gui.preferences_dialog import COLOR_THEMES, PreferencesDialog


def _option_menu_values(dialog, variable):
    """The choices of the option menu bound to `variable`."""
    menus = [
        w
        for w in _all_widgets(dialog)
        if isinstance(w, ctk.CTkOptionMenu) and w._variable is variable
    ]
    assert len(menus) == 1
    return list(menus[0]._values)


def _all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from _all_widgets(child)


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    path = tmp_path / ".dm41_test_prefs.json"
    path.write_text(json.dumps({"log_directory": str(tmp_path / "logs")}))
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", path)
    return path


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def config(prefs_file):
    c = ProjectConfig()
    c.load()
    return c


def _dialog(root, config, on_saved=None):
    serial = mock.Mock()
    serial.get_available_ports.return_value = []
    dialog = PreferencesDialog(root, config, serial, on_saved=on_saved)
    dialog.withdraw()
    return dialog


def test_the_listed_themes_are_the_ones_customtkinter_ships():
    themes_dir = Path(ctk.__file__).parent / "assets" / "themes"
    assert {p.stem for p in themes_dir.glob("*.json")} == set(COLOR_THEMES)


def test_the_dialog_offers_every_theme_and_shows_the_current_one(root, config):
    config.color_theme = "green"
    dialog = _dialog(root, config)
    assert _option_menu_values(dialog, dialog._color_theme_var) == COLOR_THEMES
    assert dialog._color_theme_var.get() == "green"
    dialog.destroy()


def test_a_theme_that_is_not_built_in_stays_selectable(root, config):
    config.color_theme = "/somewhere/my_theme.json"
    dialog = _dialog(root, config)
    assert _option_menu_values(dialog, dialog._color_theme_var) == COLOR_THEMES + [
        "/somewhere/my_theme.json"
    ]
    assert dialog._color_theme_var.get() == "/somewhere/my_theme.json"
    dialog.destroy()


def test_the_dialog_shows_the_current_contrast_choice(root, config):
    dialog = _dialog(root, config)
    assert dialog._high_contrast_var.get() is True
    dialog.destroy()

    config.high_contrast = False
    dialog = _dialog(root, config)
    assert dialog._high_contrast_var.get() is False
    dialog.destroy()


def test_save_stores_the_theme_and_contrast_in_the_prefs_file(
    root, config, prefs_file
):
    saved = mock.Mock()
    dialog = _dialog(root, config, on_saved=saved)
    dialog._color_theme_var.set("dark-blue")
    dialog._high_contrast_var.set(False)
    dialog._on_save()

    on_disk = json.loads(prefs_file.read_text())
    assert on_disk["color_theme"] == "dark-blue"
    assert on_disk["high_contrast"] is False
    saved.assert_called_once()


# -- Start-up ------------------------------------------------------------------


def test_an_unloadable_theme_falls_back_to_the_default(tmp_path):
    from gui.app import _apply_color_theme

    default = ProjectConfig.DEFAULT_PREFS["color_theme"]
    with mock.patch("gui.app.ctk.set_default_color_theme") as load:
        load.side_effect = [FileNotFoundError("gone"), None]
        _apply_color_theme(str(tmp_path / "missing.json"))
    assert load.call_args_list == [
        mock.call(str(tmp_path / "missing.json")),
        mock.call(default),
    ]


def test_a_theme_file_that_is_not_json_falls_back_to_the_default(tmp_path):
    from gui.app import _apply_color_theme

    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    _apply_color_theme(str(bad))  # must not raise
    assert ctk.ThemeManager._currently_loaded_theme == ProjectConfig.DEFAULT_PREFS[
        "color_theme"
    ]


@pytest.mark.parametrize("theme", COLOR_THEMES)
def test_every_listed_theme_loads(theme):
    from gui.app import _apply_color_theme

    _apply_color_theme(theme)
    assert ctk.ThemeManager._currently_loaded_theme == theme
    ctk.set_default_color_theme(ProjectConfig.DEFAULT_PREFS["color_theme"])


@pytest.fixture
def make_app(prefs_file):
    apps = []

    def build(**prefs):
        data = json.loads(prefs_file.read_text())
        data.update(prefs)
        prefs_file.write_text(json.dumps(data))
        from gui.app import DM41ExplorerApp

        app = DM41ExplorerApp()
        apps.append(app)
        return app

    yield build
    for app in apps:
        try:
            app.destroy()
        except Exception:
            pass
    ctk.set_default_color_theme(ProjectConfig.DEFAULT_PREFS["color_theme"])


def _button_text_color():
    return json.dumps(ctk.ThemeManager.theme["CTkButton"]["text_color"])


def test_high_contrast_off_leaves_the_themes_own_colors(make_app):
    app = make_app(color_theme="blue", high_contrast=False)
    raw = json.loads(
        (Path(ctk.__file__).parent / "assets" / "themes" / "blue.json").read_text()
    )
    assert ctk.ThemeManager.theme["CTkButton"] == raw["CTkButton"]
    app.destroy()


def test_high_contrast_on_changes_the_blue_themes_button_colors(make_app):
    app = make_app(color_theme="blue", high_contrast=True)
    raw = json.loads(
        (Path(ctk.__file__).parent / "assets" / "themes" / "blue.json").read_text()
    )
    assert ctk.ThemeManager.theme["CTkButton"] != raw["CTkButton"]
    app.destroy()


def test_the_app_starts_with_a_theme_that_cannot_be_loaded(make_app, tmp_path):
    make_app(color_theme=str(tmp_path / "missing.json"))
    assert ctk.ThemeManager._currently_loaded_theme == ProjectConfig.DEFAULT_PREFS[
        "color_theme"
    ]
