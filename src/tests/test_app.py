"""Tests for gui/app.py's file-save logic.

There's no broader test_app.py yet covering the GUI end to end (see the
"Testing notes" entry in project memory) -- these tests are narrowly
scoped to the Save Dump / Save Dump As bug reported by the user on
2026-08-13: loading a dump file, then pulling a fresh dump from the
calculator, then hitting "Save Dump" (not "Save Dump As...") used to
silently overwrite the originally-loaded file instead of prompting for a
new filename, because nothing reset `self.memory_source` after the fresh
calculator dump replaced `self.memory`. `_on_dump_received` (used by both
the explicit "Get Dump from DM41L" action and the startup auto-connect
sequence) now unconditionally resets `self.memory_source` to None, which
makes `save_dump_to_file()` fall through to `save_dump_as()` -- these tests
pin that behavior down so a future change can't reintroduce the silent
overwrite.

Requires a real Tk display (Xvfb in CI/sandboxes) -- same requirement as
the rest of the project's manual/ad-hoc GUI verification described in
project memory.
"""
import json

import pytest

pytest.importorskip("customtkinter")

from unittest import mock

from config import ProjectConfig
from memory import Memory
from gui.app import DM41LExplorerApp


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    """Same isolation trick as test_config.py's fixture -- keep the test
    from reading/writing the real ~/.dm41_test_prefs.json. Also points
    log_directory at a throwaway path so _setup_logging() doesn't write
    into the real home directory during tests."""
    fake_prefs_path = tmp_path / ".dm41_test_prefs.json"
    fake_prefs_path.write_text(json.dumps({"log_directory": str(tmp_path / "logs")}))
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", fake_prefs_path)
    return fake_prefs_path


@pytest.fixture
def app(prefs_file, tmp_path, monkeypatch):
    """A real DM41LExplorerApp, with auto-connect neutered (it opens a
    blocking modal port-selection dialog with nothing there to click it
    away, same gotcha called out in project memory's "Startup performance"
    testing notes)."""
    monkeypatch.setattr(DM41LExplorerApp, "attempt_auto_connect", lambda self: None)
    instance = DM41LExplorerApp()
    yield instance
    instance.destroy()


def _sample_dump_string():
    """A minimal, validly-formatted dump string, built the same way the
    calculator's own MemoryStringCommand response gets turned into a
    Memory object in _on_dump_received."""
    return Memory().to_string()


def test_save_after_calculator_dump_prompts_instead_of_overwriting(app, tmp_path):
    """The exact bug report: load a file, pull a new dump from the
    calculator, hit Save Dump -- must prompt for a filename (via
    save_dump_as), never silently rewrite the originally-loaded file."""
    original_path = tmp_path / "x.dm41"
    Memory().to_file(original_path)
    original_bytes = original_path.read_bytes()

    app._load_dump_into_buffer(str(original_path))
    assert app.memory_source == original_path

    # Simulate "Get Dump from DM41L" (or the equivalent auto-connect path)
    # handing back a fresh dump from the calculator.
    app._on_dump_received(_sample_dump_string())
    assert app.memory_source is None, (
        "receiving a calculator dump must clear memory_source so Save "
        "Dump can't silently target the previously-loaded file"
    )

    with mock.patch.object(app, "save_dump_as") as save_as:
        app.save_dump_to_file()
    save_as.assert_called_once()

    # And, belt-and-suspenders: confirm the original file genuinely wasn't
    # touched on disk (save_dump_as is mocked above specifically so it
    # can't write anything; this catches any future change that adds a
    # write to save_dump_to_file() itself).
    assert original_path.read_bytes() == original_bytes


def test_save_as_prompt_writes_new_file_and_updates_source(app, tmp_path):
    """Sanity check of the "prompts for a filename" half: once the user
    picks a path in the Save As dialog, it's written there and becomes the
    new memory_source (so a subsequent plain Save writes back to it)."""
    app._on_dump_received(_sample_dump_string())
    assert app.memory_source is None

    new_path = tmp_path / "renamed.dm41"
    with mock.patch(
        "gui.app.filedialog.asksaveasfilename", return_value=str(new_path)
    ), mock.patch("gui.app.messagebox.showinfo"):
        app.save_dump_as()

    assert new_path.exists()
    assert app.memory_source == new_path
    assert app.memory.modified is False


