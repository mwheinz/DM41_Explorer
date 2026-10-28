"""
Tests for program files that hold several programs, each closed by its own
END -- a whole application, say (#71): decoding them
(program_files.decode_program_raw()/decode_program_dat()/decode_program_txt()),
cutting the result into programs (split_programs()), and importing them all
at once (ProgramMemory.import_programs()) -- plus the other direction,
writing several programs into one file.

There is no multi-program capture from another tool yet, so the files are
built here from single programs the other tests already trust: APPTEST
(simple.dm41, see test_program_export.py), xrom.raw (label "DM41X") and
tower.raw (no global label).
"""

from pathlib import Path

import pytest

from memory import (
    DM41MemoryError,
    Memory,
    decode_program_dat,
    decode_program_raw,
    decode_program_txt,
    encode_program_dat,
    encode_program_ppc,
    encode_program_raw,
    encode_program_txt,
    split_programs,
)

DATA_DIR = Path(__file__).parent / "data"

APPTEST_BYTES = bytes.fromhex("c000f8004150505445535410021140111010468475b200c40309")


def _xrom_bytes():
    return decode_program_raw((DATA_DIR / "xrom.raw").read_bytes())


def _tower_bytes():
    return decode_program_raw((DATA_DIR / "tower.raw").read_bytes())


def _three_programs():
    return [APPTEST_BYTES, _xrom_bytes(), _tower_bytes()]


# -- Decoding and splitting -------------------------------------------------


def test_raw_file_with_three_programs_splits_back_into_them():
    programs = _three_programs()
    raw = encode_program_raw(b"".join(programs))
    assert split_programs(decode_program_raw(raw)) == programs


def test_raw_file_with_one_program_still_decodes():
    raw = encode_program_raw(APPTEST_BYTES)
    assert split_programs(decode_program_raw(raw)) == [APPTEST_BYTES]


def test_raw_file_with_several_programs_rejects_corrupt_checksum():
    data = bytearray(encode_program_raw(b"".join(_three_programs())))
    data[sum(map(len, _three_programs()))] ^= 0x01
    with pytest.raises(DM41MemoryError):
        decode_program_raw(bytes(data))


def test_raw_file_with_several_programs_rejects_missing_checksum():
    with pytest.raises(DM41MemoryError, match="no checksum byte"):
        decode_program_raw(APPTEST_BYTES + _xrom_bytes())


def test_dat_and_ppc_files_with_several_programs_split_back_into_them():
    programs = _three_programs()
    code = b"".join(programs)
    assert split_programs(decode_program_dat(encode_program_dat(code))) == programs
    assert split_programs(decode_program_dat(encode_program_ppc(code))) == programs


def test_txt_file_with_two_programs_compiles_each_like_on_its_own():
    first = 'LBL "A"\nSIN\nEND\n'
    second = '; second program\nLBL "B"\nCOS\nEND\n'
    programs = split_programs(decode_program_txt((first + "\n" + second).encode()))
    assert programs == [
        decode_program_txt(first.encode()),
        decode_program_txt(second.encode()),
    ]


def test_txt_file_with_real_listings_back_to_back():
    tower = (DATA_DIR / "tower.txt").read_bytes()
    xrom = (DATA_DIR / "xrom.txt").read_bytes()
    programs = split_programs(decode_program_txt(tower + b"\n" + xrom))
    assert programs == [decode_program_txt(tower), decode_program_txt(xrom)]


def test_txt_file_whose_last_program_has_no_end_names_its_first_line():
    text = 'LBL "A"\nSIN\nEND\n\n; next\nLBL "B"\nCOS\n'
    with pytest.raises(DM41MemoryError, match="line 6: the program starting here"):
        decode_program_txt(text.encode())


def test_split_programs_drops_trailing_zero_padding():
    assert split_programs(APPTEST_BYTES + bytes(5)) == [APPTEST_BYTES]


