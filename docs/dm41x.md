# The DM41X as a USB Disk

2026-10-04 · Michael Heinz (research by Claude)

This document describes how the DM41X exchanges files with a computer: how its USB disk mode is entered, what is on the disk, how the clock can be set and corrected, and how USB power behaves. The state file on the disk is described in `state_format.md`, and the Full Backup set in `backup_set.md` and `cst.md`.

Everything here is about the DM41X. Nothing is known, and nothing is assumed, about the DM41XN.

## 1. Source Material

- **DM41X User Manual v1.34 (2026-09-24)** ([online](https://technical.swissmicros.com/dm41x/doc/dm41x_user_manual.html)): §2.1 specifications, §2.2 CPU speed, §2.7 battery, §3.4 persistent settings, §3.7 RTC correction, §6.2.5 Activate USB Disk, §6.4 Settings, §7.1 firmware update, ch. 8 troubleshooting.
- **The on-device `/HELP/41x.html`** (DM41X Quick Reference).
- **[DMCP firmware history](https://technical.swissmicros.com/dmcp/firmware/history.html)** (v3.12, v3.19).
- **A DM41X mounted on macOS** on 2026-10-04, and the files it held.
- The original notes, with the reasoning behind each finding, are archived in [issue #78](https://github.com/mwheinz/DM41_Explorer/issues/78#issuecomment-6885499867).

## 2. USB Disk Mode

Every documented way into USB disk mode is a physical action on the calculator:

- **Menu:** SHIFT+USR (SETUP) → File → 5. Activate USB Disk. The CST screen's `5` key does the same.
- **DMCP System menu:** the same "Activate USB Disk" item.
- **Hardware recovery:** with the calculator off, hold R/S and press the RESET pinhole (manual, ch. 8).

The manual describes the USB port only as a mass storage device. No serial or other control interface is documented, so a computer cannot put the calculator into disk mode, and DM41_Explorer can only work on the state files the calculator has written. (The DM41L, by contrast, has a serial console.) Whether the DM41X shows up on USB at all while *not* in disk mode has not been tested.

### Files the firmware reads from the disk

After each exit from disk mode the firmware checks the root of the disk for:

| File | Effect |
| --- | --- |
| `/param.cfg` | Restores the persistent settings, then is deleted. |
| `/rtccalib.cfg` | RTC drift correction (section 4). |
| a firmware `.bin` | Offers to flash it. |

## 3. What Is on the Disk

On a calculator a few days old:

```
/BACKUP   memorylost.b41 .cst .d41 .m41 .ram     (Setup > Settings > Create Full Backup)
/HELP     41x.html  am_help.html  help_devel.html
/MODS     the .mod files of the 20 application pacs
/OFFIMG   .bmp images shown while the calculator is off (400×240, 1-bit)
/STATE    memorylost.d41                         (the default folder for state files)
```

`/PROG`, `/KEYS`, `/RAM` and `/SCREENS` did not exist yet; the calculator creates them when it first saves into them. `/BACKUP` is not in the manual's list of special directories, but Create Full Backup writes there. macOS adds `._*` files and a `.fseventsd/` folder of its own.

### What a `.d41` state records

A `.d41` file is the same text format as a DM41L `.dm41` (`state_format.md`). Compared with a "Memory Lost" state from a DM41L (`tests/data/empty.dm41`), the DM41X's own `memorylost.d41` differs only in:

- **Flag 31 set** (DMY date mode). The manual says flag 31 is saved in the `.d41`, not in the persistent settings.
- **Register R's low bits** (`0000000000e000`). These are outside the key-assignment flags; see `key_assignments.md`.
- **The special registers** `A` to `G`, which differ from save to save (`state_format.md` §3.3).

## 4. The Clock

Time and date are set only on the calculator, in Setup → Settings → Set Time / Set Date. Σ+ in Set Date switches between DMY and MDY. The calculator has no network connection, and the manual describes no way to set the clock from a computer. `param.cfg` does not carry the time.

**Drift correction.** Manual §3.7: create `/rtccalib.cfg` in the root of the disk, containing one integer `C`. For a measured drift of `P` ppm,

    C = 2^20 * P / (10^6 + P)        (approximately 1.04858 * P)

with `-511 <= C <= 512`. While a correction is active, the ppm value is shown at the end of the Set Time line in Settings.

Two untested ideas for a future tool:

- **Measuring drift.** Files the calculator writes get FAT timestamps from its own clock, to 2 seconds. Comparing them with the computer's time over several days would give `P`.
- **A clock-setting program.** A generated program that runs the HP-41CX `SETIME` and `SETDATE` functions with the computer's time in it. Whether these set the same clock as the Settings screen has not been tested.

## 5. USB Power

- **The battery is not charged.** It is a non-rechargeable CR2032, and no charging circuit is described.
- **The calculator almost certainly runs from USB power while connected.** The CPU runs at 24 MHz on battery and 80 MHz with the USB cable connected (§2.2), and the firmware update procedure says USB power is needed after the disk has been ejected (§7.1).
- **Not stated:** whether the battery is bypassed while connected.

## 6. Open Questions

- Does the DM41X show up on USB at all when disk mode is off? (`system_profiler SPUSBDataType` on a Mac would show it.)
- Does `RESET`+`[+]`, which enters disk mode directly on the DM42 (DMCP v3.12), work on the DM41X?
- Do `SETIME` and `SETDATE` change the clock shown in Settings?
- What does a `.ram` file hold when a RAM-using module is active (`backup_set.md`)?
