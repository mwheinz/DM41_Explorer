"""
Tests for GitHub issue #11 (import/export for XM Data/ASCII files and main
memory data registers): the DATA/ASCII line format's GUI wiring in
gui/xm_file_dialog.py, gui/xm_files_tab.py, and gui/data_registers_tab.py.

registers.format_data_line()/parse_data_line() and
ExtendedMemory.add_file(data_lines=...)/get_data_lines() themselves are
covered at the model level in test_memory.py; these tests cover the GUI
layer built on top of them -- the multi-line Data editor, per-line error
reporting, and the actual Export/Import file I/O.

Requires a real Tk display (Xvfb in CI/sandboxes) -- same requirement as
test_app.py.
"""

from pathlib import Path
from unittest import mock

import pytest

# tkinter itself (not just customtkinter, which is pure Python but imports
# tkinter internally) is what's actually missing in a headless Python build
# with no Tcl/Tk -- importorskip on "tkinter" first, before importing it
# below, so collection skips cleanly there instead of erroring out.
pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

from tkinter import messagebox
import customtkinter as ctk

from memory import Memory, ExtendedMemory, format_data_line, parse_data_line
from gui.xm_file_dialog import XMFileDialog
from gui.xm_files_tab import XMFilesTab, _guess_file_type
from gui.data_registers_tab import DataRegistersTab

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


# ---- XMFileDialog: multi-line Data editor + per-line validation ----


def test_dialog_save_parses_all_three_data_line_kinds(root):
    captured = {}
    dlg = XMFileDialog(root, lambda *a: captured.update(name=a[0], file_type=a[1], kwargs=a[2]))
    dlg._name_var.set("TEST")
    dlg._data_box.delete("1.0", "end")
    dlg._data_box.insert("1.0", "1.5\nHI\n0x50000000000000\n-3\n")
    dlg._on_save_clicked()

    assert captured["file_type"] == ExtendedMemory.TYPE_DATA
    assert captured["kwargs"]["data_lines"] == ["1.5", "HI", "0x50000000000000", "-3"]


def test_dialog_save_rejects_invalid_line_with_line_number(root, monkeypatch):
    on_save = mock.Mock()
    errors = []
    monkeypatch.setattr(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    )

    dlg = XMFileDialog(root, on_save)
    dlg._name_var.set("BAD")
    dlg._data_box.delete("1.0", "end")
    dlg._data_box.insert("1.0", "1.5\nTOOLONGTEXT\n")
    dlg._on_save_clicked()

    on_save.assert_not_called()
    assert len(errors) == 1
    assert "Line 2" in errors[0][1]
    assert "TOOLONGTEXT" in errors[0][1]
    dlg.destroy()


def test_dialog_save_rejects_name_outside_allowed_character_range(root, monkeypatch):
    """XM file names must be plain ASCII 32-101 -- unlike file content,
    names don't support trigraphs, so a character above that range is
    rejected with immediate GUI feedback (not just a later model-level
    exception)."""
    on_save = mock.Mock()
    errors = []
    monkeypatch.setattr(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    )

    dlg = XMFileDialog(root, on_save)
    dlg._name_var.set("BADz")  # 'z' (122) is above the 101 ('e') upper bound
    dlg._data_box.delete("1.0", "end")
    dlg._data_box.insert("1.0", "1\n")
    dlg._on_save_clicked()

    on_save.assert_not_called()
    assert len(errors) == 1
    assert "outside the allowed range" in errors[0][1]
    dlg.destroy()


