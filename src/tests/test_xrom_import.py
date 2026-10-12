"""Tests for memory/xrom_import.py: turning a .mod image, an hp41uc XROM
list or a library .json into a library file (GitHub issue #53), and the
user's library folder.

The repository holds no real .mod file (the pacs on the DM41X's disk are
HP's ROM images), so make_mod() builds a small MOD1 image here. The 20
shipped pac libraries were made from the real ones with the same code;
tools/make_xrom_library.py regenerates them byte for byte.
"""

import json

import pytest

from memory.xrom_import import (
    convert_file,
    library_from_hp41uc,
    library_from_mod,
    library_id_for,
    library_json,
    save_user_library,
    unique_id,
    user_library_paths,
)
from memory.xrom_library import XromLibraryError, load_libraries, parse_library

# -- A synthetic MOD1 image -------------------------------------------------

_HEADER_SIZE = 729
_PAGE_SIZE = 5188
_NUM_PAGES_OFFSET = 696


def _lcd(text: str) -> list:
    """LCD codes for an MCODE name: A-Z are 0x01-0x1A, the rest ASCII."""
    return [ord(c) - 0x40 if "A" <= c <= "Z" else ord(c) for c in text]


def _pack(words: list) -> bytes:
    """The inverse of the module's _unpack_image(): 4096 10-bit words into
    5120 bytes."""
    out = bytearray()
    for i in range(0, 4096, 4):
        w0, w1, w2, w3 = words[i : i + 4]
        out += bytes(
            [
                w0 & 0xFF,
                ((w0 >> 8) & 0x03) | ((w1 & 0x3F) << 2),
                ((w1 >> 6) & 0x0F) | ((w2 & 0x0F) << 4),
                ((w2 >> 4) & 0x3F) | ((w3 & 0x03) << 6),
                (w3 >> 2) & 0xFF,
            ]
        )
    return bytes(out)


def make_mod(title="Test Pac", xrom=11, unreadable=True) -> bytes:
    """One 4K page, XROM `xrom`: function 0 the MCODE header "TEST 1A",
    1 the MCODE function FOO, 2 the FOCAL program BAR, and (if
    `unreadable`) 3 an MCODE entry whose name uses a half-nut character
    the importer can't read."""
    words = [0] * 4096
    entries = []

    def mcode(address, name, bad=False):
        codes = _lcd(name)
        if bad:
            codes[0] = 0x41  # bit 6 set: a half-nut display character
        for k, code in enumerate(codes):
            word = code
            if k == len(codes) - 1:
                word |= 0x80  # the name's last character, lowest address
            words[address - 1 - k] = word
        entries.append((address >> 8, address & 0xFF))

    def focal(address, name):
        data = [0xC0, 0x00, 0xF0 | (len(name) + 1), 0x00] + [ord(c) for c in name]
        for k, byte in enumerate(data):
            words[address + k] = byte
        entries.append((0x200 | (address >> 8), address & 0xFF))

    mcode(0x100, "TEST 1A")
    mcode(0x200, "FOO")
    focal(0x300, "BAR")
    if unreadable:
        mcode(0x400, "ZAP", bad=True)
    words[0], words[1] = xrom, len(entries)
    for n, (w0, w1) in enumerate(entries):
        words[2 + 2 * n], words[3 + 2 * n] = w0, w1

    header = bytearray(_HEADER_SIZE)
    header[0:5] = b"MOD1\0"
    header[5 : 5 + len(title)] = title.encode()
    header[55:56] = b"B"
    header[65:72] = b"12345-6"
    header[85:89] = b"Mike"
    header[_NUM_PAGES_OFFSET] = 1
    page = bytearray(_PAGE_SIZE)
    page[0:6] = b"TEST1A"
    page[20:24] = b"TE1A"
    page[29] = 0x1F  # any position
    page[31] = 1  # bank 1
    page[35] = 1  # has a FAT
    page[36 : 36 + 5120] = _pack(words)
    return bytes(header + page)


# -- .mod -----------------------------------------------------------------


def test_a_mod_image_becomes_a_library():
    library, warnings = library_from_mod(make_mod(), "Test-Pac.mod")
    assert library["id"] == "test-pac"
    assert library["title"] == "Test Pac"
    assert (library["version"], library["part_number"], library["author"]) == (
        "B",
        "12345-6",
        "Mike",
    )
    assert library["source"] == "FAT of Test-Pac.mod"
    assert library["builtin_on"] == []
    (module,) = library["modules"]
    assert (module["xrom"], module["page_id"]) == (11, "TE1A")
    assert module["functions"] == [
        {"fn": 0, "name": "TEST 1A", "type": "mcode", "header": True},
        {"fn": 1, "name": "FOO", "type": "mcode"},
        {"fn": 2, "name": "BAR", "type": "focal"},
    ]
    assert warnings == [
        "XROM 11,03 has no readable name, so it was left out; it can still "
        "be assigned by number."
    ]
    parse_library(library)  # valid as it stands


