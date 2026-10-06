'''
ProgramInfo: one entry ("chain link") found while walking the global
label/END chain in program memory -- see Memory.list_global_chain() in
memory.py, and docs/program.md sec 5, for the full derivation.

ProgramLabel/Program: the grouped, END-delimited view built on top of that
chain -- what Memory.list_programs() returns, and what the Program tab and
program export actually work with. See docs/program.md sec 5.3 for why
this grouping is necessary at all (a program is not required to have a
global label, or may have several) and Program's own docstring below.
'''

from typing import Optional


class ProgramInfo:
    '''
    One entry found while walking the "global chain" -- the backward-
    linked list of every global alpha label and END marker in program
    memory, described in docs/program.md sec 5 (reverse-engineered from
    sample states and Wickes' "Synthetic Programming on the HP-41C",
    section 2C). This is the raw, per-marker view (Memory.list_global_chain());
    for the grouped, "one row per real program" view most callers actually
    want, see Program/Memory.list_programs() below and docs/program.md sec
    5.3.

    `name` is set for a global alpha label -- what CAT 1 shows as a program's
    name -- and is None for a plain END marker. Do NOT assume these pair up
    one-to-one with "programs": programs don't have to have any global labels,
    programs can have more than one global label in them, global labels aren't
    necessarily at the beginning of a program. Each ProgramInfo is just one
    independent chain link (a label header or an END marker), not "a program's
    boundary" -- `kind` says which it is.

    `distance_bytes` (built from the raw `bbb`/`distance_registers` marker
    fields, plus `end_type` for END entries -- see docs/program.md sec 5.1)
    is the byte distance *this* entry's own marker reports onward to the
    next chain link the backward walk visits from here. It is NOT a
    program's size -- it's the only per-entry number the chain format
    actually encodes.

    The last entry can be the permanent `.END.` itself (`end_type == 2`) --
    the newest thing in program memory, sitting right where the most-recently-
    created chain link's own "next END" would otherwise be.
    '''

    def __init__(
        self,
        header_addr: int,
        header_offset: int,
        name: Optional[str],
        key_assignment: Optional[int],
        distance_bytes: int,
        bbb: int,
        distance_registers: int,
        end_type: Optional[int],
    ):
        self.header_addr = header_addr
        self.header_offset = header_offset
        self.name = name
        self.key_assignment = key_assignment
        self.distance_bytes = distance_bytes
        self.bbb = bbb
        self.distance_registers = distance_registers
        self.end_type = end_type

    @property
    def is_named(self) -> bool:
        return self.name is not None

    @property
    def kind(self) -> str:
        '''"LBL" for a global-label header, "END" for a plain END marker,
        or ".END." for the one permanent end-of-program-memory marker
        (end_type == 2) -- the newest entry in the chain, when present.'''
        if self.is_named:
            return "LBL"
        return ".END." if self.end_type == 2 else "END"

    @property
    def display_name(self) -> str:
        return self.name if self.name is not None else "END"

    @property
    def address_label(self) -> str:
        return f"0x{self.header_addr:03x}:{self.header_offset}"

    @property
    def distance_label(self) -> str:
        return f"{self.distance_bytes} byte{'s' if self.distance_bytes != 1 else ''}"

    def __repr__(self):
        return (
            f"ProgramInfo({self.kind} {self.display_name!r} "
            f"@ {self.address_label}, distance={self.distance_bytes})"
        )


class ProgramLabel:
    '''
    One global alpha label attached to a `Program` (below) -- what CAT 1
    shows as a catalog entry, and whose header
    `Memory.set_program_key_assignment()`/`get_program_for_key()` write a
    key-assignment byte into. A `Program` can have zero, one, or several of
    these (docs/program.md sec 5.3).
    '''

    def __init__(
        self,
        name: str,
        key_assignment: Optional[int],
        header_addr: int,
        header_offset: int,
    ):
        self.name = name
        self.key_assignment = key_assignment
        self.header_addr = header_addr
        self.header_offset = header_offset

    @property
    def key_assignment_text(self) -> str:
        if self.key_assignment == 0:
            return "N/A"
        return f"0x{self.key_assignment:02x}"

    def __repr__(self):
        return f"ProgramLabel({self.name!r} @ 0x{self.header_addr:03x}:{self.header_offset})"


class Program:
    '''
    One real, END-delimited program in program memory -- see
    `Memory.list_programs()` and docs/program.md sec 5.3.

    Programs are told apart by explicit plain END markers, NOT by global
    labels: an HP-41 program is not required to have one at all, and may have
    several (see `ProgramLabel`). In addition, the newest (last-created)
    program in memory is not required to end with an explicit END of its own;
    the permanent `.END.` sentinel that marks the top of free program memory
    can serve as its terminator instead (`terminator == ".END."` here,
    `is_last` True). Every OLDER program, by construction, must have its own
    explicit END (`terminator == "END"`), since nothing else could have closed
    it out while a newer program was added after it.

    `labels` lists every global label found within this program's own bytes,
    in on-calculator/forward reading order. `length` is this program's real
    byte count -- the same number CAT 1 reports.
    '''

    def __init__(
        self,
        start_addr: int,
        start_offset: int,
        length: int,
        labels: list,
        terminator: str,
    ):
        self.start_addr = start_addr
        self.start_offset = start_offset
        self.length = length
        self.labels = labels
        self.terminator = terminator

    @property
    def is_named(self) -> bool:
        return bool(self.labels)

    @property
    def is_last(self) -> bool:
        '''True for the newest program in memory -- the only one that can
        lack an explicit END of its own (see class docstring).'''
        return self.terminator == ".END."

    @property
    def names_label(self) -> str:
        '''Every label this program has, comma-joined in forward/creation
        order, or "(unlabelled)" if it has none at all -- see
        `tests/data/unlabelled.dm41`.'''
        if not self.labels:
            return "(unlabelled)"
        return ", ".join(label.name for label in self.labels)

    @property
    def address_label(self) -> str:
        return f"0x{self.start_addr:03x}:{self.start_offset}"

    @property
    def length_label(self) -> str:
        return f"{self.length} byte{'s' if self.length != 1 else ''}"

    def __repr__(self):
        return (
            f"Program({self.names_label!r} @ {self.address_label}, "
            f"length={self.length}, terminator={self.terminator!r})"
        )
