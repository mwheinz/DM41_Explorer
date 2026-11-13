"""
The calculator keyboards and what each key is assigned to, without Tk.

The Key Assignments tab draws these, and so does the PDF keyboard diagram
(memory/keyboard_pdf.py, GitHub issue #76). Both models assign the same
34 keys, but their physical keyboards lay them out differently (see
docs/key_assignments.md, "Keyboard Layouts"), so there are two layout
tables.

Layout tables: each row is a list of cells, where an int is an assignable
key number (`MN` notation, docs sec 2) and a str is a non-assignable
physical key's label (empty string for a genuinely blank/spare
position).

The ENTER key (41) has only one MxN position but is a double-size key on
both keyboards, so it is listed in two neighbouring positions: side by
side in a row (the DM41X's double-width key) or one above the other in
the same position of two rows (the DM41L's double-height key). Repeating
an assignable key number like this makes it ONE key that spans those
positions -- see layout_placements().
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

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

# Which physical layout each model uses, by profile name. A profile whose
# name is missing here falls back to the DM41X's classic HP-41
# arrangement, which is the one every other model derives from.
LAYOUT_BY_PROFILE = {
    "DM41L": DM41L_LAYOUT,
    "DM41X": DM41X_LAYOUT,
}
FALLBACK_LAYOUT = DM41X_LAYOUT

# A physical keyboard row is laid out on a grid of this many columns, and
# each of the row's keys spans an equal share of them: 6-key rows (5
# columns per key) and 5-key rows (6 columns per key) therefore come out
# the same total width, the way the DM41X's 5-key section is spaced more
# widely than its 6-key section. 30 is the least common multiple of every
# row length in both layouts (5, 6 and 10).
GRID_COLUMNS = 30


def layout_for_profile(profile):
    """The keyboard layout table for `profile`."""
    if profile is None:
        return FALLBACK_LAYOUT
    return LAYOUT_BY_PROFILE.get(profile.name, FALLBACK_LAYOUT)


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
    the double-size ENTER key (see the module docstring). Labels are
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
                placement = CellPlacement(
                    cell, row_index, position * unit, width * unit
                )
                placements.append(placement)
            for covered in range(position, position + width):
                owners[(row_index, covered)] = placement
            position += width
    return placements


def resolve_key(memory, key_number: int, shifted: bool):
    """Returns (assignment, program) for key_number/shifted --
    `assignment` is KeyAssignments.get_assignment()'s KeyAssignment or
    None, `program` is ProgramMemory.get_program_for_key()'s ProgramInfo
    or None. Only one is ever non-None for a state this app wrote (the
    two setters enforce mutual exclusion on save), but the Key Assignment
    Register lookup is still checked first -- matching the real priority
    order (docs/key_assignments.md, "The Lookup Algorithm") -- in case a
    state from elsewhere has both."""
    assignment = memory.key_assignments.get_assignment(key_number, shifted)
    if assignment:
        return assignment, None
    return None, memory.programs.get_program_for_key(key_number, shifted)


FUNCTION = "function"
PROGRAM = "program"


@dataclass(frozen=True)
class KeyLabel:
    """What one assigned key/shift position does, for display.

    `name` is the function's display name (memory/mnemonics.py) or the
    global label's name, with no markers added. `kind` is FUNCTION (a
    Key Assignment Register entry: built-in, XROM or synthetic) or
    PROGRAM (a global label assigned to the key). `flag_clear` is True
    when the key's KEYFLAGS bit is clear, so the calculator treats the
    key as unassigned (as after LKAOFF)."""

    name: str
    kind: str
    flag_clear: bool = False


def key_labels(memory, layout=None) -> Dict[Tuple[int, bool], KeyLabel]:
    """Every assigned position on `layout`'s keys (default: the layout for
    memory.profile), as {(key_number, shifted): KeyLabel}. Unassigned
    positions are left out."""
    if layout is None:
        layout = layout_for_profile(memory.profile)
    flag_clear = set(memory.key_assignments.flag_clear_assignments())
    labels = {}
    for placement in layout_placements(layout):
        if not isinstance(placement.cell, int):
            continue
        for shifted in (False, True):
            key = (placement.cell, shifted)
            label = _label_for(memory, key, flag_clear)
            if label is not None:
                labels[key] = label
    return labels


def _label_for(memory, key, flag_clear) -> Optional[KeyLabel]:
    assignment, program = resolve_key(memory, *key)
    if assignment:
        return KeyLabel(assignment.name, FUNCTION, key in flag_clear)
    if program:
        return KeyLabel(program.name, PROGRAM, key in flag_clear)
    return None
