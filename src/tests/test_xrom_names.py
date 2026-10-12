"""Names of XROM functions once plug-in libraries are active (GitHub issue
#52): memory/mnemonics.py's library layer, the two lookup orders, `XROM
"NAME"`, section headers, and the key-assignment picker's labels.

The rules follow tests on a real DM41X (issue #52's comments, #56 X7):
by name, the calculator searches the built-in Time ROM, then the plug-in
pages, then the Extended Functions ROM, then the mainframe functions. A
program listing keeps the mainframe and built-in names first, as hp41uc
does, so switching a pac on never changes what an imported listing means.
"""

import pytest

from memory import mnemonics
from memory.mnemonics import (
    OpKind,
    UnknownMnemonicError,
    assignable_choices,
    assignable_display_names,
    canonical,
    display,
    display_for_key_bytes,
    entries,
    function_op,
    is_available_on,
    is_known,
    library_of,
    parse_xrom_name_text,
    resolve,
    resolve_xrom_name,
    set_active_libraries,
    xrom_bytes,
    xrom_op,
)
from memory import DM41L, DM41X
from memory.program_text import decode_program_txt, encode_program_txt
from memory.xrom_library import active_set, load_libraries


@pytest.fixture(scope="module")
def shipped():
    libraries, errors = load_libraries()
    assert errors == []
    return libraries


@pytest.fixture
def activate(shipped):
    """activate("math", "advantage") makes those libraries active, in that
    priority order, for one test; the registry goes back to the built-in
    XROMs alone afterwards, so no other test sees them."""

    def _activate(*ids):
        set_active_libraries(active_set(shipped, "DM41X", ids).libraries)

    yield _activate
    set_active_libraries([])


def _xrom(module, function):
    return xrom_op(*xrom_bytes(module, function))


LOG = function_op(0x56)


# -- Without plug-in libraries -------------------------------------------------


def test_no_pac_name_is_known_until_its_library_is_active(activate):
    with pytest.raises(UnknownMnemonicError):
        resolve("SOLVE")
    activate("math")
    assert resolve("SOLVE") == _xrom(1, 9)
    set_active_libraries([])
    with pytest.raises(UnknownMnemonicError):
        resolve("SOLVE")


def test_resetting_restores_exactly_the_builtin_registry(activate):
    before = len(entries())
    activate("math", "advantage", "standard")
    assert len(entries()) > before
    set_active_libraries([])
    assert len(entries()) == before


# -- Priority between pacs -----------------------------------------------------


def test_with_math_active_solve_is_math_pacs(activate):
    activate("math")
    assert resolve("SOLVE") == _xrom(1, 9)
    assert resolve("SOLVE", prefer_modules=True) == _xrom(1, 9)
    assert library_of(resolve("SOLVE")) == "Math Pac"


@pytest.mark.parametrize(
    "order, expected",
    [(("advantage", "math"), (24, 3)), (("math", "advantage"), (1, 9))],
)
def test_the_higher_priority_pac_wins_solve(activate, order, expected):
    activate(*order)
    assert resolve("SOLVE") == _xrom(*expected)
    assert resolve("SOLVE", prefer_modules=True) == _xrom(*expected)
    assert resolve_xrom_name("SOLVE") == _xrom(*expected)


def test_the_hidden_solve_still_decodes_by_number(activate):
    activate("advantage", "math")
    assert display_for_key_bytes(*xrom_bytes(1, 9)) == "SOLVE"
    assert library_of(_xrom(1, 9)) == "Math Pac"


def test_a_shared_xrom_number_decodes_as_the_first_library(activate):
    """Aviation and Clinical Lab both use XROM 19. 19,01 decodes as
    Aviation's FM, and Clinical's BEERS (also 19,01) is left out: its name
    would compile to bytes that decode as FM."""
    activate("aviation", "clinical")
    assert display_for_key_bytes(*xrom_bytes(19, 1)) == "FM"
    with pytest.raises(UnknownMnemonicError):
        resolve("BEERS")


