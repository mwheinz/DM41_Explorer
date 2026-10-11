"""Checks on the shipped XROM library files, src/memory/xroms/*.json
(GitHub issue #44).

Nothing reads these files yet. Until the loader lands, these tests keep the
data honest: every file matches the format, and the two built-in libraries
say exactly what memory/functions.py's tables say, so switching the app over
to the files cannot change a single name.
"""

import json
from pathlib import Path

import pytest

from memory.functions import CX_XROM_FUNCTIONS, XROM_FUNCTIONS

XROM_DIR = Path(__file__).resolve().parent.parent / "memory" / "xroms"
LIBRARY_FILES = sorted(XROM_DIR.glob("*.json"))
KNOWN_MODELS = {"DM41L", "DM41X"}


def _load(name):
    return json.loads((XROM_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _pairs(library):
    """(byte1, byte2) -> name, the encoding functions.py uses."""
    return {
        (0xA0 + module["xrom"] // 4, (module["xrom"] % 4) << 6 | f["fn"]): f["name"]
        for module in library["modules"]
        for f in module["functions"]
    }


def test_there_are_library_files():
    assert len(LIBRARY_FILES) >= 23


@pytest.mark.parametrize("path", LIBRARY_FILES, ids=lambda p: p.stem)
def test_library_file_is_well_formed(path):
    library = json.loads(path.read_text(encoding="utf-8"))
    assert library["format"] == "dm41-xrom-library"
    assert library["format_version"] == 1
    assert library["id"] == path.stem
    assert library["title"]
    assert set(library["builtin_on"]) <= KNOWN_MODELS
    numbers = [module["xrom"] for module in library["modules"]]
    assert len(numbers) == len(set(numbers)), "an XROM number appears twice"
    for module in library["modules"]:
        assert 1 <= module["xrom"] <= 31
        fns = [f["fn"] for f in module["functions"]]
        assert fns == sorted(set(fns)), "function numbers not unique and ascending"
        for f in module["functions"]:
            assert 0 <= f["fn"] <= 63
            assert f["name"] and f["name"] == f["name"].strip()
            assert f.get("type", "mcode") in ("mcode", "focal")


def test_each_model_has_exactly_one_builtin_library():
    builtin = {}
    for path in LIBRARY_FILES:
        library = json.loads(path.read_text(encoding="utf-8"))
        for model in library["builtin_on"]:
            assert model not in builtin, f"{model} has two built-in libraries"
            builtin[model] = library["id"]
    assert builtin == {"DM41L": "hp41cx", "DM41X": "dm41x-cx"}


def test_builtin_libraries_match_functions_tables():
    assert _pairs(_load("hp41cx")) == CX_XROM_FUNCTIONS
    assert _pairs(_load("dm41x-cx")) == XROM_FUNCTIONS
