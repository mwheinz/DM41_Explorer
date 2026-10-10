"""Tests for memory/mnemonics.py, the FOCAL instruction-name registry
(docs/mnemonic_dialects_plan.md), and for how program_text.py uses it.

The hp41uc tables below were transcribed from hp41uc's source at
commit ff23b21 (hp41ucg.h single20_8F/prefix90_9F, compile.h
alt_fcn1/alt_fcn2, compile.c) so the checks don't depend on that source
tree being present.
"""

import pytest

from memory.functions import SINGLE_BYTE_FUNCTIONS, XROM_FUNCTIONS
from memory.mnemonic_dialects import Dialect
from memory.mnemonics import (
    END,
    GTO,
    LBL,
    XEQ,
    XROM,
    MnemonicRegistryError,
    Registry,
    UnknownMnemonicError,
    canonical,
    display,
    entries,
    function_op,
    resolve,
    xrom_op,
)
from memory.program_text import decode_program_txt, encode_program_txt

# Every hp41uc decompile name that differs from functions.py's name.
HP41UC_CANONICAL_DIFFERENCES = {
    0x46: "X<=Y?",
    0x47: "S+",
    0x48: "S-",
    0x4E: "P-R",
    0x4F: "R-P",
    0x51: "X^2",
    0x53: "Y^X",
    0x55: "E^X",
    0x57: "10^X",
    0x58: "E^X-1",
    0x63: "X#0?",
    0x6A: "D-R",
    0x6B: "R-D",
    0x6C: "HMS",
    0x6D: "HR",
    0x6F: "OCT",
    0x70: "CLS",
    0x74: "R^",
    0x79: "X#Y?",
    0x7B: "X<=0?",
    0x83: "ENTER",
    0x99: "SREG",
}

# hp41uc's compiler alternates (compile.h alt_fcn1/alt_fcn2, minus the
# raw-CP437-Sigma spellings, which program_files.py's CP437 decoding
# covers) plus compile.c's keyword alternates.
HP41UC_ALTERNATES = {
    "SIGMA+": 0x47,
    "SIGMA-": 0x48,
    "P->R": 0x4E,
    "R->P": 0x4F,
    "X**2": 0x51,
    "Y**X": 0x53,
    "E**X": 0x55,
    "10**X": 0x57,
    "E**X-1": 0x58,
    "X!=0?": 0x63,
    "X<>0?": 0x63,
    "D->R": 0x6A,
    "R->D": 0x6B,
    "CLSIGMA": 0x70,
    "RUP": 0x74,
    "X!=Y?": 0x79,
    "X<>Y?": 0x79,
    "ENTER^": 0x83,
    "STO+": 0x92,
    "STO-": 0x93,
    "STO*": 0x94,
    "STO/": 0x95,
    "SIGREG": 0x99,
    "SIGMAREG": 0x99,
    "GOTO": 0xD0,
}


# -- Canonical and display names ----------------------------------------


def test_canonical_names_match_hp41uc():
    for byte, name in SINGLE_BYTE_FUNCTIONS.items():
        if byte < 0x40:
            continue
        want = HP41UC_CANONICAL_DIFFERENCES.get(byte, name)
        assert canonical(function_op(byte)) == want, hex(byte)


def test_xrom_canonical_names_match_hp41uc():
    # hp41uc's XROM table spells these two with ASCII stand-ins too.
    assert canonical(xrom_op(0xA6, 0x78)) == "SREG?"
    assert canonical(xrom_op(0xA6, 0x7A)) == "X#NN?"
    assert canonical(xrom_op(0xA6, 0x6A)) == "SEEKPT"


def test_every_canonical_name_is_ascii():
    for entry in entries():
        assert entry.canonical.isascii(), entry


@pytest.mark.parametrize(
    "byte, want",
    [
        (0x4E, "P-R"),
        (0x46, "X<=Y?"),
        (0x6C, "HMS"),
        (0x99, "ΣREG"),
        (0x47, "Σ+"),
        (0x83, "ENTER↑"),
        (0x79, "X≠Y?"),
        (0x65, "LN1+X"),
    ],
)
def test_display_names_are_hp41_native(byte, want):
    assert display(function_op(byte)) == want


def test_no_display_name_uses_later_model_glyphs():
    for entry in entries():
        assert not set(entry.display) & set("→≤≥√"), entry


# -- resolve() ------------------------------------------------------------


def test_every_canonical_and_display_name_resolves_to_itself():
    for entry in entries():
        kwargs = {"programmable_only": False}
        assert resolve(entry.canonical, **kwargs) == entry.op
        assert resolve(entry.display, **kwargs) == entry.op


@pytest.mark.parametrize("spelling, byte", sorted(HP41UC_ALTERNATES.items()))
def test_hp41uc_alternates_resolve(spelling, byte):
    assert resolve(spelling) == function_op(byte)


