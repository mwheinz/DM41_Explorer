"""
Regression test for gui/xm_files_tab.py's edit-save path
(_save_new_or_edited_file()) -- the same class of bug fixed in
gui/alarms_tab.py (see test_alarms_tab.py): editing is implemented as
remove_file(old) then add_file(new) (required in that order -- see
add_file()'s own docstring on why the old entry must already be gone for
its duplicate-name check to allow editing a file under its own unchanged
name). If add_file() then raises (an invalid DATA/ASCII line, no room,
...), the old handler showed an error and returned WITHOUT re-rendering,
leaving the file actually deleted from extended memory while this tab's
Treeview still showed its old (now-stale) row under its old header_addr.

Requires a real Tk display (Xvfb in CI/sandboxes) -- same requirement as
test_xm_import_export.py/test_app.py.
"""

from pathlib import Path

import pytest

pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

from tkinter import messagebox
import customtkinter as ctk

from memory import Memory, ExtendedMemory
from gui.xm_files_tab import XMFilesTab

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def test_failed_edit_restores_the_original_file_and_leaves_others_selectable(
    root, monkeypatch
):
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)

    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    xm.add_file("FIRST", xm.TYPE_DATA, data_lines=["1"])
    target = xm.add_file("SECOND", xm.TYPE_DATA, data_lines=["2"])
    xm.add_file("THIRD", xm.TYPE_DATA, data_lines=["3"])

    tab = XMFilesTab(root)
    tab.render(memory)
    assert set(tab._tree.get_children()) == {
        str(f.header_addr) for f in xm.list_files()
    }

    # Simulate the user editing "SECOND" and typing an invalid DATA line
    # (too many characters for the alpha-text form, not a valid number or
    # 0x-hex either -- see registers.parse_data_line()).
    tab._save_new_or_edited_file(
        "SECOND",
        xm.TYPE_DATA,
        {"data_lines": ["TOOLONGTEXT"]},
        replacing_addr=target.header_addr,
    )

    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    current = xm.list_files()
    by_name = {f.name.rstrip(): f for f in current}
    # The original file must still be there -- a failed edit shouldn't be
    # able to delete data the user never asked to remove. (Restoring it
    # re-appends it via the normal add_file() path -- same as a
    # *successful* edit already does per this method's own docstring --
    # so it may no longer sit in its original list position; that's
    # existing, expected XM-file-edit behavior, not something this fix
    # changes.)
    assert set(by_name) == {"FIRST", "SECOND", "THIRD"}
    assert by_name["SECOND"].get_data_lines() == ["2.0"]

    # The Treeview's rows must be re-synced to Memory's real header_addr
    # values, whatever they now are -- not left holding pre-edit
    # header_addrs that no longer identify the same file.
    assert set(tab._tree.get_children()) == {str(f.header_addr) for f in current}

    # And selecting "THIRD" (by its current, possibly-shifted
    # header_addr) must still resolve to the right XMFile, not a stale
    # or missing one -- this is the actual corruption the user hit:
    # editing/removing an earlier entry left later rows unselectable.
    tab._tree.selection_set(str(by_name["THIRD"].header_addr))
    reselected = tab._selected_file()
    assert reselected is not None
    assert reselected.name.rstrip() == "THIRD"


def test_failed_add_does_not_touch_existing_files(root, monkeypatch):
    """A failed *Add* (replacing_addr=None) never removes anything in the
    first place -- pins that the fix doesn't change that path."""
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)

    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    xm.add_file("ONLY", xm.TYPE_DATA, data_lines=["1"])

    tab = XMFilesTab(root)
    tab.render(memory)

    tab._save_new_or_edited_file("BAD", xm.TYPE_DATA, {"data_lines": ["TOOLONGTEXT"]})

    xm = ExtendedMemory(memory, address_range=[0x40, 0x2EF])
    assert [f.name.rstrip() for f in xm.list_files()] == ["ONLY"]


def test_an_unexpected_error_is_reported_and_changes_nothing(root, monkeypatch):
    """An add that fails with something other than a rejection (a bug, as
    in GitHub issue #68, where an IndexError left the Add dialog open with
    nothing reported) still gets an error box, and memory is untouched."""
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a))

    memory = Memory.from_file(DATA_DIR / "xm0-full.dm41")
    before = memory.to_string()
    tab = XMFilesTab(root)
    tab.render(memory)

    def broken_place_file(self, *args, **kwargs):
        self.set_register(0x041, self.get_register(0x0BF))  # a partial write
        raise IndexError("list index out of range")

    monkeypatch.setattr(ExtendedMemory, "_place_file", broken_place_file)
    tab._save_new_or_edited_file("NEW", ExtendedMemory.TYPE_DATA, {"data_lines": ["1"]})

    assert len(errors) == 1
    assert "list index out of range" in errors[0][1]
    assert memory.to_string() == before
