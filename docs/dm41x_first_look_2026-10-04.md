# DM41X First Look: Disk Mode, Clock, USB Power, and the Files on the Volume

2026-10-04 · Michael Heinz (research by Claude)

First hands-on findings from a DM41X mounted at `/Volumes/DM41X`, answering four questions from the DM41X_Explorer plan. Sources are the DM41X User Manual v1.34 (2026-09-24, local copy in `DM41X/DM41X_Data/`), the on-device `/HELP` pages, SwissMicros' DMCP firmware history, and the files on the volume itself. The SwissMicros forum returned HTTP 403 to the fetch tool, so nothing here relies on forum posts.

**Scope.** Everything below is about the DM41X. Nothing was found, and nothing is assumed, about the DM41XN prototype.

## Summary

| Question | Answer | Confidence |
| --- | --- | --- |
| 1. Can DM41X_Explorer trigger USB disk mode? | No documented way. Every route is an action on the calculator. | High that none is documented; the absence of any host channel is inferred |
| 2. Can the clock be set from host or network time? | Not directly. Drift can be measured and corrected; a generated program might set it. | High for "not directly"; the program idea is untested |
| 3. Does USB charge it or power it? | Does not charge (non-rechargeable CR2032). Almost certainly runs from USB power while connected. | High / Medium |
| 4. How do the volume's files compare to DM41L dumps? | `.d41` is the DM41L `.dm41` text format. The repo's existing loader reads it unchanged. | High |

## 1. Triggering disk mode

### What the documentation says

Every documented way into USB disk mode is a physical action on the calculator:

- **Menu:** SHIFT+USR (SETUP) → File → 5. Activate USB Disk.
- **DMCP System menu:** the same "Activate USB Disk" item.
- **Hardware recovery:** with the calculator off, hold R/S and press the RESET pinhole (manual, ch. 8 "Calculator troubleshooting").
- **DM42 only (unverified on the DM41X):** the DMCP changelog for v3.12 (2019-01-22) lists "RESET+[+] jumps directly to MSC mode".

The manual's specification table describes the PC connection only as "USB-Micro-B port, connects as USB mass storage device". No serial or CDC interface is documented. Third-party material on the sibling DM42/DM32 (for example the DB48x install guide) likewise describes only menu activation. A MicroPython port for these calculators ([dmpy](https://github.com/fnordsh/dmpy)) lists USB-serial as "not yet working".

### What this implies (inference)

With no documented serial or control channel, the host has nothing to send a "enter disk mode" command through. Whether the DM41X enumerates on USB at all while *not* in disk mode is not stated and has not been tested.

### What the tool can do instead

1. **Detect the mount.** Watch for the volume and show a prompt with the key sequence above.
2. **Use the config files the firmware reads from the FAT disk.** The firmware checks the disk after each disk-mode exit for:
   - `/param.cfg` (settings restore; deleted after it is applied),
   - `/rtccalib.cfg` (RTC drift correction, see section 2),
   - a firmware `.bin` in the root (it then offers to flash).
3. **Open question for the user to test.** With the cable connected and disk mode *off*, run `system_profiler SPUSBDataType` on the Mac. If nothing appears, there is no channel at all. If something does appear, record it; it matters for the DM41XN.

## 2. Setting the clock

### What the documentation says

Time and date are set only on the calculator: Setup → Settings → Set Time / Set Date ("Press Set to write the new time to the calculator clock"). Σ+ in Set Date toggles DMY/MDY. The calculator has no network connection, and the manual says nothing about syncing to a host or to network time.

`param.cfg` does not carry the time. The persistent settings it exports are the YMD setting, CLK24 (bit 6 of scratch register B), display modes, module-screen flags, slow auto-repeat and printer line delay. DMY (flag 31) lives in the `.d41` state file, not in UConf.

### Options that do exist

- **Drift correction (documented).** Manual §3.7: create `/rtccalib.cfg` in the root of the FAT disk containing an integer `C`, where `C = 2^20 * P / (10^6 + P)` for a measured drift of `P` ppm, with `-511 <= C <= 512`. The approximation `C = 1.04858 * P` is also given. Once active, the ppm value is shown at the end of the "Set Time" line in Settings.
- **Measuring drift from the host (idea).** Files the calculator writes (state saves, backups, screenshots) get FAT timestamps from the calculator's own clock, with 2-second FAT resolution. Comparing those to host time over several days gives a drift estimate, from which the tool could write `rtccalib.cfg`.
- **Generated time-setting program (idea, untested).** The tool could write a small RAW program that runs the HP-41CX `SETIME` and `SETDATE` functions with the host's time baked in; the user loads it from `/PROG` and runs it. Accuracy would be a second or two. It is not verified that these functions set the same RTC the Settings screen does.

### Observation about the sample unit

The unit in the samples is in DMY date mode (flag 31 set in both `.d41` files, see section 4).

