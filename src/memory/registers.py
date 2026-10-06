'''
Register and AlphaRegister: fixed-length byte buffers representing HP41/
DM41L hardware registers, plus the BCD/ASCII encode/decode logic for them.

NOTE: The HP41C and DM41L are "little endian" - the LSB is considered to by
"byte 0" and the MSB is considered "byte 6" - but DM41L state files print hex
data from MSB to LSB. That is, _data[0] contains the MSB of the register. and
_data[6] contains the LSB. Care must be taken to remember this difference when
comparing HP41 documentation with the implementation of the Register and
Memory classes.
'''

import math
import re
from decimal import Decimal, Context, ROUND_HALF_EVEN

from .trigraphs import encode_trigraphs, decode_trigraphs


class DM41MemoryError(ValueError):
    '''
    Raised when a register in the data state contains an illegal value.

    Shared by every DM41 model's memory code (DM41L, DM41X, ...). It was
    originally named DM41LMemoryError; that name is kept below as an alias
    so existing imports keep working.
    '''


# Backward-compatible alias for the original name.
DM41LMemoryError = DM41MemoryError


class Register:
    '''
    Represents a hardware register with arbitrary byte length.

    Physical and emulated HP41 registers are always 7 bytes in length but,
    logically, some registers (such as the HP41 ALPHA register) are composed
    of parts of multiple physical registers. In addition, some status
    registers contain multiple smaller bitfields.
    '''

    def __init__(
        self,
        data: bytes = None,
        size: int = 0,
    ):
        if data is not None:
            self._data = bytearray(data)
        else:
            self._data = bytearray(size)
        self.size = len(self._data)

    @classmethod
    def from_hex(cls, hex_str: str) -> "Register":
        clean_hex = hex_str.replace(" ", "")
        try:
            return cls(bytes.fromhex(clean_hex))
        except ValueError as e:
            raise ValueError(f"Invalid hexadecimal data: {e}") from e

    def get_hex(self) -> str:
        '''This returns the data in big-endian order.'''
        return self._data.hex()

    def get_bytes(self) -> bytes:
        '''
        Returns this register's raw data as an immutable bytes copy, in
        the same big-endian storage order as get_hex() (byte 0 = the
        first/leftmost printed byte, matching how HP41 documentation and
        docs/memory.md address register bytes).

        Note this is NOT the same order as __getitem__/__setitem__, which
        use little-endian format.
        '''
        return bytes(self._data)

    def get_nibbles(self) -> list[int]:
        '''Returns the register data as a list of nibbles from MSB to LSB.'''
        nibbles = []
        for byte in self._data:
            nibbles.append((byte >> 4) & 0x0F)  # High nibble
            nibbles.append(byte & 0x0F)  # Low nibble
        return nibbles

    def get_bcd_number(self) -> float:
        '''Reads the BCD-encoded number from this register.'''
        if self.size != 7:
            raise ValueError(
                "BCD operations require a 7-byte register, " f"got {self.size} bytes."
            )

        nibbles = self.get_nibbles()

        # MS sign nibble at index 0. 1001=negative, 0000=positive.
        ms_nibble = nibbles[0]
        if ms_nibble == 9:
            ms_sign = -1
        elif ms_nibble == 0:
            ms_sign = 1
        else:
            raise ValueError(f"Invalid MS sign nibble: {hex(ms_nibble)}")

        # Mantissa extraction and validation (nibbles 1-10)
        mantissa_val = 0
        for i in range(1, 11):
            n = nibbles[i]
            if n < 0 or n > 9:
                raise ValueError(f"Invalid mantissa digit at nibble {i}: {hex(n)}")
            mantissa_val = mantissa_val * 10 + n

        # XS sign extraction and validation (nibble 11)
        xs_nibble = nibbles[11]
        if xs_nibble == 9:
            xs_sign = -1
        elif xs_nibble == 0:
            xs_sign = 1
        else:
            raise ValueError(f"Invalid XS sign nibble: {hex(xs_nibble)}")

        # Exponent extraction and validation (nibbles 12-13)
        e1 = nibbles[12]
        e2 = nibbles[13]
        if not (0 <= e1 <= 9 and 0 <= e2 <= 9):
            raise ValueError(f"Invalid exponent digits: {hex(e1)}, {hex(e2)}")

        exponent_val = e1 * 10 + e2

        # Apply the 100-X rule for negative exponents.
        # If xs_sign is negative, the stored exponent is 100 - |E|.
        if xs_sign == -1:
            total_exponent = -(100 - exponent_val)
        else:
            total_exponent = exponent_val

        # The stored exponent assumes the mantissa is d.ddddddddd (one digit
        # before the implied decimal point), so shift back by 9 to treat
        # mantissa_val as the plain 10-digit integer it actually is.
        #
        # Do this reconstruction in Decimal, not float.
        result = Decimal(mantissa_val) * Decimal(10) ** (total_exponent - 9)
        return float(ms_sign * result)

    def set_bcd_number(self, number: float):
        '''Writes a float to this register in BCD format.'''
        if self.size != 7:
            raise ValueError(
                "BCD operations require a 7-byte register, " f"got {self.size} bytes."
            )

        if number == 0:
            ms_sign_nibble = 0
            mantissa_val = 0
            xs_sign_nibble = 0
            exp_val = 0
        else:
            sign = -1 if number < 0 else 1
            ms_sign_nibble = 9 if sign < 0 else 0

            # repr() gives the shortest decimal string that round-trips to this
            # exact float, so we round *that* value to 10 significant digits
            # rather than fighting binary-float noise from Decimal(number).
            d = Decimal(repr(abs(number)))
            d = Context(prec=10, rounding=ROUND_HALF_EVEN).create_decimal(d)
            _, digits, exponent = d.as_tuple()

            # Decimal strips trailing zeros from the coefficient (e.g. 5.0 ->
            # digits=(5,), exponent=0), so pad back out to exactly 10 digits
            # and shift the exponent to compensate.
            pad = 10 - len(digits)
            mantissa_val = int("".join(map(str, digits)) + "0" * pad)
            t_val = exponent - pad

            # The hardware treats the 10-digit mantissa as d.ddddddddd (one
            # digit before the implied decimal point, nine after), so the
            # stored exponent is offset by +9 relative to t_val, which
            # assumes a plain 10-digit integer mantissa.
            stored_exp = t_val + 9

            # Clamp to hardware's 2-digit exponent range.
            if stored_exp < -99 or stored_exp > 99:
                raise ValueError("Exponent is too large for conversion.")

            if stored_exp < 0:
                xs_sign_nibble = 9
                exp_val = 100 + stored_exp
            else:
                xs_sign_nibble = 0
                exp_val = stored_exp

        # Construct nibble array: MS(4), M1..M10 (40), XS(4), E1, E2 (8) =
        # 14 nibbles.
        nibbles = [ms_sign_nibble]

        digits = [0] * 10
        val = mantissa_val
        for i in range(9, -1, -1):
            val, digits[i] = divmod(val, 10)
        nibbles.extend(digits)

        nibbles.append(xs_sign_nibble)
        nibbles.append(exp_val // 10)  # E1
        nibbles.append(exp_val % 10)  # E2

        # Pack nibbles into bytes and update _data
        new_data = bytearray()
        for i in range(0, len(nibbles), 2):
            byte = (nibbles[i] << 4) | nibbles[i + 1]
            new_data.append(byte)
        self._data = new_data

    # Byte-0 marker for a register holding short alpha text rather than a
    # BCD number (docs/memory.md "Alpha Storage"): a register whose first
    # byte is exactly 0x10 holds 1-6 ASCII characters in the remaining 6
    # bytes, NUL-padded on the *left* (i.e. right-justified) when shorter
    # than 6 characters. This is distinct from get_ascii()/set_ascii()
    # above, which just treat all 7 bytes as raw left-justified text with
    # no marker byte or hardware-format padding -- that's what XM file name
    # registers use, not what a data register holding text uses.
    ALPHA_TEXT_MARKER = 0x10

    def is_alpha_text(self) -> bool:
        '''True if this looks like a data register holding short alpha
        text per the hardware convention (see ALPHA_TEXT_MARKER above),
        rather than a BCD number. Note a BCD register can never produce a
        false positive here: 0x10's high nibble (1) is not a legal BCD
        sign nibble (only 0/9 are), so the two encodings can't collide.'''
        return self.size == 7 and self._data[0] == self.ALPHA_TEXT_MARKER

    def get_alpha_bytes(self) -> bytes:
        '''Decodes this register as short alpha *bytes* -- the raw
        HP41/DM41L (FOCAL) character codes, not decoded to text. Raises
        ValueError if it isn't marked as an alpha-text register (see
        is_alpha_text()). Callers that know the content is safely plain
        ASCII can use get_alpha_text() instead; callers that need to
        handle FOCAL's non-ASCII characters (docs/trigraphs.md) should
        work with these raw bytes and trigraphs.encode_trigraphs().'''
        if not self.is_alpha_text():
            raise ValueError(
                f"Register {self.get_hex()} is not alpha-marked "
                f"(byte 0 must be 0x{self.ALPHA_TEXT_MARKER:02x})."
            )
        # Real text is right-justified in the remaining 6 bytes, with any
        # padding as *leading* NUL bytes -- so trimming leading NULs (not
        # filtering all NULs, which would corrupt a genuine embedded NUL
        # were one ever legal here) recovers exactly the original content.
        return bytes(self._data[1:]).lstrip(b"\x00")

    def set_alpha_bytes(self, data: bytes):
        '''Writes `data` (1-6 raw HP41/DM41L character bytes -- not
        necessarily plain ASCII, see get_alpha_bytes()) into this register
        using the hardware's alpha-text encoding (see ALPHA_TEXT_MARKER
        above). Requires a 7-byte register, matching every real data
        register this format is used with.'''
        if self.size != 7:
            raise ValueError(
                f"Alpha-text encoding requires a 7-byte register, got {self.size} bytes."
            )
        if not 1 <= len(data) <= 6:
            raise ValueError(f"Alpha content must be 1-6 characters, got {len(data)}.")

        new_data = bytearray(7)
        new_data[0] = self.ALPHA_TEXT_MARKER
        new_data[1 + (6 - len(data)) :] = data
        self._data = new_data

    def get_alpha_text(self) -> str:
        '''Decodes this register as short *plain-ASCII* alpha text.
        Raises ValueError if it isn't alpha-marked, or if its content
        includes a FOCAL character with no plain-ASCII meaning (use
        get_alpha_bytes() + trigraphs.encode_trigraphs() for those).'''
        try:
            return self.get_alpha_bytes().decode("ascii")
        except UnicodeDecodeError as e:
            raise ValueError(
                f"Register {self.get_hex()} holds a non-ASCII FOCAL "
                f"character -- use get_alpha_bytes() instead: {e}"
            ) from e

    def set_alpha_text(self, text: str):
        '''Writes `text` (1-6 plain-ASCII characters) into this register
        using the hardware's alpha-text encoding (see ALPHA_TEXT_MARKER
        above). Use set_alpha_bytes() directly for FOCAL characters with
        no plain-ASCII meaning (docs/trigraphs.md).'''
        try:
            encoded = text.encode("ascii")
        except UnicodeEncodeError as e:
            raise ValueError(f"Text contains non-ASCII characters: {e}") from e
        self.set_alpha_bytes(encoded)

    def get_ascii(self) -> str:
        '''
        Reads this register as raw ASCII text, one character per byte,
        MSB-byte first. Non-printable bytes (including 0x00 padding) are
        rendered as '.' so the string always has a fixed, predictable width.

        Note that this converts the entire register, it does not respect the
        HP41 convention for formatting physical 7 byte registers to contain
        0-6 ASCII characters.
        '''
        chars = []
        for byte in self._data:
            chars.append(chr(byte) if 0x20 <= byte <= 0x7E else ".")
        return "".join(chars)

    def set_ascii(self, text: str):
        '''
        Writes raw ASCII text into this register, one character per byte,
        MSB-byte first. Text shorter than the register is padded with
        trailing 0x00 bytes; text longer than the register raises an error.
        '''
        if len(text) > self.size:
            raise ValueError(
                f"ASCII text is too long for a {self.size}-byte register "
                f"(got {len(text)} characters)."
            )
        try:
            encoded = text.encode("ascii")
        except UnicodeEncodeError as e:
            raise ValueError(f"Text contains non-ASCII characters: {e}") from e

        new_data = bytearray(self.size)
        new_data[: len(encoded)] = encoded
        self._data = new_data

    def __eq__(self, other):
        return (
            isinstance(other, Register)
            and self.size == other.size
            and self._data == other._data
        )

    def __str__(self) -> str:
        '''
        Note that this only works for PrimaryData. ASCII data in XM can be
        variable length and is byte-packed. The StatusRegisters.Alpha register
        is 25 bytes long and doesn't have the header byte.
        '''
        if self._data[0] == 0x10:
            text = ""
            for b in self._data[1:]:
                if b != 0:
                    text += chr(b)
            if text.isprintable():
                return '"' + text + '"'
            return self._data.hex()
        try:
            return f"{self.get_bcd_number():.8g}"
        except ValueError:
            pass

        return self._data.hex()

    def __getitem__(self, index: int) -> int:
        ''' NOTE: This returns bytes in little-endian order.'''
        if index >= self.size or index < 0:
            raise IndexError(f"{index} out of range.")
        # The LSB of the data is the last byte in the array.
        return self._data[self.size - index - 1]

    def __setitem__(self, index: int, value: int):
        ''' NOTE: This returns bytes in little-endian order.'''
        if index >= self.size or index < 0:
            raise IndexError(f"{index} out of range.")
        # The LSB of the data is the last byte in the array.
        self._data[self.size - index - 1] = value

    def __repr__(self):
        return f"Register({self.get_hex()})"


# -- DATA line format (GitHub issue #11) -------------------------------
#
# Both extended-memory "Data" files and main memory's data registers are
# import/exported one register per line, in a plain-ASCII text format
# where each line is exactly one of:
#   - a decimal floating point number (e.g. "3.5", "-42", "1e-5")
#   - 1-6 *characters* of alpha text (a register holding alpha text per
#     Register.set_alpha_bytes()/ALPHA_TEXT_MARKER above), written as
#     trigraphs.encode_trigraphs() would render it: plain ASCII for any
#     byte that's safely plain ASCII, and a trigraph escape (see
#     docs/trigraphs.md) for any HP41/DM41L FOCAL character that isn't --
#     the 1-6 count is of *decoded bytes*, not source characters, since a
#     single trigraph like "\E" can be several source characters long but
#     always decodes to exactly one register byte.
#   - "0x" followed by exactly 14 hex digits -- the register's raw bytes,
#     used as a fallback for content that isn't a valid number or a valid
#     1-6 character alpha string (e.g. a corrupted/hand-crafted register).
#     The "0x" prefix is required (rather than treating any bare 14-digit
#     string as hex) specifically so an all-decimal-digit hex string can't
#     be confused with a plain decimal number.
#
# These three forms don't overlap: a BCD register's first nibble can only
# be 0 or 9 (see Register.get_bcd_number()), which can never equal the
# alpha marker's first nibble (1), the "0x" prefix makes the raw-hex form
# syntactically distinct from a bare number or short text, and a
# trigraph's leading backslash never appears in valid float syntax.
_HEX_LINE_RE = re.compile(r"^0[xX]([0-9a-fA-F]{14})$")


def format_data_line(register: "Register") -> str:
    '''Formats one register as a DATA line: a number if it holds a valid
    BCD value, trigraph-encoded alpha text if it's alpha-marked, or a
    "0x"-prefixed raw-hex fallback otherwise (see the module note above).'''
    try:
        number = register.get_bcd_number()
    except ValueError:
        pass
    else:
        # repr() gives the shortest string that round-trips back to this
        # exact float via float() -- the same approach set_bcd_number()
        # itself uses (see its docstring), so parse_data_line(line) below
        # is guaranteed to reproduce the same register bytes.
        return repr(number)

    if register.is_alpha_text():
        return encode_trigraphs(register.get_alpha_bytes())

    return "0x" + register.get_hex()


def parse_data_line(line: str) -> "Register":
    '''Parses one DATA line (see the module note above) into a new 7-byte
    Register. Raises ValueError if `line` doesn't match any of the three
    accepted forms.'''
    hex_match = _HEX_LINE_RE.match(line.strip())
    if hex_match:
        return Register.from_hex(hex_match.group(1))

    try:
        number = float(line)
    except ValueError:
        number = None
    if number is not None:
        if not math.isfinite(number):
            raise ValueError(f"{line!r} is not a finite number.")
        reg = Register(size=7)
        reg.set_bcd_number(number)
        return reg

    try:
        decoded = decode_trigraphs(line)
    except ValueError as e:
        raise ValueError(
            f"{line!r} isn't a valid DATA line -- must be a decimal "
            "number, 1-6 characters of text (trigraphs allowed -- see "
            f"docs/trigraphs.md), or 0x + 14 hex digits. ({e})"
        ) from e
    if not 1 <= len(decoded) <= 6:
        raise ValueError(
            f"{line!r} decodes to {len(decoded)} character(s) after "
            "trigraph expansion -- alpha text must be 1-6 characters."
        )
    reg = Register(size=7)
    reg.set_alpha_bytes(decoded)
    return reg


class AlphaRegister(Register):
    '''Represents the HP41/DM41L alpha register.'''

    def __str__(self):
        skip_nulls = True
        text = ""
        for b in self._data:
            if b != 0x00 and skip_nulls:
                skip_nulls = False
            if not skip_nulls:
                if 32 <= b <= 127:
                    c = chr(b)
                else:
                    c = "."
                text = text + c
        return text