def test_split_programs_rejects_bytes_after_the_last_end():
    with pytest.raises(DM41MemoryError, match="follow its last END"):
        split_programs(APPTEST_BYTES + bytes([0x41]))


def test_split_programs_rejects_data_with_no_end():
    with pytest.raises(DM41MemoryError, match="terminating END"):
        split_programs(APPTEST_BYTES[:-3])


# -- Importing ---------------------------------------------------------------


def test_import_programs_adds_them_newest_last_in_file_order():
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    programs = _three_programs()
    imported = memory.programs.import_programs(programs)

    assert [p.length for p in imported] == [len(p) for p in programs]
    listed = memory.programs.list_programs()
    # Lengths, not bytes: each program's outermost chain marker is relinked
    # to where it now sits.
    assert [p.length for p in listed] == [len(p) for p in programs]
    assert [p.names_label for p in listed][:2] == ["APPTEST", "DM41X"]


def test_import_programs_from_txt_then_repack_shows_every_label():
    # The Programs tab's own sequence: TXT programs carry unlinked chain
    # markers, which repack() links up.
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    text = b'LBL "A"\nSIN\nEND\nLBL "B"\nCOS\nEND\nLBL "C"\nTAN\nEND\n'
    with memory.transaction():
        memory.programs.import_programs(split_programs(decode_program_txt(text)))
        memory.programs.repack()
    assert [p.names_label for p in memory.programs.list_programs()] == [
        "A",
        "B",
        "C",
    ]


def test_import_programs_rejects_a_label_used_twice_in_the_file():
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    before = memory.snapshot()
    text = b'LBL "A"\nSIN\nEND\nLBL "A"\nCOS\nEND\n'
    with pytest.raises(ValueError, match="Two programs in this file"):
        memory.programs.import_programs(split_programs(decode_program_txt(text)))
    assert memory.snapshot() == before


def test_import_programs_is_all_or_nothing():
    # The second program's label already exists, so the first must not be
    # left behind either.
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    before = memory.snapshot()
    with pytest.raises(ValueError, match="already exists"):
        memory.programs.import_programs([_xrom_bytes(), APPTEST_BYTES])
    assert memory.snapshot() == before


# -- Exporting several programs to one file ---------------------------------


def _manyfiles_programs():
    """Every program in manyfiles.dm41 (XMBCD, XMALPHA, PURXM), as the
    Programs tab's Export reads them."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    return [
        memory.programs.get_program_bytes(p) for p in memory.programs.list_programs()
    ]


@pytest.mark.parametrize(
    "encode, decode",
    [
        (encode_program_raw, decode_program_raw),
        (encode_program_dat, decode_program_dat),
        (encode_program_ppc, decode_program_dat),
        (encode_program_txt, decode_program_txt),
    ],
    ids=["raw", "dat", "ppc", "txt"],
)
def test_exported_programs_import_back_under_their_own_names(encode, decode):
    programs = _manyfiles_programs()
    file_bytes = encode(b"".join(programs))

    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    with memory.transaction():
        memory.programs.import_programs(split_programs(decode(file_bytes)))
        memory.programs.repack()
    listed = memory.programs.list_programs()
    assert [p.names_label for p in listed] == ["XMBCD", "XMALPHA", "PURXM"]
    # manyfiles.dm41 isn't packed, so compare against it packed the same way.
    source = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    source.programs.repack()
    assert [p.length for p in listed] == [
        p.length for p in source.programs.list_programs()
    ]


def test_txt_export_of_several_programs_is_each_listing_in_turn():
    programs = _manyfiles_programs()
    listings = [encode_program_txt(p).decode() for p in programs]
    assert encode_program_txt(b"".join(programs)).decode() == "\n".join(listings)


def test_txt_export_ignores_a_raw_trailer_that_looks_like_an_end():
    # A checksum byte of 0xC0-0xCD followed by zero padding scans as an END.
    trailer = bytes([0xC5]) + bytes(255)
    assert encode_program_txt(APPTEST_BYTES + trailer) == encode_program_txt(
        APPTEST_BYTES
    )
