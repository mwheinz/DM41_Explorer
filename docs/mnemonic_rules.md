# How FOCAL Instruction Names Are Spelled and Resolved

2026-09-22 · Michael Heinz (research by Claude)

FOCAL listings in the wild spell the same instruction in several ways (`RDN` or `R↓`, `SQRT` or `√X`, `P-R` or `P->R`, `STO+` or `ST+`), and several HP-41 names contain characters that are hard to type (`Σ`, `≠`, `↑`). This document gives the rules DM41_Explorer follows for naming instructions: which name it shows, which it writes, which it accepts, and why. `memory/mnemonics.py` implements them. The full list of accepted spellings, generated from that code, is `mnemonics.md`.

## 1. Source Material

- **hp41uc** at commit ff23b21 (checked 2026-09-22): `hp41ucg.h` (decompile tables `single20_8F`, `prefix90_9F`, `prefixA8_AD`, `prefixCE_CF`), `compile.h` (alternate mnemonics `alt_fcn1`, `alt_fcn2`) and `compile.c` (keywords). `tests/test_mnemonics.py` holds the tables transcribed from it, so the tests don't need the hp41uc source.
- **`docs/function_table.md`** and `memory/functions.py`, the instruction set.
- **`docs/trigraphs.md`** and `memory/trigraphs.py`, the escape sequences.
- The design plan this came from, with the alternatives that were rejected, is archived in [issue #78](https://github.com/mwheinz/DM41_Explorer/issues/78#issuecomment-6885499658).

## 2. Three Kinds of Name

Each instruction is one registry entry, keyed by its encoding (`Op`: a kind plus its bytes). The kind matters because the keyboard-only functions in a Key Assignment Register (CAT 0x00, DEL 0x02, ... ASN 0x0F) reuse byte values that mean `LBL 00`-`LBL 14` in program memory, so a bare byte is not enough. Keyboard-only entries are marked `programmable=False`: the Key Assignment dialog accepts them and the program compiler rejects them.

| Name | Example (0x99) | Used for |
|---|---|---|
| Display | `ΣREG` | Everything shown in the GUI |
| Canonical | `SREG` | Text export only |
| Input aliases | the two above, plus substitutions and dialect spellings | Text import and the Key Assignment dialog |

No other code handles spellings. It asks the registry: `resolve(token)`, `canonical(op)` and `display(op)`.

## 3. Display Names

**The display name is what the HP-41 itself shows.** It uses only the glyphs in the FOCAL character set: `Σ` (0x7E), `≠` (0x1D), `↑` (0x5E) and `⊦` (0x7F). Glyphs from later HP models (`→`, `≤`, `√`) are never used, and spellings that contain them (`P→R`, `X≤Y?`, `→HMS`) are not accepted on input. So the display names are `P-R`, `X<=Y?`, `HMS`, `SQRT`, `RDN`, `ENTER↑`, `X≠Y?`, `ΣREG`, `CLΣ`.

`functions.py` stores these display names. XROM function names (`ΣREG?`, `X≠NN?`, ...) are display names too.

## 4. Canonical Names

**The canonical name is hp41uc's.** It is pure 7-bit ASCII, and text export always writes it, so an exported listing can be compiled by hp41uc. The rule: the canonical name is the display name with each glyph replaced by its first stand-in in the table in section 5.2 (`Σ`→`S`, `≠`→`#`, `↑`→`^`). There is one exception: `ENTER↑` is `ENTER`.

The bytes where hp41uc's name differs from the display name:

| Byte | Display | Canonical |
|---|---|---|
| 0x47 / 0x48 | `Σ+` / `Σ-` | `S+` / `S-` |
| 0x51 / 0x53 / 0x55 / 0x57 / 0x58 | `X↑2` / `Y↑X` / `E↑X` / `10↑X` / `E↑X-1` | `X^2` / `Y^X` / `E^X` / `10^X` / `E^X-1` |
| 0x63 / 0x79 | `X≠0?` / `X≠Y?` | `X#0?` / `X#Y?` |
| 0x70 | `CLΣ` | `CLS` |
| 0x74 | `R↑` | `R^` |
| 0x83 | `ENTER↑` | `ENTER` |
| 0x99 | `ΣREG` | `SREG` |

**XROM functions** are exported as `XROM mm,ff ;name`, as hp41uc does, so the numeric form is their canonical spelling. The function name is accepted on input. hp41uc's own XROM name tables are not used.

**`END` and `.END.`** hp41uc's compiler treats them the same: both produce an ordinary 3-byte END and stop compiling. A listing holds one program, and a printout of the last program in memory ends in `.END.`, so it is that program's closing line. Import does the same. The program becomes an ordinary END-terminated program, and the permanent `.END.` in memory is left alone. Export always writes `END`.

## 5. Resolving a Typed Name

### 5.1 Order

`resolve(token)` tries, in order:

1. **Trigraph decode.** If the token contains `\`, decode it with `memory/trigraphs.py`: `\EREG` → `ΣREG`. This happens before any case folding, because the escapes are case-sensitive (`\x` times, `\u` micro, `\E` Sigma, `\T` tee).
2. **Unicode folding.** `∑` (U+2211, which Option-W types on a Mac) becomes `Σ` (U+03A3).
3. **Exact-case lookup.**
4. **Case-insensitive lookup.** If ignoring case gives two different instructions, and no exact-case match settled it, the name is reported as ambiguous.

Keywords (`END`, `LBL`, `GTO`, `XEQ`, `XROM`) go through the same lookup, so their aliases (`GOTO`, `.END.`) work too.

If nothing matches, the error suggests up to three close matches ("did you mean ...?"), using `difflib` over all accepted spellings.

### 5.2 Layer 1: character substitutions

Every display name is accepted with every combination of these ASCII stand-ins, all taken from what hp41uc's compiler accepts:

| Glyph | Stand-ins | Examples |
|---|---|---|
| `Σ` | `S`, `SIG`, `SIGMA` | `S+`, `SIGMA+`, `SIGREG`, `SIGMAREG`, `CLSIGMA` |
| `≠` | `#`, `!=`, `<>` | `X#Y?`, `X!=Y?`, `X<>Y?` |
| `↑` | `^`; and `**` only for the power functions (0x51, 0x53, 0x55, 0x57, 0x58) | `X^2`, `X**2`, `ENTER^` |

`X<>Y?` (X≠Y?) and `X<>Y` (exchange) are different strings, so they don't collide. Layer 1 also produces a few spellings hp41uc itself doesn't accept (`SIG+`), which does no harm. Trigraph forms are not generated; step 1 handles them.

### 5.3 Layer 2: dialects

A dialect is a named, sourced list of spellings that use a different *word*, not just a different character. Dialects live in `memory/mnemonic_dialects.py`, keyed by canonical name, and their spellings get Layer 1 substitutions too. There are two:

- **hp41uc alternates** (`compile.h`, `compile.c`): `P->R`, `R->P`, `D->R`, `R->D`, `RUP` (R↑), `STO+`, `STO-`, `STO*`, `STO/`, `GOTO` and `.END.`. Together with Layer 1, this covers every alternate spelling hp41uc's compiler accepts. The CP437 forms it accepts are covered by section 7.
- **DM41X manual**: `ED$`, the DM41X's name for `ED` (XROM 25,51).

Not dialects, by decision: HP-42S, DM42 and Free42 spellings (a different instruction set), and one-off spellings from OCR'd or handwritten listings. Those listings stay test data. A pattern that recurs can become a new named dialect.

**To add a dialect:** add a `Dialect` to `DIALECTS` in `memory/mnemonic_dialects.py`, give it a `source`, and run `tests/test_mnemonics.py`, which builds the registry.

## 6. Safety Rules

1. **An ambiguous spelling is an error when the registry is built.** If any spelling would mean two different instructions, building the registry fails, and the tests catch it. Layer 1 generates many spellings, which makes accidental collisions more likely, so this check matters.
2. **No precedence.** Dialects never override each other or the canonical names. A disagreement is fixed by hand, not by a silent tie-break.
3. **Plug-in XROM libraries are a second, looser layer** (#52). The mainframe functions, every model's built-in XROMs and the dialects are checked strictly as above. In the active plug-in libraries a clash is normal, and the highest-priority library that claims a number or spelling keeps it. A name in both layers resolves by where it is used:
   - in a program listing (`resolve(token)`), the core layer first, so a bare `LOG` stays the mainframe LOG, as in hp41uc;
   - typed as on the calculator (`resolve(token, prefer_modules=True)`, used by the Key Assignment dialog), the calculator's own search order (the built-in Time ROM, then the plug-in libraries, then the rest), so a pac's `LOG` wins, as `ASN "LOG"` does on a real DM41X;
   - after `XROM "NAME"` (`resolve_xrom_name()`), only XROM functions, in the calculator's order.

## 7. File Encoding

`program_files.decode_program_txt()` strips a UTF-8 byte-order mark, tries UTF-8, and falls back to CP437. hp41uc-era listings are 8-bit DOS text, and hp41uc recognises CP437 byte 0xE4 as `Σ`. CP437 maps every byte, so decoding never fails; a file in some other encoding shows up as compile errors. Export is pure ASCII.

## 8. What Is Not Resolved Here

The registry resolves only the instruction name. Operands (`IND`, register letters, label numbers, digits) and quoted ALPHA text are parsed in `memory/program_text.py`; ALPHA text goes through `trigraphs.py` in both directions.

hp41uc accepts some spellings outside instruction names that import does not:

- ALPHA append prefix: `├` (CP437 0xC3), `|-`, `\-`, `>-`, `->`, `APND`, `APPND`, `APPEND`. Only `>` is accepted.
- ALPHA text prefix: `T`, `TXT`, `TEXT` before a quoted string.
- `W "alpha"` (hp41uc-specific; compiles to 0x1F 0xFn ...).
- Register operands `100` and `101` (0x64/0x65), `102`-`111` for `A`-`J`, `[ \ ] ^ _` and backtick for `M`-`R`, and `├ > |- \- >- ->` for register `⊦` (0x7A).
- An operand with no space before it (`ST+01`), which would need longest-prefix matching.

These can be added when a real file needs them.

## 9. Where the Names Are Shown

- The Key Assignments tab and the Key Assignment edit dialog show display names, and the dialog's typed input goes through `resolve(programmable_only=False)`.
- Help > FOCAL Mnemonics Reference is a filterable table built at run time by `memory/mnemonic_doc.py`. The same code writes `mnemonics.md`, and a test fails if that file is out of date.

## 10. Licensing

hp41uc is GPL-3.0. The tables copied from it are instruction names: HP's published mnemonics and common community spellings, not hp41uc's code.