def test_dialog_save_ascii_records_decode_trigraphs(root):
    """ASCII records containing trigraphs are decoded to their FOCAL byte
    values before being handed to add_file() -- the dialog passes the raw
    (still-trigraph-encoded) text through unchanged; decode happens in
    ExtendedMemory.add_file() itself, so this just confirms the dialog
    doesn't reject or mangle trigraph syntax on the way through."""
    captured = {}
    dlg = XMFileDialog(root, lambda *a: captured.update(name=a[0], file_type=a[1], kwargs=a[2]))
    dlg._name_var.set("NOTES")
    dlg._type_var.set("ASCII")
    dlg._on_type_changed("ASCII")
    dlg._ascii_box.delete("1.0", "end")
    dlg._ascii_box.insert("1.0", "A\\EB\n\\T\\+\n")
    dlg._on_save_clicked()

    assert captured["file_type"] == ExtendedMemory.TYPE_ASCII
    assert captured["kwargs"]["records"] == ["A\\EB", "\\T\\+"]
    dlg.destroy()


def test_dialog_save_ascii_records_reject_invalid_trigraph(root, monkeypatch):
    on_save = mock.Mock()
    errors = []
    monkeypatch.setattr(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    )

    dlg = XMFileDialog(root, on_save)
    dlg._name_var.set("BAD")
    dlg._type_var.set("ASCII")
    dlg._on_type_changed("ASCII")
    dlg._ascii_box.delete("1.0", "end")
    dlg._ascii_box.insert("1.0", "\\zz\n")
    dlg._on_save_clicked()

    on_save.assert_not_called()
    assert len(errors) == 1
    assert "Line 1" in errors[0][1]
    dlg.destroy()


def test_dialog_edit_existing_data_file_prefills_data_lines(root):
    """An existing Data file with mixed content (see
    test_xm_add_file_data_lines_supports_mixed_content in test_memory.py)
    must show its DATA-format lines, not raise, in the Edit dialog."""
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    xm.add_file("MIXED", xm.TYPE_DATA, data_lines=["1.5", "HI", "0x50000000000000"])
    existing = xm.list_files()[0]

    dlg = XMFileDialog(root, lambda *a: None, existing=existing)
    shown = dlg._data_box.get("1.0", "end").rstrip("\n")
    assert shown.split("\n") == ["1.5", "HI", "0x50000000000000"]
    dlg.destroy()


def test_dialog_initial_prefill_for_import(root):
    """XMFilesTab._import_file() passes initial={...} to prefill an
    imported file's content before the user reviews/saves it."""
    dlg = XMFileDialog(
        root,
        lambda *a: None,
        initial={"name": "IMP", "file_type": "Data", "content": "1.5\nHI"},
    )
    assert dlg._name_var.get() == "IMP"
    assert dlg._type_var.get() == "Data"
    assert dlg._data_box.get("1.0", "end").rstrip("\n") == "1.5\nHI"
    dlg.destroy()


# ---- XMFilesTab: Export / Import File... ----


def test_xm_export_file_writes_data_lines(root, tmp_path, monkeypatch):
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    xm.add_file("MIXED", xm.TYPE_DATA, data_lines=["1.5", "HI", "0x50000000000000"])
    f = xm.list_files()[0]

    tab = XMFilesTab(root)
    tab.render(memory)

    out_path = tmp_path / "MIXED.txt"
    monkeypatch.setattr("gui.xm_files_tab.filedialog.asksaveasfilename", lambda **k: str(out_path))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)

    tab._export_file(f)

    assert out_path.read_text(encoding="ascii").splitlines() == [
        "1.5", "HI", "0x50000000000000",
    ]


def test_xm_export_file_writes_ascii_records(root, tmp_path, monkeypatch):
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    xm.add_file("NOTES", xm.TYPE_ASCII, records=["hello", "world"])
    f = xm.list_files()[0]

    tab = XMFilesTab(root)
    tab.render(memory)

    out_path = tmp_path / "NOTES.txt"
    monkeypatch.setattr("gui.xm_files_tab.filedialog.asksaveasfilename", lambda **k: str(out_path))
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)

    tab._export_file(f)

    assert out_path.read_text(encoding="ascii").splitlines() == ["hello", "world"]


