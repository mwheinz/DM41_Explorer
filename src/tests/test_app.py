"""Tests for gui/app.py: saving and loading, the DM41L/DM41X mode, the
serial actions and their checks, and how each tab shows a state.

Every test shares one DM41ExplorerApp, reset to a fresh, offline state
before each test (`_reset`). Building a Tk root window gets slower with
every one a process has already built -- the cost accumulates inside
Tcl/Tk itself -- so building a new app per test made this file take
minutes. Requires a display (Xvfb in CI).
"""

import contextlib
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("customtkinter")

from unittest import mock

from config import ProjectConfig
from engine.command_engine import CommandEngine
from memory import DM41L, DM41X, DeviceMode, Memory
import gui.app as app_module
from gui.app import DM41ExplorerApp
from memory.mnemonics import resolve, set_active_libraries, xrom_op
from gui.overview_tab import xm_total_registers


def _write_prefs(path, **prefs):
    path.write_text(json.dumps({"log_directory": str(path.parent / "logs"), **prefs}))


@pytest.fixture(autouse=True)
def no_unexpected_questions(monkeypatch):
    """ask_yes_no() opens a real dialog and waits for a click, which would
    hang the run. A test that expects the question patches it itself,
    inside this one."""
    monkeypatch.setattr(
        "gui.app.ask_yes_no",
        mock.Mock(side_effect=AssertionError("unexpected ask_yes_no() question")),
    )


@pytest.fixture(scope="module")
def _shared_app(tmp_path_factory):
    prefs = tmp_path_factory.mktemp("prefs") / ".dm41_test_prefs.json"
    _write_prefs(prefs)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ProjectConfig, "PREFS_FILE", prefs)
        instance = DM41ExplorerApp()
        yield instance
        instance.destroy()


def _reset(app):
    """Puts the shared app back to how a fresh launch with the current
    preferences file would leave it: offline, an empty buffer in the saved
    mode, no dialogs open."""
    app.config_store = ProjectConfig()
    app.config_store.load()
    app.mode = app.config_store.mode
    app._apply_xrom_libraries()
    app.serial.is_connected = False
    app.serial.serial_inst = None
    app.serial.__dict__.pop("profile", None)  # a test may override it
    while app.serial.get_next_error() is not None:
        pass
    app.engine = CommandEngine(app.serial)
    app._closing = False
    dialog = getattr(app, "_mnemonics_reference", None)
    if dialog is not None and dialog.winfo_exists():
        dialog.destroy()
    app._mnemonics_reference = None
    dialog = getattr(app, "_preferences_dialog", None)
    if dialog is not None and dialog.winfo_exists():
        dialog.destroy()
    app._preferences_dialog = None
    app._apply_mode_to_menus()
    app._refresh_mode_indicator()
    app.tabview.set("Overview")
    app._replace_memory(Memory(profile=app.profile))
    app._set_status(app._idle_status())
    app._battery_label.configure(text="")
    app._calc_time_label.configure(text="")
    app._rebuild_recent_files_menu()


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    """A throwaway preferences file (mode left at its default, DM41X)."""
    path = tmp_path / ".dm41_test_prefs.json"
    _write_prefs(path)
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", path)
    return path


@pytest.fixture
def dm41l_prefs(tmp_path, monkeypatch):
    """prefs_file, but with the mode saved as DM41L."""
    path = tmp_path / ".dm41_test_prefs.json"
    _write_prefs(path, mode="DM41L")
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", path)
    return path


@pytest.fixture
def app(_shared_app, prefs_file):
    """The app, freshly reset, in DM41X mode (the default)."""
    _reset(_shared_app)
    return _shared_app


@pytest.fixture
def app_in_mode(_shared_app, tmp_path, monkeypatch):
    """Returns the app, freshly reset, in the mode named ("DM41L" or
    "DM41X") as saved in its preferences file."""

    def reset_in(mode):
        path = tmp_path / ".dm41_test_prefs.json"
        _write_prefs(path, mode=mode)
        monkeypatch.setattr(ProjectConfig, "PREFS_FILE", path)
        _reset(_shared_app)
        return _shared_app

    return reset_in


