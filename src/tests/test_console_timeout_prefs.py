"""Tests for the console timeout choice on the Preferences dialog's
Connection tab: the default (10 minutes) must be one of the choices, and a
value from a hand-edited preferences file stays on offer.

Requires a real Tk display (Xvfb in CI/sandboxes), like test_app.py.
"""

import json
from unittest import mock

import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk

from config import ProjectConfig
from gui.preferences_dialog import CONSOLE_TIMEOUTS, PreferencesDialog


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


def _dialog(root, config):
    serial = mock.Mock()
    serial.get_available_ports.return_value = []
    dialog = PreferencesDialog(root, config, serial)
    dialog.withdraw()
    return dialog


def test_the_default_timeout_is_one_of_the_choices(root, config):
    default = str(ProjectConfig.DEFAULT_PREFS["console_timeout_minutes"])
    assert default in CONSOLE_TIMEOUTS
    dialog = _dialog(root, config)
    assert dialog._timeout_var.get() == default
    assert _option_menu_values(dialog, dialog._timeout_var) == CONSOLE_TIMEOUTS
    dialog.destroy()


def test_a_hand_edited_timeout_stays_on_offer_and_is_saved(root, config):
    config.console_timeout_minutes = 7
    dialog = _dialog(root, config)
    assert dialog._timeout_var.get() == "7"
    assert "7" in _option_menu_values(dialog, dialog._timeout_var)
    dialog._on_save()
    assert config.console_timeout_minutes == 7
