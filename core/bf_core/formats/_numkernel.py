"""numba kernel of `formats.numbers`: classifies and parses a column of text cells at C speed.

Input is the cells of a column joined with NUL and encoded as UCS-4 (uint32). The kernel decides
only what it can decide exactly as the Python reference does (`numbers.classify_reference`):
ASCII numbers on the exact fast path (mantissa < 2**53, |decimal exponent| <= 22 — one correctly
rounded multiplication or division), detection limits, digit groups, empty cells and the
"no data" marks. Everything else — Unicode digits, long mantissas, huge exponents — is returned
as UNSURE and parsed by the reference. Imported lazily: numba is heavy.
"""

from __future__ import annotations

import numpy as np

from bf_core.jit import njit

EMPTY, NUMBER, BELOW, ABOVE, BAD, IDLIKE, MARKER, UNSURE = 0, 1, 2, 3, 4, 5, 6, 7

_POW10 = np.array([10.0**i for i in range(23)], dtype=np.float64)  # exact doubles up to 1e22
_MAX_EXACT = np.uint64(1 << 53)
_TEN = np.uint64(10)
_ZERO = np.uint64(0)


@njit(cache=True)
def is_space(c):  # type: ignore[no-untyped-def]
    """`str.isspace()` of one code point (checked against the Unicode database in the tests)."""
    if c <= 0x20:
        return c == 0x20 or 0x09 <= c <= 0x0D or 0x1C <= c <= 0x1F
    if c < 0x1680:
        return c == 0x85 or c == 0xA0
    return (
        c == 0x1680 or 0x2000 <= c <= 0x200A or c == 0x2028 or c == 0x2029 or c == 0x202F or c == 0x205F or c == 0x3000
    )


@njit(cache=True)
def sure_char(c):  # type: ignore[no-untyped-def]
    """A character that is certainly neither a decimal digit nor whitespace outside ASCII."""
    return (
        c < 0x80
        or 0xA1 <= c <= 0xFF
        or 0x400 <= c <= 0x52F
        or 0x2010 <= c <= 0x2027
        or 0x2030 <= c <= 0x205E
        or c == 0x2116
    )


@njit(cache=True)
def lower(c):  # type: ignore[no-untyped-def]
    """Lower case of the letters the "no data" marks are made of (ASCII and basic Cyrillic)."""
    if 0x41 <= c <= 0x5A or 0x410 <= c <= 0x42F:
        return c + 0x20
    return c


@njit(cache=True)
def _is_marker(row, a, b, markers, marker_len):  # type: ignore[no-untyped-def]
    n = b - a
    for k in range(markers.shape[0]):
        if marker_len[k] != n:
            continue
        same = True
        for j in range(n):
            if lower(row[a + j]) != markers[k, j]:
                same = False
                break
        if same:
            return True
    return False


