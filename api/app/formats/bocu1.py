"""BOCU-1 (Binary Ordered Compression for Unicode, Unicode Technical Note #6), as ICU writes it.

Micromine writes the text of some DAT files in BOCU-1 (codepage="BOCU-1" in the header): field
names and the values of C and N fields, each field encoded on its own (the state starts afresh).
Python has no codec for it; this is a decoder after ICU's ucnvbocu.cpp, checked against ICU's
own output (tests/test_bocu1.py holds bytes produced by ICU 74).

Every code point is written as its difference from the previous one ("prev"):
- bytes 0x00–0x20 are the code points themselves (C0 controls reset prev, space keeps it);
- 0x50–0xCF: one byte, difference −64…63;
- leads 0xD0–0xFA / 0x25–0x4F: two bytes; 0xFB–0xFD / 0x22–0x24: three; 0xFE / 0x21: four;
- 0xFF resets prev. Trail bytes carry base-243 digits.
prev starts at 0x40, so an ASCII character c in a fresh field is the single byte c + 0x50.
"""

from __future__ import annotations

import numpy as np

_ASCII_PREV = 0x40
_MIDDLE = 0x90
_TRAIL_COUNT = 243
_REACH_POS_1, _REACH_NEG_1 = 63, -64
_REACH_POS_2 = _REACH_POS_1 + 43 * _TRAIL_COUNT  # 10512
_REACH_NEG_2 = _REACH_NEG_1 - 43 * _TRAIL_COUNT  # -10513
_REACH_POS_3 = _REACH_POS_2 + 3 * _TRAIL_COUNT ** 2  # 187659
_REACH_NEG_3 = _REACH_NEG_2 - 3 * _TRAIL_COUNT ** 2  # -187660
_START_POS_2, _START_POS_3, _START_POS_4 = 0xD0, 0xFB, 0xFE
_START_NEG_2, _START_NEG_3, _START_NEG_4 = 0x50, 0x25, 0x22
_RESET = 0xFF

# Trail values 0–19 use these control bytes; 20–242 are the bytes 0x21–0xFF.
_TRAIL_CONTROLS = (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
                   0x18, 0x19, 0x1C, 0x1D, 0x1E, 0x1F)
_TRAIL_VALUE = [-1] * 256
for _v, _b in enumerate(_TRAIL_CONTROLS):
    _TRAIL_VALUE[_b] = _v
for _b in range(0x21, 0x100):
    _TRAIL_VALUE[_b] = _b - 13


class Bocu1Error(ValueError):
    pass


def _prev(c: int) -> int:
    if 0x3040 <= c <= 0x309F:  # Hiragana
        return 0x3070
    if 0x4E00 <= c <= 0x9FA5:  # CJK
        return 0x4E00 - _REACH_NEG_2
    if 0xAC00 <= c <= 0xD7A3:  # Hangul
        return (0xD7A3 + 0xAC00) // 2
    return (c & ~0x7F) + _ASCII_PREV


def _lead(b: int) -> tuple[int, int]:
    """(difference without the trail digits, number of trail bytes) of a lead byte >= 0x21."""
    if b >= _START_POS_2:
        if b < _START_POS_3:
            return (b - _START_POS_2) * _TRAIL_COUNT + _REACH_POS_1 + 1, 1
        if b < _START_POS_4:
            return (b - _START_POS_3) * _TRAIL_COUNT ** 2 + _REACH_POS_2 + 1, 2
        return _REACH_POS_3 + 1, 3
    if b >= _START_NEG_3:
        return (b - _START_NEG_2) * _TRAIL_COUNT + _REACH_NEG_1, 1
    if b >= _START_NEG_4:
        return (b - _START_NEG_3) * _TRAIL_COUNT ** 2 + _REACH_NEG_2, 2
    return (b - _START_NEG_4) * _TRAIL_COUNT ** 3 + _REACH_NEG_3, 3


def decode(data: bytes, errors: str = "strict") -> str:
    """BOCU-1 bytes of one field to text (prev starts at 0x40)."""
    prev = _ASCII_PREV
    out: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        i += 1
        if b <= 0x20:
            out.append(chr(b))
            if b != 0x20:
                prev = _ASCII_PREV
            continue
        if b == _RESET:
            prev = _ASCII_PREV
            continue
        if _START_NEG_2 <= b < _START_POS_2:
            diff = b - _MIDDLE
        else:
            diff, count = _lead(b)
            if i + count > n:
                if errors == "strict":
                    raise Bocu1Error("обрыв многобайтовой последовательности BOCU-1")
                out.append("�")
                break
            digits = 0
            bad = False
            for t in data[i:i + count]:
                v = _TRAIL_VALUE[t]
                bad = bad or v < 0
                digits = digits * _TRAIL_COUNT + v
            i += count
            if bad:
                if errors == "strict":
                    raise Bocu1Error("недопустимый байт в BOCU-1")
                out.append("�")
                prev = _ASCII_PREV
                continue
            diff += digits
        c = prev + diff
        if not 0 <= c <= 0x10FFFF:
            if errors == "strict":
                raise Bocu1Error("код символа вне Unicode")
            out.append("�")
            prev = _ASCII_PREV
            continue
        out.append(chr(c))
        prev = _prev(c)
    return "".join(out)


# ASCII in a fresh field is c + 0x50: numbers (digits, sign, point, exponent) and the spaces
# around them map back byte by byte. Anything else is not part of a number.
_NUMBER_LUT = np.zeros(256, dtype=np.uint8)
_NUMBER_LUT[0x20] = 0x20
for _ch in b"0123456789+-.eE":
    _NUMBER_LUT[_ch + 0x50] = _ch
_NUMBER_LUT[0x00] = 0x20


def number_bytes(raw: np.ndarray) -> np.ndarray:
    """uint8 array of BOCU-1 number fields -> ASCII bytes (other characters become 0)."""
    return _NUMBER_LUT[raw]
