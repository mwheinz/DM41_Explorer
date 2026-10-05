# DM41_Explorer Project Plan

2026-09-28 · Michael Heinz

Grow DM41_Explorer (formerly DM41L_Explorer) into one repository that builds three apps, DM41L_Explorer, DM41X_Explorer and DM41XN_Explorer, on a shared core, then port DM41X support to the SwissMicros web decoder (`~/Work/dm41decoder`). The immediate work is DM41X_Explorer: a desktop file manager for the DM41X's USB FAT disk.

## Goal and scope

**Decided:** the repository is **DM41_Explorer** (`~/Work/DM41_Explorer`, github.com/mwheinz/DM41_Explorer), and each model gets its own app named DM41$_Explorer, where $ is L, X or XN: **DM41L_Explorer**, **DM41X_Explorer** and **DM41XN_Explorer**.

The core difference is the transport. DM41L_Explorer talks to live memory over a serial console; the DM41X has no serial console and instead exposes 6 MB of internal flash as a FAT USB disk (File › Activate USB Disk). So DM41X_Explorer is a file manager: it opens either the mounted DM41X volume or any local folder holding a copy of its contents (like `DM41X/DM41X_Data`), shows one tab per special folder, and imports, exports and converts files.

**In scope**

- Browse, copy in/out, rename and delete files in each special folder.
- Decode and edit the formats that matter most: `.d41` state files, `.raw` programs, `.cst` custom keys.
- Convert programs between RAW, DAT, TXT (and PPC) using the code already in DM41L_Explorer.
- Read-only inspection of `.mod`, `.m41`, `.b41` and `.ram` files.
- Prepare OFF-images (any picture → 400×240 1-bit BMP) and preview screenshots.
- Safe-write habits for flash: minimal writes, backup-before-change, eject reminder.
- Port DM41X support to the web decoder: `.d41` and `.cst` reading and editing, and the 600-register XM map, through the decoder's existing pin-and-golden sync.

**Out of scope for v1**

- Firmware flashing (dm_tool / dfu-util already cover it).
- Building `.mod` files from ROM images.
- Live emulation or talking to the calculator while it is running.
- A FAT-disk file manager in the browser. The website works on files the user uploads; the desktop app stays the tool for managing the calculator's disk.

## Supported models

Three models share one HP-41CX memory model but differ in how they connect, so the code separates the **device profile** (memory map) from the **transport** (serial or disk).

| Model | App | USB transport | Extended memory | Status |
| --- | --- | --- | --- | --- |
| DM41L | DM41L_Explorer | Serial console | 1 module, 362 registers | Shipping today; supported by the web decoder |
| DM41X | DM41X_Explorer | FAT USB disk | 2 modules, 600 registers | The main subject of this plan |
| DM41XN (tentative name) | DM41XN_Explorer | FAT USB disk and serial | Assumed same as DM41X | Not released; protocol, memory map and file formats unconfirmed |

Until a DM41XN or its documentation is available, the plan assumes it is a DM41X plus a serial port. Nothing DM41XN-specific gets built on that assumption; the design only makes room for it.

## FAT disk and file formats

Eleven file types live in nine special folders; four are already understood, and `.d41` and `.cst` are the two that must be reverse-engineered before the tool is useful. Each format gets its own `docs/<format>.md`, in the same style as the DM41L docs (`alarms.md`, `key_assignments.md`).