## 3. USB power

- **Charging: no.** The battery is a non-rechargeable CR2032 (specifications table and §2.7); no charging circuit is described.
- **Powering from USB: almost certainly.**
  - Manual §2.2: "CPU speed is 24MHz when running on battery (due to limited battery current) and increases to 80MHz when the USB cable is connected."
  - Manual §7.1 (firmware update): "Do not unplug the cable, even after the calculator's USB disk has been successfully ejected by the OS, as USB power is required for the rest of the procedure."
- **Not stated:** that the battery is bypassed while connected, so it is not established that the battery is spared. A review of the DM42n (a different model) says it draws power from the USB connection; that is not evidence for the DM41X.

## 4. The files on the volume vs. DM41L dumps

### Volume contents at inspection time

```
/BACKUP   memorylost.b41 .cst .d41 .m41 .ram     (from Setup > Settings > Create Full Backup)
/HELP     41x.html  am_help.html  help_devel.html
/MODS     20 .mod files
/OFFIMG   25 .bmp files, plus Mac-written ._binary.bmp and ._MWH.bmp
/STATE    memorylost.d41
.fseventsd/   (macOS)
```

No `/PROG`, `/KEYS`, `/RAM` or `/SCREENS` yet; presumably created on demand. `/BACKUP` is not in the manual's list of special directories, though Create Full Backup produces it. The `._*` files and `.fseventsd` were written by macOS, not the calculator.

### `.d41` vs `.dm41`

The DM41X `.d41` is the DM41L `.dm41` text format:

- first line `DM41` (not `DM41X`);
- register lines keyed by hex address, four registers per line, only non-zero groups listed;
- then `A:`, `B:`, `C:`, `S:`, `M:`, `N:`, `G:` lines;
- LF line endings and a trailing double space on each register line (see "Trailing spaces" below: this is not specific to the DM41X, and the calculator does not need it);
- 331 bytes, the same size as `src/tests/data/empty.dm41`.

`Memory.from_file()` loaded both DM41X files (BACKUP and STATE copies) without modification.

Differences from `empty.dm41`:

| Field | DM41L `empty.dm41` | DM41X | Meaning |
| --- | --- | --- | --- |
| Register d (0x0e), flags | flags 26, 28, 29, 37, 40 | same, plus **flag 31** | DMY date mode. The manual lists "DMY (Flag 31) → [.d41]". Decoded with the repo's own `get_all_flags()`. |
| Register R (0x0a) | `00000000000000` | `0000000000e000` | See below: not a key flag. |
| CPU registers A, B, C, S, M, N | various | various | Incidental CPU snapshot (see below). |

The two DM41X state files (BACKUP written 08:59, STATE written 09:02 local time) differ from each other only in CPU registers B, C, S and N, so those registers change from save to save and should be round-tripped verbatim.

### Register R: `e000` is not a key position

The repo defines the unshifted KEYFLAGS bitmap as the first 36 bits of R, counted from the most significant bit (`docs/key_assignments.md`, "The Key Assignment Flags"). In `0000000000e000` those are the leftmost nine hex digits, `000000000`, so no key flag is set; no valid key reports an assignment. The `e` is hex digit 10 of 14, i.e. bits 41-43 of the register, outside the bitmap.

This is not specific to the DM41X. In the 29 DM41L fixtures, the low 20 bits of R vary independently of which keys are flagged:

| Fixture | R | Keys flagged |
| --- | --- | --- |
| `empty.dm41` | `00000000000000` | none |
| `empty-128.dm41` | `00000000006000` | none |
| `simple.dm41` | `00000000001000` | none |
| `helloworld.dm41` | `0000000000c000` | none |
| `twolabels.dm41` | `00000000077000` | none |
| DM41X `memorylost.d41` | `0000000000e000` | none |
| `global-key-assignments.dm41` | `00000010110999` | 11, 12 |
| `alarmtest.dm41` | `000000101a6821` | 11, 12 |

What those low bits encode is not known. They behave like state that varies independently of key assignments. (An earlier version of `docs/memory.md` listed the bitmask as `R[3:6]`, which would have put `e000` inside it; that table has since been corrected.)

### Other files

| File | Observation |
| --- | --- |
| `.b41` | Plain text, one line per sibling: `MODS memorylost.m41`, `RAM: memorylost.ram`, `STAT memorylost.d41`, `KEYS memorylost.cst`. |
| `.m41` | One line, `N PRINTER.mod`. The leading `N` marks a module that is in flash but not active; see `backup_set.md`. |
| `.cst` | One `X.` line per slot: `A.` to `P.`, then `1.`, `2.`, `3.` (the three special slots), all with empty assignments. Decoded from further samples in `cst.md`. |
| `.ram` | 40,960 bytes, all zero. This is 8 pages of 5,120 bytes if the packed page size matches `.mod` pages (inference); no information content in this sample. |
| `.mod` | `MOD1` header, sizes consistent with the plan (729-byte header plus 5,188 bytes per page). |
| `.bmp` (OFFIMG) | 400×240, 1-bit, as documented. |

