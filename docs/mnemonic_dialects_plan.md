# FOCAL Mnemonic Dialects — Design Plan

Status: Currently complete. Unless new bugs are discovered, this document is
left only for reference purposes. Much of the issues this effort needed to
resolve were my own fault for trying to blend HP48 concepts (trigraphs) with
HP41 coding, without realizing what previous HP41 coders had already
standardized on.

## 1. Problem

Three recurring problems with text-format HP-41 programs:

1. **Dialects.** FOCAL listings in the wild spell the same instruction in
   different ways (`RDN` vs `R↓`, `SQRT` vs `√X`, `P-R` vs `P->R` vs `P→R`,
   `STO+` vs `ST+`, ...). DM41L_Explorer's importer accepts only a few
   spellings per instruction, so importing a program written in another
   dialect fails.
2. **Non-ASCII mnemonics.** Instructions whose names contain characters like
   `Σ`, `≠`, `↑`, `⊦` are hard to type. Users forget which substitute
   spelling is expected (`ΣREG`? `SIGMAREG`? `SREG`? `\EREG`?).
3. **Our export isn't hp41uc-compatible.** `encode_program_txt()` currently
   emits names hp41uc doesn't use, some of them non-ASCII. Verified output
   vs hp41uc's own decompile table (`hp41ucg.h`, `single20_8F`/`prefix90_9F`):

   | Byte | We emit | hp41uc emits |
   |---|---|---|
   | 0x47 / 0x48 | `Σ+` / `Σ-` | `S+` / `S-` |
   | 0x4E / 0x4F | `PR` / `RP` | `P-R` / `R-P` |
   | 0x51 / 0x53 / 0x55 / 0x57 / 0x58 | `X↑2` / `Y↑X` / `E↑X` / `10↑X` / `E↑X-1` | `X^2` / `Y^X` / `E^X` / `10^X` / `E^X-1` |
   | 0x65 | `LNX+1` | `LN1+X` |
   | 0x6A / 0x6B | `DR` / `RD` | `D-R` / `R-D` |
   | 0x70 | `CLΣ` | `CLS` |
   | 0x74 | `R↑` | `R^` |
   | 0x83 | `ENTER↑` | `ENTER` |
   | 0x99 | `SIGMAREG` | `SREG` |

   The `PR/RP/DR/RD` spellings came from an extrapolation that
   `ASCII_DISPLAY_NAMES` itself flagged as unverified. hp41uc rejects them,
   and the Unicode lines make the file non-ASCII.

   0x65 is a plain naming bug: `functions.py` has `LNX+1`, but the HP-41
   function is `LN1+X` (`docs/function_table.md` has `LN1+X` in its
   Instruction Prefix column and `LNX+1` in its Function column). It
   shows wrongly in the Key Assignments tab too, and `LN1+X` in an
   hp41uc listing doesn't import today.

## 2. Requirements

- R1. Support multiple spellings ("dialects") of each FOCAL instruction.
- R2. hp41uc's mnemonics (its decompile table) are **canonical**. Text
  export always emits the canonical form, which is pure 7-bit ASCII.
- R3. Text import accepts the canonical form, the display (Unicode) form,
  and forms written with the escape sequences in `memory/trigraphs.py`.
  For example, `SREG`, `ΣREG`, `∑REG`, `SIGMAREG`, and `\EREG` are the same
  instruction.
- R4. New dialects are easy for a developer to add as they are discovered.
- R5. The GUI displays instructions in their display form (§3.2). Today
  that means the Key Assignments tab and its edit dialog (§3.9).
- R6. A generated reference lists every supported spelling of every
  instruction, plus the trigraph table, reachable from the Help menu.
  (README.md in the Help menu is deferred; see §4.)

## 3. Design

### 3.1 One registry, three roles

Each instruction is one registry entry, keyed by its **program-byte
encoding** (1 byte, or the 2-byte XROM pair). Each entry has:

| Field | Example (0x99) | Used for |
|---|---|---|
| Canonical | `SREG` | Text export only |
| Display | `ΣREG` | Everything shown in the GUI |
| Input aliases | canonical + display + substitutions + dialect spellings | Text import |
| `programmable` | True | Compiler accepts it |

`functions.py` is keyed by the Key Assignment Register encoding, which also
includes entries that can't appear in a program (CAT, SST, BST, ASN, ...
below 0x40). Those go in the registry with `programmable=False`. The Key
Assignments tab can use them, and the compiler rejects them, as the current
`byte >= 0x40` guard does.

**Op identity.** Don't key entries by a bare byte. The Key Assignment-only
entries (CAT 0x00, DEL 0x02, ... ASN 0x0F) reuse byte values that mean
compact `LBL 00`-`LBL 14` in program memory. Use a small tuple/dataclass
key such as `(kind, bytes)`, with keyword instructions (see below) getting
their own kind.

New module `memory/mnemonics.py` owns this. The rest of the codebase never
handles spellings directly; it asks the registry:

- `canonical(op) -> str`
- `display(op) -> str`
- `resolve(token, *, programmable_only=True) -> op` (raises on unknown token)

The existing resolution paths in `memory/program_text.py`
(`_resolve_single_byte_mnemonic()`, `_resolve_xrom_mnemonic()`,
`ASCII_DISPLAY_NAMES`/`_CANONICAL_NAME_FOR_DISPLAY`) and `memory/functions.py`'s
`normalize_function_name_input()` all route through `resolve()`. The last
becomes a thin `display(resolve(text, programmable_only=False))` wrapper
for the Key Assignment edit dialog -- but only in Phase 3, together with
the dropdown's switch to `display()` names. Switching it earlier would
return `P-R` while the dropdown and `bytes_for_function_name()` still use
`P→R`.