| Folder | File type | What it holds | Format status | Priority |
| --- | --- | --- | --- | --- |
| `/STATE` | `.d41` | Full calculator state: main memory, XM, CPU registers | Known: the same text format as the DM41L `.dm41` dump (`dm41x_first_look_2026-10-04.md`) | 1 |
| `/PROG` | `.raw` | One program, same as GETP/SAVEP | Known (hp41uc; DM41L_Explorer import/export) | 1 |
| `/KEYS` | `.cst` | CST menu: 16 keys A–P plus Shift-▲, Shift-▼, Shift-α, each a command name | Decoded from samples: `cst.md` (limits and special characters still untested) | 1 |
| `/MODS` | `.mod` | Plug-in module ROMs | Known: public MOD1 format; samples confirm 729-byte header + 5,188 bytes per page | 2 |
| `/STATE` | `.m41` | Module list: filenames + active/inactive flag | Decoded from samples and the manual: `backup_set.md` | 2 |
| `/BACKUP` | `.b41` | Backup manifest naming its `.cst` `.d41` `.m41` `.ram` siblings | Decoded from samples and the manual: `backup_set.md` | 2 |
| `/RAM` | `.ram` | Up to 8 RAM Area pages used by modules | Partial: 40,960 bytes, all zero in both samples; layout with real contents unknown (`backup_set.md`) | 3 |
| `/OFFIMG` | `.bmp` | Images shown while off | Known: 400×240, 1-bit BMP | 2 |
| `/SCREENS` | `.bmp` | LCD screenshots | Known: BMP | 3 |
| `/HELP` | `.html` | Built-in help (`41x.html`) | Known: HTML | 3 |
| root | `param.cfg`, `rtccalib.cfg` | Settings export; RTC correction integer C (−511 to 512) | Partial | 3 |

As of 2026-10-04 the samples on hand are `/MODS` (20 files), `/OFFIMG` (25) and `/HELP` (4) in `DM41X/DM41X_Data`, plus in `src/tests/data` a full backup set (`backuptest.*`), `CSTtest.cst` and the real DM41X dump `dm41x_manyfiles.dm41`. Still missing: a `.ram` with real contents, `/PROG` and `/SCREENS` files, and a `.cst` that tests limits and special characters. One **Setup › Settings › Create Full Backup** on the calculator produces a `.b41`, `.cst`, `.d41`, `.m41` and `.ram` together, which is the fastest way to get the rest.

Reverse-engineering follows the method that worked for DM41L alarms and key assignments: change one thing on the calculator, save, diff the bytes, write it down, and confirm against a second sample.

## Architecture

**Decided: share one HP-41 core.** It has three users: DM41L_Explorer, DM41X_Explorer, and the SwissMicros web decoder (`~/Work/dm41decoder`), which is a TypeScript port of DM41L_Explorer's `memory/` package.

**How the web decoder uses the Python today**

- `reference/dm41l-explorer/` is a byte-identical copy of DM41L_Explorer, pinned to one commit (`55ed479`, 2026-09-11) in `tests/oracle/provenance.json`.
- `tests/oracle/generate.py` imports `memory.*` from that copy and writes expected-output (golden) files for 29 `.dm41` fixtures; `src/dm41/*.ts` must reproduce them exactly.
- A sync is deliberate: move the pin, regenerate the goldens, port the diff, and log any intended difference in `docs/dm41-divergences.md`.
- `program_text.py` is not ported; the site uses hp41uc compiled to WebAssembly instead.

So the Python core is the reference implementation and the website is its port. Every DM41X format feature lands in Python first, with fixtures and goldens, and then moves to `dm41decoder` through that sync as a planned phase of this project.

