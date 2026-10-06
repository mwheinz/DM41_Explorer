The following table shows HP41 instruction codes as they appear in programs
("Instruction Prefix") and as function codes ("Function"). In addition it
indicates whether the code can be assigned to a keystroke.

# Primary functions

| Dec | Hex | Instruction Prefix | Instruction Length | Function | Assignable? |
|--|--|--|--|--|--|
| 000 | 0x00 | NULL | 1 | CAT | Yes |
| 001 | 0x01 | LBL 00 | 1 | GTO.. | No |
| 002 | 0x02 | LBL 01 | 1 | DEL | Yes |
| 003 | 0x03 | LBL 02 | 1 | COPY | Yes |
| 004 | 0x04 | LBL 03 | 1 | CLP | Yes |
| 005 | 0x05 | LBL 04 | 1 | R/S | No |
| 006 | 0x06 | LBL 05 | 1 | SIZE | Yes |
| 007 | 0x07 | LBL 06 | 1 | BST | Yes |
| 008 | 0x08 | LBL 07 | 1 | SST | Yes |
| 009 | 0x09 | LBL 08 | 1 | ON | No |
| 010 | 0x0A | LBL 09 | 1 | PACK | Yes |
| 011 | 0x0B | LBL 10 | 1 |  | No |
| 012 | 0x0C | LBL 11 | 1 |  | No |
| 013 | 0x0D | LBL 12 | 1 |  | No |
| 014 | 0x0E | LBL 13 | 1 | SHIFT | No |
| 015 | 0x0F | LBL 14 | 1 | ASN | Yes |
| 016 | 0x10 | "0" | 1 |  | No |
| 017 | 0x11 | "1" | 1 |  | No |
| 018 | 0x12 | "2" | 1 |  | No |
| 019 | 0x13 | "3" | 1 |  | No |
| 020 | 0x14 | "4" | 1 |  | No |
| 021 | 0x15 | "5" | 1 |  | No |
| 022 | 0x16 | "6" | 1 |  | No |
| 023 | 0x17 | "7" | 1 |  | No |
| 024 | 0x18 | "8" | 1 |  | No |
| 025 | 0x19 | "9" | 1 |  | No |
| 026 | 0x1A | "." | 1 |  | No |
| 027 | 0x1B | EEX | 1 |  | No |
| 028 | 0x1C | NEG | 1 |  | No |
| 029 | 0x1D | GTO (alpha) | Var |  | No |
| 030 | 0x1E | XEQ (alpha) | Var |  | No |
| 031 | 0x1F | SPARE | Var |  | No |
| 032 | 0x20 | RCL 00 | 1 |  | No |
| 033 | 0x21 | RCL 01 | 1 |  | No |
| 034 | 0x22 | RCL 02 | 1 |  | No |
| 035 | 0x23 | RCL 03 | 1 |  | No |
| 036 | 0x24 | RCL 04 | 1 |  | No |
| 037 | 0x25 | RCL 05 | 1 |  | No |
| 038 | 0x26 | RCL 06 | 1 |  | No |
| 039 | 0x27 | RCL 07 | 1 |  | No |
| 040 | 0x28 | RCL 08 | 1 |  | No |
| 041 | 0x29 | RCL 09 | 1 |  | No |
| 042 | 0x2A | RCL 10 | 1 |  | No |
| 043 | 0x2B | RCL 11 | 1 |  | No |
| 044 | 0x2C | RCL 12 | 1 |  | No |
| 045 | 0x2D | RCL 13 | 1 |  | No | 
| 046 | 0x2E | RCL 14 | 1 |  | No |
| 047 | 0x2F | RCL 15 | 1 |  | No |
| 048 | 0x30 | STO 00 | 1 |  | No |
| 049 | 0x31 | STO 01 | 1 |  | No |
| 050 | 0x32 | STO 02 | 1 |  | No |
| 051 | 0x33 | STO 03 | 1 |  | No |
| 052 | 0x34 | STO 04 | 1 |  | No |
| 053 | 0x35 | STO 05 | 1 |  | No |
| 054 | 0x36 | STO 06 | 1 |  | No |
| 055 | 0x37 | STO 07 | 1 |  | No |
| 056 | 0x38 | STO 08 | 1 |  | No |
| 057 | 0x39 | STO 09 | 1 |  | No |
| 058 | 0x3A | STO 10 | 1 |  | No |
| 059 | 0x3B | STO 11 | 1 |  | No |
| 060 | 0x3C | STO 12 | 1 |  | No |
| 061 | 0x3D | STO 13 | 1 |  | No |
| 062 | 0x3E | STO 14 | 1 |  | No |
| 063 | 0x3F | STO 15 | 1 |  | No |
| 064 | 0x40 | + | 1 | + | Yes |
| 065 | 0x41 | - | 1 | - | Yes |
| 066 | 0x42 | * | 1 | * | Yes |
| 067 | 0x43 | / | 1 | / | Yes |
| 068 | 0x44 | X<Y? | 1 | X<Y? | Yes |
| 069 | 0x45 | X>Y? | 1 | X>Y? | Yes |
| 070 | 0x46 | X≤Y? | 1 | X<=Y? | Yes |
| 071 | 0x47 | Σ+ | 1 | Σ+ | Yes |
| 072 | 0x48 | Σ- | 1 | Σ- | Yes |
| 073 | 0x49 | HMS+ | 1 | HMS+ | Yes |
| 074 | 0x4A | HMS- | 1 | HMS- | Yes |
| 075 | 0x4B | MOD | 1 | MOD | Yes |
| 076 | 0x4C | % | 1 | % | Yes |
| 077 | 0x4D | %CH | 1 | %CH | Yes |
| 078 | 0x4E | P→R | 1 | P-R | Yes |
| 079 | 0x4F | R→P | 1 | R-P | Yes |
| 080 | 0x50 | LN | 1 | LN | Yes |
| 081 | 0x51 | X↑2 | 1 | X↑2 | Yes |
| 082 | 0x52 | SQRT | 1 | SQRT | Yes |
| 083 | 0x53 | Y↑X | 1 | Y↑X | Yes |
| 084 | 0x54 | CHS | 1 | CHS | Yes |
| 085 | 0x55 | E↑X | 1 | E↑X | Yes |
| 086 | 0x56 | LOG | 1 | LOG | Yes |
| 087 | 0x57 | 10↑X | 1 | 10↑X | Yes |
| 088 | 0x58 | E↑X-1 | 1 | E↑X-1 | Yes |
| 089 | 0x59 | SIN | 1 | SIN | Yes |
| 090 | 0x5A | COS | 1 | COS | Yes |
| 091 | 0x5B | TAN | 1 | TAN | Yes |
| 092 | 0x5C | ASIN | 1 | ASIN | Yes |
| 093 | 0x5D | ACOS | 1 | ACOS | Yes |
| 094 | 0x5E | ATAN | 1 | ATAN | Yes |
| 095 | 0x5F | →DEC | 1 | DEC | Yes |
| 096 | 0x60 | 1/X | 1 | 1/X | Yes |
| 097 | 0x61 | ABS | 1 | ABS | Yes |
| 098 | 0x62 | FACT | 1 | FACT | Yes |
| 099 | 0x63 | X≠0 | 1 | X≠0? | Yes |
| 100 | 0x64 | X>0 | 1 | X>0? | Yes |
| 101 | 0x65 | LN1+X | 1 | LN1+X | Yes |
| 102 | 0x66 | X<0? | 1 | X<0? | Yes |
| 103 | 0x67 | X=0? | 1 | X=0? | Yes |
| 104 | 0x68 | INT | 1 | INT | Yes |
| 105 | 0x69 | FRC | 1 | FRC | Yes |
| 106 | 0x6A | D→R | 1 | D-R | Yes |
| 107 | 0x6B | R→D | 1 | R-D | Yes |
| 108 | 0x6C | →HMS | 1 | HMS | Yes |
| 109 | 0x6D | →HR | 1 | HR | Yes |
| 110 | 0x6E | RND | 1 | RND | Yes |
| 111 | 0x6F | →OCT | 1 | OCT | Yes |
| 112 | 0x70 | CL Σ | 1 | CLΣ | Yes |
| 113 | 0x71 | X<>Y | 1 | X<>Y | Yes |
| 114 | 0x72 | PI | 1 | PI | Yes |
| 115 | 0x73 | CLST | 1 | CLST | Yes |
| 116 | 0x74 | R↑ | 1 | R↑ | Yes |
| 117 | 0x75 | RDN | 1 | RDN | Yes |
| 118 | 0x76 | LASTX | 1 | LASTX | Yes |
| 119 | 0x77 | CLX | 1 | CLX | Yes |
| 120 | 0x78 | X=Y? | 1 | X=Y? | Yes |
| 121 | 0x79 | X≠Y? | 1 | X≠Y? | Yes |
| 122 | 0x7A | SIGN | 1 | SIGN | Yes |
| 123 | 0x7B | X≤0? | 1 | X<=0? | Yes |
| 124 | 0x7C | MEAN | 1 | MEAN | Yes |
| 125 | 0x7D | SDEV | 1 | SDEV | Yes |
| 126 | 0x7E | AVIEW | 1 | AVIEW | Yes |
| 127 | 0x7F | CLD | 1 | CLD | Yes |
| 128 | 0x80 | DEG | 1 | DEG | Yes |
| 129 | 0x81 | RAD | 1 | RAD | Yes |
| 130 | 0x82 | GRAD | 1 | GRAD | Yes |
| 131 | 0x83 | ENTER↑ | 1 | ENTER↑ | Yes |
| 132 | 0x84 | STOP | 1 | STOP | Yes |
| 133 | 0x85 | RTN | 1 | RTN | Yes |
| 134 | 0x86 | BEEP | 1 | BEEP | Yes |
| 135 | 0x87 | CLA | 1 | CLA | Yes |
| 136 | 0x88 | ASHF | 1 | ASHF | Yes |
| 137 | 0x89 | PSE | 1 | PSE | Yes |
| 138 | 0x8A | CLRG | 1 | CLRG | Yes |
| 139 | 0x8B | AOFF | 1 | AOFF | Yes |
| 140 | 0x8C | AON | 1 | AON | Yes |
| 141 | 0x8D | OFF | 1 | OFF | Yes |
| 142 | 0x8E | PROMPT | 1 | PROMPT | Yes |
| 143 | 0x8F | ADV | 1 | ADV | Yes |
| 144 | 0x90 | RCL | 2 | RCL | Yes |
| 145 | 0x91 | STO | 2 | STO | Yes |
| 146 | 0x92 | ST+ | 2 | ST+ | Yes |
| 147 | 0x93 | ST- | 2 | ST- | Yes |
| 148 | 0x94 | ST* | 2 | ST* | Yes |
| 149 | 0x95 | ST/ | 2 | ST/ | Yes |
| 150 | 0x96 | ISG | 2 | ISG | Yes |
| 151 | 0x97 | DSE | 2 | DSE | Yes |
| 152 | 0x98 | VIEW | 2 | VIEW | Yes |
| 153 | 0x99 | ΣREG | 2 | ΣREG | Yes |
| 154 | 0x9A | ASTO | 2 | ASTO | Yes |
| 155 | 0x9B | ARCL | 2 | ARCL | Yes |
| 156 | 0x9C | FIX | 2 | FIX | Yes |
| 157 | 0x9D | SCI | 2 | SCI | Yes |
| 158 | 0x9E | ENG | 2 | ENG | Yes |
| 159 | 0x9F | TONE | 2 | TONE | Yes |
| 160 | 0xA0 | XROM 0-3 | 2 |  |  |
| 161 | 0xA1 | XROM 4-7 | 2 |  |  |
| 162 | 0xA2 | XROM 8-11 | 2 |  |  |
| 163 | 0xA3 | XROM 12-15 | 2 |  |  |
| 164 | 0xA4 | XROM 16-19 | 2 |  |  |
| 165 | 0xA5 | XROM 20-23 | 2 |  |  |
| 166 | 0xA6 | XROM 24-27 | 2 |  |  |
| 167 | 0xA7 | XROM 28-31 | 2 |  |  |
| 168 | 0xA8 | SF | 2 | SF | Yes |
| 169 | 0xA9 | CF | 2 | CF | Yes |
| 170 | 0xAA | FS?C | 2 | FS?C | Yes |
| 171 | 0xAB | FC?C | 2 | FC?C | Yes |
| 172 | 0xAC | FS? | 2 | FS? | Yes |
| 173 | 0xAD | FC? | 2 | FC? | Yes |
| 174 | 0xAE | GTO/XEQ IND | 2 |  | No |
| 175 | 0xAF | SPARE | 2 |  | No |
| 176 | 0xB0 | SPARE | 2 |  | No |
| 177 | 0xB1 | GTO 00 | 2 |  | No |
| 178 | 0xB2 | GTO 01 | 2 |  | No |
| 179 | 0xB3 | GTO 02 | 2 |  | No |
| 180 | 0xB4 | GTO 03 | 2 |  | No |
| 181 | 0xB5 | GTO 04 | 2 |  | No |
| 182 | 0xB6 | GTO 05 | 2 |  | No |
| 183 | 0xB7 | GTO 06 | 2 |  | No |
| 184 | 0xB8 | GTO 07 | 2 |  | No |
| 185 | 0xB9 | GTO 08 | 2 |  | No |
| 186 | 0xBA | GTO 09 | 2 |  | No |
| 187 | 0xBB | GTO 10 | 2 |  | No |
| 188 | 0xBC | GTO 11 | 2 |  | No |
| 189 | 0xBD | GTO 12 | 2 |  | No |
| 190 | 0xBE | GTO 13 | 2 |  | No |
| 191 | 0xBF | GTO 14 | 2 |  | No |
| 192 | 0xC0 | GLOBAL | Var |  | No |
| 193 | 0xC1 | GLOBAL | Var |  | No |
| 194 | 0xC2 | GLOBAL | Var |  | No |
| 195 | 0xC3 | GLOBAL | Var |  | No |
| 196 | 0xC4 | GLOBAL | Var |  | No |
| 197 | 0xC5 | GLOBAL | Var |  | No |
| 198 | 0xC6 | GLOBAL | Var |  | No |
| 199 | 0xC7 | GLOBAL | Var |  | No |
| 200 | 0xC8 | GLOBAL | Var |  | No |
| 201 | 0xC9 | GLOBAL | Var |  | No |
| 202 | 0xCA | GLOBAL | Var |  | No |
| 203 | 0xCB | GLOBAL | Var |  | No |
| 204 | 0xCC | GLOBAL | Var |  | No |
| 205 | 0xCD | GLOBAL | Var |  | No |
| 206 | 0xCE | X<> -- | 2 | X<> | Yes |
| 207 | 0xCF | LBL -- | 2 | LBL | Yes |
| 208 | 0xD0 | GTO -- | 3 | GTO | Yes |
| 209 | 0xD1 | GTO -- | 3 |  | No |
| 210 | 0xD2 | GTO -- | 3 |  | No |
| 211 | 0xD3 | GTO -- | 3 |  | No |
| 212 | 0xD4 | GTO -- | 3 |  | No |
| 213 | 0xD5 | GTO -- | 3 |  | No |
| 214 | 0xD6 | GTO -- | 3 |  | No |
| 215 | 0xD7 | GTO -- | 3 |  | No |
| 216 | 0xD8 | GTO -- | 3 |  | No |
| 217 | 0xD9 | GTO -- | 3 |  | No |
| 218 | 0xDA | GTO -- | 3 |  | No |
| 219 | 0xDB | GTO -- | 3 |  | No |
| 220 | 0xDC | GTO -- | 3 |  | No |
| 221 | 0xDD | GTO -- | 3 |  | No |
| 222 | 0xDE | GTO -- | 3 |  | No |
| 223 | 0xDF | GTO -- | 3 |  | No |
| 224 | 0xE0 | XEQ -- | 3 | XEQ | Yes |
| 225 | 0xE1 | XEQ -- | 3 |  | No |
| 226 | 0xE2 | XEQ -- | 3 |  | No |
| 227 | 0xE3 | XEQ -- | 3 |  | No |
| 228 | 0xE4 | XEQ -- | 3 |  | No |
| 229 | 0xE5 | XEQ -- | 3 |  | No |
| 230 | 0xE6 | XEQ -- | 3 |  | No |
| 231 | 0xE7 | XEQ -- | 3 |  | No |
| 232 | 0xE8 | XEQ -- | 3 |  | No |
| 233 | 0xE9 | XEQ -- | 3 |  | No |
| 234 | 0xEA | XEQ -- | 3 |  | No |
| 235 | 0xEB | XEQ -- | 3 |  | No |
| 236 | 0xEC | XEQ -- | 3 |  | No |
| 237 | 0xED | XEQ -- | 3 |  | No |
| 238 | 0xEE | XEQ -- | 3 |  | No |
| 239 | 0xEF | XEQ -- | 3 |  | No |
| 240 | 0xF0 | TEXT 0 | Var |  | No |
| 241 | 0xF1 | TEXT 1 | Var |  | No |
| 242 | 0xF2 | TEXT 2 | Var |  | No |
| 243 | 0xF3 | TEXT 3 | Var |  | No |
| 244 | 0xF4 | TEXT 4 | Var |  | No |
| 245 | 0xF5 | TEXT 5 | Var |  | No |
| 246 | 0xF6 | TEXT 6 | Var |  | No |
| 247 | 0xF7 | TEXT 7 | Var |  | No |
| 248 | 0xF8 | TEXT 8 | Var |  | No |
| 249 | 0xF9 | TEXT 9 | Var |  | No |
| 250 | 0xFA | TEXT 10 | Var |  | No |
| 251 | 0xFB | TEXT 11 | Var |  | No |
| 252 | 0xFC | TEXT 12 | Var |  | No |
| 253 | 0xFD | TEXT 13 | Var |  | No |
| 254 | 0xFE | TEXT 14 | Var |  | No |
| 255 | 0xFF | TEXT 15 | Var |  | No |

