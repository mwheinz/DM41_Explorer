# DM41L Explorer: Hardware Memory Architecture Reference

The purpose of the DM41L Explorer project is to design a python application
that can read, write, and edit the memory of the DM41L emulator via the DM41L's
serial port. This document outlines the memory architecture of the original
HP41C calculator hardware and it's implementation in the DM41L. 

Note on limitations: The DM41L specifically emulates the HP41CX calculator,
which was an upgraded version of the HP41C and HP41CV models. The HP41C* series
could be expanded with various ROM and RAM cartridges. The HP41CX had the
maximum amount of main memory and the equivalent of a time functions ROM
cartridge, an extended functions cartridge, and an extended memory cartridge
already installed.

A note on confidence: most of this document describes hardware behavior
confirmed against real memory dumps (see `src/tests/data/*.dm41`), or is
derived from published documentation.

A note on sources: Much of this data was derived from "HP-41 Advanced
Programming Tips" by Alan McCornack & Keith Jarett, "HP41 Extended Functions
Made Easy", "HP41 Synthetic Programming" by Jonathan Wickes, and other
documentation found on the internet.

## 1. Core Architectural Specifications
- **Target Model:** DM41L which emulates the HP41CX calculator.
- **Memory Hardware:** The HP41 series organized its memory into 7-byte (56-bit)
  registers.
- **Instruction Set:** Operates on a variable-length instruction set where
  opcodes are between **1 and 3 bytes** long.
- **Execution Model:** When the HP41 series was developed, many modern computer
  architecture conventions had not yet stablized. HP41 programs
  employs an execution model where the instruction pointer moves
  from higher memory addresses to lower memory addresses ($N \rightarrow 0$).
  This explains why program termination sequences (like ".END.") appear at
  relatively low register indices in dumps, while the entry point resides in
  high memory. This same high-to-low convention governs files in Extended
  Memory (see §4), but not user data stored in main memory.

### 1.1 Reading Direction Quick Reference

Several regions grow toward *lower* addresses as more gets added to them, so
the newest/most-recent content ends up at the low-address end rather than
the high-address end a normal top-to-bottom dump printout would suggest.
The exact flip differs by region, so don't assume one region's convention
applies to another — check this table first:

