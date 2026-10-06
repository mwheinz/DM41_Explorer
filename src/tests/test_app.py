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
from pathlib import Path

import pytest

pytest.importorskip("customtkinter")

from unittest import mock

from config import ProjectConfig
from memory import DM41L, DM41X, Memory
from gui.app import DM41LExplorerApp
from gui.overview_tab import xm_total_registers


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


# -- Send: the check against the connected calculator's model ---------------
#
# Phase 2 of docs/dm41x_explorer_plan.md. The serial connection declares its
# model (SerialManager.profile, the DM41L); Send refuses what that model
# cannot hold and asks before sending what it cannot run.

DATA_DIR = Path(__file__).parent / "data"
ORIGINAL_CONFIRMATION = (
    "This will overwrite the calculator's current memory with the "
    "currently loaded dump. Continue?"
)


@pytest.fixture
def connected_app(app):
    """The app with a (pretend) open serial connection and a command
    engine that accepts every command, so nothing touches a port."""
    app.serial.is_connected = True
    with mock.patch.object(app.engine, "execute", return_value=True) as execute:
        app.sent = execute
        yield app


def _send(app, name, *, answer=True):
    """Opens tests/data/<name> and presses Send. Returns the mocks for the
    error box and the confirmation, in that order."""
    app._load_dump_into_buffer(str(DATA_DIR / name))
    with mock.patch("gui.app.messagebox.showerror") as error, mock.patch(
        "gui.app.messagebox.askyesno", return_value=answer
    ) as confirm:
        app.send_dump_to_calculator()
    return error, confirm


def test_the_serial_connection_declares_the_dm41l(app):
    assert app.serial.profile is DM41L


def test_send_refuses_xm_the_dm41l_does_not_have(connected_app):
    error, confirm = _send(connected_app, "dm41x_manyfiles.dm41")

    error.assert_called_once()
    title, message = error.call_args.args
    assert title == "Cannot Send to DM41L"
    assert "23 of 59 XM files" in message
    assert "'XMA16.'" in message
    assert message.endswith("Nothing was sent.")
    confirm.assert_not_called()
    connected_app.sent.assert_not_called()


def test_send_lists_what_a_dm41l_cannot_run_and_proceeds_on_yes(connected_app):
    error, confirm = _send(connected_app, "xrom.d41", answer=True)

    error.assert_not_called()
    confirm.assert_called_once()
    _title, message = confirm.call_args.args
    assert message.startswith(ORIGINAL_CONFIRMATION[: -len(" Continue?")])
    assert "These will not work on a DM41L:" in message
    assert '- Program "DM41X", step 3: X<I>Y is not built into a DM41L' in message
    # 18 warnings; the dialog shows 12 and counts the rest.
    assert "- Program \"DM41X\", step 14:" in message
    assert "step 15" not in message
    assert "...and 6 more." in message
    assert message.endswith("Send it anyway?")
    connected_app.sent.assert_called_once()


def test_send_stops_when_the_warnings_are_declined(connected_app):
    error, confirm = _send(connected_app, "xrom.d41", answer=False)

    confirm.assert_called_once()
    connected_app.sent.assert_not_called()


def test_send_lists_key_assignments_a_dm41l_cannot_run(connected_app):
    _error, confirm = _send(connected_app, "dm41x_xrom_keys.d41")

    _title, message = confirm.call_args.args
    assert "- Key 12 (unshifted): LKAOFF is not built into a DM41L" in message
    assert "- Key 11 (unshifted): LKAON is not built into a DM41L" in message


def test_send_of_a_state_that_fits_asks_the_usual_question(connected_app):
    error, confirm = _send(connected_app, "6x-xm.dm41")

    error.assert_not_called()
    confirm.assert_called_once_with("Send Dump to Calculator", ORIGINAL_CONFIRMATION)
    connected_app.sent.assert_called_once()


def test_send_checks_against_the_model_the_connection_declares(connected_app):
    """The profile comes from the driver: a connection that declares a
    DM41X would take the state the DM41L refused."""
    connected_app.serial.profile = DM41X

    error, confirm = _send(connected_app, "dm41x_manyfiles.dm41")

    error.assert_not_called()
    confirm.assert_called_once_with("Send Dump to Calculator", ORIGINAL_CONFIRMATION)
    connected_app.sent.assert_called_once()


