# 5 Program Memory

The following are the author's own notes on trying to build a catalog of the
programs contained in a DM41L state file.

## 5.1 Traversing Program Memory

The list of programs in an HP41/DM41L is traversed backwards, by starting at
the register pointed to by .END. and progressing to higher addresses.

### From HP-41 Synthetic Programming by Jonathan Wickes:

>We have come to the 'END'. The bytes CO-CD, 'GLOBAL', play a dual role--they
identify both 'END' lines and global alpha labels. If the third byte of a line
starting with 'Cn' (0<=n<E) is a text byte 'Fn', then the line is a global alpha
label. Otherwise, it is a three- byte 'END'. For both types of lines, the
second, third and fourth nybbles give the distance from the current line to the
next 'END' or alpha label preceding in memory. The distance is coded as in the
three-byte GTO's (Section 2C). Thus, all the global Tines are linked together;
a GTO-alpha or XEQ-alpha starts searching the global chain from the end of
program memory, the permanent .END., backwards to the first global line in
memory, which is identified by its first two bytes 'CO 00'. 'CAT 1' shows the
labels and END's in order forward from the first global line. In 'END' lines,
the third byte is used to provide information about the current program
--whether it has been packed and whether it is the last program in memory,
i.e., if the END is the permanent .END. In the third byte, a first nybble 'O'
indicates a normal END; a '2' identifies the permanent .END. For the second
nybble, '9' means that the program file is packed, 'D' indicates that the file
needs packing. ...

Reviewing the relevant section, we can see that END instructions will take the form:

`1100 bbbr rrrr rrrr eeee ffff`

Where '1100' is the bytecode for indicating and END or LBL instruction, 'r rrrr
rrrr' is the distance in registers, and 'bbb' is 0-6 additional bytes. Note
that this is not simply "go rrrrrrrrr registers and then bbb bytes", the actual
distance is the # of registers times 7 plus bbb, from the current HP41
instruction pointer. For an END instruction that means you count from the last
byte of the END instruction. For global labels, I believe it is from the 3rd
byte of the label (i.e., the Fn byte).

The third byte ('eeee ffff' above) is explained a little further
on in the same section of Wickes (2C, p.15). For a plain END line, the high nibble ('eeee') is `0` for a
normal END or `2` for the permanent `.END.`; the low nibble ('ffff') is `9`
if the program file has been packed, or `D` if it needs packing. For a global
label line, the high nibble is always `F` — that's exactly the marker Wickes
uses to distinguish a label from a plain END — and the low nibble gives the
label's name length, plus one. See §5.2 below for the full label format.

### Addressing within program memory

When dealing with a memory state, register offset and absolute address run in
*opposite* directions within a register. (That is, in an actual calculator
register, byte 0 is the LSB, but in DM41_Explorer, Register._data\[0\] is the
MSB of the register.) This can be confusing. If `reg` is a register's decimal
value and `offset` is the byte position counting left-to-right through its
printed hex (0 = the first/ leftmost byte, 6 = the last/rightmost byte), then:

```
address(reg, offset) = 7*reg + (6 - offset)
```

To state the issue in another way, offset 0 (the first printed byte) is at the
*highest* address within that register; offset 6 (the last printed byte) is at
the *lowest*. This matches Wickes' own description in 2C: HP41 "byte 6" (offset
0 here) is a register's first byte and "byte 0" (offset 6 here) its last, and
stepping forward through a program decrements the byte number before
decrementing the register number. Every "distance in registers and bytes"
calculation below works out to: take the address of the byte the distance is
measured from, add `r*7 + bbb`, and convert the result back to (register,
offset) with the same formula. Worked out this way, every hop in the examples
below lands exactly on the next instruction, byte for byte — verified directly
against `simple.dm41` and `6x-xm.dm41`.

**Example: "empty.dm41"**

```
DM41
08  4b000000000000  00000000000000  00000000000000  00000000000000  
0c  1000000000019c  1a70016919c19b  0000002c048000  00000000000000  
198  00000000000000  00000000000000  00000000000000  00000000c00020  
...
```

