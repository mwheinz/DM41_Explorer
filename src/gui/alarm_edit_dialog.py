"""
Modal dialog for adding or editing one alarm (memory/alarms.py).

Mirrors gui/xm_file_dialog.py's shape: `on_save(kwargs)` is called with
keyword arguments suitable for `Alarms.add_alarm()` if the user saves.
`existing`, if given, is an Alarm already in memory -- its fields are used
as the defaults; editing is implemented as remove-then-add at the tab
level (see gui/alarms_tab.py._save_new_or_edited_alarm()), the same
pattern gui/xm_files_tab.py already uses for XM files, since an edit can
change which registers (and how many) the entry needs.
"""

import datetime
from tkinter import messagebox
import customtkinter as ctk

from memory import Alarm
from gui.dialog_common import build_dialog_button_row, scaled
from gui.contrast import SECONDARY_TEXT
from gui.tab_common import ui_font

_TYPE_LABELS = {
    Alarm.TYPE_MESSAGE: "Message",
    Alarm.TYPE_CONTROL: "Control (runs a program)",
    Alarm.TYPE_CONDITIONAL: "Conditional (runs a program only if the calculator is off)",
}
_TYPE_BY_LABEL = {v: k for k, v in _TYPE_LABELS.items()}


class AlarmEditDialog(ctk.CTkToplevel):
    """Blocking modal dialog to add a new alarm, or edit an existing one."""

    def __init__(self, master, on_save, *, existing: Alarm = None):
        super().__init__(master)
        self._on_save = on_save
        self._editing = existing is not None

        self.title("Edit Alarm" if self._editing else "Add Alarm")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        when = existing.trigger_time if existing else datetime.datetime.now()

        ctk.CTkLabel(self, text="Trigger date (YYYY-MM-DD):").pack(
            anchor="w", padx=16, pady=(16, 4)
        )
        self._date_var = ctk.StringVar(value=when.strftime("%Y-%m-%d"))
        ctk.CTkEntry(self, textvariable=self._date_var, width=scaled(320)).pack(
            anchor="w", padx=16
        )

        ctk.CTkLabel(self, text="Trigger time (24-hour HH:MM:SS):").pack(
            anchor="w", padx=16, pady=(12, 4)
        )
        self._time_var = ctk.StringVar(value=when.strftime("%H:%M:%S"))
        ctk.CTkEntry(self, textvariable=self._time_var, width=scaled(320)).pack(
            anchor="w", padx=16
        )

        ctk.CTkLabel(self, text="Type:").pack(anchor="w", padx=16, pady=(12, 4))
        type_default = _TYPE_LABELS[existing.alarm_type if existing else Alarm.TYPE_MESSAGE]
        self._type_var = ctk.StringVar(value=type_default)
        ctk.CTkOptionMenu(
            self,
            values=list(_TYPE_LABELS.values()),
            variable=self._type_var,
            command=self._on_type_changed,
            width=scaled(320),
        ).pack(anchor="w", padx=16, fill="x")

        self._text_label = ctk.CTkLabel(self, text="Message (0-24 characters):")
        self._text_label.pack(anchor="w", padx=16, pady=(12, 4))
        self._text_var = ctk.StringVar(value=existing.text if existing else "")
        ctk.CTkEntry(self, textvariable=self._text_var, width=scaled(320)).pack(
            anchor="w", padx=16
        )
        ctk.CTkLabel(
            self,
            text=(
                "FOCAL has no lowercase letters above 'e' -- use uppercase, "
                "or a \\nnn trigraph (docs/trigraphs.md) for a special character."
            ),
            font=ui_font(-2),
            text_color=SECONDARY_TEXT,
            wraplength=scaled(440),
            justify="left",
        ).pack(anchor="w", padx=16)
        self._on_type_changed(type_default)

        self._repeat_var = ctk.BooleanVar(
            value=existing is not None and existing.repeat_interval is not None
        )
        ctk.CTkCheckBox(
            self,
            text="Repeats",
            variable=self._repeat_var,
            command=self._on_repeat_toggled,
        ).pack(anchor="w", padx=16, pady=(16, 4))

        interval = existing.repeat_interval if existing else None
        days = interval.days if interval else 0
        rem_seconds = interval.seconds if interval else 0
        hours, rem_seconds = divmod(rem_seconds, 3600)
        minutes, seconds = divmod(rem_seconds, 60)

        self._repeat_row = ctk.CTkFrame(self, fg_color="transparent")
        self._repeat_row.pack(anchor="w", padx=16, pady=(0, 4))
        self._days_var = ctk.StringVar(value=str(days))
        self._hours_var = ctk.StringVar(value=str(hours))
        self._minutes_var = ctk.StringVar(value=str(minutes))
        self._seconds_var = ctk.StringVar(value=str(seconds))
        for label, var in (
            ("Days", self._days_var),
            ("Hours", self._hours_var),
            ("Minutes", self._minutes_var),
            ("Seconds", self._seconds_var),
        ):
            col = ctk.CTkFrame(self._repeat_row, fg_color="transparent")
            col.pack(side="left", padx=(0, 8))
            ctk.CTkLabel(col, text=label).pack(anchor="w")
            ctk.CTkEntry(col, textvariable=var, width=scaled(60)).pack(anchor="w")
        self._on_repeat_toggled()

        self._past_due_var = ctk.BooleanVar(
            value=existing.past_due if existing else False
        )
        ctk.CTkCheckBox(
            self,
            text="Past due (won't auto-fire until reactivated -- see the Owner's Manual)",
            variable=self._past_due_var,
        ).pack(anchor="w", padx=16, pady=(4, 16))

        build_dialog_button_row(
            self,
            primary_text="Save",
            on_primary=self._on_save_clicked,
            pack_kwargs={"padx": 16, "pady": (0, 16), "fill": "x", "side": "bottom"},
        )

    def _on_type_changed(self, label_value):
        alarm_type = _TYPE_BY_LABEL[label_value]
        if alarm_type == Alarm.TYPE_MESSAGE:
            self._text_label.configure(
                text="Message (0-24 characters, FOCAL charset -- see below):"
            )
        else:
            self._text_label.configure(
                text=(
                    "Global label (0-22 characters, FOCAL charset -- see below; "
                    "empty resumes the current program line):"
                )
            )

    def _on_repeat_toggled(self):
        state = "normal" if self._repeat_var.get() else "disabled"
        for child in self._repeat_row.winfo_children():
            for entry in child.winfo_children():
                if isinstance(entry, ctk.CTkEntry):
                    entry.configure(state=state)

    def _on_save_clicked(self):
        date_str = self._date_var.get().strip()
        time_str = self._time_var.get().strip()
        try:
            trigger_time = datetime.datetime.strptime(
                f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            messagebox.showerror(
                "Invalid Date/Time",
                "Enter the date as YYYY-MM-DD and the time as 24-hour HH:MM:SS.",
            )
            return

        alarm_type = _TYPE_BY_LABEL[self._type_var.get()]
        text = self._text_var.get()

        repeat_interval = None
        if self._repeat_var.get():
            try:
                days = int(self._days_var.get() or 0)
                hours = int(self._hours_var.get() or 0)
                minutes = int(self._minutes_var.get() or 0)
                seconds = int(self._seconds_var.get() or 0)
            except ValueError:
                messagebox.showerror(
                    "Invalid Repeat Interval",
                    "Days/Hours/Minutes/Seconds must be whole numbers.",
                )
                return
            if days < 0 or hours < 0 or minutes < 0 or seconds < 0:
                messagebox.showerror(
                    "Invalid Repeat Interval", "Values can't be negative."
                )
                return
            repeat_interval = datetime.timedelta(
                days=days, hours=hours, minutes=minutes, seconds=seconds
            )
            if repeat_interval.total_seconds() <= 0:
                messagebox.showerror(
                    "Invalid Repeat Interval",
                    "A repeating alarm needs an interval greater than zero.",
                )
                return

        kwargs = {
            "trigger_time": trigger_time,
            "alarm_type": alarm_type,
            "text": text,
            "repeat_interval": repeat_interval,
            "past_due": self._past_due_var.get(),
        }

        # Save-time failures that can only be known once the write is
        # attempted (e.g. no room left in the alarm buffer, or an
        # over-length message/label) are caught and reported by the
        # caller (see gui/alarms_tab.py._save_new_or_edited_alarm()),
        # matching gui/xm_file_dialog.py's identical division of labor --
        # this dialog only validates what it can check locally (the
        # date/time and repeat-interval parsing above).
        self._on_save(kwargs)
        self.destroy()
