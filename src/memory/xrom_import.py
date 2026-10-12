"""
Turning ROM descriptions into XROM library files (GitHub issues #44, #53).

Three kinds of file can become a library (memory/xrom_library.py):

- A `.mod` file (the MOD1 format the DM41X, V41 and i41CX load), read
  directly: each ROM page that carries a function address table (FAT)
  becomes one module of the library, and each FAT entry one function, with
  the name the calculator itself shows -- the MCODE name stored before the
  entry point, or the global label a FOCAL entry points to. Nothing in the
  image is kept except those names and numbers.
- An hp41uc XROM list (its /m=file option): one function per line,
  "NAME mm ff", whitespace-separated.
- A library file (`.json`) already, which is only checked.

convert_file() handles all three and returns a validated library as JSON
data; library_json() writes that out with one function per line, so a diff
of a regenerated file shows exactly which names changed. The application's
XROM manager imports user libraries through these, and
tools/make_xrom_library.py builds the shipped ones with them.
"""

import json
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .atomic_write import atomic_write_text
from .xrom_library import FORMAT, FORMAT_VERSION, XromLibraryError, parse_library

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

# The file types convert_file() reads, by suffix (lower case).
MOD_SUFFIX = ".mod"
HP41UC_SUFFIX = ".txt"
LIBRARY_SUFFIX = ".json"