This is a state of a DM41L that has just been initalized; there are no programs
in memory. ("empty.dm41" in the tests/data folder) R00 is 19c and .END. is 19b. The END instruction appears in the last 3 bytes of register 19b: 'c0 00 20'
which appears to encode a distance of 0 registers and 0 bytes to the next END.

**Example: "simple.dm41"**

```
DM41
00  01000000000000  01000000000002  01000000000002  01000000000002
04  01000000000000  00000000000000  00000000000000  00000000000000
08  4b020000000000  7100fffffff0ff  00000000001000  00000000000000
0c  00000000002198  1a70016919c197  0000002c048000  00000000007000
194  00000000000000  00000000000000  00000000000000  00000000c40120
198  b200c403090000  40111010468475  54455354100211  c000f800415050
...
```

This example contains a single program, called "APPTEST" (26 bytes), in an
otherwise empty DM41L emulator.

In this state, R00 is register 19c and .END. is register 197. Register 197
contains "00 00 00 00 c4 01 20" which decodes like this:

| Instruction | bbb | rrrrrrrrr | eeee ffff | 
|-|-|-|-|
| 1100 | 010 | 0 0000 0001 | 0010 0000 |
| END | 2 bytes | 1 register | .END. |

**Total distance = 1 register + 2 bytes**, so 9 bytes counting from the first byte
of the end instruction. That takes us to another END instruction: "c40309".
Decoding that:

| Instruction | bbb | rrrrrrrrr | eeee ffff | 
|-|-|-|-|
| 1100 | 010 | 0 0000 0011 | 0000 1001 |
| END | 2 bytes | 3 registers | Packed End | ? |

**Total distance = 3 registers + 2 bytes**, 23 bytes, counting from the first byte
of the END instruction takes us to the MSB of register 19b: "c0 00 f8 00 41 50
50", the beginning of the global label of the program. See §5.2 below for how
to decode this into the name "APPTEST" (see "Addressing within program memory"
above for why).

**Example: "6x-xm.dm41"**

```
DM41
...
0c  00000000001189  1a70016919c188  0000023c048008  00000000007000
...
188  00000000c20120  5fb100c6020900  584d0111a674a6  c600f621505552
18c  4f4e457ecc0609  449600b2b1f444  30870220a66fa6  4a16141a101916
190  504841111218a6  4841f7584d414c  f811584d414c50  457ec40809c600
194  01b100f4444f4e  a6689600b20096  10113002101a10  4b16141a101915
198  21689b731312a6  10113101f2584d  4344111a101014  c000f601584d42
...
```


This example is a part of a larger state that contains three apps, called "XMBCD",
"XMALPHA", and "PURXM". R00 is 19c (which seems to be the default for the DM41L) and .END. is set to register 188. Looking at register 188 we find "00 00 00 00 c2 01 20". (In all samples, .END. is always found in the last 3 bytes of a register, even if that means padding it with null bytes.)

| Instruction | bbb | rrrrrrrrr | eeee ffff | 
|-|-|-|-|
| 1100 | 001 | 0 0000 0001 | 0010 0000 |
| END | 1 bytes | 1 register | .END. |

This takes us to register 189 and the next END is "c6 02 09"

| Instruction | bbb | rrrrrrrrr | eeee ffff | 
|-|-|-|-|
| 1100 | 011 | 0 0000 0010 | 0000 1001 |
| END | 3 bytes | 2 register | Packed End |

2 * 7 + 3 = 17 bytes. 17 bytes from the c6 instruction takes us register 18b and "c600f621505552"
which is the label of the program "PURXM". c600 translates to 3 bytes, which takes us to "cc0609" in register 18c:

| Instruction | bbb | rrrrrrrrr | eeee ffff | 
|-|-|-|-|
| 1100 | 110 | 0 0000 0110 | 0000 1001 |
| END | 6 bytes | 6 registers | Packed End |

