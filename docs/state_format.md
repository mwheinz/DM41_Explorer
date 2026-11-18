# The Memory State Text Format (`.dm41`, `.d41` and `.px41`)

2026-10-04 · Michael Heinz (research by Claude)

This document describes the text file format shared by the DM41L's `.dm41` memory states and the DM41X's `.d41` state files, and the PX-41CX's variant of it, its `.px41` memory dumps (#82). It covers the file layout only. What the registers mean is described in `memory.md`, and the sub-structures inside them in `extended_memory.md`, `key_assignments.md`, `alarms.md` and `program.md`.

## 1. Source Material

- **`src/memory/memory.py`**: `Memory.from_string()` and `Memory.to_string()`, the reader and writer this document was checked against.
- **`src/tests/data/*.dm41` and `*.d41`**: the 40 sample states.
- **The first DM41X `.d41` files** (`/STATE` and `/BACKUP` on a new calculator, 2026-10-04), which showed the DM41X uses this format and loaded unchanged. The notes are archived in [issue #78](https://github.com/mwheinz/DM41_Explorer/issues/78#issuecomment-6885499867); `dm41x.md` describes the rest of the disk.
- **`src/tests/data/px41cx_*.px41`**: 15 PX-41CX dumps from the hp41cx_tools test data (GPL-3.0-or-later; see `tests/data/README.md`), 13 of them real captures. They were also compared for the special registers in section 3.3 (#79).

**Where the samples came from.** `dm41x_manyfiles.dm41` and `backuptest.d41` were made on a real DM41X (stated by the user). The other fixtures with calculator-style spacing (section 4) are believed to be calculator output as well, but no record of how each was made was checked.

## 2. Same Format on Every Model

A DM41L `.dm41` state and a DM41X `.d41` file use the identical text format, with the same `DM41` header line. The file cannot say which model wrote it; the caller chooses the memory map (`DeviceProfile`, see `device_profile.py`). The DM41X differs only in having more extended-memory registers, so its state has rows at addresses up to 0x3ef.

A PX-41CX dump holds the same registers, laid out slightly differently: a `PX41` header, different spacing (section 4.1) and a closing `EOF` line. Unlike the DM41 header, `PX41` does say which calculator wrote the file.

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

The first line is `DM41`, or `PX41` in a PX-41CX dump. It is not `DM41X` on a DM41X (observed in the `.d41` files). The reader rejects any other first line, and keeps the one it read: `to_string()` writes a state back in the layout of its own header.

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
- They appear to be the HP-41's CPU registers as they were when the calculator saved the state. Only `B` and `C` are understood (#79). `B` is always the flags register (0x0e) with its last byte replaced by `fd`. `C` is the same value on the DM41, and the flags shifted right two digits, ending `00fd`, on the PX-41CX. `A` holds a BCD number or ALPHA text left from the last operation. None of them was seen to hold a memory address, so editing memory leaves nothing in them to update.
- DM41_Explorer never changes them: a loaded state keeps its own values, and a new `Memory()` starts with "Memory Lost" values.
- Three lines in the order `A B C`, then `S`, then `M N G`. `S` is optional in what `to_string()` will write; the other six are required once any special register is present.
- The special registers always come **after** all register rows. The reader raises an error for a register row after a special-register line.
- These values change from one save to the next, so they should be carried through unchanged. Two `.d41` files saved minutes apart on a new DM41X differed only in `B`, `C`, `S` and `N`.

### 3.4 Line endings and the end of the file

- A PX-41CX dump ends with a line `EOF` after the special registers (all 13 real captures, and `GRAVITY`, a DM41 state given a PX41 header). A DM41 state has none.
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

**The DM41 does not need the trailing spaces.** A copy of `dm41x_manyfiles.dm41` with all trailing whitespace removed by hand was loaded back into a real DM41X without any problem. That is one file on one model; the equivalent test on a DM41L has not been done. Because only trailing whitespace was removed, that test says nothing about the single versus double gap between special-register pairs.

### 4.1 The PX-41CX layout

```
PX41
00 00000000000000 03600000000003 06000000000000 06000000000000
04 06000000000000 00000000000000 00000000000000 00000000000000
...
A: 099999999994f0  B: 0000002c0480fd  C: 000000002c00fd
S: 00101010000000
M: 000018a9ff82fb  N: 016919418e049f  G: 19
EOF
```

| Place | PX-41CX |
| --- | --- |
| Between the address and registers of a row | one space |
| After the last register on a row | one trailing space |
| Between pairs on a special-register line | two spaces |
| After the last item of a special-register line | none |
| After the special registers | a line `EOF` |

Both calculators seem to write every item of a register row followed by its separator, two spaces on the DM41 and one on the PX-41CX, which is where the trailing spaces come from. 12 of the 13 real PX-41CX captures follow this layout exactly; `px41cx_SETCLOCK-agarza.px41` has no trailing spaces. Of the other two, `px41cx_GRAVITY.px41` is in the DM41 layout, and `px41cx_NQUEENS.px41`, made by the hp41cx_tools encoder, uses the DM41 layout with a PX41 header and no `EOF`.

## 5. What the DM41_Explorer Reader Accepts

`Memory.from_string()` is more tolerant than the calculator's own output. This describes the reader only; it says nothing about what the calculator accepts.

- Leading and trailing whitespace on the whole file, and on every line, is stripped; blank lines are skipped.
- Any whitespace may separate tokens, and CR or LF endings are both handled.
- Hex digits may be upper or lower case.
- A register row needs at least an address; row addresses need not be multiples of 4, but each address must be at or above the end of the previous row, or the reader raises an error.
- A register must be exactly 7 bytes (14 hex digits). A special register must be 7 bytes, or 1 byte for `G`. Anything else raises an error.
- More than four registers on one row raises an error ("Line too long"), after the row has been read.
- A special-register line is read as pairs of `<letter>: <hex>`. A token that is not `<letter>:` where a label is expected, or an odd number of tokens, raises "Malformed line". A label other than `A`, `B`, `C`, `S`, `M`, `N` and `G` raises "Unknown special register", since `to_string()` would drop it.
- The first line (after the whole file is stripped, and the line itself) must be `DM41` or `PX41`.
- A line `EOF` ends the state, in either layout. Anything after it is ignored, such as noise from a serial transfer. It is optional, so a PX41 file without one loads too.
- The layout of each line is not checked against the header: a `PX41` file in the DM41 layout loads, and so does the reverse.
- A register that no row mentions is zero, and an `S` line is optional: a file with no `S:` loads with none (`lander.dm41` and `targ.dm41` have neither a row 08 nor an `S:` line). The defaults a new `Memory()` starts with ("Memory Lost" values in registers 08, 0c, 0d, 0e and in the special registers) are replaced by the file's content. Before 2026-10-06 they showed through where a file had none.
- The reader does not check that an address is inside the memory map of the chosen `DeviceProfile`; a row at 0x500 loads without error (checked).

## 6. Where the Writer Differs

Changes in the text of a DM41 file that has been loaded and saved again with `Memory.to_string()`:

- Trailing spaces on register rows are dropped.
- Two-space gaps between special-register pairs become single spaces.
- All-zero register rows are omitted (the calculator already omits them, so this normally changes nothing).

The register contents themselves are unchanged. Loading a file, saving it and loading it again gives a `Memory` that compares equal (checked on `backuptest.d41`). For `.d41` and `.dm41` files a byte-identical round trip is therefore **not** expected; the project's round-trip test should compare the files after the whitespace above is normalised (`src/tests/test_state_roundtrip.py` does this for every fixture).

A PX41 file is written in the PX-41CX layout of section 4.1, so a dump in that layout comes back **byte for byte**; `src/tests/test_px41_state_format.py` checks this for the 12 captures in it. A PX41 file in any other layout is rewritten in it.

`Memory.to_string()` writes the layout of the state's own header. When DM41_Explorer saves a file, the header is first set from the file's extension (`gui/state_files.py`, `HEADER_BY_EXTENSION`): `.px41` gives a PX-41CX dump and `.dm41` or `.d41` a DM41 state, wherever the state came from; any other extension gives the current mode's layout. Save As offers the current mode's extension for a new state, so choosing another extension is how a state is converted from one layout to the other.

## 7. Open Questions

- Does a DM41L accept a state without the trailing spaces? The DM41X test is one file.
- Does the calculator accept single spaces between special-register pairs, or any of the other variations in section 5? Only the trailing-space removal has been tested.
- Why do `lander.dm41` and `targ.dm41` lack trailing spaces when the other calculator-style files have them?
- What do `S`, `M`, `N` and `G` hold, and does the calculator use any of the special registers when a state is loaded back into it? Files the hp41cx_tools encoder writes with them all zero are described as loading fine, but only in an emulator.
- Does the PX-41CX need the `EOF` line and its own spacing when a dump is loaded back into it, or would it accept the DM41 layout?
