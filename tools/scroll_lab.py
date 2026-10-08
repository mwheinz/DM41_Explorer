#!/usr/bin/env python3
"""
Scroll tuning bench for GitHub issue #29 -- a stock ttk.Treeview and a
gui/scroll_support.py SmoothTreeview side by side, with every
TreeScrollTuning knob live on screen.

Run it from the repository root (it adds ./src to sys.path itself):

    python3 tools/scroll_lab.py            # 800 rows, like Hex View
    python3 tools/scroll_lab.py --rows 100 # like a short table

Scroll the LEFT table and the RIGHT one with the same gesture and compare.
The left one is Tk's own handling, unchanged -- the multi-row jumps issue
#29 is about. The right one is this project's handler, with whatever the
controls currently say; changes take effect on the next gesture, no
restart.

The log pane shows, for every scroll event either table receives, what Tk
delivered (the raw `%D`, and for a trackpad gesture the pixel deltas
packed inside it) and what the right-hand table did with it. That answers
the question this bench exists for: what magnitudes does this Mac's Tk
actually send, and how many rows does a given tuning turn them into.

Nothing here is imported by the app -- it's a development tool, so it
depends on plain tkinter only (no customtkinter) and starts instantly.
"""

import argparse
import logging
import platform
import sys
import time
import tkinter
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# pylint: disable=wrong-import-position
from gui.scroll_support import (  # noqa: E402
    TREE_SCROLL_TUNING,
    SmoothTreeview,
    decode_touchpad_delta,
)

LOG_LINES = 400  # the log pane keeps this many lines, then drops the oldest

# Candidate tunings, as (label, {field: value}) -- one click each so a
# comparison is a button press rather than four fields of typing.
PRESETS = (
    ("Tk stock-ish", {"touchpad_gain": 22.0, "touchpad_sample": 5, "max_rows": 0}),
    ("1:1 (default)", {"touchpad_gain": 1.0, "touchpad_sample": 1, "max_rows": 24}),
    ("1.5x", {"touchpad_gain": 1.5, "touchpad_sample": 1, "max_rows": 24}),
    ("2x", {"touchpad_gain": 2.0, "touchpad_sample": 1, "max_rows": 24}),
    ("3x, uncapped", {"touchpad_gain": 3.0, "touchpad_sample": 1, "max_rows": 0}),
)


class TextLogHandler(logging.Handler):
    """Mirrors the SmoothTreeview trace log into the window's log pane,
    so the interesting numbers are where the scrolling is rather than in
    the terminal behind it."""

    def __init__(self, write):
        super().__init__(level=logging.DEBUG)
        self._write = write

    def emit(self, record):
        self._write(record.getMessage())