# Extended Functions ROM

- Standard extended functions are in the `EXT FCN 2d` set. `CX EXT FCN` is the
  set of functions that were added for the HP41CX.
- `X<I>Y` (25,63) is not in the HP-41CX. The DM41X added it (confirmed on a
  DM41XN state file); hp41uc does not have it yet. 25,51 is `ED` on every model;
  the DM41X calls it `ED$`.

| Code | Cmd | 
|--|--|
| 25,00 | -EXT FCN 2D |
| 25,01 | ALENG |
| 25,02 | ANUM |
| 25,03 | APPCHR |
| 25,04 | APPREC |
| 25,05 | ARCLREC |
| 25,06 | AROT |
| 25,07 | ATOX |
| 25,08 | CLFL |
| 25,09 | CLKEYS |
| 25,10 | CRFLAS |
| 25,11 | CRFLD |
| 25,12 | DELCHR |
| 25,13 | DELREC |
| 25,14 | EMDIR |
| 25,15 | FLSIZE |
| 25,16 | GETAS |
| 25,17 | GETKEY |
| 25,18 | GETP |
| 25,19 | GETR |
| 25,20 | GETREC |
| 25,21 | GETRX |
| 25,22 | GETSUB |
| 25,23 | GETX |
| 25,24 | INSCHR |
| 25,25 | INSREC |
| 25,26 | PASN |
| 25,27 | PCLPS |
| 25,28 | POSA |
| 25,29 | POSFL |
| 25,30 | PSIZE |
| 25,31 | PURFL |
| 25,32 | RCLFLAG |
| 25,33 | RCLPT |
| 25,34 | RCLPTA |
| 25,35 | REGMOVE |
| 25,36 | REGSWAP |
| 25,37 | SAVEAS |
| 25,38 | SAVEP |
| 25,39 | SAVER |
| 25,40 | SAVERX |
| 25,41 | SAVEX |
| 25,42 | SEEKPT |
| 25,43 | SEEKPTA |
| 25,44 | SIZE? |
| 25,45 | STOFLAG |
| 25,46 | X<>F |
| 25,47 | XTOA |
| 25,48 | -CX EXT FCN |
| 25,49 | ASROOM |
| 25,50 | CLRGX |
| 25,51 | ED |
| 25,52 | EMDIRX |
| 25,53 | EMROOM |
| 25,54 | GETKEYX |
| 25,55 | RESZFL |
| 25,56 | ΣREG? |
| 25,57 | X=NN? |
| 25,58 | X≠NN? |
| 25,59 | X<NN? |
| 25,60 | X<=NN? |
| 25,61 | X>NN? |
| 25,62 | X>=NN? |
| 25,63 | X<I>Y |

