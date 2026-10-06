"""
Tests for Memory.pack() -- GitHub issue #31 ("DM41L_Explorer needs PACK
functionality"). Key Assignments/Alarms already stay canonically packed
as a side effect of every set_key_assignment()/delete_key_assignment()
call (see _encode_key_assignment_entries()'s docstring); pack() re-runs
that explicitly and does the equivalent for program memory.

Program memory itself is handled by _forward_scan_programs() +
_rebuild_program_memory() (the latter shared with Memory.remove_program()
-- see test_program_remove.py for that other caller). _forward_scan_programs()
is the part that matters most here: per the user's own correction to this
method's first version ("Packing needs to (re)build the program chain so
that global labels can be viewed and assigned to keys"), pack() does not
just compact whatever list_programs()'s existing backward-chain walk
already recognizes -- it re-derives the whole chain from the raw opcodes,
forward, independent of whatever the existing backward-chain-link fields
say. lander.dm41/targ.dm41 (below) are real-world states -- from the
user's own investigation (project notes,
pack_anomaly_investigation_2026-08-24.md) into a real DM41L, comparing
against a third-party tool's export -- whose backward chain is entirely
missing even though their real, well-formed FOCAL programs (LANDER/TARG)
are physically present; lander-packed.dm41/targ-packed.dm41 are that
same content after a REAL PACK on real hardware, used below as ground
truth for what the repaired content should be.
"""

from pathlib import Path

import pytest

from memory import Memory, DM41MemoryError, Register
from memory.opcode_scan import clear_jump_caches
from memory.program_text import encode_program_txt
from fixture_loading import load_fixture

DATA_DIR = Path(__file__).parent / "data"

ALL_FIXTURES = sorted(DATA_DIR.glob("*.dm41"))

# Real-world states whose backward chain is missing entirely -- pack() is
# *expected* to change what list_programs() reports for these (that's
# the whole point of the fix), so they're excluded from the "never
# changes what's already visible" sweep below and covered by their own
# dedicated tests instead.
REPAIR_FIXTURES = {"lander.dm41", "targ.dm41"}
STABLE_FIXTURES = [p for p in ALL_FIXTURES if p.name not in REPAIR_FIXTURES]


# -- Safety/idempotence across every real sample state ------------------------


def _listings(memory):
    """Each program's instructions as text, minus the closing 'END ;nnn
    BYTES' line. NULLs and cached jump distances never show up in a
    listing -- which is exactly what a real PACK is allowed to change
    (see test_pack_hardware.py) -- while every real instruction does."""
    return [
        encode_program_txt(memory.programs.get_program_bytes(p)).splitlines()[:-1]
        for p in memory.programs.list_programs()
    ]


@pytest.mark.parametrize("path", STABLE_FIXTURES, ids=lambda p: p.name)
def test_pack_never_loses_or_reorders_programs(path):
    # Excludes REPAIR_FIXTURES -- see test_pack_repairs_a_broken_backward_chain
    # below for lander.dm41/targ.dm41, where pack() is supposed to change
    # what list_programs() reports (that's the fix). A program's byte length
    # CAN shrink (a real PACK deletes its NULLs), so only its identity and
    # its instructions are compared.
    memory = load_fixture(path)
    before_names = [p.names_label for p in memory.programs.list_programs()]
    before_listings = _listings(memory)

    memory.pack()

    assert [p.names_label for p in memory.programs.list_programs()] == before_names
    assert _listings(memory) == before_listings


# A real PACK can use ONE MORE register than it started with: a newest
# program the source closed with the permanent .END. directly comes out
# as a real END plus an empty .END. -- real hardware did exactly that to
# targ.dm41 (79 -> 80 registers, targ-packed.dm41). twolabels.dm41 is in
# the same state (.END. closing the program, flagged "needs packing", like
# dm41x_pack_dotend.dm41) and a real DM41L PACK of that very file cost one
# register too (twolabels-packed.dm41).
COSTS_ONE_REGISTER = {"targ.dm41", "twolabels.dm41"}


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_pack_never_reports_a_negative_reclaim_except_for_dot_end_closed_programs(path):
    # Guards the regression an earlier version of the newest-program
    # collapse optimization caused: pack() moved DotEnd the WRONG way
    # (using *more* space) on a buffer that did not need that. The only
    # legitimate cost is the one register above, and never more.
    memory = load_fixture(path)
    freed = memory.pack()
    if path.name in COSTS_ONE_REGISTER:
        assert freed == -1, f"{path.name}: expected PACK to cost one register ({freed})"
    else:
        assert freed >= 0, f"{path.name}: pack() reported a NEGATIVE reclaim ({freed})"


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_pack_is_idempotent(path):
    # Packing an already-packed buffer a second time should never find
    # anything left to reclaim -- true for the REPAIR_FIXTURES too, once
    # their first pack() has rebuilt a real backward chain for them.
    memory = load_fixture(path)
    memory.pack()
    assert memory.pack() == 0


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_pack_round_trips_through_to_string_and_from_string(path):
    memory = load_fixture(path)
    memory.pack()
    expected = [(p.names_label, p.length) for p in memory.programs.list_programs()]

    reloaded = Memory.from_string(memory.to_string(), profile=memory.profile)
    assert [(p.names_label, p.length) for p in reloaded.programs.list_programs()] == expected