@pytest.fixture
def dm41l_app(_shared_app, dm41l_prefs):
    """The app, freshly reset, in DM41L mode -- through the preferences
    file, as a real launch would get it."""
    _reset(_shared_app)
    return _shared_app


@pytest.mark.parametrize(
    "replace",
    [
        lambda app: app._on_state_received(Memory().to_string()),  # Get State
        lambda app: app.new_memory_buffer(),
    ],
    ids=["calculator state", "new buffer"],
)
def test_save_after_replacing_a_loaded_file_asks_for_a_new_name(app, tmp_path, replace):
    """Once the buffer no longer comes from the file it was loaded from,
    Save must go through Save As -- never silently overwrite that file."""
    path = tmp_path / "x.dm41"
    Memory().to_file(path)
    original = path.read_bytes()
    app._load_state_into_buffer(str(path))
    assert app.memory_source == path

    with _yes():
        replace(app)
    assert app.memory_source is None

    with mock.patch.object(app, "save_state_as") as save_as:
        app.save_state_to_file()
    save_as.assert_called_once()
    assert path.read_bytes() == original


def test_save_as_writes_the_file_and_makes_it_the_source(app, tmp_path):
    new_path = tmp_path / "renamed.dm41"
    app.memory.mark_modified()
    with (
        mock.patch("gui.app.filedialog.asksaveasfilename", return_value=str(new_path)),
        mock.patch("gui.app.messagebox.showinfo"),
    ):
        app.save_state_as()

    assert new_path.exists()
    assert app.memory_source == new_path
    assert app.memory.modified is False


@pytest.mark.parametrize("confirm", [True, False])
def test_saving_over_the_loaded_file_asks_first(app, tmp_path, confirm):
    path = tmp_path / "already-open.dm41"
    Memory().to_file(path)
    original = path.read_bytes()
    app._load_state_into_buffer(str(path))
    app.memory.status_registers.set_flag(0, True)  # something to save

    with (
        mock.patch.object(app, "save_state_as") as save_as,
        mock.patch("gui.app.messagebox.showinfo"),
        mock.patch("gui.app.messagebox.askyesno", return_value=confirm) as ask,
    ):
        app.save_state_to_file()

    ask.assert_called_once()
    save_as.assert_not_called()
    assert (path.read_bytes() != original) is confirm


def test_a_tab_edit_marks_the_state_modified_and_guards_it(app):
    """_on_memory_changed() is the callback every editable tab calls. It
    must set the modified flag, so the "Discard unsaved changes?" guard
    then protects the edit."""
    assert app.memory.modified is False
    app._on_memory_changed()
    assert app.memory.modified is True

    with mock.patch("gui.app.messagebox.askyesno", return_value=False) as ask:
        app.new_memory_buffer()
    ask.assert_called_once()


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
    with (
        mock.patch("gui.app.messagebox.showerror") as error,
        mock.patch("gui.app.ask_yes_no", return_value=answer) as confirm,
    ):
        app.send_state_to_calculator()
    return error, confirm


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


@pytest.mark.parametrize("answer", [True, False])
def test_send_lists_what_a_dm41l_cannot_run_and_sends_only_on_yes(
    connected_app, answer
):
    error, confirm = _send(connected_app, "xrom.d41", answer=answer)

    error.assert_not_called()
    confirm.assert_called_once()
    parent, _title, message = confirm.call_args.args
    assert parent is connected_app
    assert message.startswith(ORIGINAL_CONFIRMATION[: -len(" Continue?")])
    # Grouped under the program, which is named once (GitHub issue #55).
    assert (
        'These will not work on a DM41L:\n\nProgram "DM41X":\n'
        "- step 3: X<I>Y is not built into a DM41L\n"
    ) in message
    assert message.count('Program "DM41X"') == 1
    # 18 warnings; the dialog shows 12 and counts the rest.
    assert "- step 14:" in message
    assert "step 15" not in message
    assert "...and 6 more." in message
    assert message.endswith("Send it anyway?")
    assert connected_app.sent.called is answer


