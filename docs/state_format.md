# The Memory State Text Format (`.dm41` and `.d41`)

2026-10-04 · Michael Heinz (research by Claude)

This document describes the text file format shared by the DM41L's `.dm41` memory states and the DM41X's `.d41` state files. It covers the file layout only. What the registers mean is described in `memory.md`, and the sub-structures inside them in `extended_memory.md`, `key_assignments.md`, `alarms.md` and `program.md`.

## 1. Source Material

- **`src/memory/memory.py`**: `Memory.from_string()` and `Memory.to_string()`, the reader and writer this document was checked against.
- **`src/tests/data/*.dm41` and `*.d41`**: the 40 sample states.
- **`dm41x_first_look_2026-10-04.md`**: where the DM41X `.d41` was first shown to be this format.

**Where the samples came from.** `dm41x_manyfiles.dm41` and `backuptest.d41` were made on a real DM41X (stated by the user). The other fixtures with calculator-style spacing (section 4) are believed to be calculator output as well, but no record of how each was made was checked.

## 2. Same Format on Every Model

A DM41L `.dm41` state and a DM41X `.d41` file use the identical text format, with the same `DM41` header line. The file cannot say which model wrote it; the caller chooses the memory map (`DeviceProfile`, see `device_profile.py`). The DM41X differs only in having more extended-memory registers, so its state has rows at addresses up to 0x3ef.

## 3. File Layout

A file has three sections, in this order:

```
DM41
00  08000000000000  09000000000997  00000000000000  00000000000000
04  09000000000997  00000000000000  00000000000000  00000000000000
...
A: 09999999999000  B: c008150a00e033  C: 000000033c00fd
S: 00100010000000
M: 003018a5ff83d3  N: 000000000c10a0  G: 00
```

### 3.1 Header

The first line is exactly `DM41`. It is not `DM41X` on a DM41X (observed in the `.d41` files). The reader rejects any other first line.

### 3.2 Register rows

- Each row starts with a **hexadecimal register address**, lowercase, at least two digits (`00`, `0c`, `198`, `3e8`). No `0x` prefix and no zero-padding beyond two digits.
- Then up to **four registers**, each **14 hex digits** (7 bytes), separated by two spaces.
- Row addresses written by the calculator and by DM41_Explorer are multiples of 4, so a row holds the registers at `base` to `base+3`.
- Rows are in ascending address order.
- A row whose four registers are all zero is **omitted**. A register at an address that no row covers is an all-zero register.
- Hex digits are written in the same order as `Register.get_hex()`: the first printed byte is byte 0, as in the register layouts in `memory.md`.

### 3.3 Special registers

After the last register row come the special registers, written as `<letter>: <hex>`. The names seen in every sample are `A`, `B`, `C`, `S`, `M`, `N` and `G`:

```
A: <14 hex digits>  B: <14 hex digits>  C: <14 hex digits>
S: <14 hex digits>
M: <14 hex digits>  N: <14 hex digits>  G: <2 hex digits>
```

- `A`, `B`, `C`, `S`, `M` and `N` are 7 bytes (14 hex digits). `G` is **1 byte** (2 hex digits).
- The code comment in `Memory.to_string()` calls these "representations of the HP41's CPU registers". That is the code's own description; this project has not decoded them.
- Three lines in the order `A B C`, then `S`, then `M N G`. `S` is optional in what `to_string()` will write; the other six are required once any special register is present.
- The special registers always come **after** all register rows. The reader raises an error for a register row after a special-register line.
- These values change from one save to the next, so they should be carried through unchanged. In the first look, two `.d41` files saved minutes apart differed only in `B`, `C`, `S` and `N`.

### 3.4 Line endings and the end of the file

- Samples written by the calculator use **LF** line endings, no CR, and end with a newline. `to_string()` also writes LF and a final newline. No sample in `tests/data` contains a CR.
- `empty.dm41` is the smallest sample: the header, three register rows (`08`, `0c`, `198`) and three special-register lines, 331 bytes. `Memory()` starts with registers `08`, `0c`, `0d` and `0e` already set to the "Memory Lost" values, so an empty state still has those.

## 4. Whitespace

Two spacing styles occur in the samples.