def test_send_when_not_connected_does_not_check_anything(app):
    app._load_dump_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
    with mock.patch("gui.app.messagebox.showwarning") as warn, mock.patch(
        "gui.app.check_profile_fit"
    ) as check:
        app.send_dump_to_calculator()

    warn.assert_called_once()
    check.assert_not_called()


# -- Opening a DM41X state (plan, phase 3 steps 1, 4 and 5) -------------------


def _tree_rows(tree):
    return [tree.item(iid, "values") for iid in tree.get_children()]


def test_a_state_file_opens_with_the_dm41x_profile(app):
    app._load_dump_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    assert app.memory.profile is DM41X


def test_a_dm41l_dump_from_the_calculator_keeps_the_dm41l_profile(app):
    app._on_dump_received(Memory().to_string())

    assert app.memory.profile is DM41L


def test_manyfiles_lists_all_its_files_across_three_regions(app):
    with mock.patch("gui.app.messagebox.showerror") as error:
        app._load_dump_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    error.assert_not_called()
    app.xm_files_tab.render(app.memory)  # tabs render lazily, when shown
    rows = _tree_rows(app.xm_files_tab._tree)
    assert len(rows) == 59
    assert app.xm_files_tab._header_label.cget("text") == "Extended-memory files: 59"
    assert "XMA16." in [row[0] for row in rows]


def test_overview_reports_five_registers_free_on_manyfiles(app):
    app._load_dump_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    files, used, free = app.overview_tab._xm_summary_texts()

    assert files == "59"
    assert used == "595/600 registers (99%)"
    assert free == "5/600 registers (1%)"


def test_overview_total_follows_the_profile(app):
    assert xm_total_registers(DM41L) == 362  # real DM41L, EMDIR
    assert xm_total_registers(DM41X) == 600  # real DM41X, plan S8

    app.overview_tab.render(Memory())  # a DM41L-profile memory
    assert app.overview_tab._xm_summary_texts()[2] == "362/362 registers (100%)"
    app.overview_tab.render(Memory(profile=DM41X))
    assert app.overview_tab._xm_summary_texts()[2] == "600/600 registers (100%)"


def test_hex_view_runs_to_the_end_of_the_dm41x_map(app):
    app._load_dump_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
    app.hex_view_tab.render(app.memory)

    rows = _tree_rows(app.hex_view_tab._tree)

    assert len(rows) == 0x3F0
    assert (rows[0][0], rows[-1][0]) == ("0x000", "0x3ef")
    assert rows[0x3EF][3] == "XM"
    assert rows[0x2F0][3] == "Inaccessible"  # the gap between XM #1 and #2
    assert "0x000-0x3ef (1008 registers)" in app.hex_view_tab._header_label.cget("text")


def test_hex_view_of_a_dm41l_memory_still_ends_at_0x2ef(app):
    app.hex_view_tab.render(Memory())

    rows = _tree_rows(app.hex_view_tab._tree)

    assert len(rows) == 0x2F0
    assert rows[-1][0] == "0x2ef"


def test_open_dialog_offers_both_extensions(app):
    with mock.patch("gui.app.filedialog.askopenfilename", return_value="") as ask:
        app.load_dump_from_file()

    types = dict(ask.call_args.kwargs["filetypes"])
    assert set(types["DM41 memory state"]) == {"*.dm41", "*.d41"}


@pytest.mark.parametrize(
    "name, extension", [("xrom.d41", ".d41"), ("6x-xm.dm41", ".dm41")]
)
def test_save_as_keeps_the_files_own_extension(app, name, extension):
    app._load_dump_into_buffer(str(DATA_DIR / name))

    with mock.patch("gui.app.filedialog.asksaveasfilename", return_value="") as ask:
        app.save_dump_as()

    assert ask.call_args.kwargs["defaultextension"] == extension


def test_save_as_of_a_new_buffer_defaults_to_dm41(app):
    with mock.patch("gui.app.filedialog.asksaveasfilename", return_value="") as ask:
        app.save_dump_as()

    assert ask.call_args.kwargs["defaultextension"] == ".dm41"