**Keyword instructions go through `resolve()` too.** `_encode_instruction()`
currently dispatches `END`, `LBL`, `GTO`, `XEQ` and `XROM` by literal
string comparison before any resolver runs. The registry also needs
entries for these keywords (e.g. `GOTO` → `GTO`, `.END.` → `END`, both
accepted by hp41uc's compiler). Dispatch happens on the resolved keyword,
so these aliases work too.

### 3.2 What "display form" means

`functions.py`'s names today mix two conventions:

- glyphs the HP-41 itself can show, all in the FOCAL character set: `Σ`
  (0x7E), `≠` (0x1D), `↑` (0x5E), `⊦` (0x7F);
- glyphs it can't show, from later HP models: `→` (`P→R`, `→HMS`), `≤`
  (`X≤Y?`).

Only the first group has trigraphs (`\E`, `\/=`, `\^|`, `\+`). Nothing in
`trigraphs.py` spells `→`, `≤` or `√`.

**Decided: display form = what the HP-41/DM41L shows.** The display name is
the calculator's own byte string, rendered through a single FOCAL-byte →
Unicode table. The GUI needs that table anyway for ALPHA text. So:
`P-R`, `X<=Y?`, `HMS`, `SQRT`, `RDN`, `ENTER↑`, `X≠Y?`, `ΣREG`, `CLΣ`.

Consequences:

- Display = canonical with the FOCAL glyphs restored (`^`→`↑`, `#`→`≠`,
  `S`→`Σ` where hp41uc abbreviates Sigma). This is a small hand-checked
  table, not a guess.
- Trigraph input works by construction (§3.3). It needs no alias table.
- `→` and `≤` spellings (`P→R`, `X≤Y?`, `→HMS`) are **not** accepted.
  They come from later HP models (decision 6).

Sigma: `functions.py` uses `Σ` (U+03A3). Option-W on a Mac types `∑` (U+2211).
Accept both on input; display `Σ`. (This is input normalization of one
character, not a later-model dialect.)

Names that change in the Key Assignments tab:

| Byte | `functions.py` today | Display form |
|---|---|---|
| 0x46 / 0x7B | `X≤Y?` / `X≤0?` | `X<=Y?` / `X<=0?` |
| 0x4E / 0x4F | `P→R` / `R→P` | `P-R` / `R-P` |
| 0x6A / 0x6B | `D→R` / `R→D` | `D-R` / `R-D` |
| 0x6C / 0x6D / 0x6F | `→HMS` / `→HR` / `→OCT` | `HMS` / `HR` / `OCT` |

The Σ, ≠ and ↑ names are already native and don't change. XROM names
(`ΣREG?`, `X≠NN?`, ...) are already native too.

### 3.3 Input normalization and aliases

`resolve(token)` runs in this order:

1. **Trigraph decode.** If the token contains `\`, run it through
   `decode_trigraphs()` and map the bytes through the FOCAL → Unicode table.
   `\EREG` → `ΣREG`. This must happen **before any case folding**: the
   shorthands are case-sensitive (`\x` times, `\u` micro, `\E` Sigma,
   `\T` tee). `_encode_instruction()`'s current up-front `first.upper()`
   has to go.
2. **Unicode folding.** `∑` → `Σ`.
3. **Exact-case lookup** in the alias table.
4. **Case-insensitive lookup.** Precedent: `_parse_register_base()`, where
   lowercase `a`–`e` and uppercase `A`–`E` must stay distinct. A folded
   collision between two ops is an error only if no exact-case match
   disambiguates it.

The alias table has two layers.

**Layer 1: character substitutions (generated).** A small table maps each
non-ASCII display glyph to its ASCII substitutes, seeded from what hp41uc's
own compiler accepts (`compile.h`, `alt_fcn1`/`alt_fcn2`):

```
"Σ": ["S", "SIG", "SIGMA"]      # hp41uc: S+, SIGMA+, SIGREG, SIGMAREG, CLSIGMA
"≠": ["#", "!=", "<>"]          # hp41uc: X#Y?, X!=Y?, X<>Y?
"↑": ["^", "**"]                # hp41uc: X^2, X**2, ENTER^
```

At build time, every display form gets every combination of substitutions.
`ΣREG` therefore produces `SREG`, `SIGREG`, and `SIGMAREG`. Trigraph forms
are not generated; step 1 above handles them.

**Layer 2: dialect tables (hand-maintained).** For spellings that use a
genuinely different word, not just a different character. A dialect is a
named, sourced mapping from op to a list of spellings:

```
Dialect(
    name="hp41uc alternates",
    source="hp41uc compile.h alt_fcn1/alt_fcn2, compile.c (commit ff23b21)",
    aliases={"P-R": ["P->R"], "R-P": ["R->P"], "D-R": ["D->R"],
             "R-D": ["R->D"], "R^": ["RUP"], "ST+": ["STO+"],
             "ST-": ["STO-"], "ST*": ["STO*"], "ST/": ["STO/"],
             "GTO": ["GOTO"], "END": [".END."]},
)
```

Dialect aliases pass through Layer 1 too, so a dialect only needs to list a
spelling once, in its most natural form. The example above is the whole
hp41uc alternates dialect: everything else hp41uc accepts is generated by
Layer 1 (§3.4).

Initial dialects (decision 6 -- HP-41 display set, trigraph forms, and
hp41uc only):

- **HP-41 display set**: the display forms themselves (§3.2).
- **Trigraph forms**: handled by step 1 of `resolve()`, not by a table.
- **hp41uc**: the canonical names (§3.4) plus hp41uc's compiler
  alternates (`alt_fcn1`/`alt_fcn2`, `GOTO`, `.END.`). Layer 1's
  substitutions are also all taken from hp41uc.

Explicitly out of scope: HP-42S/DM42/Free42 spellings (different
instruction set), and ad-hoc spellings from OCR'd or handwritten
listings. Those can be added later as named dialects if a pattern recurs.

Dialects live in a single Python module, `memory/mnemonic_dialects.py`
(decision 2). Reasons to
prefer Python over TOML/JSON: import-time errors, no PyInstaller data-file
handling, readable diffs. Adding a dialect is one new entry plus a test run.

The `source` field records where the dialect was found and feeds the
reference document (§3.8).

### 3.4 Canonical table from hp41uc

Decision (§5, item 7): **hand-maintained tables, no extraction script.**
Checked against `~/Work/hp41uc/Source` at commit ff23b21 on 2026-09-22.
The tables are short and hp41uc is effectively frozen, so a generator
would be more code than the data it produces.

**Canonical names.** `hp41ucg.h`'s `single20_8F` (0x40–0x8F) matches
`functions.py` except for 22 bytes, and the prefix tables differ only at
0x99:

| Byte | `functions.py` | hp41uc canonical |
|---|---|---|
| 0x46 / 0x7B | `X≤Y?` / `X≤0?` | `X<=Y?` / `X<=0?` |
| 0x47 / 0x48 | `Σ+` / `Σ-` | `S+` / `S-` |
| 0x4E / 0x4F | `P→R` / `R→P` | `P-R` / `R-P` |
| 0x51 / 0x53 / 0x55 / 0x57 / 0x58 | `X↑2` / `Y↑X` / `E↑X` / `10↑X` / `E↑X-1` | `X^2` / `Y^X` / `E^X` / `10^X` / `E^X-1` |
| 0x63 / 0x79 | `X≠0?` / `X≠Y?` | `X#0?` / `X#Y?` |
| 0x65 | `LNX+1` | `LN1+X` |
| 0x6A / 0x6B | `D→R` / `R→D` | `D-R` / `R-D` |
| 0x6C / 0x6D / 0x6F | `→HMS` / `→HR` / `→OCT` | `HMS` / `HR` / `OCT` |
| 0x70 | `CLΣ` | `CLS` |
| 0x74 | `R↑` | `R^` |
| 0x83 | `ENTER↑` | `ENTER` |
| 0x99 | `ΣREG` | `SREG` |

`prefix90_9F`, `prefixA8_AD` and `prefixCE_CF` otherwise match
`program_text.py`'s prefix names (RCL ... TONE, SF ... FC?, X<>, LBL).
Implemented (Phase 1) as a rule rather than a copy of this table: the
canonical name is the display name with each glyph replaced by its first
Layer 1 stand-in (`Σ`→`S`, `≠`→`#`, `↑`→`^`), with one exception,
`ENTER↑`→`ENTER`. `tests/test_mnemonics.py` holds this table,
transcribed from hp41uc, and checks the rule against it. `functions.py`'s
0x65 was fixed to `LN1+X` in Phase 0.

**Alternates.** hp41uc's compiler matches every mnemonic
case-insensitively (`_stricmp`). Its complete list of alternate
mnemonics:

- `compile.h` `alt_fcn1`: `SIGMA+`, `SIGMA-`, `CLSIGMA`, `P->R`, `R->P`,
  `D->R`, `R->D`, `X**2`, `Y**X`, `E**X`, `10**X`, `E**X-1`, `X!=0?`,
  `X<>0?`, `X!=Y?`, `X<>Y?`, `RUP`, `ENTER^`, plus `Σ+`, `Σ-`, `CLΣ`
  written with raw CP437 byte 0xE4.
- `compile.h` `alt_fcn2`: `STO+`, `STO-`, `STO*`, `STO/`, `SIGREG`,
  `SIGMAREG`, plus `ΣREG` in CP437.
- `compile.c` keywords: `GOTO` (= `GTO`) and `.END.`. `compile_arg1()`
  treats `END` and `.END.` identically: both emit the same ordinary
  3-byte END (third byte 0x0D, unpacked, not private) and both stop
  compilation. It never creates the permanent `.END.` marker, and the
  decompiler always writes `END` (`prefixEND`). The reason: a listing
  holds one program, and a printout of the last program in memory ends
  with `.END.`, so hp41uc takes it as that program's closing line. Our
  import does the same. The program becomes an ordinary END-terminated
  program, and the memory's own permanent `.END.` is left alone.

Layer 1 (§3.3) generates every one of these except `P->R`, `R->P`,
`D->R`, `R->D`, `RUP`, `STO+`/`-`/`*`/`/`, `GOTO` and `.END.`, which make
up the whole Layer 2 hp41uc dialect. The CP437 forms are covered by
§3.7's CP437 decoding (0xE4 → `Σ`). Layer 1 also generates a few
spellings hp41uc doesn't accept (e.g. `SIG+`); that's harmless.

**hp41uc alternates outside instruction mnemonics** (not in this plan's
scope, §3.6; listed so they aren't lost):

- ALPHA append prefix: `>`, `├` (CP437 0xC3), `|-`, `\-`, `>-`, `->`,
  `APND`, `APPND`, `APPEND`. We accept only `>`.
- ALPHA text prefix: `T`, `TXT`, `TEXT` (optional before a quoted string).
- `W "alpha"` (hp41uc-specific, compiles to 0x1F 0xFn ...).
- Register operands: `100`, `101` (0x64/0x65; our parser and decoder stop
  at 99), `102`–`111` (= `A`–`J`), `[ \ ] ^ _` and backtick (= `M`–`R`),
  and `├ > |- \- >- ->` (= `R`, 0x7A). Case-insensitive for `F`–`J` and
  `T`–`R`; `a`–`e` stay case-sensitive, as ours do.

XROM functions keep the existing decision: export as numeric
`XROM mm,ff ;name`, per hp41uc convention. For XROMs, that numeric form is
the canonical spelling. Their display form is the function name (from
`functions.py`'s `XROM_FUNCTIONS`), and the name is an input alias
(already true today via `XROM_NAMES`). hp41uc's XROM name tables are not
used.

### 3.5 Safety rules enforced at registry build time

1. **Ambiguity is a hard error.** If any alias resolves to two different
   ops, registry construction fails. A unit test builds the registry, so
   this is caught before anything ships. Example: a future dialect that
   spells R↓ as `RD` would collide with one that spells R→D as `RD`.
   Automatic generation (Layer 1) makes accidental collisions more likely,
   so this check is essential.
   (Not a collision: `X<>Y?` vs `X<>Y`. hp41uc deliberately accepts `X<>Y?`
   as X≠Y?; it is a distinct string from the exchange instruction.)
2. **Case handling** as in §3.3 steps 3–4.
3. **No dialect precedence.** Dialects never override each other or the
   canonical table. If two sources disagree about what a spelling means,
   that is an ambiguity error to resolve by hand, not a silent tie-break.

### 3.6 Scope of resolution

The registry resolves only the **instruction token** (including the
keyword instructions, §3.1). Unchanged:

- Operand parsing (`IND`, register postfixes, label numbers, digits).
- Quoted ALPHA text, which already goes through `trigraphs.py` in both
  directions. Its display/export rule is the same as for mnemonics: Unicode
  in the GUI, `\nnn`/shorthand on export (already decided in the text-I/O plan).

Deferred until a real file needs it: dialects that omit the space before an
operand (`ST+01`), which would require longest-prefix matching; and the
hp41uc operand and ALPHA-prefix alternates listed at the end of §3.4.

### 3.7 File encoding

`program_files.decode_program_txt()` currently requires UTF-8 and fails with
"TXT file isn't valid UTF-8" otherwise. hp41uc-era listings are 8-bit DOS
text; hp41uc itself recognizes 0xE4 (CP437 `Σ`). Decode as: strip a UTF-8
BOM, try UTF-8, fall back to CP437. Export stays ASCII-only once §3.4 is in
place, so the output encoding question goes away.

### 3.8 Reference generator

A function inside the package (not a standalone tool), e.g.
`memory/mnemonic_doc.py: mnemonic_reference_rows()` plus
`render_mnemonic_reference() -> str` (Markdown). Content:

- **Instructions:** display form, canonical form, whole-word aliases, and
  dialect sources.
- **Character substitutions:** the Layer 1 table, listed once, rather than
  every generated combination for every instruction (which gets noisy).
- **Trigraphs:** escape sequence and the character it produces.

Consumers:

- The **Help menu** (§3.10) calls it at runtime, so the in-app reference
  always matches the code.
- A thin CLI wrapper writes the Markdown to `docs/mnemonics.md` for GitHub.
  A test regenerates it and fails if the committed file is stale.
- The same alias set feeds "did you mean ...?" suggestions on unknown
  tokens (decision 3): `difflib.get_close_matches()` over the alias set,
  reporting the display and canonical forms of up to three matches.

### 3.9 GUI display

The Programs tab lists programs (labels, address, length, key
assignment), not instructions, so today the only consumers are:

- **Key Assignments tab** cells (`key_assignments.py` fills
  `assignment["name"]` via `function_name_for_bytes()`).
- **Key Assignment edit dialog**: the dropdown values
  (`_ALL_FUNCTION_NAMES`), the typed-input path
  (`normalize_function_name_input()` → `resolve(programmable_only=False)`),
  and the name → bytes lookup (`bytes_for_function_name()`). All three
  must switch together (decision 4).
- Any future program listing, editor or viewer.

Glyph coverage for `Σ ≠ ↑ ⊦` (and `∡ µ` for ALPHA text) must be checked in
the fonts CTk actually uses on macOS and Windows. If a glyph is missing on
one platform, the FOCAL → Unicode table is the one place to choose a
fallback.

### 3.10 Help menu

Current state: Help has only About (non-macOS) and Keyboard Shortcuts
(`gui/help_dialog.py`, commit 8708ba2). Add:

- **FOCAL Mnemonics Reference**

README in the Help menu is deferred (decision 5).

**Mnemonics reference: no Markdown viewer needed.** Its format is under our
control, so show it as a filterable `ttk.Treeview` (type "sigma", see every
match), built from `mnemonic_reference_rows()`. Use plain `tk`/`ttk`
widgets, not `CTkScrollableFrame`, which doesn't get trackpad scroll events
(see `gui/scroll_support.py`'s docstring).

The Markdown viewer choice (§4) covers README only, and is deferred.

## 4. Markdown display options (README) -- deferred

Not part of this feature (decision 5). Kept for when README in the Help
menu is picked up. If done, README must be bundled via `datas` in
`dm41l.spec` (precedent: `gui/flags_doc.py` and `docs/flags.md`), and
an in-app viewer also needs the 8 screenshots in `resources/screenshots/`.

| Option | Fidelity | Dependencies | Notes |
|---|---|---|---|
| A. TkinterWeb + Python-Markdown | High (tables, images, links, CSS) | Compiled Tcl extension (Tkhtml3) | MIT. Needs packaging spike |
| B. markdown-it-py → `tk.Text` tags (own renderer) | Medium | Pure Python | Tables as monospace; images need `PhotoImage` handling |
| C. tkhtmlview | Low | Pure Python | Small HTML subset; images yes, tables weak/unsupported as far as known |
| D. Convert to HTML, open in system browser | High | None beyond a Markdown converter | Least integrated; zero risk |

**Option A details.** TkinterWeb wraps a modified Tkhtml3 widget and names
help files and documentation as a primary use case. Risks:

- Tkhtml3 is a compiled Tcl extension. It must package cleanly with
  PyInstaller on Apple Silicon and on the Windows CI runners. Given the
  Windows CI Tcl-init flakiness already dealt with, prototype before
  committing.
- Python-Markdown loads extensions (e.g. `tables`) by name at runtime.
  PyInstaller misses them unless they are imported explicitly or listed as
  hidden imports.
- It won't follow the CTk light/dark theme on its own. Supply two small CSS
  stylesheets and pick one from the current appearance mode.

**Option B details.** No binaries, themes easily. A few hundred lines, more
with images. README has 15 table lines and 8 screenshots, so B's
limitations show there.

**Recommendation.** Since the mnemonic reference no longer needs Markdown,
the stakes are lower: ship Option D for README first. Spend an afternoon on
an Option A spike (macOS .app and Windows exe) only if an in-app README is
wanted.

## 5. Open decisions

Resolved 2026-09-22:

1. Display convention: **HP-41-native** (`P-R`, `X<=Y?`, `ΣREG`).
2. Dialect storage: **single module**.
3. "Did you mean ...?" suggestions: **yes** (§3.8).
4. Key Assignments tab switches to `display()` names: **yes**, using the
   HP-41 display set (Phase 3).
5. README viewer: **postponed**; not part of this feature.
6. Initial dialects: **HP-41 display set, trigraph forms, hp41uc** only.
   No HP-42S/DM42/Free42 spellings. OCR'd and handwritten listings
   (TowerOfSkelos, GhostTown, PPC) stay test data, not dialect sources.

7. Canonical table source: **hand-maintained**, no extraction script
   (findings in §3.4).

Also resolved: backward compatibility with files exported by earlier
versions is not required (former R7 removed).

## 6. Licensing note

hp41uc is GPLv3. The tables contain only instruction names, which
are HP's own published mnemonics and common community spellings (facts),
not hp41uc code. This keeps the tables
clear of the concern flagged in `program_text_io_plan.md` §6.

## 7. Phasing

One PR per phase, per CONTRIBUTING.md. The app stays fully usable after
each phase; run the full test suite plus a manual import/export smoke
test before starting the next one.

0. **File encoding** (§3.7). Strip BOM, UTF-8, CP437 fallback in
   `program_files.decode_program_txt()`. Also fix `functions.py`'s 0x65
   name to `LN1+X`. Independent of everything else.
1. **Registry core, Layer 1, canonical export.** `mnemonics.py`, canonical
   table (§3.4), `canonical()`/`display()`/`resolve()`, Layer 1
   substitutions, the Σ U+2211 fold, the hp41uc alternates dialect,
   route the program-text resolvers and keyword dispatch through
   `resolve()`, remove the early `.upper()`. Layer 1 must be in this
   phase, and so must the hp41uc alternates: today
   `normalize_function_name_input()` makes `P->R`, `ENTER^` and
   `sigmareg` import, and dropping it without a replacement would
   regress those. `normalize_function_name_input()` stays as-is for the
   Key Assignment dialog until Phase 3.
   **User-visible:** export switches to hp41uc canonical names (§1 table).
   Tests: ambiguity check, case rules, round-trip over every op, export
   of tower's bytes matches tower.txt, export is pure ASCII, every
   hp41uc alternate in §3.4 imports.
2. **Trigraphs and suggestions.** Trigraph normalization and
   "did you mean ...?". Tests for
   `SREG` / `ΣREG` / `∑REG` / `SIGMAREG` / `\EREG` equivalence and
   `\x`-style case sensitivity.
3. **Key Assignments display.** Tab cells, edit-dialog dropdown, typed
   input and name → bytes lookup all use the registry. Font glyph check
   on macOS and Windows.
4. **Reference + Help menu.** `mnemonic_reference_rows()`,
   `render_mnemonic_reference()`, CLI wrapper, `docs/mnemonics.md`,
   staleness test, and the Help > FOCAL Mnemonics Reference Treeview.

## 8. References

- hp41uc (canonical mnemonic source, commit ff23b21): `~/Work/hp41uc/Source` —
  `hp41ucg.h` (decompile tables), `compile.h` (`alt_fcn1`/`alt_fcn2`),
  `compile.c` (`GOTO`, `.END.`).
- TkinterWeb: https://github.com/Andereoo/TkinterWeb
- Python-Markdown: https://python-markdown.github.io/
- Python-Markdown extensions under PyInstaller:
  https://discuss.python.org/t/problem-with-python-markdown-and-pyinstaller/10206
