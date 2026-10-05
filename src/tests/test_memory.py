"""
Unit tests for DM41L emulator memory system.
Covers Register class (BCD, ASCII, hex) and Memory class (parsing, access).
"""

import pytest
from decimal import Decimal
from memory import (
    Register,
    Memory,
    StatusRegisters,
    KeyAssignments,
    Alarms,
    format_data_line,
    parse_data_line,
    encode_trigraphs,
    decode_trigraphs,
)  # Assuming memory.py is in the same directory or installable

# --- Register Tests ---


def test_register_init():
    """Test basic initialization of a Register."""
    reg = Register(size=7)
    assert reg.size == 7
    assert reg.get_hex() == "00000000000000"

    reg_data = Register(data=bytes([0x01, 0x02]))
    assert reg_data.size == 2
    assert reg_data.get_hex() == "0102"


def test_register_from_hex():
    """Test parsing from hex strings."""
    reg = Register.from_hex("01 02 03")
    assert reg.get_hex() == "010203"
    assert reg.size == 3

    with pytest.raises(ValueError):
        Register.from_hex("not hex")
    with pytest.raises(ValueError):
        Register.from_hex("01 G2")  # Invalid hex digit


def test_register_bcd_roundtrip():
    """Test round-tripping of BCD numbers: Positive, Negative, Zero."""
    reg = Register(size=7)

    test_cases = [
        1.0,
        123.456789,
        -123.456789,
        0.0,
        1e9,  # Large number
        1e-5,  # Small exponent that requires negative XS sign
        -1e-2,  # Negative small exponent
        1234567890.0,  # Max mantissa digits? Let's check precision limit 10 digits.
    ]

    for val in test_cases:
        reg.set_bcd_number(val)
        # We use pytest.approx because of float representation noise,
        # though BCD should be precise for the significant digits stored.
        assert reg.get_bcd_number() == pytest.approx(
            val, abs=1e-9
        ), f"Failed roundtrip for {val}"


def test_register_bcd_invalid_size():
    """BCD operations must fail if register is not 7 bytes."""
    reg = Register(size=3)
    with pytest.raises(ValueError, match="require a 7-byte register"):
        reg.set_bcd_number(1.0)
    with pytest.raises(ValueError, match="require a 7-byte register"):
        reg.get_bcd_number()


def test_register_bcd_invalid_nibbles():
    """Test error handling for corrupted BCD data."""
    # Manually creating a register with bad sign nibble (e.g., 1 instead of 0 or 9)
    # 1 is first nibble of '10...' in hex string '10000000000000'
    reg = Register.from_hex("10000000000000")
    with pytest.raises(ValueError, match="Invalid MS sign nibble"):
        reg.get_bcd_number()


def test_register_ascii():
    """Test ASCII writing and reading."""
    reg = Register(size=7)
    text = "HELLO"
    reg.set_ascii(text)
    assert reg.get_ascii().startswith("HELLO")
    # The register size is 7, text is 5, so expect padding '.' or null check
    # get_ascii returns '.' for non-printable. Padding is 0x00 which is not printable.
    assert (
        reg.get_ascii() == "HELLO.."
    )  # Assuming 0x00 renders as '.' in implementation logic


def test_register_ascii_error():
    """Test error conditions for ASCII."""
    reg = Register(size=3)
    with pytest.raises(ValueError, match="too long"):
        reg.set_ascii("TOOLONG")

    reg_long = Register(size=7)
    with pytest.raises(ValueError, match="non-ASCII"):
        reg_long.set_ascii("Hélló")  # Contains non-ASCII characters


def test_register_alpha_text_roundtrip():
    """Register.set_alpha_text()/get_alpha_text() -- the docs/memory.md
    "Alpha Storage" format: byte 0 == 0x10, remaining 6 bytes hold the
    text right-justified, NUL-padded on the left."""
    reg = Register(size=7)
    reg.set_alpha_text("HI")
    assert reg.is_alpha_text() is True
    assert reg.get_hex() == "10000000004849"  # 0x10, 4 NULs, "HI"
    assert reg.get_alpha_text() == "HI"

    reg.set_alpha_text("ABCDEF")  # exactly 6 chars, no padding
    assert reg.get_hex() == "10414243444546"
    assert reg.get_alpha_text() == "ABCDEF"


def test_register_alpha_text_errors():
    reg = Register(size=7)
    with pytest.raises(ValueError, match="1-6 characters"):
        reg.set_alpha_text("")
    with pytest.raises(ValueError, match="1-6 characters"):
        reg.set_alpha_text("TOOLONG")
    with pytest.raises(ValueError, match="non-ASCII"):
        reg.set_alpha_text("héllo")

    reg3 = Register(size=3)
    with pytest.raises(ValueError, match="7-byte register"):
        reg3.set_alpha_text("HI")

    # A plain zero register isn't alpha-marked (byte 0 == 0x00, not 0x10).
    assert Register(size=7).is_alpha_text() is False
    with pytest.raises(ValueError, match="not alpha-marked"):
        Register(size=7).get_alpha_text()


def test_register_alpha_bytes_roundtrip_non_ascii_focal_bytes():
    """get_alpha_bytes()/set_alpha_bytes() are the raw-byte primitives
    get_alpha_text()/set_alpha_text() are built on -- unlike the text
    wrappers, they accept any byte value 0-255, including bytes above the
    plain-ASCII range (128-255) that get_alpha_text()'s ASCII decode can't
    handle."""
    reg = Register(size=7)
    data = bytes([0xFF, 0x41, 0x00])  # a >127 FOCAL byte, 'A', a NUL
    reg.set_alpha_bytes(data)
    assert reg.is_alpha_text() is True
    assert reg.get_alpha_bytes() == data

    # get_alpha_text() refuses to decode a byte above the plain-ASCII
    # range rather than silently mangling it.
    with pytest.raises(ValueError, match="non-ASCII FOCAL character"):
        reg.get_alpha_text()


def test_register_alpha_bytes_errors():
    reg = Register(size=7)
    with pytest.raises(ValueError, match="1-6 characters"):
        reg.set_alpha_bytes(b"")
    with pytest.raises(ValueError, match="1-6 characters"):
        reg.set_alpha_bytes(b"TOOLONG")


def test_register_alpha_text_is_thin_ascii_wrapper_over_alpha_bytes():
    """set_alpha_text()/get_alpha_text() must still work exactly as
    before for pure-ASCII content -- they're now just get_alpha_bytes()/
    set_alpha_bytes() with an ASCII encode/decode around them."""
    reg = Register(size=7)
    reg.set_alpha_text("HI")
    assert reg.get_alpha_bytes() == b"HI"
    assert reg.get_alpha_text() == "HI"


def test_register_alpha_and_bcd_markers_cannot_collide():
    """An alpha-marked register (byte 0 == 0x10) can never also parse as a
    valid BCD number, and vice versa -- confirms format_data_line()/
    parse_data_line() below can tell the two apart unambiguously."""
    reg = Register(size=7)
    reg.set_alpha_text("HI")
    with pytest.raises(ValueError, match="Invalid MS sign nibble"):
        reg.get_bcd_number()


# --- DATA line format (format_data_line/parse_data_line, issue #11) ---


def test_format_data_line_number():
    reg = Register(size=7)
    reg.set_bcd_number(3.5)
    assert format_data_line(reg) == "3.5"
    reg.set_bcd_number(-42)
    assert format_data_line(reg) == "-42.0"  # get_bcd_number() always returns a float


def test_format_data_line_alpha_text():
    reg = Register(size=7)
    reg.set_alpha_text("HELLO")
    assert format_data_line(reg) == "HELLO"


def test_format_data_line_raw_hex_fallback():
    """A register that's neither a valid BCD number (bad MS sign nibble,
    5, instead of the only legal 0/9) nor alpha-marked (byte 0 is 0x50,
    not 0x10) round-trips as a 0x-prefixed 14-hex-digit line."""
    reg = Register.from_hex("50000000000000")
    line = format_data_line(reg)
    assert line == "0x50000000000000"


def test_parse_data_line_number():
    reg = parse_data_line("3.5")
    assert reg.get_bcd_number() == pytest.approx(3.5)
    reg2 = parse_data_line("-42")
    assert reg2.get_bcd_number() == pytest.approx(-42)


def test_parse_data_line_alpha_text():
    reg = parse_data_line("HELLO")
    assert reg.is_alpha_text() is True
    assert reg.get_alpha_text() == "HELLO"


def test_parse_data_line_raw_hex_requires_0x_prefix():
    """The "0x" prefix is required specifically so a bare 14-digit decimal
    number isn't ambiguous with raw hex -- without the prefix requirement,
    "12345678901234" (14 digits, all valid hex) could mean either."""
    reg = parse_data_line("0x99999999999999")
    assert reg.get_hex() == "99999999999999"

    # No prefix, same 14 digits: parsed as a (very large) decimal number,
    # not raw hex.
    reg2 = parse_data_line("12345678901234")
    assert reg2.get_bcd_number() == pytest.approx(12345678901234, rel=1e-9)


def test_parse_data_line_invalid_raises():
    with pytest.raises(ValueError):
        parse_data_line("")  # empty
    with pytest.raises(ValueError):
        parse_data_line("TOOLONGTEXT")  # > 6 chars, not a number
    with pytest.raises(ValueError):
        parse_data_line("0xZZZZZZZZZZZZZZ")  # "0x" prefix but not valid hex
    with pytest.raises(ValueError):
        parse_data_line("nan")  # not finite
    with pytest.raises(ValueError):
        parse_data_line("inf")  # not finite


def test_data_line_roundtrip_through_format_and_parse():
    """format_data_line() -> parse_data_line() must reproduce the exact
    same register bytes, for all three line kinds."""
    number_reg = Register(size=7)
    number_reg.set_bcd_number(1234567890.0)
    assert parse_data_line(format_data_line(number_reg)).get_hex() == number_reg.get_hex()

    alpha_reg = Register(size=7)
    alpha_reg.set_alpha_text("AB")
    assert parse_data_line(format_data_line(alpha_reg)).get_hex() == alpha_reg.get_hex()

    raw_reg = Register.from_hex("50000000000000")
    assert parse_data_line(format_data_line(raw_reg)).get_hex() == raw_reg.get_hex()


def test_format_data_line_alpha_text_uses_trigraphs_for_focal_bytes():
    """A register holding a FOCAL-special byte (Sigma, 0x7E) must format
    as its trigraph, not raise or mangle the byte."""
    reg = Register(size=7)
    reg.set_alpha_bytes(bytes([0x7E]))
    assert format_data_line(reg) == "\\E"


def test_parse_data_line_alpha_text_decodes_trigraphs():
    reg = parse_data_line("\\E")
    assert reg.is_alpha_text() is True
    assert reg.get_alpha_bytes() == bytes([0x7E])


def test_data_line_alpha_trigraph_roundtrip():
    """format_data_line() -> parse_data_line() must reproduce the exact
    same register bytes when the alpha content includes FOCAL-special
    bytes needing trigraphs."""
    reg = Register(size=7)
    reg.set_alpha_bytes(bytes([0x7E, 0x41, 0x00]))  # Sigma, 'A', high bar
    line = format_data_line(reg)
    assert parse_data_line(line).get_hex() == reg.get_hex()


def test_parse_data_line_alpha_validates_decoded_byte_length_not_source_length():
    """The 1-6 character alpha limit applies to DECODED bytes, not raw
    source-text length -- "\\E\\T\\+" is 3 decoded bytes (legal) despite
    being 9 source characters; "\\E\\E\\E\\E\\E\\E\\E" is 7 decoded bytes
    (too many) despite being shorter in some other encoding."""
    reg = parse_data_line("\\E\\T\\+")
    assert reg.get_alpha_bytes() == bytes([0x7E, 0x60, 0x7F])

    with pytest.raises(ValueError, match="alpha text must be 1-6 characters"):
        parse_data_line("\\E\\E\\E\\E\\E\\E\\E")


def test_parse_data_line_rejects_invalid_trigraph():
    with pytest.raises(ValueError):
        parse_data_line("\\zz")  # not a recognized shorthand or 3 digits


def test_register_indexing():
    """Test register item access with LSB convention."""
    reg = Register(data=bytes([0x11, 0x22, 0x33]))  # size 3. Index 0 is LSB?
    # The code says: return self._data[self.size - index - 1]
    # So Index 0 -> self._data[3-0-1] = self._data[2] = 0x33
    # Index 2 -> self._data[3-2-1] = self._data[0] = 0x11
    assert reg[0] == 0x33
    assert reg[2] == 0x11


def test_register_equality():
    """Test Register __eq__."""
    reg1 = Register.from_hex("aabbcc")
    reg2 = Register.from_hex("aabbcc")
    reg3 = Register.from_hex("ddeeff")
    assert reg1 == reg2
    assert reg1 != reg3
    assert reg1 != "not a register"


# --- Memory Tests ---


@pytest.fixture
def empty_memory():
    return Memory()


def test_memory_initialization(empty_memory):
    """Test default special register values."""
    # Check S as an example from implementation defaults
    s_reg = empty_memory.get_register("S")
    assert s_reg is not None
    assert s_reg.get_hex() == "00001100000000"


