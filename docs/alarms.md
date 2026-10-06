# Alarms in Memory

Unlike most of the HP41 architecture, there's no documentation (that I could
find) on how alarms are managed, just where they are stored in user memory. The
buffer-level layout, the per-alarm entry format, the fire/reschedule/ expire
lifecycle, the message-length range, alarm TYPE encoding
(message/control/conditional), and a best-effort "past due" marker are all
**confirmed against real DM41L states**, including:
`src/tests/data/alarmtest.dm41`, `src/tests/data/alarmtest2.dm41`,
`src/tests/data/alarmtest3.dm41`, `src/tests/data/repeater.dm41`, and
`src/tests/data/4alarmtest.dm41`. Each memory state built on the previous test
to build an understanding of how alarms are stored.

It has been concerning that there doesn't seem to be any way to definitively
know how the HP41CX/DM41L tell the difference between a repeating and a
one-shot alarm, but it is where we are. Also, note that if an "old" DM41L
state file that contains alarms is loaded into the emulator all elapsed alarms
will be triggered again when the emulator is turned on.

## 1. Sources

- **`src/tests/data/alarmtest.dm41`, `alarmtest2.dm41`, `alarmtest3.dm41`,
  `repeater.dm41`, `4alarmtest.dm41`** — real DM41L states created by the user
  specifically for research into alarms. These are the primary source for
  everything in §3–7 and §9 below.
- **HP-41 Synthetic Programming Made Easy**, Keith Jarett (SYNTHETIX) Chapter 6
  ("On-Line Memory"), Figure 6.2 ("On-Line Memory Usage") and its accompanying
  text. Source for the buffer's overall shape (header register, top delimiter,
  "time plus optional message/repeat" per alarm) before it was checked against
  the real states.
- **HP-41 Advanced Programming Tips**, Alan McCornack & Keith Jarett
  (SYNTHETIX). Contains a full FOCAL program, "SA"/"RA" (Save Alarms / Recall
  Alarms), with line-by-line commentary that independently confirmed the
  header's marker byte and its position before the real-state check.
- **A Programmer's Handbook v2.07**. A microcode/hardware-level reference.
  Documents the physical Timer chip's own "Alarm Register A/B" (a different
  thing from the main-memory alarm buffer — see §8), and its
  1/100-second-since-1900 clock format turned out to be exactly the format used
  by the main-memory alarm entries too (see §4).
- **HP-41CX Owner's Manual, Volume 2** — Section 16 ("Alarm Functions"). Defines
  how different alarm types are entered by the user.

## 2. The Alarm Memory Region

| Region (low → high address) | Notes |
| --- | --- |
| 0x000–0x00F | Status registers |
| 0x040–0x0BF | Extended Memory #0 |
| 0x0C0 upward | Key Assignments — grows upward as entries are added |
| **immediately above Key Assignments** | **Alarms** |
| above Alarms, below `.END.` | Free registers, available for programs, alarms, or key assignments |
| `.END.` up to R00 | User Programs |
| R00 up to 0x1FF | Data Memory |

## 3. Alarm Region Structure

The structure of the Alarm region was derived from studying the alarm memory states.
Here is one of the test cases, `alarmtest3.dm41`:

```
0x0c1 aa 14 00 00 00 00 00 <- Header Register
0x0c2 39 96 23 22 00 01 02 <- First Alarm
0x0c3 00 00 00 36 00 00 00 <- Repeat Interval 
0x0c4 00 00 48 4f 55 52 4c <- 0x00 0x00 "HOURL
0x0c5 59 20 41 4c 41 52 4d <- Y ALARM"
0x0c6 39 96 23 40 00 00 03 <- Second Alarm
0x0с7 00 00 00 00 00 4c 41 <- 0x00*5 "LA"
0x0c8 52 47 45 52 20 41 4c <- "RGER AL"
0x0c9 41 52 4d 20 4d 53 47 <- "ARM MSG"
0x0ca 39 96 23 49 00 00 00 <- Third Alarm
0x0cb 39 96 23 58 00 00 04 <- Fourth Alarm
0x0cc 00 00 00 00 00 4c 41 <- 0x00*5 "LA"
0x0cd 52 47 45 52 20 41 4c <- "RGER AL"
0x0ce 41 52 4d 20 4d 45 53 <- "ARM MES"
0x0cf 53 41 47 45 2c 2c 2c <- "SAGE,,,"
0x0d0 39 96 30 24 00 01 02 <- Fifth Alarm
0x0d1 00 00 08 64 00 00 00 <- Repeat Interval
0x0d2 00 00 00 44 41 49 4c <- 0x00*3 "DAIL"
0x0d3 59 20 41 4c 41 52 4d <- "Y ALARM"
0x0d4 f0 00 00 00 00 00 00 <- Terminator
```