def test_keyword_alternates_resolve():
    assert resolve(".END.") == END
    assert resolve("END") == END
    assert resolve("XROM") == XROM
    assert resolve("GOTO") == GTO
    assert resolve("LBL") == LBL
    assert resolve("XEQ") == XEQ


@pytest.mark.parametrize("spelling", ["SREG", "ΣREG", "∑REG", "SIGMAREG", "SIGREG"])
def test_sigma_reg_spellings_are_equivalent(spelling):
    assert resolve(spelling) == function_op(0x99)


def test_resolve_is_case_insensitive():
    assert resolve("sigmareg") == function_op(0x99)
    assert resolve("p->r") == function_op(0x4E)
    assert resolve("enter^") == function_op(0x83)
    assert resolve("seekpt") == xrom_op(0xA6, 0x6A)
    assert resolve("σreg") == function_op(0x99)


@pytest.mark.parametrize("spelling", ["P→R", "X≤Y?", "→HMS", "FOO", ""])
def test_unknown_spellings_are_rejected(spelling):
    with pytest.raises(UnknownMnemonicError, match="unrecognized instruction"):
        resolve(spelling)


def test_keyboard_only_functions_are_not_programmable():
    with pytest.raises(UnknownMnemonicError, match="keyboard-only"):
        resolve("CAT")
    assert resolve("CAT", programmable_only=False) == function_op(0x00)


def test_unknown_mnemonic_error_is_a_value_error():
    # program_text.py's callers rely on catching ValueError.
    assert issubclass(UnknownMnemonicError, ValueError)


@pytest.mark.parametrize(
    "spelling, byte",
    [("X**2", 0x51), ("Y**X", 0x53), ("E**X", 0x55), ("10**X", 0x57), ("E**X-1", 0x58)],
)
def test_double_star_works_for_power_functions(spelling, byte):
    assert resolve(spelling) == function_op(byte)


@pytest.mark.parametrize("spelling", ["R**", "ENTER**"])
def test_double_star_is_rejected_outside_power_functions(spelling):
    # hp41uc accepts ** only where the arrow means "to the power of".
    with pytest.raises(UnknownMnemonicError, match="unrecognized instruction"):
        resolve(spelling)


# -- Registry safety rules (plan sec 3.5) -----------------------------------


def test_registry_rejects_a_spelling_that_means_two_instructions():
    clash = Dialect(name="bad", source="test", aliases={"RDN": ("SIN",)})
    with pytest.raises(MnemonicRegistryError, match="SIN"):
        Registry(dialects=(clash,))


def test_registry_rejects_a_dialect_naming_an_unknown_instruction():
    typo = Dialect(name="bad", source="test", aliases={"NOSUCH": ("X",)})
    with pytest.raises(MnemonicRegistryError, match="NOSUCH"):
        Registry(dialects=(typo,))


def test_dialect_spellings_get_character_substitutions():
    extra = Dialect(name="test", source="test", aliases={"SREG": ("ΣRG",)})
    registry = Registry(dialects=(extra,))
    assert registry.resolve("SIGMARG") == function_op(0x99)


# -- program_text.py through the registry -----------------------------------


def _compile_one(line: str) -> bytes:
    compiled = decode_program_txt(f'LBL "T"\n{line}\nEND\n')
    return compiled[5:-3]  # drop the 5-byte LBL "T" header and 3-byte END


def _decompile_one(instruction: bytes) -> str:
    text = encode_program_txt(b"\xc0\x00\xf2\x00T" + instruction + b"\xc0\x00\x0d")
    return text.splitlines()[1]


def test_every_single_byte_instruction_round_trips():
    for byte in range(0x40, 0x90):
        op = function_op(byte)
        if byte not in SINGLE_BYTE_FUNCTIONS:
            continue
        assert _compile_one(canonical(op)) == bytes([byte]), hex(byte)
        assert _compile_one(display(op)) == bytes([byte]), hex(byte)
        assert _decompile_one(bytes([byte])) == canonical(op), hex(byte)


@pytest.mark.parametrize(
    "byte", [0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0xA8, 0xCE]
)
def test_every_register_prefix_round_trips(byte):
    op = function_op(byte)
    for name in (canonical(op), display(op)):
        assert _compile_one(f"{name} 20") == bytes([byte, 20]), hex(byte)
    assert _decompile_one(bytes([byte, 20])) == f"{canonical(op)} 20"


def test_every_xrom_function_round_trips():
    for (byte1, byte2), name in XROM_FUNCTIONS.items():
        op = xrom_op(byte1, byte2)
        assert _compile_one(canonical(op)) == bytes([byte1, byte2]), name
        assert _compile_one(name) == bytes([byte1, byte2]), name
        assert _decompile_one(bytes([byte1, byte2])).endswith(f";{canonical(op)}")