# -- Built-in and mainframe names ----------------------------------------------


def test_size_is_the_builtin_in_a_listing_and_the_pacs_by_name(activate):
    """ASN "SIZE?" on a real DM41X with Real Estate plugged in took Real
    Estate's (issue #52); a listing's SIZE? stays the built-in one."""
    activate("real-estate")
    assert resolve("SIZE?") == _xrom(25, 44)
    assert resolve("SIZE?", prefer_modules=True) == _xrom(11, 44)
    assert resolve_xrom_name("SIZE?") == _xrom(11, 44)


def test_size_is_the_builtin_whatever_is_active_in_a_listing(activate):
    for ids in [(), ("games",), ("real-estate", "stress", "structural")]:
        activate(*ids)
        assert resolve("SIZE?") == _xrom(25, 44), ids


def test_log_is_the_mainframes_in_a_listing_and_the_pacs_by_name(activate):
    """ASN "LOG" on a real DM41X with the Standard Applications Pac
    plugged in stored the pac's XROM (issue #52)."""
    activate("standard")
    assert resolve("LOG") == LOG
    assert resolve("LOG", prefer_modules=True) == _xrom(5, 45)
    assert resolve_xrom_name("LOG") == _xrom(5, 45)
    assert resolve("+", prefer_modules=True) == _xrom(5, 34)


def test_by_name_without_the_pac_log_is_the_mainframes(activate):
    assert resolve("LOG", prefer_modules=True) == LOG
    with pytest.raises(UnknownMnemonicError, match="XROM mm,ff"):
        resolve_xrom_name("LOG")


def test_the_time_rom_beats_a_pac_by_name(activate, shipped):
    """The Time ROM (XROM 26, page 5) is searched before the plug-in
    pages, so a pac's TIME never wins."""
    from memory.xrom_library import parse_library

    clash = parse_library(
        {
            "format": "dm41-xrom-library",
            "format_version": 1,
            "id": "clash",
            "title": "Clash",
            "modules": [{"xrom": 11, "functions": [{"fn": 1, "name": "TIME"}]}],
        }
    )
    set_active_libraries([clash])
    builtin_time = _xrom(26, 28)
    assert resolve("TIME", prefer_modules=True) == builtin_time
    assert resolve_xrom_name("TIME") == builtin_time
    assert display_for_key_bytes(*xrom_bytes(11, 1)) == "TIME"


def test_xrom_name_finds_builtin_xroms_but_never_mainframe_functions():
    assert resolve_xrom_name("SEEKPT") == _xrom(25, 42)
    with pytest.raises(UnknownMnemonicError):
        resolve_xrom_name("SIN")


@pytest.mark.parametrize(
    "text, expected",
    [
        ('XROM "LOG"', "LOG"),
        ('xrom "P\\<)V"', "P\\<)V"),
        ("XROM 05,45", None),
        ('XROM ""', None),
        ("LOG", None),
    ],
)
def test_parse_xrom_name_text(text, expected):
    assert parse_xrom_name_text(text) == expected


# -- ∡ names -------------------------------------------------------------------


def test_an_angle_name_gets_a_trigraph_canonical_and_round_trips(activate):
    activate("circuit")
    op = resolve("P∡V")
    assert op == _xrom(6, 42)
    assert display(op) == "P∡V"
    assert canonical(op) == "P\\<)V"
    assert canonical(op).isascii()
    assert resolve(canonical(op)) == op

    data = decode_program_txt('LBL "T"\nP\\<)V\nEND\n')
    assert bytes(op.code) in data
    listing = encode_program_txt(data)
    assert "XROM 06,42 ;P\\<)V" in listing
    assert decode_program_txt(listing) == data


# -- Section headers -----------------------------------------------------------