This continues the chain further back toward XMALPHA and XMBCD.

### 5.2 Decoding a Global Label's Name

Once the chain above lands on an entry whose third byte is `Fn` (high nibble
`F`) rather than a plain END, that entry is a global label, not just a
chain link. Wickes (2C, p.15) gives the exact byte layout:

`LBL "ABC" = C1 mn F4 ab 41 42 43`

- Byte 0: `Cn` — the same marker byte as an END; `n` is part of the shared
  chain-distance field described above.
- Byte 1: `mn` — the rest of that chain-distance field.
- Byte 2: `Fn` — high nibble `F` marks this as a label (not an END); low
  nibble `n` is the name's length **plus one**.
- Byte 3: the assigned-key code (`00` = no key assigned; `ab` in the example
  above is a placeholder for a real key code).
- The remaining `n - 1` bytes spell out the name, in plain forward reading
  order, starting right after the key-code byte.

For a 3-character name like "ABC" this is exactly 7 bytes — one whole
register, header and name together, as in Wickes' example. For a longer
name, the header's own register only has 3 bytes left after the 4-byte
header (offsets 4, 5, 6), so the name overflows into the **preceding**
(lower-address) register, starting at *its* offset 0, for up to 4 more
characters — continuing a register further back if the name is longer
still.

Worked examples, both confirmed against the real calculator's CAT 1 listing:

- **XMBCD** (`6x-xm.dm41`): header at register 19b, offset 0: `c0 00 f6 01`.
  `f6` → length 5. Name chars: offset 4-6 of 19b = "XMB", + offset 0-1 of
  19a = "CD" → **XMBCD**.
- **PURXM** (`6x-xm.dm41`): header at register 18b, offset 0: `c6 00 f6 21`.
  `f6` → length 5. Name chars: offset 4-6 of 18b = "PUR", + offset 0-1 of
  18a = "XM" → **PURXM**.
- **APPTEST** (`simple.dm41`, the "single small program" example above):
  header at register 19b, offset 0: `c0 00 f8 00`. `f8` → length 7. Name
  chars: offset 4-6 of 19b = "APP", + offset 0-3 of 19a = "TEST" →
  **APPTEST**.

### 5.3 Grouping the Chain Into Real Programs

Sections 5.1/5.2 walk the raw "global chain" -- every global label header
and every plain END marker, one link at a time. That chain is NOT the same
thing as "the list of programs": a program is not required to have a
global label at all (it can consist of nothing but local, numbered labels,
or none whatsoever), and it can have more than one. **A program is
delimited by an explicit plain END marker, never by a label.** The one
exception is the single *newest* program in memory: it does not need an
explicit END of its own, because the permanent `.END.` sentinel (§5.1) can
serve as its terminator instead. Every *older* program, by construction,
must have a real END of its own, since nothing else could have closed it
out while a newer program was added after it.

**The grouping rule**, walking the chain oldest to newest (implemented by
`ProgramMemory.list_programs()`, returning `Program`/`ProgramLabel` objects --
`src/memory/program_info.py`):

1. Every global label encountered is added to the label list of whatever
   program is currently being accumulated (there may be zero, one, or
   several before the next boundary).
2. Every plain END (high nibble `0`) always closes a real program --
   whatever labels (if any) have been accumulated since the last boundary,
   plus everything back to that boundary, form one program ending at this
   END's own bytes. A fresh, empty label list starts for whatever comes
   next.
3. The permanent `.END.` (high nibble `2`) is always the last entry in the
   chain, and is handled specially:
   - If any labels are pending (nothing has closed them out yet), `.END.`
     really is this program's own terminator.
   - Otherwise, check whether every byte between wherever the last real
     program left off and `.END.`'s own marker is zero. If so, that's
     nothing but register-alignment padding -- not a program.

Every existing sample state in `src/tests/data/` that has a trailing gap
before `.END.` turns out to be pure zero-padding once checked this way.

### 5.4 Removing a Program, and Packing

