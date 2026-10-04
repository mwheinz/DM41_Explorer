'''
ProgramMemory: the user-program region -- everything between the `.END.`
sentinel and R00 -- plus the "global chain" of END lines and global alpha
labels threaded through it (docs/program.md sec 5).

This region is the most mobile one in the dump: both of its boundaries are
pointers stored in status register c, and every import, removal or pack
moves at least one of them. Its `start`/`end` therefore read those
pointers live on every access (see regions.py), so a `ProgramMemory`
instance held across an edit always describes where program memory is
*now*.

Register offset and absolute address run in *opposite* directions within a
register (offset 0 = the first/leftmost printed byte = the *highest*
address in that register; offset 6 = the last/rightmost byte = the
*lowest*) -- see docs/program.md's "Addressing within program memory".
`addr_for()`/`pos_for()` convert between the two; every chain-distance
calculation below goes through them.
'''

from typing import Optional, TYPE_CHECKING

from .registers import Register, DM41MemoryError
from .regions import MemoryRegion
from .constants import PRIMARY_DATA_END, KEY_ASSIGNMENTS_RANGE, MIN_SANE_R00
from .program_info import ProgramInfo, ProgramLabel, Program
from .opcode_scan import find_program_end, scan_global_markers_forward
from .program_chain import walk_chain, encode_chain_marker

if TYPE_CHECKING:
    from .memory import Memory