# -- twolabels.dm41: the specific alignment regression ------------------------


def test_pack_on_twolabels_closes_the_program_with_end_and_an_empty_dot_end():
    # FIRST/SECOND (twolabels.dm41) has no explicit END at all -- only
    # the permanent .END. closes it (flagged "needs packing"). A real PACK
    # of this very file on a DM41L (twolabels-packed.dm41, and likewise
    # dm41x_pack_dotend.dm41 -> -packed; see test_pack_hardware.py) wrote
    # a real END right after the last instruction and left .END. as a
    # separate, empty marker. The old expectation here -- that
    # .END.-terminated is "already the most compact form" and PACK keeps
    # it -- was never checked on hardware and contradicted those captures.
    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    before = memory.programs.list_programs()[0]
    assert before.terminator == ".END."
    before_dot_end = memory.status_registers.DotEnd()

    freed = memory.pack()

    after = memory.programs.list_programs()
    assert len(after) == 1
    assert after[0].terminator == "END"
    assert freed == -1
    assert memory.status_registers.DotEnd() == before_dot_end - 1


# -- Key Assignments / Alarms --------------------------------------------------


def test_pack_leaves_correct_key_assignment_entries_unchanged():
    memory = Memory.from_file(DATA_DIR / "keyassigns.dm41")
    before = memory.key_assignments.decode_entries()
    memory.pack()
    assert memory.key_assignments.decode_entries() == before


def test_pack_leaves_key_assignments_end_and_alarms_end_unchanged_when_canonical():
    memory = Memory.from_file(DATA_DIR / "alarmtest.dm41")
    before_key_end = memory.key_assignments.end_exclusive
    before_alarms_end = memory.alarms.end_exclusive
    memory.pack()
    assert memory.key_assignments.end_exclusive == before_key_end
    assert memory.alarms.end_exclusive == before_alarms_end


# -- Programs with key assignments (sec 4.6) ----------------------------------


def test_pack_preserves_global_label_key_assignments():
    memory = Memory.from_file(DATA_DIR / "global-key-assignments.dm41")
    before = {
        p.names_label: p.labels[0].key_assignment for p in memory.programs.list_programs()
    }
    memory.pack()
    after = {
        p.names_label: p.labels[0].key_assignment for p in memory.programs.list_programs()
    }
    assert after == before

    # And the KEYFLAGS bits themselves are untouched too.
    for p in memory.programs.list_programs():
        key_number, shifted = memory.key_assignments.key_number_for_byte(p.labels[0].key_assignment)
        assert memory.key_assignments.get_key_flag(key_number, shifted) is True


# -- Edge cases ----------------------------------------------------------------


def test_pack_on_empty_program_memory_is_a_safe_no_op():
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    assert memory.programs.list_programs() == []
    before_dot_end = memory.status_registers.DotEnd()
    freed = memory.pack()
    assert freed == 0
    assert memory.programs.list_programs() == []
    assert memory.status_registers.DotEnd() == before_dot_end


def test_pack_on_a_freshly_constructed_memory_does_not_raise():
    # A brand-new Memory() (no state loaded at all) has no sane R00/.END.
    # partition -- pack() should still repack Key Assignments/Alarms
    # (trivially empty) without raising, and leave program memory alone.
    memory = Memory()
    memory.pack()  # must not raise
    assert memory.programs.list_programs() == []


# -- Rebuilding a broken/missing backward chain (the pack() correction) ------
#
# The scenario the user's own real-hardware investigation identified
# (pack_anomaly_investigation_2026-08-24.md, referenced above): a state
# written by a tool other than this app or a real HP-41/DM41L can leave
# the backward chain-link fields zeroed or never set at all, even though
# real FOCAL program bytes are physically present. Before this fix,
# list_programs()/list_global_chain() reported nothing at all for such a
# state, and nothing in it could be assigned to a key. lander.dm41/
# targ.dm41 are exactly that scenario; lander-packed.dm41/targ-packed.dm41
# are the same content after a real PACK on real hardware.


