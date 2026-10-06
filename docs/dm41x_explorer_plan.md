# DM41X Support Plan

2026-10-05 · Michael Heinz (drafted with Claude)

Replaces the 2026-09-28 file-manager-first plan (still in git history, last committed in `ad66d8d`). That plan started from the DM41X's FAT disk. This one starts from the memory module, because everything else depends on it.

## Goal

1. Extend the memory module so it handles everything a DM41X can put in a state file: the larger XM (already done), the XROM instructions the DM41X adds (its own module, plus X<I>Y and TRNG), and any DM41X state that turns out to live in `.d41` files.
2. Make every existing DM41L_Explorer feature work on a `.d41` state file.
3. Port the result to `~/Work/dm41decoder`, web UI included, as pull requests to SwissMicros.

## Decisions (2026-10-05)

| Topic | Decision |
| --- | --- |
| Models | DM41X and DM41XN are treated identically. The extra XROMs are the same on both. |
| Model detection | None. A `.d41` and a `.dm41` are the same text format, and nothing in the file says which model wrote it except the extension. No auto-detect, no manual override, no model selector in the web app. |
| Protecting the DM41L | The check runs only when **uploading** a state/dump to a DM41L (Send Dump). XM data that does not fit a DM41L is an **error**. Any XROM the DM41L does not have built in (the DM41X's additions, or an XROM from another module) is a **warning**, because the user may mean to edit the program on the DM41L and replace it. Opening and saving files, including large states, is never restricted. |
| Revisit | If LKAOFF or FAST turn out to change what is stored in a state file, model detection and the checks above get revisited. |
| Round trip | `.d41` writes only have to match the calculator's own output after normalising whitespace (trailing spaces, the two-space gap between special-register pairs). |
| XROM encodings | Look in existing sources first; use calculator test programs only for what they do not cover. |
| XROM coverage | **First release:** only the XROMs built into the two calculators. The DM41L has just the original HP-41CX modules (Extended Functions/Memory and Time) and cannot load others. The DM41X has those, plus the DM41X module, plus extensions to the CX modules (X<I>Y, TRNG), and can also load ROM modules. Support for additional ROM modules is a **later release** (see the section near the end). |
| Delivery | Change and test locally, then open pull requests to `dm41decoder`. The first web release has no model selector but must be able to **save modified state files**. |
| License | Possibly moving DM41_Explorer to GPLv3. No existing license obliges us to push changes back to hp41uc. |

## Where things stand

Checked against the repository on 2026-10-05.

**Already done**

- `memory/device_profile.py` has a `DM41X` profile with three XM regions (0x40–0xBF, 0x201–0x2EF, 0x301–0x3EF). `Memory.from_string(text, profile=DM41X)` loads `dm41x_manyfiles.dm41`, a real dump whose files span all three regions (`tests/test_xm_three_regions.py`).
- `tests/data/dm41xn.dm41` (a DM41XN state file saved as `.d41` and renamed `.dm41` so DM41L_Explorer will open it: 331 bytes, header `DM41`) loads unchanged with both the DM41L and DM41X profiles and holds one program, `LBL "AAA"`, X<I>Y, TRNG, END. `dm41xn.txt` is its text listing, `XROM 25,63` and `XROM 26,36`. In a scratch copy, adding all 18 names to `XROM_FUNCTIONS` builds the mnemonic registry without collisions, and `X<I>Y` / `TRNG` as names compile to exactly the bytes in the dump.
- `memory/mnemonics.py` is the single registry of instruction spellings. It generates the character variants itself, so hp41uc's `SREG?` and `X#NN?` already resolve to the same XROMs as `ΣREG?` and `X≠NN?` (also `SIGMAREG?`, `X!=NN?`, `X<>NN?`). The canonical (export) name of every existing XROM already equals hp41uc's. **The Σ/≠ spellings need no change.**

**Gaps found**

1. **The DM41X's XROM additions are missing from `functions.py`.** It has the 95 functions of the two HP-41CX modules (25 and 26), and all 95 agree with hp41uc's names and codes. Missing are 17 functions in module 26 that hp41uc's table (`dm41decoder/Source/hp41ucg.h`) and the DM41X manual (§3.8.5) both list:

   | XROM | Functions |
   | --- | --- |
   | 26,36 | TRNG (an extension of the CX Time module) |
   | 26,38–26,53 | ABSP, AINT, ASWAP, CLAC, CLEM, FAST, FILL, FLCOPY, FLHD, FLTYPE, LKAOFF, LKAON, RENMFL, RETPFL, SLOW, WORKFL (the DM41X module) |

   Because both tables already exist, the 16 DM41X-module functions need no reverse engineering, only a sample to confirm them (S5). **Confirmed 2026-10-05** from a real DM41XN dump (`tests/data/dm41xn.dm41` and `dm41xn.txt`, one program calling both): **X<I>Y is XROM 25,63** (bytes `A6 7F`) and **TRNG is XROM 26,36** (`A6 A4`), matching hp41uc for TRNG. hp41uc has no entry for X<I>Y, so one has to be added there too. **CRT?** (stealth header, XROM 25,00) is documented but still needs a sample.
2. **Nothing stops an oversized state from being sent to a DM41L.** Nothing in the code checks a dump against a profile before upload, so a DM41X state with a third XM region would go out as-is. (Loading and saving such a file is fine and stays unrestricted; `Memory.from_string(text, profile=DM41L)` keeps the third-region registers and writes them back out.)
3. **XM file types.** `XMFile` only accepts program, data and ASCII headers and raises on anything else. RETPFL can set types 1–15, so a retyped file may make `list_files()` fail.
4. **Overview totals are DM41L-only.** `gui/overview_tab.py` derives its free-XM number from constants calibrated on a real DM41L (362 registers by EMDIR). The DM41X value is untested.
5. **The web decoder is two XM regions only.** `src/dm41/constants.ts` hard-codes two regions, and uploads accept only `.txt`, `.dm41` and `.raw`.

## Phase 0 · Calculator samples (you)

These produce the evidence for phases 1–3. Save each into `src/tests/data/` with the name shown. The calculator writes `.d41`; until phase 3 step 1 teaches DM41L_Explorer that extension, copy each state file in with the same name and **`.dm41`** instead (as was done for `dm41xn.dm41`), and the tests refer to the renamed file. The content is identical.

| # | Sample | Answers |
| --- | --- | --- |
| S1 | `dm41x_base.d41`: a state saved from a known starting point (for example right after CLEM), with nothing else changed | Baseline for the diffs below |
| S2 | `dm41x_lkaoff.d41`: S1 plus LKAOFF, saved. Also `dm41x_lkaon.d41` after LKAON | Does LKAOFF change the state file? |
| S3 | `dm41x_fast.d41`, `dm41x_slow.d41`: S1 saved after FAST, then after SLOW | Does the speed setting change the state file? |
| S4 | `dm41x_retpfl_before.d41`, `dm41x_retpfl_after.d41`: one XM file saved before and after RETPFL (try a program file retyped as data, and a type above 3) | File type codes 4–15, and whether the header changes |
| S5 | `dm41x_xroms.d41` and `dm41x_xroms.raw`: one short program using the 16 DM41X-module functions (ABSP … WORKFL) and CRT?, saved as a state and exported as RAW. (X<I>Y and TRNG are already confirmed by `dm41xn.dm41`.) | Confirms 26,38–53, and shows whether CRT? is executable and its code |
| S6 | `dm41x_xrom_keys.d41`: LKAOFF and one more new function assigned to keys | Key-assignment encoding of the new XROMs |
| S7 | `dm41x_settings.d41`: S1 resaved with DMY/MDY, CLK24 and flag 26 changed, one at a time | What the manual's "stored in `.d41`" settings look like |
| S8 | `dm41x_emroom.txt`: the EMROOM and EMDIR readings on a freshly cleared XM (CLEM), and with one known file | The DM41X value for the Overview tab's free-XM number |

**Results so far (2026-10-05).** S1–S3 arrived as `dm41x_base.d41`, `dm41x_lkaoff.d41`, `dm41x_lkan.d41` (LKAON), `dm41x_fast.d41` and `dm41x_slow.d41`. All five, and `dm41xn.dm41`, load with the DM41X profile and round-trip identically after normalising whitespace. Compared with each other:

| Difference | Where | Reading |
| --- | --- | --- |
| Name of the last function run | Register Q (0x09), ASCII, last character first: `LKAOFF`, `LKAON`, `FAST`, `SLOW`, and `TRNG` in `dm41xn.dm41`. Register P (0x08) also holds a stray `4B` in all six files and is zero in the base | Side effect of running a function; docs/memory.md lists Q as scratch |
| Low bytes of register e (0x0f) | `0x7000` after LKAOFF and in `dm41xn`, `0x016000` after FAST and after SLOW, zero after LKAON and in the base | Outside the 36 key-flag bits (all key flags are clear in every file). docs/memory.md puts the line number and a scratch byte there. Not understood |
| Flag 50 | Set in the base only; flag 52 set in `dm41xn` only | Incidental display and program-mode state |
| CPU registers C and S | The base differs from the other four; `dm41xn` differs again in C | Incidental, as in the first-look notes |

What it says about the open questions:

- **FAST vs SLOW:** the two files differ only in register Q's name. The speed setting is **not** stored in the state file.
- **LKAOFF vs LKAON:** they differ in Q and in e. Whether e carries the LKAOFF setting or just a line number or scratch value is **not settled**: FAST and SLOW also set e, and `dm41xn` has the same e as LKAOFF after TRNG.
- **Model detection:** nothing here distinguishes a DM41XN file from a DM41X one, so that decision stands. Nothing in S1–S3 forces the "revisit" clause.
- **ASN key flags:** LKAOFF and LKAON do not touch the key-assignment bitmaps in R and e.

To settle LKAOFF (and confirm FAST), two **load tests** on the calculator are cleaner than more diffs:

| # | Test | Answers |
| --- | --- | --- |
| S2b | Run LKAOFF and save a state. Run LKAON. Load the saved state. Does a local label key assignment (top two rows, USER mode) behave as off again? | Whether the state file carries LKAOFF at all |
| S3b | Run SLOW and save a state. Run FAST. Load the saved state. Is the calculator slow again? | Confirms speed is not in the state file. **Done: no, it reverts to FAST on load.** |

**S2b result (Mike, 2026-10-05): the state file carries LKAOFF.** With `lkaoff.d41` and `lkaon.d41` (each holds the test program `LKATST`, with `LBL A` and `LBL B`), loading one or the other repeatedly changed how the upper-left key behaved. So the "revisit" clause in the decisions table fires in a limited way: a state saved with LKAOFF behaves differently on a DM41L. It does not change the decision to have no detection, but Phase 3 must round-trip the bits and the docs must say they exist.

**Explanation (Mike, 2026-10-05, confirmed by the files).** Both files contain the global label `LKATST` with key byte 1 (key 11, the upper-left key) in its header, so it is *assigned* to key 11 in both. LBL A is also in the program. The only R difference is bit 35, the KEYFLAGS bit for key 11 unshifted: set in `lkaon.d41`, clear in `lkaoff.d41`. That is the whole effect:

- LKAON: the flag is set, the OS finds the global-label assignment and runs `LKATST` when key 11 is pressed.
- LKAOFF: the flag is cleared, so the OS treats key 11 as unassigned and falls back to the auto-assigned local label (`LBL A`). The assignment itself is untouched.
- Key 12 (`LBL B`) has no manual assignment, so it runs `LBL B` either way and its flag is never set. This is also why `lkaoff.d41` and `lkaon.d41` otherwise differ only in key 11.

So LKAOFF is stored as **cleared KEYFLAGS bits for the top two rows of keys** (keys 11–15 and 21–25: bits 35, 27, 19, 11, 3 and 34, 26, 18, 10, 2, in R for unshifted and e for shifted), while the assignments themselves remain. No separate LKAOFF bit exists in R or e: S2h shows the low nibbles of e that move with LKAOFF also move with FAST and SLOW, so they are not LKAOFF's. Q is scratch.

Consequences for the Explorer:

- A state is **LKAOFF-like** when a top-two-row key has an assignment (a Key Assignment Register entry or a global label's key byte) but its KEYFLAGS bit is clear. The Key Assignments tab should show the assignment anyway and flag the mismatch (for example "assigned, but the key flag is clear: local key assignments are off"), instead of treating it as corruption. Today `list_assignments()` reads only Key Assignment Registers, so it shows neither file's key-11 assignment, and global-label assignments are reached through the program chain.
- The Explorer only sets or clears one key's flag when that key's assignment changes (`set_key_flag` via `set_assignment`/`delete_assignment` and the global-label paths in `program_memory.py`). It never recomputes flags from the assignments, so saving an edited LKAOFF state keeps it LKAOFF. Keep it that way, and add a test.
- ASN on a top-two-row key while LKAOFF is active sets that key's flag (S2f, below). The Explorer does the same when it creates an assignment, so it matches the calculator.

| # | Test | Answers |
| --- | --- | --- |
| S2c | From `lkaon.d41`, run LKAOFF and save; then LKAON and save, with no program runs or key presses in between. | The clean toggle: only Q and the key-11 flag should differ. **Done, see below.** |
| S2h | From `lkaon2.d41`, run FAST and save; then run SLOW and save. | Whether FAST/SLOW alone move the e nibbles while the key-11 flag stays set. **Done: yes, see below.** |
| S2f | Run LKAOFF. Then ASN a function to a top-two-row key, press it, and save. | Whether ASN under LKAOFF sets the flag. **Done, see below.** |
| S2g | A global label assigned to a shifted top-two-row key; save with LKAON, then LKAOFF and save. | Whether the shifted flags in e clear the same way. **Done, see below.** |

**S2c result (Mike, 2026-10-05).** `lkaoff2.d41` (loaded `lkaon.d41`, ran LKAOFF) and `lkaon2.d41` (then ran LKAON). Both round-trip identically after normalising whitespace.

| Comparison | Differences |
| --- | --- |
| `lkaoff.d41` vs `lkaoff2.d41` | **None.** Byte for byte identical, although the first was saved after pressing keys 11 and 12 and the second was not. |
| `lkaon.d41` vs `lkaoff2.d41` | Q, R (bit 35, key 11 unshifted: set → clear), e (`00000000000fff` → `00000000016fff`) |
| `lkaoff2.d41` vs `lkaon2.d41` | Q, R (bit 35 restored), e (back to `00000000000fff`) |
| `lkaon.d41` vs `lkaon2.d41` | Q only |

So:

- Toggling LKAOFF then LKAON restores R and e exactly, and the only trace left is Q. The state is fully reversible, and key presses leave no trace beyond Q (so S2e is not needed and has been dropped).
- The key-11 flag is cleared by LKAOFF and restored by LKAON. The restoring must come from the assignment data (the global label's key byte), since LKAOFF does not remove it.
- In this clean test, e also toggles with LKA: `000fff` (on) ⇄ `016fff` (off). Earlier pairs fit the same pattern (`dm41x_lkan` `000000` vs `dm41x_lkaoff` `007000`). But after a reset FAST and SLOW also give `016000` with LKA at its default, so those nibbles are not a pure LKAOFF flag. S2h (below) settles it: they are not an LKAOFF bit at all.
- Q is not always the plain ASCII name of the last function: in `lkaon2.d41` it is `07014c4b41c19b`, not `LKAON`. Treat Q as opaque scratch.

**S2f result (Mike, 2026-10-05).** ASN on a top-two-row key while LKAOFF is active sets that key's flag, so the new assignment works. Switching to LKAON left it unchanged. Running LKAOFF again hid it. That is consistent with the model: LKAOFF clears the flags of all ten top-two-row keys, LKAON sets the flag of every key that has an assignment. Mike's view is that the ASN-under-LKAOFF behaviour may be an emulator bug and is not relevant to the Explorer, and the Explorer already behaves the same way.

**S2g result.** `lkaoff3.d41` and `lkaon3.d41` have the global label `LKATST` on key 11 (key byte 1) and the new global label `BBB` assigned to shifted key 12 (key byte 25, which the Explorer decodes as key 12 shifted). Both round-trip identically after normalising whitespace.

| File | Key flags set | R | e |
| --- | --- | --- | --- |
| `lkaon3.d41` | key 11 unshifted, key 12 shifted | `0000000010e000` | `00000010004000` |
| `lkaoff3.d41` | none | `0000000000e000` | `00000000016fff` |

- LKAOFF clears the shifted flag in e exactly as it clears the unshifted one in R, and both assignments stay in the program chain (`key_assignment` 1 and 25 in both files). No Key Assignment Register entries are involved.
- The low nibbles of e vary with history in LKAON states (`000fff` in `lkaon.d41`, `004000` here) but are the same `016fff` in every LKAOFF state with a program present. See S2h below.

**S2h result (Mike, 2026-10-05).** `lkaon2_fast.d41` (FAST run from `lkaon2.d41`) and `lkaon2_slow.d41` (then SLOW). All round-trip identically after normalising whitespace.

| File | Key flags set | R | e | Q |
| --- | --- | --- | --- | --- |
| `lkaon2.d41` | key 11 unshifted | `0000000010e000` | `00000000000fff` | `07014c4b41c19b` |
| `lkaon2_fast.d41` | key 11 unshifted | `0000000010e000` | `00000000016fff` | `FAST` |
| `lkaon2_slow.d41` | key 11 unshifted | `0000000010e000` | `00000000016fff` | `SLOW` |

FAST and SLOW move the low nibbles of e to `016fff` while LKAON stays in force and the key-11 flag stays set. So those nibbles are **not** the LKAOFF setting. They change when a DM41X-module function runs (LKAOFF, FAST, SLOW), and LKAON puts them back, so they look like per-function scratch. The final model: **LKAOFF is represented only by the cleared top-two-row key flags in R and e; nothing else in the state file records it.** The Explorer treats the low nibbles of e, and Q, as opaque scratch and round-trips them byte for byte. A state saved with LKAOFF on but no assignments on the top two rows cannot be distinguished from LKAON, which is harmless because the flags are all that the calculator uses.

**S3b result (Mike, 2026-10-05): FAST/SLOW is not saved in the state file.** After SLOW, saving and reloading the state, the calculator ran at full speed; Mike confirmed that it reverts to FAST mode whenever a state file is loaded. S3 and S3b together settle it: the speed setting is not part of the state, and loading a state always gives FAST.

**S3 repeated (Mike, 2026-10-05):** after a reset, `fast.d41` (FAST) and `slow.d41` (SLOW after that) are byte-for-byte identical to `dm41x_fast.d41` and `dm41x_slow.d41`, so the S3 result reproduces exactly. They differ only in Q (`FAST` vs `SLOW`). Register e is `016000` in both, but `000000` after a reset with LKAON and `007000` after LKAOFF, so e's low nibbles also move with FAST/SLOW and are not a pure LKAOFF flag.


**S4 results (2026-10-05).** `dm41x_retpfl_before.d41` and `dm41x_retpfl_after.d41` are `manyfiles` variants: 22 XM files, ten of them 8-register data files XM0–XM9. In the after file XM0 is type 4, XM1 type 5 and XM2 type 6. Both load with the DM41X profile and round-trip identically after normalising whitespace. Findings:

- RETPFL changes only the type nibble of the header: `20aa…`→`60aa…` (XM2, header 0xaa), `20b4…`→`50b4…` (XM1, 0xb4), `20be…`→`40be…` (XM0, 0xbe). AAA, the reserved zeros, RRR/SSS (`0000008008`), the name register and the data registers are untouched. Types 4–6 keep the Data header layout.
- The region 0 pointer register 0x040 also changed: `000160162ef0bf` (WW = 0x16, PPP = 0x016, 22 files) before, `000030022ef0bf` (WW = 3, PPP = 2) after. All 22 files are still present, so the doc's "PPP in region 0 = number of files" does not hold after a RETPFL. S4b (below) shows RETPFL itself disturbs these fields.
- `ExtendedMemory.list_files()` raises `DM41MemoryError` ("Detected invalid XM file header. 0xbe") on the after file. The error is all-or-nothing, so one retyped file hides the whole XM directory.
- The stack, Alpha, Q and e registers also differ between the two files. These are incidental calculator state.
- The calculator labels program files `P`, data files `D` and ASCII files `A`, and every type above 3 `@` (S4c, answered by Mike). The Explorer should show the same `@` label for those files, with the numeric type alongside.

**S4b results.** `dm41x_s4b1.d41` is the before state with only XM0 retyped to 4. `dm41x_s4b2.d41` is the same file after retyping XM0 back to 2. In `s4b2` the header at 0xbe is `20be…` again and all 22 files list normally. Register 0x040 across the whole S4 series:

| File | XM0 type | WW | PPP |
| --- | --- | --- | --- |
| `dm41x_retpfl_before` | 2 | 0x16 | 0x016 |
| `dm41x_s4b1` | 4 | 0x01 | 0x016 |
| `dm41x_s4b2` | 2 | 0x07 | 0x001 |
| `dm41x_retpfl_after` (XM0, XM1, XM2 → 4, 5, 6) | 4 | 0x03 | 0x002 |

So RETPFL does disturb WW and PPP, and retyping a file back does not restore them. The file count did not change (22 each time), so neither field is a file count after a RETPFL. Treat both as opaque calculator working values: round-trip them byte for byte, never recompute them from the file list, and soften the "PPP = number of files" sentence in `docs/extended_memory.md`. The Q register's last byte also tracks the type just passed to RETPFL (04, 02, 06), which is consistent with the X value at the time.

**Exit gate:** each sample loads with `Memory.from_string(text, profile=DM41X)`, and the S2/S3 diffs are written down (identical, or the bytes that move).

## Phase 1 · XROM table

Scope is the first-release decision: the DM41L's 95 plus the DM41X's additions. Nothing from other modules.

- Add the 18 entries (the 17 plus X<I>Y; CRT? only if S5 shows it executes) to `memory/functions.py`. X<I>Y and TRNG are already confirmed by `dm41xn.dm41`; the other 16 get confirmed by S5 before they merge. `mnemonics.py` builds its registry from that table, so import, aliases, "did you mean" and the key-assignment name list follow automatically. At this size, with names and codes also printed in the DM41X manual, it is small enough to type by hand and check against hp41uc; no bulk import of hp41uc's table is needed.
- Give `DeviceProfile` a way to say which XROMs a model has built in: the DM41L's 95, and the DM41X's 95 plus the additions. Phase 2 needs this. Files open with the DM41X profile everywhere (phase 3), so the registry always includes the additions.
- Mark the additions as DM41X-only in the Key Assignments dropdown and in the generated mnemonic reference, grouped under their hp41uc section headers ("-DM 41X-").
- Regenerate `docs/function_table.md` and the mnemonic reference (`mnemonic_doc.py`), and update `docs/mnemonics.md`.
- **Tests.** `dm41xn.txt` written with names (`X<I>Y`, `TRNG`) compiles to the bytes in `dm41xn.dm41` and decompiles to `XROM 25,63 ;X<I>Y` and `XROM 26,36 ;TRNG` (already checked in a scratch copy; this makes it permanent). The 16 DM41X-module functions compile to the exact bytes in `dm41x_xroms.raw` (S5) and decompile back; every new name resolves by canonical, case and space variants; a key assignment of a new XROM round-trips (S6); a one-off diff of modules 25 and 26 against `hp41ucg.h` shows agreement for every function except the new X<I>Y.

**Exit gate:** the full suite passes, the S5 program compiles to the exact RAW bytes, and decompiling them gives hp41uc's canonical names.

## Phase 2 · Protecting the DM41L

- New function in the core, `check_profile_fit(memory, profile)`, returning findings, each with a level (`error`, `warning`), a short message and a location. Pure and synchronous, like the rest of the core.
  - **Error:** any XM data in a region the target profile does not have (for the DM41L, anything from 0x301). Also a `.d41` with more XM than the DM41L can load.
  - **Warning:** any XROM the DM41L does not have built in, found in a program or key assignment: the DM41X's additions (26,36, 26,38–26,53, X<I>Y) and any XROM outside modules 25 and 26, since a DM41L cannot load other modules. List the program and step (via `opcode_scan.iter_instructions`) or the key.
- Wire it into DM41L_Explorer's **Send Dump** only: an error blocks the upload and says why; warnings show a confirmation that lists what will not run on a DM41L. Open and Save do no checking.
- Tests use `dm41x_manyfiles.dm41` (error) and `dm41x_xroms.d41` (warning, program and key assignment), plus a DM41L fixture that must produce no findings.

**Exit gate:** sending `dm41x_manyfiles.dm41` to a DM41L is refused with a clear message; sending `dm41x_xroms.d41` lists the programs affected and proceeds on confirmation.

## Phase 3 · State files in the desktop app

Make the existing tabs work, in this order, on `dm41x_manyfiles.dm41` and the S-samples. Each tab gets a test that fails if it still assumes two regions or 0x2EF.

1. Open/Save use `.d41` as well as `.dm41` (until then, `.d41` samples are renamed to `.dm41` by hand, and nothing else about them changes) and keep the file's own extension on Save. Files open with the DM41X profile in every app, since it is a superset of the DM41L's, so a large state opens, displays and saves in DM41L_Explorer without loss. A dump read from a live DM41L over serial still uses the DM41L profile. The phase 2 check applies only at upload.
2. **XM Files** and **Hex View**: walk three regions and show addresses up to 0x3EF (`profile.display_end`).
3. **Overview**: free/total XM from the profile instead of the DM41L constants; DM41X numbers confirmed by S8.
4. **XM file types**: decide from S4 what `XMFile` does with types 4–15 (show them as `@` (the calculator's label) with the numeric type, keep the data untouched, never raise from `list_files()`).
5. **Programs**, **Data Registers**, **Alarms**, **Key Assignments**, **Flags**: smoke-test each on a DM41X dump (flag 31 shows as DMY in Flags). Fix whatever fails.
6. **LKAOFF/FAST**: FAST/SLOW are not in the state file, and the calculator reverts to FAST when a state is loaded (S3 and S3b). LKAOFF is: it shows as cleared KEYFLAGS bits for the top two rows of keys while their assignments remain. Show that state in the Key Assignments tab (and document it in `docs/key_assignments.md`), keep the flags untouched on save. S2c–S2h are done, and show nothing else in the state records LKAOFF. Treat register e's low nibbles and the P/Q scratch registers as opaque and round-trip them byte for byte.
7. Round-trip gate: every `.d41` and `.dm41` sample loads and saves identically after normalising whitespace (`dump_format.md` lists the two normalisations).

## Phase 4 · License and tag

- Decide on GPLv3 (still open). If yes: replace `LICENSE` (currently BSD-style), update `pyproject.toml`, `README.md`, `CONTRIBUTING.md` and the file headers, and say so in the release notes. As the sole author you can relicense on your own; any outside contributor's commit would need their agreement.
- Tag a DM41_Explorer release once phases 1–3 pass. The web port pins to that tag.

## Phase 5 · Port to dm41decoder

Follow the decoder's existing sync procedure (`reference/README.md`, `tests/oracle/provenance.json`, `docs/dm41-divergences.md`). It is **vendored, not a submodule**: there is no `.gitmodules`, and `reference/dm41l-explorer/` is a byte-identical copy that `tests/oracle/generate.py` imports to build the golden files. It is pinned to commit `55ed479` (2026-09-11); this repository has moved well beyond it. Whether to keep that arrangement is a question for SwissMicros, and it does not block the port.

**W1 · Core**

1. Re-vendor at the tagged release, update `provenance.json`, and exclude the GUI and serial engine.
2. Add the new fixtures (`dm41x_manyfiles.dm41`, the S-samples) to `tests/oracle/fixtures/` and regenerate every golden.
3. Port `device_profile.py` to TypeScript. `constants.ts` and `memory.ts` currently hard-code two regions (`XM_REGIONS[0]`, `[1]`).
4. Add the 17 DM41X XROMs, X<I>Y (and CRT? once confirmed) to `functions.ts`.
5. Port `check_profile_fit`.
6. `mnemonics.py`, `mnemonic_dialects.py` and `program_text.py` are not ported: the site compiles and decompiles with hp41uc as WebAssembly. Add divergence rows for them.
7. **hp41uc needs one small C change.** TRNG and the 16 DM41X-module functions are already in `hp41ucg.h` and in the compiled `hp41uc.wasm` (confirm by compiling `dm41x_xroms.txt` through the WASM), but **X<I>Y (25,63) is not**: add it to `hp41ucg.h` as a small, separate pull request (GPL, no obligation upstream), and CRT? too if S5 shows it executes.

**W2 · Web UI**

- Accept `.d41` uploads (`fileDispatch.ts` accepts only `.txt`, `.dm41`, `.raw` today).
- **Save modified state files**: the shell's Download writes the edited dump with the right extension.
- XM views (Overview, XM Files, Hex View) use the profile's regions.
- Write-to-calculator (WebSerial) runs the phase 2 check before sending.
- No model selector and no DM41X-specific tabs in the first release.

**Exit gate:** `npm test` passes on the new pin, a `.d41` uploaded, edited and downloaded loads on the DM41X, and `dist/` is rebuilt.

## Later release: additional ROM modules (not in this plan)

The DM41X can load ROM modules, so a later release will need XROMs beyond the built-in ones. What was found so far, so it is not lost:

- **hp41uc covers HP's own modules:** 411 functions in 11 modules (17, 18, 22–30). Loaded into the mnemonic registry as they stand, eight names collide: `SST` (22,52 vs the built-in SST), `DDL`, `DDT`, `LAD`, `TAD`, `UNL`, `UNT` (modules 22 and 23) and `FLTYPE` (23,04 vs the DM41X's 26,47). hp41uc settles these by checking its built-in tables first, then its XROM table in file order.
- **The Programmer's Handbook v2.07** (XROM section, from p. 51) tabulates roughly 2,200 function entries in 56 module tables (approximate: heuristic parse of a multi-column PDF), including third-party ROMs such as PPC ROM, Advantage, ZENROM, PANAME, CCD and HEPAX. These share the 31 XROM IDs (ID 5 is STANDARD, PANAME and ZENROM 1; ID 10 is PPC ROM, GAMES, FORECAST 1 and FORECASTER 2), so `(mm, ff)` alone does not identify a function.
- **Approach to evaluate:** since hardware allows one module per XROM ID at a time, a chosen **module set** is a conflict-free namespace. Other modules would be data files (ID, name, function names), names resolve only within the chosen set, and decompile keeps writing `XROM mm,ff` with a name comment. PPC ROM (IDs 10 and 20) is the likely first candidate.
- **Data sources and licensing:** hp41uc's table is GPL-3.0-or-later (bulk-copying it into a BSD-licensed repo is a licensing question, so settle GPLv3 first); the Handbook is © Ángel M. Martin. The Handbook text extracts badly, so any transcription needs coordinate-aware extraction and a check against a second source; `.mod` ROM images carry their own function-name tables and could provide that check.

## Parked (not in this plan)

The file-manager work from the earlier plan is not abandoned, just later: DM41X_Explorer as a FAT-disk file manager, `.m41` module-list and `.cst` custom-key files, `.b41` backups, `.mod` files, RAM images, OFF-images, the program editor with line numbers and jump-to-error, and everything specific to the DM41XN's serial interface. The format research already done (`cst.md`, `backup_set.md`, `dm41x_first_look_2026-10-04.md`) stays in `docs/`.

## Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| LKAOFF/FAST change state files | "Identical models" decision breaks | S2/S3 first; decision table says to revisit |
| Retyped XM files break `list_files()` | A DM41X state fails to load | S4 before phase 3.4; never raise from listing |
| The two `functions` tables drift (Python, TypeScript, hp41uc) | Wrong names or opcodes on one side | One-off diff of modules 25 and 26 against `hp41ucg.h` in phase 1; goldens in phase 5 |
| Re-vendoring brings in many unported modules | Divergence log grows | Port only the core listed in W1; add divergence rows for the rest |
| DM41XN differs from the DM41X | Shared-XROM assumption fails | Partly settled: `dm41xn.dm41` confirms X<I>Y and TRNG on a DM41XN. The 16 DM41X-module functions are untested there |

## Open questions

- [ ] The web app has no model selector, but a DM41L `.dm41` dump will show DM41X-sized XM totals. Acceptable, or should the extension pick the display profile (`.d41` → DM41X, `.dm41` → DM41L)?
- [ ] GPLv3 for DM41_Explorer: decided yes or no, and before or after the tag?
- [ ] Optional: can you run the S5 program on the DM41XN prototype as well, to confirm the 16 DM41X-module functions there? (X<I>Y and TRNG are already confirmed by `dm41xn.dm41`.)

## Sources

- DM41X User Manual v1.34 (`DM41X/DM41X_Data/dm41x_user_manual.pdf`), §3.4, §3.8.5, §4.7.3, §6.2.1.
- `dm41decoder/Source/hp41ucg.h` (hp41uc XROM table, GPL-3.0-or-later), `LICENSING-NOTE.md`, `reference/README.md`, `tests/oracle/provenance.json`, `docs/dm41-divergences.md`.
- This repository: `src/memory/device_profile.py`, `functions.py`, `mnemonics.py`, `mnemonic_dialects.py`, `xm_file.py`, `src/gui/overview_tab.py`, `src/tests/test_xm_three_regions.py`, `docs/dm41x_first_look_2026-10-04.md`, `docs/dump_format.md`.