### What these samples cannot show

They have empty extended memory, and all-zero register groups are omitted from the text format, so they say nothing about the 600-register XM map (second XM block at 0x301-0x3EF in the plan). A sample with XM files that spill into the third block is needed.

## Follow-ups

- Capture samples: ~~a `.d41` with XM files in the third block~~ (done: `dm41x_manyfiles.dm41`); ~~a `.cst` with real assignments~~ (done: `CSTtest.cst`); a `.ram` with a RAM-using module active; a second `.d41` taken with a different flag state.
- Run the `system_profiler SPUSBDataType` check from section 1.
- Test `SETIME`/`SETDATE` on the calculator to see whether they change the RTC shown in Settings.
- ~~Find out what the leading `N` in `.m41` means.~~ Done: see the addendum below and `backup_set.md`.

## Sources

- DM41X User Manual v1.34, 2026-09-24 (`DM41X/DM41X_Data/dm41x_user_manual.pdf`; [online](https://technical.swissmicros.com/dm41x/doc/dm41x_user_manual.html)): §2.1 specifications, §2.2 CPU speed, §2.7 battery, §3.4 persistent settings, §3.7 RTC correction, §6.2.5 Activate USB Disk, §6.4 Settings, §7.1 firmware update, ch. 8 troubleshooting.
- On-device `/HELP/41x.html` (DM41X Quick Reference).
- [DMCP firmware history](https://technical.swissmicros.com/dmcp/firmware/history.html) (v3.12, v3.19).
- [DB48x INSTALL.md](https://github.com/c3d/db48x/blob/stable/INSTALL.md) (DM42/DM32 disk-mode activation).
- [dmpy](https://github.com/fnordsh/dmpy) (USB-serial status).
- [DM42n review](https://magazinmehatronika.com/en/swissmicros-dm42n-review/) (power draw; different model).
- This repo: `docs/key_assignments.md`, `docs/flags.md`, `docs/memory.md`, `docs/dm41x_explorer_plan.md`, `src/memory/status_registers.py`, `src/tests/data/*.dm41`.

## Addendum (2026-10-04, later the same day)

Findings from the second round of samples: `src/tests/data/CSTtest.cst`, `backuptest.*` and `dm41x_manyfiles.dm41`. The detail is in `cst.md`, `backup_set.md` and, for the dump text format, `dump_format.md`.

### Corrections to the sections above

- **`.m41` flag.** The "unknown `N`" in the table is resolved. The DM41X manual (§4.5) describes each module as active (in the Active Modules list and flash) or non-active (flash only). In the samples `A` marks the two modules that were plugged in and `N` marks every inactive one, including `PRINTER.mod`, the Thermal Printer module pre-loaded in flash.
- **`.m41` paths.** Path does not follow the flag: two inactive modules carry a `/MODS/` path and `PRINTER.mod` is a bare name.
- **`.cst`.** The empty file in the table was not enough to decode the format; `CSTtest.cst` fills in the details. The three special slots are `1.` Shift-α, `2.` Shift-▲ and `3.` Shift-▼ (observed once). The calculator accepts any name as a command and only fails when the key is used.
- **XM map.** `dm41x_manyfiles.dm41` is a real DM41X dump. It loads with the `DM41X` profile and its XM files lie in all three regions. The "samples cannot show" paragraph under section 4 is therefore out of date for the XM map.

### Trailing spaces in dump files

The "trailing double space" noted in section 4 is **not specific to the DM41X**, and the calculator does not need it.

- Every register line ends in two spaces in these files: `dm41x_manyfiles.dm41`, `backuptest.d41`, `dm41x_pack_dotend.dm41`, `dm41x_pack_dotend-packed.dm41` and `fillextended.dm41`, and also in the DM41L fixtures `empty.dm41` and `empty-128.dm41`. Most other DM41L fixtures have none.
- `Memory.to_string()` writes no trailing spaces, so a file saved by DM41L_Explorer loses them. That does not explain every file: `lander.dm41` and `targ.dm41` have the calculator-style two-space gap between special-register pairs but no trailing spaces. The two-space gap, not the trailing spaces, is the reliable marker of calculator-style output. See `dump_format.md` for the full comparison; why those two files differ was not investigated.
- A copy of `dm41x_manyfiles.dm41` with all trailing whitespace removed by hand was loaded back into the DM41X without a problem. That is one file on one model. No equivalent test has been done on a DM41L.
- Consequence for the plan: the phase 2 round-trip gate compares dump files after stripping trailing whitespace from each line, and no code change to `to_string()` is needed. (`dump_format.md` records that `to_string()` also changes the two-space gaps between special-register pairs to one space, which should go into the same comparison.)
