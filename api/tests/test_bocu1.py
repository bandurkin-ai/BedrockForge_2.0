"""BOCU-1 decoder: bytes are what ICU 74 (ucnv_convert to "BOCU-1") writes for each text."""

import numpy as np
import pytest

from app.formats.bocu1 import Bocu1Error, decode, number_bytes

ICU_VECTORS = [
    ('AU_CUT', bytes.fromhex('91a5af93a5a4')),
    ('Body_12', bytes.fromhex('92bfb4c9af8182')),
    ('-12.345', bytes.fromhex('7d81827e838485')),
    ('  466170.000', bytes.fromhex('20208486868187807e808080')),
    ('Плотность', bytes.fromhex('d3d38b8e928d8e91929c')),
    ('Руда окисл. ёЁ', bytes.fromhex('d3d4938480208e8a88918b4bfa20d40651')),
    ('BHTSE-2-07', bytes.fromhex('9298a4a3957d827d8087')),
    ('日本語', bytes.fromhex('fb4cd43f8be45e')),
    ('xéy', bytes.fromhex('c8d0764ff9')),
    ('한국어', bytes.fromhex('fbc2493acbd3d7')),
    ('😀z', bytes.fromhex('fcff5d2301aa')),
    ('a\tb', bytes.fromhex('b109b2')),
]


@pytest.mark.parametrize(("text", "data"), ICU_VECTORS)
def test_decodes_what_icu_writes(text, data):
    assert decode(data) == text


def test_ascii_in_a_fresh_field_is_shifted_by_0x50_and_numbers_map_back():
    assert decode(bytes(c + 0x50 for c in b"X_NEW")) == "X_NEW"
    raw = np.frombuffer(bytes.fromhex(dict(ICU_VECTORS)["  466170.000"].hex()), dtype=np.uint8)
    assert number_bytes(raw).tobytes() == b"  466170.000"


def test_broken_input():
    with pytest.raises(Bocu1Error):
        decode(bytes([0xD3]))  # a lead byte without its trail
    assert decode(bytes([0xD3]), errors="replace") == "\ufffd"