# Time ROM

- Standard time functions are in the `TIME 2C` set. `CX TIME` is the
  set of functions that were added for the HP41CX.
- `TRNG` (26,36) and the whole `DM41X` set (26,38 - 26,53) are not in the
  HP-41CX. They are the DM41X's additions, and the same on the DM41XN
  (confirmed against real state files). A DM41L does not have them.

| Code | Cmd | 
|--|--|
| 26,0 | -TIME 2C |
| 26,01 | ADATE |
| 26,02 | ALMCAT |
| 26,03 | ALMNOW |
| 26,04 | ATIME |
| 26,05 | ATIME24 |
| 26,06 | CLK12 |
| 26,07 | CLK24 |
| 26,08 | CLKT |
| 26,09 | CLKTD |
| 26,10 | CLOCK |
| 26,11 | CORRECT |
| 26,12 | DATE |
| 26,13 | DATE+ |
| 26,14 | DDAYS |
| 26,15 | DMY |
| 26,16 | DOW |
| 26,17 | MDY |
| 26,18 | RCLAF |
| 26,19 | RCLSW |
| 26,20 | RUNSW |
| 26,21 | SETAF |
| 26,22 | SETDATE |
| 26,23 | SETIME |
| 26,24 | SETSW |
| 26,25 | STOPSW |
| 26,26 | SW |
| 26,27 | T+X |
| 26,28 | TIME |
| 26,29 | XYZALM |
| 26,30 | -CX TIME |
| 26,31 | CLALMA |
| 26,32 | CLALMX |
| 26,33 | CLRALMS |
| 26,34 | RCLALM |
| 26,35 | SWPT |
| 26,36 | TRNG |
| 26,37 | -DM 41X- |
| 26,38 | ABSP |
| 26,39 | AINT |
| 26,40 | ASWAP |
| 26,41 | CLAC |
| 26,42 | CLEM |
| 26,43 | FAST |
| 26,44 | FILL |
| 26,45 | FLCOPY |
| 26,46 | FLHD |
| 26,47 | FLTYPE |
| 26,48 | LKAOFF |
| 26,49 | LKAON |
| 26,50 | RENMFL |
| 26,51 | RETPFL |
| 26,52 | SLOW |
| 26,53 | WORKFL |
