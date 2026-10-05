# CST Custom Menu Files (`.cst`)

2026-10-04 · Michael Heinz (research by Claude)

This document describes the `.cst` file the DM41X uses to save and load its CST custom-menu key assignments. CST is separate from the HP-41 ASN key assignments described in `key_assignments.md`: ASN lives in main memory (so inside a `.d41` state file), CST lives in its own file.

**Status.** The layout is decoded from two byte-identical samples plus one empty sample. Several details (limits, special characters, tolerance of malformed files) have not been tested; section 6 lists them. Anything not in the manual or a sample is marked as inference.

## 1. Source Material

- **DM41X User Manual v1.34 (2026-09-24)**, `DM41X/DM41X_Data/dm41x_user_manual.pdf`: §3.3 "CST - Custom Menu", §3.3.2 "CONF Screen", §6.4.5 "Create Full Backup".
- **`src/tests/data/CSTtest.cst`**: hand-made test file (91 bytes). Assignments were made on a real DM41X in the CONF screen and saved from there.
- **`src/tests/data/backuptest.cst`**: the `.cst` written by Setup › Settings › Create Full Backup with the same assignments loaded. It is byte-identical to `CSTtest.cst`.
- **`memorylost.cst`** (on the calculator's `/BACKUP` folder, not in the repo): the same layout with every slot empty. Recorded in `dm41x_first_look_2026-10-04.md`.

## 2. What CST Is

The CST key opens the Custom menu, so any assigned command takes two key presses (`[CST] [A]`). `[SHIFT][CST]` opens the CONF screen, where the assignments are edited and loaded or saved (default folder `/KEYS`).

There are 19 slots: 16 letter keys `A` to `P`, and three special keys, Shift-▲, Shift-▼ and Shift-α. Digits `1`-`6` on the CST screen itself are not assignable slots; they open fixed DM41X functions (Help, ROM Map, Load RAW, Save RAW, USB Disk, Flags).

A "command" is, per the manual:

- any function name (mainframe or module),
- any global program label (user or module), or
- any valid label name, **whether or not the destination exists**.

## 3. File Format

Plain text, one line per slot, always 19 lines in this order:

```
A.<command>
B.<command>
...
P.<command>
1.<command>
2.<command>
3.<command>
```

Observed properties (both `CSTtest.cst` and `backuptest.cst`):

| Property | Observation |
| --- | --- |
| Line endings | LF (no CR) |
| Final line | Ends with a newline |
| Size | 91 bytes |
| Header or comments | None |
| Empty slot | The label and the dot only, for example `C.` |
| Encoding of the samples | ASCII, upper case |
| Separator | A single `.` directly after the slot label, no space |

### Slot labels

| File label | Key | Evidence |
| --- | --- | --- |
| `A.` to `P.` | The letter keys A to P | Manual §3.3; matches the sample |
| `1.` | **Shift-α** | Observed: CLA was assigned to Shift-α and appears as `1.CLA` |
| `2.` | **Shift-▲** | Observed: CLX was assigned to Shift-▲ and appears as `2.CLX` |
| `3.` | **Shift-▼** | Observed: CLP was assigned to Shift-▼ and appears as `3.CLP` |

The file order for the special keys (α, ▲, ▼) is **not** the order the manual lists them in (▲, ▼, α). This comes from one test in which each of the three keys got a different command, so the mapping is unambiguous, but it is a single observation. The numeric file labels `1.`-`3.` are not the same thing as the `1`-`6` function keys on the CST screen; that is an inference from the manual, not something the samples show.

## 4. The Sample File

`CSTtest.cst` as saved:

| Slot | Command | Purpose of the test |
| --- | --- | --- |
| A | `XMBCD` | Assignment of a name |
| B | `XMALPHA` | Longest command in the samples (7 characters) |
| H | `BADFUNC` | A name that is not a function or label |
| P | `EMROOM` | A real extended-memory function |
| 1 (Shift-α) | `CLA` | Slot-mapping test |
| 2 (Shift-▲) | `CLX` | Slot-mapping test |
| 3 (Shift-▼) | `CLP` | Slot-mapping test |
| all others | empty | |

## 5. Validation Behaviour

The calculator does **not** check at assignment time that a command exists. Assigning `BADFUNC` to `H` raised no error and was saved; trying to use `H` afterwards raised an error message. This matches the manual's definition in section 2, which accepts any valid label name regardless of whether the destination exists.

Consequence for the tool: validating a command against the function table or a `.d41`'s global labels can only produce a **warning**, never a rejection. A name that is neither a function nor a known label is legal and is saved as it stands. The failure shows up when the key is used.

## 6. Open Questions

These need further samples from the calculator; none has been tested.

- **Maximum command length.** The longest name seen is 7 characters. Whether the editor or the file limits the length is unknown.
- **Case.** All samples are upper case. Whether lower case can be entered, and whether it is stored as typed, is unknown.
- **Special characters.** The manual says `[SHIFT]` enters special characters in the editor. How characters such as Σ, →, ≠ or the append mark are written to the file (UTF-8, a single byte in an HP-41 character set, or something else) is unknown.
- **Spaces, quotes and other punctuation** in a command: not tested.
- **Tolerance on load.** Not tested: a missing trailing newline, CRLF endings, missing lines, extra lines, or trailing whitespace. The related `.d41` dump format was shown to load without its trailing whitespace; see `dm41x_first_look_2026-10-04.md`.
- **Whether CST is also stored inside the `.d41` state file.** Nothing in `backuptest.d41` has been identified as CST data, and Create Full Backup writes the `.cst` as its own file, but the question has not been tested by changing only the CST and comparing two `.d41` files.
- **Firmware dependence.** Whether the format differs between firmware versions is unknown. The firmware version of the unit was not recorded with the samples.

## 7. Notes for an Implementation

Design notes only; no code has been written.

- The file is small and regular, so a reader and writer can round-trip it **byte for byte**: keep the 19 lines in file order, keep each command exactly as read, and write LF line endings with a final newline.
- Keep the file's own slot labels (`1.`-`3.`) in the data model and map them to Shift-α, Shift-▲ and Shift-▼ only in the UI, so the mapping lives in one place.
- Do not reject unknown command names (section 5). Offer a warning when a name matches neither a function nor a known global label.
- Keep unknown content (lines beyond the 19, or lower-case or non-ASCII text) as read rather than normalising it, until the questions in section 6 are answered.
- The core must stay pure and synchronous, with bytes or text in and objects out, and with exact, stable error messages, so the web decoder can port it (see the portability rules in `dm41x_explorer_plan.md`).
