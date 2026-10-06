# Key Assignments

This document describes how the HP-41C/CV/CX (and therefore the DM41L emulator)
stores USER-mode key assignments.

## 1. Source Material

- **William C. Wickes, *Synthetic Programming on the HP-41C***: Section 2E,
  "The Key Assignment Registers," and Section 4E, "The Key Assignment Flags"
- **Keith Jarrett, *Synthetic Programming Made Easy*** Section 6B and Figure
  6.3 give the processor's actual USER-mode key-press lookup algorithm.
- **"A programmers handbook v.2.07.pdf"** — Ch. 8 "Key assign flag registers &
  e," Ch. 39-42 "Key code maps," including a "Key assignment flag bits" keypad
  chart.

## 2. Terminology

- **Unshifted / shifted key** — nearly every physical key on the keyboard can
  hold two independent assignments: one for a plain press, one for a press
  preceded by the shift key. These assignments are stored independently.
- **Key number** — a two-digit row/column identifier, `MN`, where `M` is the
  physical row (1-8) and `N` is the physical column. This is the notation
  Wickes uses and this document follows; it is *not* the same as the byte value
  used to encode that key internally, and it does not match the physical key
  layout of the DM41L.
- **Built-in function assignment** — assigning a key to a function that's part
  of the calculator's own instruction set (`+`, `SIN`, `STO`, ...) or a
  plugged-in module's function. (For the DM41L, this is limited to the
  functions that came with the HP41CX. Stored in the Key Assignment region.
- **Global label / program assignment** — assigns a key to run a user-written
  program by its global label (`LBL "NAME"`). Stored within the global label
  itself, not in the key assignment area.
- **KEYFLAGS** — the 36-bit-per-shift-state bitmap in registers R and e that
  records *whether* a key has been reassigned.

## 3. Overview

Key Assignment Registers hold the user-mode assignments of built-in and XROM
functions and occupy addresses `0x0C0` upward, growing toward `.END.` as more
are added. Nearly every key on the HP41/DM41L can be reassigned, only "ON",
"User", "PRGM", "Shift", and "Alpha" cannot. Registers "R" (`0x0A`) and "e"
(`0x0F`) hold the KEYFLAGS bitmaps. Global-label assignments are actually parts
of the global labels themselves.

## 4. How Key Assignments Are Stored

A key assignment can be recorded in one of two completely different places,
depending on what it's assigned to:

1. A built-in or peripheral function assignment is recorded as an entry in
   the **Key Assignment Registers**, a buffer starting at `0x0C0` (§4.2).
2. A user program assignment is recorded **inside that program's own global
   label** and never touches the Key Assignment Registers at
   all.

Both kinds set the same KEYFLAGS bit for the assigned key.

### The Key Assignment Registers (starting at `0x0C0`)

Each register that holds assignments begins with marker byte `0xF0` followed by
one or two 3-byte entries packed into the remaining 6 bytes of the register:

    F0  [fn byte 1 / fn byte 2] [key byte]  [fn byte 1 / fn byte 2] [key byte]
        \___________ entry 1 ____________/  \___________ entry 2 ____________/

A built-in HP-41 function may only need one function byte; in that case, byte 1
will be 0x00. A XROM function code will always be two bytes. Note that these
function codes might be slightly different from the actual byte codes for a
function when used in a program.

An odd number of assignments, or deleting a key assignment, will leave a
register half-full. Repacking calculator memory (`GTO ..`) will eliminate empty
entries if possible.

### Insertion Order

A brand-new assignment is always written into the **lowest** register
(`0x0C0`), immediately after its marker byte, pushing every existing entry up
toward `.END.`. Reading the buffer from `0x0C0` upward therefore lists
assignments **newest first** — the opposite of a naive top-to-bottom read of a
printed dump. This also means that if alarms are in use, they have to be moved
every time a new register is needed for key assignments.

### Key Position Encoding

The key position for an **unshifted** key at row `M`, column `N` is:

    byte = 16*(N-1) + M

For the **shifted** version of the same key:

    byte = 16*(N-1) + (M+8)

carrying into the high nibble when `M+8 >= 16` (only possible for `M=8`
rows). Confirmed exactly against `keyassigns.dm41`'s 14 assignments across
8 different physical keys, both shift states, with zero mismatches.

Note that M and N refer to the positions of the keys on the original
HP41C/CV/CX calculator. The DM41L uses the same key position values even though
the physical keys are in different positions. Note, too, that on the original
HP41 series the ENTER key only has one MxN position even thourhg it was double
width, while on the DM41L it is double height.

### The Key Assignment Flags (KEYFLAGS)

