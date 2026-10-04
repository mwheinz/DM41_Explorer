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
say. lander.dm41/targ.dm41 (below) are real-world dumps -- from the
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
from fixture_loading import load_fixture

DATA_DIR = Path(__file__).parent / "data"

ALL_FIXTURES = sorted(DATA_DIR.glob("*.dm41"))

# Real-world dumps whose backward chain is missing entirely -- pack() is
# *expected* to change what list_programs() reports for these (that's
# the whole point of the fix), so they're excluded from the "never
# changes what's already visible" sweep below and covered by their own
# dedicated tests instead.
REPAIR_FIXTURES = {"lander.dm41", "targ.dm41"}
STABLE_FIXTURES = [p for p in ALL_FIXTURES if p.name not in REPAIR_FIXTURES]


# -- Safety/idempotence across every real sample dump ------------------------


@pytest.mark.parametrize("path", STABLE_FIXTURES, ids=lambda p: p.name)
def test_pack_never_loses_or_reorders_programs(path):
    # Excludes REPAIR_FIXTURES -- see test_pack_repairs_a_broken_backward_chain
    # below for lander.dm41/targ.dm41, where pack() is supposed to change
    # what list_programs() reports (that's the fix).
    memory = load_fixture(path)
    before = [(p.names_label, p.length) for p in memory.programs.list_programs()]
    before_bytes = [memory.programs.get_program_bytes(p) for p in memory.programs.list_programs()]

    memory.pack()

    after_programs = memory.programs.list_programs()
    assert [(p.names_label, p.length) for p in after_programs] == before
    assert [memory.programs.get_program_bytes(p) for p in after_programs] == before_bytes


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_pack_never_reports_a_negative_reclaim(path):
    # The regression this guards: an earlier version of the newest-
    # program collapse optimization (_collapse_trailing_end_into_dot_end())
    # didn't check register alignment before rewriting DotEnd, and could
    # move it the WRONG way (using *more* register space than before,
    # e.g. on twolabels.dm41 -- a single program terminated only by the
    # permanent .END., already in its most-compact form). pack() must
    # never make memory less free than it started.
    memory = load_fixture(path)
    freed = memory.pack()
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


def test_pack_on_twolabels_stays_at_the_optimal_dot_end_terminated_layout():
    # FIRST/SECOND (twolabels.dm41) has no explicit END at all -- only
    # the permanent .END. terminates it, already the most compact form a
    # single newest program can take (Program's own docstring). This is
    # exactly the fixture that caught the alignment bug during
    # development (see test_pack_never_reports_a_negative_reclaim).
    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    before = memory.programs.list_programs()[0]
    assert before.terminator == ".END."
    before_dot_end = memory.status_registers.DotEnd()

    freed = memory.pack()

    after = memory.programs.list_programs()[0]
    assert freed == 0
    assert after.terminator == ".END."
    assert memory.status_registers.DotEnd() == before_dot_end


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
    # A brand-new Memory() (no dump loaded at all) has no sane R00/.END.
    # partition -- pack() should still repack Key Assignments/Alarms
    # (trivially empty) without raising, and leave program memory alone.
    memory = Memory()
    memory.pack()  # must not raise
    assert memory.programs.list_programs() == []


# -- Rebuilding a broken/missing backward chain (the pack() correction) ------
#
# The scenario the user's own real-hardware investigation identified
# (pack_anomaly_investigation_2026-08-24.md, referenced above): a dump
# written by a tool other than this app or a real HP-41/DM41L can leave
# the backward chain-link fields zeroed or never set at all, even though
# real FOCAL program bytes are physically present. Before this fix,
# list_programs()/list_global_chain() reported nothing at all for such a
# dump, and nothing in it could be assigned to a key. lander.dm41/
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
    # Recovered content may be a few zero-padding bytes longer than a
    # real hardware PACK's own register-alignment choice (see
    # test_pack_repaired_bytes_match_real_hardware_content below) but
    # never shorter -- nothing real was dropped.
    assert programs[0].length >= real_length


@pytest.mark.parametrize(
    "unpacked_name,packed_name",
    [("lander.dm41", "lander-packed.dm41"), ("targ.dm41", "targ-packed.dm41")],
)
def test_pack_repaired_bytes_match_real_hardware_content(unpacked_name, packed_name):
    # The repaired program's own real content (opcodes, embedded labels,
    # key bytes) must match a real hardware PACK exactly. The only
    # allowed difference is *where* harmless zero-alignment padding sits
    # in front of the final chain marker -- an already-accepted tradeoff
    # of rebuilding programs through import_program() (see
    # _collapse_trailing_end_into_dot_end()'s own docstring) that has
    # nothing to do with this repair specifically.
    memory = Memory.from_file(DATA_DIR / unpacked_name)
    memory.pack()
    mine = memory.programs.get_program_bytes(memory.programs.list_programs()[0])

    reference = Memory.from_file(DATA_DIR / packed_name)
    real = reference.programs.get_program_bytes(reference.programs.list_programs()[0])

    assert len(mine) >= len(real)
    padding = len(mine) - len(real)
    # Real content (everything except the trailing marker) must match
    # exactly; only zero padding may separate it from mine's own marker.
    assert mine[: len(real) - 3] == real[:-3]
    assert mine[len(real) - 3 : len(real) - 3 + padding] == bytes(padding)
    # And mine's own trailing marker must still be a valid one (0xC0-0xCD).
    assert 0xC0 <= mine[-3] <= 0xCD


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
