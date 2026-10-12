"""Tests for gui/xrom_libraries_dialog.py, the XROM library manager (GitHub
issue #53): the rows it lists, ticking and ordering them, importing and
removing user libraries, the conflict panel, and what it hands back.

The dialog is built against a live (withdrawn) Tk root and driven through
its handlers, as the other dialog tests do. Requires a real Tk display.
"""

from unittest import mock

import pytest

pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

from tkinter import messagebox

import customtkinter as ctk

from config import ProjectConfig
from gui.xrom_libraries_dialog import LibraryDetailsDialog, XromLibrariesDialog
from memory import DeviceMode
from memory.xrom_import import user_library_paths


@pytest.fixture(autouse=True)
def no_unexpected_boxes(monkeypatch):
    """A real message box waits for a click and would hang the run; a test
    that expects one patches it itself, inside this."""
    for name in ("askyesno", "askyesnocancel", "showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(
            messagebox,
            name,
            mock.Mock(side_effect=AssertionError(f"unexpected messagebox.{name}")),
        )


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", tmp_path / ".dm41_test_prefs.json")
    return ProjectConfig()


@pytest.fixture
def make(root, config):
    """make(mode=..., entries=...) builds the dialog; on_saved is a Mock,
    available as dialog.saved."""
    made = []

    def _make(mode=DeviceMode.DM41X, entries=None):
        if entries is not None:
            config.set_xrom_library_entries(mode, entries)
        saved = mock.Mock()
        dialog = XromLibrariesDialog(root, config, mode, on_saved=saved)
        dialog.saved = saved
        made.append(dialog)
        return dialog

    yield _make
    for dialog in made:
        if dialog.winfo_exists():
            dialog.destroy()


def _ids(dialog):
    return [row.id for row in dialog._rows]


def _index(dialog, library_id):
    return _ids(dialog).index(library_id)


def _conflict_text(dialog):
    return dialog._conflicts.get("1.0", "end").strip()


def _write_list(tmp_path, name, text="FOO 11 1\nBAR 11 2\n"):
    path = tmp_path / name
    path.write_text(text)
    return path


# -- What is listed -------------------------------------------------------


def test_every_pluggable_library_is_listed_unticked_by_title(make):
    dialog = make()
    ids = _ids(dialog)
    assert "math" in ids and "advantage" in ids and "dm41x-thermal-printer" in ids
    assert "dm41x-cx" not in ids and "hp41cx" not in ids, "built-in ROMs aren't rows"
    assert not any(row.enabled for row in dialog._rows)
    titles = [row.library.title.lower() for row in dialog._rows]
    assert titles == sorted(titles)
    assert _conflict_text(dialog) == "No conflicts."


def test_the_saved_arrangement_comes_first_in_its_order(make):
    dialog = make(entries=[("stat", False), ("math", True), ("gone", True)])
    assert _ids(dialog)[:3] == ["stat", "math", "gone"]
    assert dialog._rows[2].library is None, "a missing id is kept, not dropped"
    assert dialog.entries()[:3] == [("stat", False), ("math", True), ("gone", True)]
    assert "Ticked but missing: gone" in _conflict_text(dialog)


def test_the_intro_says_a_dm41l_cannot_load_modules(make):
    assert "cannot load modules" in make(DeviceMode.DM41L)._intro.cget("text")
    assert "cannot load modules" not in make(DeviceMode.DM41X)._intro.cget("text")


# -- Ticking, ordering, saving --------------------------------------------------


def test_ticking_and_moving_then_saving_hands_back_the_arrangement(make):
    dialog = make()
    dialog._toggle(_index(dialog, "math"))
    dialog._toggle(_index(dialog, "advantage"))
    while _index(dialog, "math") > 0:
        dialog._select(_index(dialog, "math"))
        dialog._move(-1)
    dialog._select(_index(dialog, "advantage"))
    while _index(dialog, "advantage") > 1:
        dialog._move(-1)

    dialog._save()

    (entries,) = dialog.saved.call_args.args
    assert entries[:2] == [("math", True), ("advantage", True)]
    assert all(not enabled for _id, enabled in entries[2:])
    assert not dialog.winfo_exists()


def test_cancel_without_file_changes_saves_nothing(make):
    dialog = make()
    dialog._toggle(0)
    dialog._cancel()
    dialog.saved.assert_not_called()


def test_up_and_down_stop_at_the_ends(make):
    dialog = make()
    first = _ids(dialog)[0]
    dialog._select(0)
    dialog._move(-1)
    assert _ids(dialog)[0] == first
    assert dialog._buttons["Up"].cget("state") == "disabled"
    assert dialog._buttons["Down"].cget("state") == "normal"


def test_the_conflict_panel_follows_the_ticks(make):
    dialog = make()
    dialog._toggle(_index(dialog, "aviation"))
    dialog._toggle(_index(dialog, "clinical"))
    text = _conflict_text(dialog)
    assert text.startswith("Warning: Aviation Pac and Clinical Lab")
    assert "XROM 19" in text