def test_xm_import_file_prefills_dialog_and_adds_on_save(root, tmp_path, monkeypatch):
    """_import_file() reads the chosen file and opens it pre-filled in
    the Add dialog; saving that dialog actually adds the file."""
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    tab = XMFilesTab(root)
    tab.render(memory)

    src_path = tmp_path / "myfile.txt"
    src_path.write_text("1.5\nHI\n0x50000000000000\n", encoding="ascii")
    monkeypatch.setattr("gui.xm_files_tab.filedialog.askopenfilename", lambda **k: str(src_path))

    created = {}
    monkeypatch.setattr(
        "gui.xm_files_tab.XMFileDialog",
        lambda master, on_save, **kwargs: created.update(
            master=master, on_save=on_save, kwargs=kwargs
        ),
    )

    tab._import_file()

    assert created["kwargs"]["initial"]["name"] == "myfile"
    assert created["kwargs"]["initial"]["file_type"] == "Data"
    assert created["kwargs"]["initial"]["content"] == "1.5\nHI\n0x50000000000000"

    # Simulate the user clicking Save in that pre-filled dialog. (Name
    # must be 1-7 characters -- see ExtendedMemory.add_file() -- an
    # 8-char name here would hit _save_new_or_edited_file()'s real,
    # unmocked messagebox.showerror() and hang the test under Xvfb.)
    created["on_save"]("IMPORT", ExtendedMemory.TYPE_DATA, {"data_lines": ["1.5", "HI"]})
    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    files = xm.list_files()
    assert len(files) == 1
    assert files[0].name.rstrip() == "IMPORT"
    assert files[0].get_data_lines() == ["1.5", "HI"]


# ---- _guess_file_type() / import-type-detection regressions ----
#
# User-reported bug: importing a genuine ASCII text file (long, punctuated
# lines -- see tests/data/ALLCHRS.txt, a real XM ASCII-file export) opened
# the Add dialog defaulted to "Data", and the lines don't parse as DATA
# lines (way over the 6-char alpha-text limit, not numbers, no 0x prefix),
# so saving failed with a DATA-line error even though the user wanted an
# ASCII file. Two separate fixes: _guess_file_type() below picks a better
# default, and XMFileDialog pre-fills *both* editors (see the dialog tests
# above) so switching the type dropdown to correct a wrong guess doesn't
# lose the imported text either.
#

def test_guess_file_type_prose_text_is_ascii():
    """The exact scenario reported: long, punctuated lines from a real XM
    ASCII export aren't valid DATA lines (too long, not numbers, no 0x
    prefix)."""
    lines = (DATA_DIR / "ALLCHRS.txt").read_text(encoding="ascii").splitlines()
    assert _guess_file_type(lines) == "ASCII"


def test_guess_file_type_numbers_and_short_text_is_data():
    # Deliberately not tests/data/XM1.000.txt: that file (created while
    # testing this feature) has a genuine typo -- "0x0123456789ABCDE" is
    # 15 hex digits, one too many for a 7-byte register -- so it's
    # correctly *not* all-valid-DATA content; see the file-format note
    # surfaced back to the user rather than silently "fixed" here.
    lines = ["TEST1", "TEST2", "0x0123456789ABCD", "1.23456789", "3.1415926"]
    assert _guess_file_type(lines) == "Data"


def test_guess_file_type_empty_defaults_to_data():
    assert _guess_file_type([]) == "Data"


def test_guess_file_type_one_bad_line_is_enough_for_ascii():
    assert _guess_file_type(["1.5", "HI", "this line is way too long for data"]) == "ASCII"