class ScrollLab:
    """The whole bench: two tables, the tuning controls, and the log."""

    def __init__(self, root: tkinter.Tk, rows: int):
        self.root = root
        self.rows = rows
        self.tuning = TREE_SCROLL_TUNING
        # The handler is macOS-only in the app; here it has to run
        # wherever the bench is run, including a Linux sandbox.
        self.tuning.everywhere = True
        self.tuning.trace = True
        self._last_event_at = {}

        root.title("DM41_Explorer scroll lab (issue #29)")
        root.geometry("1100x760")
        ttk.Style(root).configure("Treeview", rowheight=22)

        self._build_controls()
        tables = ttk.Frame(root)
        tables.pack(fill="both", expand=True, padx=8)
        self.stock = self._build_table(tables, "Stock ttk.Treeview", smooth=False)
        self.smooth = self._build_table(tables, "SmoothTreeview", smooth=True)
        self._build_log()
        self._install_trace_logging()
        self._report_environment()

    # -- construction ------------------------------------------------

    def _build_controls(self):
        frame = ttk.LabelFrame(
            self.root, text="Tuning (applies to the right-hand table)"
        )
        frame.pack(fill="x", padx=8, pady=8)

        self.gain = tkinter.DoubleVar(value=self.tuning.touchpad_gain)
        self.sample = tkinter.IntVar(value=self.tuning.touchpad_sample)
        self.wheel = tkinter.DoubleVar(value=self.tuning.wheel_divisor)
        self.max_rows = tkinter.IntVar(value=self.tuning.max_rows)
        self.row_height = tkinter.IntVar(value=self.tuning.row_height)

        self._scale(
            frame,
            0,
            "touchpad_gain",
            self.gain,
            0.25,
            6.0,
            "pixels of content per pixel of finger; 1.0 = one-to-one",
        )
        self._scale(
            frame,
            1,
            "wheel_divisor",
            self.wheel,
            5.0,
            160.0,
            "event.delta per row for a wheel; Tk's own factor is 40",
        )
        self._spin(
            frame,
            2,
            "touchpad_sample",
            self.sample,
            1,
            10,
            "handle every Nth touchpad event; Tk's own binding uses 5",
        )
        self._spin(
            frame,
            3,
            "max_rows",
            self.max_rows,
            0,
            200,
            "most rows one event may scroll; 0 = uncapped",
        )
        self._spin(
            frame,
            4,
            "row_height",
            self.row_height,
            0,
            80,
            "pixels per row; 0 = ask the ttk style (22 here)",
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=4, sticky="w", padx=6, pady=(8, 6))
        for label, values in PRESETS:
            ttk.Button(
                buttons, text=label, command=lambda v=values: self._apply_preset(v)
            ).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Clear log", command=self._clear_log).pack(
            side="left", padx=(18, 0)
        )
        frame.columnconfigure(1, weight=1)

    def _scale(self, frame, row, name, variable, low, high, hint):
        ttk.Label(frame, text=name).grid(row=row, column=0, sticky="w", padx=6, pady=2)
        ttk.Scale(
            frame,
            variable=variable,
            from_=low,
            to=high,
            command=lambda _value: self._apply(),
        ).grid(row=row, column=1, sticky="ew", padx=6)
        value = ttk.Label(frame, width=7, anchor="e")
        value.grid(row=row, column=2, sticky="e")
        variable.trace_add(
            "write", lambda *_: value.configure(text=f"{variable.get():.2f}")
        )
        value.configure(text=f"{variable.get():.2f}")
        ttk.Label(frame, text=hint, foreground="gray40").grid(
            row=row, column=3, sticky="w", padx=6
        )

    def _spin(self, frame, row, name, variable, low, high, hint):
        ttk.Label(frame, text=name).grid(row=row, column=0, sticky="w", padx=6, pady=2)
        ttk.Spinbox(
            frame,
            textvariable=variable,
            from_=low,
            to=high,
            width=6,
            command=self._apply,
        ).grid(row=row, column=1, sticky="w", padx=6)
        ttk.Label(frame, text=hint, foreground="gray40").grid(
            row=row, column=3, sticky="w", padx=6
        )

    def _build_table(self, parent, title: str, *, smooth: bool):
        frame = ttk.LabelFrame(parent, text=title)
        frame.pack(side="left", fill="both", expand=True, padx=(0, 8))
        columns = [("addr", "Address", 90), ("hex", "Bytes", 200), ("note", "Row", 80)]
        factory = SmoothTreeview if smooth else ttk.Treeview
        tree = factory(frame, columns=[c[0] for c in columns], show="headings")
        for column, heading, width in columns:
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w", stretch=(column == "hex"))
        for index in range(self.rows):
            tree.insert(
                "",
                "end",
                values=(
                    f"0x{index:03X}",
                    " ".join(f"{(index + byte) & 0xFF:02X}" for byte in range(7)),
                    index,
                ),
            )
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")
        self._spy_on(tree, title)
        return tree

    def _build_log(self):
        frame = ttk.LabelFrame(self.root, text="Events")
        frame.pack(fill="both", padx=8, pady=8)
        self.log = tkinter.Text(frame, height=12, wrap="none", font=("Courier", 11))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")

    def _install_trace_logging(self):
        logging.getLogger("gui.scroll_support").addHandler(TextLogHandler(self._write))
        logging.getLogger("gui.scroll_support").setLevel(logging.DEBUG)

    # -- behavior ----------------------------------------------------

    def _apply(self, *_args):
        """Pushes the controls into the live tuning. Called on every
        control change; the handler reads these fields per event, so the
        next gesture uses them."""
        try:
            self.tuning.touchpad_gain = max(0.01, float(self.gain.get()))
            self.tuning.touchpad_sample = max(1, int(self.sample.get()))
            self.tuning.wheel_divisor = float(self.wheel.get()) or 40.0
            self.tuning.max_rows = max(0, int(self.max_rows.get()))
            self.tuning.row_height = max(0, int(self.row_height.get()))
        except (tkinter.TclError, ValueError):
            return  # half-typed number in a spinbox; the next change wins
        self._write(
            f"tuning: gain={self.tuning.touchpad_gain:.2f} "
            f"sample={self.tuning.touchpad_sample} "
            f"wheel={self.tuning.wheel_divisor:.1f} "
            f"maxrows={self.tuning.max_rows} "
            f"rowheight={self.tuning.row_height or 'style'}"
        )

    def _apply_preset(self, values: dict):
        self.gain.set(values["touchpad_gain"])
        self.sample.set(values["touchpad_sample"])
        self.max_rows.set(values["max_rows"])
        self._apply()

    def _spy_on(self, tree, title: str):
        """Records (never consumes) the raw events each table receives,
        so the log shows what Tk delivered as well as what the handler
        made of it. add="+" and no "break": the real handlers still run."""
        label = title.split()[0]

        def wheel(event):
            self._write(
                f"[{label}] <MouseWheel> delta={event.delta} {self._rate(label)}"
            )

        def touchpad(event):
            delta_x, delta_y = decode_touchpad_delta(event.delta)
            self._write(
                f"[{label}] <TouchpadScroll> raw={event.delta} "
                f"dx={delta_x} dy={delta_y} {self._rate(label)}"
            )

        tree.bind("<MouseWheel>", wheel, add="+")
        try:
            tree.bind("<TouchpadScroll>", touchpad, add="+")
        except tkinter.TclError:
            pass  # no such event in this Tk build (X11)

    def _rate(self, label: str) -> str:
        """How long since this table's last scroll event -- the other
        half of the tuning question, since a trackpad sends these
        continuously and a wheel sends them one notch at a time."""
        now = time.monotonic()
        previous = self._last_event_at.get(label)
        self._last_event_at[label] = now
        if previous is None:
            return ""
        gap = (now - previous) * 1000
        return f"(+{gap:.0f}ms, {1000 / gap:.0f}/s)" if gap else ""

    def _write(self, line: str):
        self.log.insert("end", line + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > LOG_LINES:
            self.log.delete("1.0", f"{lines - LOG_LINES}.0")
        self.log.see("end")

    def _clear_log(self):
        self.log.delete("1.0", "end")

    def _report_environment(self):
        """What Tk this actually is, and what its own scroll bindings say
        -- the facts the issue #29 analysis rests on, printed from the
        horse's mouth rather than assumed."""
        tcl = self.root.tk
        self._write(f"python {platform.python_version()} on {platform.platform()}")
        self._write(
            f"Tcl/Tk {tcl.call('info', 'patchlevel')}, "
            f"windowingsystem {tcl.call('tk', 'windowingsystem')}, "
            f"{self.rows} rows, style rowheight "
            f"{ttk.Style(self.root).lookup('Treeview', 'rowheight')}"
        )
        for class_name in ("Treeview", "TtkScrollable"):
            try:
                events = self.root.bind_class(class_name)
            except tkinter.TclError as e:
                self._write(f"{class_name}: {e}")
                continue
            for event in events:
                if "Wheel" in event or "Touchpad" in event:
                    script = " ".join(self.root.bind_class(class_name, event).split())
                    self._write(f"  stock {class_name} {event}: {script}")
        self._apply()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--rows",
        type=int,
        default=800,
        help="rows in each table (default 800, Hex View's register count)",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG, format="%(message)s", stream=sys.stdout)
    root = tkinter.Tk()
    ScrollLab(root, arguments.rows)
    root.mainloop()


if __name__ == "__main__":
    main()
