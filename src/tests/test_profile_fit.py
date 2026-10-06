"""Tests for memory/profile_fit.py: check_profile_fit(memory, profile), the
check before a memory state is sent to a DM41L (phase 2 of
docs/dm41x_explorer_plan.md).

The fixtures that matter:
  - dm41x_manyfiles.dm41: 59 XM files across all three regions (error).
  - xrom.d41: one program calling ED$, X<I>Y, TRNG and the 16 DM41X-module
    functions (18 warnings: ED$ is the same code as ED, which a DM41L has).
  - dm41x_xrom_keys.d41: LKAON, LKAOFF and ED$ on keys (2 warnings).
  - every DM41L capture (the .dm41 files that are not dm41x_*): no findings.
"""

from pathlib import Path

import pytest

from memory import (
    DM41L,
    DM41X,
    ERROR,
    WARNING,
    DM41MemoryError,
    Memory,
    ProfileFinding,
    Register,
    check_profile_fit,
    decode_program_txt,
    format_findings,
)
from memory.functions import DM41X_XROM_FUNCTIONS
from memory.mnemonics import key_bytes_for, resolve

DATA_DIR = Path(__file__).parent / "data"

# Files in tests/data that were not captured on a DM41L.
NOT_DM41L = {"dm41xn.dm41", "dm41x_manyfiles.dm41"}


def _load(name, profile=DM41X):
    return Memory.from_file(DATA_DIR / name, profile=profile)


def _dm41l_captures():
    return sorted(
        path.name
        for path in DATA_DIR.glob("*.dm41")
        if path.name not in NOT_DM41L and not path.name.startswith("dm41x_")
    )


def _levels(findings):
    return [finding.level for finding in findings]


# -- A DM41L's own states fit a DM41L -------------------------------------


def test_there_are_dm41l_captures_to_check():
    assert len(_dm41l_captures()) > 30


@pytest.mark.parametrize("name", _dm41l_captures())
@pytest.mark.parametrize("opened_as", [DM41L, DM41X], ids=["as_dm41l", "as_dm41x"])
def test_dm41l_capture_has_no_findings(name, opened_as):
    assert check_profile_fit(_load(name, opened_as), DM41L) == []


@pytest.mark.parametrize(
    "name",
    ["dm41x_manyfiles.dm41", "xrom.d41", "dm41x_xrom_keys.d41", "dm41xn.dm41"],
)
def test_dm41x_states_fit_a_dm41x(name):
    assert check_profile_fit(_load(name), DM41X) == []


# -- Errors: XM the model does not have -----------------------------------


def test_manyfiles_is_refused_with_one_clear_error():
    findings = check_profile_fit(_load("dm41x_manyfiles.dm41"), DM41L)

    (finding,) = findings
    assert finding.level == ERROR
    assert finding.location == "Extended memory"
    # 23 of the 59 files have data above 0x2EF; XMA16 is the first, with
    # its last five registers at the top of the third region.
    assert "23 of 59 XM files" in finding.message
    assert "'XMA16.'" in finding.message
    assert "0x3EB-0x3EF" in finding.message
    assert "ends at 0x2EF" in finding.message


def test_the_error_does_not_depend_on_how_the_state_was_opened():
    """Opened with the DM41L profile the directory cannot even be walked
    (list_files raises), so the check reads it again under the DM41X's."""
    memory = _load("dm41x_manyfiles.dm41", DM41L)
    with pytest.raises(DM41MemoryError):
        memory.extended_memory.list_files()

    findings = check_profile_fit(memory, DM41L)

    assert _levels(findings) == [ERROR]
    assert "23 of 59 XM files" in findings[0].message


def test_checking_does_not_change_the_memory():
    memory = _load("dm41x_manyfiles.dm41", DM41L)
    before = memory.to_string()

    check_profile_fit(memory, DM41L)

    assert memory.to_string() == before
    assert memory.profile is DM41L


def test_a_file_that_only_pushes_the_directory_end_out_is_an_error():
    """A data file that exactly fills the DM41L's two regions has no data
    above 0x2EF, but the register that ends the directory is at 0x3EF."""
    memory = _load("dm41x_base.d41")
    xm = memory.extended_memory
    big = xm.add_file("BIG", xm.TYPE_DATA, numbers=[1.0] * 363)
    assert [s for s in big.segments if s[0] <= s[1] and s[1] > 0x2EF] == []
    assert big.segments[-1] == [0x3F0, 0x3EF]  # the empty placeholder

    (finding,) = check_profile_fit(memory, DM41L)

    assert finding.level == ERROR
    assert "directory ends at 0x3EF" in finding.message
    assert check_profile_fit(memory, DM41X) == []