def test_builtin_headers_display_but_never_resolve():
    assert display_for_key_bytes(*xrom_bytes(25, 0)) == "-EXT FCN 2X"
    assert display_for_key_bytes(*xrom_bytes(26, 37)) == "-DM 41X-"
    assert not is_known(_xrom(25, 0))
    with pytest.raises(UnknownMnemonicError):
        resolve("-EXT FCN 2X")


def test_a_pacs_header_displays_once_its_library_is_active(activate):
    assert display_for_key_bytes(*xrom_bytes(1, 0)) == "XROM 01,00"
    activate("math")
    assert display_for_key_bytes(*xrom_bytes(1, 0)) == "MATH 1C"
    with pytest.raises(UnknownMnemonicError):
        resolve("MATH 1C")


# -- Text import ---------------------------------------------------------------


def test_text_import_keeps_bare_names_and_takes_xrom_names(activate):
    activate("standard")
    data = decode_program_txt('LBL "T"\nLOG\nXROM "LOG"\nXROM "SEEKPT"\nEND\n')
    assert bytes([0x56]) + bytes(xrom_bytes(5, 45)) + bytes(xrom_bytes(25, 42)) in data


def test_text_import_of_an_unknown_xrom_name_says_what_to_write(activate):
    with pytest.raises(ValueError, match="XROM mm,ff"):
        decode_program_txt('LBL "T"\nXROM "LOG"\nEND\n')


def test_a_bare_pac_name_imports_once_its_library_is_active(activate):
    with pytest.raises(ValueError):
        decode_program_txt('LBL "T"\nSOLVE\nEND\n')
    activate("math")
    assert bytes(xrom_bytes(1, 9)) in decode_program_txt('LBL "T"\nSOLVE\nEND\n')


# -- Availability and the picker ---------------------------------------------------


def test_a_dm41x_can_have_any_xrom_and_a_dm41l_only_its_own(activate):
    activate("math")
    solve = resolve("SOLVE")
    assert is_available_on(solve, DM41X)
    assert not is_available_on(solve, DM41L)
    assert is_available_on(_xrom(1, 63), DM41X), "any module may be plugged in"
    assert "SOLVE" in assignable_display_names(DM41X)
    assert "SOLVE" not in assignable_display_names(DM41L)


def test_picker_labels_are_plain_names_when_nothing_clashes(activate):
    activate("math")
    labels = dict(assignable_choices())
    assert labels["SOLVE"] == _xrom(1, 9)
    assert labels["LOG"] == LOG
    assert not any("  (" in label for label in labels)


def test_picker_labels_tell_clashing_names_apart(activate):
    activate("math", "advantage", "standard", "real-estate")
    labels = dict(assignable_choices())
    assert labels["SOLVE  (Math Pac, XROM 01,09)"] == _xrom(1, 9)
    assert labels["SOLVE  (Advantage Pac, XROM 24,03)"] == _xrom(24, 3)
    assert labels["LOG  (HP-41 function)"] == LOG
    assert labels["LOG  (Standard Applications Pac, XROM 05,45)"] == _xrom(5, 45)
    assert labels["SIZE?  (built in, XROM 25,44)"] == _xrom(25, 44)
    assert labels["SIZE?  (Real Estate Pac, XROM 11,44)"] == _xrom(11, 44)
    assert "SOLVE" not in labels and "LOG" not in labels


def test_every_library_entry_is_an_xrom_flagged_dm41x_only(activate):
    activate("math", "standard")
    library = [e for e in entries() if e.library is not None]
    assert library
    assert all(e.op.kind is OpKind.XROM and e.dm41x_only for e in library)
    assert {e.library for e in library} == {
        "Math Pac",
        "Standard Applications Pac",
    }


def test_the_module_level_registry_is_rebuilt_not_mutated(activate):
    before = mnemonics._REGISTRY
    activate("math")
    assert mnemonics._REGISTRY is not before