def test_xm_import_prose_file_guesses_ascii_and_saves_correctly(root, tmp_path, monkeypatch):
    """End-to-end regression for the reported bug, using a real ASCII XM
    export (ALLCHRS.txt): Import File... must guess ASCII and actually let
    it save as an ASCII file without the user touching the type
    dropdown."""
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    tab = XMFilesTab(root)
    tab.render(memory)

    src_path = DATA_DIR / "ALLCHRS.txt"
    monkeypatch.setattr("gui.xm_files_tab.filedialog.askopenfilename", lambda **k: str(src_path))

    created = {}
    monkeypatch.setattr(
        "gui.xm_files_tab.XMFileDialog",
        lambda master, on_save, **kwargs: created.update(on_save=on_save, kwargs=kwargs),
    )

    tab._import_file()

    assert created["kwargs"]["initial"]["file_type"] == "ASCII"
    expected_lines = src_path.read_text(encoding="ascii").splitlines()
    assert created["kwargs"]["initial"]["content"] == "\n".join(expected_lines)

    created["on_save"]("CHRS", ExtendedMemory.TYPE_ASCII, {"records": expected_lines})
    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    files = xm.list_files()
    assert len(files) == 1
    assert files[0].file_type == ExtendedMemory.TYPE_ASCII
    assert files[0].get_records() == expected_lines


def test_dialog_switching_type_after_import_does_not_lose_content(root):
    """The other half of the same bug: even when the guess is wrong (or
    the user just wants to double check), toggling the type dropdown must
    not empty out the box the user is switching to."""
    lines = (DATA_DIR / "ALLCHRS.txt").read_text(encoding="ascii").splitlines()
    content = "\n".join(lines)

    dlg = XMFileDialog(
        root,
        lambda *a: None,
        # Deliberately wrong guess ("Data") to prove switching *to* ASCII
        # still shows the content, not an empty box.
        initial={"name": "CHRS", "file_type": "Data", "content": content},
    )
    assert dlg._data_box.get("1.0", "end").rstrip("\n") == content

    dlg._type_var.set("ASCII")
    dlg._on_type_changed("ASCII")
    assert dlg._ascii_box.get("1.0", "end").rstrip("\n") == content
    dlg.destroy()


# ---- Duplicate-name rejection (GitHub issue #11 follow-up) ----


def test_xm_import_duplicate_name_shows_error_not_silent_success(root, tmp_path, monkeypatch):
    """User-reported bug: re-importing an exported file whose name
    matches one already present used to silently add a duplicate
    directory entry (something a real DM41L would reject). The model-
    level fix is tested directly in test_memory.py; this confirms the GUI
    surfaces the resulting DM41MemoryError as an error dialog instead of
    quietly succeeding."""
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")  # already has "XM1.000"
    tab = XMFilesTab(root)
    tab.render(memory)
    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    before_count = len(xm.list_files())

    errors = []
    monkeypatch.setattr(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    )

    tab._save_new_or_edited_file("XM1.000", ExtendedMemory.TYPE_DATA, {"numbers": [1, 2, 3]})

    assert errors and "already exists" in errors[0][1]
    xm_after = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    assert len(xm_after.list_files()) == before_count


# ---- DataRegistersTab: Export / Import ----
#
# _export_registers()/_import_registers() now open RegisterRangeDialog /
# RegisterImportLocationDialog (gui/register_range_dialog.py -- issues #15
# and #14) before touching the filesystem or Memory; that dialog's own
# validation is tested directly in test_register_range_dialog.py. These
# tests monkeypatch the dialog classes to invoke their on_confirm callback
# immediately with a chosen (start[, end]), the same way
# test_xm_import_file_prefills_dialog_and_adds_on_save above bypasses
# XMFileDialog's UI to drive XMFilesTab's save path.


def test_data_registers_export_default_range_writes_every_register(
    root, tmp_path, monkeypatch
):
    """Confirming the range dialog unchanged reproduces the pre-#15
    'export everything' behavior."""
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    tab = DataRegistersTab(root)
    tab.render(memory)
    r00, count = tab._current_range()

    monkeypatch.setattr(
        "gui.data_registers_tab.RegisterRangeDialog",
        lambda master, cnt, on_confirm: on_confirm(0, cnt - 1),
    )
    out_path = tmp_path / "registers.txt"
    monkeypatch.setattr(
        "gui.data_registers_tab.filedialog.asksaveasfilename", lambda **k: str(out_path)
    )
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)

    tab._export_registers()

    lines = out_path.read_text(encoding="ascii").splitlines()
    assert len(lines) == count
    expected = [format_data_line(memory.get_register(a)) for a in range(r00, r00 + count)]
    assert lines == expected


