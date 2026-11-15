"""The PX-41CX's device profile and mode (GitHub issue #82).

The PX-41CX is an HP-41CX clone: the DM41X's memory map, but only the
original HP-41CX's built-in ROMs (Extended Functions and Time, the DM41L's
hp41cx.json), other ROMs loaded by the user, no serial console for now, and
state files with a PX41 header.
"""

from pathlib import Path

import pytest

from memory import (
    DM41L,
    DM41X,
    PROFILES,
    PX41CX,
    DeviceMode,
    Memory,
    check_profile_fit,
)
from memory.keyboard import DM41X_LAYOUT, layout_for_profile
from memory.xrom_library import builtin_xrom_names

DATA_DIR = Path(__file__).parent / "data"


def test_the_profile_is_registered_by_name():
    assert PROFILES["PX-41CX"] is PX41CX
    assert PX41CX.name == "PX-41CX"


def test_its_extended_memory_is_the_dm41xs():
    assert PX41CX.xm_regions == DM41X.xm_regions
    assert PX41CX.xm_raw_registers == DM41X.xm_raw_registers


def test_its_built_in_xroms_are_the_original_hp41cxs():
    """The DM41L's set, without the DM41X's own additions."""
    assert PX41CX.builtin_xroms == DM41L.builtin_xroms
    assert PX41CX.builtin_xroms < DM41X.builtin_xroms
    assert set(builtin_xrom_names("PX-41CX")) == set(builtin_xrom_names("DM41L"))


def test_other_roms_are_loaded_by_the_user_and_there_is_no_serial_console():
    assert PX41CX.loads_modules
    assert not PX41CX.supports_serial


def test_it_writes_px41_states():
    assert PX41CX.state_header == "PX41"
    assert DM41L.state_header == DM41X.state_header == "DM41"


def test_a_new_state_for_it_is_written_in_its_own_layout():
    written = Memory(profile=PX41CX).to_string()
    assert written.startswith("PX41\n") and written.endswith("\nEOF\n")
    assert Memory(profile=DM41X).to_string().startswith("DM41\n")


def test_a_dm41_state_opened_with_it_keeps_its_dm41_header():
    """The header comes from the file, not the profile."""
    memory = Memory.from_file(DATA_DIR / "dm41x_manyfiles.dm41", profile=PX41CX)
    assert memory._header == "DM41"


def test_it_draws_the_classic_hp41_keyboard():
    assert layout_for_profile(PX41CX) is DM41X_LAYOUT


def test_its_mode():
    mode = DeviceMode.PX41CX
    assert mode.value == "PX-41CX"
    assert mode.profile is PX41CX
    assert not mode.supports_serial
    assert DeviceMode.from_value("PX-41CX") is mode
    assert list(DeviceMode) == [DeviceMode.DM41L, DeviceMode.DM41X, mode]


@pytest.mark.parametrize(
    "name", ["px41cx_SIZE300.px41", "px41cx_ASN.px41", "px41cx_CLOCK.px41"]
)
def test_the_dumps_with_upper_extended_memory_fit_it(name):
    """These hold data in the second Extended Memory module's region, and
    nothing is reported about it. The only findings are keys holding XROMs
    from ROMs the user had loaded (the Advantage Pac's XROM 22 in ASN and
    CLOCK, and XROM 02 in ASN), which it does not have built in."""
    memory = Memory.from_file(DATA_DIR / name, profile=PX41CX)
    findings = check_profile_fit(memory, PX41CX)
    assert all(
        str(finding).startswith("Key ")
        and str(finding).endswith("is not built into a PX-41CX")
        for finding in findings
    ), findings


def test_every_mode_has_a_save_extension():
    from gui.state_files import DEFAULT_EXTENSION_BY_MODE

    assert set(DEFAULT_EXTENSION_BY_MODE) == set(DeviceMode)
    assert DEFAULT_EXTENSION_BY_MODE[DeviceMode.PX41CX] == ".px41"


def test_every_mode_has_a_preferences_hint():
    pytest.importorskip("customtkinter")
    from gui.preferences_dialog import MODE_HINTS, MODE_VALUES

    assert set(MODE_HINTS) == set(DeviceMode)
    assert "PX-41CX" in MODE_VALUES
