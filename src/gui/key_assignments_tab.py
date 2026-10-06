"""
Key Assignments tab: two synchronized keypad-shaped grids ("DM41L" and
"HP41", docs/key_assignments.md sec 6 item 4), each on its own sub-tab
(issue #39 -- DM41L first, HP41 second), for viewing and editing
key assignments -- both the built-in/peripheral kind (sec 4.2, stored in
the Key Assignment Registers) and global-label/program assignments (sec
4.6, stored inside the program's own header instead). Both grids render
the exact same 34 assignable keys, just laid out to match a different
physical keyboard -- an edit made through either grid is immediately
reflected in the other, since both are just two different arrangements of
the same Memory calls: get_key_assignment()/set_key_assignment()/
delete_key_assignment() for the first kind, get_program_for_key()/
set_program_key_assignment()/clear_program_key_assignment() for the
second (see gui/key_assignment_edit_dialog.py's "Program" tab).

Per the real lookup order (docs sec 4.7), a Key Assignment Register entry
always takes priority over a global-label one on the same key -- this
tab's own writes never let both exist on one key at once (see
KeyAssignments.set_assignment()/ProgramMemory.set_program_key_assignment()'s mutual-
exclusion docstrings), but _resolve_key() below still checks the register
first when deciding what to display, matching that real priority in case
a dump imported from elsewhere is in an inconsistent state.

Import/export of key assignments is still out of scope -- see
docs/key_assignments.md sec 6 items 1-2.

Unlike every other tab's render(), this one does NOT destroy and rebuild
its widgets on every call. Every other tab's row count scales with actual
data (XM files, programs, ...), so a full rebuild is cheap; this tab
always has the same fixed 34*2 = 68 buttons per grid regardless of how
many keys are actually assigned, and CustomTkinter widget construction
(each CTkButton draws its own rounded-rect image) is expensive enough
that destroying and recreating ~270 widgets on every single key edit was
visibly slow -- the whole tab would go blank for a couple of seconds
before redrawing. The grids are now built once and subsequently only
their existing buttons' text/color are updated in place via configure().
"""

import logging
import customtkinter as ctk

from memory import Memory
from gui.key_assignment_edit_dialog import KeyAssignmentEditDialog
from gui.scroll_support import bind_touchpad_scroll
from gui.tab_common import build_tab_header, build_caption_label, CARD_FG, CARD_BORDER

logger = logging.getLogger(__name__)

# Physical keyboard layouts (docs/key_assignments.md sec 6 item 4): each
# row is a list of cells, where an int is an assignable key number (`MN`
# notation, sec 2) and a str is a non-assignable physical key's label
# (empty string for a genuinely blank/spare position). Both layouts cover
# the same 34 assignable keys -- see the docs section for why they're laid
# out differently (the DM41L's actual compact keyboard relocates several
# keys relative to the classic HP-41's row layout).
HP41_LAYOUT = [
    [11, 12, 13, 14, 15],
    [21, 22, 23, 24, 25],
    ["SHIFT", 32, 33, 34, 35],
    [41, 41, 42, 43, 44],
    [51, 52, 53, 54],
    [61, 62, 63, 64],
    [71, 72, 73, 74],
    [81, 82, 83, 84],
]

DM41L_LAYOUT = [
    [11, 12, 13, 14, 15, 42, 51, 52, 53, 54],
    [21, 22, 23, 24, 25, 43, 61, 62, 63, 64],
    ["USR", "PGM", 32, 35, 44, 41, 71, 72, 73, 74],
    ["ON", "SHIFT", "ALPHA", 33, 34, 41, 81, 82, 83, 84],
]

# Sub-tab names for the two layouts (issue #39), in display order.
DM41L_TAB = "DM41L"
HP41_TAB = "HP41"

UNASSIGNED_TEXT = "gray50"
UNASSIGNED_FG = ("gray85", "gray24")

# An assignment whose key flag is clear (what LKAOFF leaves behind): shown
# like any other assignment, but in amber and marked with a warning sign,
# since the calculator treats the key as unassigned.
FLAG_CLEAR_TEXT = ("#a35b00", "#e6a23c")
FLAG_CLEAR_MARK = "⚠"