def test_send_lists_key_assignments_a_dm41l_cannot_run(connected_app):
    _error, confirm = _send(connected_app, "dm41x_xrom_keys.d41")

    _parent, _title, message = confirm.call_args.args
    assert "Key 12 (unshifted):\n- LKAOFF is not built into a DM41L" in message
    assert "Key 11 (unshifted):\n- LKAON is not built into a DM41L" in message


@pytest.mark.parametrize(
    "state, connection",
    [
        ("6x-xm.dm41", DM41L),
        # The check uses the model the connection declares: one declaring a
        # DM41X takes the state a DM41L refuses.
        ("dm41x_manyfiles.dm41", DM41X),
    ],
)
def test_send_of_a_state_that_fits_asks_the_usual_question(
    connected_app, state, connection
):
    connected_app.serial.profile = connection

    error, confirm = _send(connected_app, state)

    error.assert_not_called()
    confirm.assert_called_once_with(
        connected_app, "Send State to Calculator", ORIGINAL_CONFIRMATION
    )
    connected_app.sent.assert_called_once()


def test_send_when_not_connected_does_not_check_anything(dm41l_app):
    """In DM41L mode, where Send is available at all: not being connected
    is what stops it, before any checking. (In DM41X mode the mode guard
    fires first instead -- see
    test_the_serial_actions_refuse_in_dm41x_mode.)"""
    dm41l_app.memory = Memory.from_file(
        DATA_DIR / "dm41x_manyfiles.dm41", profile=DM41X
    )
    with (
        mock.patch("gui.app.messagebox.showwarning") as warn,
        mock.patch("gui.app.check_profile_fit") as check,
    ):
        dm41l_app.send_state_to_calculator()

    warn.assert_called_once()
    check.assert_not_called()


# -- Opening a DM41X state (plan, phase 3 steps 1, 4 and 5) -------------------


def _tree_rows(tree):
    return [tree.item(iid, "values") for iid in tree.get_children()]


@pytest.mark.parametrize(
    "mode, state", [("DM41X", "dm41x_manyfiles.dm41"), ("DM41L", "lander.dm41")]
)
def test_a_state_file_opens_with_the_modes_profile(app_in_mode, mode, state):
    """A DM41L owner's own state is shown as a DM41L's, not padded out to
    the DM41X's memory map."""
    app = app_in_mode(mode)
    app._load_state_into_buffer(str(DATA_DIR / state))
    assert app.memory.profile is DeviceMode(mode).profile


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


@pytest.mark.parametrize("profile, free", [(DM41L, "362/362"), (DM41X, "600/600")])
def test_overview_total_follows_the_profile(app, profile, free):
    app.overview_tab.render(Memory(profile=profile))
    assert app.overview_tab._xm_summary_texts()[2] == f"{free} registers (100%)"


@pytest.mark.parametrize("profile, last", [(DM41X, 0x3EF), (DM41L, 0x2EF)])
def test_hex_view_runs_to_the_end_of_the_profiles_map(app, profile, last):
    app.hex_view_tab.render(Memory(profile=profile))

    rows = _tree_rows(app.hex_view_tab._tree)

    assert len(rows) == last + 1
    assert (rows[0][0], rows[-1][0]) == ("0x000", f"0x{last:03x}")
    assert rows[last][3] == "XM"
    # The register below each later XM region's pointer is inaccessible (#65).
    assert (rows[0x200][3], rows[0x201][3]) == ("Inaccessible", "XM")
    if profile is DM41X:
        assert rows[0x2F0][3] == "Inaccessible"  # the gap between XM #1 and #2
        assert (rows[0x300][3], rows[0x301][3]) == ("Inaccessible", "XM")
    assert f"0x000-0x{last:03x} ({last + 1} registers)" in (
        app.hex_view_tab._header_label.cget("text")
    )


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


@pytest.mark.parametrize("mode, extension", [("DM41X", ".d41"), ("DM41L", ".dm41")])
def test_save_as_of_a_new_buffer_follows_the_mode(app_in_mode, mode, extension):
    """A buffer with no file of its own gets the extension the current
    mode's calculator writes (plan, phase 6)."""
    app = app_in_mode(mode)
    with mock.patch("gui.app.filedialog.asksaveasfilename", return_value="") as ask:
        app.save_state_as()
    assert ask.call_args.kwargs["defaultextension"] == extension