class ProgramMemory(MemoryRegion):
    '''User program storage, from `.END.` up to (but not including) R00.'''

    key = "program"
    label = "User Programs"

    def __init__(self, memory: "Memory"):
        super().__init__(memory)

    # -- Extent ----------------------------------------------------------

    @property
    def start(self) -> int:
        '''The register holding the permanent `.END.` sentinel -- the
        lowest register program memory currently occupies. Reports an
        empty region (start one past PRIMARY_DATA_END) when the dump has
        no sane R00/`.END.` partition at all -- a corrupt or never-loaded
        one -- rather than inventing a span out of meaningless pointer
        values. See Memory.has_program_partition().'''
        if not self._memory.has_program_partition():
            return PRIMARY_DATA_END + 1
        return self._memory.status_registers.DotEnd()

    @property
    def end(self) -> int:
        '''The register just below R00. R00 itself belongs to data memory,
        not here.'''
        if not self._memory.has_program_partition():
            return PRIMARY_DATA_END
        return self._memory.status_registers.R00() - 1

    # -- Byte addressing within program memory ---------------------------

    @staticmethod
    def addr_for(reg: int, offset: int) -> int:
        '''Absolute byte address of (register, offset).'''
        return 7 * reg + (6 - offset)

    @staticmethod
    def pos_for(addr: int) -> tuple:
        '''(register, offset) for an absolute byte address.'''
        reg, remainder = divmod(addr, 7)
        return reg, 6 - remainder

    def top_addr(self) -> int:
        '''Address just below R00 -- the top of program memory, where the
        very first program ever written begins (see docs/program.md's
        "Addressing within program memory"). R00 itself belongs to the
        free/data-register side of the partition, not to program memory.'''
        return self.addr_for(self._memory.status_registers.R00() - 1, 0)

    def read_bytes_forward(self, reg: int, offset: int, count: int) -> bytes:
        '''Reads `count` bytes starting at (reg, offset) in the direction
        chain markers and global-label names read correctly in (increasing
        program line number / decreasing address -- see docs/program.md).
        Running past offset 6 continues at offset 0 of the next LOWER
        register, matching how program memory actually continues across a
        register boundary.'''
        out = bytearray()
        r, o = reg, offset
        for _ in range(count):
            out.append(self._memory.get_register(r).get_bytes()[o])
            o += 1
            if o > 6:
                o = 0
                r -= 1
        return bytes(out)

    def write_bytes_forward(self, addr: int, data: bytes):
        '''Writes `data` starting at absolute byte-address `addr`,
        continuing in the same forward/decreasing-address direction
        `read_bytes_forward()` reads in (and crossing register boundaries
        the same way) -- the write-side counterpart used by
        `import_program()` to splice a program's bytes into program
        memory. Each byte is its own register read-modify-write, same
        granularity as `_write_program_key_byte()` already uses below.'''
        reg, offset = self.pos_for(addr)
        for byte in data:
            reg_data = bytearray(self._memory.get_register(reg).get_bytes())
            reg_data[offset] = byte
            self._memory.set_register(reg, Register(data=bytes(reg_data)))
            offset += 1
            if offset > 6:
                offset = 0
                reg -= 1

    def _has_nonzero_bytes(self, addr: int, count: int) -> bool:
        '''True if any of the `count` bytes forward from `addr` (same
        addressing convention as `read_bytes_forward`) is non-zero.
        `count <= 0` is trivially False -- used by `list_programs()` to
        tell real (if unnamed) trailing program content apart from mere
        register-alignment padding before the permanent `.END.` marker.'''
        if count <= 0:
            return False
        reg, offset = self.pos_for(addr)
        return any(self.read_bytes_forward(reg, offset, count))

    # -- The global chain (docs/program.md sec 5) ------------------------

    def _decode_chain_marker(self, reg: int, offset: int) -> Optional[dict]:
        '''Decodes the 3-byte '1100 bbb rrrrrrrrr eeeeffff' marker at
        (reg, offset) -- docs/program.md sec 5.1. Returns None if the byte
        at (reg, offset) doesn't start with the 0xC0-0xCD marker range --
        `bbb` is a byte offset within a 7-byte register and so is only
        ever 0-6 for a real marker; `0xCE`/`0xCF` (bbb == 7) are ordinary
        2-byte opcodes elsewhere in FOCAL code, never markers (see
        `program_chain.decode_chain_marker()`, the byte-buffer twin of
        this method).'''
        raw = self.read_bytes_forward(reg, offset, 3)
        if not 0xC0 <= raw[0] <= 0xCD:
            return None
        val = (raw[0] << 16) | (raw[1] << 8) | raw[2]
        is_label = (raw[2] >> 4) == 0xF
        return {
            "bbb": (val >> 17) & 0x7,
            "distance_registers": (val >> 8) & 0x1FF,
            "is_label": is_label,
            # High nibble of the third byte, when this isn't a label: 0 =
            # normal END, 2 = the permanent `.END.` itself (docs/program.md
            # sec 5.1). None for a label, where that nibble is always F.
            "end_type": None if is_label else (raw[2] >> 4),
            "label_length": (raw[2] & 0x0F) - 1 if is_label else None,
        }

    def _decode_label_name(self, reg: int, offset: int, length: int) -> tuple:
        '''Decodes a global label's key-assignment byte and name, given
        where its 4-byte header starts -- docs/program.md sec 5.2. Reading
        the header and name in one continuous forward pass (rather than as
        two separate reads) is what makes a name longer than 3 characters
        correctly spill into the preceding register: `read_bytes_forward`
        only wraps registers within a single call. Returns
        (name, key_assignment).'''
        combined = self.read_bytes_forward(reg, offset, 4 + max(length, 0))
        key_assignment = combined[3]
        name = "".join(
            chr(b) if 0x20 <= b <= 0x7E else "?" for b in combined[4:]
        )
        return name, key_assignment

    def list_global_chain(self) -> list:
        '''
        Walks the global chain backward from `.END.` toward R00 and
        returns every global alpha label and plain END marker found along
        the way, oldest first -- the register nearest R00 is the first
        chain link ever created, matching the order CAT 1 shows on a real
        calculator. See docs/program.md sec 5 for the derivation and the
        worked examples this was checked against (every
        `src/tests/data/*.dm41` sample that has any programs in it).

        Each entry is one independent chain link (see ProgramInfo) -- do
        NOT assume labels and END markers pair up one-to-one, and do NOT
        assume consecutive entries belong to different programs. The
        user's own testing (against a modified copy of 6x-xm.dm41) found a
        single END can have zero, one, or several global labels chained to
        it, so this makes no attempt to group entries into "programs"; it
        just reports the raw chain in the order it's found, same as CAT 1
        would list it. For the grouped, "one row per real program" view --
        the one the Program tab and program export actually use -- see
        `list_programs()` below and docs/program.md sec 5.3. This method
        is still what key-assignment code (`find_program_by_name()` and
        friends) uses, since a key assignment lives on one label's own
        header regardless of how many labels its program has.

        Returns [] if program memory is empty, if R00/.END. don't look
        like a real partition, or if a fresh, never-loaded Memory()'s own
        "Memory Lost" register defaults (which decode as R00=0x19C,
        .END.=0x19B) pass that check but then fail to decode any marker
        at all -- see the MIN_SANE_R00 comment below for the actual
        mechanism in that last case. The permanent `.END.` marker itself
        is otherwise included as the last
        (newest) entry -- see ProgramInfo's docstring -- unless it truly
        has nothing chained to it yet (see docs/program.md's first worked
        example), in which case there's nothing to report at all.

        Stops -- without raising -- the moment a byte that should start a
        marker doesn't have the 0xC0-0xCD high nibble, since that means
        either this model doesn't fit this dump or the data is corrupt;
        better to show whatever was found up to that point than to crash
        the caller. Also bounded to a generous iteration cap, and guards
        against revisiting the same position, as a backstop against an
        accidentally circular chain.
        '''
        status = self._memory.status_registers
        r00 = status.R00()
        dend = status.DotEnd()
        # MIN_SANE_R00 -- guards against an R00 value below any real
        # partition boundary. This is NOT what makes a fresh,
        # never-loaded Memory() return [] below: Memory.__init__ seeds
        # registers 0x0C/0x0D with "Memory Lost" defaults, so a fresh
        # Memory() actually decodes R00 as 0x19C and .END. as 0x19B --
        # both pass the checks here. The empty result in that case
        # instead comes from the walk itself, just below: register 0x19B
        # is all zeroes, so the very first _decode_chain_marker() call
        # returns None and the loop breaks immediately, before ever
        # appending an entry.
        if not (MIN_SANE_R00 <= r00 <= PRIMARY_DATA_END) or not (
            KEY_ASSIGNMENTS_RANGE[0] <= dend < r00
        ):
            return []

        entries = []
        reg, offset = dend, 4
        visited = set()
        for _ in range(512):
            if (reg, offset) in visited:
                break
            visited.add((reg, offset))

            marker = self._decode_chain_marker(reg, offset)
            if marker is None:
                break

            # The byte distance THIS entry's own marker reports onward to
            # the next chain link the walk visits from here -- see
            # ProgramInfo's docstring for why this is exposed raw rather
            # than interpreted as a program size.
            distance_bytes = marker["distance_registers"] * 7 + marker["bbb"]

            if marker["is_label"]:
                name, key = self._decode_label_name(
                    reg, offset, marker["label_length"]
                )
                entries.append((
                    reg, offset, name, key,
                    distance_bytes, marker["bbb"], marker["distance_registers"],
                    None,
                ))
            else:
                # The very first marker examined is always this partition's
                # permanent `.END.` (it's where the walk starts). Only skip
                # recording it when program memory is genuinely empty --
                # i.e. it has no predecessor of its own (bbb/distance_regs
                # both 0) -- since then it's not really a chain link, just
                # an empty partition's bookkeeping. Whenever it DOES have a
                # predecessor, it's a real, informative entry: it's the
                # newest thing in memory, and its own distance can account
                # for bytes CAT 1 counts as part of the newest program that
                # a plain-END-only view would otherwise miss entirely (see
                # ProgramInfo's docstring -- this was found by the user
                # comparing this tab's output against a real CAT 1 listing).
                is_empty_partition_marker = (
                    len(entries) == 0
                    and marker["end_type"] == 2
                    and marker["bbb"] == 0
                    and marker["distance_registers"] == 0
                )
                if not is_empty_partition_marker:
                    entries.append((
                        reg, offset, None, None,
                        distance_bytes, marker["bbb"], marker["distance_registers"],
                        marker["end_type"],
                    ))

            if marker["bbb"] == 0 and marker["distance_registers"] == 0:
                break  # no predecessor -- first global line in memory

            addr = self.addr_for(reg, offset)
            target_addr = addr + distance_bytes
            if target_addr <= addr:
                break  # defensive: distance should never be non-positive
            next_reg, next_offset = self.pos_for(target_addr)
            if next_reg >= r00 or next_reg < 0xC0:
                break  # defensive: shouldn't walk past R00 or below 0xC0
            reg, offset = next_reg, next_offset

        programs = [
            ProgramInfo(
                header_addr=r, header_offset=o, name=n, key_assignment=k,
                distance_bytes=d, bbb=b, distance_registers=dr, end_type=et,
            )
            for r, o, n, k, d, b, dr, et in entries
        ]
        programs.reverse()
        return programs

    def list_programs(self) -> list:
        '''
        Groups `list_global_chain()`'s raw chain into real, END-delimited
        programs -- one `Program` per program CAT 1 would show, oldest
        first. See docs/program.md sec 5.3 for the full derivation; this
        replaces an earlier version of this method (see
        `list_global_chain()`, and Program/ProgramInfo's docstrings) that
        conflated "one chain link" with "one program" and, separately, had
        a real bug: it could mistake register-alignment zero-padding
        before the permanent `.END.` marker for a small extra unnamed
        program. Both are fixed here, verified against the user's own
        real-hardware `CAT 1` comparisons on two purpose-built fixtures:

          - `tests/data/unlabelled.dm41`: two programs, NEITHER one named
            (one holds only an ALPHA string, the other only a local
            numbered label) -- `CAT 1` reports them as 16 and 20 bytes.
            The old code miscounted this as three programs (16, 20, and a
            phantom 6-byte one made of nothing but the zero-padding in
            front of `.END.`).
          - `tests/data/twolabels.dm41`: ONE program with TWO global
            labels ("FIRST" and "SECOND") and, notably, no explicit END
            at all -- only the permanent `.END.` terminates it, which is
            legal for the single newest program in memory (see `Program`'s
            docstring). `CAT 1` would show two catalog entries here (one
            per label) but they are the same underlying program.

        A program is delimited by an explicit plain END marker, never by
        a global label -- a program can have zero, one, or several labels
        (`Program.labels`), and per the second fixture above the single
        newest program in memory does not need an explicit END of its own
        at all; the permanent `.END.` sentinel can close it out instead.
        Every OLDER program, by construction, must have a real END, since
        nothing else could have closed it while a newer one was added
        after it.

        Walking the chain oldest to newest: every LBL entry is added to
        the label list for whatever program is currently being
        accumulated. Every plain END entry (`end_type == 0`) always closes
        a real program -- its length is computed directly from the
        address arithmetic already validated for the chain itself (no
        byte-by-byte opcode scanning needed: the chain's own marker
        position already tells us exactly where this program's terminator
        sits), and a fresh, empty label list starts for whatever comes
        next. The permanent `.END.` entry (`end_type == 2`) is always
        last, and is handled specially, since it plays two different
        roles depending on what precedes it:

          - If any labels have been accumulated since the last explicit
            END (or since the top of program memory, if there's been none
            yet) -- as in `twolabels.dm41` -- `.END.` is genuinely this
            program's own terminator, and everything from the oldest of
            those labels' own header through `.END.`'s own bytes is one
            real program.
          - Otherwise (no labels pending), check whether the bytes between
            wherever the last real program left off and `.END.`'s own
            marker are ALL zero. If so, that gap is nothing but the
            register-alignment padding described above -- not a program,
            not even an empty one -- and is dropped entirely (this is
            what fixes the `unlabelled.dm41` miscount). If the gap
            contains any non-zero byte, it's a real final program with no
            label of its own at all (an HP-41 program doesn't require
            one, confirmed against tests/data/tower.txt, which opens with
            a local "LBL 21" instead of a global one) -- `.END.` closes
            that program too, same as the labeled case.

        Returns [] if `list_global_chain()` does (empty or not-yet-real
        program memory).
        '''
        chain = self.list_global_chain()
        if not chain:
            return []

        programs = []
        pending_labels = []
        group_start_addr = self.top_addr()

        for entry in chain:
            marker_addr = self.addr_for(entry.header_addr, entry.header_offset)

            if entry.is_named:
                pending_labels.append(ProgramLabel(
                    name=entry.name,
                    key_assignment=entry.key_assignment,
                    header_addr=entry.header_addr,
                    header_offset=entry.header_offset,
                ))
                continue

            marker_last_byte_addr = marker_addr - 2  # 3-byte marker
            length = group_start_addr - marker_last_byte_addr + 1

            if entry.end_type == 2:
                # The permanent .END. -- always the last entry examined.
                gap = length - 3
                if not pending_labels and not self._has_nonzero_bytes(
                    group_start_addr, gap
                ):
                    break  # pure padding -- not a program, nothing to add
            start_reg, start_offset = self.pos_for(group_start_addr)
            programs.append(Program(
                start_addr=start_reg,
                start_offset=start_offset,
                length=length,
                labels=pending_labels,
                terminator=".END." if entry.end_type == 2 else "END",
            ))
            pending_labels = []
            group_start_addr = marker_last_byte_addr - 1

        return programs

    def get_program_bytes(self, program: Program) -> bytes:
        '''
        Returns the raw instruction bytes for one real, END-delimited
        program (`list_programs()`) -- its own opcodes, in on-calculator
        reading order (decreasing address, see docs/program.md's
        "Addressing within program memory"), up to and including its own
        terminating marker (an explicit END, or the permanent `.END.` for
        the single newest program in memory -- see `Program`'s
        docstring). This is the byte sequence a program-file export
        (RAW/DAT/...) should contain -- see program_files.py.

        A named program (one with at least one global label) is not
        required for this to work: an HP-41 program can consist of
        nothing but local (numbered) labels, or no labels at all -- see
        `list_programs()`'s docstring for how such a program is told
        apart from mere register-alignment padding before `.END.`.
        Verified against docs/program.md's own worked APPTEST example (26
        bytes) and against the user's own real-hardware `CAT 1`
        comparison for tests/data/unlabelled.dm41 (16 and 20 bytes,
        neither program named) and tests/data/twolabels.dm41 (28 bytes,
        one program with two labels and no explicit END).

        A program with more than one global label (twolabels.dm41's case)
        is exported as the single physical block CAT 1's END-delimited
        view treats it as -- from its OLDEST label's own header through
        its own terminator -- not as separate slices per label.

        `length` was already computed directly from the global chain's
        own validated marker positions (see `list_programs()`); this
        re-reads exactly that many bytes and, as a defensive
        cross-check against corrupt data, confirms `find_program_end()`
        -- an independent forward opcode-stream scan (ported from
        hp41uc's seek_end(), see opcode_scan.py) -- agrees on exactly
        where those bytes end.

        Raises ValueError if `program` doesn't match any entry in the
        current program list (e.g. it's stale, from a `list_programs()`
        call before the dump changed).
        Raises DM41MemoryError if `find_program_end()` disagrees with
        the chain-derived length -- signals corrupt data or a program
        that isn't well-formed HP-41 code.
        '''
        for candidate in self.list_programs():
            if (
                candidate.start_addr == program.start_addr
                and candidate.start_offset == program.start_offset
            ):
                instruction_bytes = self.read_bytes_forward(
                    candidate.start_addr, candidate.start_offset, candidate.length
                )
                if find_program_end(instruction_bytes) != candidate.length:
                    raise DM41MemoryError(
                        f"Program {candidate.names_label!r}'s own bytes "
                        "don't form one well-formed HP-41 program (forward "
                        "opcode scan disagrees with the global chain) -- "
                        "the dump may be corrupt."
                    )
                return instruction_bytes
        raise ValueError(
            "This program entry doesn't match the current program list -- "
            "it may be stale (from a list_programs() call taken before "
            "the dump changed)."
        )

    # -- Import (splicing a program in) ----------------------------------

    def _convert_dot_end_to_real_end(self, entry: ProgramInfo):
        '''Rewrites the permanent `.END.` marker's own end-type nibble
        (docs/program.md sec 5.1's high `eeee` nibble) from `2` to `0` in
        place, at its existing (`entry.header_addr`, `entry.header_offset`)
        position -- turning it into a genuine closing `END` for whatever
        program it used to terminate, without touching its `bbb`/
        `distance_registers` fields (still correctly linking back to
        whatever preceded it -- see `import_program()`'s "Case A"). Used
        only when the program `.END.` used to terminate is no longer the
        newest thing in memory -- i.e. right before a fresh `.END.` gets
        written further down by `import_program()`.'''
        marker_addr = self.addr_for(entry.header_addr, entry.header_offset)
        third_byte_addr = marker_addr - 2
        reg, offset = self.pos_for(third_byte_addr)
        data = bytearray(self._memory.get_register(reg).get_bytes())
        data[offset] &= 0x0F  # clear the high (end-type) nibble: 2 -> 0
        self._memory.set_register(reg, Register(data=bytes(data)))

    def import_program(self, instruction_bytes: bytes) -> Program:
        '''
        Splices a standalone program's instruction bytes -- as produced by
        `get_program_bytes()` above, or by `decode_program_raw()`/
        `decode_program_dat()` (program_files.py) reading an external
        RAW/DAT file -- into this region as the newest program, updating
        the global chain (docs/program.md sec 5) and moving the permanent
        `.END.` sentinel so the result reads exactly like a program a real
        calculator wrote there itself. This is the write-side counterpart
        to `get_program_bytes()`; see that method and `program_files.py`'s
        module docstring for the read side, and `program_chain.py` for the
        byte-level chain parsing this leans on.

        Only ever appends as the newest program -- there's no "insert at
        an arbitrary position" here, matching the project's own scope
        decision that Import should be as low-risk as the data model
        allows (see project notes). Splicing anywhere else in the middle
        of an existing chain would need a fundamentally different (and
        much riskier) algorithm.

        **The algorithm**, in the order it runs:

        1. Confirm `instruction_bytes` is one well-formed program (
           `find_program_end()` agrees with its own length) -- the same
           cross-check `get_program_bytes()` already relies on.
        2. `program_chain.walk_chain()` finds every chain marker inside
           `instruction_bytes` -- its own trailing terminator, plus any
           internal global labels, backward-linked to each other exactly
           as `list_global_chain()` links live registers. Every one of
           these internal links is already correct and stays untouched;
           only the *outermost* one (`walk_chain()`'s last entry -- the
           one whose distance pointed outside `instruction_bytes` in its
           original source memory) needs a new distance computed, since
           the destination memory it's being copied into is a different
           place entirely.
        3. Block the import if any global label name found in step 2
           already exists somewhere in this memory's own global chain --
           see project notes for why duplicates are refused rather than
           silently created.
        4. Every label's own key-assignment byte (the header's 4th byte,
           docs/program.md sec 5.2) is zeroed (unassigned) in the copy
           about to be written -- an imported program shouldn't silently
           steal a key from whatever else already holds it in this
           buffer; the user can reassign it afterward (Key Assignments
           tab / `ASN`).
        5. Figure out where the new program goes and what its outermost
           marker's distance should now point back to:
           - If this memory has no programs at all yet, it starts at
             `top_addr()` and the outermost marker gets
             `bbb = distance_registers = 0` ("no predecessor" -- the
             chain's own way of saying "first program in memory").
           - Otherwise, the permanent `.END.` entry (`list_global_chain()`'s
             own last entry) is always the link point, but plays one of
             two roles depending on what `list_programs()` says about it
             (mirroring that method's own "Case A"/"Case B" split, see its
             docstring): if it was genuinely serving as the newest
             program's own terminator (pending label(s), or real
             unlabelled trailing content -- `twolabels.dm41`'s case),
             it's converted in place into a real closing `END`
             (`_convert_dot_end_to_real_end()`) and left as the new link
             target. Otherwise a real `END` already exists further up
             (`simple.dm41`'s case, where `.END.` is pure register-
             alignment padding) -- that untouched entry is the link
             target instead, and the padding-plus-`.END.` bytes below it
             are simply about to be overwritten by the new program's own
             content.
        6. If `instruction_bytes`' own trailing marker happens to be a
           `.END.`-type one itself (`end_type == 2` -- true when the
           program being imported was originally exported as the single
           newest, `.END.`-terminated program in *its* source memory,
           e.g. a `twolabels.dm41`-style export), it's forced to a normal
           closing `END` here too, in the copy -- it's not going to be the
           top of memory anymore once step 8 writes a fresh `.END.` below
           it.
        7. Enough free program memory to hold all of this (the program's
           own bytes, plus whatever zero-padding is needed to land the
           fresh `.END.` on a register boundary, plus its own 3 bytes)?
           If not, raises `DM41MemoryError` rather than overwriting the
           Key Assignments/Alarms regions below it.
        8. Writes the (patched) instruction bytes into registers
           (`write_bytes_forward()`), zero-pads up to the next register
           boundary (`.END.` is always found in the last 3 bytes of a
           register -- docs/program.md sec 5.1), writes a fresh `.END.`
           marker there linking back to the program's own trailing
           marker, and moves the `.END.` pointer (`set_DotEnd()`) to it.
           `R00()` is never touched -- growing into the Key
           Assignments/Alarms regions is refused outright (step 7) rather
           than silently reclaiming data-register space to make room.

        Returns the newly-imported `Program`, freshly re-read via
        `list_programs()` as a sanity check that the splice produced a
        well-formed chain (defensive -- mirrors `get_program_bytes()`'s
        own independent-verification habit).

        Raises `ValueError` if `instruction_bytes` isn't one well-formed
        program, or contains a global label name that already exists in
        this memory. Raises `DM41MemoryError` if there's no valid R00/
        `.END.` partition loaded yet, or if there isn't enough free
        program memory.

        Two more `DM41MemoryError` cases are checked defensively below
        but are dead given this region's own bounds, not reachable
        failure modes today: `_relink_outermost_marker()`'s 9-bit-
        register-count overflow (the widest possible gap between the Key
        Assignments floor, `0xC0`, and `PRIMARY_DATA_END`, `0x1FF`, is
        only 319 registers -- nowhere near the 511 the 9-bit field can
        hold) and this method's own "no valid END/label marker" check
        just below (the `find_program_end()` agreement with
        `instruction_bytes`'s own length, checked immediately above,
        already guarantees `walk_chain()` finds at least one marker;
        confirmed exhaustively for every 3-byte input and over 400,000
        random short buffers). Left in place as cheap insurance in case
        these bounds ever change.
        '''
        status = self._memory.status_registers
        if not instruction_bytes:
            raise ValueError("Nothing to import -- the program is empty.")
        if find_program_end(instruction_bytes) != len(instruction_bytes):
            raise ValueError(
                "This doesn't decode as one well-formed HP-41 program -- "
                "find_program_end() disagrees with the file's own length."
            )
        if not (MIN_SANE_R00 <= status.R00() <= PRIMARY_DATA_END):
            raise DM41MemoryError(
                "No valid program memory partition is loaded -- load or "
                "start a memory buffer first."
            )

        chain_entries = walk_chain(instruction_bytes)
        if not chain_entries:
            # Defensive only -- dead in practice given the guard just
            # above: see this method's own docstring.
            raise DM41MemoryError(
                "Could not find a valid END/label marker in this "
                "program's own bytes -- it may be corrupt."
            )
        self._check_no_duplicate_labels(chain_entries)

        data = bytearray(instruction_bytes)

        # Step 4: zero every label's own key-assignment byte in the copy.
        for entry in chain_entries:
            if entry["is_label"]:
                data[entry["index"] + 3] = 0x00

        # Step 5: where this goes, and what the outermost marker should
        # now link back to. `dot_end_to_convert` records whether Case A
        # applies (see _resolve_import_link()'s docstring) -- nothing
        # touches a live register yet.
        insertion_addr, link_addr, dot_end_to_convert = self._resolve_import_link()

        # Step 6: force a copied `.END.`-type trailing marker to a real END.
        trailing = chain_entries[0]
        if not trailing["is_label"] and trailing["end_type"] == 2:
            data[trailing["index"] + 2] &= 0x0F

        # Step 2 (continued): recompute the outermost marker's own link.
        self._relink_outermost_marker(data, chain_entries[-1], insertion_addr, link_addr)

        # Step 7: is there room? This has to run -- and be allowed to
        # raise -- before anything below actually touches a live register
        # (including converting `.END.` in Case A), so a rejected import
        # leaves this Memory completely unchanged rather than partially
        # spliced.
        end_marker_addr, next_free_addr = self._check_import_room(
            insertion_addr, len(data)
        )

        # Step 8: write it -- starting with the Case A conversion deferred
        # from step 5, now that every earlier check has passed.
        if dot_end_to_convert is not None:
            self._convert_dot_end_to_real_end(dot_end_to_convert)
        self.write_bytes_forward(insertion_addr, bytes(data))
        padding = next_free_addr - end_marker_addr
        if padding > 0:
            self.write_bytes_forward(next_free_addr, bytes(padding))

        trailing_dest_addr = insertion_addr - trailing["index"]
        end_dr, end_bbb = divmod(trailing_dest_addr - end_marker_addr, 7)
        end_marker_bytes = encode_chain_marker(end_bbb, end_dr, 0x20)
        self.write_bytes_forward(end_marker_addr, end_marker_bytes)

        new_dot_end_reg, _ = self.pos_for(end_marker_addr)
        status.set_DotEnd(new_dot_end_reg)

        programs = self.list_programs()
        if not programs or programs[-1].length != len(instruction_bytes):
            raise DM41MemoryError(
                "Import produced an inconsistent program chain -- this "
                "looks like a bug, please report it."
            )
        return programs[-1]

    def _check_no_duplicate_labels(self, chain_entries: list):
        '''Raises ValueError if any global label name found by
        `walk_chain()` (in a program about to be imported) already exists
        somewhere in this memory's own global chain -- see
        `import_program()`'s step 3.'''
        existing_names = {p.name for p in self.list_global_chain() if p.is_named}
        for entry in chain_entries:
            if entry["is_label"] and entry["name"] in existing_names:
                raise ValueError(
                    f"A global label named {entry['name']!r} already exists "
                    "in this memory -- rename or delete the existing "
                    "program before importing this one."
                )

    def _resolve_import_link(self) -> tuple:
        '''
        `import_program()`'s step 5: figures out where a new program
        should be written (the highest address it can start at) and what
        its outermost marker's distance should now point back to.

        Returns `(insertion_addr, link_addr, dot_end_to_convert)`:
        - `insertion_addr`: the highest address the new program's own
          first byte can occupy.
        - `link_addr`: the address the new program's outermost marker
          should now link back to, or `None` if this memory has no
          programs at all yet (the new marker gets `bbb =
          distance_registers = 0`, "no predecessor").
        - `dot_end_to_convert`: the permanent `.END.` `ProgramInfo` to
          convert into a real closing `END` (Case A -- see
          `_convert_dot_end_to_real_end()`), or `None` if no conversion
          is needed (Case B, or no existing programs at all). Deliberately
          not converted here -- see `import_program()`'s own comment on
          why that has to wait until after the room check.

        Raises `DM41MemoryError` if this memory's program chain is
        non-empty but doesn't resolve to any real program at all (would
        only happen for corrupt data -- see `list_programs()`'s
        docstring).
        '''
        chain = self.list_global_chain()
        if not chain:
            return self.top_addr(), None, None

        boundary = chain[-1]  # always the permanent .END. entry
        programs = self.list_programs()
        if not programs:
            raise DM41MemoryError(
                "This memory's program chain doesn't resolve to any "
                "real program -- it may be corrupt."
            )
        if programs[-1].terminator == ".END.":
            # Case A: `.END.` was genuinely serving as the newest
            # program's own terminator -- it'll be converted in place
            # into a real closing END (its own link, unchanged, is still
            # valid) and used as the new link target.
            link_entry = boundary
            dot_end_to_convert = boundary
        else:
            # Case B: a real END already exists further up; `.END.`
            # itself (plus any padding before it) is pure register-
            # alignment filler about to be overwritten.
            link_entry = chain[-2]
            dot_end_to_convert = None

        link_addr = self.addr_for(link_entry.header_addr, link_entry.header_offset)
        insertion_addr = link_addr - 2 - 1  # one below the link marker's own last byte
        return insertion_addr, link_addr, dot_end_to_convert

    @classmethod
    def _relink_outermost_marker(
        cls,
        data: bytearray,
        outermost: dict,
        insertion_addr: int,
        link_addr: Optional[int],
    ):
        '''`import_program()`'s step 2 finish: overwrites `outermost`'s
        `bbb`/`distance_registers` fields in `data` (in place) so it
        correctly links back to `link_addr` once `data` is written
        starting at `insertion_addr` -- or to "no predecessor" (`bbb =
        distance_registers = 0`) if `link_addr` is `None` (this is the
        first program in memory). Its own third byte is preserved as-is.
        Raises `DM41MemoryError` if the computed distance doesn't fit
        the format's 9-bit register-count field -- checked defensively,
        but dead in practice given this region's own bounds: the widest
        possible gap, from the Key Assignments floor (`0xC0`) to
        `PRIMARY_DATA_END` (`0x1FF`), is only 319 registers, well inside
        the 511 the 9-bit field can hold. Left in place as cheap
        insurance in case those bounds ever change.'''
        if link_addr is None:
            new_bbb, new_dr = 0, 0
        else:
            outermost_dest_addr = insertion_addr - outermost["index"]
            new_dr, new_bbb = divmod(link_addr - outermost_dest_addr, 7)
            if new_dr > 0x1FF:
                raise DM41MemoryError(
                    "This program lands too far from the existing program "
                    "chain to encode -- program memory may be unusually "
                    "large or fragmented."
                )
        index = outermost["index"]
        third = data[index + 2]
        data[index : index + 3] = encode_chain_marker(new_bbb, new_dr, third)

    def _check_import_room(self, insertion_addr: int, data_len: int) -> tuple:
        '''`import_program()`'s step 7: works out where the fresh `.END.`
        marker would land -- register-aligned to the last 3 bytes of a
        register, right after `data_len` bytes written starting at
        `insertion_addr` and however much zero-padding gets it to that
        boundary -- and checks that against the Alarms/Key Assignments
        boundary. Returns `(end_marker_addr, next_free_addr)` if there's
        room; raises `DM41MemoryError` if not.'''
        floor_reg = self._memory.alarms.end_exclusive
        program_end_addr = insertion_addr - data_len + 1
        next_free_addr = program_end_addr - 1
        end_marker_addr = next_free_addr - ((next_free_addr - 2) % 7)
        end_marker_last_addr = end_marker_addr - 2
        end_marker_reg, _ = self.pos_for(end_marker_last_addr)
        if end_marker_reg < floor_reg:
            lowest_free_addr = self.addr_for(floor_reg, 6)
            available = insertion_addr - lowest_free_addr + 1
            needed = insertion_addr - end_marker_last_addr + 1
            raise DM41MemoryError(
                f"Not enough free program memory to import this program "
                f"(needs {needed} bytes, only {max(available, 0)} available)."
            )
        return end_marker_addr, next_free_addr

    # -- Rebuild / pack / remove -----------------------------------------

    def _forward_scan_programs(self) -> list:
        '''
        Physically re-derives every program in this region by scanning its
        raw opcodes forward -- from `top_addr()` (the oldest program's
        fixed starting point) down to `.END.`'s own floor
        (`self.addr_for(DotEnd(), 6)`) -- entirely independent of the
        existing backward chain-link ("backlink") fields
        `list_global_chain()`/`list_programs()` rely on. This is `repack()`'s
        primary job (below), per the user's own real-hardware
        investigation (project notes,
        `pack_anomaly_investigation_2026-08-24.md`): a dump written by a
        tool other than a real HP-41/DM41L (or this app) can leave those
        backlinks zeroed or simply never set, even though real,
        well-formed FOCAL code sits right there in the raw bytes --
        `list_global_chain()` then reports no programs at all, and no
        global label in that memory can be viewed, exported, or assigned
        to a key, even though it is genuinely present. This is real
        PACK's actual documented job: walk the opcodes forward and
        rebuild the chain from scratch, the same way
        `scan_global_markers_forward()` (opcode_scan.py) does.

        Trusts `R00()`/`DotEnd()` themselves as sane boundary pointers --
        confirmed by the user's own investigation to remain correct even
        when the chain *inside* that span is broken -- but nothing about
        the marker bytes within that span, including a marker's own
        distance/`bbb` fields, its end-type nibble, or whether `.END.`
        itself decodes at all.

        Because `import_program()` (used by `_rebuild()` below to actually
        re-splice each program found here) itself trusts
        `program_chain.walk_chain()` to find every *embedded* label within
        one program's own bytes -- which depends on exactly the same
        backlink fields that may be broken, not just for the outermost
        link but for any internal one -- this does not just slice out each
        program's bytes unchanged. It first rewrites every marker's own
        `bbb`/`distance_registers` fields, in a local working copy, to the
        true physical gap back to whichever marker
        `scan_global_markers_forward()` found immediately before it (0
        for the very first marker in the whole span -- "no predecessor",
        the same convention `_resolve_import_link()` uses for an empty
        memory). Only then are the (now internally self-consistent)
        per-program byte ranges sliced out. A marker's own third byte
        (end-type, or a label's length-plus-key-length byte, docs/
        program.md sec 5.1/5.2) is never touched -- only its link.

        Returns a list of `(instruction_bytes, key_assignments)` tuples,
        oldest program first -- the same shape `repack()`/`remove_program()`
        already pass to `_rebuild()` -- or `[]` if program memory holds
        nothing at all, or if R00/`.END.` do not look like a real
        partition yet (matching `list_global_chain()`'s own guard).

        Raises `DM41MemoryError` if the scan cannot safely determine
        where real content ends: if real (non-zero) bytes are found but
        no marker at all could be located in them, if the very last
        marker found is a label with nothing closing it, or if non-zero
        bytes remain between the last marker found and `DotEnd()`'s own
        floor. Any of these mean the scan cannot be sure it has found
        every real program without risking silently dropping one --
        matching this project's existing preference (see
        `get_program_bytes()`, `import_program()`) for raising over
        guessing when a dump does not look well-formed.
        '''
        status = self._memory.status_registers
        r00 = status.R00()
        dend = status.DotEnd()
        if not (MIN_SANE_R00 <= r00 <= PRIMARY_DATA_END) or not (
            KEY_ASSIGNMENTS_RANGE[0] <= dend < r00
        ):
            return []

        top_addr = self.addr_for(r00 - 1, 0)
        floor_addr = self.addr_for(dend, 6)
        top_reg, top_offset = self.pos_for(top_addr)
        data = bytearray(
            self.read_bytes_forward(top_reg, top_offset, top_addr - floor_addr + 1)
        )

        markers = scan_global_markers_forward(bytes(data))
        if not markers:
            if any(data):
                raise DM41MemoryError(
                    "Program memory contains data, but no recognizable "
                    "global chain marker (a label or END) could be found "
                    "in it -- pack() can't safely determine where a "
                    "program boundary is."
                )
            return []

        last_marker = markers[-1]
        if last_marker["is_label"]:
            raise DM41MemoryError(
                "Program memory ends with a global label that's never "
                "closed by an END -- pack() can't safely determine where "
                "that program ends."
            )
        tail_start = last_marker["index"] + 3
        if any(data[tail_start:]):
            raise DM41MemoryError(
                "Program memory has unrecognized data after its last "
                "global chain marker -- pack() can't safely determine "
                "where program memory's real boundary is."
            )

        # Repair every marker's own link, in a local working copy, to the
        # true physical gap back to whichever marker was found just
        # before it -- see docstring. Nothing here touches a live
        # register; _rebuild() does that once these byte ranges are
        # re-imported.
        for i, marker in enumerate(markers):
            distance_bytes = (
                0 if i == 0 else marker["index"] - markers[i - 1]["index"]
            )
            distance_registers, bbb = divmod(distance_bytes, 7)
            if distance_registers > 0x1FF:
                # Defensive only -- dead in practice, same reasoning as
                # _relink_outermost_marker()'s docstring: this region's
                # widest possible gap (Key Assignments floor 0xC0 to
                # PRIMARY_DATA_END 0x1FF) is only 319 registers. Left in
                # place as cheap insurance in case those bounds ever
                # change.
                raise DM41MemoryError(
                    "Two global chain markers are too far apart to "
                    "re-link -- program memory may be unusually large or "
                    "fragmented."
                )
            start = marker["index"]
            data[start : start + 3] = encode_chain_marker(
                bbb, distance_registers, marker["third_byte"]
            )

        programs = []
        pending_labels = []
        group_start_index = 0

        for marker in markers:
            if marker["is_label"]:
                pending_labels.append((marker["name"], marker["key_assignment"]))
                continue

            marker_last_byte_index = marker["index"] + 2
            if (
                marker is last_marker
                and not pending_labels
                and not any(data[group_start_index : marker["index"]])
            ):
                break  # pure register-alignment padding -- not a program

            instruction_bytes = bytes(
                data[group_start_index : marker_last_byte_index + 1]
            )
            key_assignments = {name: key for name, key in pending_labels if key}
            programs.append((instruction_bytes, key_assignments))
            pending_labels = []
            group_start_index = marker_last_byte_index + 1

        return programs

    def _rebuild(self, programs: list):
        '''
        Physically rewrites this region from scratch so it exactly
        contains `programs` -- a list of `(instruction_bytes,
        key_assignments)` tuples, oldest program first (`key_assignments`
        is a `{label_name: key_byte}` dict, for that program's own labels
        that currently hold a real key assignment, i.e. `key_byte != 0`).
        Shared by `remove_program()` (called with every *other* existing
        program, physically closing the gap the removed one leaves
        behind) and `repack()` (called with every existing program,
        unchanged -- reclaims only incidental drift, e.g. from a
        hand-edited or externally-loaded dump).

        Every entry in `programs` is assumed to already be well-formed
        (each `instruction_bytes` came from this same region's own
        `get_program_bytes()`, captured by the caller *before* this
        method touches anything) and to contain no label name duplicated
        elsewhere in `programs` -- both guaranteed by construction, since
        these are exactly the programs that were already coexisting
        validly in this Memory before the call.

        First clears every register from the top of the Alarms buffer up
        to (not including) `R00()` and resets `.END.` to `R00()` itself --
        `list_global_chain()`'s own definition of "no programs at all
        yet" (its `dend < r00` check) -- then re-`import_program()`s each
        entry in order. Reusing `import_program()` here, rather than
        re-deriving its splicing/linking arithmetic, is deliberate: it's
        already the thoroughly-tested single source of truth for "how
        does one program get spliced onto the current chain," and every
        program here is by definition importable (no duplicate names,
        each already well-formed, and the total result can only be
        smaller than or equal to what was already fitting in this same
        space before the call).

        `import_program()` always zeroes a freshly-spliced program's own
        label key-assignment bytes (it can't tell "this is a foreign
        import" from "this is the exact same program moving to a new
        address" -- see its own docstring, step 4); this method restores
        each label's original key-assignment byte immediately afterward
        instead, straight from the `key_assignments` dict passed in for
        it. The corresponding KEYFLAGS bits (sec 4.5) are never touched
        by any of this -- they live in a completely different register
        (`KeyAssignments.set_key_flag()`) -- so as long as they were
        already correct before this call, restoring the header byte alone
        is enough to leave a kept program's key assignment exactly as it
        was.
        '''
        status = self._memory.status_registers
        for reg in range(self._memory.alarms.end_exclusive, status.R00()):
            self._memory.set_register(reg, Register(size=7))
        status.set_DotEnd(status.R00())

        for instruction_bytes, key_assignments in programs:
            imported = self.import_program(instruction_bytes)
            for label in imported.labels:
                key_byte = key_assignments.get(label.name)
                if key_byte:
                    self._write_program_key_byte(
                        label.header_addr, label.header_offset, key_byte
                    )

        if programs:
            self._collapse_trailing_end_into_dot_end()

    def _collapse_trailing_end_into_dot_end(self):
        '''
        `_rebuild()`'s own best-effort cleanup pass: every call it makes
        to `import_program()` -- including the very last one, for the
        newest program being kept -- always writes a real, explicit END
        for whatever it just imported and then a *separate*, freshly
        written permanent `.END.` sentinel right after it (see
        `import_program()`'s own step 8; it has no way to know, on any
        given call, whether another program is about to be imported right
        after it). Left alone, that can leave the newest kept program
        genuinely `terminator == "END"` in `list_programs()`'s eyes,
        wasting up to a full register on a redundant second marker that
        the `Program`/`import_program()` docstrings' own stated invariant
        says shouldn't exist -- the single newest program in memory is
        supposed to be closed out by the permanent `.END.` sentinel
        directly, with no explicit END of its own.

        This collapses the two back into that canonical single-marker
        form -- but ONLY when it's actually safe to: `.END.` is only ever
        valid register-aligned, sitting in the last 3 bytes of whatever
        register `DotEnd()` names (docs/program.md sec 5.1), while an
        ordinary internal chain marker (what the last-imported program's
        own real END now is) can legally sit at any byte offset within
        its own register -- most of the time it won't happen to be
        exactly offset 4. When it IS (i.e. this program's own real END
        already happens to occupy the same 3 bytes a `.END.` marker would
        need), it's rewritten in place as the permanent `.END.` itself
        (only its end-type nibble changes -- its `bbb`/`distance_registers`
        fields already correctly link back to whatever precedes it and
        are left untouched), `.END.` is moved to point at it, and the now
        -superfluous separate sentinel above it (pure zero padding by
        construction) is zeroed out and reclaimed as free space. This is
        exactly what recovers `twolabels.dm41`-style dumps (a single
        program with no explicit END of its own, terminated only by
        `.END.`) back to their original, maximally-compact layout after a
        `pack()` that changed nothing else about them.

        When it's NOT offset-4-aligned, this leaves memory exactly as
        `import_program()` itself already produces it -- correct, just
        not maximally compact, the same tradeoff every single call to
        that method already makes and that the rest of this project
        already accepts (see e.g. `test_import_apptest_into_empty_memory
        _matches_simple_dm41_exactly()`, which only round-trips exactly
        because APPTEST happens to already be offset-4-aligned).

        Only ever called when `_rebuild()` actually imported at least one
        program -- with none, `.END.` is already sitting at `R00()` (no
        separate sentinel was ever written) and there is nothing to
        collapse.
        '''
        status = self._memory.status_registers
        chain = self.list_global_chain()
        sentinel = chain[-1]  # the fresh, empty .END. just written
        sentinel_addr = self.addr_for(sentinel.header_addr, sentinel.header_offset)
        target_addr = sentinel_addr + sentinel.distance_bytes
        pred_reg, pred_offset = self.pos_for(target_addr)
        if pred_offset != 4:
            return  # not register-aligned -- can't collapse without moving bytes

        third_byte_addr = self.addr_for(pred_reg, pred_offset) - 2
        reg, offset = self.pos_for(third_byte_addr)
        data = bytearray(self._memory.get_register(reg).get_bytes())
        data[offset] = (data[offset] & 0x0F) | 0x20  # end-type nibble -> 2 (.END.)
        self._memory.set_register(reg, Register(data=bytes(data)))

        old_dot_end_reg = status.DotEnd()
        status.set_DotEnd(pred_reg)
        for stale in range(pred_reg + 1, old_dot_end_reg + 1):
            self._memory.set_register(stale, Register(size=7))

    def remove_program(self, program: Program):
        '''
        Removes `program` from this region entirely and closes up the gap
        it leaves behind, so every remaining program stays exactly as
        contiguous as it was before -- the write-side counterpart to
        `get_program_bytes()`/`import_program()`, and this project's
        answer to GitHub issue #6 ("add the ability to remove programs";
        Import/Export already covered "add"/"edit").

        Removing anything other than the single newest program
        (`is_last`) is not simply "erase these bytes": every OLDER
        program sits at a fixed, unmovable address (the oldest one
        always starts exactly at `top_addr()`, right below `R00()` -- see
        that method's docstring), so deleting one from the middle (or the
        very oldest one) would otherwise leave a hole of genuinely
        unreachable register space wedged between `R00()` and whatever
        programs remain above it -- space the `FreeSpace` region's own
        accounting would never see, since it only ever looks at the gap
        between the Alarms buffer and `.END.`. This reclaims that space by
        rebuilding the entire program area from scratch, keeping
        everything except `program` (`_rebuild()`).

        Also clears the KEYFLAGS bit (sec 4.5) for any of `program`'s own
        labels that currently hold a key assignment (sec 4.6) -- once its
        header is gone, `get_program_for_key()` can never find it there
        again, so leaving the flag set would misreport that key as still
        assigned to something. Key Assignment Register entries (sec 4.1
        -- the *other* storage mechanism, see KeyAssignments) are
        completely unrelated to any program and are left untouched.

        Raises ValueError if `program` doesn't match any entry in the
        current program list (e.g. it's stale, from a `list_programs()`
        call taken before the dump changed) -- same defensive check as
        `get_program_bytes()`.
        '''
        key_assignments_region = self._memory.key_assignments
        programs = self.list_programs()
        match = next(
            (
                p for p in programs
                if p.start_addr == program.start_addr
                and p.start_offset == program.start_offset
            ),
            None,
        )
        if match is None:
            raise ValueError(
                "This program entry doesn't match the current program "
                "list -- it may be stale (from a list_programs() call "
                "taken before the dump changed)."
            )

        for label in match.labels:
            if label.key_assignment:
                try:
                    key_number, shifted = key_assignments_region.key_number_for_byte(
                        label.key_assignment
                    )
                    key_assignments_region.set_key_flag(key_number, shifted, False)
                except ValueError:
                    pass  # didn't decode to a real key position

        keep = []
        for p in programs:
            if p is match:
                continue
            raw = self.get_program_bytes(p)
            key_assignments = {
                l.name: l.key_assignment for l in p.labels if l.key_assignment
            }
            keep.append((raw, key_assignments))

        self._rebuild(keep)

    def repack(self):
        '''
        The program-memory half of `Memory.pack()` (GitHub issue #31).

        Packing has to *rebuild* the global chain, not just compact
        whatever it already recognizes, per the user's own correction to
        the first version of this: `_forward_scan_programs()` walks the
        raw opcodes forward, entirely independent of the existing
        (possibly zeroed, possibly never-set) backward chain-link fields,
        so a global label that's physically present but not currently
        chain-linked -- confirmed on real hardware, see project notes
        `pack_anomaly_investigation_2026-08-24.md` -- becomes visible
        again via `list_programs()`/`list_global_chain()`, and assignable
        to a key. Every program found is then rewritten back tightly
        against `R00()` with `_rebuild()`, reclaiming any accumulated
        register-alignment drift the same way `remove_program()` does for
        the program it deletes.

        Safe to call on a buffer with no programs at all -- program
        memory is then left untouched rather than guessing at a `.END.`
        for an empty partition this method did not create.

        Raises `DM41MemoryError` if `_forward_scan_programs()` cannot
        safely determine program memory's real content -- see that
        method's own docstring for exactly when that happens. Nothing is
        changed if that happens: the scan runs, and can raise, before
        anything about program memory is touched.
        '''
        keep = self._forward_scan_programs()
        if keep:
            self._rebuild(keep)

    # -- Global label (program) key assignments --------------------------
    #
    # docs/key_assignments.md sec 4.6 -- a completely separate storage
    # mechanism from the Key Assignment Registers (sec 4.2, see
    # key_assignments.py): the key byte lives in the program's own
    # global-label header (ProgramInfo.key_assignment, the 4th header
    # byte, docs/program.md sec 5.2) rather than in a shared buffer. A
    # label's header has room for exactly one key byte, so a program can
    # hold only one key assignment at a time -- unlike a physical key,
    # which has independent unshifted/shifted slots.

    def find_program_by_name(self, name: str) -> Optional[ProgramInfo]:
        '''First named global label matching `name` (oldest-created, i.e.
        list_global_chain()'s own order, in the rare case of a duplicate
        name) -- shared by set_program_key_assignment()/
        clear_program_key_assignment()/get_program_for_key(). A key
        assignment lives on one label's own header (sec 4.6/5.2)
        regardless of how many labels its program has, so this works off
        the flat per-label chain, not the grouped `list_programs()`.'''
        for program in self.list_global_chain():
            if program.is_named and program.name == name:
                return program
        return None

    def _write_program_key_byte(self, header_addr: int, header_offset: int, value: int):
        '''Overwrites the key-assignment byte (the 4th byte, sec 4.2/5.2)
        of the global-label header starting at (header_addr,
        header_offset) -- the write-side counterpart to
        _decode_label_name() reading it. `addr_for`/`pos_for` convert to
        and from the linear address space so this doesn't need its own
        register-boundary-crossing loop (see read_bytes_forward for why
        register offset and address run in opposite directions).'''
        reg, offset = self.pos_for(self.addr_for(header_addr, header_offset) - 3)
        data = bytearray(self._memory.get_register(reg).get_bytes())
        data[offset] = value
        self._memory.set_register(reg, Register(data=bytes(data)))

    def clear_assignments_for_key_byte(
        self, key_byte: int, except_name: Optional[str] = None
    ):
        '''Writes 0x00 (unassigned) into the header of every global label
        currently holding `key_byte`, except one named `except_name` (used
        by set_program_key_assignment() while moving that program itself
        onto this key -- its own old byte is handled separately there).
        Does not touch KEYFLAGS -- callers own that, since the bit should
        usually end up set (by whatever new assignment is replacing these)
        rather than cleared. Also called by
        KeyAssignments.set_assignment(), which is claiming the same key
        for the other storage mechanism.'''
        for program in self.list_global_chain():
            if (
                program.is_named
                and program.key_assignment == key_byte
                and program.name != except_name
            ):
                self._write_program_key_byte(
                    program.header_addr, program.header_offset, 0x00
                )

    def get_program_for_key(self, key_number: int, shifted: bool) -> Optional[ProgramInfo]:
        '''Looks up the global label (if any) assigned to `key_number`/
        `shifted` via sec 4.6 -- the counterpart to
        KeyAssignments.get_assignment() for the other storage mechanism
        (sec 4.1). Per the real lookup order (sec 4.7), a Key Assignment
        Register entry on the same key always takes priority over a
        global-label one, but this method only checks global labels --
        callers wanting "whatever's actually assigned to this key" should
        check KeyAssignments.get_assignment() first and fall back to this
        (see gui/key_assignments_tab.py).'''
        key_byte = self._memory.key_assignments.key_byte_for(key_number, shifted)
        for program in self.list_global_chain():
            if program.is_named and program.key_assignment == key_byte:
                return program
        return None

    def set_program_key_assignment(self, name: str, key_number: int, shifted: bool):
        '''Assigns the global label `name` to `key_number`/`shifted` (sec
        4.6) -- `ASN "name" [key]` on a real calculator. Unlike
        KeyAssignments.set_assignment(), this never touches the Key
        Assignment Registers; it writes directly into the label's own
        header.

        Because that header holds only one key byte, reassigning a
        program that's already on a different key MOVES it here rather
        than creating a second assignment -- its previous key's KEYFLAGS
        bit is cleared as part of the move. This also enforces mutual
        exclusivity with the *other* storage mechanism on the target key:
        any existing Key Assignment Register entry there is removed, and
        any other program currently pointing at this key is cleared to
        unassigned -- the real lookup order (sec 4.7) means a Key
        Assignment Register entry would otherwise silently shadow a
        global-label one on the same key, so letting both exist at once
        would be misleading rather than a real dual assignment. Same
        silent-overwrite precedent as KeyAssignments.set_assignment().

        Raises ValueError if no global label named `name` exists.'''
        key_assignments_region = self._memory.key_assignments
        program = self.find_program_by_name(name)
        if program is None:
            raise ValueError(f"No global label named {name!r} found")

        key_byte = key_assignments_region.key_byte_for(key_number, shifted)

        # Moving this same program off whatever key it held before, if any
        # (0x00 means "never assigned" -- nothing to move off of).
        if program.key_assignment:
            try:
                old_key_number, old_shifted = key_assignments_region.key_number_for_byte(
                    program.key_assignment
                )
                key_assignments_region.set_key_flag(old_key_number, old_shifted, False)
            except ValueError:
                pass  # didn't decode to a real key position; nothing to clear

        # This key can only run one thing -- clear whatever else was there.
        key_assignments_region.delete_assignment(key_number, shifted)
        self.clear_assignments_for_key_byte(key_byte, except_name=name)

        self._write_program_key_byte(
            program.header_addr, program.header_offset, key_byte
        )
        key_assignments_region.set_key_flag(key_number, shifted, True)

    def clear_program_key_assignment(self, name: str):
        '''Removes global label `name`'s key assignment (sec 4.6), if it
        has one -- writes 0x00 back into its header and clears the
        corresponding KEYFLAGS bit. No-op if the label has no key
        assignment. Raises ValueError if no global label named `name`
        exists.'''
        key_assignments_region = self._memory.key_assignments
        program = self.find_program_by_name(name)
        if program is None:
            raise ValueError(f"No global label named {name!r} found")
        if not program.key_assignment:
            return
        try:
            key_number, shifted = key_assignments_region.key_number_for_byte(
                program.key_assignment
            )
            key_assignments_region.set_key_flag(key_number, shifted, False)
        except ValueError:
            pass
        self._write_program_key_byte(
            program.header_addr, program.header_offset, 0x00
        )