@njit(cache=True)
def _parse(row, a, b):  # type: ignore[no-untyped-def]
    """(kind, value) of the stripped token row[a:b]; BAD means "not a number by the ASCII grammar"."""
    p = a
    limit = 0
    c = row[p]
    if c == 0x3C or c == 0x3E:  # < >
        limit = 1 if c == 0x3C else 2
        p += 1
        while p < b and is_space(row[p]):
            p += 1
        if p == b:
            return BAD, 0.0
    negative = False
    signed = False
    if row[p] == 0x2B or row[p] == 0x2D:
        negative = row[p] == 0x2D
        signed = True
        p += 1
    m = _ZERO
    nsig = 0
    q = p
    while q < b and 0x30 <= row[q] <= 0x39:
        d = row[q] - 0x30
        if nsig > 0 or d != 0:
            nsig += 1
            if nsig <= 19:
                m = m * _TEN + np.uint64(d)
        q += 1
    int_digits = q - p
    leading_zero = int_digits > 1 and row[p] == 0x30
    frac = 0
    exp = 0
    identifier = False  # written like an identifier: a whole number, or digits-E-digits ("1E5")
    if q < b and (row[q] == 0x20 or row[q] == 0xA0 or row[q] == 0x202F):
        # Digit groups: 1–3 digits, then (separator + 3 digits) at least once, then an optional fraction.
        if int_digits < 1 or int_digits > 3:
            return BAD, 0.0
        while q < b and (row[q] == 0x20 or row[q] == 0xA0 or row[q] == 0x202F):
            if q + 3 >= b:
                return BAD, 0.0
            for j in range(1, 4):
                ch = row[q + j]
                if ch < 0x30 or ch > 0x39:
                    return BAD, 0.0
                d = ch - 0x30
                if nsig > 0 or d != 0:
                    nsig += 1
                    if nsig <= 19:
                        m = m * _TEN + np.uint64(d)
            q += 4
        if q < b and (row[q] == 0x2E or row[q] == 0x2C):
            q += 1
            start = q
            while q < b and 0x30 <= row[q] <= 0x39:
                d = row[q] - 0x30
                if nsig > 0 or d != 0:
                    nsig += 1
                    if nsig <= 19:
                        m = m * _TEN + np.uint64(d)
                q += 1
            frac = q - start
            if frac == 0:
                return BAD, 0.0
        if q != b:
            return BAD, 0.0
    else:
        has_point = False
        if q < b and (row[q] == 0x2E or row[q] == 0x2C):
            has_point = True
            q += 1
            start = q
            while q < b and 0x30 <= row[q] <= 0x39:
                d = row[q] - 0x30
                if nsig > 0 or d != 0:
                    nsig += 1
                    if nsig <= 19:
                        m = m * _TEN + np.uint64(d)
                q += 1
            frac = q - start
        if int_digits == 0 and frac == 0:
            return BAD, 0.0
        has_exp = False
        exp_signed = False
        if q < b and (row[q] == 0x65 or row[q] == 0x45):
            has_exp = True
            q += 1
            exp_negative = False
            if q < b and (row[q] == 0x2B or row[q] == 0x2D):
                exp_negative = row[q] == 0x2D
                exp_signed = True
                q += 1
            start = q
            while q < b and 0x30 <= row[q] <= 0x39:
                if exp < 100000:
                    exp = exp * 10 + (row[q] - 0x30)
                q += 1
            if q == start:
                return BAD, 0.0
            if exp_negative:
                exp = -exp
        if q != b:
            return BAD, 0.0
        if not has_point:
            identifier = (leading_zero or nsig > 15) if not has_exp else (not signed and not exp_signed)
    kind = NUMBER
    if limit == 1:
        kind = BELOW
    elif limit == 2:
        kind = ABOVE
    elif identifier:
        kind = IDLIKE
    if nsig > 19:
        return UNSURE, 0.0
    # Trailing zeros of the fraction carry no value: "0.5000" is 5 / 10.
    while frac > 0 and m != _ZERO and m % _TEN == _ZERO:
        m = m // _TEN
        frac -= 1
    value = 0.0
    if m != _ZERO:
        e10 = exp - frac
        if m >= _MAX_EXACT or e10 > 22 or e10 < -22:
            return UNSURE, 0.0
        value = float(m) * _POW10[e10] if e10 >= 0 else float(m) / _POW10[-e10]
    if negative:
        value = -value
    if limit == 1:
        value = value / 2.0
    return kind, value


@njit(cache=True, nogil=True)
def classify_joined(codes, count, markers, marker_len, values, kinds):  # type: ignore[no-untyped-def]
    """Classify `count` cells written one after another in `codes` (UCS-4), separated by NUL.

    Fills `values` (float64, NaN where there is no number) and `kinds` (uint8). Returns the number of
    cells found: it differs from `count` when a cell itself contains a NUL (the caller then takes
    the slow way).
    """
    m = len(codes)
    start = 0
    done = 0
    while done < count and start <= m:
        end = start
        while end < m and codes[end] != 0:
            end += 1
        a = start
        b = end
        while a < b and is_space(codes[a]):
            a += 1
        while b > a and is_space(codes[b - 1]):
            b -= 1
        values[done] = np.nan
        if a == b:
            kinds[done] = EMPTY
        else:
            kind, value = _parse(codes, a, b)
            if kind == BAD:
                sure = True
                for j in range(a, b):
                    if not sure_char(codes[j]):
                        sure = False
                        break
                if not sure:
                    kind = UNSURE
                elif b - a <= markers.shape[1] and _is_marker(codes, a, b, markers, marker_len):
                    kind = MARKER
            elif kind != UNSURE:
                values[done] = value
            kinds[done] = kind
        done += 1
        start = end + 1
    if start <= m:  # text left after the last cell: there were more NULs than separators
        return count + 1
    return done
