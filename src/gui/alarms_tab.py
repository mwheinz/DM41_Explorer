"""
Alarms tab: view, add, edit, and remove alarms (memory/alarms.py).

Follows the same pattern gui/xm_files_tab.py established for GitHub issue
#21 -- a single ttk.Treeview rather than one CTk widget per row, with
Add/Edit/Remove living in the header and acting on whichever row is
selected. Unlike XM files or programs, the buffer itself is always kept
sorted by trigger time (docs/alarms.md sec 5) -- Alarms.add_alarm() does
that insertion, so this tab never needs to reorder anything itself.
"""

import logging
from tkinter import messagebox
import customtkinter as ctk

from memory import Memory, DM41MemoryError
from gui.alarm_edit_dialog import AlarmEditDialog
from gui.tab_common import (
    build_tab_header,
    build_tab_treeview,
    style_treeview,
    apply_row_tags,
    highlight_selected_row,
    clear_tree_for_render,
)

logger = logging.getLogger(__name__)

# Distinct ttk style name for this tab's Treeview -- see
# gui/tab_common.py's style_treeview() docstring for why each Treeview-
# based tab needs its own, separately-named style.
_TREE_STYLE = "Alarms.Treeview"

_TREE_COLUMNS = [
    ("time", "Trigger Time", 150, False),
    ("repeat", "Repeats", 110, False),
    ("type", "Type", 90, False),
    ("text", "Message / Label", 220, True),
    ("status", "Status", 90, False),
]


