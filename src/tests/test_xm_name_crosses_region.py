"""
A file whose name is on one XM region's last usable register, with its
header at the top of the next region (GitHub issue #68).

The calculator treats extended memory as one continuous run of usable
registers -- each region from its ceiling down, skipping its pointer
register -- and a file's name, header and data are consecutive positions
in that run. This app once let only a file's *data* continue into the next
region, so it could neither read nor write this layout.

Three real DM41L captures, each a 124- or 125-register FILL file filling
region 0 and a second file created on the calculator after it:

  xm0-full.dm41           FILL alone (124 registers): 0x41 is left free.
  xm_file_cross_115.dm41  + FILL2, 115 registers: name 0x41, header 0x2EF.
  xm_file_cross.dm41      FILL1 + FILL2, 124 registers each: name 0x41,
                          header 0x2EF.
  xm_file_cross_2.dm41    FILL1 + FILL2, 125 registers each: FILL1's data
                          ends on 0x41, so FILL2 starts whole in region 1.
                          This layout was always read correctly; it is the
                          control case.
"""

from pathlib import Path

import pytest

from memory import DM41L, DM41X, DM41MemoryError, Memory, EOM_REGISTER_HEX

DATA_DIR = Path(__file__).parent / "data"

# (capture, FILL2's name, header, lowest data register, EOM register),
# decoded by hand from the captures.
CAPTURES = [
    ("xm_file_cross_115.dm41", 0x041, 0x2EF, 0x27C, 0x27B),
    ("xm_file_cross.dm41", 0x041, 0x2EF, 0x273, 0x272),
    ("xm_file_cross_2.dm41", 0x2EF, 0x2EE, 0x271, 0x270),
]


def _load(name, profile=DM41L):
    return Memory.from_file(DATA_DIR / name, profile=profile)


@pytest.mark.parametrize("profile", [DM41L, DM41X], ids=lambda p: p.name)
@pytest.mark.parametrize("name, name_addr, header, low, eom", CAPTURES)
def test_the_captures_read(name, name_addr, header, low, eom, profile):
    memory = _load(name, profile)
    files = memory.extended_memory.list_files()

    assert [f.name.strip() for f in files][-1] == "FILL2"
    second = files[-1]
    assert second.name_addr == name_addr
    assert second.header_addr == header
    assert second.data_start == low
    assert second.data_end == header - 1
    assert second.spans_regions is False
    assert memory.get_register(eom).get_hex() == EOM_REGISTER_HEX
    assert second.data_start - 1 == eom


@pytest.mark.parametrize("name, name_addr, header, low, eom", CAPTURES)
def test_the_captures_are_rebuilt_where_the_calculator_put_them(
    name, name_addr, header, low, eom
):
    """Removing FILL2 and adding it back puts every register of it -- name,
    header, data and the EOM after it -- where the calculator did, and
    region 1's pointer register as the calculator wrote it.

    Not compared: region 0's pointer register, whose WW is the index of
    the file open on the calculator and whose PPP is not yet understood
    (docs/extended_memory.md), and the AAA field of the headers (the
    calculator zeroed it in these captures)."""
    capture = _load(name)
    memory = _load(name)
    xm = memory.extended_memory
    fill2 = xm.list_files()[-1]

    xm.remove_file(fill2.header_addr)
    added = xm.add_file("FILL2", xm.TYPE_DATA, data_lines=fill2.get_data_lines())

    assert (added.name_addr, added.header_addr) == (name_addr, header)
    assert added.segments == fill2.segments
    for addr in [name_addr, eom, 0x201, *range(low, header)]:
        assert memory.get_register(addr) == capture.get_register(addr), hex(addr)
    assert xm.list_files()[-1].segments == fill2.segments


def test_a_file_can_be_added_to_a_full_region_0():
    """The Add File case from the issue: xm0-full.dm41 leaves only 0x41
    free in region 0, and adding a file used to fail partway through."""
    memory = _load("xm0-full.dm41")
    xm = memory.extended_memory

    added = xm.add_file("NEXT", xm.TYPE_DATA, data_lines=["1", "2", "3"])

    assert (added.name_addr, added.header_addr) == (0x041, 0x2EF)
    assert added.segments == [[0x2EC, 0x2EE]]
    assert memory.get_register(0x2EB).get_hex() == EOM_REGISTER_HEX
    # Region 0's pointer register is intact, and now shows region 1 in use.
    assert memory.get_register(0x040).get_hex() == "000010002ef0bf"
    assert [f.name.strip() for f in xm.list_files()] == ["FILL", "NEXT"]
    assert xm.list_files()[-1].get_data_lines() == ["1.0", "2.0", "3.0"]


@pytest.mark.parametrize(
    "profile, first",
    [
        (DM41L, 123),
        (DM41L, 124),
        (DM41L, 125),
        (DM41X, 124),
        (DM41X, 361),
        (DM41X, 362),
        (DM41X, 363),
    ],
)
def test_every_split_of_name_header_and_data_round_trips(profile, first):
    """A first file of `first` registers puts the second file's name, its
    header or its data on the far side of a region boundary (124 and 362
    are the name-header splits that used to fail)."""
    memory = Memory(profile=profile)
    xm = memory.extended_memory
    xm.add_file("A", xm.TYPE_DATA, numbers=[float(i) for i in range(first)])
    xm.add_file("B", xm.TYPE_DATA, numbers=[7.0, 8.0])

    files = xm.list_files()
    assert [f.get_numbers() for f in files] == [
        [float(i) for i in range(first)],
        [7.0, 8.0],
    ]

    # And a third file after it, then the second removed again.
    xm.add_file("C", xm.TYPE_DATA, numbers=[9.0])
    xm.remove_file(xm.list_files()[1].header_addr)
    assert [f.name.strip() for f in xm.list_files()] == ["A", "C"]


def test_a_name_slot_with_no_room_for_a_header_is_refused():
    """On a DM41L the run ends at 0x202. A name there would need a header
    past it, so the add is refused before anything is written."""
    memory = Memory(profile=DM41L)
    xm = memory.extended_memory
    run = 0x0BF - 0x040 + 0x2EF - 0x201  # usable registers in all
    # Name and header, then data up to the second-last usable register.
    xm.add_file("A", xm.TYPE_DATA, numbers=[1.0] * (run - 3))
    assert xm._next_slot()[0] == 0x202
    before = memory.to_string()

    with pytest.raises(DM41MemoryError, match="Not enough free space"):
        xm.add_file("B", xm.TYPE_DATA, numbers=[1.0])
    assert memory.to_string() == before