def test_a_d41_file_saves_back_identically(app, tmp_path):
    text = (DATA_DIR / "dm41x_xrom_keys.d41").read_text()
    app._load_state_into_buffer(str(DATA_DIR / "dm41x_xrom_keys.d41"))
    target = tmp_path / "copy.d41"

    with (
        mock.patch("gui.app.filedialog.asksaveasfilename", return_value=str(target)),
        mock.patch("gui.app.messagebox.showinfo"),
    ):
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


@contextlib.contextmanager
def _answer(value):
    """Answers every question with `value`: the native discard prompt
    (messagebox.askyesno) and the mode question (ask_yes_no, GitHub issue
    #55). Yields the ask_yes_no mock, whose call is the mode question."""
    with (
        mock.patch("gui.app.messagebox.askyesno", return_value=value),
        mock.patch("gui.app.ask_yes_no", return_value=value) as ask,
    ):
        yield ask


def _yes():
    return _answer(True)


def _no():
    return _answer(False)


@pytest.mark.parametrize("mode, profile", [("DM41X", DM41X), ("DM41L", DM41L)])
def test_the_mode_comes_from_the_saved_preference(app_in_mode, mode, profile):
    app = app_in_mode(mode)
    assert app.mode is DeviceMode(mode)
    assert app.profile is profile
    with _yes():
        app.new_memory_buffer()
    assert app.memory.profile is profile, "a new buffer uses the mode's profile"


@pytest.mark.parametrize(
    "start, target, state",
    [("DM41L", "DM41X", "keyassigns.dm41"), ("DM41X", "DM41L", "dm41x_manyfiles.dm41")],
)
def test_switching_mode_starts_an_empty_state(app_in_mode, start, target, state):
    """In either direction: keeping the state would mean rebuilding the
    Memory through to_string()/from_string(), which phase 6 avoids."""
    app = app_in_mode(start)
    app._load_state_into_buffer(str(DATA_DIR / state))
    assert app.memory_source is not None

    with _yes():
        assert app.set_mode(DeviceMode(target), persist=True) is True

    assert app.mode is DeviceMode(target)
    assert app.memory.profile is DeviceMode(target).profile
    assert app.memory_source is None
    assert app.memory.to_string() == Memory(profile=app.profile).to_string()


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
    with _no() as ask:
        app.set_mode(DeviceMode.DM41L, persist=True)

    parent, title, message = ask.call_args.args
    assert (parent, title) == (app, "Switch Mode")
    assert "starts a new, empty memory state" in message
    assert "export" in message, "says how to move data between models"


def test_switching_mode_persists_only_when_asked(app, prefs_file):
    """A persisted switch is saved; a session-only one isn't, and the
    status bar marks it."""
    assert app._mode_label.cget("text") == "DM41X mode"

    with _yes():
        app.set_mode(DeviceMode.DM41L, persist=True)
    assert json.loads(prefs_file.read_text())["mode"] == "DM41L"
    assert app.config_store.mode is DeviceMode.DM41L
    assert not app.mode_is_temporary
    assert app._mode_label.cget("text") == "DM41L mode"

    with _yes():
        app.set_mode(DeviceMode.DM41X, persist=False)
    assert json.loads(prefs_file.read_text())["mode"] == "DM41L", "not written"
    assert app.mode is DeviceMode.DM41X
    assert app.mode_is_temporary
    assert app._mode_label.cget("text") == "DM41X mode (this session)"


# -- Auto-switch when a state will not fit the mode --------------------------


def test_a_too_large_state_offers_dm41x_mode_and_loads_on_yes(dm41l_app):
    with _yes() as ask:
        dm41l_app._load_state_into_buffer(str(DATA_DIR / "dm41x_manyfiles.dm41"))

    message = ask.call_args.args[2]
    # Just the reason, not the findings (GitHub issue #60).
    assert message.startswith(
        "dm41x_manyfiles.dm41 needs more memory than a DM41L has.\n\n"
        "DM41X mode can open it.\n\n"
    )
    assert "XM files" not in message
    assert dm41l_app.mode is DeviceMode.DM41X
    assert dm41l_app.memory.profile is DM41X
    assert len(dm41l_app.memory.extended_memory.list_files()) == 59