The processor keeps 72 flags — one bit per key, per shift state (36 unshifted +
36 shifted, inclkuding the unused key position under the double-sized ENTER key) —
packed as the first 36 bits of register R (unshifted) and the first 36 bits of
register e (shifted) respectively. This is a fast existence check consulted
*before* searching the Key Assignment Registers or scanning global labels.

Bit position for key row `M`, physical column `N` (0-indexed, MSB-first — the
same convention `StatusRegisters.get_flag()`/`set_flag()` use for register d's
56 flags):

    bit = 36 - M - 8*(N-1)

#### LKAOFF (DM41X)

The DM41X's `LKAOFF` function turns off the auto-assignment of local labels A-J to
the top two rows of keys, and a state file saved afterwards records it in these flags
and nowhere else: the bits for keys 11-15 and 21-25 (bits 35, 27, 19, 11, 3 and 34,
26, 18, 10, 2, in R for unshifted and e for shifted) are **cleared**, while every
assignment, whether a Key Assignment Register entry or a global label's key byte,
stays where it was. Since the flags are consulted first, a key whose flag is clear
is treated as unassigned and falls back to the auto-assigned local label; `LKAON`
sets the flags again. Confirmed on a DM41X with `lkaoff3.d41` and `lkaon3.d41`,
which differ in exactly these bits (plan, S2b-S2g). `FAST` and `SLOW` are not in the
state file at all, and the low nibbles of e that move with them are not LKAOFF's.

How the Explorer treats it:

- `KeyAssignments.flag_clear_assignments()` lists the assigned keys whose flag is
  clear, and `is_lkaoff_like()` says whether any of them is in the top two rows. The
  Key Assignments tab shows those assignments as usual, marked ⚠ with a note in the
  header and in the edit dialog, rather than as corruption.
- The flags are never recomputed from the assignments. Only `set_assignment()`,
  `delete_assignment()` and the global-label paths in `program_memory.py` touch
  one key's flag, so loading, editing another key and saving keeps a state LKAOFF.
- Assigning a key whose flag is clear sets the flag, as `ASN` does on the calculator
  (S2f).

### Global Label (User Program) Key Assignments

Assigning a key to a user program (`ASN "PROGLABEL" [key]`) does **not** create
any entry in the Key Assignment Registers. Instead, the key position being
assigned is written directly into the 4th byte of the global label itself — see
`docs/program.md` for the rest of that header's format. A label with no key
assignment has `0x00` in that byte.

### The Lookup Algorithm

From Jarrett's SPME (Section 6B), when a USER-mode key is pressed:

1. Check the corresponding KEYFLAGS bit. If clear, the key isn't
   assigned — run its default (printed) function, or, for a top-row/local-
   label key (A-J, a-e), search the current program for a matching local
   label first.
2. If the bit is set, search the Key Assignment Registers (§4.2) for a
   matching key byte.
3. If nothing is found there, scan every global label in Catalog 1 (from
   `.END.` up to the curtain) for a matching key-assignment byte.
4. If that also comes up empty (not a normal case), fall back to the
   default function.

### Function Byte Codes

These are single HP-41 opcodes; XROM/synthetic functions use two bytes (an
XROM-catalog prefix plus a selector byte — see the byte table's `A0`-`A7` "XROM
n-n+3" rows). The full translation table is `src/memory/functions.py`.

Given an XROM function's catalog number `xrom` (the number before the comma in
`docs/function_table.md`, e.g. `25` for Extended Functions, `26` for Time) and
its selector `fn` (the number after the comma):

    byte1 = 0xA0 + floor(xrom / 4)
    byte2 = ((xrom mod 4) << 6) | fn

This matches Wickes' Table 5-1 examples (`CARD READER 30,00` → `A7 80`,
`PRINTER 29,00` → `A7 40`, `LIST 29,07` → `A7 47`, `PRP 29,13` → `A7 4D`,
`VER 30,05` → `A7 85`)

For example:

    0xC0: F0 A6 82 31 A6 81 21  ->  (A6,82)=XROM 26,02=ALMCAT -> key 14
                                    (A6,81)=XROM 26,01=ADATE  -> key 13
    0xC1: F0 A6 42 11 A6 41 01  ->  (A6,42)=XROM 25,02=ANUM   -> key 12
                                    (A6,41)=XROM 25,01=ALENG  -> key 11

### Notes

- A global label's header holds exactly one key byte (§4.6), unlike a
  physical key's independent unshifted/shifted slots — so assigning a
  program that's already on a different key *moves* it rather than
  creating a second assignment.
- The two storage mechanisms are made mutually exclusive on save: since
  the real lookup order always checks the Key Assignment Registers
  before global labels, letting both point at the same key at once would
  mean the global-label will never fire. `set_key_assignment()`
  now clears any global label pointing at the target key, and
  `set_program_key_assignment()` clears any Key Assignment Register entry
  (and any *other* program) there.

- Import and export of key assignments has not been implemented.