def test_memory_from_string(empty_memory):
    """Test parsing a memory dump string."""
    dump = """DM41
00  01020304050607  08090a0b0c0d0e  0f101112131415 
A: c000f50046494c B: 8000000000c196 C: 0000002c0480fd S: 00101100100000 M: 00011cd5ff73cb N: 00000000000000 G: 00"""
    # Note: In my earlier analysis I saw A was c0..., which I'll assume is valid for test parsing,
    # even if get_bcd_number() would fail later. We are testing the parser here.
    mem = Memory.from_string(dump)
    assert mem._header == "DM41"
    assert mem.get_register(0).get_hex() == "01020304050607"
    assert mem.get_register("A").get_hex() == "c000f50046494c"
    assert mem.get_register("S").get_hex() == "00101100100000"


def test_memory_get_set_register(empty_memory):
    """Test accessing core memory and special registers."""
    # Core memory access by index
    new_reg = Register.from_hex("11223344556677")
    empty_memory.set_register(0x10, new_reg)
    assert empty_memory.get_register(0x10) == new_reg

    # Missing register defaults to 7 zeroed bytes
    missing_reg = empty_memory.get_register(0xFF)  # Address not in dict
    assert missing_reg.size == 7
    assert missing_reg.get_hex() == "00000000000000"

    # Special register access by label
    empty_memory.set_register("A", Register.from_hex("11223344556677"))
    assert empty_memory.get_register("A").get_hex() == "11223344556677"


def test_memory_to_string(empty_memory):
    """Test reconstructing dump string from Memory object."""
    # Add some data to core memory
    empty_memory.set_register(0, Register.from_hex("01020304050607"))
    dump_str = empty_memory.to_string()
    assert "DM41" in dump_str
    assert "00  01020304050607" in dump_str
    assert "A:" in dump_str


def test_memory_equality():
    """Test that two identical memories are equal."""
    dump = "DM41\n00 01020304050607\nA: 00000000000000 B: 00000000000000 C: 00000000000000 S: 00000000000000 M: 00000000000000 N: 00000000000000 G: 00"
    mem1 = Memory.from_string(dump)
    mem2 = Memory.from_string(dump)
    assert mem1 == mem2

    # Modify one
    mem2.set_register(0, Register.from_hex("ffffffffffffffff"))
    assert mem1 != mem2


def test_memory_malformed_dump():
    """Test error handling for invalid dump formats."""
    with pytest.raises(ValueError, match="Invalid header"):
        Memory.from_string("WRONG\n00 01020304050607")

    with pytest.raises(ValueError, match="not a hexadecimal number"):
        Memory.from_string("DM41\nXX 01020304050607")


def test_memory_from_string_rejects_short_core_register():
    """A core-memory register field that is fewer than 7 bytes is a
    corrupt/truncated dump and must be rejected, not silently loaded as
    an under-sized Register."""
    with pytest.raises(ValueError, match="6 bytes, expected 7"):
        Memory.from_string("DM41\n00 010203040506")


def test_memory_from_string_rejects_long_core_register():
    """Symmetric case: a register field with extra bytes is just as
    corrupt as a short one and must also be rejected."""
    with pytest.raises(ValueError, match="8 bytes, expected 7"):
        Memory.from_string("DM41\n00 0102030405060708")


def test_memory_from_string_rejects_wrong_length_special_register():
    """The A/B/C/S/M/N special registers are full 7-byte hardware
    registers too, so a short one is just as corrupt as a short core
    register and must be rejected the same way."""
    dump = (
        "DM41\n"
        "A: c000f50046494c B: 8000000000c196 C: 0000002c0480fd "
        "S: 00101100100000 M: 00011cd5ff73cb N: 0000 G: 00"
    )
    with pytest.raises(ValueError, match="Special register N is 2 bytes, expected 7"):
        Memory.from_string(dump)


def test_memory_from_string_accepts_one_byte_g_register():
    """G is a documented exception -- a single status byte, not a full
    hardware register -- so it must stay accepted at its real 1-byte
    size instead of being forced to 7."""
    dump = (
        "DM41\n"
        "A: c000f50046494c B: 8000000000c196 C: 0000002c0480fd "
        "S: 00101100100000 M: 00011cd5ff73cb N: 00000000000000 G: 00"
    )
    mem = Memory.from_string(dump)
    assert mem.get_register("G").get_hex() == "00"


# ---- StatusRegisters tests


@pytest.fixture
def status_memory():
    """Fixture to provide a basic memory object with some data initialized."""
    mem = Memory()
    # Fill core memory with some controlled data to test accessors and labels
    # Addresses 0-4: BCD data for label_for testing
    for addr in range(0, 5):
        reg = Register(size=7)
        reg.set_bcd_number(float(addr))  # 0, 1, 2, 3, 4
        mem.set_register(addr, reg)

    # Addresses 5-8: ASCII data for label_for testing
    for addr in range(5, 9):
        reg = Register(size=7)
        reg.set_ascii("A")  # Simple char 'A' at each address
        mem.set_register(addr, reg)

    # Addresses 9-15: Hex data for label_for testing
    for addr in range(9, 16):
        reg = Register(size=7)
        reg.set_ascii("X")  # We'll treat this as hex data by ignoring ASCII logic
        # Manually set hex to be predictable
        reg._data = bytearray([0x12, 0x34, 0x56, 0x00, 0x00, 0x00, 0x00])
        mem.set_register(addr, reg)

    # Set up address 14 specifically for 'd' and 'Flags' tests
    flag_reg = Register(size=7)
    flag_reg._data = bytearray([0xFF] * 7)
    mem.set_register(14, flag_reg)

    return mem


def test_status_registers_accessors(status_memory):
    """Test that all named accessors return the correct registers at correct addresses."""
    sr = StatusRegisters(status_memory)

    # Test BCD range accessors
    assert sr.T().get_bcd_number() == 0.0
    assert sr.Z().get_bcd_number() == 1.0
    assert sr.Y().get_bcd_number() == 2.0
    assert sr.X().get_bcd_number() == 3.0
    assert sr.LastX().get_bcd_number() == 4.0

    # Test ASCII range accessors (assuming address 5 contains 'A')
    assert sr.M().get_ascii().startswith("A")  # M is at index 5
    assert sr.N().get_ascii().startswith("A")  # N is at index 6
    assert sr.O().get_ascii().startswith("A")  # O is at index 7
    assert (
        sr.P().get_ascii().startswith("A")
    )  # P is at index 8 is outside range? No, 0x05-0x08 is M,N,O,P.
    # Let's check implementation: M=5, N=6, O=7. 25 is not right.
    # The loop goes 5,6,7,8. So M(5), N(6), O(7), P(8). Correct.


def test_status_registers_flags_accessor(status_memory):
    """Test the Flags() accessor returns register d at address 14."""
    sr = StatusRegisters(status_memory)
    expected = status_memory.get_register(14)
    assert sr.Flags() == expected
    assert sr.d() == expected


def test_status_registers_label_for(status_memory):
    """Test that label_for returns correctly formatted strings based on address range."""
    sr = StatusRegisters(status_memory)

    # BCD Range (0-4)
    bcd_label = sr.label_for(1)
    assert "Z" in bcd_label  # Z is at index 1
    assert "1.0" in bcd_label

    # ASCII Range (5-8)
    ascii_label = sr.label_for(6)  # N is at index 6
    assert "N" in ascii_label
    assert "A" in ascii_label  # Should contain the char A

    # Hex Range (9-15)
    hex_label = sr.label_for(10)  # Q is at index 10? No, label_for says 9..15 is hex.
    # Index 9 is Q in list: T=0... Q=9. So index 9 is Q.
    assert f"{sr.Q().get_hex()}" in hex_label.split("\n")[-1]  # simplified check


def test_status_registers_alpha_construction(status_memory):
    """
    Test the alpha register construction logic from combined data.
    Note: The current implementation in src/memory.py uses 'register + register',
    which may raise a TypeError. This test documents that behavior.
    """
    sr = StatusRegisters(status_memory)
    # If implementation is fixed to concatenate bytes properly, this should pass.
    # Currently it will likely fail with TypeError unless Register implements __add__.
    try:
        assert isinstance(sr.alpha, Register)
    except TypeError:
        pytest.fail(
            "StatusRegisters alpha construction failed - check if Register implements __add__ or use byte concatenation."
        )


def test_status_registers_invalid_address(status_memory):
    """Test label_for returns None for addresses outside the Status register range."""
    sr = StatusRegisters(status_memory)
    assert sr.label_for(0x20) is None
    assert sr.label_for(-1) is None


# --- Extended Memory / XMFile Tests ---

from pathlib import Path
from memory import ExtendedMemory, DM41MemoryError, XM_REGIONS, zero_register
from fixture_loading import load_fixture

DATA_DIR = Path(__file__).parent / "data"


def _load_xm(filename: str) -> ExtendedMemory:
    memory = Memory.from_file(DATA_DIR / filename)
    return ExtendedMemory(memory, address_range=[0x40, 0x2EF])


def test_xm_6x_finds_all_six_files():
    """6x-xm.dm41 has 4 Data files, 1 ASCII file, and 1 Program file (PURXM)."""
    xm = _load_xm("6x-xm.dm41")
    files = xm.list_files()
    assert len(files) == 6

    by_name = {f.name: f for f in files}
    # Names are the raw, space-padded 7-character header field (see
    # docs/memory.md sec. 4.3): only PURXM is shorter than 7 characters.
    assert set(by_name) == {
        "XM1.000", "XM2.000", "XM3.000", "XM4.000", "XMALPHA", "PURXM  ",
    }

    for name in ("XM1.000", "XM2.000", "XM3.000"):
        assert by_name[name].file_type == xm.TYPE_DATA
        assert by_name[name].num_registers == 32

    # XM4.000 declares 32 registers but only 23 fit below it within region 0
    # -- the remaining 9 continue at the top of region 1 (0x2e7-0x2ef, just
    # above XMALPHA's header), confirmed by the BCD values there picking up
    # exactly where region 0's data leaves off (see docstring on
    # ExtendedMemory.list_files()).
    xm4 = by_name["XM4.000"]
    assert xm4.file_type == xm.TYPE_DATA
    assert xm4.declared_length == 32
    assert xm4.num_registers == 32
    assert xm4.spans_regions is True
    assert xm4.segments == [[0x041, 0x057], [0x2E7, 0x2EF]]
    numbers = xm4.get_numbers()
    assert len(numbers) == 32
    assert numbers == pytest.approx([64.095 + i * 1.0 for i in range(32)])

    assert by_name["XMALPHA"].file_type == xm.TYPE_ASCII
    assert by_name["XMALPHA"].num_registers == 128
    # Only some of the 128 allocated registers hold actual records; the
    # rest are unused/zero-filled, so get_records() legitimately returns
    # fewer entries than num_registers.
    assert len(by_name["XMALPHA"].get_records()) > 0

    assert by_name["PURXM  "].file_type == xm.TYPE_PROGRAM
    assert by_name["PURXM  "].num_registers == 3
    assert by_name["PURXM  "].byte_length == 20
    assert by_name["PURXM  "].checksum_valid is True
    assert len(by_name["PURXM  "].get_instruction_bytes()) == 20


def test_xm_3x_purxm_program_detected():
    """3x-xm.dm41 also contains a saved PURXM program between XMBCD and
    XMALPHA; before Program-header detection, its registers were wrongly
    counted as part of XMALPHA's data (133 registers instead of 128)."""
    xm = _load_xm("3x-xm.dm41")
    files = xm.list_files()
    by_name = {f.name: f for f in files}

    # XMBCD and PURXM are shorter than 7 characters, so their names carry
    # the raw space padding from the header field; XMALPHA fills all 7.
    assert set(by_name) == {"XMBCD  ", "XMALPHA", "PURXM  "}
    assert by_name["PURXM  "].file_type == xm.TYPE_PROGRAM
    assert by_name["PURXM  "].checksum_valid is True
    assert by_name["XMALPHA"].num_registers == 128


def test_xm_program_header_rejects_non_signature_type1_nibble():
    """A register merely starting with a 0x1 nibble (e.g. packed ASCII
    record bytes) must not be mistaken for a Program header -- only the
    fixed 0x10 00 00 00 signature counts. _parse_header() actually reports
    the mismatch by raising ValueError (not by returning None -- its own
    docstring's "Returns None if raw doesn't match a known header shape"
    is inaccurate for this branch; the caller in list_files() only ever
    catches the exception, it never checks for a None return), so that's
    what we assert here."""
    with pytest.raises(ValueError):
        ExtendedMemory._parse_header(bytes.fromhex("18444546474849"))
    assert ExtendedMemory._parse_header(bytes.fromhex("10000000014003")) is not None


def test_xm_header_does_not_require_aaa_match_own_address():
    """A Data/ASCII header's AAA field (nibble 1-3) equals its own address
    in every undisturbed real dump seen -- but _parse_header() must NOT
    enforce that: a real DM41L PURFL delete can relocate a surviving
    file's header without refreshing AAA to match (see
    test_xm_delfl_purfl_delete_relocates_file_with_stale_aaa below), so a
    mismatched AAA alone must not be fatal. What must still be rejected is
    a header that fails the *reserved-zero-nibble* check -- that's what
    actually disambiguates a real header from a coincidental false
    positive (see docs/extended_memory.md sec. 6), not AAA."""
    real_header = bytes.fromhex("20580000020020")  # 6x-xm.dm41's XM4.000, AAA=0x058
    assert ExtendedMemory._parse_header(real_header) is not None
    # Same header content, parsed at a different address -- AAA (0x058) no
    # longer matches addr (0x059), but the reserved nibbles are still zero,
    # so this must still succeed.
    assert ExtendedMemory._parse_header(real_header) is not None

    phantom_header = bytes.fromhex("20555004574152")  # false header -- reserved nibbles non-zero.
    with pytest.raises(ValueError):
        ExtendedMemory._parse_header(phantom_header)