@pytest.mark.parametrize(
    "unpacked_name,label,real_length",
    [("lander.dm41", "LANDER", 771), ("targ.dm41", "TARG", 552)],
)
def test_pack_repairs_a_broken_backward_chain(unpacked_name, label, real_length):
    memory = Memory.from_file(DATA_DIR / unpacked_name)

    # Before the fix: the label is physically present but invisible.
    assert memory.programs.list_programs() == []
    assert memory.programs.list_global_chain() == []

    memory.pack()

    programs = memory.programs.list_programs()
    assert [p.names_label for p in programs] == [label]
    # Exactly the length a real hardware PACK produced -- since pack()
    # also deletes NULLs and puts the END right after the last instruction
    # (see test_pack_hardware.py), there's no padding difference left.
    assert programs[0].length == real_length


@pytest.mark.parametrize(
    "unpacked_name,packed_name",
    [("lander.dm41", "lander-packed.dm41"), ("targ.dm41", "targ-packed.dm41")],
)
def test_pack_repaired_bytes_match_real_hardware_content(unpacked_name, packed_name):
    # The repaired program (opcodes, embedded labels, key bytes, and its
    # closing END marker) must match a real hardware PACK byte for byte.
    # The one allowed difference: pack() clears every GTO/XEQ's cached
    # jump distance, which a real PACK only does when something moved.
    memory = Memory.from_file(DATA_DIR / unpacked_name)
    memory.pack()
    mine = memory.programs.get_program_bytes(memory.programs.list_programs()[0])

    reference = Memory.from_file(DATA_DIR / packed_name)
    real = reference.programs.get_program_bytes(reference.programs.list_programs()[0])

    assert mine == clear_jump_caches(real)


@pytest.mark.parametrize("unpacked_name,label", [("lander.dm41", "LANDER"), ("targ.dm41", "TARG")])
def test_pack_repaired_label_can_be_assigned_to_a_key(unpacked_name, label):
    # The actual point of the fix: a repaired label isn't just visible in
    # list_programs(), it can be assigned to a key like any other.
    memory = Memory.from_file(DATA_DIR / unpacked_name)
    memory.pack()
    memory.programs.set_program_key_assignment(label, key_number=11, shifted=False)
    assert memory.programs.get_program_for_key(11, shifted=False).name == label


# -- Corrupt/unrecoverable data: raise rather than guess -----------------------


def _zero_marker(memory, index_from_top, count=3):
    top_addr = memory.programs.addr_for(memory.status_registers.R00() - 1, 0)
    memory.programs.write_bytes_forward(top_addr - index_from_top, bytes(count))


def test_pack_raises_when_no_marker_can_be_found_at_all():
    # simple.dm41's own LBL, explicit END, and separate .END. markers all
    # zeroed out, but real opcode bytes still sit between them -- no
    # marker at all can be found even though real content is present.
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    before_dot_end = memory.status_registers.DotEnd()
    for index in (0, 23, 32):
        _zero_marker(memory, index)

    with pytest.raises(DM41MemoryError):
        memory.pack()
    # Program memory itself must be untouched by a call that raises.
    assert memory.status_registers.DotEnd() == before_dot_end


def test_pack_raises_when_the_last_marker_is_an_unterminated_label():
    # twolabels.dm41's own permanent .END. -- its only terminator -- is
    # zeroed out, leaving its SECOND label as the last thing the forward
    # scan can find, with nothing closing it.
    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    before_dot_end = memory.status_registers.DotEnd()
    program = memory.programs.list_programs()[0]
    raw = memory.programs.get_program_bytes(program)
    _zero_marker(memory, len(raw) - 3)

    with pytest.raises(DM41MemoryError):
        memory.pack()
    assert memory.status_registers.DotEnd() == before_dot_end


def test_pack_raises_when_unrecognized_data_follows_the_last_marker():
    # DotEnd() moved one register lower than where simple.dm41's real
    # .END. actually sits, with a stray non-zero byte in that extra
    # register -- pack() can't tell whether that's real, unparsed
    # content or just corruption, so it refuses to guess.
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    memory.status_registers.set_DotEnd(memory.status_registers.DotEnd() - 1)
    before_dot_end = memory.status_registers.DotEnd()
    extra_reg = memory.status_registers.DotEnd()
    data = bytearray(memory.get_register(extra_reg).get_bytes())
    data[3] = 0x55
    memory.set_register(extra_reg, Register(data=bytes(data)))

    with pytest.raises(DM41MemoryError):
        memory.pack()
    assert memory.status_registers.DotEnd() == before_dot_end