def test_data_registers_export_partial_range_writes_only_that_range(
    root, tmp_path, monkeypatch
):
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    tab = DataRegistersTab(root)
    tab.render(memory)
    r00, count = tab._current_range()
    assert count > 5  # sanity: simple.dm41 needs enough registers for this range

    monkeypatch.setattr(
        "gui.data_registers_tab.RegisterRangeDialog",
        lambda master, cnt, on_confirm: on_confirm(2, 4),
    )
    out_path = tmp_path / "registers.txt"
    monkeypatch.setattr(
        "gui.data_registers_tab.filedialog.asksaveasfilename", lambda **k: str(out_path)
    )
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)

    tab._export_registers()

    lines = out_path.read_text(encoding="ascii").splitlines()
    expected = [format_data_line(memory.get_register(a)) for a in range(r00 + 2, r00 + 5)]
    assert lines == expected


def test_data_registers_import_default_location_overwrites_registers_on_confirm(
    root, tmp_path, monkeypatch
):
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    tab = DataRegistersTab(root)
    tab.render(memory)
    r00, count = tab._current_range()

    new_lines = ["7" for _ in range(count)]
    in_path = tmp_path / "registers.txt"
    in_path.write_text("\n".join(new_lines) + "\n", encoding="ascii")

    monkeypatch.setattr(
        "gui.data_registers_tab.filedialog.askopenfilename", lambda **k: str(in_path)
    )
    monkeypatch.setattr(
        "gui.data_registers_tab.RegisterImportLocationDialog",
        lambda master, cnt, import_count, on_confirm: on_confirm(0),
    )
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)

    tab._import_registers()

    for addr in range(r00, r00 + count):
        assert memory.get_register(addr).get_bcd_number() == pytest.approx(7)


def test_data_registers_import_at_offset_only_overwrites_target_registers(
    root, tmp_path, monkeypatch
):
    """The scenario from GitHub issue #14: a file with fewer registers
    than the displayed range, imported starting partway through it --
    only the targeted registers should change."""
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    tab = DataRegistersTab(root)
    tab.render(memory)
    r00, count = tab._current_range()
    assert count > 5
    before = [memory.get_register(a).get_hex() for a in range(r00, r00 + count)]

    in_path = tmp_path / "registers.txt"
    in_path.write_text("7\n7\n7\n", encoding="ascii")  # 3 registers

    monkeypatch.setattr(
        "gui.data_registers_tab.filedialog.askopenfilename", lambda **k: str(in_path)
    )
    monkeypatch.setattr(
        "gui.data_registers_tab.RegisterImportLocationDialog",
        lambda master, cnt, import_count, on_confirm: on_confirm(2),
    )
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)

    tab._import_registers()

    after = [memory.get_register(a).get_hex() for a in range(r00, r00 + count)]
    for i in range(count):
        if 2 <= i <= 4:
            assert memory.get_register(r00 + i).get_bcd_number() == pytest.approx(7)
        else:
            assert after[i] == before[i]


def test_data_registers_import_declines_on_cancel(root, tmp_path, monkeypatch):
    """If the user says no to the overwrite confirmation, nothing changes."""
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    tab = DataRegistersTab(root)
    tab.render(memory)
    r00, count = tab._current_range()
    before = [memory.get_register(a).get_hex() for a in range(r00, r00 + count)]

    new_lines = ["7" for _ in range(count)]
    in_path = tmp_path / "registers.txt"
    in_path.write_text("\n".join(new_lines) + "\n", encoding="ascii")

    monkeypatch.setattr(
        "gui.data_registers_tab.filedialog.askopenfilename", lambda **k: str(in_path)
    )
    monkeypatch.setattr(
        "gui.data_registers_tab.RegisterImportLocationDialog",
        lambda master, cnt, import_count, on_confirm: on_confirm(0),
    )
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)

    tab._import_registers()

    after = [memory.get_register(a).get_hex() for a in range(r00, r00 + count)]
    assert after == before


