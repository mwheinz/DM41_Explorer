"""
Regression test for gui/alarms_tab.py's edit-save path
(_save_new_or_edited_alarm()) -- user-reported 2026-09-02: entering an
invalid message while editing an alarm ("added an invalid character")
left the Alarms tab's list uneditable/unselectable for that alarm and
every alarm after it in the list.

Root cause: editing is implemented as delete_alarm(old) then
add_alarm(new) (see gui/alarms_tab.py's own docstring). When the
add_alarm() half raised (a bad trigraph, an over-length message, ...),
the except handler showed an error and returned WITHOUT re-rendering.
The old alarm was already gone from Memory (delete_alarm() closes the
gap, shifting every later alarm's start_addr down), but the Treeview
still held its pre-edit rows under their now-stale start_addr-as-iid
values -- so selecting the edited row (or anything after it) looked up
an address that either didn't exist anymore or now belonged to a
different alarm.

Requires a real Tk display (Xvfb in CI/sandboxes) -- same requirement as
test_xm_import_export.py/test_app.py.
"""

import datetime

import pytest

pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")

from tkinter import messagebox
import customtkinter as ctk

from memory import Memory, Alarm
from gui.alarms_tab import AlarmsTab


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def _dt(*args):
    return datetime.datetime(*args)


def test_failed_edit_restores_the_original_alarm_and_leaves_others_selectable(
    root, monkeypatch
):
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)

    memory = Memory()
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 1), text="FIRST")
    target = memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 2), text="SECOND")
    later = memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 3), text="THIRD")

    tab = AlarmsTab(root)
    tab.render(memory)
    # Sanity check: the tab's rows are keyed by start_addr, as of before
    # the failed edit.
    assert set(tab._tree.get_children()) == {
        str(a.start_addr) for a in memory.alarms.list_alarms()
    }

    # Simulate the user editing "SECOND" and typing an invalid trigraph
    # ("\\" alone -- not a known shorthand or a full \nnn escape).
    tab._save_new_or_edited_alarm(
        {
            "trigger_time": target.trigger_time,
            "text": "BROKEN\\",
            "alarm_type": Alarm.TYPE_MESSAGE,
            "repeat_interval": None,
            "past_due": False,
        },
        replacing_addr=target.start_addr,
    )

    current = memory.alarms.list_alarms()
    # The original alarm must still be there -- a failed edit shouldn't
    # be able to delete data the user never asked to remove.
    assert [a.text for a in current] == ["FIRST", "SECOND", "THIRD"]

    # And the Treeview's rows must be re-synced to Memory's real
    # start_addr values -- both "SECOND" and "THIRD" (everything at or
    # after the deleted-then-restored entry) would have shifted if the
    # restore had landed at a different address than before.
    assert set(tab._tree.get_children()) == {str(a.start_addr) for a in current}

    # "THIRD"'s own start_addr is untouched by any of this -- restoring
    # "SECOND" with its exact original fields re-creates an
    # identically-sized entry at the same slot, so nothing after it
    # should have moved either.
    assert current[2].start_addr == later.start_addr

    # And selecting/editing the later alarm ("THIRD") must still resolve
    # to the right Alarm object, not a stale or missing one.
    tab._tree.selection_set(str(later.start_addr))
    reselected = tab._selected_alarm()
    assert reselected is not None
    assert reselected.text == "THIRD"


def test_failed_add_does_not_touch_existing_alarms(root, monkeypatch):
    """A failed *Add* (replacing_addr=None) never deletes anything in the
    first place -- this pins that the fix doesn't change that path."""
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)

    memory = Memory()
    memory.alarms.add_alarm(trigger_time=_dt(2026, 9, 1), text="ONLY")

    tab = AlarmsTab(root)
    tab.render(memory)

    tab._save_new_or_edited_alarm(
        {
            "trigger_time": _dt(2026, 9, 5),
            "text": "BAD\\",
            "alarm_type": Alarm.TYPE_MESSAGE,
            "repeat_interval": None,
            "past_due": False,
        },
    )

    assert [a.text for a in memory.alarms.list_alarms()] == ["ONLY"]