### Header (0xC1): 

| Byte | Meaning |
|--|--|
| 0 | Constant ```0xAA```. Indicates the beginning of the alarms region. |
| 1 | The length of the alarms region, in registers. |
| 2-7 | Unknown. Seems to be always zero. |

### Terminator (0xD4):
| Byte | Meaning |
|--|--|
| 0 | Constant ```0xF0```. Indicates the end of the alarms region. |
| 1-7 | Unknown. Seems to be always zero. |

### Alarms are stored in chronological order

No matter what order alarms are added, they are stored in the order they will
occur, with the first alarm at the lowest address and later alarms in higher
addresses. This means that as alarms are triggered the entire alarm region will
be reorganized as alarms are either deleted or rescheduled. In addition, if
alarms are not properly acknowledged they become "Past Due" and may be
triggered again as defined in the HP-41CX Owner's Manual.

## 4. Per-Alarm Entry Format

Alarms are stored as whole registers, Using at least one register for one-shot
alarms or two registers for repeating alarms. The total length of an alarm will
depend on the length of the alarm message, which is recorded in the LSB of the
alarm time register.

Each alarm is `[time register] + [optional repeat register] + [0-4 message
  register(s)]`.

### Time register

The first 11 nibbles (bytes 0–4.5) are plain BCD decimal digits (0–9 only,
never A–F) forming an 11-digit number in tenths of a second since January 1st,
1900. This is similar to the format documented in the Programmer's Handbook
documents for the timer chip's own CLOCK/ALARM registers (§8). The next nibble
(the low 4 bits of byte 5) indicates a repeating alarm flag (0x00 or 0x01) and
the length of the alarm message / control label. Looking again at
`alarmtest3.dm41`, the time registers are as follows:

| Alarm | Register | Raw | Time | Repeat? | Message Length |
| --- | --- | --- | --- | --- | --- |
| 1 | 0xC2 "HOURLY ALARM" | `39962322000102` | 2026-08-20 16:30:00 | Y | 2 | 
| 2 | 0xC6 "LARGER ALARM MSG" | `39962340000003` | 2026-08-20 17:00:00  | N | 3 | 
| 3 | 0xCA *no message* | `39962349000000` | 2026-08-20 17:15:00 | N | 0 | 
| 4 | 0xCB "LARGER ALARM MESSAGE,,," | `39962358000004` | 2026-08-20 17:30:00  | N | 4 | 
| 5 | 0xD2 "DAILY ALARM" | `39963024000102` | 2026-08-21 12:00:00 | Y | 2 |

For an example of a non-repeating alarm that specifies a 10th of a second resolution:

| Register | Raw | Time | Repeat? | Message Length |
| --- | --- | --- | --- | --- | 
| 0xC2 "TENTHS" | `39975066099001` | 2026-09-04 10:30:00.0 | N | 1 | 

Byte 6 of the time register contains a flag indicating whether the alarm is
past due (0xf vs 0x0) in the high nibble and the message's register count (in
the low nibble):

| Register | Raw | Time | Repeat? | Past Due? | Message Length |
| --- | --- | --- | --- | --- | --- | 
| 0xC2 "HOURLY ALARM" | `399623220001f2` | 2026-08-20 16:30:00 | Y | Y | 2 | 

### Repeat Interval

From the HP-41CX Owners Manual, the largest possible interval is 10,000 hours
(testing indicates that this is actually 9999:59:59.9). This translates to `00
35 99 99 99 90 00`. In all examples, the LSB has been zero. I do not know if
that is significant or not. Looking again at `alarmtest3.dm41` as an example:

| Register | Raw | Interval |
| --- | --- | --- |
| 0xC3 (alarm 1 repeat) | `00 00 00 36 00 00 00` | 3600.00 sec = **1 hour** |
| 0xD3 (alarm 5 repeat) | `00 00 08 64 00 00 00` | 86400.00 sec = **1 day** |

The 3rd alarm has no message, and is a one-time alarm, so it has no repeat
register at all.

### Message / Control Label

As previously indicated, messages are stored with pre-pended NULL bytes to fill
up one or more complete registers. The maximum length of a message is 24
characters. For example:

```
0x0cc 00 00 00 00 00 4c 41 <- "LA"
0x0cd 52 47 45 52 20 41 4c <- "RGER AL"
0x0ce 41 52 4d 20 4d 45 53 <- "ARM MES"
0x0cf 53 41 47 45 2c 2c 2c <- "SAGE,,,"
```

```
0x0d2 00 00 00 44 41 49 4c <- "DAIL"
0x0d3 59 20 41 4c 41 52 4d <- "Y ALARM"
```