Both operations are implemented in terms of a single shared primitive,
`ProgramMemory._rebuild()`, rather than any new low-level marker
arithmetic of their own:

1. Capture every program to be kept (`ProgramMemory.get_program_bytes()`, oldest
   first) plus, for each of its labels, whatever key assignment (§4.6) it
   currently holds -- `import_program()` always zeroes a freshly-spliced
   label's own key-assignment byte (see its own docstring, step 4), so
   this has to be restored afterward rather than assumed preserved.
2. Zero every register from `alarms_end()` up to (not including) `R00()`
   and set `.END.` to `R00()` itself -- `list_global_chain()`'s own
   definition of "no programs at all yet" (its `dend < r00` check).
3. Re-`import_program()` each captured program in order, restoring its
   labels' key assignments (but not touching KEYFLAGS -- §4.5 -- which
   live in a separate register and were never cleared in the first
   place).
4. Best-effort cleanup (`remove_program()` only -- `pack()` deliberately does
   NOT do this, see "What a real PACK does" below): `import_program()`'s own step 8 always writes a
   *separate*, freshly register-aligned `.END.` sentinel after whatever
   it just imported, even when that program is the last one being
   rebuilt -- leaving it `terminator == "END"` rather than the more
   compact `".END."` a real newest program doesn't strictly need (see
   `Program`'s own docstring). Whenever that redundant sentinel's
   predecessor marker happens to already sit register-aligned at offset 4
   of its own register (i.e. it would have been a legal `.END.` position
   on its own), `_collapse_trailing_end_into_dot_end()` rewrites it in
   place as the permanent `.END.` and reclaims the now-superfluous
   sentinel's register(s). When it isn't aligned, this step is skipped --
   the result is still correct, just not maximally compact, the same
   tradeoff every single `import_program()` call already accepts.

#### ProgramMemory.remove_program(program)

`ProgramMemory.remove_program(program)` calls this for the remaining
programs. This matters for more than just the newest program: the oldest
program in memory always starts at a fixed address (`_program_memory_top_
addr()`, right below `R00()`), so deleting anything older than the newest can't
just erase bytes in place -- without a rebuild, the freed space would sit
*above* wherever `.END.` making the registers of the removed program
unreachable. Also clears the KEYFLAGS bit (§4.5) for any of the removed
program's own labels that held a key assignment, since its header is gone and
`get_program_for_key()` can never find it there again.

#### Memory.pack()

`Memory.pack()` calls `_forward_scan_programs()` (below),
reclaiming any incidental register-alignment drift the same way. It also
explicitly re-runs the Key Assignments/Alarms repack
(`_encode_key_assignment_entries()`). This is a no-op for assignments that have
already been edited, but will close any gaps that had accumulated in a state
that was loaded from a DM41L. current as a side effect -- a no-op for a state
this app has only ever edited itself, but self-healing for one that wasn't.
Returns the number of registers reclaimed (`DotEnd() - alarms_end()`'s
increase), 0 if nothing needed packing; idempotent (a second `pack()` call
always returns 0) across every sample state in `src/tests/data/`. It is
negative (-1) only when a newest program was closed by `.END.` directly and
now needs a real END plus an empty `.END.` -- see below. See
`src/tests/test_pack.py` and `src/tests/test_pack_hardware.py`.

#### What a real PACK does

Established from four before/after pairs captured on real hardware
(`manyfiles` and `lander`/`targ` on a DM41L, `dm41x_pack_dotend` on a DM41X,
each with a `-packed` twin in `src/tests/data/`) and reproduced exactly by
`pack()`, apart from the jump caches (rule 2):

1. **NULLs.** Every standalone `0x00` instruction is deleted, except one
   sitting between two number-entry instructions (`0x10`-`0x1C`), which is
   what keeps "12345 NULL 67890" two numbers. Zeros that are part of an
   instruction (`FIX 0` is `9C 00`, `ISG 00`, a label header, a character
   inside an ALPHA string) are never touched. manyfiles.dm41: 47 bytes
   deleted, `.END.` moved up 7 registers.