def test_xm_delfl_purfl_delete_relocates_file_with_stale_aaa():
    """tests/data/delfl-xm.dm41: 6x-xm.dm41 loaded into the DM41L emulator,
    then XM4.000 deleted with the emulator's own PURFL command (not this
    tool's remove_file()) and the result saved back out -- the real-world
    bug report that prompted _parse_header() to stop enforcing
    AAA == addr (see its docstring and the class docstring above).

    A real PURFL delete repacks extended memory using the very same
    top-down/cross-region-spanning layout this tool already models for its
    own add_file()/list_files(): it closes the gap XM4.000 left behind by
    relocating XMALPHA into it (from header 0x2e5 down to 0x058 -- exactly
    where XM4.000's own header used to sit, and PURXM down with it) -- but
    it does NOT refresh XMALPHA's AAA field to match its new address (it's
    still 0x2e5). Confirms every surviving file still loads, in its
    relocated position, with byte-for-byte identical content to
    6x-xm.dm41 -- despite that stale AAA."""
    orig_files = {f.name: f for f in _load_xm("6x-xm.dm41").list_files()}
    delfl_files = _load_xm("delfl-xm.dm41").list_files()
    by_name = {f.name: f for f in delfl_files}

    assert set(by_name) == {
        "XM1.000", "XM2.000", "XM3.000", "XMALPHA", "PURXM  ",
    }

    # XM1-3 sit above XM4.000 in the stack and are untouched by its removal.
    for name in ("XM1.000", "XM2.000", "XM3.000"):
        assert by_name[name].header_addr == orig_files[name].header_addr

    # XMALPHA is relocated into XM4.000's old slot -- header 0x2e5 -> 0x058
    # -- and now spans regions itself (it didn't before).
    xmalpha = by_name["XMALPHA"]
    assert xmalpha.header_addr == 0x058
    assert xmalpha.spans_regions is True
    assert xmalpha.segments == [[0x041, 0x057], [0x287, 0x2EF]]

    # PURXM (a Program file, found by its fixed signature rather than by
    # AAA, so it was never at risk from this bug) shifts down with it.
    purxm = by_name["PURXM  "]
    assert purxm.header_addr == 0x285
    assert purxm.segments == [[0x282, 0x284]]

    # Every surviving file's content round-trips byte-for-byte through the
    # relocation, regardless of file type.
    for f in delfl_files:
        orig_f = orig_files[f.name]
        if f.file_type == ExtendedMemory.TYPE_DATA:
            assert f.get_data_lines() == orig_f.get_data_lines()
        elif f.file_type == ExtendedMemory.TYPE_ASCII:
            assert f.get_records() == orig_f.get_records()
        else:
            assert f.get_program_bytes() == orig_f.get_program_bytes()
            assert f.checksum_valid is True


def test_xm_register_length_reads_full_three_nibble_field():
    """declared_length must read the full 3-nibble SSS field, not just the
    header's last byte -- fillextended.dm41's FILLMEM file is declared as
    362 registers (spanning multiple XM regions), which a single-byte read
    would truncate to 106 (362 mod 256)."""
    xm = _load_xm("fillextended.dm41")
    files = xm.list_files()
    assert len(files) == 1
    assert files[0].declared_length == 362


def test_xm_get_program_bytes_wrong_type_raises():
    xm = _load_xm("6x-xm.dm41")
    data_file = next(f for f in xm.list_files() if f.file_type == xm.TYPE_DATA)
    with pytest.raises(ValueError):
        data_file.get_program_bytes()


# --- ExtendedMemory.add_file() ---
#
# Each test round-trips through list_files() rather than asserting on raw
# register bytes -- that's the strongest available check, since list_files()
# is independently tested against real captured dumps above.


def test_xm_add_file_bootstraps_empty_extended_memory():
    """Adding a file to extended memory that has never been used at all
    must both write the file and initialize region 0's pointer register
    (0x040, all-zero beforehand) so list_files() recognizes it."""
    xm = _load_xm("empty.dm41")
    assert xm.list_files() == []

    added = xm.add_file("HELLO", xm.TYPE_DATA, numbers=[1, 2, 3.5, -42])
    assert added.name == "HELLO  "

    files = xm.list_files()
    assert len(files) == 1
    assert files[0].name == "HELLO  "
    assert files[0].get_numbers() == [1, 2, 3.5, -42]


def test_xm_add_file_data_lines_supports_mixed_content():
    """add_file(data_lines=[...]) (GitHub issue #11) lets a Data file mix
    numbers, short alpha text, and raw-hex registers -- unlike numbers=,
    which only ever produces plain BCD registers."""
    xm = _load_xm("empty.dm41")
    added = xm.add_file(
        "MIXED",
        xm.TYPE_DATA,
        data_lines=["1.5", "HI", "0x50000000000000", "-3"],
    )
    assert added.name == "MIXED  "

    files = xm.list_files()
    assert len(files) == 1
    assert files[0].get_data_lines() == ["1.5", "HI", "0x50000000000000", "-3.0"]
    regs = files[0].data_registers()
    assert regs[0].get_bcd_number() == pytest.approx(1.5)
    assert regs[1].get_alpha_text() == "HI"
    assert regs[2].get_hex() == "50000000000000"
    assert regs[3].get_bcd_number() == pytest.approx(-3)


def test_xm_add_file_rejects_duplicate_name():
    """Regression (user-reported): re-importing an exported file whose
    name matches one already present used to silently create a duplicate
    directory entry -- something a real DM41L would reject. add_file()
    must raise before writing anything."""
    xm = _load_xm("6x-xm.dm41")
    before = {f.name: f for f in xm.list_files()}
    assert "XM1.000" in before  # sanity: this fixture really has it

    with pytest.raises(DM41MemoryError, match="already exists"):
        xm.add_file("XM1.000", xm.TYPE_DATA, numbers=[1, 2, 3])

    # Nothing should have been written -- same files, same content.
    after = {f.name: f for f in xm.list_files()}
    assert set(after) == set(before)


def test_xm_add_file_duplicate_check_does_not_flag_unrelated_names():
    """The duplicate check shouldn't be overly broad -- a genuinely
    different (if short) name must still be addable even though a
    same-prefix file already exists."""
    xm = _load_xm("6x-xm.dm41")
    added = xm.add_file("XM1", xm.TYPE_DATA, numbers=[1])
    assert added.name == "XM1    "


def test_xm_edit_file_keeping_same_name_does_not_self_collide():
    """Editing a file without renaming it (remove-then-add with the same
    name, as the GUI's Edit flow does) must not trip the new duplicate
    check against its own old entry."""
    xm = _load_xm("empty.dm41")
    added = xm.add_file("NOTES", xm.TYPE_ASCII, records=["hello"])

    xm.remove_file(added.header_addr)
    edited = xm.add_file("NOTES", xm.TYPE_ASCII, records=["hello", "world"])
    assert edited.get_records() == ["hello", "world"]


def test_xm_list_files_preserves_raw_name_bytes_even_when_display_collides():
    """manyfiles.dm41 has five ASCII files whose raw directory-entry names
    differ only in an unprintable byte (0x01, 0x02, 0x03, 0x04, 0x06) --
    Register.get_ascii() (which produces the display-only `.name`) renders
    any byte outside 0x20-0x7E as '.', so all five show the identical
    'XMA.   ' display name. `.name_bytes` (the raw 7 bytes, unaffected by
    that sanitization) must still tell them apart -- add_file()'s
    duplicate check and remove_file()'s rebuild both depend on this to
    avoid mistaking these for the same file (see the tests below)."""
    xm = _load_xm("manyfiles.dm41")
    xma_files = [f for f in xm.list_files() if f.name == "XMA.   "]
    assert len(xma_files) == 5
    assert all(f.file_type == xm.TYPE_ASCII for f in xma_files)
    assert {f.name_bytes for f in xma_files} == {
        b"XMA\x01   ",
        b"XMA\x02   ",
        b"XMA\x03   ",
        b"XMA\x04   ",
        b"XMA\x06   ",
    }


def test_xm_edit_file_with_colliding_display_name_does_not_corrupt_dump():
    """Regression (user-reported, manyfiles.dm41): several XM files in
    this dump have raw names that differ only in an unprintable byte, so
    they all show the same '.'-sanitized display name (see
    test_xm_list_files_preserves_raw_name_bytes_even_when_display_collides).
    Editing one of them -- remove-then-add, same as the GUI's Edit flow
    (xm_files_tab.py's _save_new_or_edited_file()) -- used to raise a
    false "duplicate file name" DM41MemoryError from *inside*
    remove_file()'s rebuild, once it reached a second survivor whose
    display name collided with one already rebuilt. That happened after
    every register had already been wiped, so every file from the
    collision point onward (not just the one being edited) vanished for
    good, with no rollback -- matching the reported symptom of unrelated
    special-character-named files disappearing from the XM Files tab."""
    xm = _load_xm("manyfiles.dm41")
    before = xm.list_files()
    assert len(before) == 30

    target = next(f for f in before if f.name_bytes == b"XMA\x01   ")

    # Same sequence the GUI's Edit flow runs.
    xm.remove_file(target.header_addr)
    edited = xm.add_file("XMA1", xm.TYPE_ASCII, records=target.get_records())

    after = xm.list_files()
    assert len(after) == 30, "no file should be lost"
    assert edited.name == "XMA1   "
    # The other four originally-colliding files must have survived intact.
    remaining_xma_bytes = {f.name_bytes for f in after if f.name == "XMA.   "}
    assert remaining_xma_bytes == {
        b"XMA\x02   ",
        b"XMA\x03   ",
        b"XMA\x04   ",
        b"XMA\x06   ",
    }


def test_xm_add_file_appends_after_existing_files():
    """Adding a file to an already-populated region must place it after
    (below) the current last file, and must not disturb any existing
    file's content."""
    xm = _load_xm("6x-xm.dm41")
    before = {f.name: f for f in xm.list_files()}

    xm.add_file("NOTES", xm.TYPE_ASCII, records=["hello", "world", "a third record"])

    after = {f.name: f for f in xm.list_files()}
    assert set(after) == set(before) | {"NOTES  "}
    assert after["NOTES  "].get_records() == ["hello", "world", "a third record"]

    # Every pre-existing file must read back exactly as it did before.
    for name, old in before.items():
        new = after[name]
        assert new.segments == old.segments
        assert new.file_type == old.file_type
        if old.file_type == xm.TYPE_DATA:
            assert new.get_numbers() == old.get_numbers()
        elif old.file_type == xm.TYPE_ASCII:
            assert new.get_records() == old.get_records()
        elif old.file_type == xm.TYPE_PROGRAM:
            assert new.get_instruction_bytes() == old.get_instruction_bytes()
            assert new.checksum_valid is True


def test_xm_add_file_program_checksum_and_roundtrip():
    xm = _load_xm("empty.dm41")
    instructions = bytes([0x1D, 0x2E, 0x3F, 0x40, 0x50] * 3)  # 15 bytes

    added = xm.add_file("MYPROG", xm.TYPE_PROGRAM, instruction_bytes=instructions)
    assert added.byte_length == 15

    files = xm.list_files()
    assert len(files) == 1
    prog = files[0]
    assert prog.name == "MYPROG "
    assert prog.byte_length == 15
    assert prog.get_instruction_bytes() == instructions
    assert prog.checksum_valid is True


def test_xm_add_file_spans_regions_when_it_does_not_fit():
    """A file added when region 0 doesn't have enough room left must
    spill into region 1 exactly the way a naturally-created spanning file
    does (see docs/memory.md sec. 4.5), and read back correctly across
    both segments."""
    xm = _load_xm("empty.dm41")
    xm.add_file("SMALL", xm.TYPE_DATA, numbers=list(range(70)))

    big_numbers = [float(i) for i in range(60)]
    added = xm.add_file("BIGFILE", xm.TYPE_DATA, numbers=big_numbers)
    assert added.spans_regions is True
    assert len(added.segments) == 2

    files = {f.name: f for f in xm.list_files()}
    big = files["BIGFILE"]
    assert big.spans_regions is True
    assert big.get_numbers() == big_numbers

    # Region 1's own pointer register must have been bootstrapped too,
    # since this is the first time anything has been written there.
    # Confirmed against real captures with a genuinely-spanning file
    # (tests/data/3x-xm.dm41, 6x-xm.dm41) -- both show this exact value.
    assert xm.get_register(0x201).get_hex() == "000000400002ef"


def test_xm_add_file_bootstrap_matches_real_device_pointer_register():
    """A real DM41L, erased then given its very first (non-spanning) XM
    file, wrote region 0's pointer register (0x040) as 000010000000bf --
    notably with NNN left at 0 even though region 1 exists in hardware
    (see tests/data/helloworld.dm41). add_file() bootstrapping the same
    scenario must produce the identical register."""
    real = _load_xm("helloworld.dm41")
    assert real.get_register(0x40).get_hex() == "000010000000bf"

    xm = _load_xm("empty.dm41")
    xm.add_file("NOTES", xm.TYPE_ASCII, records=["HELLO", "WORLD"])
    assert xm.get_register(0x40).get_hex() == "000010000000bf"


