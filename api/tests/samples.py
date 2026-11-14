"""Small synthetic Micromine files shared by the import tests."""

import math

from tests.dat_builder import build, encode_bocu

CP = "ibm-5347_P100-1998"


def collar_dat() -> bytes:
    return build(
        CP,
        [
            (b"BHID", "C", 15, 0), (b"X", "R", 8, 3), (b"Y", "R", 8, 3), (b"Z", "R", 8, 3),
            (b"TDEPTH", "R", 8, 2), (b"AZ", "R", 8, 2), (b"DIP", "R", 8, 2),
            (b"STATUS", "C", 20, 0),
        ],
        [["H1", 466570.5, 4584699.7, 557.9, 300.3, 0.0, -90.0, "measured"],
         ["H2", 466543.9, 4584688.6, 561.4, 250.0, 165.0, -60.0, ""]],
    )


def survey_dat() -> bytes:
    return build(
        "BOCU-1",
        [
            (encode_bocu("BHID"), "C", 15, 0), (encode_bocu("DEPTH"), "R", 8, 3),
            (encode_bocu("BRG"), "R", 8, 3), (encode_bocu("BRG_TRUE"), "R", 8, 3),
            (encode_bocu("DIP"), "R", 8, 3),
        ],
        [[encode_bocu("H1"), 0.0, 177.6, 184.6, -89.7],
         [encode_bocu("H1"), 10.0, math.nan, 184.6, -89.7]],
    )


def assay_dat() -> bytes:
    return build(
        CP,
        [
            (b"BHID", "C", 15, 0), (b"SAMPID", "C", 20, 0), (b"FROM", "R", 8, 3), (b"TO", "R", 8, 3),
            (b"H", "R", 8, 3), (b"AU", "R", 8, 4), (b"CU", "R", 8, 5), (b"TYPE", "C", 20, 0),
        ],
        [["H1", "H1-01", 0.0, 1.0, 1.0, 0.007, 0.0003, "HOLES"],
         ["H1", "H1-02", 1.0, 2.0, 1.0, 0.012, 0.00005, "HOLES"]],
    )