**Rules that keep the core portable** (taken from the decoder's divergence log)

- Pure and synchronous: bytes or text in, objects out. No file paths, tkinter or threads; `from_file`/`to_file` were left unported for exactly this reason.
- No shared mutable module-level objects; the port had to turn `ZERO_REGISTER`/`EOM_REGISTER` into functions.
- Exact, stable error messages; the TypeScript tests compare them word for word.
- Integer arithmetic for byte, address and time values; avoid output that depends on Python's float `repr` or `%g` rounding.
- Every new format ships with real sample files and a JSON dump view, so `generate.py` can produce goldens for it.

**Where the code lives: decided.** One repository, DM41_Explorer, holds the shared core, the transports, the shared GUI tabs and three thin apps. A suggested layout, to settle in phase 0:

| Directory | Holds | Used by |
| --- | --- | --- |
| `src/memory/` (the core) | Registers, regions, programs, XM, key assignments, alarms, device profiles, `.d41`/`.cst` formats | All three apps; vendored by the web decoder |
| `src/engine/` | Serial transport | DM41L_Explorer, DM41XN_Explorer |
| `src/fs/` | Disk transport | DM41X_Explorer, DM41XN_Explorer |
| `src/gui/` | Shared tabs and dialogs | All three apps |
| `src/apps/dm41l/`, `dm41x/`, `dm41xn/` | Each app's entry point, device profile choice, model-specific tabs, PyInstaller spec | One app each |

For the web decoder, the pinned copy becomes a copy of DM41_Explorer: `provenance.json`'s URL moves to the renamed repo, and `generate.py` keeps importing `memory.*` from `src/`. The app, GUI and transport directories can go on its `excluded_paths` list, since the site ports only the core.

**Transports.** Two transports cover all three models:

- **Serial**: DM41L_Explorer's `engine/` (`SerialManager`, `CommandEngine`, get/send memory dump). The web decoder already has a WebSerial port of it in `src/serial/`. If the DM41XN uses the DM41L's serial protocol, both reuse it unchanged.
- **Disk**: the new `fs/` layer, reading and writing the FAT volume or a local copy of it.

Each app is then a device profile plus its transports: DM41L_Explorer uses serial, DM41X_Explorer uses disk, and DM41XN_Explorer offers both for one calculator. Tabs that don't depend on the transport (Programs, Data Registers, XM Files, Key Assignments, Alarms, Flags, Hex View) live once in `src/gui/`.

**Memory map changes.** The DM41X is an HP-41CX with two extended memory modules, so XM grows from 362 to 600 registers. Today `constants.py` hard-codes `XM_REGIONS = [(0x40, 0xBF), (0x201, 0x2EF)]` and the display range stops at 0x2EF. The core needs:

- A device profile (DM41L: 1 module; DM41X: 2 modules) that adds the region 0x301–0x3EF and extends the address range to 0x3EF.
- XM directory and file-chain code checked for the jump across the second module boundary.
- Test fixtures with XM files that span all three XM blocks.

The web decoder mirrors this: `src/dm41/constants.ts` copies `XM_REGIONS`, and `src/dm41/memory.ts` indexes `XM_REGIONS[0]` and `XM_REGIONS[1]` directly. Make the Python version loop over any number of regions first, so the port is a straight translation.

**App layers** (desktop only; the website has its own UI in `src/ui/`). `engine/` stays as the serial transport.

- `fs/` — volume detection (macOS `/Volumes/...`, Windows drive letter, Linux mount), folder model, safe copy/write helpers.
- `formats/` — one reader/writer per file type, each with round-trip tests against real samples.
- `gui/` — customtkinter app shell and one tab per folder, reusing DM41L's `dialog_common`, `scroll_support`, Treeview lists and Help dialog.
- State-file views reuse DM41L's Overview, Programs, Data Registers, XM Files, Key Assignments, Alarms and Flags tabs, fed from a decoded `.d41` instead of a serial dump.

## Key assignments: ASN and CST

ASN and CST are separate systems stored in separate files, so the tool handles them in two editors with a shared keyboard view.

| | ASN (HP-41 key assignments) | CST (DM41X Custom menu) |
| --- | --- | --- |
| Where stored | Key-assignment registers in main memory, so inside the `.d41` state file | Separate `.cst` file in `/KEYS` (also written by Full Backup) |
| Slots | Any key, shifted or unshifted, in USER mode | 16 keys A–P plus Shift-▲, Shift-▼, Shift-α |
| Value | Function (prefix/postfix bytes) or global label | A command name as text: function, global label, or any label even if it does not exist |
| Format | Fully decoded for the DM41L (`docs/key_assignments.md`) | Unknown — reverse-engineer from `.cst` samples |
| Reuse | DM41L key-assignment tab and editor, pointed at a `.d41` | New editor |

**Plan**

- ASN: reuse the DM41L editor unchanged once `.d41` decoding works. Confirm the DM41X's `LKAOFF`/`LKAON` (local key assignments off/on) do not change the stored format.
- CST: a 4×4 A–P grid plus three special slots. Validate each name against the function table and the global labels in a chosen `.d41`, but warn rather than block, since CST accepts labels that do not exist yet.
- A combined keyboard view shows, per key, its ASN assignment and its CST letter, so both layers are visible at once.

## Program editor

Yes, include an editor, but as a later phase: tkinter can do everything on the list, and DM41L_Explorer already has the encoder that produces the errors to jump to.

- **Line numbers:** a narrow Canvas gutter beside a `tk.Text` widget, redrawn on scroll and edit. This is a well-known tkinter pattern, a few hundred lines.
- **Syntax colouring:** Text tags for labels, functions, numbers and strings, driven by the existing mnemonic tables and dialects.
- **Jump to error:** `program_text.decode_program_txt()` already rejects bad lines. Have it report a line number; the editor then calls `see()` on that line and tags it red.
- **Save targets:** `.raw` into `/PROG` (via the existing encoder), `.txt` locally, or straight into a program slot of a `.d41`.

**Shared with the website.** The two editors can't share UI code (tkinter vs. the site's React), but they can share a contract: compile errors are reported as line number plus message. The site's hp41uc WebAssembly compiler already tracks the source line (`source_line` in `Source/compile.c`), so a web editor could offer the same jump-to-error. Write the desktop editor's behaviour down as a short spec, so the website can follow it.