def test_a_file_one_register_smaller_fits():
    memory = _load("dm41x_base.d41")
    xm = memory.extended_memory
    xm.add_file("BIG", xm.TYPE_DATA, numbers=[1.0] * 362)

    assert check_profile_fit(memory, DM41L) == []


def test_unreadable_xm_directory_is_an_error():
    memory = _load("6x-xm.dm41")
    assert check_profile_fit(memory, DM41L) == []
    # Region 0's ceiling must be 0xBF; make it 0xBE.
    memory.set_register(0x40, Register.from_hex("000010062ef0be"))

    (finding,) = check_profile_fit(memory, DM41L)

    assert finding.level == ERROR
    assert "cannot be read" in finding.message
    assert "cannot be checked against a DM41L" in finding.message


# -- Warnings: leftover data ----------------------------------------------


def test_stale_data_in_the_third_region_is_only_a_warning():
    """dm41x_retpfl_before.d41 has junk at 0x301-0x3EF that no XM file owns
    (and, separately, CLEM assigned to a key)."""
    findings = check_profile_fit(_load("dm41x_retpfl_before.d41"), DM41L)

    assert _levels(findings) == [WARNING, WARNING]
    leftover = [f for f in findings if f.location.startswith("Registers")]
    (leftover,) = leftover
    assert leftover.location == "Registers 0x301-0x3EF"
    assert "belongs to no XM file" in leftover.message


def test_no_leftover_warning_when_files_already_explain_the_error():
    findings = check_profile_fit(_load("dm41x_manyfiles.dm41"), DM41L)
    assert not any(f.location.startswith("Registers") for f in findings)


# -- Warnings: XROM steps in programs -------------------------------------


def test_xrom_program_lists_each_dm41x_function_with_its_step():
    findings = check_profile_fit(_load("xrom.d41"), DM41L)

    assert _levels(findings) == [WARNING] * 18
    # The program is LBL "DM41X", ED$, X<I>Y, TRNG, then the 16 functions
    # of the DM41X module in table order (TRNG excepted, it is up front).
    expected_names = ["X<I>Y", "TRNG"] + [
        name
        for (byte1, byte2), name in DM41X_XROM_FUNCTIONS.items()
        if name not in ("X<I>Y", "TRNG")
    ]
    assert [f.message for f in findings] == [
        f"{name} is not built into a DM41L" for name in expected_names
    ]
    assert [f.location for f in findings] == [
        f'Program "DM41X", step {step}' for step in range(3, 21)
    ]


def test_ed_dollar_is_not_reported_because_a_dm41l_has_ed():
    findings = check_profile_fit(_load("xrom.d41"), DM41L)
    # Step 2 of the program is ED$; the warnings start at step 3 (X<I>Y).
    assert 'Program "DM41X", step 2' not in [f.location for f in findings]
    assert not any(f.message.split()[0] == "ED" for f in findings)


def test_dm41xn_program_warnings():
    findings = check_profile_fit(_load("dm41xn.dm41"), DM41L)

    assert [str(f) for f in findings] == [
        'Program "AAA", step 2: X<I>Y is not built into a DM41L',
        'Program "AAA", step 3: TRNG is not built into a DM41L',
    ]


def _xm_program(text):
    memory = _load("dm41x_base.d41")
    xm = memory.extended_memory
    xm.add_file("PROG", xm.TYPE_PROGRAM, instruction_bytes=decode_program_txt(text))
    return memory


def test_xm_program_files_are_checked_too():
    memory = _xm_program(b'LBL "T"\nLKAOFF\nEND\n')

    (finding,) = check_profile_fit(memory, DM41L)

    assert finding.level == WARNING
    assert finding.location == 'XM file "PROG", step 2'
    assert finding.message == "LKAOFF is not built into a DM41L"


def test_a_number_is_one_step_not_one_per_digit():
    memory = _xm_program(b'LBL "T"\n12.5\nSTO 01\nLKAOFF\nEND\n')

    (finding,) = check_profile_fit(memory, DM41L)

    # LBL, 12.5 (four bytes), STO 01, LKAOFF.
    assert finding.location == 'XM file "PROG", step 4'