# Background colors for the fixed non-assignable physical-key cells
# rendered by _build_static_cell() (issue #24): the color is the CELL's
# fg_color (the CTkFrame tile itself), not the label's text -- CTkLabel's
# own fg_color defaults to "transparent" (confirmed against customtkinter's
# theme JSON), so it always shows whatever color the surrounding cell frame
# is painted, the same way CARD_FG paints every other cell. SHIFT and ALPHA
# each get one color used in both appearance modes, per the issue (no
# light/dark split was asked for those two); ON/USR/PGM get a light/dark
# tuple, matching the issue's explicit "dark grey in light mode, light grey
# in dark mode" ask.
SHIFT_BG_COLOR = "#FFD700"  # gold yellow
ALPHA_BG_COLOR = "#ADD8E6"  # light blue
NON_ASSIGNABLE_BG_COLOR = ("gray30", "gray70")  # dark grey / light grey

STATIC_CELL_BG_COLORS = {
    "SHIFT": SHIFT_BG_COLOR,
    "ALPHA": ALPHA_BG_COLOR,
    "ON": NON_ASSIGNABLE_BG_COLOR,
    "USR": NON_ASSIGNABLE_BG_COLOR,
    "PGM": NON_ASSIGNABLE_BG_COLOR,
}

# Label text colors to match each background above -- dark text reads on
# the light SHIFT/ALPHA backgrounds; NON_ASSIGNABLE's text is the inverse
# of its own tuple (light text on the dark-grey light-mode background,
# dark text on the light-grey dark-mode background), not a copy of it.
STATIC_CELL_TEXT_COLORS = {
    "SHIFT": "gray10",
    "ALPHA": "gray10",
    "ON": ("gray90", "gray10"),
    "USR": ("gray90", "gray10"),
    "PGM": ("gray90", "gray10"),
}

# Sized for the actual function-name lengths in memory/functions.py (median
# 4 characters, longest 7, e.g. "RCLFLAG") rather than the widest string
# that could ever appear -- a handful of long names may render a touch
# wider than this minimum, which is fine; a fixed width big enough for the
# rare 7-character name made every column far wider than it needed to be
# and pushed the DM41L grid's 10 columns past the visible area.
KEY_BUTTON_WIDTH = 62
KEY_BUTTON_HEIGHT = 20
KEY_BUTTON_FONT_SIZE = 14


def _program_names(memory: Memory) -> list:
    """Every assignable global label's name, alphabetical -- the Program
    tab's picker list. A set first in case a dump has a duplicate label
    name (list_global_chain() doesn't assume uniqueness). Key assignments
    live on one label's own header (sec 4.6/5.2) regardless of how many
    labels its program has, so this works off the flat per-label chain,
    not the grouped list_programs()."""
    return sorted({p.name for p in memory.programs.list_global_chain() if p.is_named})


def _count_all_assignments(memory: Memory) -> int:
    """Total key assignments of both kinds (sec 4.1): Key Assignment
    Register entries plus global labels that currently have a key
    assignment -- the header count used to just be the first of these,
    silently omitting any global-label assignments."""
    program_count = sum(
        1 for p in memory.programs.list_global_chain() if p.is_named and p.key_assignment
    )
    return len(memory.key_assignments.list_assignments()) + program_count