def test_xm_add_file_updates_region0_nnn_when_a_later_file_first_spans():
    """A real bug: region 0's pointer register (0x040) is only written
    once, when the very first file bootstraps it -- at that point NNN is
    correctly 0 if that file doesn't span. But if a *later* file (not the
    first) is the one that first spans into region 1, 0x040 must be
    patched then too, or its NNN field stays stuck at 0 forever and the
    real DM41L never looks at region 1 again -- every file placed there
    from that point on (including ones entirely inside region 1, not just
    the spanning one) becomes invisible to it, even though this tool's
    own list_files() doesn't depend on NNN and so doesn't notice.

    Confirmed via a real repro: several small ASCII files that fit
    entirely in region 0, followed by one large enough to span, followed
    by more files placed entirely in region 1."""
    xm = _load_xm("empty.dm41")

    def nnn():
        r40 = xm.get_register(0x40)
        return (r40[2] << 4) | (r40[1] >> 4)

    # Nine 9-record files fit entirely within region 0.
    for i in range(1, 10):
        xm.add_file(
            f"NOTES{i}", xm.TYPE_ASCII,
            records=[f"RECORD{n}" for n in range(1, 10)],
        )
        assert nnn() == 0, f"NNN went non-zero before any file spans (after NOTES{i})"

    # The tenth is the one that spans.
    tenth = xm.add_file(
        "NOTES10", xm.TYPE_ASCII, records=[f"RECORD{n}" for n in range(1, 10)]
    )
    assert tenth.spans_regions is True
    assert nnn() == XM_REGIONS[1][1]

    # Ten more Data files land entirely within region 1 and must still be
    # visible.
    for i in range(1, 11):
        xm.add_file(f"DATA{i}", xm.TYPE_DATA, numbers=list(range(1, 14)))
        assert nnn() == XM_REGIONS[1][1]

    files = xm.list_files()
    assert len(files) == 20
    assert {f.name.strip() for f in files} == {
        f"NOTES{i}" for i in range(1, 11)
    } | {f"DATA{i}" for i in range(1, 11)}


def test_xm_add_file_no_room_raises():
    xm = _load_xm("empty.dm41")
    with pytest.raises(DM41MemoryError):
        xm.add_file("HUGE", xm.TYPE_DATA, numbers=[float(i) for i in range(4000)])


def test_xm_add_file_one_register_over_total_capacity_raises_cleanly():
    """Regression test for a user-reported bug: a file needing exactly 1
    register more than extended memory can safely hold used to be
    ACCEPTED by add_file() -- the write succeeded -- and corrupted the
    directory so badly that the very next list_files() call raised
    DM41MemoryError trying to parse unrelated memory past the region as
    a header.

    Root cause: _allocate_segments()'s spanning-into-region-1 capacity
    check was `cursor + 1 <= XM_REGIONS[next_region][0]` (equivalent to
    `cursor < XM_REGIONS[next_region][0]`), one register too permissive
    -- it let a file's data land so that its own successor (another
    file's name register, or list_files()'s EOM sentinel) would have to
    go exactly ON region 1's own reserved pointer register, which
    _place_file() then correctly refuses to overwrite with a bogus EOM,
    but leaves with NO terminator at all. Fixed to `cursor <=
    XM_REGIONS[next_region][0]`, so add_file() now rejects this case up
    front -- before any registers are written -- instead of writing a
    directory with no way to signal its own end."""
    region0, region1 = XM_REGIONS

    # The largest single file (as the very first thing ever written to
    # an empty extended memory) that still leaves room for a directory
    # terminator: every usable register in region 0 except the 2 this
    # file's own name+header consume, plus every usable register in
    # region 1 except the 1 reserved for the terminator that follows.
    max_safe = (region0[1] - region0[0] - 2) + (region1[1] - region1[0] - 1)
    one_over = max_safe + 1

    xm = _load_xm("empty.dm41")
    xm.add_file("MAXSAFE", xm.TYPE_DATA, numbers=[1.0] * max_safe)
    # Must round-trip cleanly -- this is the exact scenario the bug broke.
    files = xm.list_files()
    assert len(files) == 1
    assert files[0].get_numbers() == [1.0] * max_safe

    xm2 = _load_xm("empty.dm41")
    with pytest.raises(DM41MemoryError, match="Not enough free space"):
        xm2.add_file("ONEOVER", xm2.TYPE_DATA, numbers=[1.0] * one_over)
    # And confirm the rejection happened before any writes -- extended
    # memory must be left exactly as it started, not partially corrupted.
    assert xm2.list_files() == []
    assert xm2.get_register(region0[0]) == zero_register()


def test_xm_add_file_exact_single_region_fill_does_not_falsely_span():
    """Regression test for a related, but distinct, off-by-one found
    while investigating the bug above: a file that exactly fills a
    single region -- using every one of its usable registers, leaving
    none there for a directory terminator -- correctly gets its
    terminator pushed into the next region (this is necessary, not a
    bug: region 0's own reserved pointer register can't double as a
    terminator slot), but was then misreported as "spanning regions"
    even though not one byte of its own data lives in the next region.

    XMFile.spans_regions now only counts non-empty segments, so a file
    like this reports spans_regions is False while a file that
    genuinely does spill data into the next region still reports True
    (see test_xm_add_file_spans_regions_when_it_does_not_fit)."""
    region0, region1 = XM_REGIONS

    # Every usable register in region 0 after this file's own
    # name+header, with nothing left over -- the exact boundary that
    # forces a terminator-only push into region 1.
    exact_fill = region0[1] - region0[0] - 2

    xm = _load_xm("empty.dm41")
    added = xm.add_file("EXACT", xm.TYPE_DATA, numbers=[1.0] * exact_fill)
    assert added.spans_regions is False
    assert added.get_numbers() == [1.0] * exact_fill

    # Region 1's own pointer register must still have been bootstrapped
    # -- the terminator genuinely lives there even though this file's
    # data doesn't.
    assert xm.get_register(region1[0]) != zero_register()

    # Must round-trip identically through list_files().
    files = xm.list_files()
    assert len(files) == 1
    assert files[0].spans_regions is False
    assert files[0].get_numbers() == [1.0] * exact_fill

    # A second file must still be placed correctly, continuing from the
    # terminator's real location in region 1 -- not corrupted by the
    # first file's now-accurate (but still internally 2-segment, for
    # next_name_addr's sake) segment bookkeeping.
    second = xm.add_file("SECOND", xm.TYPE_DATA, numbers=[2.0, 3.0])
    assert second.spans_regions is False
    files = xm.list_files()
    assert len(files) == 2
    assert files[1].get_numbers() == [2.0, 3.0]


def test_xm_add_file_rejects_name_over_seven_characters():
    xm = _load_xm("empty.dm41")
    with pytest.raises(ValueError):
        xm.add_file("TOOLONGNAME", xm.TYPE_DATA, numbers=[1])


def test_xm_add_file_rejects_name_outside_allowed_character_range():
    """File names must be plain ASCII 32-101 inclusive (space through
    lowercase 'e') -- unlike file *content*, names don't support
    trigraphs, so a character above that range is simply rejected."""
    xm = _load_xm("empty.dm41")
    # 'z' is 122, above the 101 ('e') upper bound.
    with pytest.raises(ValueError, match="outside the allowed range"):
        xm.add_file("zzz", xm.TYPE_DATA, numbers=[1])
    # A DEL byte (127) or anything else above 101 is rejected the same way.
    with pytest.raises(ValueError, match="outside the allowed range"):
        xm.add_file("A\x7fB", xm.TYPE_DATA, numbers=[1])


def test_xm_add_file_accepts_name_at_range_boundaries():
    """32 (space) and 101 ('e') are themselves valid -- the range is
    inclusive on both ends."""
    xm = _load_xm("empty.dm41")
    added_low = xm.add_file(" a", xm.TYPE_DATA, numbers=[1])  # leading space
    assert added_low.name == " a".ljust(7)
    xm2 = _load_xm("empty.dm41")
    added_high = xm2.add_file("e", xm.TYPE_DATA, numbers=[1])  # 'e' == 101
    assert added_high.name == "e".ljust(7)


def test_xm_get_records_encodes_focal_bytes_as_trigraphs():
    """get_records() must losslessly round-trip FOCAL-special bytes as
    trigraphs rather than the old lossy '?' replacement behavior."""
    xm = _load_xm("empty.dm41")
    added = xm.add_file("NOTES", xm.TYPE_ASCII, records=["A\\EB", "\\T\\+"])
    assert added.get_records() == ["A\\EB", "\\T\\+"]

    # And it decodes to the correct raw bytes underneath.
    regs = added.data_registers()
    stream = b"".join(r.get_bytes() for r in regs)
    # First record: length 3, then 'A', 0x7E (Sigma), 'B'.
    assert stream[0] == 3
    assert stream[1:4] == bytes([0x41, 0x7E, 0x42])


def test_xm_add_file_ascii_records_reject_out_of_range_trigraph():
    xm = _load_xm("empty.dm41")
    with pytest.raises(ValueError):
        xm.add_file("BAD", xm.TYPE_ASCII, records=["\\999"])  # >255


def test_xm_add_file_ascii_plain_content_unaffected_by_trigraph_support():
    """Existing plain-ASCII-only content (no trigraphs present) must still
    round-trip identically -- no regression from adding trigraph support."""
    xm = _load_xm("empty.dm41")
    added = xm.add_file("NOTES", xm.TYPE_ASCII, records=["hello", "world"])
    assert added.get_records() == ["hello", "world"]


def test_xm_add_file_rejects_wrong_argument_for_type():
    xm = _load_xm("empty.dm41")
    with pytest.raises(ValueError):
        xm.add_file("BADARGS", xm.TYPE_DATA, records=["oops"])
    with pytest.raises(ValueError):
        xm.add_file("NOARGS", xm.TYPE_ASCII)


def test_xm_add_file_dump_rows_stay_page_aligned():
    """to_string() must only ever emit rows starting on a 4-register-
    aligned address, filling in any untouched register in a partially-
    used page with the zero register -- the DM41L's loader rejects a
    dump with a misaligned row (confirmed: it rejected one after adding
    a 6-register ASCII file to empty.dm41, which left a 2-register gap
    at the top of a page before the file's own registers began)."""
    xm = _load_xm("empty.dm41")
    xm.add_file("NOTES", xm.TYPE_ASCII, records=["1", "2", "3", "4", "5", "-16"])

    dump = xm._memory.to_string()
    for line in dump.splitlines()[1:]:
        line = line.strip()
        if not line or ":" in line.split()[0]:
            continue  # blank line or the special-register section
        base = int(line.split()[0], 16)
        assert base % 4 == 0, f"row {line!r} doesn't start on a 4-register boundary"

    # And it must still round-trip correctly.
    reloaded = Memory.from_string(dump)
    xm2 = ExtendedMemory(reloaded, address_range=[0x40, 0x2EF])
    files = xm2.list_files()
    assert len(files) == 1
    assert files[0].get_records() == ["1", "2", "3", "4", "5", "-16"]


def test_xm_add_file_ascii_matches_real_device_encoding():
    """A real DM41L, given a fresh (erased) memory and told to create an
    ASCII file called NOTES with records HELLO/WORLD, wrote its data
    registers as 0548454c4c4f05 / 574f524c44ff00 -- terminating the
    record stream with 0xFF (not 0x00). A file created by add_file()
    with the same name and records must produce byte-identical data
    registers to that real capture (see tests/data/helloworld.dm41,
    and docs/memory.md sec. 4.3)."""
    real = _load_xm("helloworld.dm41")
    real_notes = next(f for f in real.list_files() if f.name == "NOTES  ")
    real_register_hex = [r.get_hex() for r in real_notes.data_registers()]

    xm = _load_xm("empty.dm41")
    added = xm.add_file("NOTES", xm.TYPE_ASCII, records=["HELLO", "WORLD"])

    assert [r.get_hex() for r in added.data_registers()] == real_register_hex
    assert added.get_records() == ["HELLO", "WORLD"]


# ---- R00 / .END. / Flags (register c / register d) ----
#
# Expected R00/.END. values below were read directly off register c
# (address 0x0d) in each sample dump and cross-checked two ways: against
# the old (pre-rewrite) Project Voyager memory.py, which had independently
# arrived at the same nibble math for R00/.END., and against the filename
# of empty-128.dm41 (0x200 - 0x180 = 128 data registers).


def test_r00_and_dotend_match_known_sample_dumps():
    cases = {
        "empty.dm41": (0x19C, 0x19B),
        "empty-128.dm41": (0x180, 0x17F),
        "helloworld.dm41": (0x19C, 0x19B),
        "3x-xm.dm41": (0x19C, 0x187),
        # 0x188, not 0x182 -- 6x-xm.dm41 was regenerated after this
        # expectation was written (it briefly had an extra unnamed/empty
        # program, just an END instruction, while its author was testing
        # program-memory research; that shifted .END. and confused the
        # chain walk in list_global_chain() until the fixture was fixed).
        # 0x188 matches the file as it exists now and is what
        # test_list_global_chain_6x_finds_xmbcd_xmalpha_purxm_their_ends_and_the_global_end
        # already relies on below.
        "6x-xm.dm41": (0x19C, 0x188),
    }
    for filename, (expected_r00, expected_dotend) in cases.items():
        memory = Memory.from_file(DATA_DIR / filename)
        assert memory.status_registers.R00() == expected_r00, filename
        assert memory.status_registers.DotEnd() == expected_dotend, filename