@pytest.mark.parametrize("spelling, byte", sorted(HP41UC_ALTERNATES.items()))
def test_hp41uc_alternates_compile(spelling, byte):
    operand = " 01" if 0x90 <= byte <= 0x9F else ""
    if byte == 0xD0:
        assert _compile_one(f"{spelling} 20") == bytes([0xD0, 0x00, 20])
        return
    assert _compile_one(spelling + operand)[0] == byte


def test_dot_end_closes_the_program_like_end():
    assert decode_program_txt('LBL "T"\nSIN\n.END.\n') == decode_program_txt(
        'LBL "T"\nSIN\nEND\n'
    )


def test_lowercase_keywords_compile():
    assert decode_program_txt('lbl "T"\nsin\ngoto 01\nend\n') == decode_program_txt(
        'LBL "T"\nSIN\nGTO 01\nEND\n'
    )


def test_keyboard_only_function_in_program_text_is_an_error():
    with pytest.raises(ValueError, match="line 2: .*keyboard-only"):
        decode_program_txt('LBL "T"\nCAT\nEND\n')


def test_later_model_spellings_are_rejected_in_program_text():
    with pytest.raises(ValueError, match="unrecognized instruction"):
        decode_program_txt('LBL "T"\nP→R\nEND\n')


# -- Trigraph spellings (plan sec 3.3 step 1) --------------------------------


@pytest.mark.parametrize(
    "spelling, byte",
    [
        ("\\EREG", 0x99),
        ("\\126REG", 0x99),
        ("\\E+", 0x47),
        ("CL\\E", 0x70),
        ("X\\/=Y?", 0x79),
        ("X\\/=0?", 0x63),
        ("X\\^|2", 0x51),
        ("ENTER\\^|", 0x83),
        ("\\Ereg", 0x99),  # letters after the trigraph still fold case
    ],
)
def test_trigraph_spellings_resolve(spelling, byte):
    assert resolve(spelling) == function_op(byte)


def test_trigraph_xrom_spelling_resolves():
    assert resolve("\\EREG?") == xrom_op(0xA6, 0x78)


@pytest.mark.parametrize("spelling", ["SREG", "ΣREG", "∑REG", "SIGMAREG", "\\EREG"])
def test_all_sigma_reg_forms_compile_identically(spelling):
    assert _compile_one(f"{spelling} 05") == bytes([0x99, 0x05])


def test_trigraph_shorthand_is_case_sensitive():
    # \e isn't a trigraph (only \E is Sigma), so this must not resolve,
    # even though letters elsewhere in a token are case-insensitive.
    with pytest.raises(UnknownMnemonicError, match="Unrecognized trigraph"):
        resolve("\\eREG")


def test_bad_trigraph_in_program_text_names_the_line():
    with pytest.raises(ValueError, match="line 2: .*Unrecognized trigraph"):
        decode_program_txt('LBL "T"\n\\qREG 05\nEND\n')


# -- "Did you mean ...?" suggestions (decision 3) ---------------------------


@pytest.mark.parametrize(
    "typo, suggestion",
    [
        ("SIGMAREGG", "ΣREG (SREG)"),
        ("ENTR", "ENTER↑ (ENTER)"),
        ("P>R", "P-R"),
        ("LN1X", "LN1+X"),
        ("SEEKP", "SEEKPT"),
        ("RCLL", "RCL"),
    ],
)
def test_unknown_token_suggests_close_matches(typo, suggestion):
    with pytest.raises(UnknownMnemonicError, match="did you mean") as info:
        resolve(typo)
    assert suggestion in str(info.value)


def test_no_suggestion_when_nothing_is_close():
    with pytest.raises(UnknownMnemonicError) as info:
        resolve("ZZZZZ")
    assert "did you mean" not in str(info.value)


def test_suggestions_skip_keyboard_only_functions_in_programs():
    with pytest.raises(UnknownMnemonicError) as info:
        resolve("CATT")
    assert "did you mean" not in str(info.value)
    # ...but the Key Assignment side (programmable_only=False) gets it.
    with pytest.raises(UnknownMnemonicError, match="did you mean: CAT"):
        resolve("CATT", programmable_only=False)


def test_suggestions_are_capped_at_three():
    with pytest.raises(UnknownMnemonicError) as info:
        resolve("RCLL")
    listed = str(info.value).split("did you mean: ")[1].rstrip(")")
    assert len(listed.split(", ")) <= 3


def test_program_text_error_includes_suggestion():
    with pytest.raises(ValueError, match=r"line 2: .*did you mean: ΣREG \(SREG\)"):
        decode_program_txt('LBL "T"\nSIGMAREGG 05\nEND\n')
