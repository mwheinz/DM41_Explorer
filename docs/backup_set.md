# DM41X Backup Set: `.b41`, `.m41` and `.ram`

2026-10-04 · Michael Heinz (research by Claude)

This document describes three small files in the DM41X's Full Backup set: the `.b41` manifest, the `.m41` module list and the `.ram` RAM-page file. The other two files in a Full Backup are the `.cst` (see `cst.md`) and the `.d41`, which is the same text state format as a DM41L `.dm41` file (see `dm41x_first_look_2026-10-04.md`).

**Status.** `.b41` and `.m41` are decoded from samples and the manual. `.ram` is only partly understood: both samples are entirely zero.

## 1. Source Material

- **DM41X User Manual v1.34 (2026-09-24)**: §4.5 "Backup Module Lists", §4.6 "Activating Modules (plug-in)", §4.7.1 "Module Loader", §4.7.2 "RAM pages", §6.4.5 "Create Full Backup", §6.4.6 "Restore from Backup".
- **`src/tests/data/backuptest.*`**: a Full Backup taken on a real DM41X with five modules in flash, two of them plugged in. Taken twice on 2026-10-04; the second set, with three inactive modules, is the one in the repo.
- **`memorylost.*`** (on the calculator's `/BACKUP` folder, not in the repo): an earlier Full Backup, with one inactive module and none active.

## 2. The Full Backup Set

Setup › Settings › Create Full Backup creates five files in `/BACKUP`, all with the same name and different extensions:

| Extension | Content | Documented in |
| --- | --- | --- |
| `.b41` | Base backup file listing the others | section 3 |
| `.cst` | CST keys | `cst.md` |
| `.d41` | State file | `dm41x_first_look_2026-10-04.md` |
| `.m41` | Module lists | section 4 |
| `.ram` | RAM pages | section 5 |

Restore from Backup takes a `.b41`; every file it references must be in the same directory as the `.b41`. For sharing, unused files can be deleted together with their line in the `.b41`. When modules are loaded from the `.m41`, the `.mod` files are looked up in the `.b41`'s directory first (manual §6.4.6).

## 3. The `.b41` Manifest

Plain text, one line per member file:

```
MODS backuptest.m41
RAM: backuptest.ram
STAT backuptest.d41
KEYS backuptest.cst
```

| Property | Observation |
| --- | --- |
| Line endings | LF |
| Final line | Ends with a newline (80 bytes) |
| Line layout | `<kind> <filename>`, one space |
| Kinds | `MODS`, `RAM:`, `STAT`, `KEYS`, in that order |
| Filenames | Bare names, no directory; all share the backup's stem |

Note that `RAM:` is written with a colon and the other three kinds are not. This holds in both samples (`memorylost.b41` and `backuptest.b41`), so it looks like real format rather than a typing accident. The manual says unused files may be removed from the set together with their reference, but how a reader should treat a manifest with fewer than four lines has not been tested.

## 4. The `.m41` Module List

Plain text, one line per module, in this layout:

```
A /MODS/GAMES.MOD
A /MODS/PPC.MOD
N PRINTER.mod
N /MODS/Stat.mod
N /MODS/Advantage.mod
```

| Property | Observation |
| --- | --- |
| Line endings | LF |
| Final line | Ends with a newline (87 bytes) |
| Line layout | `<flag> <module file>`, one space |
| Flags | `A` or `N` |

### The flags

Per manual §4.5, each entry says whether the module is **active** (it is in both the Active Modules list and the flash module area) or **non-active** (stored in the flash module area only).

- **`A`** is an active module, one that is "plugged in" in the emulator.
- **`N`** is a non-active module: present in flash but not plugged in. Every inactive module in the samples carries `N`.

### Order

Active modules come first, then the non-active ones, which matches the manual's description of Save Flash and AM Lists ("current AM list followed by the remaining non-active modules in flash"). The order of the active entries probably matters, because the Module Loader assigns ROM pages to active modules in the order of the Active Modules list (manual §4.7.1). The order of the inactive entries (`Stat` before `Advantage`) is not alphabetical, and is probably flash order; that is an inference. A reader should keep the order as read.

### Paths

Entries are either a module file path under `/MODS/` or a bare filename. The flag does not decide which: `N /MODS/Stat.mod` and `N /MODS/Advantage.mod` carry a path, while `N PRINTER.mod` does not. A likely explanation is that `PRINTER.mod` is the Thermal Printer module pre-loaded in the calculator's flash (manual §4.2) and has no source file on the disk. That is not confirmed. The manual's rule for telling which modules are loaded is that the filename alone, compared without regard to case, identifies a module in flash (§4.4.1).

### Which save option produced the sample

The manual describes two save options: Save Active Modules List only (every entry marked active) and Save Flash and AM Lists (active entries followed by inactive ones). The Full Backup sample contains `N` entries, so it follows the second form. Whether the Full Backup uses exactly that option is not documented.

## 5. The `.ram` File

| Property | Observation |
| --- | --- |
| Size | 40,960 bytes in both samples (`memorylost.ram`, `backuptest.ram`) |
| Content | Every byte zero in both samples |

The manual (§4.7.2) says the system offers 8 RAM pages, "RAM Area", assigned to modules in the order they request them, and that Save RAM Pages saves the currently used pages. 40,960 is exactly 8 pages of 5,120 bytes, and 5,120 is what a 4,096-word page of 10-bit words occupies when packed (4,096 x 10 / 8). That is an inference from the sizes, not something the samples show; they contain no non-zero data. Both samples are all zero because neither backup had a module that uses RAM pages. The manual says the file's contents are tied to the particular configuration of modules that use RAM pages.

What a `.ram` file looks like with real contents, and whether a backup with unused pages is still 8 pages long, needs a sample taken with a RAM-using module active.

## 6. Open Questions

- What distinguishes bare filenames from `/MODS/` paths in `.m41` (section 4)? A test would be loading a module from outside `/MODS`, or a second pre-loaded module.
- Does an active module ever appear as a bare filename?
- How does Restore from Backup treat a `.b41` with fewer lines, or a `.m41` entry whose file is missing from flash and from disk?
- What does a `.ram` file contain when a RAM-using module is active?
- Do these formats change between firmware versions? The firmware version was not recorded with the samples.

## 7. Notes for an Implementation

Design notes only; no code has been written.

- Treat `.b41` and `.m41` as ordered lists of lines and keep the order and filenames exactly as read.
- Read `.ram` as an opaque block of bytes in whole pages until a non-zero sample shows how it is organised.
- The Full Backup writer must produce all four manifest kinds in the order shown and keep `RAM:` with its colon.
- The core must stay pure and synchronous so the web decoder can port it (see `dm41x_explorer_plan.md`).