def test_an_accepted_auto_switch_is_not_persisted(dm41l_app, dm41l_prefs):
    """Someone who owns one calculator will rarely want the other mode
    permanently, so the offer is session-only."""
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


@pytest.mark.parametrize(
    "state",
    [
        "lander.dm41",  # fits a DM41L
        # Junk above 0x300 that belongs to no XM file: a warning at Send,
        # not a reason to refuse the load.
        "dm41x_retpfl_before.d41",
        # Calls 18 functions a DM41L lacks: those warn at Send only -- the
        # user may mean to retype the step on the calculator.
        "xrom.d41",
        # A file name on region 0's last register, its header at the top
        # of region 1: an XM directory this app could not read once, and
        # reported as "needs more memory" (GitHub issue #68).
        "xm_file_cross.dm41",
        "xm_file_cross_115.dm41",
    ],
)
def test_a_state_a_dm41l_can_hold_loads_without_an_offer(dm41l_app, state):
    with mock.patch("gui.app.messagebox.askyesno") as ask:
        dm41l_app._load_state_into_buffer(str(DATA_DIR / state))

    ask.assert_not_called()
    assert dm41l_app.mode is DeviceMode.DM41L
    assert dm41l_app.memory_source == DATA_DIR / state


def test_an_unreadable_xm_directory_does_not_offer_dm41x_mode(dm41l_app, tmp_path):
    """DM41X mode cannot read a broken directory either, so it is not
    offered as the cure: the state opens as it is, as it would in DM41X
    mode, and the XM Files tab says what is wrong (GitHub issue #68)."""
    text = (DATA_DIR / "xm_file_cross.dm41").read_text()
    broken = tmp_path / "broken.dm41"
    # FILL2's header, at the top of region 1, no longer parses as one.
    broken.write_text(
        text.replace(
            "2ec  00000000000000  00000000000000  00000000000000  2000000000007c",
            "2ec  00000000000000  00000000000000  00000000000000  ffffffffffffff",
        )
    )
    assert broken.read_text() != text

    with _yes() as ask:
        dm41l_app._load_state_into_buffer(str(broken))

    ask.assert_not_called()
    assert dm41l_app.mode is DeviceMode.DM41L
    assert dm41l_app.memory_source == broken


# -- Serial is gated by the mode ---------------------------------------------


def _connect_entries(instance):
    menu = instance._connect_menu
    return [
        menu.entrycget(index, "state") or "normal"
        for index in instance._serial_menu_indices
    ]


def test_the_connect_menu_follows_the_mode(app):
    """The serial entries are disabled, not hidden, in a mode with no
    serial console."""
    assert app._serial_menu_indices, "there are serial entries to gate"
    assert set(_connect_entries(app)) == {"disabled"}

    with _yes():
        app.set_mode(DeviceMode.DM41L, persist=True)
    assert set(_connect_entries(app)) == {"normal"}

    with _yes():
        app.set_mode(DeviceMode.DM41X, persist=True)
    assert set(_connect_entries(app)) == {"disabled"}


@pytest.mark.parametrize(
    "action",
    [
        "show_connect_dialog",
        "get_state_from_calculator",
        "send_state_to_calculator",
        "set_calculator_time",
    ],
)
def test_the_serial_actions_refuse_in_dm41x_mode(app, action):
    """The keyboard shortcuts never consult the menu's state, so each
    action guards itself as well."""
    with (
        mock.patch("gui.app.messagebox.showinfo") as info,
        mock.patch.object(app.serial, "connect") as connect,
    ):
        getattr(app, action)()

    assert info.called, f"{action} said nothing"
    assert "DM41X Mode" in info.call_args.args[0]
    connect.assert_not_called()