def _format_repeat(interval) -> str:
    if interval is None:
        return ""
    total_seconds = int(interval.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return "every " + " ".join(parts)


def _alarm_to_add_kwargs(alarm):
    """The kwargs Alarms.add_alarm() needs to recreate `alarm` exactly as
    it currently is -- used by _save_new_or_edited_alarm() to restore an
    alarm it had to remove before discovering the replacement was
    invalid."""
    return {
        "trigger_time": alarm.trigger_time,
        "alarm_type": alarm.alarm_type,
        "text": alarm.text,
        "repeat_interval": alarm.repeat_interval,
        "past_due": alarm.past_due,
    }


class AlarmsTab(ctk.CTkFrame):
    """Renders the alarm list for a Memory object. Call `render(memory)`
    whenever the buffer changes."""

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self._memory: Memory = None
        self._on_change = on_change

        header, self._header_label = build_tab_header(
            self,
            button_kwargs={"text": "Add Alarm", "width": 100, "command": self._add_alarm},
        )
        edit_button = ctk.CTkButton(
            header, text="Edit", width=90, command=self._edit_selected
        )
        edit_button.pack(side="right", padx=(0, 8))
        remove_button = ctk.CTkButton(
            header,
            text="Remove",
            width=90,
            fg_color="#a03e3e",
            hover_color="#832f2f",
            command=self._remove_selected,
        )
        remove_button.pack(side="right", padx=(0, 8))

        _, self._tree = build_tab_treeview(self, _TREE_COLUMNS, style=_TREE_STYLE)

        self._tree.bind("<Double-1>", lambda e: self._edit_selected())
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_selected)

        def update_action_buttons(selected):
            state = "normal" if selected is not None else "disabled"
            edit_button.configure(state=state)
            remove_button.configure(state=state)

        self._update_action_buttons = update_action_buttons
        self._update_action_buttons(None)

    def refresh_theme(self):
        """Re-applies theme-dependent ttk styling/colors -- see
        gui/xm_files_tab.py's identical method."""
        self._stripe_bg = style_treeview(_TREE_STYLE)
        apply_row_tags(self._tree, self._stripe_bg)

    def _notify_change(self):
        if self._on_change:
            self._on_change()

    def _on_tree_selected(self, event=None):  # pylint: disable=unused-argument
        highlight_selected_row(self._tree)
        self._update_action_buttons(self._selected_alarm())

    def _selected_start_addr(self):
        selection = self._tree.selection()
        return int(selection[0]) if selection else None

    def _selected_alarm(self):
        """The Alarm currently selected in the table, re-fetched fresh
        from Memory rather than cached -- same reasoning as
        gui/xm_files_tab.py._selected_file(): an alarm's exact position
        can only be trusted as of the last render(), and start_addr is
        this tab's stable per-alarm identity (also used as each row's
        Treeview iid)."""
        addr = self._selected_start_addr()
        if addr is None or self._memory is None:
            return None
        return self._memory.alarms.get_alarm(addr)

    def render(self, memory: Memory):
        self._memory = memory
        if clear_tree_for_render(self._tree, self._header_label, memory):
            self._update_action_buttons(None)
            return

        try:
            alarms = memory.alarms.list_alarms()
        except DM41MemoryError as e:
            logger.warning("Could not list alarms: %s", e)
            self._header_label.configure(text=f"Could not list alarms: {e}")
            self._update_action_buttons(None)
            return

        self._header_label.configure(text=f"Alarms: {len(alarms)}")

        for pos, a in enumerate(alarms):
            status = "Past due" if a.past_due else ""
            self._tree.insert(
                "",
                "end",
                iid=str(a.start_addr),
                values=(
                    a.trigger_time.strftime("%Y-%m-%d %H:%M:%S"),
                    _format_repeat(a.repeat_interval),
                    a.type_label,
                    a.text,
                    status,
                ),
                tags=("oddrow",) if pos % 2 else (),
            )
        self._update_action_buttons(None)

    # -- Add / Edit / Remove ------------------------------------------------

    def _save_new_or_edited_alarm(
        self, kwargs, *, replacing_addr=None, replacing_alarm=None
    ):
        """Shared save path for Add and Edit. Editing is implemented as
        remove-then-add (no in-place resize -- an edit can change how many
        registers the entry needs, e.g. a longer message or a newly-added
        repeat interval), the same pattern gui/xm_files_tab.py uses for XM
        files.

        `replacing_alarm` (the pre-edit Alarm, when this is an edit) is
        what lets a rejected add_alarm() be rolled back below: it's only
        used for restoring the original entry, never to decide where it
        was, since `replacing_addr` already covers that (add_alarm()
        validates the new text/length *before* touching any registers --
        see _build_entry_registers() -- so the delete_alarm() just above
        is the only mutation that can have already happened when this
        fires)."""
        alarms = self._memory.alarms
        try:
            if replacing_addr is not None:
                alarms.delete_alarm(replacing_addr)
            alarms.add_alarm(**kwargs)
        except (ValueError, DM41MemoryError) as e:
            verb = "save" if replacing_addr is not None else "add"
            logger.warning("Could not %s alarm: %s", verb, e)
            if replacing_alarm is not None:
                # The original alarm was already removed above (to make
                # room for the edit) before add_alarm() rejected the
                # replacement -- put it back. Without this, the buffer
                # would be left missing the edited alarm entirely while
                # this tab's Treeview still showed its old row under its
                # now-stale start_addr, and delete_alarm() closing the
                # gap shifts every later alarm's start_addr too --
                # corrupting selection for the edited row *and* every
                # alarm after it until the next full reload.
                try:
                    alarms.add_alarm(**_alarm_to_add_kwargs(replacing_alarm))
                except (ValueError, DM41MemoryError) as restore_error:
                    logger.error(
                        "Could not restore alarm %r after a failed edit "
                        "-- it has been lost from the buffer: %s",
                        replacing_alarm.text,
                        restore_error,
                    )
                # Re-render either way: whatever the buffer now actually
                # contains, the Treeview's iids (each alarm's start_addr)
                # must match it before the user can select anything again.
                self.render(self._memory)
            messagebox.showerror(f"Could Not {verb.title()} Alarm", str(e))
            return
        logger.info(
            "Alarm %s: %r at %s",
            "edited" if replacing_addr is not None else "added",
            kwargs.get("text", ""),
            kwargs.get("trigger_time"),
        )
        self._notify_change()
        self.render(self._memory)

    def _add_alarm(self):
        if self._memory is None:
            messagebox.showwarning(
                "No Memory Loaded", "Load or start a memory buffer first."
            )
            return
        AlarmEditDialog(self, self._save_new_or_edited_alarm)

    def _edit_selected(self):
        alarm = self._selected_alarm()
        if alarm is None:
            messagebox.showinfo("No Selection", "Select an alarm first.")
            return

        def save(kwargs):
            self._save_new_or_edited_alarm(
                kwargs, replacing_addr=alarm.start_addr, replacing_alarm=alarm
            )

        AlarmEditDialog(self, save, existing=alarm)

    def _remove_selected(self):
        alarm = self._selected_alarm()
        if alarm is None:
            messagebox.showinfo("No Selection", "Select an alarm first.")
            return
        label = alarm.text or "(no message)"
        if not messagebox.askyesno("Remove Alarm", f"Remove {label!r}?"):
            return
        try:
            self._memory.alarms.delete_alarm(alarm.start_addr)
        except (ValueError, DM41MemoryError) as e:
            logger.warning("Could not remove alarm: %s", e)
            messagebox.showerror("Could Not Remove Alarm", str(e))
            return
        logger.info("Alarm removed: %r", label)
        self._notify_change()
        self.render(self._memory)
