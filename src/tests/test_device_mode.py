"""Tests for the DM41L/DM41X mode layer: DeviceMode and DEFAULT_MODE in
memory/device_profile.py, DeviceProfile.supports_serial, and
evaluate_mode_switch() in memory/profile_fit.py (phase 6 of
docs/dm41x_explorer_plan.md).

The mode is the single source of truth for the active profile. These tests
cover the core of that, with no Tk window involved; the application's own
switch sequence is tested in test_app.py.

The fixtures that matter here:
  - dm41x_manyfiles.dm41: 59 XM files across all three regions, so it does
    NOT fit a DM41L and must block DM41L mode.
  - dm41x_retpfl_before.d41: stale data above 0x300 that belongs to no XM
    file -- a warning, so it DOES load in DM41L mode.
  - xrom.d41: 18 DM41X-only XROM steps -- warnings reported at upload, so
    it also loads in DM41L mode.
"""

from pathlib import Path

import pytest

from memory import (
    DEFAULT_MODE,
    DM41L,
    DM41X,
    ERROR,
    DeviceMode,
    DeviceProfile,
    DM41MemoryError,
    Memory,
    Register,
    XMFile,
    check_profile_fit,
    evaluate_mode_switch,
)

DATA_DIR = Path(__file__).parent / "data"


def _load(name, profile=DM41X):
    return Memory.from_file(DATA_DIR / name, profile=profile)


# -- DeviceMode itself ----------------------------------------------------


def test_there_is_one_mode_per_profile():
    """Every profile is reachable as a mode, and every mode names a real
    profile. A profile with no mode could never be selected."""
    assert {mode.value for mode in DeviceMode} == set(PROFILE_NAMES)


PROFILE_NAMES = ("DM41L", "DM41X")


@pytest.mark.parametrize("mode,expected", [
    (DeviceMode.DM41L, DM41L),
    (DeviceMode.DM41X, DM41X),
])
def test_a_mode_resolves_to_its_profile(mode, expected):
    assert mode.profile is expected


def test_the_default_mode_is_dm41x():
    """Mike, 2026-10-09: the DM41X is the more common device, so a user
    with no `mode` in their preferences file gets DM41X."""
    assert DEFAULT_MODE is DeviceMode.DM41X


@pytest.mark.parametrize("value,expected", [
    ("DM41L", DeviceMode.DM41L),
    ("DM41X", DeviceMode.DM41X),
    (DeviceMode.DM41L, DeviceMode.DM41L),
    (DeviceMode.DM41X, DeviceMode.DM41X),
])
def test_from_value_accepts_names_and_modes(value, expected):
    assert DeviceMode.from_value(value) is expected


@pytest.mark.parametrize("value", [None, "", "dm41l", "DM41XN", 0, 41, [], {}])
def test_from_value_falls_back_instead_of_raising(value):
    """A hand-edited preferences file must not stop the app starting, so
    anything unrecognised (including the wrong case) becomes the default."""
    assert DeviceMode.from_value(value) is DEFAULT_MODE


def test_from_value_honours_an_explicit_fallback():
    assert DeviceMode.from_value("junk", DeviceMode.DM41L) is DeviceMode.DM41L


# -- supports_serial ------------------------------------------------------


def test_only_the_dm41l_supports_serial():
    """The DM41L has the serial console; the DM41X is strictly
    file-based, so its mode offers no serial actions."""
    assert DM41L.supports_serial is True
    assert DM41X.supports_serial is False


def test_a_mode_asks_its_profile_about_serial():
    """The gating must go through the profile, so that a DM41XN profile
    (disk AND serial) can be added without revisiting it."""
    assert DeviceMode.DM41L.supports_serial is DM41L.supports_serial
    assert DeviceMode.DM41X.supports_serial is DM41X.supports_serial


def test_a_new_profile_has_to_opt_into_serial():
    """The default is False, so forgetting the field cannot accidentally
    expose the serial actions for a model that has no console."""
    quiet = DeviceProfile("quiet", ((0x40, 0xBF),), frozenset())
    assert quiet.supports_serial is False


def test_a_profile_can_declare_serial_and_three_regions():
    """What a DM41XN profile will look like: the DM41X's memory with a
    serial console. Nothing in the profile forbids the combination."""
    future = DeviceProfile(
        "DM41XN",
        DM41X.xm_regions,
        DM41X.builtin_xroms,
        supports_serial=True,
    )
    assert future.supports_serial is True
    assert future.xm_raw_registers == DM41X.xm_raw_registers