| Region | Across registers, newest/forward is... | Within a register, forward is... |
| --- | --- | --- |
| Program Memory | Toward *lower* register numbers — continuing at the next lower register once you run off the bottom of the current one (see `docs/program.md`'s `address(reg, offset)` formula) | Left-to-right (offset increasing) — normal reading order |
| Key Assignments (§3, Main Memory) | Toward *lower* register numbers — a brand-new assignment always lands in the lowest register, 0xC0, pushing everything else up | Right-to-left — Each assignment consumes 3 bytes of a register, and each register has an F0 in the MSB and zero, one, or two key assignments. The entry closest to the `F0` marker byte is always the newer of a register's two entries. If a register has only one key assignment, it can be either in the left or right half of the register. |
| Extended Memory (§4) | Toward *lower* register numbers within a region — files pack from the region's top (highest address) downward in creation order | Left-to-right (normal) — see §4.2's "nearest-header-first" record packing |
| Data Memory (Register 00, 01, 02...) | Toward *higher* register numbers — the one region that reads normally: Register 00 sits at R00, Register 01 at R00+1, etc. | Left-to-right (normal) |

Key Assignments is the one region where the within-register direction also
flips, not just the across-register one — see the Key Assignments entry in
§3's Main Memory table below for the byte format this comes from.

## 2. Memory Register Structure and Data Formats

### Word Size:
- Every hardware register is exactly **7 bytes** (56 bits) long. Registers can
  contain numeric data, text data, or program instructions.

### Numeric data
1. Values are stored in **Binary Coded Decimal (BCD)**. The MSB is in byte 0 of
   the register, the LSB is in byte 6.
2. **Mantissa Sign (MS):** 1 nybble (4 bits). Stored in bits 4-7 of the MSB.
   0000 for positive, 1001 for negative.
3. **Mantissa:** The next 10 nybbles of the register represent 10 decimal
   digits. This represents normalized values with fixed 10-digit precision.
4. **Exponent Sign (XS):** 1 nybble (4 bits). 0000 for positive, 1001 for
   negative.
5. **Exponent:** 2 decimal digits packed into 2 nybbles. Provides a range of
$10^{-99}$ to $10^{99}$.
   - **Negative Exponent Convention:** When the XS nibble indicates a negative
     exponent, the stored BCD value follows a complementary rule where the
     value represents $100 - |E|$, where $E$ is the true exponent. For example,
     an exponent of $-5$ is stored as $95$.

### Alpha Storage:
- A register in main memory holding ASCII data will begin with 0x10 in the MSB
  and can hold up to 6 ASCII characters. If the ASCII data is less than 6
  characters long, it will be prepended with one or more NUL bytes until the
  data is 6 characters long.

### Program instructions
- instructions are read from registers as bytes and are read from higher
  addresses to lower ones.

## 3. Memory Architecture

The calculator's memory is divided into regions where the hardware registers
are interpreted differently:

| Region | Description | Start | End |
| ----------- | ----------- | ----------- | ----------- | 
| Status Registers | Operational registers for the HP41/DM41L, including the math stack, Alpha Register and system pointers | 0x00 | 0x0F |
| Void Region | Registers do not exist / not used. | 0x10 | 0x3F |
| Extended Memory #0 | File-oriented memory region | 0x40 | 0xBF |
| Main Memory | Main memory consists of 319 registers that are subdivided into variable length sub-regions. | 0xC0 | 0x1FF |
| Extended Memory #1 | File-oriented memory region. | 0x200 | 0x2EF |

An additional memory region may exist in actual HP41CV and HP41CX calculators
if an additional Extended Memory module is installed, but this is not
reflected in the DM41L emulator.

### 3.1 Status Registers

| Status Registers | Description | Address |
|---|---|---|
| T | Top of the Math Stack | 0x00 |
| Z | Math Stack Register Z | 0x01 |
| Y | Math Stack Register Y | 0x02 |
| X | Math Stack Register X | 0x03 |
| L | Last X (additional Math Register) | 0x04 |
| Alpha Register | 3.5 registers comprising the HP41 Alphanumeric display | 0x05-0x08 |
| M | Alpha characters 1-7 | 0x05 |
| N | Alpha characters 8-14 | 0x06 | 
| O | Alpha characters 15-21 | 0x07 | 
| P | Alpha characters 22-25: P[0:3], scratch: P[4-6] | 0x08 |
| Q | scratch | 0x09 |
| R | Some sources call this "F" or "Append" (it is displayed as the append symbol). unshifted key assignment bitmask: R[0:3] plus the high nibble of R[4] (36 bits), scratch: low nibble of R[4], R[5:6] | 0x0a |
| Execution Stack | 2 registers that provide a 6-level address stack. Each entry in the stack is 2 bytes long. | 0x0b-0x0c |
| a | return stack part 2 | 0x0b |
| b | return stack part 1 | 0x0c | 
| c | Contains ∑REG, R00, and ".END." | 0x0d |
| d | User and System flags. | 0x0e | 
| e | shifted key assignment bitmask: e[0:3] plus the high nibble of e[4] (36 bits), scratch: low nibble of e[4], LineNo: e[5:6] | 0x0f |

### 3.2 HP41 Alpha display:

The alpha display of the HP41 series and the DM41L emulator is actually composed
of Status Registers M, N, O, and part of register P. The user sees this as a
continuous buffer of 24 ASCII characters but the physical memory is still
comprised of 7-byte registers.

### 3.3 Main Memory

| Main Memory Sub-Region | Description | Start | End |
| ----------- | ----------- | ----------- | ----------- | 
| Key assignments | User defined key assignments to built-in/peripheral functions (see below) | 0xC0 | variable |
| Alarms | User defined alarms. Not well documented. | After Key Assignments | variable |
| User Programs | Space reserved for user programs. Programs begin at high addresses and proceed to lower addresses as they execute. | .END. | R00 |
| Data Memory | Space reserved for user data. | R00 | 0x1FF |  

- R00 contains the address of the first data memory register
  (which the user sees as Register 00). Unlike application
  instructions and XM files, main data memory registers begin
  at a low address and go UP towards the end of main memory. In
  other words, what the user sees as "Memory 00" is at address
  R00, "Memory 01" is at R00+1, "Memory 02" is at R00+2, et
  cetra. The highest main memory location the user can access
  will always be at address 0x1ff.

- ".END." indicates the end of the currently loaded user
  programs. The registers between ".END." and the last Alarm,
  Key Assignment, or address 0x0C0 are available for use by
  additional programs, alarms, or key assignments.

#### Key Assignment Registers

Key Assignments aare explained further in `docs/key_assignments.md`.

#### Alarms

These are explored further in `docs/alarms.md`.

#### Program Memory

HP41 programs begin at the register pointed to by R00-1 and proceed down
through memory. The lowest register used by programs will be pointed to by
.END. and will contain a 3-byte instruction that is also called ".END.".

This is explained further in `docs/program.md`.

### 3.4 Extended Memory

In an actual HP41 series calculator, extended memory may or may not be present.
The DM41L emulates the presence of an "Extended Functions" module which
occupies registers 0x040-0x0bf and a single "Extended Memory" module which
occupies registers 0x200-0x2ef. This is explained further in `docs/extended_memory.md`