def test_data_registers_import_rejects_empty_file(root, tmp_path, monkeypatch):
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    tab = DataRegistersTab(root)
    tab.render(memory)
    r00, count = tab._current_range()
    before = [memory.get_register(a).get_hex() for a in range(r00, r00 + count)]

    in_path = tmp_path / "registers.txt"
    in_path.write_text("", encoding="ascii")

    monkeypatch.setattr(
        "gui.data_registers_tab.filedialog.askopenfilename", lambda **k: str(in_path)
    )
    errors = []
    monkeypatch.setattr(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    )
    dialog_shown = []
    monkeypatch.setattr(
        "gui.data_registers_tab.RegisterImportLocationDialog",
        lambda *a, **k: dialog_shown.append(1),
    )

    tab._import_registers()

    assert errors and "Empty File" in errors[0][0]
    assert not dialog_shown  # must bail out before ever prompting for a location
    after = [memory.get_register(a).get_hex() for a in range(r00, r00 + count)]
    assert after == before


def test_data_registers_import_rejects_invalid_line_with_line_number(
    root, tmp_path, monkeypatch, caplog
):
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    tab = DataRegistersTab(root)
    tab.render(memory)
    r00, count = tab._current_range()
    before = [memory.get_register(a).get_hex() for a in range(r00, r00 + count)]

    lines = ["1" for _ in range(count)]
    lines[2] = "TOOLONGTEXTVALUE"  # invalid: not a number, not <=6 chars, no 0x prefix
    in_path = tmp_path / "registers.txt"
    in_path.write_text("\n".join(lines) + "\n", encoding="ascii")

    monkeypatch.setattr(
        "gui.data_registers_tab.filedialog.askopenfilename", lambda **k: str(in_path)
    )
    errors = []
    monkeypatch.setattr(
        messagebox, "showerror", lambda title, msg: errors.append((title, msg))
    )
    dialog_shown = []
    monkeypatch.setattr(
        "gui.data_registers_tab.RegisterImportLocationDialog",
        lambda *a, **k: dialog_shown.append(1),
    )

    with caplog.at_level("ERROR"):
        tab._import_registers()

    assert errors and "Line 3" in errors[0][1]
    assert not dialog_shown  # must bail out before ever prompting for a location
    after = [memory.get_register(a).get_hex() for a in range(r00, r00 + count)]
    assert after == before

    # Regression: every messagebox.showerror() in this codebase is
    # supposed to be paired with a logger.error()/logger.exception() call
    # (see gui/app.py's logging-level policy comment) -- this path was
    # missing one until now.
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "ERROR"
    assert "line 3" in caplog.records[0].message
    assert "TOOLONGTEXTVALUE" in caplog.records[0].message


def test_data_registers_import_dialog_receives_actual_import_count(root, tmp_path, monkeypatch):
    """RegisterImportLocationDialog needs the file's real register count
    (not the displayed range's count) to validate/report where an offset
    import would land -- see GitHub issue #14's example (a 30-register
    file imported into a larger buffer)."""
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    tab = DataRegistersTab(root)
    tab.render(memory)
    r00, count = tab._current_range()
    assert count > 3

    in_path = tmp_path / "registers.txt"
    in_path.write_text("1\n2\n3\n", encoding="ascii")

    monkeypatch.setattr(
        "gui.data_registers_tab.filedialog.askopenfilename", lambda **k: str(in_path)
    )
    captured = {}
    monkeypatch.setattr(
        "gui.data_registers_tab.RegisterImportLocationDialog",
        lambda master, cnt, import_count, on_confirm: captured.update(
            count=cnt, import_count=import_count
        ),
    )

    tab._import_registers()

    assert captured == {"count": count, "import_count": 3}