class KeyAssignmentsTab(ctk.CTkFrame):
    """Renders the HP41/DM41L key-assignment grids for a Memory object.
    Call `render(memory)` whenever the buffer changes."""

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self._memory: Memory = None
        self._on_change = on_change
        self._grids_built = False
        # (key_number, shifted) -> list of CTkButton, populated once by
        # _build_grid() and reused by _refresh_buttons() from then on. A
        # list, not a single button, because the DM41L and HP41 layouts
        # both reference the same 34 key numbers (just arranged
        # differently) -- each key number maps to one button per grid, and
        # both need to stay in sync. Order within each list follows grid
        # build order in render(): DM41L button first, HP41 second.
        self._key_buttons = {}

        _, self._header_label = build_tab_header(self)

        self._caption = build_caption_label(
            self,
            "Click a key's unshifted or shifted function to assign, "
            "reassign, or delete it -- a built-in/peripheral function "
            "or a global program (marked ▸). An assignment marked ⚠ has "
            "its key flag clear, so the calculator treats the key as "
            "unassigned, as after LKAOFF. Import/export of key "
            "assignments isn't handled here yet.",
        )

        # Issue #39: one sub-tab per keyboard layout, instead of both
        # grids stacked in a single scrolling frame. The DM41L layout comes
        # first since it's the keyboard actually in the user's hand; the
        # classic HP41 layout is second. CTkTabview keeps whichever sub-tab
        # was last selected across render() calls, since render() never
        # rebuilds the tabview itself (see the module docstring).
        self._layout_tabs = ctk.CTkTabview(self)
        self._layout_tabs.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._dm41l_frame = self._build_layout_tab(DM41L_TAB)
        self._hp41_frame = self._build_layout_tab(HP41_TAB)

        # A throwaway button, never packed/gridded, just to read back
        # CustomTkinter's own theme defaults for fg_color/text_color --
        # needed so an "unassigned" cell's overridden colors can be reset
        # to "whatever a normal button looks like" rather than a
        # hard-coded guess that could drift from the active theme.
        probe = ctk.CTkButton(self)
        self._default_fg_color = probe.cget("fg_color")
        self._default_text_color = probe.cget("text_color")
        probe.destroy()

    def _build_layout_tab(self, name: str):
        """Adds one sub-tab to self._layout_tabs and returns the empty
        frame its keypad grid will be built into (by _build_grid(), on the
        first render()). Each sub-tab gets its own scrollable frame -- the
        8-row HP41 grid can be taller than a small window. Having one
        bind_touchpad_scroll() per frame is safe: its handler skips any
        frame that isn't currently mapped, and CTkTabview unmaps every
        sub-tab except the selected one."""
        tab = self._layout_tabs.add(name)
        scroll = ctk.CTkScrollableFrame(tab)
        scroll.pack(fill="both", expand=True)
        bind_touchpad_scroll(scroll)
        grid_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        grid_frame.pack(anchor="w", padx=4, pady=4)
        return grid_frame

    def _notify_change(self):
        if self._on_change:
            self._on_change()

    def render(self, memory: Memory):
        self._memory = memory

        if memory is None:
            self._header_label.configure(text="(no memory dump loaded)")
            if self._grids_built:
                self._teardown_grids()
            return

        try:
            count = _count_all_assignments(memory)
        except Exception as e:
            logger.warning("Could not list key assignments: %s", e)
            self._header_label.configure(text=f"Could not list key assignments: {e}")
            return

        self._header_label.configure(text=self._header_text(count))

        if not self._grids_built:
            self._build_grid(self._dm41l_frame, DM41L_LAYOUT)
            self._build_grid(self._hp41_frame, HP41_LAYOUT)
            self._grids_built = True

        self._refresh_buttons()

    def _header_text(self, count: int) -> str:
        """"Key assignments: N", plus how many of them have their key flag
        clear (see KeyAssignments.flag_clear_assignments()) when any do."""
        text = f"Key assignments: {count}"
        clear = len(self._memory.key_assignments.flag_clear_assignments())
        if clear:
            text += f" -- {clear} with the key flag clear (LKAOFF)"
        return text

    def _teardown_grids(self):
        for widget in self._dm41l_frame.winfo_children():
            widget.destroy()
        for widget in self._hp41_frame.winfo_children():
            widget.destroy()
        self._key_buttons = {}
        self._grids_built = False

    # -- Grid construction (once) -------------------------------------------

    def _build_grid(self, parent, layout):
        for row_index, row in enumerate(layout):
            for col_index, cell in enumerate(row):
                if isinstance(cell, int):
                    self._build_key_cell(parent, cell, row_index, col_index)
                else:
                    self._build_static_cell(parent, cell, row_index, col_index)

    def _build_key_cell(self, parent, key_number: int, row: int, col: int):
        cell = ctk.CTkFrame(
            parent,
            fg_color=CARD_FG,
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=6,
        )
        cell.grid(row=row, column=col, padx=2, pady=2, sticky="nsew")

        ctk.CTkLabel(
            cell,
            text=f"{key_number:02d}",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(pady=(0, 0))

        for shifted in (False, True):
            btn = ctk.CTkButton(
                cell,
                text="",
                width=KEY_BUTTON_WIDTH,
                height=KEY_BUTTON_HEIGHT,
                font=ctk.CTkFont(size=KEY_BUTTON_FONT_SIZE),
                command=lambda k=key_number, s=shifted: self._edit_key(k, s),
            )
            btn.pack(padx=3, pady=(0, 3 if shifted else 1))
            self._key_buttons.setdefault((key_number, shifted), []).append(btn)

    def _build_static_cell(self, parent, label: str, row: int, col: int):
        cell = ctk.CTkFrame(
            parent,
            fg_color=STATIC_CELL_BG_COLORS.get(label, CARD_FG),
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=6,
        )
        cell.grid(row=row, column=col, padx=2, pady=2, sticky="nsew")
        ctk.CTkLabel(
            cell,
            text=label,
            text_color=STATIC_CELL_TEXT_COLORS.get(label, "gray50"),
            font=ctk.CTkFont(size=10),
            width=KEY_BUTTON_WIDTH,
            height=44,
        ).pack(expand=True)

    # -- Refresh (every render) ----------------------------------------------

    def _resolve_key(self, key_number: int, shifted: bool):
        """Returns (assignment, program) for key_number/shifted --
        `assignment` is get_key_assignment()'s dict or None, `program` is
        get_program_for_key()'s ProgramInfo or None. Only one is ever
        non-None for a dump this tab itself wrote (memory.py's
        set_key_assignment()/set_program_key_assignment() enforce mutual
        exclusion on save), but the Key Assignment Register lookup is
        still checked first -- matching the real priority order (docs sec
        4.7) -- in case a dump from elsewhere has both."""
        assignment = self._memory.key_assignments.get_assignment(key_number, shifted)
        if assignment:
            return assignment, None
        return None, self._memory.programs.get_program_for_key(key_number, shifted)

    def _refresh_buttons(self):
        flag_clear = set(self._memory.key_assignments.flag_clear_assignments())
        for (key_number, shifted), btns in self._key_buttons.items():
            assignment, program = self._resolve_key(key_number, shifted)
            prefix = "⇧" if shifted else ""
            if assignment or program:
                fg_color = self._default_fg_color
                text_color = self._default_text_color
                if (key_number, shifted) in flag_clear:
                    prefix = FLAG_CLEAR_MARK + prefix
                    text_color = FLAG_CLEAR_TEXT
                if assignment:
                    text = f"{prefix}{assignment['name']}"
                else:
                    # "▸" marks a global-program assignment, distinct from
                    # a built-in/peripheral function -- see the tab caption.
                    text = f"{prefix}▸{program.name}"
            else:
                text = f"{prefix}--"
                fg_color = UNASSIGNED_FG
                text_color = UNASSIGNED_TEXT
            for btn in btns:
                btn.configure(text=text, fg_color=fg_color, text_color=text_color)

    # -- Editing ------------------------------------------------------------

    def _edit_key(self, key_number: int, shifted: bool):
        assignment, program = self._resolve_key(key_number, shifted)
        program_names = _program_names(self._memory)

        def update_header():
            self._header_label.configure(
                text=self._header_text(_count_all_assignments(self._memory))
            )

        def save(kind, value):
            try:
                if kind == "function":
                    self._memory.key_assignments.set_assignment(key_number, shifted, value)
                    logger.info(
                        "Key %02d (%s) assigned function: %s",
                        key_number,
                        "shifted" if shifted else "unshifted",
                        value,
                    )
                else:  # "program"
                    self._memory.programs.set_program_key_assignment(value, key_number, shifted)
                    logger.info(
                        "Key %02d (%s) assigned program: %s",
                        key_number,
                        "shifted" if shifted else "unshifted",
                        value,
                    )
                self._notify_change()
                self._refresh_buttons()
                update_header()
            except Exception as e:
                logger.error("Failed to save key assignment: %s", str(e))


        def delete():
            # Clear both storage mechanisms -- normally only one is ever
            # actually populated (see _resolve_key()), but this is cheap
            # and safe (both calls no-op when there's nothing to clear)
            # and avoids leaving a stale assignment behind on a dump
            # that's somehow in an inconsistent state.
            try:
                self._memory.key_assignments.delete_assignment(key_number, shifted)
                if program is not None:
                    self._memory.programs.clear_program_key_assignment(program.name)
                logger.info(
                    "Key %02d (%s) assignment deleted",
                    key_number,
                    "shifted" if shifted else "unshifted",
                )
                self._notify_change()
                self._refresh_buttons()
                update_header()
            except Exception as e:
                logger.error("Failed to delete key assignment: %s", str(e))

        KeyAssignmentEditDialog(
            self,
            key_number,
            shifted,
            assignment,
            program,
            program_names,
            save,
            delete,
            flag_clear=(key_number, shifted)
            in self._memory.key_assignments.flag_clear_assignments(),
        )