def test_shipped_libraries_cannot_be_removed(make):
    dialog = make()
    dialog._select(_index(dialog, "math"))
    assert dialog._buttons["Remove"].cget("state") == "disabled"
    assert dialog._buttons["Details"].cget("state") == "normal"


# -- Importing ----------------------------------------------------------------


def test_importing_a_list_adds_a_ticked_user_library(make, config, tmp_path):
    dialog = make()

    new_id = dialog.import_file(_write_list(tmp_path, "mine.txt"))

    assert new_id == "mine"
    assert user_library_paths(config.xrom_directory) == [
        config.xrom_directory / "mine.json"
    ]
    row = dialog._rows[_index(dialog, "mine")]
    assert row.enabled and not row.library.shipped
    assert dialog._selected == _index(dialog, "mine")
    dialog._cancel()
    dialog.saved.assert_called_once_with([]), "files changed: the caller re-reads"


def test_a_shipped_id_is_never_replaced(make, config, tmp_path):
    dialog = make()
    with mock.patch.object(messagebox, "showinfo") as info:
        new_id = dialog.import_file(_write_list(tmp_path, "math.txt"))
    assert new_id == "math-2"
    assert 'was added as "math-2"' in info.call_args.args[1]
    assert dialog._rows[_index(dialog, "math")].library.shipped


@pytest.mark.parametrize(
    "answer, expected_files, expected_id",
    [
        (True, ["mine"], "mine"),
        (False, ["mine", "mine-2"], "mine-2"),
        (None, ["mine"], None),
    ],
)
def test_a_user_id_already_taken_is_replaced_or_kept(
    make, config, tmp_path, answer, expected_files, expected_id
):
    dialog = make()
    dialog.import_file(_write_list(tmp_path, "mine.txt"))

    with mock.patch.object(messagebox, "askyesnocancel", return_value=answer):
        new_id = dialog.import_file(_write_list(tmp_path, "mine.txt", "BAZ 12 0\n"))

    assert new_id == expected_id
    stems = sorted(p.stem for p in user_library_paths(config.xrom_directory))
    assert stems == sorted(expected_files)


def test_a_file_that_cannot_be_used_is_reported(make, config, tmp_path):
    dialog = make()
    with mock.patch.object(messagebox, "showerror") as error:
        assert dialog.import_file(_write_list(tmp_path, "bad.txt", "FOO\n")) is None
    assert "expected NAME mm ff" in error.call_args.args[1]
    assert user_library_paths(config.xrom_directory) == []


def test_an_imported_builtin_library_becomes_a_plugin(make, tmp_path):
    source = tmp_path / "copy.json"
    source.write_text(
        '{"format": "dm41-xrom-library", "format_version": 1, "id": "copy", '
        '"title": "Copy", "builtin_on": ["DM41X"], '
        '"modules": [{"xrom": 11, "functions": [{"fn": 1, "name": "FOO"}]}]}'
    )
    dialog = make()
    with mock.patch.object(messagebox, "showinfo") as info:
        assert dialog.import_file(source) == "copy"
    assert "plug-in module instead" in info.call_args.args[1]
    assert not dialog._rows[_index(dialog, "copy")].library.builtin_on


# -- Removing and details ---------------------------------------------------------


def test_removing_a_user_library_deletes_its_file(make, config, tmp_path):
    dialog = make()
    dialog.import_file(_write_list(tmp_path, "mine.txt"))
    dialog._select(_index(dialog, "mine"))
    assert dialog._buttons["Remove"].cget("state") == "normal"

    with mock.patch.object(messagebox, "askyesno", return_value=True) as ask:
        dialog._remove()

    assert "mine.json, will be deleted" in ask.call_args.args[1]
    assert user_library_paths(config.xrom_directory) == []
    assert "mine" not in _ids(dialog)


def test_declining_the_removal_keeps_the_file(make, config, tmp_path):
    dialog = make()
    dialog.import_file(_write_list(tmp_path, "mine.txt"))
    dialog._select(_index(dialog, "mine"))
    with mock.patch.object(messagebox, "askyesno", return_value=False):
        dialog._remove()
    assert len(user_library_paths(config.xrom_directory)) == 1
    assert "mine" in _ids(dialog)


def test_a_missing_entry_is_dropped_without_asking(make):
    dialog = make(entries=[("gone", True)])
    dialog._select(_index(dialog, "gone"))
    dialog._remove()
    assert "gone" not in _ids(dialog)


def test_details_list_every_function(make):
    dialog = make()
    dialog._select(_index(dialog, "math"))
    window = dialog._details()
    assert isinstance(window, LibraryDetailsDialog)
    lines = window.winfo_children()[0].get("1.0", "end").splitlines()
    assert lines[0] == "Math Pac"
    assert any(line.startswith("XROM 01,09  SOLVE ") for line in lines)
    header = next(line for line in lines if line.startswith("XROM 01,00"))
    assert "MATH 1C" in header and header.endswith("section header")
    window.destroy()