| Place | Calculator style | DM41_Explorer style (`to_string()`) |
| --- | --- | --- |
| Between registers on a row | two spaces | two spaces |
| After the last register on a row | two trailing spaces in most files (see below) | none |
| Between pairs on a special-register line | two spaces (`A: ...  B: ...`) | one space (`A: ... B: ...`) |
| After the last item of a special-register line | none | none |

Of the 40 sample files, 9 use two spaces between special-register pairs, and the other 31 use one:

| Files | Gap between pairs | Trailing spaces on register rows |
| --- | --- | --- |
| `dm41x_manyfiles.dm41`, `backuptest.d41`, `dm41x_pack_dotend.dm41`, `dm41x_pack_dotend-packed.dm41`, `fillextended.dm41`, `empty.dm41`, `empty-128.dm41` | two spaces | on every register row |
| `lander.dm41`, `targ.dm41` | two spaces | none |
| the other 31 (including all files with `-packed`, `-repacked` or `-xm` in the name) | one space | none |

Two spaces between pairs is the reliable marker of calculator-style output here. The trailing spaces are not: `lander.dm41` and `targ.dm41` have the calculator-style gaps but no trailing spaces. Why is unknown; an editor or version-control setting stripping trailing whitespace is one possibility, but that is a guess and was not checked.

**The calculator does not need the trailing spaces.** A copy of `dm41x_manyfiles.dm41` with all trailing whitespace removed by hand was loaded back into a real DM41X without any problem. That is one file on one model; the equivalent test on a DM41L has not been done. Because only trailing whitespace was removed, that test says nothing about the single versus double gap between special-register pairs.

## 5. What the DM41_Explorer Reader Accepts

`Memory.from_string()` is more tolerant than the calculator's own output. This describes the reader only; it says nothing about what the calculator accepts.

- Leading and trailing whitespace on the whole file, and on every line, is stripped; blank lines are skipped.
- Any whitespace may separate tokens, and CR or LF endings are both handled.
- Hex digits may be upper or lower case.
- A register row needs at least an address; row addresses need not be multiples of 4, but each address must be at or above the end of the previous row, or the reader raises an error.
- A register must be exactly 7 bytes (14 hex digits). A special register must be 7 bytes, or 1 byte for `G`. Anything else raises an error.
- More than four registers on one row raises an error ("Line too long"), after the row has been read.
- A special-register line is read as pairs of `<letter>: <hex>`. A token that is not `<letter>:` where a label is expected raises "Malformed line", and an odd number of tokens fails with an `IndexError` rather than a clear message. Any single character is accepted as a register label, not only `A` to `N`; `to_string()` writes only the named ones.
- The first line (after the whole file is stripped) must be exactly `DM41`.
- A register that no row mentions is zero, and an `S` line is optional: a file with no `S:` loads with none (`lander.dm41` and `targ.dm41` have neither a row 08 nor an `S:` line). The defaults a new `Memory()` starts with ("Memory Lost" values in registers 08, 0c, 0d, 0e and in the special registers) are replaced by the file's content. Before 2026-10-06 they showed through where a file had none.
- The reader does not check that an address is inside the memory map of the chosen `DeviceProfile`; a row at 0x500 loads without error (checked).

## 6. Where the Writer Differs

Changes in the text of a file that has been loaded and saved again with `Memory.to_string()`:

- Trailing spaces on register rows are dropped.
- Two-space gaps between special-register pairs become single spaces.
- All-zero register rows are omitted (the calculator already omits them, so this normally changes nothing).

The register contents themselves are unchanged. Loading a file, saving it and loading it again gives a `Memory` that compares equal (checked on `backuptest.d41`). For `.d41` and `.dm41` files a byte-identical round trip is therefore **not** expected; the project's round-trip test should compare the files after the whitespace above is normalised (`src/tests/test_state_roundtrip.py` does this for every fixture).

## 7. Open Questions

- Does a DM41L accept a state without the trailing spaces? The DM41X test is one file.
- Does the calculator accept single spaces between special-register pairs, or any of the other variations in section 5? Only the trailing-space removal has been tested.
- Why do `lander.dm41` and `targ.dm41` lack trailing spaces when the other calculator-style files have them?
- What do the special registers `A` to `N` and `G` hold, and which parts of them matter when a state is loaded back into a calculator?
- Does a `.d41` file from a DM41XN (not yet released) use the same format?
