#!/usr/bin/env python3
"""Builds DM41_Explorer XROM library files (GitHub issue #44)
from a module image or from an hp41uc XROM list.

Run from the repository root:

    python3 tools/make_xrom_library.py MODULE.mod [MODULE.mod ...] -o OUT_DIR
    python3 tools/make_xrom_library.py LIST.txt --title "My ROM" -o OUT_DIR

A `.mod` file (the MOD1 format the DM41X, V41 and i41CX load) is read
directly: each ROM page that carries a function address table (FAT)
becomes one module of the library, and each FAT entry becomes one function
with the name the calculator itself would show -- read from the MCODE
name stored before the entry point, or from the global label a FOCAL
entry points to. Nothing in the image is copied except those names.

A `.txt` file is an hp41uc XROM list (its /m=file option): one function
per line, "NAME mm ff", whitespace-separated.

Each input becomes OUT_DIR/<id>.json, where <id> is the input's file name
in lower case, with anything but letters and digits turned into "-".
"""

import argparse
import json
import re
import sys
from pathlib import Path

FORMAT = "dm41-xrom-library"
FORMAT_VERSION = 1

# -- MOD1 layout (the V41 ModuleFile.h structures) --------------------------
#
# A 729-byte file header, then NumPages pages of 5188 bytes. In a page:
# Name[20], ID[9], Page, PageGroup, Bank, BankGroup, RAM, WriteProtect, FAT,
# Image[5120] (4096 10-bit words packed 4 to 5 bytes), PageCustom[32].
_HEADER_SIZE = 729
_PAGE_SIZE = 5188
_NUM_PAGES_OFFSET = 5 + 50 + 10 + 20 + 50 + 100 + 200 + 255 + 6
_IMAGE_OFFSET = 36
_IMAGE_SIZE = 5120
# Page position codes. A module's pages are laid out as one address space
# so a FAT entry can point into the other page of an 8K module: "upper"
# sits 0x1000 words above "lower" (or "any").
_POSITION_UPPER = 0x3F

# FOCAL byte -> the character the calculator displays, for the bytes that
# are not plain ASCII (as memory/trigraphs.py's _UNICODE_BY_BYTE).
_FOCAL_GLYPHS = {
    0x00: "‾",
    0x01: "×",
    0x0C: "µ",
    0x0D: "∡",
    0x1D: "≠",
    0x5E: "↑",
    0x60: "⊤",
    0x7E: "Σ",
    0x7F: "⊦",
}


def _c_string(raw: bytes) -> str:
    return raw.split(b"\0")[0].decode("latin-1").strip()


def _unpack_image(image: bytes) -> list:
    """The 4096 10-bit ROM words of a packed (.BIN-format) page image."""
    words = []
    for j in range(0, _IMAGE_SIZE, 5):
        b = image[j : j + 5]
        words += [
            ((b[1] & 0x03) << 8) | b[0],
            ((b[2] & 0x0F) << 6) | (b[1] >> 2),
            ((b[3] & 0x3F) << 4) | (b[2] >> 4),
            (b[4] << 2) | (b[3] >> 6),
        ]
    return words


def _focal_char(byte: int):
    if byte in _FOCAL_GLYPHS:
        return _FOCAL_GLYPHS[byte]
    if 0x20 <= byte < 0x7F:
        return chr(byte)
    return None


def _lcd_char(code: int):
    """An MCODE name character: a 6-bit LCD code, where 0x00-0x1F are
    @A-Z[\\]↑_ (FOCAL 0x40-0x5F) and 0x20-0x3F are ASCII. Codes with bit 6
    set are the half-nut display's extra characters, which no stock module
    name uses; they give None, so the name is reported as unreadable."""
    if code & 0x40:
        return None
    return _focal_char(code + 0x40 if code < 0x20 else code)


def _mcode_name(space: dict, address: int):
    """The name stored, last character first, in the words just below an
    MCODE entry point; the first character (lowest address) has bit 7 set."""
    name = ""
    for k in range(1, 16):
        word = space.get(address - k)
        if word is None:
            return None
        char = _lcd_char(word & 0x7F)
        if char is None:
            return None
        name += char
        if word & 0x80:
            return name.strip() or None
    return None


def _focal_name(space: dict, address: int):
    """The name of the global label a FOCAL FAT entry points to: bytes
    Cx xx Fn 00 followed by n-1 name characters."""
    data = [space.get(address + i, 0) & 0xFF for i in range(20)]
    if data[0] & 0xF0 != 0xC0 or data[2] & 0xF0 != 0xF0:
        return None
    length = (data[2] & 0x0F) - 1
    chars = [_focal_char(b) for b in data[4 : 4 + length]]
    if length <= 0 or None in chars:
        return None
    return "".join(chars)


def _looks_like_header(entry: dict) -> bool:
    """A CAT 2 section header ("MATH 1C", "-ADV MTRX"): an MCODE name with
    a space in it that is either function 0 or starts with "-". Function 0
    is not always a header -- the PPC ROM's XROM 20 starts with a real
    function -- and "-SI" (Thermal Pac) is a function, not a header."""
    name = entry["name"] or ""
    if entry["type"] != "mcode" or " " not in name:
        return False
    return entry["fn"] == 0 or name.startswith("-")


