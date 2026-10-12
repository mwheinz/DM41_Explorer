import json
import os
import platform
import pytest
from config import FONT_ROLES, ProjectConfig
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


def test_failed_save_leaves_the_existing_prefs_file_intact(prefs_file, monkeypatch):
    """save() is atomic: if the final replace fails, the old preferences
    file is untouched (not truncated) and no temporary file is left."""
    prefs_file.write_text(json.dumps({"baudrate": 115200}))
    config = ProjectConfig()
    config.load()
    config.baudrate = 9600

    def fail(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(Exception, match="Could not save preferences"):
        config.save()

    assert json.loads(prefs_file.read_text()) == {"baudrate": 115200}
    assert [p.name for p in prefs_file.parent.iterdir()] == [prefs_file.name]


def test_save_writes_the_same_json_as_before(prefs_file):
    config = ProjectConfig()
    config.baudrate = 9600
    config.save()
    assert prefs_file.read_text(encoding="utf-8") == json.dumps(
        config.get_all(), indent=2
    )


def test_configs_do_not_share_the_default_recent_files_list(prefs_file):
    """DEFAULT_PREFS holds a list; each config must get its own copy."""
    first = ProjectConfig()
    first._prefs["recent_files"].append("/tmp/x.dm41")
    assert ProjectConfig().recent_files == []
    assert ProjectConfig.DEFAULT_PREFS["recent_files"] == []


def test_missing_prefs_file_keeps_the_defaults(prefs_file):
    config = ProjectConfig()
    config.load()  # no file: not an error
    assert config.baudrate == ProjectConfig.DEFAULT_PREFS["baudrate"]


@pytest.mark.parametrize("content", ["[1, 2, 3]", '"text"', "42", "null"])
def test_load_rejects_a_prefs_file_that_is_not_a_json_object(prefs_file, content):
    from config import PreferencesError

    prefs_file.write_text(content)
    config = ProjectConfig()
    config.baudrate = 9600
    with pytest.raises(PreferencesError, match="expected a JSON object"):
        config.load()
    assert config.baudrate == 9600  # unchanged


def test_load_and_save_failures_are_preferences_errors(prefs_file, monkeypatch):
    from config import PreferencesError

    prefs_file.write_text("{not valid json")
    with pytest.raises(PreferencesError):
        ProjectConfig().load()

    def fail(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PreferencesError, match="replace failed"):
        ProjectConfig().save()


def test_plain_settings_round_trip_through_the_file(prefs_file):
    config = ProjectConfig()
    config.serial_port = "/dev/ttyTEST"
    config.color_theme = "green"
    config.save()
    reloaded = ProjectConfig()
    reloaded.load()
    assert reloaded.serial_port == "/dev/ttyTEST"
    assert reloaded.color_theme == "green"


def test_window_size_defaults_to_none(prefs_file):
    config = ProjectConfig()
    config.load()
    assert config.window_size is None


def test_window_size_round_trips_through_the_file(prefs_file):
    config = ProjectConfig()
    config.window_size = (1234, 777)
    config.save()
    again = ProjectConfig()
    again.load()
    assert again.window_size == (1234, 777)


@pytest.mark.parametrize(
    "width,height", [(0, 0), (-1, 500), (500, 0), ("wide", "tall"), (None, None)]
)
def test_unusable_saved_window_sizes_read_as_none(prefs_file, width, height):
    prefs_file.write_text(json.dumps({"window_width": width, "window_height": height}))
    config = ProjectConfig()
    config.load()
    assert config.window_size is None


# -- XROM libraries (GitHub issue #53) ----------------------------------------


def test_no_xrom_libraries_are_arranged_by_default(prefs_file):
    config = ProjectConfig()
    assert config.xrom_library_entries(DeviceMode.DM41X) == []
    assert config.enabled_xrom_library_ids("DM41L") == []


def test_xrom_library_entries_round_trip_per_mode(prefs_file):
    config = ProjectConfig()
    config.set_xrom_library_entries(
        DeviceMode.DM41X, [("advantage", True), ("math", False), ("gone", True)]
    )
    config.set_xrom_library_entries("DM41L", [("math", True)])
    config.save()

    reloaded = ProjectConfig()
    reloaded.load()
    assert reloaded.xrom_library_entries("DM41X") == [
        ("advantage", True),
        ("math", False),
        ("gone", True),
    ]
    assert reloaded.enabled_xrom_library_ids(DeviceMode.DM41X) == ["advantage", "gone"]
    assert reloaded.xrom_library_entries(DeviceMode.DM41L) == [("math", True)]
    stored = json.loads(prefs_file.read_text())["xrom_libraries"]
    assert stored["DM41X"][0] == {"id": "advantage", "enabled": True}


def test_setting_one_modes_libraries_keeps_the_others(prefs_file):
    config = ProjectConfig()
    config.set_xrom_library_entries("DM41L", [("math", True)])
    config.set_xrom_library_entries("DM41X", [("stat", True)])
    assert config.xrom_library_entries("DM41L") == [("math", True)]


@pytest.mark.parametrize(
    "stored",
    [
        "not a dict",
        {"DM41X": "not a list"},
        {"DM41X": ["math", 7, None]},
        {"DM41X": [{"id": 3}, {"enabled": True}, {"id": ""}]},
    ],
)
def test_unreadable_xrom_library_settings_read_as_none(prefs_file, stored):
    """A hand-edited preferences file must not stop the app starting."""
    prefs_file.write_text(json.dumps({"xrom_libraries": stored}))
    config = ProjectConfig()
    config.load()
    assert config.xrom_library_entries("DM41X") == []


def test_xrom_library_entries_skip_repeats_and_need_true_to_enable(prefs_file):
    prefs_file.write_text(
        json.dumps(
            {
                "xrom_libraries": {
                    "DM41X": [
                        {"id": "math", "enabled": "yes"},
                        {"id": "stat", "enabled": True},
                        {"id": "math", "enabled": True},
                    ]
                }
            }
        )
    )
    config = ProjectConfig()
    config.load()
    assert config.xrom_library_entries("DM41X") == [("math", False), ("stat", True)]


def test_the_xrom_folder_is_named_after_the_preferences_file(prefs_file):
    assert (
        ProjectConfig().xrom_directory
        == prefs_file.parent / ".dm41_test_prefs" / "xroms"
    )
    assert ProjectConfig.PREFS_FILE.name.endswith(".json")


# -- Window position (GitHub issue #63) -----------------------------------------


def test_no_window_position_is_saved_by_default(prefs_file):
    assert ProjectConfig().window_position is None


@pytest.mark.parametrize("position", [(40, 60), (-1200, 100), (0, -900)])
def test_window_position_round_trips(prefs_file, position):
    config = ProjectConfig()
    config.window_position = position
    config.save()
    reloaded = ProjectConfig()
    reloaded.load()
    assert reloaded.window_position == position


@pytest.mark.parametrize(
    "stored",
    [
        {"window_x": "40", "window_y": 60},
        {"window_x": 40},
        {"window_x": True, "window_y": 60},
        {"window_x": 4.5, "window_y": 60},
    ],
)
def test_an_unusable_window_position_reads_as_none(prefs_file, stored):
    prefs_file.write_text(json.dumps(stored))
    config = ProjectConfig()
    config.load()
    assert config.window_position is None


# -- font roles (GitHub issue #61) ---------------------------------------------


def test_every_font_role_defaults_to_the_platform_default(prefs_file):
    config = ProjectConfig()
    config.load()
    assert {role: config.font_role(role) for role in FONT_ROLES} == {
        "ui": ("", 0, False),
        "small": ("", 0, False),
        "title": ("", 0, True),
        "mono": ("", 0, False),
    }


def test_font_roles_round_trip_through_the_file(prefs_file):
    config = ProjectConfig()
    config.set_font_role("mono", "Menlo", 12, True)
    config.set_font_role("title", "Helvetica", 18, False)
    config.save()
    assert json.loads(prefs_file.read_text())["font_mono"] == ["Menlo", 12, True]
    reloaded = ProjectConfig()
    reloaded.load()
    assert reloaded.font_role("mono") == ("Menlo", 12, True)
    assert reloaded.font_role("title") == ("Helvetica", 18, False)


def test_the_old_application_font_becomes_the_ui_role(prefs_file):
    prefs_file.write_text(json.dumps({"font_family": "Helvetica", "font_size": 15}))
    config = ProjectConfig()
    config.load()
    assert config.font_role("ui") == ("Helvetica", 15, False)
    config.save()
    saved = json.loads(prefs_file.read_text())
    assert "font_family" not in saved and "font_size" not in saved
    assert saved["font_ui"] == ["Helvetica", 15, False]


def test_the_old_default_application_font_migrates_to_the_default(prefs_file):
    prefs_file.write_text(json.dumps({"font_family": "", "font_size": 0}))
    config = ProjectConfig()
    config.load()
    assert config.font_role("ui") == ("", 0, False)
    assert "font_family" not in config.get_all()


def test_a_saved_ui_role_wins_over_leftover_old_font_keys(prefs_file):
    prefs_file.write_text(
        json.dumps(
            {
                "font_ui": ["Menlo", 11, True],
                "font_family": "Helvetica",
                "font_size": 15,
            }
        )
    )
    config = ProjectConfig()
    config.load()
    assert config.font_role("ui") == ("Menlo", 11, True)


@pytest.mark.parametrize(
    "stored, expected",
    [
        ("Helvetica", ("", 0, True)),
        (["Helvetica", 12], ("", 0, True)),
        (None, ("", 0, True)),
        ([7, 12, False], ("", 12, False)),
        (["Helvetica", "12", False], ("Helvetica", 0, False)),
        (["Helvetica", -4, False], ("Helvetica", 0, False)),
        (["Helvetica", True, False], ("Helvetica", 0, False)),
        (["Helvetica", 12, "yes"], ("Helvetica", 12, True)),
        ([" Helvetica ", 12, False], ("Helvetica", 12, False)),
    ],
)
def test_a_hand_edited_font_role_reads_part_by_part_as_the_default(
    prefs_file, stored, expected
):
    prefs_file.write_text(json.dumps({"font_title": stored}))
    config = ProjectConfig()
    config.load()
    assert config.font_role("title") == expected


def test_an_unknown_font_role_is_an_error(prefs_file):
    config = ProjectConfig()
    with pytest.raises(ValueError):
        config.font_role("huge")
    with pytest.raises(ValueError):
        config.set_font_role("huge", "Helvetica", 12, False)
