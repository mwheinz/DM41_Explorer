"""Tests for gui/app.py's file-save logic.

There's no broader test_app.py yet covering the GUI end to end (see the
"Testing notes" entry in project memory) -- these tests are narrowly
scoped to the Save State / Save State As bug reported by the user on
2026-08-13: loading a state file, then pulling a fresh state from the
calculator, then hitting "Save State" (not "Save State As...") used to
silently overwrite the originally-loaded file instead of prompting for a
new filename, because nothing reset `self.memory_source` after the fresh
calculator state replaced `self.memory`. `_on_state_received` (used by both
the explicit "Get State from DM41L" action and the startup auto-connect
sequence) now unconditionally resets `self.memory_source` to None, which
makes `save_state_to_file()` fall through to `save_state_as()` -- these tests
pin that behavior down so a future change can't reintroduce the silent
overwrite.

Requires a real Tk display (Xvfb in CI/sandboxes) -- same requirement as
the rest of the project's manual/ad-hoc GUI verification described in
project memory.
"""
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("customtkinter")

from unittest import mock

from config import ProjectConfig
from memory import DM41L, DM41X, DeviceMode, Memory
from gui.app import DM41ExplorerApp
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
def app(prefs_file, tmp_path):
    """A real DM41ExplorerApp. It starts offline, so nothing here needs a
    serial port or a modal dialog.

    Its mode is whatever the preferences default to, which is DM41X --
    see dm41l_app for the other one."""
    instance = DM41ExplorerApp()
    yield instance
    instance.destroy()


@pytest.fixture
def dm41l_prefs(tmp_path, monkeypatch):
    """prefs_file, but with the mode saved as DM41L, so the app comes up
    in that mode through the real config path rather than by having its
    attribute poked afterwards."""
    fake_prefs_path = tmp_path / ".dm41_test_prefs.json"
    fake_prefs_path.write_text(
        json.dumps({"log_directory": str(tmp_path / "logs"), "mode": "DM41L"})
    )
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", fake_prefs_path)
    return fake_prefs_path


@pytest.fixture
def dm41l_app(dm41l_prefs):
    instance = DM41ExplorerApp()
    yield instance
    instance.destroy()


def _sample_state_string():
    """A minimal, validly-formatted state string, built the same way the
    calculator's own MemoryStringCommand response gets turned into a
    Memory object in _on_state_received."""
    return Memory().to_string()


def test_save_after_calculator_state_prompts_instead_of_overwriting(app, tmp_path):
    """The exact bug report: load a file, pull a new state from the
    calculator, hit Save State -- must prompt for a filename (via
    save_state_as), never silently rewrite the originally-loaded file."""
    original_path = tmp_path / "x.dm41"
    Memory().to_file(original_path)
    original_bytes = original_path.read_bytes()

    app._load_state_into_buffer(str(original_path))
    assert app.memory_source == original_path

    # Simulate "Get State from DM41L" (or the equivalent auto-connect path)
    # handing back a fresh state from the calculator.
    app._on_state_received(_sample_state_string())
    assert app.memory_source is None, (
        "receiving a calculator state must clear memory_source so Save "
        "State can't silently target the previously-loaded file"
    )

    with mock.patch.object(app, "save_state_as") as save_as:
        app.save_state_to_file()
    save_as.assert_called_once()

    # And, belt-and-suspenders: confirm the original file genuinely wasn't
    # touched on disk (save_state_as is mocked above specifically so it
    # can't write anything; this catches any future change that adds a
    # write to save_state_to_file() itself).
    assert original_path.read_bytes() == original_bytes


def test_save_as_prompt_writes_new_file_and_updates_source(app, tmp_path):
    """Sanity check of the "prompts for a filename" half: once the user
    picks a path in the Save As dialog, it's written there and becomes the
    new memory_source (so a subsequent plain Save writes back to it)."""
    app._on_state_received(_sample_state_string())
    assert app.memory_source is None

    new_path = tmp_path / "renamed.dm41"
    with mock.patch(
        "gui.app.filedialog.asksaveasfilename", return_value=str(new_path)
    ), mock.patch("gui.app.messagebox.showinfo"):
        app.save_state_as()

    assert new_path.exists()
    assert app.memory_source == new_path
    assert app.memory.modified is False


