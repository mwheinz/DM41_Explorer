"""Tests for memory/functions.py's instruction tables and for typing a
function name in the Key Assignment dialog (GitHub issue #17).

functions.py's names are the HP-41's own display names
(docs/mnemonic_dialects_plan.md sec 3.2). Typed names go through
memory/mnemonics.py's resolve(programmable_only=False) -- the same
spellings program-text import accepts, plus keyboard-only functions --
then key_bytes_for() gives the Key Assignment Register encoding.
"""

import pytest

from memory.functions import SINGLE_BYTE_FUNCTIONS, XROM_FUNCTIONS
from memory.mnemonics import (
    assignable_display_names,
    display_for_key_bytes,
    key_bytes_for,
    resolve,
)


def _key_bytes(typed):
    return key_bytes_for(resolve(typed, programmable_only=False))


# -- The tables themselves ------------------------------------------------


def test_names_use_only_hp41_display_glyphs():
    for name in list(SINGLE_BYTE_FUNCTIONS.values()) + list(XROM_FUNCTIONS.values()):
        assert not set(name) & set("→≤≥√"), name


def test_names_are_unique_across_both_tables():
    names = list(SINGLE_BYTE_FUNCTIONS.values()) + list(XROM_FUNCTIONS.values())
    assert len(names) == len(set(names))


def test_assignable_names_cover_both_tables_and_nothing_else():
    names = set(SINGLE_BYTE_FUNCTIONS.values()) | set(XROM_FUNCTIONS.values())
    assert assignable_display_names() == sorted(names)


# -- display_for_key_bytes() ----------------------------------------------


@pytest.mark.parametrize(
    "fn1, fn2, expected",
    [
        (0x4E, None, "P-R"),
        (0x6C, None, "HMS"),
        (0x99, None, "ΣREG"),
        (0x00, None, "CAT"),
        (0xA6, 0x78, "ΣREG?"),
        (0x05, None, "0x05"),
        (0xA6, 0x3F, "0xA6 0x3F"),
    ],
)
def test_display_for_key_bytes(fn1, fn2, expected):
    assert display_for_key_bytes(fn1, fn2) == expected


# -- Typed input ------------------------------------------------------------


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("cos", 0x5A),
        ("Sin", 0x59),
        ("rtn", 0x85),
        ("xeq", 0xE0),
        ("x^2", 0x51),
        ("y^x", 0x53),
        ("r^", 0x74),
        ("enter^", 0x83),
        ("e^x-1", 0x58),
        ("p->r", 0x4E),
        ("r->p", 0x4F),
        ("d->r", 0x6A),
        ("p-r", 0x4E),
        ("hms", 0x6C),
        ("oct", 0x6F),
        ("sigma+", 0x47),
        ("SIGMA-", 0x48),
        ("clsigma", 0x70),
        ("sigmareg", 0x99),
        ("\\EREG", 0x99),
        ("σreg", 0x99),  # lowercase Greek sigma
        ("x<=y?", 0x46),
        ("x<=0?", 0x7B),
        ("x#y?", 0x79),
    ],
)
def test_typed_names_resolve(typed, expected):
    assert _key_bytes(typed) == expected


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("x<=nn?", (0xA6, 0x7C)),
        ("X<=NN?", (0xA6, 0x7C)),
        ("x>=nn?", (0xA6, 0x7E)),
        ("sigmareg?", (0xA6, 0x78)),
        ("seekpt", (0xA6, 0x6A)),
    ],
)
def test_typed_xrom_names_resolve(typed, expected):
    assert _key_bytes(typed) == expected


def test_keyboard_only_functions_are_assignable():
    assert _key_bytes("cat") == 0x00
    assert _key_bytes("SST") == 0x08


def test_text_format_keywords_are_not_assignable():
    with pytest.raises(ValueError, match="can't be assigned"):
        _key_bytes("END")


def test_every_known_name_is_reachable_case_insensitively():
    for byte, name in SINGLE_BYTE_FUNCTIONS.items():
        assert _key_bytes(name.lower()) == byte, name
    for pair, name in XROM_FUNCTIONS.items():
        assert _key_bytes(name.lower()) == pair, name
