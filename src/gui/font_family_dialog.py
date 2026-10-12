"""
A font family picker: a filter box over a scrolling list (GitHub issue #61).

Preferences used a drop-down menu for each font role's family, but a Tk menu
can't scroll on Linux (X11), where a few hundred installed families run off
the screen, and CustomTkinter opens its menus there in a way that closes
them as soon as the mouse button is released. A list in a dialog has
neither problem, and typing part of a name is quicker than scrolling on
any platform.
"""

import platform
from tkinter import ttk

import customtkinter as ctk

from gui.contrast import SECONDARY_TEXT
from gui.dialog_common import build_dialog_button_row, fit_dialog
from gui.fonts import font_for, font_spec, widget_scaling
from gui.tab_common import (
    apply_row_tags,
    build_tree_with_scrollbar,
    highlight_selected_row,
    style_treeview,
)
from gui.window_geometry import scaled_row_height

PLATFORM_SYSTEM = platform.system()

# Its own ttk style, so the list can be drawn in the ui font -- the tables'
# shared style uses the mono font for their content.
_STYLE = "FontFamilies.Treeview"


class FontFamilyDialog(ctk.CTkToplevel):
    """Modal list of `families` to pick one from, with a filter box.

    `default_label` (e.g. "System Default") heads the list whatever the
    filter, so the default is always one click away. `current` is selected
    to start with. `on_chosen(family)` is called with the row chosen --
    `default_label` itself for the default -- and not at all on Cancel.
    `note`, if given, is shown under the list (e.g. that only fixed-width
    fonts are listed)."""

    def __init__(
        self,
        master,
        title: str,
        families: list,
        *,
        default_label: str,
        current: str,
        on_chosen,
        note: str = None,
    ):
        super().__init__(master)
        self._master = master
        self._families = families
        self._default_label = default_label
        self._current = current
        self._on_chosen = on_chosen

        self.title(title)
        self.transient(master)
        self.grab_set()
        if PLATFORM_SYSTEM == "Darwin" and hasattr(master, "_menubar"):
            self.config(menu=master._menubar)

        self._filter_var = ctk.StringVar()
        self._filter_entry = ctk.CTkEntry(
            self, textvariable=self._filter_var, placeholder_text="Filter"
        )
        self._filter_entry.pack(fill="x", padx=16, pady=(16, 8))

        table = ctk.CTkFrame(self, fg_color="transparent")
        table.pack(fill="both", expand=True, padx=16)
        stripe_bg = style_treeview(_STYLE)
        # The rows are names, not register content: the ui font, not mono.
        ui = font_spec("ui").tk_tuple(widget_scaling(self))
        ttk.Style().configure(
            _STYLE, font=ui, rowheight=scaled_row_height(font_spec("ui").size)
        )
        self._tree, _ = build_tree_with_scrollbar(
            table, [("family", "Family", 260, True)], style=_STYLE
        )
        apply_row_tags(self._tree, stripe_bg)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Double-1>", lambda _event: self._choose())

        if note:
            ctk.CTkLabel(
                self,
                text=note,
                text_color=SECONDARY_TEXT,
                font=font_for("small"),
                anchor="w",
                justify="left",
            ).pack(fill="x", padx=16, pady=(4, 0))

        build_dialog_button_row(
            self,
            primary_text="Choose",
            on_primary=self._choose,
            on_cancel=self._close,
            pack_kwargs={"padx": 16, "pady": (12, 16), "fill": "x"},
        )
        self.bind("<Escape>", lambda _event: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        # Down arrow from the filter box moves into the list.
        self._filter_entry.bind("<Down>", self._into_list)

        self._filter_var.trace_add("write", lambda *_: self._refill())
        self._refill()
        fit_dialog(self, 340, 440, content=False)
        self._filter_entry.focus_set()

    # -- the list ---------------------------------------------------------

    def visible_families(self) -> list:
        """The rows the filter leaves, in order -- the default first."""
        text = self._filter_var.get().strip().casefold()
        matching = [f for f in self._families if text in f.casefold()]
        return [self._default_label] + matching

    def _refill(self):
        tree = self._tree
        tree.delete(*tree.get_children())
        for position, family in enumerate(self.visible_families()):
            tree.insert(
                "",
                "end",
                iid=family,
                values=(family,),
                tags=("oddrow",) if position % 2 else (),
            )
        # Keep the current choice selected while it is still listed.
        if tree.exists(self._current):
            tree.selection_set(self._current)
            tree.see(self._current)

    def _on_select(self, _event=None):
        selection = self._tree.selection()
        if selection:
            self._current = selection[0]
        highlight_selected_row(self._tree)

    def _into_list(self, _event=None):
        children = self._tree.get_children()
        if not children:
            return "break"
        target = self._current if self._tree.exists(self._current) else children[0]
        self._tree.focus_set()
        self._tree.focus(target)
        self._tree.selection_set(target)
        self._tree.see(target)
        return "break"

    # -- closing ----------------------------------------------------------

    def _choose(self):
        selection = self._tree.selection()
        if not selection:
            return
        family = selection[0]
        self._close()
        self._on_chosen(family)

    def _close(self):
        """Closes, handing the modal grab back to the dialog that opened
        this one (Preferences is modal too)."""
        self.grab_release()
        self.destroy()
        if self._master.winfo_exists():
            self._master.grab_set()