def library_id_for(name: str) -> str:
    """A library id from a file name or title: lower case, with anything
    but letters and digits turned into single "-" ("Real-Estate.mod" ->
    "real-estate-mod" for the name, "real-estate" for the stem)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "library"


def unique_id(wanted: str, taken: Iterable[str]) -> str:
    """`wanted`, or `wanted-2`, `wanted-3`, ... -- the first not in
    `taken`."""
    taken = set(taken)
    if wanted not in taken:
        return wanted
    n = 2
    while f"{wanted}-{n}" in taken:
        n += 1
    return f"{wanted}-{n}"


# -- .mod files -------------------------------------------------------------


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
    name = entry["name"]
    if entry["type"] != "mcode" or " " not in name:
        return False
    return entry["fn"] == 0 or name.startswith("-")


def library_from_mod(data: bytes, file_name: str) -> Tuple[dict, List[str]]:
    """The library a `.mod` image describes, as JSON data, and a warning
    for each FAT entry whose name could not be read; such an entry is left
    out, as a library function must have a name. Raises ValueError if
    `data` is not a MOD1 image."""
    if data[:4] != b"MOD1":
        raise ValueError(f"{file_name}: not a .mod (MOD1) module file")
    if len(data) < _HEADER_SIZE:
        raise ValueError(f"{file_name}: the module file is cut short")
    page_count = data[_NUM_PAGES_OFFSET]
    if len(data) < _HEADER_SIZE + page_count * _PAGE_SIZE:
        raise ValueError(
            f"{file_name}: the module file is cut short (it says it has "
            f"{page_count} pages)"
        )
    pages = []
    for p in range(page_count):
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
    warnings = []
    for i, page in enumerate(pages):
        if i not in base or not page["has_fat"]:
            continue
        words = page["words"]
        xrom, count = words[0], min(words[1], 64)
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
            name = find(space, base[i] + offset)
            if name is None:
                warnings.append(
                    f"XROM {xrom:02d},{fn:02d} has no readable name, so it was "
                    "left out; it can still be assigned by number."
                )
                continue
            entry = {"fn": fn, "name": name, "type": "focal" if focal else "mcode"}
            if _looks_like_header(entry):
                entry["header"] = True
            functions.append(entry)
        if functions and 1 <= xrom <= 31:
            modules.append(
                {"xrom": xrom, "page_id": page["id"] or None, "functions": functions}
            )

    header = {
        "title": _c_string(data[5:55]),
        "version": _c_string(data[55:65]),
        "part_number": _c_string(data[65:85]),
        "author": _c_string(data[85:135]),
    }
    if not header["title"]:
        header["title"] = Path(file_name).stem
    library = _library(Path(file_name).stem, header, f"FAT of {file_name}", modules)
    return library, warnings


# -- hp41uc XROM lists ------------------------------------------------------


def library_from_hp41uc(text: str, file_name: str, title: Optional[str] = None) -> dict:
    """The library an hp41uc XROM list describes, as JSON data. `title`
    defaults to the file name without its suffix. Raises ValueError,
    naming the line, for a line that is not "NAME mm ff"."""
    by_xrom = {}
    for number, line in enumerate(text.splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3 or not (fields[1].isdigit() and fields[2].isdigit()):
            raise ValueError(f"{file_name}, line {number}: expected NAME mm ff")
        name, mm, ff = fields[0], int(fields[1]), int(fields[2])
        if not (1 <= mm <= 31 and 0 <= ff <= 63):
            raise ValueError(
                f"{file_name}, line {number}: XROM {mm},{ff} is out of range"
            )
        by_xrom.setdefault(mm, []).append({"fn": ff, "name": name})
    if not by_xrom:
        raise ValueError(f"{file_name}: no functions listed")
    modules = [
        {"xrom": mm, "functions": sorted(fns, key=lambda f: f["fn"])}
        for mm, fns in sorted(by_xrom.items())
    ]
    stem = Path(file_name).stem
    return _library(
        stem, {"title": title or stem}, f"hp41uc XROM list {file_name}", modules
    )


def _library(stem: str, header: dict, source: str, modules: list) -> dict:
    library = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "id": library_id_for(stem),
    }
    library.update({k: v for k, v in header.items() if v})
    library["source"] = source
    library["builtin_on"] = []
    library["modules"] = modules
    return library


# -- Any of the three -------------------------------------------------------


def convert_file(path, title: Optional[str] = None) -> Tuple[dict, List[str]]:
    """Reads `path` -- a `.mod`, an hp41uc `.txt` list or a library `.json`
    -- and returns it as validated library data, with any warnings.
    `title` names an hp41uc list's library. Raises XromLibraryError, with a
    message for the user naming the file, if it cannot be used."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == MOD_SUFFIX:
            data, warnings = library_from_mod(path.read_bytes(), path.name)
        elif suffix == HP41UC_SUFFIX:
            text = path.read_text(encoding="latin-1")
            data, warnings = library_from_hp41uc(text, path.name, title), []
        elif suffix == LIBRARY_SUFFIX:
            data = json.loads(path.read_text(encoding="utf-8"))
            warnings = []
        else:
            raise ValueError(
                f"{path.name}: not a .mod, hp41uc .txt or XROM library .json file"
            )
    except OSError as e:
        raise XromLibraryError(f"{path.name}: cannot be read ({e.strerror})") from e
    except json.JSONDecodeError as e:
        raise XromLibraryError(
            f"{path.name}: not valid JSON (line {e.lineno}: {e.msg})"
        ) from e
    except (ValueError, UnicodeDecodeError) as e:
        raise XromLibraryError(str(e)) from e
    # Validate exactly as the loader will; this raises XromLibraryError.
    parse_library(data, path.name)
    return data, warnings


def library_json(library: dict) -> str:
    """`library` (JSON data) as the text of a library file, one function
    per line."""
    lines = ["{"]
    for key, value in library.items():
        if key != "modules":
            lines.append(
                f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},"
            )
    lines.append('  "modules": [')
    modules = library["modules"]
    for m, module in enumerate(modules):
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
        lines.append("    }" + ("," if m < len(modules) - 1 else ""))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


# -- The user's library folder ----------------------------------------------


def user_library_paths(directory) -> List[Path]:
    """The library files in the user's folder (none if it doesn't exist)."""
    directory = Path(directory)
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def save_user_library(library: dict, directory) -> Path:
    """Writes `library` (JSON data) to <directory>/<id>.json, creating the
    folder if needed and replacing a file of that name in one step.
    Returns the file's path; raises OSError if it can't be written."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{library['id']}.json"
    atomic_write_text(path, library_json(library))
    return path