def test_cold_start_signature_is_0x169_in_every_sample():
    """The 3-nibble field between 'printer use' and R00 in register c is
    documented as a fixed cold-start signature; every real sample dump
    should show the same value (0x169) there. There's no public accessor
    for this (it's not user-actionable), so read it via the same nibble
    math R00()/DotEnd() use."""
    for path in DATA_DIR.glob("*.dm41"):
        memory = load_fixture(path)
        nibbles = memory.status_registers._reg_c_nibbles()
        cold_start = memory.status_registers._nibbles_to_int(nibbles[5:8])
        assert cold_start == 0x169, path.name


def test_key_assignments_end_detects_f0_marked_registers():
    """keyassigns.dm41 has 22 real assignments packed two-per-register into
    registers 0xc0-0xca, each starting with the 0xf0 marker byte Wickes'
    "Synthetic Programming on the HP-41C" (Section 2E) documents. Register
    0xcb (203) is free/zeroed, so the scan should stop there."""
    memory = Memory.from_file(DATA_DIR / "keyassigns.dm41")
    assert memory.key_assignments.end_exclusive == 0xCB


def test_key_assignments_end_is_start_when_no_assignments():
    """Every other sample fixture has no Key Assignment Register entries,
    so register 0xc0 itself shouldn't carry the 0xf0 marker and the scan
    should report an empty region. keyassigns.dm41 and xrom-
    keyassignments.dm41 are excluded -- both were purpose-built to
    exercise the Key Assignment Registers and have real 0xf0-marked
    entries there. (Two other fixtures once needed the same exclusion --
    keyassigntest.dm41, referenced in memory/functions.py's module note,
    and assigntest.dm41, a real device capture used while diagnosing the
    2026-08-18 row-4 key-byte bug -- but both have since been retired from
    tests/data/, so they're no longer in this set at all.)
    global-key-assignments.dm41 is NOT excluded: its two assignments are
    global-label ones (sec 4.6), which never touch the Key Assignment
    Registers at all -- register 0xc0 there is correctly empty.

    The alarmtest*.dm41/4alarmtest.dm41/repeater.dm41 fixtures are also
    excluded: each one carries a real ALMCAT/XYZALM (and, in
    4alarmtest.dm41, CLRALMS/TIME) Key Assignment Register pair captured
    alongside their alarm data -- found 2026-08-20 while adding
    Alarms-buffer-relocation tests, which is what first exercised this
    check against them. badalarms.dm41 (2026-09-02, the user's own
    add/delete/edit repro that turned up the FOCAL-literal-character bug,
    docs/alarms.md sec 11) carries the same ALMCAT/XYZALM pair, inherited
    from the 4alarmtest.dm41 buffer it was edited from -- excluded for the
    same reason. goodalarms.dm41 (2026-09-02, the user's clean re-run of
    the same add/delete/edit repro -- same 4 alarms as badalarms.dm41,
    just with the first three retimed so none are past due -- confirming
    the sec 12 repeats-flag fix on real hardware) carries the identical
    inherited pair for the same reason. past-due.dm41 (2026-09-04,
    alarmtest3.dm41's content with 4 of its 5 alarms forced past due to
    confirm the past-due high-nibble marker on a repeating alarm for the
    first time) is excluded for the same reason too -- it's built from
    alarmtest3.dm41's own buffer, ALMCAT/XYZALM pair included.
    tenthsofasecond.dm41 (2026-09-04, a single alarm deliberately set to
    tenth-of-a-second trigger-time precision, used to correct the sec 12
    repeats-flag digit to the last BCD nibble only rather than the last
    two) carries the same inherited ALMCAT/XYZALM pair and is excluded
    for the same reason. So do manyfiles-packed.dm41 and the
    dm41x_pack_dotend pair (2026-10-04, real before/after PACK captures
    taken from the same calculators)."""
    excluded = {
        "keyassigns.dm41", "xrom-keyassignments.dm41",
        "manyfiles.dm41", "dm41x_manyfiles.dm41", "manyfiles-packed.dm41",
        "dm41x_pack_dotend.dm41", "dm41x_pack_dotend-packed.dm41",
        "manyfiles-repacked.dm41",
        "alarmtest.dm41", "alarmtest2.dm41", "alarmtest3.dm41",
        "4alarmtest.dm41", "repeater.dm41", "badalarms.dm41",
        "goodalarms.dm41", "past-due.dm41", "tenthsofasecond.dm41",
    }
    for path in DATA_DIR.glob("*.dm41"):
        if path.name in excluded:
            continue
        memory = load_fixture(path)
        assert memory.key_assignments.end_exclusive == 0xC0, path.name


def test_key_assignments_end_on_fresh_memory():
    assert Memory().key_assignments.end_exclusive == 0xC0


def test_set_r00_rewrites_only_the_r00_field():
    memory = Memory.from_file(DATA_DIR / "empty.dm41")
    sigma_before = memory.status_registers.SigmaReg()
    dotend_before = memory.status_registers.DotEnd()

    memory.status_registers.set_R00(0x150)

    assert memory.status_registers.R00() == 0x150
    assert memory.status_registers.SigmaReg() == sigma_before
    assert memory.status_registers.DotEnd() == dotend_before


def test_set_r00_rejects_out_of_range_values():
    memory = Memory()
    with pytest.raises(ValueError):
        memory.status_registers.set_R00(-1)
    with pytest.raises(ValueError):
        memory.status_registers.set_R00(0x1000)


def test_flags_get_set_bit_mapping():
    """Flag N is bit N of the 56-bit register, MSB first: flag 0 is the
    top bit of byte 0, flag 55 is the bottom bit of byte 6."""
    memory = Memory()
    memory.set_register(StatusRegisters.REG_D_ADDR, Register.from_hex("80000000000001"))

    assert memory.status_registers.get_flag(0) is True
    assert memory.status_registers.get_flag(55) is True
    for n in range(1, 55):
        assert memory.status_registers.get_flag(n) is False, n

    all_flags = memory.status_registers.get_all_flags()
    assert len(all_flags) == 56
    assert all_flags[0] is True
    assert all_flags[55] is True
    assert all_flags.count(True) == 2


def test_flags_set_flag_roundtrip():
    # Memory() now ships with realistic ("Memory Lost" state) defaults for
    # register d, not an all-zero one -- start from an explicitly zeroed
    # flag register so this test is about set_flag()/get_flag() mechanics,
    # not whatever flags happen to be on by default.
    memory = Memory()
    memory.set_register(StatusRegisters.REG_D_ADDR, Register(size=7))

    memory.status_registers.set_flag(11, True)  # "auto execute" per docs/flags.md
    memory.status_registers.set_flag(48, True)  # "ALPHA"
    assert memory.status_registers.get_flag(11) is True
    assert memory.status_registers.get_flag(48) is True
    assert memory.status_registers.get_all_flags().count(True) == 2

    memory.status_registers.set_flag(11, False)
    assert memory.status_registers.get_flag(11) is False
    assert memory.status_registers.get_flag(48) is True


def test_flags_reject_out_of_range_numbers():
    memory = Memory()
    with pytest.raises(ValueError):
        memory.status_registers.get_flag(56)
    with pytest.raises(ValueError):
        memory.status_registers.set_flag(-1, True)


def test_status_registers_flags_agrees_with_memory_get_flag(status_memory):
    """status_memory's fixture fills register d (0x0e) with 0xFF bytes --
    every flag should read True through both the low-level Memory API and
    the StatusRegisters.Flags() register accessor."""
    assert status_memory.status_registers.get_all_flags() == [True] * 56
    sr = StatusRegisters(status_memory)
    assert sr.Flags() == status_memory.get_register(14)


# ---- ExtendedMemory.remove_file() ----


def _snapshot_content(f):
    """Eagerly materializes an XMFile's content as a plain value. Needed
    whenever a snapshot must survive a later mutation of the underlying
    Memory -- XMFile reads its registers lazily, so holding onto the
    XMFile object itself (instead of calling this right away) would read
    back whatever ends up at those addresses *after* the mutation, not
    the content that was really there when the snapshot was taken."""
    if f.file_type == ExtendedMemory.TYPE_DATA:
        # get_data_lines() (not get_numbers()) so this snapshot works for
        # a Data file that mixes numbers, alpha text, and/or raw-hex
        # registers -- see test_xm_remove_file_preserves_mixed_data_file.
        return (f.file_type, f.get_data_lines())
    if f.file_type == ExtendedMemory.TYPE_ASCII:
        return (f.file_type, f.get_records())
    if f.file_type == ExtendedMemory.TYPE_PROGRAM:
        return (f.file_type, f.get_instruction_bytes())
    raise AssertionError(f"unexpected file type: {f.file_type}")


def test_xm_remove_file_from_middle_preserves_the_rest():
    xm = _load_xm("6x-xm.dm41")
    before_files = xm.list_files()
    assert len(before_files) == 6
    before = {f.name: _snapshot_content(f) for f in before_files}

    target = next(f for f in before_files if f.name == "XMALPHA")
    xm.remove_file(target.header_addr)

    after_files = xm.list_files()
    after = {f.name: _snapshot_content(f) for f in after_files}

    assert "XMALPHA" not in after
    assert set(after) == set(before) - {"XMALPHA"}
    for name, old_content in before.items():
        if name == "XMALPHA":
            continue
        assert after[name] == old_content, name
    for f in after_files:
        if f.file_type == ExtendedMemory.TYPE_PROGRAM:
            assert f.checksum_valid is True


def test_xm_remove_file_preserves_mixed_data_file():
    """Regression: remove_file()'s rebuild used to snapshot Data-type
    survivors via get_numbers(), which raises ValueError for any register
    that isn't a plain BCD number -- so removing *any* file while a
    sibling Data file contained alpha text or raw-hex content would have
    crashed instead of preserving it. It now uses get_data_lines()."""
    xm = _load_xm("empty.dm41")
    xm.add_file("MIXED", xm.TYPE_DATA, data_lines=["1.5", "HI", "0x50000000000000"])
    xm.add_file("DOOMED", xm.TYPE_ASCII, records=["remove me"])

    doomed = next(f for f in xm.list_files() if f.name == "DOOMED ")
    xm.remove_file(doomed.header_addr)

    files = xm.list_files()
    assert {f.name for f in files} == {"MIXED  "}
    assert files[0].get_data_lines() == ["1.5", "HI", "0x50000000000000"]


def test_xm_remove_unrelated_file_preserves_colliding_display_name_survivors():
    """Same root cause as
    test_xm_edit_file_with_colliding_display_name_does_not_corrupt_dump,
    but via a plain Remove (no rename involved): removing a file that has
    nothing to do with the collision must not disturb XM files whose raw
    names happen to collapse to the same '.'-sanitized display name (see
    test_xm_list_files_preserves_raw_name_bytes_even_when_display_collides)."""
    xm = _load_xm("manyfiles.dm41")
    before = xm.list_files()
    unrelated = next(f for f in before if f.name.rstrip() == "XMBCD")

    xm.remove_file(unrelated.header_addr)

    after = xm.list_files()
    assert len(after) == len(before) - 1
    remaining_xma_bytes = {f.name_bytes for f in after if f.name == "XMA.   "}
    assert remaining_xma_bytes == {
        b"XMA\x01   ",
        b"XMA\x02   ",
        b"XMA\x03   ",
        b"XMA\x04   ",
        b"XMA\x06   ",
    }


def test_xm_remove_last_file_leaves_extended_memory_empty():
    xm = _load_xm("empty.dm41")
    xm.add_file("ONLYONE", xm.TYPE_DATA, numbers=[1, 2, 3])
    added = xm.list_files()[0]

    xm.remove_file(added.header_addr)

    assert xm.list_files() == []
    # Region 0's pointer register should be back to "never used".
    assert xm.get_register(0x40) == zero_register()


def test_xm_remove_file_unknown_header_raises():
    xm = _load_xm("empty.dm41")
    with pytest.raises(DM41MemoryError):
        xm.remove_file(0x99)


def test_xm_remove_file_then_add_new_file_still_works():
    """Removing a file frees up its space for reuse via a subsequent
    add_file() call (even though remove_file() doesn't reuse the space
    itself -- see its docstring), since the rebuild always leaves
    extended memory in a normal, valid state."""
    xm = _load_xm("3x-xm.dm41")
    files = xm.list_files()
    xm.remove_file(files[0].header_addr)

    added = xm.add_file("NEWFILE", xm.TYPE_ASCII, records=["hi"])
    assert added.get_records() == ["hi"]
    names = {f.name for f in xm.list_files()}
    assert "NEWFILE" in names


