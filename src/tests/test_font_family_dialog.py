"""Tests for the font family picker and the monospace filter (GitHub issue
#61).

Requires a real Tk display (Xvfb in CI/sandboxes), like test_app.py.
"""

import tkinter.font as tkfont
from unittest import mock

import pytest

pytest.importorskip("customtkinter")

import customtkinter as ctk

from gui import fonts
from gui.font_family_dialog import FontFamilyDialog

DEFAULT = "System Default"
FAMILIES = ["Andale Mono", "Courier New", "Helvetica", "Menlo", "Times"]


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def picker(root):
    chosen = mock.Mock()
    dialog = FontFamilyDialog(
        root,
        "Application Font",
        FAMILIES,
        default_label=DEFAULT,
        current="Menlo",
        on_chosen=chosen,
    )
    dialog.withdraw()
    yield dialog, chosen
    if dialog.winfo_exists():
        dialog.destroy()


# ---- monospace_families ----


def test_monospace_families_are_installed_and_fixed_width(root):
    installed = set(fonts.installed_families(root)) | {
        tkfont.nametofont("TkFixedFont", root=root).actual("family")
    }
    families = fonts.monospace_families(root)
    assert families
    assert set(families) <= installed
    for family in families:
        font = tkfont.Font(root=root, family=family, size=-13)
        assert font.measure("iiii") == font.measure("MMMM"), family


def test_monospace_families_include_the_platform_default(root):
    default = tkfont.nametofont("TkFixedFont", root=root).actual("family")
    assert default in fonts.monospace_families(root)


def test_monospace_families_leave_out_the_proportional_ui_font(root):
    ui = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
    assert ui not in fonts.monospace_families(root)


# ---- the picker ----


def _rows(dialog):
    return list(dialog._tree.get_children())


def test_the_default_heads_the_list(picker):
    dialog, _ = picker
    assert _rows(dialog) == [DEFAULT] + FAMILIES


def test_the_current_family_starts_selected(picker):
    dialog, _ = picker
    assert dialog._tree.selection() == ("Menlo",)


def test_the_filter_narrows_the_list_ignoring_case_but_keeps_the_default(picker):
    dialog, _ = picker
    dialog._filter_var.set("mono")
    assert _rows(dialog) == [DEFAULT, "Andale Mono"]
    dialog._filter_var.set("")
    assert _rows(dialog) == [DEFAULT] + FAMILIES


def test_choose_reports_the_selected_family_and_closes(picker):
    dialog, chosen = picker
    dialog._tree.selection_set("Times")
    dialog.update()
    dialog._choose()
    chosen.assert_called_once_with("Times")
    assert not dialog.winfo_exists()


def test_choosing_the_default_reports_its_label(picker):
    dialog, chosen = picker
    dialog._tree.selection_set(DEFAULT)
    dialog.update()
    dialog._choose()
    chosen.assert_called_once_with(DEFAULT)


def test_cancel_closes_without_choosing(picker):
    dialog, chosen = picker
    dialog._close()
    chosen.assert_not_called()
    assert not dialog.winfo_exists()