def test_a_d41_file_saves_back_identically(app, tmp_path):
    text = (DATA_DIR / "dm41x_xrom_keys.d41").read_text()
    app._load_dump_into_buffer(str(DATA_DIR / "dm41x_xrom_keys.d41"))
    target = tmp_path / "copy.d41"

    with mock.patch(
        "gui.app.filedialog.asksaveasfilename", return_value=str(target)
    ), mock.patch("gui.app.messagebox.showinfo"):
        app.save_dump_as()

    assert " ".join(target.read_text().split()) == " ".join(text.split())


@pytest.mark.parametrize(
    "name",
    [
        "dm41x_manyfiles.dm41",
        "xrom.d41",
        "dm41x_xrom_keys.d41",
        "dm41xn.dm41",
        "dm41x_retpfl_before.d41",
        "dm41x_retpfl_after.d41",
        "lkaoff3.d41",
    ],
)
def test_every_tab_renders_every_dm41x_sample(app, name):
    """Smoke test (plan, phase 3 step 7): no tab raises on a state opened
    the way the app now opens every file."""
    app._load_dump_into_buffer(str(DATA_DIR / name))

    for tab in (
        app.overview_tab,
        app.flags_tab,
        app.data_registers_tab,
        app.hex_view_tab,
        app.program_tab,
        app.key_assignments_tab,
        app.xm_files_tab,
        app.alarms_tab,
    ):
        tab.render(app.memory)


def test_xm_tab_shows_retyped_files_as_at_with_their_type(app):
    app._load_dump_into_buffer(str(DATA_DIR / "dm41x_retpfl_after.d41"))
    app.xm_files_tab.render(app.memory)

    rows = {row[0]: row for row in _tree_rows(app.xm_files_tab._tree)}

    assert len(rows) == 22
    assert rows["XM0"][1] == "@ (type 4)"
    assert rows["XM2"][1] == "@ (type 6)"
    assert rows["XM0"][4] == "type 4: 8 registers (not decoded)"
    assert rows["XM3"][1] == "Data"


# -- Tabs behave on DM41X data (plan, phase 3 step 7) ------------------------


def test_editing_a_data_file_in_the_third_region_keeps_every_other_file(app):
    app._load_dump_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
    app.xm_files_tab.render(app.memory)  # tabs render lazily, when shown
    xm = app.memory.extended_memory

    def contents():
        out = {}
        for f in xm.list_files():
            if f.file_type == f.TYPE_DATA:
                out[f.name] = f.get_data_lines()
            elif f.file_type == f.TYPE_ASCII:
                out[f.name] = f.get_records()
            else:
                out[f.name] = f.get_instruction_bytes()
        return out

    before = contents()
    target = next(f for f in xm.list_files() if f.name == "XM20.  ")
    assert target.segments[0][0] > 0x300  # entirely in the third region

    app.xm_files_tab._save_new_or_edited_file(
        "XM20.",
        target.TYPE_DATA,
        {"data_lines": ["1", "2", "3", "4", "5", "6", "7", "8"]},
        replacing_addr=target.header_addr,
        replacing_file=target,
    )

    after = contents()
    assert len(after) == 59
    assert after["XM20.  "] == ["1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0"]
    assert {k: v for k, v in after.items() if k != "XM20.  "} == {
        k: v for k, v in before.items() if k != "XM20.  "
    }
    assert app.overview_tab._xm_summary_texts()[2] == "5/600 registers (1%)"


def test_flags_tab_shows_and_toggles_flag_31_on_a_dm41x_state(app):
    app._load_dump_into_buffer(str(DATA_DIR / "dm41x_base.d41"))
    flags = app.memory.status_registers
    app.flags_tab.render(app.memory)
    assert flags_label(app, 31) == "31 timer MDY / DMY"
    before = [flags.get_flag(n) for n in range(56)]
    assert app.flags_tab._flag_vars[31].get() == before[31]

    app.flags_tab._flag_vars[31].set(not before[31])  # DMY / MDY
    app.flags_tab._on_flag_toggled(31)

    after = [flags.get_flag(n) for n in range(56)]
    assert after[31] == (not before[31])
    assert [n for n in range(56) if after[n] != before[n]] == [31]
    assert app.memory.modified


def flags_label(app, n):
    return app.flags_tab._body.winfo_children()[n].cget("text")