def library_from_mod(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] != b"MOD1":
        raise ValueError(f"{path}: not a MOD1 module file")
    pages = []
    for p in range(data[_NUM_PAGES_OFFSET]):
        page = data[_HEADER_SIZE + p * _PAGE_SIZE : _HEADER_SIZE + (p + 1) * _PAGE_SIZE]
        pages.append(
            {
                "id": _c_string(page[20:29]),
                "position": page[29],
                "bank": page[31],
                "has_fat": bool(page[35]),
                "words": _unpack_image(
                    page[_IMAGE_OFFSET : _IMAGE_OFFSET + _IMAGE_SIZE]
                ),
            }
        )

    # Only bank 1 is visible when the FAT is read; bank-switched pages hold
    # code reached from it, never another FAT.
    space, base = {}, {}
    for i, page in enumerate(pages):
        if page["bank"] != 1:
            continue
        base[i] = 0x1000 if page["position"] == _POSITION_UPPER else 0
        for k, word in enumerate(page["words"]):
            space[base[i] + k] = word

    modules = []
    for i, page in enumerate(pages):
        if i not in base or not page["has_fat"]:
            continue
        words = page["words"]
        xrom, count = words[0], words[1]
        functions = []
        for fn in range(count):
            w0, w1 = words[2 + 2 * fn], words[3 + 2 * fn]
            if w0 == 0 and w1 == 0:
                break  # the FAT's end marker
            # A 16-bit offset from this page's start; it can be negative,
            # an upper page reaching into its lower partner.
            offset = ((w0 & 0xFF) << 8) | (w1 & 0xFF)
            if offset & 0x8000:
                offset -= 0x10000
            focal = bool(w0 & 0x200)
            find = _focal_name if focal else _mcode_name
            entry = {"fn": fn, "name": find(space, base[i] + offset)}
            entry["type"] = "focal" if focal else "mcode"
            if entry["name"] is None:
                print(
                    f"{path.name}: XROM {xrom},{fn:02d} has no readable name",
                    file=sys.stderr,
                )
            functions.append(entry)
        if not functions:
            continue
        for entry in functions:
            if _looks_like_header(entry):
                entry["header"] = True
        modules.append(
            {"xrom": xrom, "page_id": page["id"] or None, "functions": functions}
        )

    header = {
        "title": _c_string(data[5:55]),
        "version": _c_string(data[55:65]),
        "part_number": _c_string(data[65:85]),
        "author": _c_string(data[85:135]),
    }
    return _library(path, header, f"FAT of {path.name}", modules)


def library_from_hp41uc(path: Path, title: str) -> dict:
    by_xrom = {}
    for number, line in enumerate(path.read_text(encoding="latin-1").splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3:
            raise ValueError(f"{path}:{number}: expected NAME mm ff, got {line!r}")
        name, mm, ff = fields[0], int(fields[1]), int(fields[2])
        if not (1 <= mm <= 31 and 0 <= ff <= 63):
            raise ValueError(f"{path}:{number}: XROM {mm},{ff} is out of range")
        by_xrom.setdefault(mm, []).append({"fn": ff, "name": name})
    modules = [
        {"xrom": mm, "functions": sorted(fns, key=lambda f: f["fn"])}
        for mm, fns in sorted(by_xrom.items())
    ]
    return _library(
        path, {"title": title or path.stem}, f"hp41uc XROM list {path.name}", modules
    )


def _library(path: Path, header: dict, source: str, modules: list) -> dict:
    library = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "id": re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-"),
    }
    library.update({k: v for k, v in header.items() if v})
    library["source"] = source
    library["builtin_on"] = []
    library["modules"] = modules
    return library


def write_library(library: dict, out_dir: Path) -> Path:
    """Writes `library` with one function per line, so a diff of a
    regenerated file shows exactly which names changed."""
    lines = ["{"]
    items = [(k, v) for k, v in library.items() if k != "modules"]
    for key, value in items:
        lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append('  "modules": [')
    for m, module in enumerate(library["modules"]):
        lines.append("    {")
        for key, value in module.items():
            if key != "functions":
                lines.append(f"      {json.dumps(key)}: {json.dumps(value)},")
        lines.append('      "functions": [')
        functions = module["functions"]
        for f, function in enumerate(functions):
            comma = "," if f < len(functions) - 1 else ""
            lines.append(f"        {json.dumps(function, ensure_ascii=False)}{comma}")
        lines.append("      ]")
        lines.append("    }" + ("," if m < len(library["modules"]) - 1 else ""))
    lines.append("  ]")
    lines.append("}")
    out = out_dir / f"{library['id']}.json"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "inputs", nargs="+", type=Path, help=".mod or hp41uc .txt files"
    )
    parser.add_argument("-o", "--out-dir", type=Path, required=True)
    parser.add_argument("--title", help="library title, for an hp41uc .txt input")
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for path in args.inputs:
        if path.suffix.lower() == ".mod":
            library = library_from_mod(path)
        else:
            library = library_from_hp41uc(path, args.title)
        print(write_library(library, args.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
