"""
Key Assignments tab: two synchronized keypad-shaped grids ("DM41L" and
"DM41X", docs/key_assignments.md sec 6 item 4), each on its own sub-tab
(issue #39 -- DM41L first, DM41X second), for viewing and editing
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
a state imported from elsewhere is in an inconsistent state.

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
from dataclasses import dataclass

import customtkinter as ctk

from memory import Memory
from memory.functions import SINGLE_BYTE_FUNCTIONS, XROM_FUNCTIONS
from gui.contrast import SECONDARY_TEXT
from gui.key_assignment_edit_dialog import KeyAssignmentEditDialog
from gui.scroll_support import bind_touchpad_scroll
from gui.tab_common import (
    build_tab_header,
    build_caption_label,
    ui_font,
    CARD_FG,
    CARD_BORDER,
)
from gui.window_geometry import scaled_row_height

logger = logging.getLogger(__name__)

# Physical keyboard layouts (docs/key_assignments.md sec 6 item 4): each
# row is a list of cells, where an int is an assignable key number (`MN`
# notation, sec 2) and a str is a non-assignable physical key's label
# (empty string for a genuinely blank/spare position). Both layouts cover
# the same 34 assignable keys -- see the docs section for why they're laid
# out differently (the DM41L's actual compact keyboard relocates several
# keys relative to the classic HP-41's row layout).
#
# The ENTER key (41) has only one MxN position but is a double-size key on
# both keyboards, so it is listed in two neighbouring positions: side by
# side in a row (the DM41X's double-width key) or one above the other in
# the same position of two rows (the DM41L's double-height key). Repeating
# an assignable key number like this makes it ONE key that spans those
# positions -- see layout_placements().
DM41X_LAYOUT = [
    [11, 12, 13, 14, 15, "ON"],
    [21, 22, 23, 24, 25, "USR"],
    ["SHIFT", 32, 33, 34, 35, "ALPHA"],
    [41, 41, 42, 43, "DSP", 44],
    [51, 52, 53, 54, "⬆︎"],
    [61, 62, 63, 64, "⬇︎"],
    [71, 72, 73, 74, "PGM"],
    [81, 82, 83, "CST", 84],
]

DM41L_LAYOUT = [
    [11, 12, 13, 14, 15, 42, 51, 52, 53, 54],
    [21, 22, 23, 24, 25, 43, 61, 62, 63, 64],
    ["USR", "PGM", 32, 35, 44, 41, 71, 72, 73, 74],
    ["ON", "SHIFT", "ALPHA", 33, 34, 41, 81, 82, 83, 84],
]

# Sub-tab names for the two layouts (issue #39), in display order.
DM41L_TAB = "DM41L"
DM41X_TAB = "DM41X"

UNASSIGNED_TEXT = SECONDARY_TEXT
UNASSIGNED_FG = ("gray85", "gray24")

# An assignment whose key flag is clear (what LKAOFF leaves behind): shown
# like any other assignment, but on an amber-brown fill and marked with a
# warning sign, since the calculator treats the key as unassigned. It is a
# fill rather than amber text because amber text on the blue key button is
# unreadable (issue #42); white on this fill is 6.8:1.
FLAG_CLEAR_FG = ("#8a4b00", "#8a4b00")
FLAG_CLEAR_TEXT = "#ffffff"
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
    "DSP": NON_ASSIGNABLE_BG_COLOR,
    "CST": NON_ASSIGNABLE_BG_COLOR,
    "⬆︎": NON_ASSIGNABLE_BG_COLOR,
    "⬇︎": NON_ASSIGNABLE_BG_COLOR,
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
    "DSP": ("gray90", "gray10"),
    "CST": ("gray90", "gray10"),
    "⬆︎": ("gray90", "gray10"),
    "⬇︎": ("gray90", "gray10"),
}

# Key sizing (GitHub issue #41). Everything on the keys follows the
# application font (Preferences > Font): the key number, the assignment text
# and the labels of the fixed physical keys all use it, and a key button is
# never narrower than KEY_MIN_CHARS characters of that font -- so a long
# function name (the longest are 7 characters, e.g. "RCLFLAG") doesn't make
# its whole column of keys wider than the others.
KEY_MIN_CHARS = 7
# Height of an assignment button at the default font size; it grows with
# the font (see window_geometry.scaled_row_height()).
KEY_BUTTON_BASE_HEIGHT = 20

# What _refresh_buttons() puts in front of a shifted assignment's text. The
# minimum width leaves room for it on the longest name; the rarer program
# marker (▸) and warning mark (⚠, after LKAOFF) can still widen a key a little.
KEY_TEXT_PREFIXES = "⇧"

# What CTkButton adds to its text's width before it has to grow (measured:
# a button asked to be narrower than its text ends up this much wider).
BUTTON_TEXT_PADDING = 14

# A physical keyboard row is drawn on a grid of this many columns, and each
# of the row's keys spans an equal share of them: 6-key rows (5 columns
# per key) and 5-key rows (6 columns per key) therefore come out the same
# total width, the way the DM41X's 5-key section is spaced more widely
# than its 6-key section. 30 is the least common multiple of every row
# length in both layouts (5, 6 and 10).
GRID_COLUMNS = 30
# Horizontal gap between neighbouring keys, each side.
CELL_PADX = 1
# A key cell's border is drawn on the cell itself, and the children of a
# CTkFrame are placed from its very edge -- so the key number label has to be
# pushed down past the border, or it paints over the border's top edge and
# leaves a gap in it.
KEY_NUMBER_PADY = 2
# What a key's cell adds around its buttons: the padding each button is
# packed with (padx=2, both sides).
KEY_CELL_MARGIN = 2 * 2


@dataclass
class CellPlacement:
    """Where one key (or fixed physical-key label) goes on a layout's
    GRID_COLUMNS-wide grid. `cell` is an assignable key number (int) or a
    label (str)."""

    cell: object
    row: int
    column: int
    columnspan: int
    rowspan: int = 1


def layout_placements(layout) -> list:
    """Turns a layout (DM41X_LAYOUT/DM41L_LAYOUT) into the CellPlacements
    to draw. Every position in a row is GRID_COLUMNS // len(row) grid
    columns wide, and a key number that is repeated in neighbouring
    positions of a row, or in the same position of neighbouring rows
    (same-length rows only), is merged into one key spanning them all --
    the double-size ENTER key (see the layout comment above). Labels are
    never merged."""
    placements = []
    owners = {}  # (row, position) -> the CellPlacement covering that position
    for row_index, row in enumerate(layout):
        unit = GRID_COLUMNS // len(row)
        above_row = row_index - 1
        comparable_above = above_row >= 0 and len(layout[above_row]) == len(row)
        position = 0
        while position < len(row):
            cell = row[position]
            width = 1
            above = None
            if isinstance(cell, int):
                while position + width < len(row) and row[position + width] == cell:
                    width += 1
                if comparable_above:
                    candidate = owners.get((above_row, position))
                    if (
                        candidate is not None
                        and candidate.cell == cell
                        and candidate.column == position * unit
                        and candidate.columnspan == width * unit
                    ):
                        above = candidate
            if above is not None:
                above.rowspan += 1
                placement = above
            else:
                placement = CellPlacement(cell, row_index, position * unit, width * unit)
                placements.append(placement)
            for covered in range(position, position + width):
                owners[(row_index, covered)] = placement
            position += width
    return placements


def _function_names() -> set:
    return set(SINGLE_BYTE_FUNCTIONS.values()) | set(XROM_FUNCTIONS.values())


def key_button_min_width(font) -> int:
    """The least width (CustomTkinter units, i.e. before display scaling)
    of an assignment button for `font`: KEY_MIN_CHARS characters of the
    font -- the wider of that many digits and the widest function name, so
    every name the calculator can show fits -- plus room for the prefixes
    the tab adds (KEY_TEXT_PREFIXES). `font` is a CTkFont; its measure()
    is in the same unscaled units as a widget's width."""
    digits = font.measure("0" * KEY_MIN_CHARS)
    widest_name = max(font.measure(name) for name in _function_names())
    return max(digits, widest_name) + font.measure(KEY_TEXT_PREFIXES) + BUTTON_TEXT_PADDING


