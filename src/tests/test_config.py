import json
import os
import platform
import pytest
from config import ProjectConfig
from memory import DeviceMode

running_as_root = hasattr(os, "geteuid") and os.geteuid() == 0
skip_if_permission_bits_unenforced = pytest.mark.skipif(
    running_as_root or platform.system() == "Windows",
    reason="chmod-based directory/unreadable-file checks aren't enforced "
    "(root, or Windows os.chmod semantics)",
)

@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    """Points ProjectConfig.PREFS_FILE at a throwaway path
    for the test. It doesn't exist on disk until a test creates it."""
    fake_prefs_path = tmp_path / ".dm41_test_prefs.json"
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", fake_prefs_path)
    return fake_prefs_path

def test_defaults_when_no_prefs_file(prefs_file):
    """ProjectConfig() no longer touches disk at all -- __init__ just
    copies DEFAULT_PREFS. This test is really pinning down __init__'s
    behavior, not load()'s; nothing here calls .load()."""
    config = ProjectConfig()
    assert config.baudrate == ProjectConfig.DEFAULT_PREFS["baudrate"]
    assert config.serial_port == ProjectConfig.DEFAULT_PREFS["serial_port"]
    assert config.logging_level == ProjectConfig.DEFAULT_PREFS["logging_level"]
    assert config.appearance_mode == ProjectConfig.DEFAULT_PREFS["appearance_mode"]
    assert config.color_theme == ProjectConfig.DEFAULT_PREFS["color_theme"]

def test_load_merges_saved_values_over_defaults(prefs_file):
    """Only the keys present on disk should override the defaults.
    ProjectConfig() no longer auto-loads, so load() has to be called
    explicitly before checking what came off disk."""
    prefs_file.write_text(json.dumps({"baudrate": 115200}))
    config = ProjectConfig()
    config.load()
    assert config.baudrate == 115200
    assert (
        config.console_timeout_minutes
        == ProjectConfig.DEFAULT_PREFS["console_timeout_minutes"]
    )

def test_load_raises_exception_on_corrupt_json(prefs_file):
    """Corrupt JSON raises an exception. Construction itself can't raise
    any more (it no longer touches disk) -- the raise happens on the
    explicit load() call."""
    prefs_file.write_text("{not valid json")
    config = ProjectConfig()
    with pytest.raises(Exception) as excinfo:
        config.load()
    assert "Could not load preferences from" in str(excinfo.value)

def test_save_writes_current_prefs_to_disk(prefs_file):
    config = ProjectConfig()
    config.baudrate = 57600
    config.save()
    on_disk = json.loads(prefs_file.read_text())
    assert on_disk["baudrate"] == 57600

def test_save_with_explicit_prefs_argument(prefs_file):
    config = ProjectConfig()
    new_prefs = {**ProjectConfig.DEFAULT_PREFS, "logging_level": "DEBUG"}
    config.save(new_prefs)
    on_disk = json.loads(prefs_file.read_text())
    assert on_disk["logging_level"] == "DEBUG"
    assert config.logging_level == "DEBUG"

def test_property_setters_update_internal_state(prefs_file):
    config = ProjectConfig()
    config.serial_port = "/dev/tty.fake"
    config.console_timeout_minutes = 5
    config.appearance_mode = "Dark"
    config.color_theme = "green"
    assert config.serial_port == "/dev/tty.fake"
    assert config.console_timeout_minutes == 5
    assert config.appearance_mode == "Dark"
    assert config.color_theme == "green"
    assert config.get_all()["serial_port"] == "/dev/tty.fake"

def test_save_persists_appearance_and_theme(prefs_file):
    """appearance_mode/color_theme round-trip through the same single file
    as every other setting -- there's no more separate GUI prefs file."""
    config = ProjectConfig()
    config.appearance_mode = "Dark"
    config.color_theme = "green"
    config.save()
    on_disk = json.loads(prefs_file.read_text())
    assert on_disk["appearance_mode"] == "Dark"
    assert on_disk["color_theme"] == "green"
    reloaded = ProjectConfig()
    reloaded.load()
    assert reloaded.appearance_mode == "Dark"
    assert reloaded.color_theme == "green"