The hardest part is not tkinter but HP-41 characters (Σ, →, ≠, append-mark) and dialect choice on paste; the planned mnemonic-dialect work covers both, so do it before the editor.

## Web decoder port

The website gains DM41X support in two steps: first the domain code, then the UI. It follows the sync process `dm41decoder` already uses, so every ported line is checked against the Python.

**W1. Core port** (`src/dm41/`)

1. Tag a DM41_Explorer release. Re-vendor it into `reference/`, point `provenance.json` at the renamed repo, and add the app, GUI and transport directories to `excluded_paths`.
2. Add the new fixtures (`.d41`, `.cst`, 600-register XM dumps) to `tests/oracle/fixtures/` and regenerate every golden.
3. Port the device profile to `constants.ts`, and the generalised region handling to `memory.ts` and `regions.ts`.
4. Port the `.d41` and `.cst` readers and writers as new modules, pure and synchronous like the rest of `src/dm41/`.
5. Record any intended difference in `docs/dm41-divergences.md`; anything unrecorded counts as a bug.

**W2. Web UI** (`src/ui/`)

- Accept `.d41` and `.cst` uploads. `App.ts` already reads uploads as text or `ArrayBuffer`, so this is file-type dispatch, not new I/O.
- Make the dump panel and tabs device-aware; the panel is labelled "DM41L Memory Dump" today.
- Show the larger XM in Overview, XM Files and Hex View.
- Add a CST view to the Key Assignments tab, alongside ASN.
- Offer the edited file for download, with the same filename rules the calculator uses.

**DM41XN on the web.** The site already connects over WebSerial (`src/serial/webSerialTransport.ts`), so a DM41XN that speaks the DM41L protocol needs only its device profile there. Its disk side works through uploads, like the DM41X.