@pytest.mark.parametrize("confirm", [True, False])
def test_switching_mode_while_connected_warns_and_disconnects_only_on_yes(
    dm41l_app, confirm
):
    dm41l_app.serial.is_connected = True
    with (
        mock.patch.object(dm41l_app, "disconnect") as disconnect,
        _answer(confirm) as ask,
    ):
        assert dm41l_app.set_mode(DeviceMode.DM41X, persist=True) is confirm

    assert "serial connection will be closed" in ask.call_args.args[2]
    assert disconnect.called is confirm


# -- The Preferences dialog reports the mode rather than writing it ----------


@pytest.mark.parametrize(
    "confirm, expected", [(True, DeviceMode.DM41L), (False, DeviceMode.DM41X)]
)
def test_preferences_can_change_the_mode_if_the_switch_is_confirmed(
    app, confirm, expected
):
    with _answer(confirm):
        app._on_preferences_saved(requested_mode=DeviceMode.DM41L)

    assert app.mode is expected
    assert app.config_store.mode is expected


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


@pytest.mark.parametrize("mode, status", [("DM41X", ""), ("DM41L", "Not connected")])
def test_the_status_mentions_a_connection_only_where_one_is_possible(
    app_in_mode, mode, status
):
    assert app_in_mode(mode)._status_label.cget("text") == status


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


@pytest.mark.parametrize("mode, capacity", [("DM41L", 362), ("DM41X", 600)])
def test_the_mode_sets_the_extended_memory_size(app_in_mode, mode, capacity):
    """The limit is not a separate check: the mode's profile has only the
    XM regions its calculator has, so extended memory refuses what will
    not fit in them."""
    from memory import DM41MemoryError

    app = app_in_mode(mode)
    assert xm_total_registers(app.memory.profile) == capacity
    xm = app.memory.extended_memory
    if capacity < 400:
        with pytest.raises(DM41MemoryError, match="Not enough free space"):
            xm.add_file("BIG", 2, numbers=[1.0] * 400)
    else:
        xm.add_file("BIG", 2, numbers=[1.0] * 400)
        assert [f.name.strip() for f in xm.list_files()] == ["BIG"]


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
    """The mode is the standing fact about the session;
    the status label beside it is running commentary. Order is the pack
    order within the status bar, left to right."""
    bar = app._mode_label.master
    left = [w for w in bar.winfo_children() if w.pack_info().get("side") == "left"]
    assert left.index(app._mode_label) < left.index(app._status_label)
    assert left.index(app._status_label) < left.index(app._modified_label)


def test_serial_thread_errors_are_handled_by_the_engine_pump(dm41l_app):
    """A fatal error from SerialManager's background thread is queued, and
    the app picks it up from its own Tk-thread pump instead of the
    background thread calling into Tk directly."""
    dm41l_app.serial.error_queue.put("Serial communications failure: boom")
    with mock.patch.object(dm41l_app, "after") as after:
        dm41l_app._pump_engine()

    assert dm41l_app._status_label.cget("text") == dm41l_app._idle_status()
    assert dm41l_app.serial.get_next_error() is None
    # The error dialog is deferred; the pump reschedules itself.
    assert after.call_count == 2


def test_auto_fetch_keeps_the_reading_status_until_the_state_arrives(connected_app):
    """With nothing loaded, connecting fetches the calculator's state and the
    status bar says so -- it must not be overwritten straight away by
    "Connected to ...", which made the fetch look finished."""
    connected_app.serial.serial_inst = mock.Mock(port="/dev/tty.test")
    with mock.patch.object(connected_app, "after") as after:
        connected_app._fetch_memory_state()

    assert connected_app._status_label.cget("text") == "Reading memory state..."
    after.assert_called_once()


def test_auto_fetch_is_skipped_when_a_state_is_already_loaded(connected_app):
    connected_app.serial.serial_inst = mock.Mock(port="/dev/tty.test")
    connected_app.memory.mark_modified()
    with mock.patch.object(connected_app, "after") as after:
        connected_app._fetch_memory_state()

    assert connected_app._status_label.cget("text") == "Connected to /dev/tty.test"
    after.assert_not_called()


# -- A command refused because the engine is busy is reported -------------