2. **Jump caches.** A GTO/XEQ's cached jump distance (§ Compact/General
   GTO forms in `program_text.py`) is stale once bytes move. A real PACK
   resolves it again or clears it, and the calculator re-resolves it the next
   time the program runs; `pack()` clears every one
   (`opcode_scan.clear_jump_caches()`): the second byte of `B1`-`BF`, and in
   the 3-byte `D0`-`EF` forms the low nibble of byte 1, all of byte 2 and
   the direction bit of byte 3. A real PACK leaves a cache alone when nothing
   moved, so the tests treat these bytes as don't-cares.
3. **END flags and chain.** Every END is marked packed (third byte low nibble
   `9`, versus `D`/`F` for "needs packing") and the chain is re-linked.
4. **The newest program is always closed by a real END**, placed immediately
   after its last instruction, followed by the permanent `.END.` as a
   separate, empty marker (`C? ?? 20`) on the next register boundary -- even
   when the source closed the program with `.END.` directly
   (`dm41x_pack_dotend`, `targ`, `lander`). This can cost a register
   (targ: 79 -> 80), and a packed buffer packs to itself.

`twolabels.dm41` has the same shape as `dm41x_pack_dotend.dm41` (`.END.`
closing the program, flagged `2D`); a real DM41L PACK of it
(`twolabels-packed.dm41`) came out byte-for-byte what `pack()` produces. A
real PACK of an already-packed file (`manyfiles-repacked.dm41`) changes no
program byte, including cached jumps, which is why `pack()` is idempotent
apart from clearing them.

Not reproduced: a real PACK also moves the program counter (status register
`b`) along with whatever it pointed at, e.g. `twolabels`: `…4198` ->
`…1198`, from the start of the old `.END.` to the start of the END that
replaced it. `pack()` leaves `b` alone.
Checked on a DM41L: `manyfiles.dm41` packed by the Explorer (so `.END.` moves 7
registers with `b` left stale) loads and CAT 1 lists its programs correctly.
Stepping through the programs (SST) after such a load has not been tried.

#### Rebuilding the chain, not just compacting it.

`ProgramMemory._forward_scan_programs()` reads
program memory as one flat span, from `_program_memory_top_addr()` (the oldest
program's fixed, unmovable start) down to `.END.`'s own floor
(`_addr_for(DotEnd(), 6)`) -- trusting `R00()`/`DotEnd()` themselves as sane
boundary pointers, uses`opcode_scan.scan_global_markers_forward()` to
explicitly traverse the entire memory region, and records every marker (label
or END) it passes, in physical/forward-discovery order, using none of their
`bbb`/`distance_registers` fields at all -- only their physical position.

That information is then used to rebuild the chain: for every
marker found, `_forward_scan_programs()` rewrites its own `bbb`/
`distance_registers` fields, in a local working copy, to the true byte
distance back to whichever marker was found immediately before it (zero
for the very first marker in the whole span -- "no predecessor", same as
an empty memory). This matters even for markers *inside* one program, not
just the boundary between programs: `import_program()` (used to actually
re-splice each program found here into a rebuilt memory) trusts
`program_chain.walk_chain()` to find every label embedded within one
program's own bytes by following that program's own internal backlinks,
so if those were broken too, simply slicing the raw bytes out unchanged
and re-importing them would silently reproduce the same invisible-label
problem in the rebuilt memory. Repairing every link first, before
grouping the (now internally self-consistent) per-program byte ranges,
avoids that.

Because this only trusts `R00()`/`DotEnd()` and the physical opcode
bytes -- nothing about the existing chain -- it raises `DM41MemoryError`
(and changes nothing) rather than guessing wherever it cannot be sure it
has found every real program without risking silently dropping one: if
real (non-zero) data is present but no marker at all can be found in it,
if the very last marker found is a label with nothing closing it, or if
non-zero bytes remain between the last marker found and `DotEnd()`'s own
floor. See `src/tests/test_pack.py`'s own corruption tests for each case.
