"""
ProgramMemory: the user-program region -- everything between the `.END.`
sentinel and R00 -- plus the "global chain" of END lines and global alpha
labels threaded through it (docs/program.md sec 5).

This region is the most mobile one in the state: both of its boundaries are
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
"""

from typing import Optional

from .registers import Register, DM41MemoryError
from .regions import MemoryRegion
from .constants import (
    KEY_ASSIGNMENTS_RANGE,
    MIN_SANE_R00,
    PRIMARY_DATA_END,
    REGISTER_SIZE_BYTES,
    zero_register,
)
from .program_info import ProgramInfo, ProgramLabel, Program
from .opcode_scan import (
    compact_program_stream,
    find_program_end,
    scan_global_markers_forward,
)
from .program_chain import (
    ChainMarker,
    decode_chain_marker,
    decode_label_name,
    encode_chain_marker,
    walk_chain,
)


class ProgramMemory(MemoryRegion):
    """User program storage, from `.END.` up to (but not including) R00."""

    key = "program"
    label = "User Programs"

    # -- Extent ----------------------------------------------------------

    @property
    def start(self) -> int:
        """The register holding the permanent `.END.` sentinel -- the
        lowest register program memory currently occupies. Reports an
        empty region (start one past PRIMARY_DATA_END) when the state has
        no sane R00/`.END.` partition at all -- a corrupt or never-loaded
        one -- rather than inventing a span out of meaningless pointer
        values. See Memory.has_program_partition()."""
        if not self._memory.has_program_partition():
            return PRIMARY_DATA_END + 1
        return self._memory.status_registers.DotEnd

    @property
    def end(self) -> int:
        """The register just below R00. R00 itself belongs to data memory,
        not here."""
        if not self._memory.has_program_partition():
            return PRIMARY_DATA_END
        return self._memory.status_registers.R00 - 1

    # -- Byte addressing within program memory ---------------------------

    @staticmethod
    def addr_for(reg: int, offset: int) -> int:
        """Absolute byte address of (register, offset)."""
        return REGISTER_SIZE_BYTES * reg + (REGISTER_SIZE_BYTES - 1 - offset)

    @staticmethod
    def pos_for(addr: int) -> tuple:
        """(register, offset) for an absolute byte address."""
        reg, remainder = divmod(addr, REGISTER_SIZE_BYTES)
        return reg, REGISTER_SIZE_BYTES - 1 - remainder

    def top_addr(self) -> int:
        """Address just below R00 -- the top of program memory, where the
        very first program ever written begins (see docs/program.md's
        "Addressing within program memory"). R00 itself belongs to the
        free/data-register side of the partition, not to program memory."""
        return self.addr_for(self._memory.status_registers.R00 - 1, 0)

    def read_bytes_forward(self, reg: int, offset: int, count: int) -> bytes:
        """Reads `count` bytes starting at (reg, offset) in the direction
        chain markers and global-label names read correctly in (increasing
        program line number / decreasing address -- see docs/program.md).
        Running past offset 6 continues at offset 0 of the next LOWER
        register, matching how program memory actually continues across a
        register boundary."""
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
        """Writes `data` starting at absolute byte-address `addr`,
        continuing in the same forward/decreasing-address direction
        `read_bytes_forward()` reads in (and crossing register boundaries
        the same way) -- the write-side counterpart used by
        `import_program()` to splice a program's bytes into program
        memory. Each byte is its own register read-modify-write, same
        granularity as `_write_program_key_byte()` already uses below."""
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
        """True if any of the `count` bytes forward from `addr` (same
        addressing convention as `read_bytes_forward`) is non-zero.
        `count <= 0` is trivially False -- used by `list_programs()` to
        tell real (if unnamed) trailing program content apart from mere
        register-alignment padding before the permanent `.END.` marker."""
        if count <= 0:
            return False
        reg, offset = self.pos_for(addr)
        return any(self.read_bytes_forward(reg, offset, count))

    # -- The global chain (docs/program.md sec 5) ------------------------

    def _decode_chain_marker(self, reg: int, offset: int) -> Optional[ChainMarker]:
        """Decodes the 3-byte marker at (reg, offset) -- docs/program.md
        sec 5.1 -- by reading those bytes forward and handing them to
        `program_chain.decode_chain_marker()`. None if there is no marker
        there."""
        return decode_chain_marker(self.read_bytes_forward(reg, offset, 3), 0)

    def _decode_label_name(self, reg: int, offset: int, length: int) -> tuple:
        """Decodes a global label's name and key-assignment byte, given
        where its 4-byte header starts -- docs/program.md sec 5.2 -- by
        reading the header and name forward and handing them to
        `program_chain.decode_label_name()`. They are read in one
        continuous pass so a name longer than 3 characters correctly
        spills into the preceding register (`read_bytes_forward` only
        wraps registers within a single call). Returns
        (name, key_assignment)."""
        data = self.read_bytes_forward(reg, offset, 4 + max(length, 0))
        return decode_label_name(data, 0, length)

    def list_global_chain(self) -> list:
        """Walks the global chain backward from `.END.` toward R00 and returns
        every global label and END marker on it as ProgramInfo, oldest first --
        the order CAT 1 lists them (docs/program.md sec 5).

        Each entry is one chain link. Labels and ENDs don't pair up: an END can
        have zero, one or several global labels chained to it. For the grouped
        "one entry per program" view use `list_programs()`; this per-label view
        is what the key-assignment lookups use, since a key assignment lives on
        one label's header.

        The permanent `.END.` is included as the last (newest) entry, unless
        program memory is empty: nothing is chained to it and every byte above
        it is zero. A lone program with no global label has nothing chained
        to `.END.` either, but its bytes are non-zero, so it is kept (#67).

        Returns [] when program memory is empty or R00/`.END.` don't describe a
        real partition. Never raises: the walk stops at the first position that
        doesn't hold a marker (a model mismatch or corrupt data), and it is
        bounded and never revisits a position, so a circular chain can't loop
        forever."""
        status = self._memory.status_registers
        r00 = status.R00
        dend = status.DotEnd
        # Rejects pointers no real partition could have. A fresh Memory()'s
        # "Memory Lost" defaults (R00 0x19C, .END. 0x19B) pass this; it
        # returns [] because register 0x19B is all zeroes, so the walk
        # finds no marker at all.
        if not (MIN_SANE_R00 <= r00 <= PRIMARY_DATA_END) or not (
            KEY_ASSIGNMENTS_RANGE[0] <= dend < r00
        ):
            return []

        entries = []
        top_addr = self.top_addr()
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
            distance_bytes = marker.distance_bytes

            if marker.is_label:
                name, key = self._decode_label_name(reg, offset, marker.label_length)
                entries.append(
                    ProgramInfo(
                        header_addr=reg,
                        header_offset=offset,
                        name=name,
                        key_assignment=key,
                        distance_bytes=distance_bytes,
                        bbb=marker.bbb,
                        distance_registers=marker.distance_registers,
                        end_type=None,
                    )
                )
            else:
                # The first marker examined is always the permanent `.END.`
                # (the walk starts there). It is skipped only when program
                # memory is empty: no predecessor AND nothing but zeroes
                # above it. A lone program with no global label also leaves
                # `.END.` with no predecessor (nolabel-noend.dm41), and
                # skipping it would hide that program from list_programs()
                # and let import_program() overwrite it (#67). Otherwise it
                # is the newest chain link, and its distance can cover bytes
                # CAT 1 counts as part of the newest program (see
                # ProgramInfo's docstring).
                is_empty_partition_marker = (
                    len(entries) == 0
                    and marker.end_type == 2
                    and distance_bytes == 0
                    and not self._has_nonzero_bytes(
                        top_addr, top_addr - self.addr_for(reg, offset)
                    )
                )
                if not is_empty_partition_marker:
                    entries.append(
                        ProgramInfo(
                            header_addr=reg,
                            header_offset=offset,
                            name=None,
                            key_assignment=None,
                            distance_bytes=distance_bytes,
                            bbb=marker.bbb,
                            distance_registers=marker.distance_registers,
                            end_type=marker.end_type,
                        )
                    )

            if distance_bytes == 0:
                break  # no predecessor -- first global line in memory

            addr = self.addr_for(reg, offset)
            target_addr = addr + distance_bytes
            if target_addr <= addr:
                break  # defensive: distance should never be non-positive
            next_reg, next_offset = self.pos_for(target_addr)
            if next_reg >= r00 or next_reg < 0xC0:
                break  # defensive: shouldn't walk past R00 or below 0xC0
            reg, offset = next_reg, next_offset

        entries.reverse()
        return entries

    def list_programs(self) -> list:
        """Groups `list_global_chain()` into real, END-delimited programs -- one
        Program per program CAT 1 shows, oldest first (docs/program.md sec 5.3).

        A program is delimited by an END marker, never by a label: it can have
        zero, one or several global labels (`Program.labels`). Every older
        program ends in a real END; only the newest may instead be closed by
        the permanent `.END.` directly.

        Walking the chain oldest to newest, labels accumulate onto the program
        being built and each plain END (`end_type == 0`) closes it; its length
        comes straight from the chain's marker addresses. The permanent `.END.`
        (`end_type == 2`, always last) closes a final program if labels are
        pending, or if any byte between the last END and `.END.` is non-zero (a
        program needs no label at all). If every such byte is zero, that gap is
        register-alignment padding, not a program, and is skipped.

        Returns [] if `list_global_chain()` does."""
        chain = self.list_global_chain()
        if not chain:
            return []

        programs = []
        pending_labels = []
        group_start_addr = self.top_addr()

        for entry in chain:
            marker_addr = self.addr_for(entry.header_addr, entry.header_offset)

            if entry.is_named:
                pending_labels.append(
                    ProgramLabel(
                        name=entry.name,
                        key_assignment=entry.key_assignment,
                        header_addr=entry.header_addr,
                        header_offset=entry.header_offset,
                    )
                )
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
            programs.append(
                Program(
                    start_addr=start_reg,
                    start_offset=start_offset,
                    length=length,
                    labels=pending_labels,
                    terminator=".END." if entry.end_type == 2 else "END",
                )
            )
            pending_labels = []
            group_start_addr = marker_last_byte_addr - 1

        return programs

    def get_program_bytes(self, program: Program) -> bytes:
        """Returns one program's instruction bytes (a `list_programs()` entry), in
        on-calculator reading order (decreasing address), up to and including
        its own END or `.END.` -- the byte sequence a program file export
        (RAW/DAT/...) contains; see program_files.py.

        A program with several global labels is exported as one block, from its
        oldest label's header through its terminator.

        The length comes from the chain. As a cross-check against corrupt data,
        `find_program_end()`'s independent forward opcode scan must agree on
        where those bytes end.

        Raises ValueError if `program` doesn't match the current program list
        (it is stale, from before the state changed), and DM41MemoryError if
        the opcode scan disagrees with the chain (corrupt data, or bytes that
        aren't well-formed HP-41 code)."""
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
                        "the state may be corrupt."
                    )
                return instruction_bytes
        raise ValueError(
            "This program entry doesn't match the current program list -- "
            "it may be stale (from a list_programs() call taken before "
            "the state changed)."
        )

    # -- Import (splicing a program in) ----------------------------------

    def _convert_dot_end_to_real_end(self, entry: ProgramInfo):
        """Rewrites the permanent `.END.` marker's own end-type nibble
        (docs/program.md sec 5.1's high `eeee` nibble) from `2` to `0` in
        place, at its existing (`entry.header_addr`, `entry.header_offset`)
        position -- turning it into a genuine closing `END` for whatever
        program it used to terminate, without touching its `bbb`/
        `distance_registers` fields (still correctly linking back to
        whatever preceded it -- see `import_program()`'s "Case A"). Used
        only when the program `.END.` used to terminate is no longer the
        newest thing in memory -- i.e. right before a fresh `.END.` gets
        written further down by `import_program()`."""
        marker_addr = self.addr_for(entry.header_addr, entry.header_offset)
        third_byte_addr = marker_addr - 2
        reg, offset = self.pos_for(third_byte_addr)
        data = bytearray(self._memory.get_register(reg).get_bytes())
        data[offset] &= 0x0F  # clear the high (end-type) nibble: 2 -> 0
        self._memory.set_register(reg, Register(data=bytes(data)))

    def import_program(self, instruction_bytes: bytes) -> Program:
        """Splices a standalone program's instruction bytes -- from
        `get_program_bytes()`, or from `decode_program_raw()`/
        `decode_program_dat()` reading a RAW/DAT file -- into this region as the
        newest program, updating the global chain (docs/program.md sec 5) and
        moving `.END.` so the result reads exactly like a program the calculator
        wrote itself. Programs are only ever appended as the newest; inserting
        one in the middle of the chain is not supported.

        The steps (the code below refers to them by number):

        1. Check `instruction_bytes` is one well-formed program
           (`find_program_end()` agrees with its length).
        2. `program_chain.walk_chain()` finds every chain marker in it. Links
           between markers inside the program stay valid when it moves; only
           the outermost one, which pointed outside the program in its source
           memory, is relinked.
        3. Refuse the import if any of its global label names already exists
           here.
        4. Zero each label's key-assignment byte, so the import doesn't take a
           key already assigned to something else; the user can reassign it.
        5. Decide where it goes and what the outermost marker links to (see
           `_resolve_import_link()`): the top of program memory if there are no
           programs yet; otherwise right below the newest program's END, after
           first turning `.END.` into a real END if it was closing that program
           directly ("Case A").
        6. A trailing `.END.`-type marker copied from the source becomes a
           plain END, since a fresh `.END.` is written after it.
        7. Check there is room above the Key Assignments/Alarms regions for the
           program, its alignment padding and the new `.END.`.
        8. Write the bytes, zero-pad to the register boundary, write a fresh
           `.END.` linking back to the program's END, and move the `.END.`
           pointer to it. R00 is never moved.

        Returns the new Program, re-read through `list_programs()` as a check
        that the chain is consistent.

        Raises ValueError if `instruction_bytes` isn't one well-formed program
        or has a label name that already exists here, and DM41MemoryError if no
        valid R00/`.END.` partition is loaded or there isn't enough free program
        memory. Two further DM41MemoryError checks guard against cases the
        region's bounds make impossible, as cheap insurance.

        All-or-nothing: if any step raises, including the final consistency
        check after the bytes are written, this Memory is left exactly as it
        was (see Memory.transaction())."""
        with self._memory.transaction():
            return self._splice_program(instruction_bytes)

    def _splice_program(self, instruction_bytes: bytes) -> Program:
        """import_program()'s body, run inside its transaction."""
        status = self._memory.status_registers
        if not instruction_bytes:
            raise ValueError("Nothing to import -- the program is empty.")
        if find_program_end(instruction_bytes) != len(instruction_bytes):
            raise ValueError(
                "This doesn't decode as one well-formed HP-41 program -- "
                "find_program_end() disagrees with the file's own length."
            )
        if not (MIN_SANE_R00 <= status.R00 <= PRIMARY_DATA_END):
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
            if entry.is_label:
                data[entry.index + 3] = 0x00

        # Step 5: where this goes, and what the outermost marker should
        # now link back to. `dot_end_to_convert` records whether Case A
        # applies (see _resolve_import_link()'s docstring) -- nothing
        # touches a live register yet.
        insertion_addr, link_addr, dot_end_to_convert = self._resolve_import_link()

        # Step 6: force a copied `.END.`-type trailing marker to a real END.
        trailing = chain_entries[0]
        if not trailing.is_label and trailing.end_type == 2:
            data[trailing.index + 2] &= 0x0F

        # Step 2 (continued): recompute the outermost marker's own link.
        self._relink_outermost_marker(
            data, chain_entries[-1], insertion_addr, link_addr
        )

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

        trailing_dest_addr = insertion_addr - trailing.index
        end_dr, end_bbb = divmod(
            trailing_dest_addr - end_marker_addr, REGISTER_SIZE_BYTES
        )
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
        """Raises ValueError if any global label name found by
        `walk_chain()` (in a program about to be imported) already exists
        somewhere in this memory's own global chain -- see
        `import_program()`'s step 3."""
        existing_names = {p.name for p in self.list_global_chain() if p.is_named}
        for entry in chain_entries:
            if entry.is_label and entry.name in existing_names:
                raise ValueError(
                    f"A global label named {entry.name!r} already exists "
                    "in this memory -- rename or delete the existing "
                    "program before importing this one."
                )

    def _resolve_import_link(self) -> tuple:
        """
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
        """
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
        outermost: ChainMarker,
        insertion_addr: int,
        link_addr: Optional[int],
    ):
        """`import_program()`'s step 2 finish: overwrites `outermost`'s
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
        insurance in case those bounds ever change."""
        if link_addr is None:
            new_bbb, new_dr = 0, 0
        else:
            outermost_dest_addr = insertion_addr - outermost.index
            new_dr, new_bbb = divmod(
                link_addr - outermost_dest_addr, REGISTER_SIZE_BYTES
            )
            if new_dr > 0x1FF:
                raise DM41MemoryError(
                    "This program lands too far from the existing program "
                    "chain to encode -- program memory may be unusually "
                    "large or fragmented."
                )
        index = outermost.index
        third = data[index + 2]
        data[index : index + 3] = encode_chain_marker(new_bbb, new_dr, third)

    def _check_import_room(self, insertion_addr: int, data_len: int) -> tuple:
        """`import_program()`'s step 7: works out where the fresh `.END.`
        marker would land -- register-aligned to the last 3 bytes of a
        register, right after `data_len` bytes written starting at
        `insertion_addr` and however much zero-padding gets it to that
        boundary -- and checks that against the Alarms/Key Assignments
        boundary. Returns `(end_marker_addr, next_free_addr)` if there's
        room; raises `DM41MemoryError` if not."""
        floor_reg = self._memory.alarms.end_exclusive
        program_end_addr = insertion_addr - data_len + 1
        next_free_addr = program_end_addr - 1
        end_marker_addr = next_free_addr - ((next_free_addr - 2) % REGISTER_SIZE_BYTES)
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
        """Re-derives every program in this region by scanning its opcodes
        forward from `top_addr()` to `.END.`, without trusting the chain's
        backward links. This is PACK's real job: a state written by something
        other than a calculator (or this app) can have those links zeroed or
        never set, so `list_global_chain()` finds no programs although
        well-formed code is there, and its labels can't be viewed, exported or
        assigned to keys.

        Only the R00 and `.END.` pointers are trusted; nothing about the marker
        bytes between them is.

        The stream is first compacted the way a real PACK does it (NULLs that
        don't separate two numbers removed, cached jump distances cleared --
        see `opcode_scan.compact_program_stream()`). Then, in a local copy,
        every marker's link is rewritten to the real distance back to the marker
        before it (0 for the first) and every END is marked packed; a `.END.`
        that was closing the newest program becomes a plain END. Only then is
        the stream split into per-program byte ranges, because
        `import_program()` relies on those links being right.

        Returns `(instruction_bytes, key_assignments)` per program, oldest first
        -- the shape `_rebuild()` takes -- or [] if program memory is empty or
        R00/`.END.` don't describe a real partition.

        Raises DM41MemoryError when the scan can't be sure it has found every
        program: non-zero bytes with no marker at all, a final label never
        closed by an END, or non-zero bytes after the last marker."""
        status = self._memory.status_registers
        r00 = status.R00
        dend = status.DotEnd
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

        # What a real PACK does to the program bytes themselves: delete
        # every NULL that isn't keeping two numbers apart and clear the
        # cached GTO/XEQ jump distances (opcode_scan.compact_program_stream
        # has the details and the evidence). This works on a local copy --
        # nothing live is touched until _rebuild() -- and it has to happen
        # before the markers are located and re-linked below, because
        # deleting bytes changes the distance between every marker.
        data = bytearray(compact_program_stream(bytes(data)))

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
        if last_marker.is_label:
            raise DM41MemoryError(
                "Program memory ends with a global label that's never "
                "closed by an END -- pack() can't safely determine where "
                "that program ends."
            )
        tail_start = last_marker.index + 3
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
            distance_bytes = 0 if i == 0 else marker.index - markers[i - 1].index
            distance_registers, bbb = divmod(distance_bytes, REGISTER_SIZE_BYTES)
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
            start = marker.index
            third_byte = marker.third_byte
            if not marker.is_label:
                # Every END comes out of a real PACK marked "packed" (low
                # nibble 9, versus d/f for "needs packing"), and a `.END.`
                # that was closing the newest program directly becomes a
                # plain END -- _rebuild() writes the empty permanent
                # `.END.` after it (see repack()).
                high = third_byte & 0xF0
                third_byte = (0x00 if high == 0x20 else high) | 0x09
            data[start : start + 3] = encode_chain_marker(
                bbb, distance_registers, third_byte
            )

        programs = []
        pending_labels = []
        group_start_index = 0

        for marker in markers:
            if marker.is_label:
                pending_labels.append((marker.name, marker.key_assignment))
                continue

            marker_last_byte_index = marker.index + 2
            if (
                marker is last_marker
                and not pending_labels
                and not any(data[group_start_index : marker.index])
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

    def _rebuild(self, programs: list, collapse_trailing_end: bool = True):
        """Rewrites this region from scratch so it holds exactly `programs`: a
        list of `(instruction_bytes, key_assignments)`, oldest first, where
        `key_assignments` maps each label name to its key byte (only labels
        that have one). Shared by `remove_program()` and `repack()`.

        Clears everything from the top of the Alarms buffer up to R00, resets
        `.END.` to R00 (an empty partition), then `import_program()`s each
        program in order, so the splicing logic lives in one place. Every entry
        must be well-formed with no duplicate label names, which holds because
        they all coexisted in this memory before the call.

        `import_program()` zeroes label key bytes; this restores each label's
        original byte afterwards. The KEYFLAGS bits live elsewhere and aren't
        touched, so key assignments survive unchanged.

        `collapse_trailing_end` runs `_collapse_trailing_end_into_dot_end()`
        afterwards, which `remove_program()` wants; `repack()` passes False,
        since a real PACK never does that."""
        status = self._memory.status_registers
        for reg in range(self._memory.alarms.end_exclusive, status.R00):
            self._memory.set_register(reg, zero_register())
        status.set_DotEnd(status.R00)

        for instruction_bytes, key_assignments in programs:
            imported = self.import_program(instruction_bytes)
            for label in imported.labels:
                key_byte = key_assignments.get(label.name)
                if key_byte:
                    self._write_program_key_byte(
                        label.header_addr, label.header_offset, key_byte
                    )

        if programs and collapse_trailing_end:
            self._collapse_trailing_end_into_dot_end()

    def _collapse_trailing_end_into_dot_end(self):
        """After `_rebuild()`, the newest program ends with its own END followed
        by a separate, empty `.END.` (`import_program()` always writes both).
        When that END already sits where a `.END.` can go -- the last 3 bytes
        of a register, offset 4 -- this turns it into the `.END.` itself (only
        its end-type nibble changes; its link is already right), moves the
        `.END.` pointer to it and frees the space the separate `.END.` used.
        That restores the compact layout of a state whose newest program is
        closed by `.END.` directly (e.g. twolabels.dm41). Otherwise memory is
        left as `import_program()` wrote it: correct, just less compact.

        Called only by `remove_program()`, and only when `_rebuild()` imported
        at least one program. `repack()` doesn't use it: a real PACK always
        leaves an END plus an empty `.END.` (docs/program.md "What a real PACK
        does")."""
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

        # Programs grow downward, so the old, separate `.END.` register
        # sits at a LOWER address than the END that just became `.END.`:
        # it (and anything between) is the stale space to give back.
        old_dot_end_reg = status.DotEnd
        status.set_DotEnd(pred_reg)
        for stale in range(old_dot_end_reg, pred_reg):
            self._memory.set_register(stale, zero_register())

    def remove_program(self, program: Program):
        """Removes `program` and closes up the gap it leaves, so the remaining
        programs stay contiguous (GitHub issue #6).

        Programs grow down from R00 and older ones never move, so removing
        anything but the newest would otherwise leave space between R00 and the
        remaining programs that no region accounts for. Instead the whole area
        is rebuilt with every other program (`_rebuild()`).

        Also clears the KEYFLAGS bit for each of `program`'s labels that holds
        a key assignment, since that key is no longer assigned once the label's
        header is gone. Key Assignment Register entries are unrelated and left
        alone.

        Raises ValueError if `program` doesn't match the current program list
        (it is stale, from before the state changed).

        All-or-nothing: if anything raises part-way through (e.g. a kept
        program's bytes fail get_program_bytes()'s cross-check), this Memory is
        left exactly as it was (see Memory.transaction())."""
        with self._memory.transaction():
            self._remove_program(program)

    def _remove_program(self, program: Program):
        """remove_program()'s body, run inside its transaction."""
        key_assignments_region = self._memory.key_assignments
        programs = self.list_programs()
        match = next(
            (
                p
                for p in programs
                if p.start_addr == program.start_addr
                and p.start_offset == program.start_offset
            ),
            None,
        )
        if match is None:
            raise ValueError(
                "This program entry doesn't match the current program "
                "list -- it may be stale (from a list_programs() call "
                "taken before the state changed)."
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
                label.name: label.key_assignment
                for label in p.labels
                if label.key_assignment
            }
            keep.append((raw, key_assignments))

        self._rebuild(keep)

    def repack(self):
        """The program-memory half of `Memory.pack()` (GitHub issue #31).

        Rebuilds the global chain from a forward opcode scan
        (`_forward_scan_programs()`) rather than the existing backward links, so
        a global label that is present but not chain-linked becomes visible to
        `list_programs()`/`list_global_chain()` and assignable to a key, then
        rewrites every program tightly against R00 (`_rebuild()`).

        The result matches a real PACK, as captured from a DM41L/DM41X
        (tests/test_pack_hardware.py): standalone NULLs removed (except one
        between two numbers), cached GTO/XEQ distances cleared, every END marked
        packed and the chain relinked, and the newest program closed by a real
        END with an empty `.END.` on the next register boundary -- even when it
        was closed by `.END.` directly before. So a packed buffer packs to
        itself, and packing can cost a register (`pack()` then reports a
        negative reclaim).

        With no programs at all, program memory is left untouched.

        Raises DM41MemoryError if `_forward_scan_programs()` can't safely
        determine program memory's content; nothing is changed then. The
        rebuild after it is all-or-nothing too (see Memory.transaction())."""
        keep = self._forward_scan_programs()
        if keep:
            with self._memory.transaction():
                self._rebuild(keep, collapse_trailing_end=False)

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
        """First named global label matching `name` (oldest-created, i.e.
        list_global_chain()'s own order, in the rare case of a duplicate
        name) -- shared by set_program_key_assignment()/
        clear_program_key_assignment()/get_program_for_key(). A key
        assignment lives on one label's own header (sec 4.6/5.2)
        regardless of how many labels its program has, so this works off
        the flat per-label chain, not the grouped `list_programs()`."""
        for program in self.list_global_chain():
            if program.is_named and program.name == name:
                return program
        return None

    def _write_program_key_byte(self, header_addr: int, header_offset: int, value: int):
        """Overwrites the key-assignment byte (the 4th byte, sec 4.2/5.2)
        of the global-label header starting at (header_addr,
        header_offset) -- the write-side counterpart to
        _decode_label_name() reading it. `addr_for`/`pos_for` convert to
        and from the linear address space so this doesn't need its own
        register-boundary-crossing loop (see read_bytes_forward for why
        register offset and address run in opposite directions)."""
        reg, offset = self.pos_for(self.addr_for(header_addr, header_offset) - 3)
        data = bytearray(self._memory.get_register(reg).get_bytes())
        data[offset] = value
        self._memory.set_register(reg, Register(data=bytes(data)))

    def clear_assignments_for_key_byte(
        self, key_byte: int, except_name: Optional[str] = None
    ):
        """Writes 0x00 (unassigned) into the header of every global label
        currently holding `key_byte`, except one named `except_name` (used
        by set_program_key_assignment() while moving that program itself
        onto this key -- its own old byte is handled separately there).
        Does not touch KEYFLAGS -- callers own that, since the bit should
        usually end up set (by whatever new assignment is replacing these)
        rather than cleared. Also called by
        KeyAssignments.set_assignment(), which is claiming the same key
        for the other storage mechanism."""
        for program in self.list_global_chain():
            if (
                program.is_named
                and program.key_assignment == key_byte
                and program.name != except_name
            ):
                self._write_program_key_byte(
                    program.header_addr, program.header_offset, 0x00
                )

    def get_program_for_key(
        self, key_number: int, shifted: bool
    ) -> Optional[ProgramInfo]:
        """Looks up the global label (if any) assigned to `key_number`/
        `shifted` via sec 4.6 -- the counterpart to
        KeyAssignments.get_assignment() for the other storage mechanism
        (sec 4.1). Per the real lookup order (sec 4.7), a Key Assignment
        Register entry on the same key always takes priority over a
        global-label one, but this method only checks global labels --
        callers wanting "whatever's actually assigned to this key" should
        check KeyAssignments.get_assignment() first and fall back to this
        (see gui/key_assignments_tab.py)."""
        key_byte = self._memory.key_assignments.key_byte_for(key_number, shifted)
        for program in self.list_global_chain():
            if program.is_named and program.key_assignment == key_byte:
                return program
        return None

    def set_program_key_assignment(self, name: str, key_number: int, shifted: bool):
        """Assigns the global label `name` to `key_number`/`shifted` (sec
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

        Raises ValueError if no global label named `name` exists."""
        key_assignments_region = self._memory.key_assignments
        program = self.find_program_by_name(name)
        if program is None:
            raise ValueError(f"No global label named {name!r} found")

        key_byte = key_assignments_region.key_byte_for(key_number, shifted)

        # Moving this same program off whatever key it held before, if any
        # (0x00 means "never assigned" -- nothing to move off of).
        if program.key_assignment:
            try:
                old_key_number, old_shifted = (
                    key_assignments_region.key_number_for_byte(program.key_assignment)
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
        """Removes global label `name`'s key assignment (sec 4.6), if it
        has one -- writes 0x00 back into its header and clears the
        corresponding KEYFLAGS bit. No-op if the label has no key
        assignment. Raises ValueError if no global label named `name`
        exists."""
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
        self._write_program_key_byte(program.header_addr, program.header_offset, 0x00)
