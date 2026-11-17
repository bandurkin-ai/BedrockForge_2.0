"""BOCU-1: the byte strings are what ICU 74 (ucnv_convert to "BOCU-1") writes for each text."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.formats.bocu1 import Bocu1Error, decode, encode, number_bytes

ICU_VECTORS = [
    ("AU_CUT", bytes.fromhex("91a5af93a5a4")),
    ("Body_12", bytes.fromhex("92bfb4c9af8182")),
    ("-12.345", bytes.fromhex("7d81827e838485")),
    ("  466170.000", bytes.fromhex("20208486868187807e808080")),
    ("Плотность", bytes.fromhex("d3d38b8e928d8e91929c")),
    ("Руда окисл. ёЁ", bytes.fromhex("d3d4938480208e8a88918b4bfa20d40651")),
    ("BHTSE-2-07", bytes.fromhex("9298a4a3957d827d8087")),
    ("日本語", bytes.fromhex("fb4cd43f8be45e")),
    ("xéy", bytes.fromhex("c8d0764ff9")),
    ("한국어", bytes.fromhex("fbc2493acbd3d7")),
    ("😀z", bytes.fromhex("fcff5d2301aa")),
    ("a\tb", bytes.fromhex("b109b2")),
]


@pytest.mark.parametrize(("text", "data"), ICU_VECTORS)
def test_decodes_what_icu_writes(text: str, data: bytes) -> None:
    assert decode(data) == text


@pytest.mark.parametrize(("text", "data"), ICU_VECTORS)
def test_encodes_as_icu_does(text: str, data: bytes) -> None:
    assert encode(text) == data


def test_encode_decode_round_trip_on_every_plane() -> None:
    rng = np.random.default_rng(7)
    pools = [(0x20, 0x7F), (0x400, 0x500), (0x3040, 0x30A0), (0x4E00, 0x9FA6), (0xAC00, 0xD7A4), (0x1F600, 0x1F650)]
    for _ in range(300):
        chars = []
        for _ in range(int(rng.integers(1, 30))):
            lo, hi = pools[int(rng.integers(0, len(pools)))]
            chars.append(chr(int(rng.integers(lo, hi))))
        text = "".join(chars)
        assert decode(encode(text)) == text
    edge = "\x00\x01\x1f \x7f\x80߿ࠀ\ud7ff\ue000\uffff\U00010000\U0010ffff"
    assert decode(encode(edge)) == edge


def test_ascii_in_a_fresh_field_is_shifted_by_0x50_and_numbers_map_back() -> None:
    assert decode(bytes(c + 0x50 for c in b"X_NEW")) == "X_NEW"
    raw = np.frombuffer(dict(ICU_VECTORS)["  466170.000"], dtype=np.uint8)
    assert number_bytes(raw).tobytes() == b"  466170.000"


def test_broken_input() -> None:
    with pytest.raises(Bocu1Error):
        decode(bytes([0xD3]))  # a lead byte without its trail
    assert decode(bytes([0xD3]), errors="replace") == "\ufffd"
    with pytest.raises(Bocu1Error):
        decode(bytes([0xD3, 0x07]))  # 0x07 is not a trail byte
    assert decode(bytes([0xD3, 0x07, 0x91]), errors="replace") == "\ufffdA"