@pytest.fixture
def busy_app(dm41l_app):
    """A connected app whose engine refuses every command, as it does while
    another command is still in progress. after() runs its callback at
    once, so deferred commands are attempted inside the test."""
    dm41l_app.serial.is_connected = True
    dm41l_app.serial.serial_inst = mock.Mock(port="/dev/tty.test")
    with (
        mock.patch.object(dm41l_app.engine, "execute", return_value=False) as execute,
        mock.patch.object(
            dm41l_app, "after", side_effect=lambda ms, fn=None: fn() if fn else None
        ),
        mock.patch("gui.app.messagebox.showwarning") as warning,
    ):
        dm41l_app.execute = execute
        dm41l_app.warning = warning
        yield dm41l_app


def _assert_reported_busy(app):
    app.execute.assert_called_once()
    app.warning.assert_called_once()
    assert app.warning.call_args.args[0] == "Busy"
    # The status no longer claims the refused command is under way.
    assert app._status_label.cget("text") == "Connected to /dev/tty.test"


@pytest.mark.parametrize(
    "action",
    [
        lambda app: app.get_state_from_calculator(),
        lambda app: app._fetch_memory_state(),  # the automatic fetch on connect
        lambda app: app.set_calculator_time(),
        lambda app: app._on_timeout_command("Console timeout set"),  # a startup step
    ],
    ids=["get state", "auto fetch", "set time", "startup step"],
)
def test_a_command_refused_as_busy_is_reported_and_the_status_restored(
    busy_app, action
):
    action(busy_app)
    _assert_reported_busy(busy_app)


def test_busy_send_is_reported_and_nothing_is_transferred(busy_app):
    busy_app.memory = Memory.from_file(DATA_DIR / "6x-xm.dm41", profile=DM41L)
    with (
        mock.patch("gui.app.ask_yes_no", return_value=True),
        mock.patch("gui.app.LoadMemoryStringCommand.trigger_transfer") as transfer,
    ):
        busy_app.send_state_to_calculator()

    _assert_reported_busy(busy_app)
    transfer.assert_not_called()


# -- XROM libraries (GitHub issue #53) ----------------------------------------


@pytest.fixture
def builtin_xroms_after():
    """Puts the name registry back to the built-in XROMs after a test that
    activates libraries, so no later test (in any module) sees them."""
    yield
    set_active_libraries([])


def _xrom_prefs(tmp_path, monkeypatch, **arrangement):
    path = tmp_path / ".dm41_test_prefs.json"
    stored = {
        mode: [{"id": i, "enabled": True} for i in ids]
        for mode, ids in arrangement.items()
    }
    _write_prefs(path, xrom_libraries=stored)
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", path)
    return path


def _active_ids(app):
    return [library.id for library in app.xrom_active.libraries]


def test_the_saved_arrangement_is_active_at_start(
    _shared_app, tmp_path, monkeypatch, builtin_xroms_after
):
    _xrom_prefs(tmp_path, monkeypatch, DM41X=["math"])
    _reset(_shared_app)
    assert _active_ids(_shared_app) == ["dm41x-cx", "math"]
    assert resolve("SOLVE") == xrom_op(0xA0, 0x49)


def test_without_an_arrangement_only_the_builtin_library_is_active(app):
    assert _active_ids(app) == ["dm41x-cx"]
    with pytest.raises(ValueError):
        resolve("SOLVE")


def test_each_mode_has_its_own_arrangement(
    _shared_app, tmp_path, monkeypatch, builtin_xroms_after
):
    _xrom_prefs(tmp_path, monkeypatch, DM41X=["math"], DM41L=["stat"])
    _reset(_shared_app)
    with _yes():
        _shared_app.set_mode(DeviceMode.DM41L, persist=False)
    assert _active_ids(_shared_app) == ["hp41cx", "stat"]
    with pytest.raises(ValueError):
        resolve("SOLVE")


