"""Tests for memory/mnemonic_doc.py (the FOCAL mnemonic reference) and
the committed docs/mnemonics.md it generates."""

from pathlib import Path

import pytest

from memory.mnemonic_doc import (
    DEFAULT_OUTPUT,
    mnemonic_reference_rows,
    render_mnemonic_reference,
    substitution_rows,
    trigraph_rows,
)
from memory.mnemonics import entries, resolve

DOCS_MNEMONICS = Path(__file__).resolve().parents[2] / "docs" / "mnemonics.md"


def _row(display):
    (row,) = [r for r in mnemonic_reference_rows() if r.display == display]
    return row


def test_committed_reference_is_up_to_date():
    # If this fails, regenerate: cd src && python -m memory.mnemonic_doc
    assert DEFAULT_OUTPUT == DOCS_MNEMONICS
    assert DOCS_MNEMONICS.read_text(encoding="utf-8") == render_mnemonic_reference()


def test_every_instruction_has_one_row():
    rows = mnemonic_reference_rows()
    assert len(rows) == len(entries())
    assert len({(r.display, r.encoding) for r in rows}) == len(rows)


def test_every_listed_spelling_resolves_to_its_row():
    for row in mnemonic_reference_rows():
        op = resolve(row.display, programmable_only=False)
        assert resolve(row.canonical, programmable_only=False) == op
        for spelling in row.other_spellings:
            assert resolve(spelling, programmable_only=False) == op, spelling


def test_sigma_reg_row():
    row = _row("ΣREG")
    assert row.canonical == "SREG"
    assert row.encoding == "0x99"
    assert row.other_spellings == ("SIGREG", "SIGMAREG", "\\EREG")
    assert "SIGMA" in row.search_text


def test_dialect_spellings_are_listed():
    assert _row("P-R").other_spellings == ("P->R",)
    assert _row("ST+").other_spellings == ("STO+",)
    assert _row("END").other_spellings == (".END.",)
    assert _row("GTO").other_spellings == ("GOTO",)
    assert _row("R↑").other_spellings == ("RUP", "R\\^|")


@pytest.mark.parametrize(
    "display, expected",
    [
        ("Σ+", ("SIG+", "SIGMA+", "\\E+")),
        ("CLΣ", ("CLSIG", "CLSIGMA", "CL\\E")),
        ("X≠Y?", ("X!=Y?", "X<>Y?", "X\\/=Y?")),
        ("X↑2", ("X**2", "X\\^|2")),
        ("ENTER↑", ("ENTER^", "ENTER\\^|")),
        ("10↑X", ("10**X", "10\\^|X")),
        ("ΣREG?", ("SIGREG?", "SIGMAREG?", "\\EREG?")),
    ],
)
def test_substitution_and_trigraph_spellings_are_listed(display, expected):
    assert _row(display).other_spellings == expected


def test_every_accepted_ascii_spelling_is_listed():
    for row, entry in zip(
        mnemonic_reference_rows(),
        sorted(entries(), key=lambda e: (e.canonical.upper(), e.canonical)),
    ):
        listed = {row.display, row.canonical, *row.other_spellings}
        for spelling, _ in entry.aliases:
            if spelling.isascii():
                assert spelling in listed, (row.display, spelling)


def test_notes_and_encodings():
    assert _row("CAT").notes == "keyboard only"
    assert _row("END").notes == "text keyword"
    assert _row("SEEKPT").encoding == "XROM 25,42"
    assert _row("TIME").encoding == "XROM 26,28"
    assert _row("SIN").notes == ""


def test_dm41x_additions_are_noted_and_originals_are_not():
    assert _row("LKAOFF").notes == "DM41X only"
    assert _row("LKAOFF").encoding == "XROM 26,48"
    assert _row("X<I>Y").notes == "DM41X only"
    assert _row("TRNG").notes == "DM41X only"
    assert _row("SWPT").notes == ""  # the last original Time function
    assert _row("ED").notes == ""  # ED$ is only another name for ED


def test_ed_dollar_is_listed_as_another_spelling_of_ed():
    assert "ED$" in _row("ED").other_spellings
    assert "DM41X manual" in render_mnemonic_reference()


def test_rows_sort_by_canonical_name():
    canonicals = [r.canonical.upper() for r in mnemonic_reference_rows()]
    assert canonicals == sorted(canonicals)


def test_substitution_rows():
    assert ("Σ", "S", "SIG, SIGMA") in substitution_rows()
    assert ("≠", "#", "!=, <>") in substitution_rows()
    assert (
        "↑",
        "^",
        "** (powers only: X↑2, Y↑X, E↑X, 10↑X, E↑X-1)",
    ) in substitution_rows()


def test_markdown_marks_double_star_as_powers_only():
    assert (
        "| ↑ | `^` | `**` (powers only: X↑2, Y↑X, E↑X, 10↑X, E↑X-1) |"
        in render_mnemonic_reference()
    )


def test_trigraph_rows():
    rows = trigraph_rows()
    assert ("\\E", "Σ", "0x7E") in rows
    assert ("\\^|", "↑", "0x5E") in rows
    assert len(rows) == 10


def test_markdown_escapes_pipes_in_table_cells():
    text = render_mnemonic_reference()
    assert "| `\\^\\|` | `↑` | 0x5E |" in text
    # Every table row has the same number of unescaped cell separators
    # as its header.
    for line in text.splitlines():
        if line.startswith("| `"):
            cells = line.replace("\\|", "").count("|")
            assert cells in (4, 6), line