def test_a_mod_without_a_title_is_named_after_its_file():
    library, _ = library_from_mod(make_mod(title=""), "Odd.mod")
    assert library["title"] == "Odd"


@pytest.mark.parametrize(
    "data, expected",
    [
        (b"MOD2" + bytes(800), "not a .mod (MOD1) module file"),
        (b"MOD1" + bytes(100), "cut short"),
        (make_mod()[:2000], "cut short (it says it has 1 pages)"),
    ],
)
def test_a_bad_mod_is_refused(data, expected):
    with pytest.raises(ValueError, match="^bad.mod: ") as caught:
        library_from_mod(data, "bad.mod")
    assert expected in str(caught.value)


# -- hp41uc lists ---------------------------------------------------------


def test_an_hp41uc_list_becomes_a_library():
    library = library_from_hp41uc("BAR 11 2\nFOO 11 1\n\nBAZ 12 0\n", "mine.txt")
    assert (library["id"], library["title"]) == ("mine", "mine")
    assert library["source"] == "hp41uc XROM list mine.txt"
    assert library["modules"] == [
        {"xrom": 11, "functions": [{"fn": 1, "name": "FOO"}, {"fn": 2, "name": "BAR"}]},
        {"xrom": 12, "functions": [{"fn": 0, "name": "BAZ"}]},
    ]
    assert library_from_hp41uc("FOO 11 1", "mine.txt", "My ROM")["title"] == "My ROM"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("FOO 11\n", "line 1: expected NAME mm ff"),
        ("FOO 11 1\nBAR x 2\n", "line 2: expected NAME mm ff"),
        ("FOO 32 1\n", "line 1: XROM 32,1 is out of range"),
        ("FOO 11 64\n", "line 1: XROM 11,64 is out of range"),
        ("\n\n", "no functions listed"),
    ],
)
def test_a_bad_hp41uc_list_is_refused(text, expected):
    with pytest.raises(ValueError) as caught:
        library_from_hp41uc(text, "mine.txt")
    assert str(caught.value).startswith("mine.txt")
    assert expected in str(caught.value)


# -- Any file -----------------------------------------------------------------


def test_convert_file_reads_all_three_kinds(tmp_path):
    mod = tmp_path / "Test.MOD"
    mod.write_bytes(make_mod(unreadable=False))
    txt = tmp_path / "list.txt"
    txt.write_text("FOO 11 1\n")
    library, _ = library_from_mod(make_mod(unreadable=False), "copy.mod")
    js = tmp_path / "copy.json"
    js.write_text(library_json(library), encoding="utf-8")

    assert convert_file(mod) == (library_from_mod(mod.read_bytes(), "Test.MOD")[0], [])
    assert convert_file(txt, "Listed")[0]["title"] == "Listed"
    assert convert_file(js) == (library, [])


@pytest.mark.parametrize(
    "name, content, expected",
    [
        ("rom.bin", b"x", "not a .mod, hp41uc .txt or XROM library .json file"),
        ("bad.json", b"{oops", "not valid JSON (line 1"),
        ("bad.txt", b"FOO\n", "expected NAME mm ff"),
        ("bad.json", b'{"format": "other"}', "not an XROM library"),
        ("bad.mod", b"MOD9", "not a .mod (MOD1) module file"),
    ],
)
def test_convert_file_refuses_what_it_cannot_use(tmp_path, name, content, expected):
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(XromLibraryError) as caught:
        convert_file(path)
    assert expected in str(caught.value)


def test_convert_file_names_a_missing_file(tmp_path):
    with pytest.raises(XromLibraryError, match="^gone.mod: cannot be read"):
        convert_file(tmp_path / "gone.mod")


# -- Ids and the user's folder ------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [("Real-Estate", "real-estate"), ("My  ROM (v2)", "my-rom-v2"), ("***", "library")],
)
def test_library_id_for(name, expected):
    assert library_id_for(name) == expected


def test_unique_id():
    assert unique_id("math", []) == "math"
    assert unique_id("math", ["math"]) == "math-2"
    assert unique_id("math", ["math", "math-2", "math-3"]) == "math-4"


def test_library_json_is_one_function_per_line_and_reads_back():
    library = library_from_hp41uc("FOO 11 1\nΣX 11 2\n", "mine.txt")
    text = library_json(library)
    assert json.loads(text) == library
    assert '        {"fn": 2, "name": "ΣX"}' in text.splitlines()


def test_a_saved_user_library_loads_as_a_user_library(tmp_path):
    folder = tmp_path / "xroms"
    assert user_library_paths(folder) == []
    library = library_from_hp41uc("FOO 11 1\n", "mine.txt")

    path = save_user_library(library, folder)

    assert path == folder / "mine.json"
    assert user_library_paths(folder) == [path]
    libraries, errors = load_libraries([], user_library_paths(folder))
    assert errors == []
    assert [(lib.id, lib.shipped) for lib in libraries] == [("mine", False)]