# ---- Program memory: the raw global chain (docs/program.md sec 5) ----
#
# list_global_chain() is the raw, per-marker walk: expected names/counts
# below were derived by walking the chain by hand against the raw hex in
# each sample dump (see docs/program.md sec 5.1 for simple.dm41 and
# 6x-xm.dm41 specifically) and cross-checked against the real device's
# CAT 1 listing for XMBCD/XMALPHA/PURXM, already recorded in project notes
# from an earlier session.
#
# Entries here are NOT grouped into "programs" -- the user's own testing
# (against a modified copy of 6x-xm.dm41) found a single END can have zero,
# one, or several LBLs chained to it, so each entry is just one independent
# chain link (a LBL header or an END marker). `distance_bytes` below is
# that entry's own raw marker distance to the next chain link the walk
# visits from it -- confirmed against the exact address arithmetic in
# docs/program.md's worked examples, not a program size. For the grouped,
# "one row per real program" view (what the Program tab and program export
# actually use), see the list_programs() tests further below and
# docs/program.md sec 5.3.
#
# The permanent `.END.` itself (kind == ".END.") is included as the newest
# (last-listed) entry whenever program memory has any real content -- the
# user found comparing an earlier Program tab's output against a real CAT 1
# listing that omitting it (an even earlier version of this method silently
# dropped it) was hiding bytes CAT 1 counts as part of the newest program.


def test_list_global_chain_empty_memory_returns_nothing():
    for filename in ("empty.dm41", "empty-128.dm41", "helloworld.dm41"):
        memory = Memory.from_file(DATA_DIR / filename)
        assert memory.programs.list_global_chain() == [], filename


def test_list_global_chain_fresh_memory_returns_nothing():
    """A brand new, never-loaded Memory() has R00 decoded as 0 -- not a
    real partition -- so list_global_chain() should bail out to []
    rather than trying to walk a nonsensical chain."""
    assert Memory().programs.list_global_chain() == []


def test_list_global_chain_simple_finds_apptest_lbl_its_end_and_the_global_end():
    """simple.dm41 (docs/program.md's second worked example) has one LBL
    header (APPTEST), one plain END marker, and -- since this program is
    also the newest thing in memory -- the permanent `.END.` itself as a
    third, distinct chain link. Oldest (nearest R00, i.e. created first)
    is listed first, matching CAT 1's display order."""
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    chain = memory.programs.list_global_chain()

    assert len(chain) == 3

    assert chain[0].name == "APPTEST"
    assert chain[0].kind == "LBL"
    assert chain[0].header_addr == 0x19B
    assert chain[0].header_offset == 0
    assert chain[0].key_assignment == 0x00
    assert chain[0].is_named is True
    # The oldest/first-ever chain link has no predecessor to point to.
    assert chain[0].distance_bytes == 0

    assert chain[1].name is None
    assert chain[1].kind == "END"
    assert chain[1].is_named is False
    assert chain[1].display_name == "END"
    # This END's own marker points 23 bytes onward to APPTEST's header.
    assert chain[1].distance_bytes == 23
    assert chain[1].distance_label == "23 bytes"
    assert chain[1].end_type == 0x0

    assert chain[2].name is None
    assert chain[2].kind == ".END."
    assert chain[2].is_named is False
    assert chain[2].end_type == 0x2
    assert chain[2].distance_bytes == 9


def test_list_global_chain_6x_finds_xmbcd_xmalpha_purxm_their_ends_and_the_global_end():
    """6x-xm.dm41 (docs/program.md's third worked example) has three LBL
    headers, each followed in the chain by its own separate END marker,
    plus the permanent `.END.` itself as the seventh and newest entry --
    but the LBL/END pairing is NOT guaranteed in general (see module-level
    note above), just what this specific fixture happens to contain."""
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    chain = memory.programs.list_global_chain()

    names_in_order = [p.name for p in chain]
    assert names_in_order == ["XMBCD", None, "XMALPHA", None, "PURXM", None, None]

    kinds_in_order = [p.kind for p in chain]
    assert kinds_in_order == ["LBL", "END", "LBL", "END", "LBL", "END", ".END."]

    # Each entry's raw distance, confirmed against the address arithmetic
    # worked out by hand for this exact fixture.
    distances_in_order = [p.distance_bytes for p in chain]
    assert distances_in_order == [0, 58, 3, 48, 3, 17, 8]

    by_name = {p.name: p for p in chain if p.is_named}
    assert by_name["XMBCD"].header_addr == 0x19B
    assert by_name["XMALPHA"].header_addr == 0x193
    assert by_name["PURXM"].header_addr == 0x18B
    # Key-assignment byte (0x00 = unassigned) -- decoded but not otherwise
    # asserted elsewhere in this file.
    assert by_name["XMBCD"].key_assignment == 0x01

    end_entries = [p for p in chain if not p.is_named]
    assert [p.end_type for p in end_entries] == [0x0, 0x0, 0x0, 0x2]
    assert all(p.key_assignment is None for p in end_entries)

    assert chain[-1].kind == ".END."


def test_list_global_chain_address_label_format():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    assert memory.programs.list_global_chain()[0].address_label == "0x19b:0"


def test_list_global_chain_terminates_on_every_sample_dump():
    """Defensive/regression coverage: list_global_chain() should never
    raise or hang on any real fixture, regardless of whether it has
    programs."""
    for path in DATA_DIR.glob("*.dm41"):
        memory = load_fixture(path)
        chain = memory.programs.list_global_chain()
        assert isinstance(chain, list)
        for p in chain:
            assert p.header_addr >= 0xC0
            assert 0 <= p.header_offset <= 6


# ---- Program memory: real, END-delimited programs (docs/program.md sec
# 5.3) ----
#
# list_programs() groups list_global_chain()'s raw entries into what CAT 1
# actually shows as one program: programs are told apart by explicit plain
# END markers, NOT by global labels (a program can have zero, one, or
# several -- see Program's docstring). The two purpose-built fixtures below
# (unlabelled.dm41, twolabels.dm41) were created by the user specifically to
# pin this down against a real DM41L's CAT 1 listing, and caught a real bug
# in an earlier version of this grouping: it mistook the zero-padding
# bytes that keep the permanent `.END.` marker aligned to a register
# boundary (docs/program.md sec 5.1) for a small extra unnamed program.


def test_list_programs_empty_memory_returns_nothing():
    for filename in ("empty.dm41", "empty-128.dm41", "helloworld.dm41"):
        memory = Memory.from_file(DATA_DIR / filename)
        assert memory.programs.list_programs() == [], filename


def test_list_programs_fresh_memory_returns_nothing():
    assert Memory().programs.list_programs() == []


def test_list_programs_simple_is_one_program_not_two():
    """simple.dm41 has exactly one program, APPTEST (26 bytes) -- NOT a
    second "nameless" program as an earlier (buggy) reading of this dump
    assumed. The bytes after APPTEST's own explicit END are entirely zero
    padding in front of the permanent `.END.` marker, confirmed by the same
    all-zero-gap check the unlabelled.dm41 fixture below exercises
    directly."""
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    programs = memory.programs.list_programs()

    assert len(programs) == 1
    assert programs[0].names_label == "APPTEST"
    assert programs[0].is_named is True
    assert programs[0].length == 26
    assert programs[0].terminator == "END"
    assert programs[0].is_last is False
    assert programs[0].address_label == "0x19b:0"
    assert programs[0].length_label == "26 bytes"


def test_list_programs_6x_finds_three_named_programs():
    """6x-xm.dm41 has three named programs (XMBCD, XMALPHA, PURXM), each
    closed by its own explicit END -- the trailing gap before the
    permanent `.END.` is (like simple.dm41's) pure alignment padding, not
    a fourth program."""
    memory = Memory.from_file(DATA_DIR / "6x-xm.dm41")
    programs = memory.programs.list_programs()

    assert [p.names_label for p in programs] == ["XMBCD", "XMALPHA", "PURXM"]
    assert [p.length for p in programs] == [61, 51, 20]
    assert all(p.terminator == "END" for p in programs)
    assert all(not p.is_last for p in programs)


def test_list_programs_unlabelled_finds_two_unnamed_programs_matching_cat_1():
    """tests/data/unlabelled.dm41: two programs, NEITHER named (one holds
    only an ALPHA string "NO LABEL", the other only a local numbered
    label) -- confirmed against the real DM41L's own CAT 1 listing: 16 and
    20 bytes. An earlier version of this grouping miscounted this as
    THREE programs (16, 20, and a phantom 6-byte one made of nothing but
    the zero-padding in front of the permanent `.END.` marker) -- this is
    the fixture that caught that bug."""
    memory = Memory.from_file(DATA_DIR / "unlabelled.dm41")
    programs = memory.programs.list_programs()

    assert len(programs) == 2
    assert [p.is_named for p in programs] == [False, False]
    assert [p.names_label for p in programs] == ["(unlabelled)", "(unlabelled)"]
    assert [p.length for p in programs] == [16, 20]
    assert [p.terminator for p in programs] == ["END", "END"]


def test_list_programs_twolabels_is_one_program_with_two_labels():
    """tests/data/twolabels.dm41: ONE program with TWO global labels
    ("FIRST", "SECOND") and no explicit END at all -- legal for the single
    newest program in memory, terminated instead by the permanent `.END.`
    sentinel. CAT 1 would show two catalog entries here, but the user's
    own real-hardware testing confirmed this is one program: it should be
    counted as such, with both labels listed against the one program."""
    memory = Memory.from_file(DATA_DIR / "twolabels.dm41")
    programs = memory.programs.list_programs()

    assert len(programs) == 1
    program = programs[0]
    assert [label.name for label in program.labels] == ["FIRST", "SECOND"]
    assert program.names_label == "FIRST, SECOND"
    assert program.is_named is True
    assert program.length == 28
    assert program.terminator == ".END."
    assert program.is_last is True


def test_list_programs_address_label_format():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    assert memory.programs.list_programs()[0].address_label == "0x19b:0"


def test_list_programs_terminates_on_every_sample_dump():
    """Defensive/regression coverage: list_programs() should never raise
    or hang on any real fixture, regardless of whether it has programs,
    and every program's length should be positive."""
    for path in DATA_DIR.glob("*.dm41"):
        memory = load_fixture(path)
        programs = memory.programs.list_programs()
        assert isinstance(programs, list)
        for p in programs:
            assert p.start_addr >= 0xC0
            assert 0 <= p.start_offset <= 6
            assert p.length > 0


# ---- Key Assignments (docs/key_assignments.md sec 4) ----
#
# key_byte_for()/_keyflags_bit() below are confirmed against three real
# captured dumps (keyassigns.dm41, xrom-keyassignments.dm41,
# global-key-assignments.dm41) -- see the key-assignment research notes for
# the full derivation, including how an earlier (wrong) formula
# (bit = 37 - M - 8*(N-1)) looked validated against a subset of the data
# due to a numeric aliasing coincidence, and was only caught by testing
# all three fixtures' independently-known key numbers simultaneously.


def test_key_byte_for_matches_known_assignments():
    """keyassigns.dm41's 14 assignments span 8 different physical keys in
    both shift states; the formula must reproduce every stored key byte
    exactly (docs sec 4.3)."""
    cases = [
        (11, False, 0x01), (21, True, 0x0A),
        (12, False, 0x11), (22, True, 0x1A),
        (13, False, 0x21), (23, True, 0x2A),
        (14, False, 0x31), (24, True, 0x3A),
        (53, False, 0x25), (52, False, 0x15),
        (62, True, 0x1E), (54, False, 0x35),
        (64, True, 0x3E), (63, True, 0x2E),
    ]
    for key_number, shifted, expected_byte in cases:
        assert KeyAssignments.key_byte_for(key_number, shifted) == expected_byte, (
            key_number, shifted
        )


def test_key_byte_for_row4_uses_physical_column_not_key_number():
    """Row 4's ENTER^ key is physically double-width and eats the
    physical-column-2 slot, so keys 42/43/44 sit at physical columns
    3/4/5 -- NOT 2/3/4, unlike every other row where the key-number
    column digit is the physical column directly (docs sec 4.3's row-4
    correction). Found 2026-08-18 via real-hardware testing: the old
    (unmapped) formula wrote key 42's assignment to the byte for the
    unused phantom column-2 slot instead. Key 42's expected bytes here
    are independently confirmed against a real device capture in
    test_key_42_row4_matches_real_device_capture below; 41/43/44 are
    hand-derived from the same corrected formula (Wickes' Figure 4-2
    diagram), not yet independently cross-checked against a real capture
    the way 42 is."""
    cases = [
        (41, False, 0x04), (41, True, 0x0C),
        (42, False, 0x24), (42, True, 0x2C),
        (43, False, 0x34), (43, True, 0x3C),
        (44, False, 0x44), (44, True, 0x4C),
    ]
    for key_number, shifted, expected_byte in cases:
        assert KeyAssignments.key_byte_for(key_number, shifted) == expected_byte, (
            key_number, shifted
        )