def test_save_to_already_loaded_file_confirms_then_saves(app, tmp_path):
    """Normal case, for contrast: saving back to a file you just opened
    (no calculator state in between) should NOT fall through to Save As --
    it's expected to write straight back to that same file. But per the
    user's 2026-08-18 report (saw a plain overwrite with no prompt at all
    for this exact sequence), it must still confirm with the user before
    writing over the file on disk."""
    path = tmp_path / "already-open.dm41"
    Memory().to_file(path)

    app._load_state_into_buffer(str(path))
    assert app.memory_source == path

    with mock.patch.object(app, "save_state_as") as save_as, mock.patch(
        "gui.app.messagebox.showinfo"
    ), mock.patch(
        "gui.app.messagebox.askyesno", return_value=True
    ) as confirm:
        app.save_state_to_file()

    confirm.assert_called_once()
    save_as.assert_not_called()
    assert app.memory_source == path


def test_save_to_already_loaded_file_declined_does_not_write(app, tmp_path):
    """Answering "No" to the overwrite confirmation must leave the file on
    disk untouched -- the whole point of asking first."""
    path = tmp_path / "already-open.dm41"
    Memory().to_file(path)
    original_bytes = path.read_bytes()

    app._load_state_into_buffer(str(path))
    app.memory.status_registers.set_flag(0, True)  # make an in-memory change to try to save

    with mock.patch.object(app, "save_state_as") as save_as, mock.patch(
        "gui.app.messagebox.askyesno", return_value=False
    ):
        app.save_state_to_file()

    save_as.assert_not_called()
    assert path.read_bytes() == original_bytes


def test_new_memory_buffer_also_clears_source(app, tmp_path):
    """Starting a fresh, empty buffer is the same kind of "not tied to any
    file" state as a calculator state -- Save should prompt here too."""
    path = tmp_path / "x.dm41"
    Memory().to_file(path)
    app._load_state_into_buffer(str(path))
    assert app.memory_source == path

    with mock.patch("gui.app.messagebox.askyesno", return_value=True):
        app.new_memory_buffer()
    assert app.memory_source is None

    with mock.patch.object(app, "save_state_as") as save_as:
        app.save_state_to_file()
    save_as.assert_called_once()