def test_saving_from_the_manager_applies_and_persists(
    app, prefs_file, builtin_xroms_after
):
    with mock.patch("gui.app.messagebox.showwarning") as warn:
        app._on_xrom_libraries_saved([("math", True), ("stat", False)])

    warn.assert_not_called()
    assert _active_ids(app) == ["dm41x-cx", "math"]
    assert resolve("SOLVE") == xrom_op(0xA0, 0x49)
    stored = json.loads(prefs_file.read_text())["xrom_libraries"]["DM41X"]
    assert stored == [{"id": "math", "enabled": True}, {"id": "stat", "enabled": False}]
    assert "1 plug-in library active" in app._status_label.cget("text")


def test_a_number_clash_is_shown_once_after_a_change(app, builtin_xroms_after):
    entries = [("aviation", True), ("clinical", True)]
    with mock.patch("gui.app.messagebox.showwarning") as warn:
        app._on_xrom_libraries_saved(entries)
        app._on_xrom_libraries_saved(entries)  # nothing changed this time
    warn.assert_called_once()
    assert "both use XROM 19" in warn.call_args.args[1]


def _menu_labels(app, name):
    menu = app.nametowidget(app._menubar.entrycget(name, "menu"))
    return [
        menu.entrycget(i, "label")
        for i in range(menu.index("end") + 1)
        if menu.type(i) == "command"
    ]


def test_the_tools_menu_opens_the_manager(app):
    labels = _menu_labels(app, "Tools")
    assert "XROM Libraries..." not in _menu_labels(app, "File")
    assert "XROM Libraries..." in labels
    dialog = app.show_xrom_libraries()
    assert dialog.winfo_exists()
    dialog.destroy()


def test_a_library_file_that_cannot_load_is_reported(app, prefs_file):
    folder = ProjectConfig().xrom_directory
    folder.mkdir(parents=True)
    (folder / "broken.json").write_text("{not json")
    errors = app._apply_xrom_libraries()
    assert len(errors) == 1
    assert str(errors[0]).startswith("broken.json: not valid JSON (line 1")
    assert _active_ids(app) == ["dm41x-cx"]


# -- Preferences in the macOS application menu (GitHub issue #62) ------------


def test_preferences_is_in_the_file_menu_except_on_macos(app):
    """On macOS it is in the application menu instead, the one place Tk
    shows its Cmd+, shortcut."""
    in_file_menu = "Preferences" in _menu_labels(app, "File")
    assert in_file_menu is (app_module.PLATFORM_SYSTEM != "Darwin")


@pytest.mark.skipif(
    app_module.PLATFORM_SYSTEM != "Darwin", reason="the macOS application menu"
)
def test_the_macos_application_menu_opens_preferences(app):
    assert app.tk.call("info", "commands", "::tk::mac::ShowPreferences")
    app.tk.call("::tk::mac::ShowPreferences")
    assert app._preferences_dialog.winfo_exists()


def test_preferences_opens_only_once(app):
    """The menu item and the key binding can both deliver one Cmd+,."""
    first = app.show_preferences()
    assert app.show_preferences() is first
    first.destroy()
    second = app.show_preferences()
    assert second is not first and second.winfo_exists()


# -- Save As shortcut ---------------------------------------------------------


def _accelerator(app, menu_name, label):
    menu = app.nametowidget(app._menubar.entrycget(menu_name, "menu"))
    for i in range(menu.index("end") + 1):
        if menu.type(i) == "command" and menu.entrycget(i, "label") == label:
            return menu.entrycget(i, "accelerator")
    raise LookupError(label)


@pytest.mark.parametrize("key", ["S", "s"])
def test_save_as_has_a_shift_shortcut(app, key):
    """Cmd/Ctrl+Shift+S, shown on the menu item, runs Save As -- not Save."""
    acc = "Command" if app_module.PLATFORM_SYSTEM == "Darwin" else "Control"
    assert _accelerator(app, "File", "Save State As...") == f"{acc}+Shift+S"

    with (
        mock.patch.object(app, "save_state_as") as save_as,
        mock.patch.object(app, "save_state_to_file") as save,
    ):
        app.focus_force()
        app.update()
        app.event_generate(f"<{acc}-Shift-{key}>")
        app.update()

    save_as.assert_called_once()
    save.assert_not_called()