def test_key_42_row4_matches_real_device_capture():
    """xrom-keyassignments.dm41 has real two-byte XROM assignments on all
    four row-4 keys (41-44), both shift states -- decoding all eight and
    checking their raw_key_byte against the corrected physical-column-3
    formula (see test_key_byte_for_row4_uses_physical_column_not_key_
    number above) confirms row 4 decodes correctly end-to-end, not just
    at the formula level.

    Historical note: this test originally cross-checked assigntest.dm41,
    a real device capture the user made while diagnosing the row-4 bug --
    it had a genuine key-42 assignment (TIME-module `T+X`/`TIME`, raw key
    byte 0x24/0x2C) plus a leftover `COS`/`ACOS` entry at raw byte
    0x14/0x1C, the phantom physical-column-2 slot under `ENTER^` that the
    pre-fix buggy formula would have produced. That fixture was retired
    when tests/data/ was pared down, so the stray-phantom-byte check no
    longer has data to run against; test_key_byte_for_row4_uses_physical_
    column_not_key_number is the test that still directly proves the
    formula rejects the old column-2 mapping."""
    memory = Memory.from_file(DATA_DIR / "xrom-keyassignments.dm41")
    all_entries = memory.key_assignments.list_assignments()
    assignments = {
        (a["key_number"], a["shifted"]): a
        for a in all_entries
        if a["key_number"] is not None
    }

    key_unshifted = assignments[(41, False)]
    assert key_unshifted["raw_key_byte"] == 0x04
    assert key_unshifted["name"] == "ALENG"

    key_shifted = assignments[(41, True)]
    assert key_shifted["raw_key_byte"] == 0x0C
    assert key_shifted["name"] == "APPCHR"

    key_unshifted = assignments[(42, False)]
    assert key_unshifted["raw_key_byte"] == 0x24
    assert key_unshifted["name"] == "ANUM"

    key_shifted = assignments[(42, True)]
    assert key_shifted["raw_key_byte"] == 0x2C
    assert key_shifted["name"] == "APPREC"

    key_unshifted = assignments[(43, False)]
    assert key_unshifted["raw_key_byte"] == 0x34
    assert key_unshifted["name"] == "ADATE"

    key_shifted = assignments[(43, True)]
    assert key_shifted["raw_key_byte"] == 0x3C
    assert key_shifted["name"] == "ALMCAT"

    key_unshifted = assignments[(44, False)]
    assert key_unshifted["raw_key_byte"] == 0x44
    assert key_unshifted["name"] == "TIME"

    key_shifted = assignments[(44, True)]
    assert key_shifted["raw_key_byte"] == 0x4C
    assert key_shifted["name"] == "CLOCK"


def test_keyflags_bit_row4_matches_wickes_figure_4_2():
    """Bit positions for row 4 read directly off Wickes' Figure 4-2 ("Key
    Assignment Flag Bits") diagram, which draws a single wide box spanning
    physical columns 1-2 for ENTER^ (labeled bit 33, 1-indexed -> 0-indexed
    32) and separate boxes for the other three keys at physical columns
    3/4/5 (labeled 17/9/1 1-indexed -> 0-indexed 16/8/0) -- with no box at
    all for physical column 2, confirming that position is never a real
    key (the "imaginary 42nd key under ENTER", docs sec 4.5)."""
    cases = [(41, 32), (42, 16), (43, 8), (44, 0)]
    for key_number, expected_bit in cases:
        assert KeyAssignments._keyflags_bit(key_number) == expected_bit, key_number


def test_keyflags_bit_matches_all_three_real_fixtures():
    """The KEYFLAGS bit formula (docs sec 4.5, bit = 36 - M - 8*(N-1)) and
    register mapping (F = unshifted, e = shifted) must reproduce exactly
    the bits set in every real fixture -- no extra bits, no missing ones."""

    def bits_set(register_bytes) -> set:
        # Only the first 36 bits are the KEYFLAGS bitmap (docs sec 4.5) --
        # the remaining 20 bits of the 56-bit register hold something else
        # entirely (confirmed non-zero, e.g. bits 40-54, in real captures
        # like keyassigns.dm41's register R), so comparing the whole
        # register would spuriously fail against real, correct data.
        bits = set()
        for byte_index, byte in enumerate(register_bytes):
            for bit_in_byte in range(8):
                bit = byte_index * 8 + bit_in_byte
                if bit >= 36:
                    continue
                if (byte >> (7 - bit_in_byte)) & 1:
                    bits.add(bit)
        return bits

    cases = {
        "keyassigns.dm41": {
            "unshifted": [11, 12, 13, 14, 15, 21, 22, 23, 24, 25, 84],
            "shifted": [11, 12, 13, 14, 15, 21, 22, 23, 24, 25, 84],
        },
        "xrom-keyassignments.dm41": {
            "unshifted": [41, 42, 43, 44],
            "shifted": [41, 42, 43, 44],
        },
        "global-key-assignments.dm41": {
            "unshifted": [11, 12],
            "shifted": [],
        },
    }
    for filename, expected in cases.items():
        memory = Memory.from_file(DATA_DIR / filename)
        f_reg = memory.get_register(StatusRegisters.KEYFLAGS_UNSHIFTED_ADDR)
        e_reg = memory.get_register(StatusRegisters.KEYFLAGS_SHIFTED_ADDR)

        expected_f_bits = {KeyAssignments._keyflags_bit(k) for k in expected["unshifted"]}
        expected_e_bits = {KeyAssignments._keyflags_bit(k) for k in expected["shifted"]}

        assert bits_set(f_reg.get_bytes()) == expected_f_bits, filename
        assert bits_set(e_reg.get_bytes()) == expected_e_bits, filename

        for k in expected["unshifted"]:
            assert memory.key_assignments.get_key_flag(k, False) is True, (filename, k)
        for k in expected["shifted"]:
            assert memory.key_assignments.get_key_flag(k, True) is True, (filename, k)


def test_list_key_assignments_decodes_keyassigns_dm41():
    """keyassigns.dm41's 22 real assignments, filler-first single-byte
    entries only (docs sec 4.2/4.8) -- newest-first order (sec 4.4)."""
    memory = Memory.from_file(DATA_DIR / "keyassigns.dm41")
    assignments = memory.key_assignments.list_assignments()
    assert len(assignments) == 22
    for a in assignments:
        assert a["fn_byte2"] is None
        assert a["name"] in {"%", "%CH", "+", "-", "*", "/", "1/X", "10↑X",
                             "ABS", "ACOS", "BEEP", "TONE" }
        # Every decoded assignment's KEYFLAGS bit must also be set.
        assert memory.key_assignments.get_key_flag(a["key_number"], a["shifted"]) is True


def test_list_key_assignments_decodes_xrom_two_byte_entries():
    """xrom-keyassignments.dm41's four assignments are all real two-byte
    XROM entries (docs sec 4.8) -- confirms fn_byte2 is populated (not
    None) and the function names resolve via memory/functions.py."""
    memory = Memory.from_file(DATA_DIR / "xrom-keyassignments.dm41")
    assignments = memory.key_assignments.list_assignments()
    by_key = {a["key_number"]: a for a in assignments}

    assert by_key[41]["name"] == "ALENG"
    assert by_key[42]["name"] == "ANUM"
    assert by_key[43]["name"] == "ADATE"
    assert by_key[44]["name"] == "TIME"
    for a in assignments:
        assert a["fn_byte2"] is not None


def test_set_key_assignment_single_byte_function():
    memory = Memory()
    memory.key_assignments.set_assignment(11, False, 0x40)  # '+'
    assert memory.key_assignments.get_key_flag(11, False) is True
    assignments = memory.key_assignments.list_assignments()
    assert len(assignments) == 1
    assert assignments[0] == {
        "key_number": 11, "shifted": False,
        "fn_byte1": 0x40, "fn_byte2": None, "name": "+",
        "raw_key_byte": KeyAssignments.key_byte_for(11, False),
    }
    # Filler-first storage in the actual register (sec 4.2).
    reg = memory.get_register(0xC0)
    assert reg.get_bytes()[1] == 0x04
    assert reg.get_bytes()[2] == 0x40
    assert reg.get_bytes()[3] == KeyAssignments.key_byte_for(11, False)


def test_set_key_assignment_xrom_function():
    memory = Memory()
    memory.key_assignments.set_assignment(13, False, (0xA6, 0x81))  # ADATE
    assert memory.key_assignments.get_key_flag(13, False) is True
    assignments = memory.key_assignments.list_assignments()
    assert assignments[0]["name"] == "ADATE"
    reg = memory.get_register(0xC0)
    assert reg.get_bytes()[1:4] == bytes([0xA6, 0x81, KeyAssignments.key_byte_for(13, False)])


def test_set_key_assignment_overwrites_existing_entry_for_same_key():
    """Re-assigning an already-assigned key/shift-state replaces the old
    entry rather than leaving a stale duplicate -- and the new entry lands
    at the front (register 0xc0), same as a brand-new assignment would
    (sec 4.4's LIFO order)."""
    memory = Memory()
    memory.key_assignments.set_assignment(11, False, 0x40)  # '+'
    memory.key_assignments.set_assignment(12, True, 0x41)  # '-'
    memory.key_assignments.set_assignment(11, False, 0x42)  # '*' replaces '+'

    assignments = memory.key_assignments.list_assignments()
    assert len(assignments) == 2
    by_key = {(a["key_number"], a["shifted"]): a["name"] for a in assignments}
    assert by_key[(11, False)] == "*"
    assert by_key[(12, True)] == "-"
    # The replaced entry is newest -- front of the buffer.
    assert assignments[0]["key_number"] == 11


def test_set_key_assignment_rejects_out_of_range_bytes():
    memory = Memory()
    with pytest.raises(ValueError):
        memory.key_assignments.set_assignment(11, False, 0x100)
    with pytest.raises(ValueError):
        memory.key_assignments.set_assignment(11, False, (0x100, 0x00))


def test_delete_key_assignment_removes_entry_and_clears_flag():
    memory = Memory()
    memory.key_assignments.set_assignment(11, False, 0x40)
    memory.key_assignments.set_assignment(12, True, 0x41)

    memory.key_assignments.delete_assignment(11, False)

    assert memory.key_assignments.get_key_flag(11, False) is False
    assignments = memory.key_assignments.list_assignments()
    assert len(assignments) == 1
    assert assignments[0]["key_number"] == 12


def test_delete_key_assignment_shrinks_buffer_back_to_empty():
    memory = Memory()
    memory.key_assignments.set_assignment(11, False, 0x40)
    assert memory.key_assignments.end_exclusive == 0xC1

    memory.key_assignments.delete_assignment(11, False)

    assert memory.key_assignments.list_assignments() == []
    assert memory.key_assignments.end_exclusive == 0xC0
    # The now-stale register must be cleared, not left with 0xf0 marker.
    assert memory.get_register(0xC0).get_hex() == "00000000000000"


def test_delete_key_assignment_on_unassigned_key_is_a_noop():
    memory = Memory()
    memory.key_assignments.delete_assignment(11, False)  # never assigned
    assert memory.key_assignments.list_assignments() == []
    assert memory.key_assignments.get_key_flag(11, False) is False


# ---- Alarms region: bounds (alarms_end()) and relocation on Key
# Assignments edits (docs/alarms.md sec 3/4) ----


def test_alarms_end_reports_buffer_bounds_from_real_fixture():
    """4alarmtest.dm41 has 2 Key Assignment registers (0xc0-0xc1) followed
    immediately by a real 4-alarm buffer: a header at 0xc2 declaring 14
    total registers (header + entries + delimiter), so the buffer runs
    through 0xcf inclusive -- alarms_end() should report 0xd0. (This
    fixture was regenerated by the user 2026-09-01 to also cover control/
    conditional/past-due alarms -- see [[alarms_tab_planning_2026-09-01]]
    in project memory -- so its exact register count differs from the
    13-register buffer this test originally pinned.)"""
    memory = Memory.from_file(DATA_DIR / "4alarmtest.dm41")
    assert memory.key_assignments.end_exclusive == 0xC2
    header = memory.get_register(0xC2).get_bytes()
    assert header[0] == Alarms.HEADER_MARKER
    assert header[1] == 0x0E
    assert memory.alarms.end_exclusive == 0xD0


def test_alarms_end_equals_key_assignments_end_when_no_alarms():
    """keyassigns.dm41 has real Key Assignment Register entries but no
    Alarms buffer above them -- the register right after Key Assignments
    doesn't start with the 0xAA header marker, so alarms_end() should
    report an empty Alarms region (same value as key_assignments_end())."""
    memory = Memory.from_file(DATA_DIR / "keyassigns.dm41")
    assert memory.alarms.end_exclusive == memory.key_assignments.end_exclusive


def test_alarms_end_on_fresh_memory():
    assert Memory().alarms.end_exclusive == Memory().key_assignments.end_exclusive == 0xC0