def _program_names(memory: Memory) -> list:
    """Every assignable global label's name, alphabetical -- the Program
    tab's picker list. A set first in case a state has a duplicate label
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
    """Renders the DM41X/DM41L key-assignment grids for a Memory object.
    Call `render(memory)` whenever the buffer changes."""

    def __init__(self, master, on_change=None, **kwargs):
        super().__init__(master, **kwargs)
        self._memory: Memory = None
        self._on_change = on_change
        self._grids_built = False
        # (key_number, shifted) -> list of CTkButton, populated once by
        # _build_grid() and reused by _refresh_buttons() from then on. A
        # list, not a single button, because the DM41L and DM41X layouts
        # both reference the same 34 key numbers (just arranged
        # differently) -- each key number maps to one button per grid, and
        # both need to stay in sync. Order within each list follows grid
        # build order in render(): DM41L button first, DM41X second.
        self._key_buttons = {}

        # Fonts and sizes for the keys, all from the application font
        # (issue #41); see KEY_MIN_CHARS.
        self._key_font = ui_font()
        self._key_number_font = ui_font(-3, weight="bold")
        self._static_font = ui_font(-3)
        self._key_width = key_button_min_width(self._key_font)
        font_size = ctk.ThemeManager.theme["CTkFont"]["size"]
        self._key_height = scaled_row_height(font_size, KEY_BUTTON_BASE_HEIGHT)

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
        # classic DM41X layout is second. CTkTabview keeps whichever sub-tab
        # was last selected across render() calls, since render() never
        # rebuilds the tabview itself (see the module docstring).
        self._layout_tabs = ctk.CTkTabview(self)
        self._layout_tabs.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._dm41l_frame = self._build_layout_tab(DM41L_TAB)
        self._hp41_frame = self._build_layout_tab(DM41X_TAB)

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
        8-row DM41X grid can be taller than a small window. Having one
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
            self._header_label.configure(text="(no memory state loaded)")
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
            self._build_grid(self._hp41_frame, DM41X_LAYOUT)
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
        # All GRID_COLUMNS columns are kept the same width by
        # _equalize_columns(), so a row of 5 keys (each spanning 6 columns)
        # is as wide as a row of 6 keys (5 columns each). A double-size key
        # (ENTER) spans two positions, one cell with one pair of buttons.
        for placement in layout_placements(layout):
            if isinstance(placement.cell, int):
                self._build_key_cell(parent, placement)
            else:
                self._build_static_cell(parent, placement)

    def _build_key_cell(self, parent, placement: CellPlacement):
        key_number = placement.cell
        cell = ctk.CTkFrame(
            parent,
            fg_color=CARD_FG,
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=6,
        )
        self._grid_cell(cell, placement)

        ctk.CTkLabel(
            cell,
            text=f"{key_number:02d}",
            font=self._key_number_font,
        ).pack(pady=(KEY_NUMBER_PADY, 0))

        # A double-height key's buttons share the extra height between them.
        tall = placement.rowspan > 1
        for shifted in (False, True):
            btn = ctk.CTkButton(
                cell,
                text="",
                width=self._key_width,
                height=self._key_height,
                font=self._key_font,
                command=lambda k=key_number, s=shifted: self._edit_key(k, s),
            )
            # fill="x": a key in a 5-key row (or a double-width key) is wider
            # than the minimum, and its buttons stretch to match.
            btn.pack(
                fill="both" if tall else "x",
                expand=tall,
                padx=2,
                pady=(0, 3 if shifted else 1),
            )
            self._key_buttons.setdefault((key_number, shifted), []).append(btn)

    @staticmethod
    def _grid_cell(cell, placement: CellPlacement):
        cell.grid(
            row=placement.row,
            column=placement.column,
            columnspan=placement.columnspan,
            rowspan=placement.rowspan,
            padx=CELL_PADX,
            pady=2,
            sticky="nsew",
        )

    def _build_static_cell(self, parent, placement: CellPlacement):
        label = placement.cell
        cell = ctk.CTkFrame(
            parent,
            fg_color=STATIC_CELL_BG_COLORS.get(label, CARD_FG),
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=6,
        )
        self._grid_cell(cell, placement)
        # The cell stretches to the height of the key cells in its row, so
        # the label just fills it (no height of its own to scale).
        ctk.CTkLabel(
            cell,
            text=label,
            text_color=STATIC_CELL_TEXT_COLORS.get(label, SECONDARY_TEXT),
            font=self._static_font,
            width=self._key_width,
        ).pack(expand=True, fill="both")

    def _equalize_columns(self, frame):
        """Gives all GRID_COLUMNS columns of `frame` the same width: the
        least that holds every key, a key spanning several columns counting
        for its share. (Tk's own "uniform" columns don't do this for
        spanning cells -- it hands a wide cell's extra width to only some of
        the columns it spans.) A key whose text outgrows the minimum width
        therefore widens every column together, so the grid stays aligned.

        The widths are worked out from the fonts and the padding this class
        puts around the buttons, not read back from the widgets: asking Tk
        for a widget's size first flushes all pending drawing, and
        customtkinter's scroll bars start drawing again from inside that
        flush -- which, nested, took minutes on macOS."""
        width = 0
        for cell in frame.winfo_children():
            info = cell.grid_info()
            if not info:
                continue
            widest = self._key_width
            for button in cell.winfo_children():
                if isinstance(button, ctk.CTkButton):
                    text_width = self._key_font.measure(button.cget("text"))
                    widest = max(widest, text_width + BUTTON_TEXT_PADDING)
            needed = widest + KEY_CELL_MARGIN + 2 * CELL_PADX
            width = max(width, -(-needed // int(info["columnspan"])))
        for column in range(GRID_COLUMNS):
            frame.grid_columnconfigure(column, minsize=width)

    # -- Refresh (every render) ----------------------------------------------

    def _resolve_key(self, key_number: int, shifted: bool):
        """Returns (assignment, program) for key_number/shifted --
        `assignment` is get_key_assignment()'s dict or None, `program` is
        get_program_for_key()'s ProgramInfo or None. Only one is ever
        non-None for a state this tab itself wrote (memory.py's
        set_key_assignment()/set_program_key_assignment() enforce mutual
        exclusion on save), but the Key Assignment Register lookup is
        still checked first -- matching the real priority order (docs sec
        4.7) -- in case a state from elsewhere has both."""
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
                    fg_color = FLAG_CLEAR_FG
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
        self._equalize_columns(self._dm41l_frame)
        self._equalize_columns(self._hp41_frame)

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
            # and avoids leaving a stale assignment behind on a state
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