# -- evaluate_mode_switch: what blocks a load -----------------------------


def test_a_dm41x_state_blocks_dm41l_mode():
    findings = evaluate_mode_switch(_load("dm41x_manyfiles.dm41"), DM41L)
    assert findings, "59 files across three regions cannot load on a DM41L"
    assert all(finding.level == ERROR for finding in findings)
    assert "XM files use memory a DM41L does not have" in str(findings[0])


def test_every_state_fits_its_own_model():
    for name in (
        "dm41x_manyfiles.dm41",
        "dm41x_base.d41",
        "dm41x_retpfl_before.d41",
        "xrom.d41",
        "dm41x_xrom_keys.d41",
        "lkaoff3.d41",
    ):
        assert evaluate_mode_switch(_load(name), DM41X) == [], name


def test_a_dm41l_state_loads_in_dm41l_mode():
    memory = _load("lander.dm41", profile=DM41L)
    assert evaluate_mode_switch(memory, DM41L) == []


def test_stale_data_above_region_two_does_not_block_a_load():
    """dm41x_retpfl_before.d41 has junk at 0x301-0x3EF belonging to no XM
    file. check_profile_fit warns about it, but it is not a reason to
    refuse the load or to offer a mode switch."""
    memory = _load("dm41x_retpfl_before.d41")
    assert evaluate_mode_switch(memory, DM41L) == []
    assert check_profile_fit(memory, DM41L), "the warning is still reported"


def test_dm41x_only_xroms_never_block_a_load():
    """xrom.d41 calls 18 functions a DM41L lacks. Those are warnings shown
    when SENDING a state, not a reason to refuse to open it -- the user may
    mean to retype the step on the calculator."""
    memory = _load("xrom.d41")
    assert evaluate_mode_switch(memory, DM41L) == []
    assert len(check_profile_fit(memory, DM41L)) == 18


def test_xrom_key_assignments_never_block_a_load():
    memory = _load("dm41x_xrom_keys.d41")
    assert evaluate_mode_switch(memory, DM41L) == []
    assert check_profile_fit(memory, DM41L)


def test_blocking_findings_are_a_subset_of_the_full_check():
    """evaluate_mode_switch is deliberately a filter over
    check_profile_fit, so the two can never disagree about what fits."""
    for name in ("dm41x_manyfiles.dm41", "dm41x_retpfl_before.d41", "xrom.d41"):
        memory = _load(name)
        blocking = evaluate_mode_switch(memory, DM41L)
        full = check_profile_fit(memory, DM41L)
        assert blocking == [f for f in full if f.level == ERROR], name


def test_a_directory_ending_outside_the_target_blocks_a_load():
    """A single XM data file of exactly 363 registers keeps all its data
    below 0x2EF, but the register that ends the directory lands at 0x3EF,
    where a DM41L has no memory. One register fewer fits. (The finding
    itself is test_profile_fit's; what matters here is that this kind of
    error reaches evaluate_mode_switch and so blocks a load.)"""
    memory = Memory(profile=DM41X)
    memory.extended_memory.add_file("BIG", XMFile.TYPE_DATA, numbers=[1.0] * 363)
    assert evaluate_mode_switch(memory, DM41L)

    smaller = Memory(profile=DM41X)
    smaller.extended_memory.add_file("BIG", XMFile.TYPE_DATA, numbers=[1.0] * 362)
    assert evaluate_mode_switch(smaller, DM41L) == []


def test_an_unreadable_directory_blocks_a_load():
    """If the XM directory cannot be walked at all, the state cannot be
    judged, so it must not be loaded in the narrower mode on an
    assumption. A type-0 header is one way to make list_files() raise."""
    memory = Memory(profile=DM41X)
    memory.extended_memory.add_file("OK", XMFile.TYPE_DATA, numbers=[1.0])
    header = memory.extended_memory.list_files()[0].header_addr
    register = memory.get_register(header)
    memory.set_register(header, Register(bytes([0x00]) + register.get_bytes()[1:]))

    with pytest.raises(DM41MemoryError):
        memory.extended_memory.list_files()
    findings = evaluate_mode_switch(memory, DM41L)
    assert findings and findings[0].level == ERROR
    assert "cannot be read" in str(findings[0])


def test_an_empty_state_fits_either_mode():
    for profile in (DM41L, DM41X):
        assert evaluate_mode_switch(Memory(profile=profile), DM41L) == []
        assert evaluate_mode_switch(Memory(profile=profile), DM41X) == []