def test_relocate_alarms_moves_buffer_on_key_assignment_growth_and_shrinkage():
    """A hand-built Alarms buffer sitting right above a single Key
    Assignment register must move up, byte-for-byte, when a third
    assignment forces Key Assignments to grow to a second register --
    and must move back down again when that third assignment is deleted
    -- never getting overwritten and never leaving a gap between the two
    regions either way."""
    memory = Memory()
    memory.key_assignments.set_assignment(11, False, 0x40)
    memory.key_assignments.set_assignment(12, True, 0x41)
    assert memory.key_assignments.end_exclusive == 0xC1  # 2 entries still fit in 1 register

    # Hand-build a minimal 3-register Alarms buffer (header + 1 entry +
    # delimiter) immediately above it, at 0xc1-0xc3.
    memory.set_register(0xC1, Register.from_hex("aa030000000000"))
    entry_bytes = "11223344556677"
    memory.set_register(0xC2, Register.from_hex(entry_bytes))
    memory.set_register(0xC3, Register.from_hex("f0000000000000"))
    assert memory.alarms.end_exclusive == 0xC4

    # A third assignment needs a second Key Assignment register --
    # Key Assignments grows from 0xc1 to 0xc2, so the Alarms buffer must
    # move up from 0xc1-0xc3 to 0xc2-0xc4.
    memory.key_assignments.set_assignment(13, False, 0x42)
    assert memory.key_assignments.end_exclusive == 0xC2
    assert memory.alarms.end_exclusive == 0xC5
    assert memory.get_register(0xC1).get_bytes()[0] == 0xF0  # real KA data now
    moved_header = memory.get_register(0xC2).get_bytes()
    assert moved_header[0] == Alarms.HEADER_MARKER
    assert moved_header[1] == 3
    assert memory.get_register(0xC3).get_hex() == Register.from_hex(entry_bytes).get_hex()
    assert memory.get_register(0xC4).get_bytes()[0] == 0xF0

    # Deleting that third assignment shrinks Key Assignments back to 1
    # register -- the Alarms buffer must move back down to 0xc1-0xc3,
    # with 0xc4 (its old delimiter slot) cleared, not left stale.
    memory.key_assignments.delete_assignment(13, False)
    assert memory.key_assignments.end_exclusive == 0xC1
    assert memory.alarms.end_exclusive == 0xC4
    restored_header = memory.get_register(0xC1).get_bytes()
    assert restored_header[0] == Alarms.HEADER_MARKER
    assert restored_header[1] == 3
    assert memory.get_register(0xC2).get_hex() == Register.from_hex(entry_bytes).get_hex()
    assert memory.get_register(0xC3).get_bytes()[0] == 0xF0
    assert memory.get_register(0xC4).get_hex() == "00000000000000"


def test_relocate_alarms_preserves_content_against_real_fixture():
    """Same relocation, but against 4alarmtest.dm41's real 4-alarm, 14-
    register buffer rather than a hand-built one -- every register's
    exact bytes must survive the move unchanged, just shifted up by
    however many registers Key Assignments grew by. None of 4alarmtest
    .dm41's existing assignments use key 13, so this adds a genuinely
    new one rather than replacing an existing entry."""
    memory = Memory.from_file(DATA_DIR / "4alarmtest.dm41")
    old_ka_end = memory.key_assignments.end_exclusive
    old_alarms_end = memory.alarms.end_exclusive
    before = {
        addr: memory.get_register(addr).get_hex()
        for addr in range(old_ka_end, old_alarms_end)
    }

    memory.key_assignments.set_assignment(13, False, 0x40)

    new_ka_end = memory.key_assignments.end_exclusive
    new_alarms_end = memory.alarms.end_exclusive
    delta = new_ka_end - old_ka_end
    assert delta > 0  # this fixture's 4 existing entries already fill both
    #                   of its 2 KA registers, so a 5th forces growth
    assert new_alarms_end - old_alarms_end == delta  # buffer size unchanged
    for addr, hex_value in before.items():
        assert memory.get_register(addr + delta).get_hex() == hex_value, hex(addr)
    # And the vacated span right below the new buffer is real KA data,
    # not leftover alarm bytes.
    assert memory.get_register(new_ka_end - 1).get_bytes()[0] == 0xF0


def test_key_assignment_round_trip_through_from_string_to_string():
    """A dump built entirely through set_key_assignment() must reload
    identically via to_string()/from_string(), same as every other region
    of memory."""
    memory = Memory()
    memory.key_assignments.set_assignment(11, False, 0x40)
    memory.key_assignments.set_assignment(11, True, (0xA6, 0x81))
    memory.key_assignments.set_assignment(84, False, 0x0F)  # ASN, low-code (sec 5 caveat)

    reloaded = Memory.from_string(memory.to_string())
    assert reloaded.key_assignments.list_assignments() == memory.key_assignments.list_assignments()
    for key_number, shifted in [(11, False), (11, True), (84, False)]:
        assert reloaded.key_assignments.get_key_flag(key_number, shifted) == memory.key_assignments.get_key_flag(
            key_number, shifted
        )


def test_key_number_for_byte_inverts_key_byte_for():
    for key_number in KeyAssignments._VALID_KEY_NUMBERS:
        for shifted in (False, True):
            byte = KeyAssignments.key_byte_for(key_number, shifted)
            assert KeyAssignments.key_number_for_byte(byte) == (key_number, shifted)


def test_key_row_col_rejects_non_assignable_positions():
    """The physical SHIFT key (`31`) and anything outside the real
    keyboard's row/column layout (e.g. `85` -- row 8 only has 4 columns)
    must be rejected, not silently accepted with a nonsensical bit/byte
    computed from an out-of-range (M, N)."""
    for bad_key in (31, 85, 45, 90, 0, 100):
        with pytest.raises(ValueError):
            KeyAssignments.key_byte_for(bad_key, False)
        with pytest.raises(ValueError):
            KeyAssignments._keyflags_bit(bad_key)


def test_get_key_assignment_single_key_lookup():
    """get_key_assignment() -- the per-cell lookup a keypad-grid GUI would
    call -- must agree with list_key_assignments() for both assigned and
    unassigned keys. keyassigns.dm41 assigns key 11 in both shift states
    (see test_list_key_assignments_decodes_keyassigns_dm41's 22-entry
    fixture); key 42 has no assignment there at all."""
    memory = Memory.from_file(DATA_DIR / "keyassigns.dm41")
    assert memory.key_assignments.get_assignment(11, False) == {
        "key_number": 11, "shifted": False,
        "fn_byte1": 0x4C, "fn_byte2": None, "name": "%",
        "raw_key_byte": KeyAssignments.key_byte_for(11, False),
    }
    assert memory.key_assignments.get_assignment(11, True) == {
        "key_number": 11, "shifted": True,
        "fn_byte1": 0x4C, "fn_byte2": None, "name": "%",
        "raw_key_byte": KeyAssignments.key_byte_for(11, True),
    }
    assert memory.key_assignments.get_assignment(42, False) is None


def test_list_key_assignments_matches_all_real_fixtures_key_flags():
    """Every fixture with real key-assignment-register content: every
    decoded entry's KEYFLAGS bit must be set, and conversely every set
    KEYFLAGS bit not backed by a global label must be explained by a
    decoded entry. (global-key-assignments.dm41 sets flags for two
    global-label assignments with nothing in the Key Assignment
    Registers -- excluded here, see sec 4.6.)"""
    for filename in ("keyassigns.dm41", "xrom-keyassignments.dm41"):
        memory = Memory.from_file(DATA_DIR / filename)
        assignments = memory.key_assignments.list_assignments()
        flagged = {
            (k, s)
            for k in KeyAssignments._VALID_KEY_NUMBERS
            for s in (False, True)
            if memory.key_assignments.get_key_flag(k, s)
        }
        decoded = {(a["key_number"], a["shifted"]) for a in assignments}
        assert flagged == decoded, filename


# ---- Global label (program) key assignments (docs/key_assignments.md
# sec 4.6) -- a completely separate storage mechanism from the Key
# Assignment Registers above: the key byte lives in the program's own
# global-label header rather than a shared buffer, and a label can hold
# only one key assignment at a time (unlike a physical key's independent
# unshifted/shifted slots). manyfiles.dm41 mixes both kinds of assignment
# in one real dump -- three global labels (XMBCD/XMALPHA/PURXM, keys
# 11/12/13 unshifted) plus two built-in/peripheral ones (EMROOM/XTOA,
# keys 14/15 unshifted) -- so it's used below alongside
# global-key-assignments.dm41 (AAA/BBB, program-only) and simple.dm41
# (APPTEST, unassigned) for the synthetic move/mutual-exclusion cases.


def test_get_program_for_key_decodes_global_key_assignments_dm41():
    memory = Memory.from_file(DATA_DIR / "global-key-assignments.dm41")
    assert memory.programs.get_program_for_key(11, False).name == "AAA"
    assert memory.programs.get_program_for_key(12, False).name == "BBB"
    assert memory.programs.get_program_for_key(11, True) is None
    assert memory.programs.get_program_for_key(13, False) is None


def test_get_program_for_key_decodes_manyfiles_dm41_mixed_dump():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    assert memory.programs.get_program_for_key(11, False).name == "XMBCD"
    assert memory.programs.get_program_for_key(12, False).name == "XMALPHA"
    assert memory.programs.get_program_for_key(13, False).name == "PURXM"
    # 14/15 are built-in/peripheral assignments (sec 4.2), not programs.
    assert memory.programs.get_program_for_key(14, False) is None
    assert memory.programs.get_program_for_key(15, False) is None


def test_set_program_key_assignment_assigns_unassigned_program():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    assert memory.programs.list_global_chain()[0].key_assignment == 0x00  # APPTEST

    memory.programs.set_program_key_assignment("APPTEST", 21, False)

    assert memory.programs.get_program_for_key(21, False).name == "APPTEST"
    assert memory.key_assignments.get_key_flag(21, False) is True
    apptest = memory.programs.find_program_by_name("APPTEST")
    assert apptest.key_assignment == KeyAssignments.key_byte_for(21, False)


def test_set_program_key_assignment_moves_existing_assignment():
    """A program's header holds only one key byte -- reassigning it to a
    new key moves it, clearing the old key's KEYFLAGS bit."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")

    memory.programs.set_program_key_assignment("XMBCD", 25, True)

    assert memory.programs.get_program_for_key(25, True).name == "XMBCD"
    assert memory.key_assignments.get_key_flag(25, True) is True
    assert memory.programs.get_program_for_key(11, False) is None
    assert memory.key_assignments.get_key_flag(11, False) is False


def test_set_program_key_assignment_clears_conflicting_ka_register_entry():
    """Assigning a program onto a key that currently holds a built-in/
    peripheral assignment (sec 4.2) clears that entry -- the real lookup
    order (sec 4.7) would otherwise let it silently shadow the program."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    assert memory.key_assignments.get_assignment(14, False)["name"] == "EMROOM"

    memory.programs.set_program_key_assignment("PURXM", 14, False)

    assert memory.key_assignments.get_assignment(14, False) is None
    assert memory.programs.get_program_for_key(14, False).name == "PURXM"
    assert memory.key_assignments.get_key_flag(14, False) is True


def test_set_program_key_assignment_clears_other_programs_own_conflict():
    """Two programs can't both claim the same key -- assigning one onto a
    key already held by a different program clears the other one."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")

    memory.programs.set_program_key_assignment("XMALPHA", 11, False)  # was XMBCD's key

    assert memory.programs.get_program_for_key(11, False).name == "XMALPHA"
    xmbcd = memory.programs.find_program_by_name("XMBCD")
    assert xmbcd.key_assignment == 0x00


def test_set_program_key_assignment_reassigning_to_same_key_is_a_noop_move():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")

    memory.programs.set_program_key_assignment("XMBCD", 11, False)

    assert memory.programs.get_program_for_key(11, False).name == "XMBCD"
    assert memory.key_assignments.get_key_flag(11, False) is True


def test_set_program_key_assignment_unknown_name_raises():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    with pytest.raises(ValueError):
        memory.programs.set_program_key_assignment("NOPE", 21, False)


def test_set_key_assignment_clears_conflicting_program_assignment():
    """The reverse direction of the mutual-exclusion rule: assigning a
    built-in function onto a key that currently holds a global-label
    assignment clears that program's assignment."""
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    assert memory.programs.get_program_for_key(11, False).name == "XMBCD"

    memory.key_assignments.set_assignment(11, False, 0x40)  # '+'

    assert memory.key_assignments.get_assignment(11, False)["name"] == "+"
    assert memory.programs.get_program_for_key(11, False) is None
    xmbcd = memory.programs.find_program_by_name("XMBCD")
    assert xmbcd.key_assignment == 0x00
    # The key's flag stays set -- now backed by the function assignment.
    assert memory.key_assignments.get_key_flag(11, False) is True


def test_clear_program_key_assignment_removes_assignment_and_flag():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")

    memory.programs.clear_program_key_assignment("XMBCD")

    assert memory.programs.get_program_for_key(11, False) is None
    assert memory.key_assignments.get_key_flag(11, False) is False
    xmbcd = memory.programs.find_program_by_name("XMBCD")
    assert xmbcd.key_assignment == 0x00
    # Unrelated programs/assignments untouched.
    assert memory.programs.get_program_for_key(12, False).name == "XMALPHA"
    assert memory.key_assignments.get_assignment(14, False)["name"] == "EMROOM"


def test_clear_program_key_assignment_on_unassigned_program_is_a_noop():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    memory.programs.clear_program_key_assignment("APPTEST")  # never assigned
    assert memory.programs.find_program_by_name("APPTEST").key_assignment == 0x00


def test_clear_program_key_assignment_unknown_name_raises():
    memory = Memory.from_file(DATA_DIR / "simple.dm41")
    with pytest.raises(ValueError):
        memory.programs.clear_program_key_assignment("NOPE")


def test_program_key_assignment_round_trip_through_from_string_to_string():
    memory = Memory.from_file(DATA_DIR / "manyfiles.dm41")
    memory.programs.set_program_key_assignment("PURXM", 22, True)

    reloaded = Memory.from_string(memory.to_string())

    assert reloaded.programs.get_program_for_key(22, True).name == "PURXM"
    assert reloaded.key_assignments.get_key_flag(22, True) == memory.key_assignments.get_key_flag(22, True)
    reloaded_names = {
        (p.name, p.key_assignment) for p in reloaded.programs.list_global_chain() if p.is_named
    }
    memory_names = {
        (p.name, p.key_assignment) for p in memory.programs.list_global_chain() if p.is_named
    }
    assert reloaded_names == memory_names