def test_an_xrom_outside_the_cx_modules_is_reported_by_number():
    memory = _xm_program(b'LBL "T"\nXROM 10,05\nEND\n')

    (finding,) = check_profile_fit(memory, DM41L)

    assert finding.message == "XROM 10,05 is not built into a DM41L"


def test_cx_xroms_in_programs_are_not_reported():
    # PURXM in 6x-xm.dm41 calls EMDIRX and PURFL.
    assert check_profile_fit(_load("6x-xm.dm41"), DM41L) == []
    memory = _xm_program(b'LBL "T"\nSEEKPT\nTIME\nED\nEND\n')
    assert check_profile_fit(memory, DM41L) == []


def test_unlabelled_program_is_located_by_address():
    """unlabelled.dm41's first program is `"NO LABEL"`, PI, X<>Y, X^2, *,
    END with no global label. Replace its X^2 and * (one byte each) with
    LKAOFF (two bytes) so it holds an XROM without changing its length."""
    memory = _load("unlabelled.dm41")
    first, _second = memory.programs.list_programs()
    assert not first.is_named
    start = memory.programs.addr_for(first.start_addr, first.start_offset)
    memory.programs.write_bytes_forward(start - 11, b"\xa6\xb0")
    assert memory.programs.list_programs()[0].length == first.length

    (finding,) = check_profile_fit(memory, DM41L)

    assert finding.location == "Unlabelled program at 0x19b:0, step 4"
    assert finding.message == "LKAOFF is not built into a DM41L"


# -- Warnings: key assignments --------------------------------------------


def test_key_assignments_of_dm41x_functions_are_reported():
    findings = check_profile_fit(_load("dm41x_xrom_keys.d41"), DM41L)

    # ED$ on key 13 is the DM41L's ED; the other two are not.
    assert [str(f) for f in findings] == [
        "Key 12 (unshifted): LKAOFF is not built into a DM41L",
        "Key 11 (unshifted): LKAON is not built into a DM41L",
    ]


def test_shifted_key_is_named_as_shifted():
    memory = _load("dm41x_base.d41")
    memory.key_assignments.set_assignment(21, True, key_bytes_for(resolve("TRNG")))

    (finding,) = check_profile_fit(memory, DM41L)

    assert finding.location == "Key 21 (shifted)"


def test_single_byte_and_cx_assignments_are_not_reported():
    memory = _load("dm41x_base.d41")
    keys = memory.key_assignments
    keys.set_assignment(21, False, key_bytes_for(resolve("COS")))
    keys.set_assignment(22, False, key_bytes_for(resolve("SEEKPT")))

    assert check_profile_fit(memory, DM41L) == []


# -- Findings ---------------------------------------------------------------


def test_errors_come_first():
    memory = _load("dm41x_manyfiles.dm41")
    memory.key_assignments.set_assignment(11, False, key_bytes_for(resolve("LKAON")))

    findings = check_profile_fit(memory, DM41L)

    assert _levels(findings) == [ERROR, WARNING]


def test_a_finding_prints_its_location_then_its_message():
    finding = ProfileFinding(WARNING, "Key 12 (unshifted)", "LKAOFF is not built in")
    assert str(finding) == "Key 12 (unshifted): LKAOFF is not built in"


def test_format_findings_makes_a_bulleted_list():
    findings = [
        ProfileFinding(
            WARNING, "Key 11 (unshifted)", "LKAON is not built into a DM41L"
        ),
        ProfileFinding(
            WARNING, "Key 12 (unshifted)", "LKAOFF is not built into a DM41L"
        ),
    ]
    assert format_findings(findings) == (
        "- Key 11 (unshifted): LKAON is not built into a DM41L\n"
        "- Key 12 (unshifted): LKAOFF is not built into a DM41L"
    )


def test_format_findings_cuts_a_long_list_and_counts_the_rest():
    findings = [ProfileFinding(WARNING, f"Step {n}", "x") for n in range(1, 6)]

    text = format_findings(findings, limit=3)

    assert text.splitlines() == [
        "- Step 1: x",
        "- Step 2: x",
        "- Step 3: x",
        "...and 2 more.",
    ]
    assert format_findings(findings, limit=5).count("\n") == 4  # nothing cut
    assert format_findings([]) == ""