**Not ported:** the FAT-disk file manager (browsers can't see the mounted volume; uploads replace it) and, for now, the program editor UI. The editor's error contract is still written down in phase 5 so a web editor can follow later.

## Phases and milestones

Seven desktop phases, two web phases and a DM41XN phase that waits for hardware, each ending in a gate you can check on the real calculator or the website; phases 1 and 2 carry the risk, so they come before any GUI work. Sizes are relative (S/M/L), not dates.

| Phase | Deliverables | Exit gate | Size |
| --- | --- | --- | --- |
| 0. Repo layout and setup | Monorepo layout (core, transports, shared GUI, three app entry points); DM41L_Explorer moved to `src/apps/dm41l/` unchanged in behaviour; CI builds and tests each app on macOS, Windows, Linux | DM41L_Explorer passes its full suite and builds from the new layout; empty DM41X_Explorer launches | S |
| 1. Samples and format research | Sample set for every folder (Full Backup, RAW saves, CST variants, RAM pages, screenshots); `docs/` for `.d41`, `.cst`, `.m41`, `.b41`, `.ram`, `param.cfg` | `.d41` and `.cst` documented; every field explained by at least two samples | L |
| 2. Core library | Device profiles for DM41L and DM41X (600 XM registers) with N-region handling; transport interface separating serial from disk; `.d41` and `.cst` readers/writers following the portability rules; JSON dump views | Full suite passes; round trip on every sample (byte-identical for `.cst`, `.m41` and `.b41`; for dump-format `.d41`/`.dm41` files, identical after normalising whitespace: trailing spaces on each line and the gap between special-register pairs, see `dump_format.md`); DM41_Explorer release tagged | L |
| 3. DM41X_Explorer shell | Disk transport: volume detection, Open Folder, one Treeview tab per folder; copy in/out, rename, delete; backup-before-write; eject reminder | Files managed on the real DM41X with no stray macOS `._*` files left behind | M |
| 4. Format tabs | PROG (RAW↔TXT↔DAT↔PPC), STATE (shared views and edits on a `.d41`), KEYS (CST editor + keyboard view), MODS (`.mod` header info, `.m41` view), OFFIMG (image → 400×240 1-bit BMP), BACKUP (manifest view, trim for sharing) | Each file edited by the tool loads correctly on the calculator | L |
| 5. Program editor | Line-numbered editor in the shared GUI (so all three apps get it), syntax colouring, jump-to-error, save to `.raw` / `.txt` / `.d41`; short editor spec for the website | Program written in the editor runs on the calculator; a bad line opens at that line | M |
| 6. Release | Help menu, README with screenshots, PyInstaller builds of DM41L_Explorer and DM41X_Explorer (macOS `.app` first), GitHub release | Fresh install of each app on a second Mac works with its calculator | S |
| W1. Web core port | Re-vendor DM41_Explorer, new fixtures and goldens, port device profiles, regions, `.d41` and `.cst` to `src/dm41/` | `npm test` oracle suite passes on the new pin; divergences logged | M |
| W2. Web UI | `.d41`/`.cst` upload, device-aware panel and tabs, larger XM views, CST view, download of edited files | A `.d41` edited on the website loads on the calculator; `dist/` rebuilt and CI green | M |
| N. DM41XN_Explorer | Serial and disk samples from a real unit; DM41XN device profile; app combining both transports; same support on the web | Memory read and written over serial, and files managed over disk, on a real DM41XN, desktop and web | M (unknown until the protocol is known) |

**Dependencies**

- Phase 2 needs phase 1's `.d41` findings; phase 4's STATE and KEYS tabs need phase 2.
- Phase 5 needs the mnemonic-dialect work planned for DM41L_Explorer.
- PROG, MODS and OFFIMG tabs use known formats, so they can start right after phase 3 if research stalls.
- W1 starts once phase 2 is tagged and runs alongside phases 3–6; W2 needs W1. Neither blocks the desktop release.
- Phase N waits for a DM41XN unit or its documentation. It depends on phase 2's transport interface; if it arrives after phase 6, it ships as a point release.

## Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| `.d41` is binary and holds CPU/firmware state that is hard to decode | STATE tab and ASN editing slip | Decode only memory regions first; copy unknown bytes through untouched; check SwissMicros' online decoder for clues |
| Firmware updates change `.d41` or `.cst` layout | Files written by the tool fail to load | Record firmware version with every sample; refuse to write unknown versions without a warning |
| Writing a bad file corrupts calculator state | Lost programs and data | Always write a new file or a `.bak` copy; never overwrite a `.d41` in place by default |
| macOS writes `._*` and `.DS_Store` onto the FAT disk | Junk entries in calculator file lists; extra flash wear | Copy with metadata stripped; offer a Clean Volume command |
| Flash wear from many small writes | Shorter flash life | Batch changes; write once on Save, not on every edit |
| Unplugging without eject corrupts the FAT | Disk needs reformat | Eject button in the app and a reminder on every write |
| Scope creep from 20+ module formats and RAM pages | Core tabs never finish | `.mod`, `.m41`, `.ram` stay read-only in v1 |
| Core changes the TypeScript port can't mirror cheaply (float formatting, I/O, async) | Website falls behind; divergence log grows | Follow the portability rules; review each core PR with the question "how would this port?" |
| Moving the core breaks the decoder's pinned-copy workflow | Goldens can't be regenerated | Update `provenance.json` and `generate.py`'s import path in the same sync that first vendors DM41_Explorer, and point the verify URL at the renamed repo (GitHub redirects the old name, but only until something reuses it) |
| DM41XN differs from the assumptions (new serial protocol, different memory map or file formats) | Phase N grows; parts of phases 2–4 need rework | Keep transport and device profile behind interfaces now; write no DM41XN code until real samples exist; ask SwissMicros early for protocol notes |

## Open questions

- [ ] Should DM41X_Explorer also open DM41L `.dm41` dumps, making it a superset, or stay DM41X-only?
- [x] Is `.d41` a binary file, or the same text layout as a DM41L `.dm41` dump? **Resolved 2026-10-04:** same text layout; the existing loader reads it unchanged.
- [ ] Is CST stored inside `.d41` as well, or only in `.cst` files? Open: Create Full Backup writes `.cst` as its own file and nothing in the sample `.d41` has been identified as CST, but it has not been tested by changing only the CST and comparing two `.d41` files.
- [ ] Program editor: confirm it belongs in v1 (phase 5) or moves to v2.
- [ ] Which sample-generating sessions on the calculator are you willing to run (Full Backup, CST variants, RAM pages)?
- [ ] `dm41decoder` lives under the swissmicros GitHub organisation: will the port go in as pull requests for SwissMicros to review, or do you merge directly?
- [ ] Should the website's first DM41X release be read-only (view `.d41`/`.cst`), with editing in a later release?
- [ ] DM41XN: does its serial side use the DM41L's console protocol (get/send memory dump), and is its memory map and file set the same as the DM41X?
- [ ] DM41XN: can you get a pre-release unit or protocol notes from SwissMicros, and roughly when?
- [ ] Do the three apps release together from DM41_Explorer with one version number, or each on its own schedule?

## Sources

- `DM41X/notes.md` — project notes (9 points).
- `DM41X/DM41X_Data/dm41x_user_manual.pdf` — DM41X User Manual v1.34, 2026-09-24 ([online copy](https://technical.swissmicros.com/dm41x/doc/dm41x_user_manual.pdf)).
- `DM41X/DM41X_Data/` — sample `/MODS`, `/OFFIMG`, `/HELP` folders.
- `docs/dm41x_first_look_2026-10-04.md`, `docs/cst.md`, `docs/backup_set.md`, `docs/dump_format.md` — findings from the first hands-on samples; `src/tests/data/CSTtest.cst`, `backuptest.*` and `dm41x_manyfiles.dm41` are the samples.
- `DM41_Explorer/` (formerly DM41L_Explorer) — `src/memory/`, `src/gui/`, `docs/`, `README.md`.
- `dm41decoder/` — `README.md`, `reference/README.md`, `tests/oracle/provenance.json`, `tests/oracle/generate.py`, `docs/dm41-divergences.md`, `Source/compile.c`, `src/dm41/constants.ts`, `src/dm41/memory.ts`, `src/ui/shell/tabs.tsx`, `src/classes/App.ts`.