def test_round_trip_persists_across_instances(prefs_file):
    """Saving with one instance and loading with a fresh one should agree."""
    first = ProjectConfig()
    first.baudrate = 4800
    first.save()
    second = ProjectConfig()
    second.load()
    assert second.baudrate == 4800

@skip_if_permission_bits_unenforced
def test_save_raises_exception_on_permission_error(tmp_path, monkeypatch):
    """Verifies that save() raises an exception when writing fails."""
    readonly_dir = tmp_path / "readonly_dir"
    readonly_dir.mkdir()
    # Read + execute only, no write: the directory stays traversable, but
    # writing a new file inside it fails. (stat.S_IREAD alone also strips
    # the execute bit, which breaks exists()-style checks on children
    # rather than exercising the write failure this test is meant to
    # cover -- see the equivalent, correct 0o555 usage in
    # test_commands.py's no_permission_on_parent test.)
    os.chmod(readonly_dir, 0o555)
    fake_prefs_path = readonly_dir / ".dm41_test_prefs.json"
    monkeypatch.setattr(ProjectConfig, "PREFS_FILE", fake_prefs_path)
    config = ProjectConfig()
    with pytest.raises(Exception) as excinfo:
        config.save()
    assert "Could not save preferences to" in str(excinfo.value)


def test_high_contrast_defaults_to_on(prefs_file):
    assert ProjectConfig().high_contrast is True


def test_high_contrast_round_trips_through_the_file(prefs_file):
    config = ProjectConfig()
    config.high_contrast = False
    config.save()
    assert json.loads(prefs_file.read_text())["high_contrast"] is False

    reloaded = ProjectConfig()
    reloaded.load()
    assert reloaded.high_contrast is False


@pytest.mark.parametrize("junk", ["false", 0, None, "no"])
def test_a_hand_edited_high_contrast_value_reads_as_the_default(prefs_file, junk):
    prefs_file.write_text(json.dumps({"high_contrast": junk}))
    config = ProjectConfig()
    config.load()
    assert config.high_contrast is True

# -- The DM41L/DM41X mode (docs/dm41x_explorer_plan.md phase 6) -----------


def test_mode_defaults_to_dm41x(prefs_file):
    """A fresh config, with no preferences file, is in DM41X mode: the
    more common device (Mike, 2026-10-09)."""
    config = ProjectConfig()
    assert config.mode is DeviceMode.DM41X


def test_an_existing_user_with_no_mode_key_gets_dm41x(prefs_file):
    """The release-notes case: a preferences file written by an earlier
    version has no `mode`, so the user gets DM41X whichever calculator
    they own, until they set it themselves."""
    prefs_file.write_text(json.dumps({"baudrate": 115200, "color_theme": "green"}))
    config = ProjectConfig()
    config.load()
    assert config.mode is DeviceMode.DM41X
    assert config.baudrate == 115200, "the rest of their settings still load"


def test_a_saved_mode_is_read_back(prefs_file):
    prefs_file.write_text(json.dumps({"mode": "DM41L"}))
    config = ProjectConfig()
    config.load()
    assert config.mode is DeviceMode.DM41L


@pytest.mark.parametrize("bad", ["", "dm41l", "DM41XN", None, 41, [], {}])
def test_an_unreadable_mode_falls_back_to_the_default(prefs_file, bad):
    """A hand-edited preferences file must not stop the app starting."""
    prefs_file.write_text(json.dumps({"mode": bad}))
    config = ProjectConfig()
    config.load()
    assert config.mode is DeviceMode.DM41X


def test_setting_the_mode_stores_a_readable_name(prefs_file):
    """The preferences file stays readable JSON: the enum's name, not a
    repr or an ordinal."""
    config = ProjectConfig()
    config.mode = DeviceMode.DM41L
    config.save()
    assert json.loads(prefs_file.read_text())["mode"] == "DM41L"
    assert config.mode is DeviceMode.DM41L


def test_the_mode_setter_also_accepts_a_name(prefs_file):
    config = ProjectConfig()
    config.mode = "DM41L"
    assert config.mode is DeviceMode.DM41L


def test_a_saved_mode_survives_a_restart(prefs_file):
    """What the exit gate asks for: the persisted setting comes back."""
    first = ProjectConfig()
    first.mode = DeviceMode.DM41L
    first.save()

    second = ProjectConfig()
    second.load()
    assert second.mode is DeviceMode.DM41L