def test_on_memory_changed_sets_modified_flag(app):
    """Regression test for a bug where `_on_memory_changed()` -- the
    callback wired to every editable tab's `on_change=` (see __init__)
    and called explicitly by `pack_memory()` -- read
    `self.memory.is_modified` as a bare attribute access instead of
    calling it (`self.memory.is_modified()`). Memory.is_modified() is the
    ONLY thing that ever sets Memory._modified True, so the missing
    parens silently meant no edit, anywhere in the app, ever marked the
    state as modified -- every "Discard unsaved changes?" guard below was
    permanently dead and a loaded state could be overwritten with no
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
    "currently loaded state. Continue?"
)


@pytest.fixture
def connected_app(dm41l_app):
    """The app with a (pretend) open serial connection and a command
    engine that accepts every command, so nothing touches a port.

    In DM41L mode, because that is the only mode that can connect at all
    (phase 6): a DM41X has no serial console."""
    dm41l_app.serial.is_connected = True
    with mock.patch.object(dm41l_app.engine, "execute", return_value=True) as execute:
        dm41l_app.sent = execute
        yield dm41l_app


def _send(app, name, *, answer=True):
    """Puts tests/data/<name> in the buffer and presses Send. Returns the
    mocks for the error box and the confirmation, in that order.

    The state is assigned directly, with the DM41X profile, rather than
    opened through _load_state_into_buffer(): since phase 6, opening a
    too-large state in DM41L mode offers a switch to DM41X mode instead
    of loading it, so the load path can no longer produce the situation
    the Send check guards against. These tests are about Send's own
    behaviour given such a state, so they build it directly -- see the
    note in send_state_to_calculator()."""
    app.memory = Memory.from_file(DATA_DIR / name, profile=DM41X)
    app.memory_source = DATA_DIR / name
    with mock.patch("gui.app.messagebox.showerror") as error, mock.patch(
        "gui.app.messagebox.askyesno", return_value=answer
    ) as confirm:
        app.send_state_to_calculator()
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
    confirm.assert_called_once_with("Send State to Calculator", ORIGINAL_CONFIRMATION)
    connected_app.sent.assert_called_once()


def test_send_checks_against_the_model_the_connection_declares(connected_app):
    """The profile comes from the driver: a connection that declares a
    DM41X would take the state the DM41L refused."""
    connected_app.serial.profile = DM41X

    error, confirm = _send(connected_app, "dm41x_manyfiles.dm41")

    error.assert_not_called()
    confirm.assert_called_once_with("Send State to Calculator", ORIGINAL_CONFIRMATION)
    connected_app.sent.assert_called_once()


def test_send_when_not_connected_does_not_check_anything(dm41l_app):
    """In DM41L mode, where Send is available at all: not being connected
    is what stops it, before any checking. (In DM41X mode the mode guard
    fires first instead -- see
    test_the_serial_actions_refuse_in_dm41x_mode.)"""
    dm41l_app.memory = Memory.from_file(
        DATA_DIR / "dm41x_manyfiles.dm41", profile=DM41X
    )
    with mock.patch("gui.app.messagebox.showwarning") as warn, mock.patch(
        "gui.app.check_profile_fit"
    ) as check:
        dm41l_app.send_state_to_calculator()

    warn.assert_called_once()
    check.assert_not_called()


# -- Opening a DM41X state (plan, phase 3 steps 1, 4 and 5) -------------------


def _tree_rows(tree):
    return [tree.item(iid, "values") for iid in tree.get_children()]


def test_a_state_file_opens_with_the_modes_profile(app):
    """Phase 3 opened every file with the DM41X profile; phase 6 replaced
    that with the mode's profile. The default mode is DM41X, so this
    fixture still lands on DM41X -- but now because of the mode."""
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    assert app.mode is DeviceMode.DM41X
    assert app.memory.profile is DM41X


def test_a_dm41l_state_opens_with_the_dm41l_profile_in_dm41l_mode(dm41l_app):
    """The whole point of DM41L mode: a DM41L owner's own state is shown
    as a DM41L's, not padded out to the DM41X's memory map."""
    dm41l_app._load_state_into_buffer(str(DATA_DIR / "lander.dm41"))

    assert dm41l_app.memory.profile is DM41L
    assert xm_total_registers(dm41l_app.memory.profile) == 362


def test_a_dm41l_state_from_the_calculator_keeps_the_dm41l_profile(app):
    app._on_state_received(Memory().to_string())

    assert app.memory.profile is DM41L


def test_manyfiles_lists_all_its_files_across_three_regions(app):
    with mock.patch("gui.app.messagebox.showerror") as error:
        app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    error.assert_not_called()
    app.xm_files_tab.render(app.memory)  # tabs render lazily, when shown
    rows = _tree_rows(app.xm_files_tab._tree)
    assert len(rows) == 59
    assert app.xm_files_tab._header_label.cget("text") == "Extended-memory files: 59"
    assert "XMA16." in [row[0] for row in rows]


def test_overview_reports_five_registers_free_on_manyfiles(app):
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

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
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
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
        app.load_state_from_file()

    types = dict(ask.call_args.kwargs["filetypes"])
    assert set(types["DM41 memory state"]) == {"*.dm41", "*.d41"}


@pytest.mark.parametrize(
    "name, extension", [("xrom.d41", ".d41"), ("6x-xm.dm41", ".dm41")]
)
def test_save_as_keeps_the_files_own_extension(app, name, extension):
    app._load_state_into_buffer(str(DATA_DIR / name))

    with mock.patch("gui.app.filedialog.asksaveasfilename", return_value="") as ask:
        app.save_state_as()

    assert ask.call_args.kwargs["defaultextension"] == extension


def test_save_as_of_a_new_buffer_follows_the_mode(app, dm41l_app):
    """A buffer with no file of its own gets the extension the current
    mode's calculator writes (plan, phase 6). Before modes this was always
    ".dm41"."""
    with mock.patch("gui.app.filedialog.asksaveasfilename", return_value="") as ask:
        app.save_state_as()
    assert ask.call_args.kwargs["defaultextension"] == ".d41", "DM41X mode"

    with mock.patch("gui.app.filedialog.asksaveasfilename", return_value="") as ask:
        dm41l_app.save_state_as()
    assert ask.call_args.kwargs["defaultextension"] == ".dm41", "DM41L mode"


def test_a_d41_file_saves_back_identically(app, tmp_path):
    text = (DATA_DIR / "dm41x_xrom_keys.d41").read_text()
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_xrom_keys.d41"))
    target = tmp_path / "copy.d41"

    with mock.patch(
        "gui.app.filedialog.asksaveasfilename", return_value=str(target)
    ), mock.patch("gui.app.messagebox.showinfo"):
        app.save_state_as()

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
    app._load_state_into_buffer(str(DATA_DIR / name))

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
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_retpfl_after.d41"))
    app.xm_files_tab.render(app.memory)

    rows = {row[0]: row for row in _tree_rows(app.xm_files_tab._tree)}

    assert len(rows) == 22
    assert rows["XM0"][1] == "@ (type 4)"
    assert rows["XM2"][1] == "@ (type 6)"
    assert rows["XM0"][4] == "type 4: 8 registers (not decoded)"
    assert rows["XM3"][1] == "Data"


# -- Tabs behave on DM41X data (plan, phase 3 step 7) ------------------------


def test_editing_a_data_file_in_the_third_region_keeps_every_other_file(app):
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
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
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_base.d41"))
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


def test_the_app_starts_offline_and_never_touches_the_serial_port(
    prefs_file, monkeypatch
):
    """Nothing connects at launch: no port is listed, opened or prompted
    for, however long the window has been up. Connecting is the user's
    choice (Connect > Connect / Reconnect...)."""
    import time

    from engine.serial_manager import SerialManager
    from gui import app as app_module

    calls = []
    monkeypatch.setattr(
        SerialManager, "get_available_ports", lambda self: calls.append("ports") or []
    )
    monkeypatch.setattr(
        SerialManager,
        "connect",
        lambda self, *a, **k: calls.append("connect") or (False, "no"),
    )
    monkeypatch.setattr(
        app_module,
        "PortSelectionDialog",
        lambda *a, **k: calls.append("dialog"),
    )
    instance = DM41ExplorerApp()
    try:
        time.sleep(0.4)  # longer than the old 100 ms start-up delay
        instance.update()
        assert calls == []
        assert not instance.serial.is_connected
        # Empty, not "Not connected": this app is in DM41X mode, where
        # there is nothing to connect to (see the idle-status tests).
        assert instance._status_label.cget("text") == ""
    finally:
        instance.destroy()


# -- Connect dialog: never opened from inside the menu command ---------------


def test_connect_menu_command_returns_before_the_dialog_blocks(dm41l_app):
    """The Connect dialog waits for itself to close, which hangs the app on
    macOS when started from inside a menu command (the dialog can't be
    clicked and the app shows the beachball). So show_connect_dialog()
    must return first and open the dialog from the normal event loop.

    In DM41L mode: since phase 6, DM41X mode refuses the action outright
    and never reaches the dialog."""
    with mock.patch.object(dm41l_app, "_prompt_for_port") as prompt:
        dm41l_app.show_connect_dialog()
        prompt.assert_not_called()
        deadline = time.time() + 2
        while not prompt.called and time.time() < deadline:
            dm41l_app.update()
            time.sleep(0.01)
        prompt.assert_called_once_with(None)


# -- Explicit DM41L/DM41X modes (plan, phase 6 -- GitHub issue #43) ----------
#
# The mode is the single source of truth for the active profile. Changing it
# erases the open state, and an auto-switch offered when a state will not fit
# the current mode lasts for this session only. The core decision logic is
# tested without a window in test_device_mode.py; these cover the
# application's own sequence.


def _yes():
    return mock.patch("gui.app.messagebox.askyesno", return_value=True)


def _no():
    return mock.patch("gui.app.messagebox.askyesno", return_value=False)


def test_the_mode_comes_from_the_saved_preference(app, dm41l_app):
    assert app.mode is DeviceMode.DM41X
    assert app.profile is DM41X
    assert dm41l_app.mode is DeviceMode.DM41L
    assert dm41l_app.profile is DM41L


def test_a_new_buffer_uses_the_modes_profile(dm41l_app):
    """Before phase 6 a new buffer was always a DM41L-profile memory, even
    when a DM41X state had just been open."""
    assert dm41l_app.memory.profile is DM41L
    with _yes():
        dm41l_app.new_memory_buffer()
    assert dm41l_app.memory.profile is DM41L


def test_switching_mode_starts_an_empty_state(dm41l_app):
    dm41l_app._load_state_into_buffer(str(DATA_DIR / "keyassigns.dm41"))
    assert dm41l_app.memory.programs.list_programs(), "the fixture has programs"
    assert dm41l_app.memory_source is not None

    with _yes():
        assert dm41l_app.set_mode(DeviceMode.DM41X, persist=True) is True

    assert dm41l_app.mode is DeviceMode.DM41X
    assert dm41l_app.memory.profile is DM41X
    assert dm41l_app.memory_source is None
    assert not dm41l_app.memory.programs.list_programs(), "the state was erased"
    assert dm41l_app.memory.to_string() == Memory(profile=DM41X).to_string()


def test_switching_mode_erases_in_both_directions(app):
    """Mike, 2026-10-09: one rule either way. Widening is lossless, but
    preserving the state would mean rebuilding the Memory through
    to_string()/from_string(), which phase 6 deliberately avoids."""
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
    assert app.memory.extended_memory.list_files()

    with _yes():
        assert app.set_mode(DeviceMode.DM41L, persist=True) is True

    assert app.mode is DeviceMode.DM41L
    assert app.memory.profile is DM41L
    assert not app.memory.extended_memory.list_files(), "the state was erased"


def test_switching_to_the_same_mode_does_nothing(app):
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
    source = app.memory_source

    with mock.patch("gui.app.messagebox.askyesno") as ask:
        assert app.set_mode(DeviceMode.DM41X, persist=True) is True

    ask.assert_not_called(), "no confirmation for a no-op"
    assert app.memory_source == source, "the open state is untouched"


def test_declining_the_switch_confirmation_changes_nothing(app):
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
    source = app.memory_source

    with _no():
        assert app.set_mode(DeviceMode.DM41L, persist=True) is False

    assert app.mode is DeviceMode.DM41X
    assert app.memory_source == source
    assert app.memory.extended_memory.list_files(), "the state is still there"


def test_the_discard_guard_runs_before_the_switch_confirmation(app):
    """An unsaved buffer gets the usual discard prompt first, and
    declining it stops the switch before the mode question is even
    asked."""
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
    app._on_memory_changed()
    assert app.memory.modified

    with mock.patch("gui.app.messagebox.askyesno", return_value=False) as ask:
        assert app.set_mode(DeviceMode.DM41L, persist=True) is False

    assert ask.call_count == 1, "stopped at the discard guard"
    assert "Discard unsaved changes" in ask.call_args.args[1]
    assert app.mode is DeviceMode.DM41X


def test_the_switch_confirmation_names_the_consequence(app):
    with mock.patch("gui.app.messagebox.askyesno", return_value=False) as ask:
        app.set_mode(DeviceMode.DM41L, persist=True)

    message = ask.call_args.args[1]
    assert "starts a new, empty memory state" in message
    assert "export" in message, "says how to move data between models"


def test_switching_mode_persists_only_when_asked(app, prefs_file):
    with _yes():
        app.set_mode(DeviceMode.DM41L, persist=True)
    assert json.loads(prefs_file.read_text())["mode"] == "DM41L"
    assert app.config_store.mode is DeviceMode.DM41L
    assert not app.mode_is_temporary

    with _yes():
        app.set_mode(DeviceMode.DM41X, persist=False)
    assert json.loads(prefs_file.read_text())["mode"] == "DM41L", "not written"
    assert app.mode is DeviceMode.DM41X
    assert app.mode_is_temporary


def test_the_status_bar_shows_the_mode_and_marks_an_override(app):
    assert app._mode_label.cget("text") == "DM41X mode"

    with _yes():
        app.set_mode(DeviceMode.DM41L, persist=True)
    assert app._mode_label.cget("text") == "DM41L mode"

    with _yes():
        app.set_mode(DeviceMode.DM41X, persist=False)
    assert app._mode_label.cget("text") == "DM41X mode (this session)"


# -- Auto-switch when a state will not fit the mode --------------------------


def test_a_too_large_state_offers_dm41x_mode_and_loads_on_yes(dm41l_app):
    with _yes() as ask:
        dm41l_app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    message = ask.call_args.args[1]
    assert "does not fit a DM41L" in message
    assert "XM files use memory a DM41L does not have" in message
    assert dm41l_app.mode is DeviceMode.DM41X
    assert dm41l_app.memory.profile is DM41X
    assert len(dm41l_app.memory.extended_memory.list_files()) == 59


def test_an_accepted_auto_switch_is_not_persisted(dm41l_app, dm41l_prefs):
    """Mike, 2026-10-09: someone who owns one calculator will rarely want
    the other mode permanently, so the offer is session-only."""
    with _yes():
        dm41l_app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    assert dm41l_app.mode is DeviceMode.DM41X
    assert dm41l_app.config_store.mode is DeviceMode.DM41L
    assert dm41l_app.mode_is_temporary
    assert json.loads(dm41l_prefs.read_text())["mode"] == "DM41L"


def test_declining_the_offer_aborts_the_load(dm41l_app):
    """The mode and the previously open state are both untouched."""
    dm41l_app._load_state_into_buffer(str(DATA_DIR / "lander.dm41"))
    before = dm41l_app.memory.to_string()

    with _no():
        dm41l_app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    assert dm41l_app.mode is DeviceMode.DM41L
    assert dm41l_app.memory_source == DATA_DIR / "lander.dm41"
    assert dm41l_app.memory.to_string() == before


def test_a_state_that_fits_loads_without_any_offer(dm41l_app):
    with mock.patch("gui.app.messagebox.askyesno") as ask:
        dm41l_app._load_state_into_buffer(str(DATA_DIR / "lander.dm41"))

    ask.assert_not_called()
    assert dm41l_app.mode is DeviceMode.DM41L


def test_stale_data_above_region_two_loads_in_dm41l_mode(dm41l_app):
    """dm41x_retpfl_before.d41 has junk above 0x300 that belongs to no XM
    file. That is a warning reported at Send, not a reason to refuse the
    load or to offer a switch (plan, phase 6)."""
    with mock.patch("gui.app.messagebox.askyesno") as ask:
        dm41l_app._load_state_into_buffer(str(DATA_DIR / "dm41x_retpfl_before.d41"))

    ask.assert_not_called()
    assert dm41l_app.mode is DeviceMode.DM41L
    assert dm41l_app.memory_source is not None


def test_dm41x_only_xroms_load_in_dm41l_mode_without_an_offer(dm41l_app):
    """xrom.d41 calls 18 functions a DM41L lacks. Confirmed 2026-10-09:
    those warn at upload only -- the user may mean to retype the step on
    the calculator."""
    with mock.patch("gui.app.messagebox.askyesno") as ask:
        dm41l_app._load_state_into_buffer(str(DATA_DIR / "xrom.d41"))

    ask.assert_not_called()
    assert dm41l_app.memory_source is not None


# -- Serial is gated by the mode ---------------------------------------------


def _connect_entries(instance):
    menu = instance._connect_menu
    return [
        menu.entrycget(index, "state") or "normal"
        for index in instance._serial_menu_indices
    ]


def test_the_connect_menu_is_enabled_in_dm41l_mode(dm41l_app):
    assert dm41l_app._serial_menu_indices, "there are serial entries to gate"
    assert set(_connect_entries(dm41l_app)) == {"normal"}


def test_the_connect_menu_is_disabled_in_dm41x_mode(app):
    """Disabled, not hidden (Mike, 2026-10-09)."""
    assert set(_connect_entries(app)) == {"disabled"}


def test_the_menu_gating_follows_a_mode_change(app):
    with _yes():
        app.set_mode(DeviceMode.DM41L, persist=True)
    assert set(_connect_entries(app)) == {"normal"}

    with _yes():
        app.set_mode(DeviceMode.DM41X, persist=True)
    assert set(_connect_entries(app)) == {"disabled"}


@pytest.mark.parametrize("action", [
    "show_connect_dialog",
    "get_state_from_calculator",
    "send_state_to_calculator",
    "set_calculator_time",
])
def test_the_serial_actions_refuse_in_dm41x_mode(app, action):
    """The keyboard shortcuts never consult the menu's state, so each
    action guards itself as well."""
    with mock.patch("gui.app.messagebox.showinfo") as info, \
            mock.patch.object(app.serial, "connect") as connect:
        getattr(app, action)()

    assert info.called, f"{action} said nothing"
    assert "DM41X Mode" in info.call_args.args[0]
    connect.assert_not_called()


def test_switching_mode_while_connected_warns_and_disconnects(dm41l_app):
    dm41l_app.serial.is_connected = True
    with mock.patch.object(dm41l_app, "disconnect") as disconnect, _yes() as ask:
        assert dm41l_app.set_mode(DeviceMode.DM41X, persist=True) is True

    assert "serial connection will be closed" in ask.call_args.args[1]
    disconnect.assert_called_once()


def test_declining_the_switch_leaves_the_connection_alone(dm41l_app):
    dm41l_app.serial.is_connected = True
    with mock.patch.object(dm41l_app, "disconnect") as disconnect, _no():
        assert dm41l_app.set_mode(DeviceMode.DM41X, persist=True) is False

    disconnect.assert_not_called()


# -- The Preferences dialog reports the mode rather than writing it ----------


def test_preferences_can_change_the_mode(app):
    with _yes():
        app._on_preferences_saved(requested_mode=DeviceMode.DM41L)

    assert app.mode is DeviceMode.DM41L
    assert app.config_store.mode is DeviceMode.DM41L


def test_a_cancelled_mode_change_from_preferences_keeps_the_saved_value(app):
    with _no():
        app._on_preferences_saved(requested_mode=DeviceMode.DM41L)

    assert app.mode is DeviceMode.DM41X
    assert app.config_store.mode is DeviceMode.DM41X


def test_preferences_can_confirm_a_session_override_without_erasing(dm41l_app):
    """After an auto-switch the app is in DM41X mode while DM41L is saved.
    Choosing DM41X in Preferences then means "make that the setting" --
    there is nothing to erase, so it must not prompt."""
    with _yes():
        dm41l_app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))
    assert dm41l_app.mode_is_temporary
    files = len(dm41l_app.memory.extended_memory.list_files())

    with mock.patch("gui.app.messagebox.askyesno") as ask:
        dm41l_app._on_preferences_saved(requested_mode=DeviceMode.DM41X)

    ask.assert_not_called()
    assert dm41l_app.config_store.mode is DeviceMode.DM41X
    assert not dm41l_app.mode_is_temporary
    assert len(dm41l_app.memory.extended_memory.list_files()) == files
    assert dm41l_app._mode_label.cget("text") == "DM41X mode"


def test_preferences_saved_without_a_mode_still_works(app):
    """The callback keeps working for a caller that reports no mode."""
    app._on_preferences_saved()
    assert app.mode is DeviceMode.DM41X



# -- The status bar says nothing about a connection it cannot make ----------


def test_dm41x_mode_starts_with_an_empty_status(app):
    """"Not connected" is only meaningful where connecting is possible
    (Mike, 2026-10-09)."""
    assert app._status_label.cget("text") == ""


def test_dm41l_mode_still_says_not_connected(dm41l_app):
    assert dm41l_app._status_label.cget("text") == "Not connected"


def test_switching_to_dm41x_leaves_no_stale_not_connected(dm41l_app):
    assert dm41l_app._status_label.cget("text") == "Not connected"

    with _yes():
        dm41l_app.set_mode(DeviceMode.DM41X, persist=True)

    text = dm41l_app._status_label.cget("text")
    assert "Not connected" not in text
    assert "DM41X mode" in text, "it says what just happened instead"


def test_disconnecting_in_dm41l_mode_says_not_connected(dm41l_app):
    dm41l_app.serial.is_connected = True
    dm41l_app._set_status("Connected to /dev/ttyUSB0")

    with mock.patch.object(dm41l_app.serial, "disconnect"):
        dm41l_app.disconnect()

    assert dm41l_app._status_label.cget("text") == "Not connected"


def test_a_serial_failure_in_dm41x_mode_leaves_the_status_empty(app):
    """Not reachable through the UI -- DM41X mode refuses to connect at
    all -- but the error callbacks are shared, so they must not print a
    connection state in a mode that has no connection."""
    app._set_status("")
    # _on_command_error posts its dialog with after(0, ...), so update()
    # below would open a real modal box and block forever.
    with mock.patch("gui.app.messagebox.showerror"):
        app._on_command_error("something went wrong")
        app.update()

    assert app._status_label.cget("text") == ""


# -- The mode limits extended memory (issue #43, DM41L mode item 2) ----------


def test_dm41l_mode_limits_extended_memory_to_the_dm41ls_size(dm41l_app, app):
    """The limit is not a separate check: the mode's profile has only two
    XM regions, so extended memory refuses what will not fit in them. The
    same file goes in without complaint in DM41X mode."""
    from memory import DM41MemoryError

    assert xm_total_registers(dm41l_app.memory.profile) == 362
    with pytest.raises(DM41MemoryError, match="Not enough free space"):
        dm41l_app.memory.extended_memory.add_file(
            "BIG", 2, numbers=[1.0] * 400
        )

    assert xm_total_registers(app.memory.profile) == 600
    app.memory.extended_memory.add_file("BIG", 2, numbers=[1.0] * 400)
    assert [f.name.strip() for f in app.memory.extended_memory.list_files()] == ["BIG"]


def test_a_dm41x_state_file_that_fits_is_usable_in_dm41l_mode(dm41l_app):
    """Issue #43, DM41L mode item 4: a .d41 is just a state file, and one
    that fits a DM41L opens, edits and saves in DM41L mode."""
    dm41l_app._load_state_into_buffer(str(DATA_DIR / "dm41x_retpfl_before.d41"))

    assert dm41l_app.mode is DeviceMode.DM41L
    assert dm41l_app.memory.profile is DM41L
    before = len(dm41l_app.memory.extended_memory.list_files())
    assert before, "the fixture has XM files"

    dm41l_app.memory.extended_memory.add_file("NEW", 2, numbers=[1.0, 2.0])
    assert len(dm41l_app.memory.extended_memory.list_files()) == before + 1


def test_the_mode_is_shown_before_the_connection_status(app):
    """Mike, 2026-10-09. The mode is the standing fact about the session;
    the status label beside it is running commentary. Order is the pack
    order within the status bar, left to right."""
    bar = app._mode_label.master
    left = [w for w in bar.winfo_children() if w.pack_info().get("side") == "left"]
    assert left.index(app._mode_label) < left.index(app._status_label)
    assert left.index(app._status_label) < left.index(app._modified_label)