def test_save_to_already_loaded_file_confirms_then_saves(app, tmp_path):
    """Normal case, for contrast: saving back to a file you just opened
    (no calculator dump in between) should NOT fall through to Save As --
    it's expected to write straight back to that same file. But per the
    user's 2026-08-18 report (saw a plain overwrite with no prompt at all
    for this exact sequence), it must still confirm with the user before
    writing over the file on disk."""
    path = tmp_path / "already-open.dm41"
    Memory().to_file(path)

    app._load_dump_into_buffer(str(path))
    assert app.memory_source == path

    with mock.patch.object(app, "save_dump_as") as save_as, mock.patch(
        "gui.app.messagebox.showinfo"
    ), mock.patch(
        "gui.app.messagebox.askyesno", return_value=True
    ) as confirm:
        app.save_dump_to_file()

    confirm.assert_called_once()
    save_as.assert_not_called()
    assert app.memory_source == path


def test_save_to_already_loaded_file_declined_does_not_write(app, tmp_path):
    """Answering "No" to the overwrite confirmation must leave the file on
    disk untouched -- the whole point of asking first."""
    path = tmp_path / "already-open.dm41"
    Memory().to_file(path)
    original_bytes = path.read_bytes()

    app._load_dump_into_buffer(str(path))
    app.memory.status_registers.set_flag(0, True)  # make an in-memory change to try to save

    with mock.patch.object(app, "save_dump_as") as save_as, mock.patch(
        "gui.app.messagebox.askyesno", return_value=False
    ):
        app.save_dump_to_file()

    save_as.assert_not_called()
    assert path.read_bytes() == original_bytes


def test_new_memory_buffer_also_clears_source(app, tmp_path):
    """Starting a fresh, empty buffer is the same kind of "not tied to any
    file" state as a calculator dump -- Save should prompt here too."""
    path = tmp_path / "x.dm41"
    Memory().to_file(path)
    app._load_dump_into_buffer(str(path))
    assert app.memory_source == path

    with mock.patch("gui.app.messagebox.askyesno", return_value=True):
        app.new_memory_buffer()
    assert app.memory_source is None

    with mock.patch.object(app, "save_dump_as") as save_as:
        app.save_dump_to_file()
    save_as.assert_called_once()


def test_on_memory_changed_sets_modified_flag(app):
    """Regression test for a bug where `_on_memory_changed()` -- the
    callback wired to every editable tab's `on_change=` (see __init__)
    and called explicitly by `pack_memory()` -- read
    `self.memory.is_modified` as a bare attribute access instead of
    calling it (`self.memory.is_modified()`). Memory.is_modified() is the
    ONLY thing that ever sets Memory._modified True, so the missing
    parens silently meant no edit, anywhere in the app, ever marked the
    dump as modified -- every "Discard unsaved changes?" guard below was
    permanently dead and a loaded dump could be overwritten with no
    warning at all."""
    assert app.memory.modified is False
    app._on_memory_changed()
    assert app.memory.modified is True


def test_new_memory_buffer_prompts_after_tab_edit(app):
    """An edit made through a tab's on_change callback (simulated here by
    calling _on_memory_changed() directly, the same hook every editable
    tab invokes) must be enough to trigger the "Discard unsaved changes?"
    confirmation -- not just an edit made by directly poking
    memory.status_registers like the save-path tests above do."""
    app._on_memory_changed()

    with mock.patch(
        "gui.app.messagebox.askyesno", return_value=False
    ) as confirm:
        app.new_memory_buffer()

    confirm.assert_called_once()


def test_mnemonics_reference_opens_once_and_is_reused(app):
    first = app.show_mnemonics_reference()
    assert first.winfo_exists()
    assert app.show_mnemonics_reference() is first
    first.destroy()
    second = app.show_mnemonics_reference()
    assert second is not first
    second.destroy()
