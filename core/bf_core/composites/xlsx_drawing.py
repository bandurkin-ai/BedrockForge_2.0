"""The look of the streaming XLSX book (`ore_xlsx.Book`; ADR 0020 §8, решение 15) apart from the sheet data:
the cell styles (`Styles`: the fixed ones of Э12, then those made by `Book.style` — number formats, fills) and
the pictures (a PNG anchored at a cell of a sheet — the image in `xl/media`, a drawing part per sheet in
`xl/drawings` with its relationships, the sheet's relationship to it and the content types). A book that makes
no style and no picture has the parts of Э12 byte for byte.
"""

from __future__ import annotations

import re
from collections.abc import Callable

__all__ = [
    "CONTENT_PNG",
    "TONES",
    "Picture",
    "Styles",
    "content_override",
    "picture",
    "png_size",
    "sheet_drawing",
    "write_pictures",
]

#: png, row, col (0-based cell of the top left corner), width, height (pixels), name
Picture = tuple[bytes, int, int, int, int, str]
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_EMU = 9525  # EMU in a pixel at 96 dpi
LINE_PX = 20  # the default height of a line (15 pt)
CONTENT_PNG = '<Default Extension="png" ContentType="image/png"/>'
#: tones of the form (`ore_protocol` cells) → fill (ARGB); the classes in the brand colours mixed with white
TONES = {
    "ore": "FFFBC3A3",
    "subgrade": "FFFBE1CF",
    "waste_in": "FFE3E7EC",
    "interlayer": "FFB3CDFF",
    "waste": "FFC9CFD6",
    "frame": "FFEEF0F3",
    "pass": "FFD6E8D4",
    "reject": "FFF6D2D2",
    "none": "FFF4F5F7",
}
TONE_IDS = {t: 3 + k for k, t in enumerate(TONES)}  # the style (and the fill) of a tone
#: a cell format: (numFmtId, fontId, fillId, borderId, wrap, quote); the fixed ones are those of Э12
XF = tuple[int, int, int, int, bool, bool]
_FIXED: list[XF] = [(0, 0, 0, 0, False, False), (0, 1, 2, 0, True, False), (0, 2, 0, 0, False, False)]
_FIXED += [(0, 0, 3 + k, 1, True, False) for k in range(len(TONES))]
#: the format codes Excel has without a <numFmt> (ECMA-376 1, 18.8.30)
_BUILTIN_FMT = {"General": 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10, "0.00E+00": 11}


def _attr(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _xf(x: XF) -> str:
    fmt, font, fill, border, wrap, quote = x
    s = f'<xf numFmtId="{fmt}" fontId="{font}" fillId="{fill}" borderId="{border}"'
    s += ' applyNumberFormat="1"' if fmt else ""
    s += ' applyFont="1"' if font else ""
    s += ' applyFill="1"' if fill else ""
    s += ' applyBorder="1"' if border else ""
    s += ' quotePrefix="1"' if quote else ""
    return s + ('><alignment wrapText="1" vertical="top"/></xf>' if wrap else "/>")


def _fill(argb: str) -> str:
    return f'<fill><patternFill patternType="solid"><fgColor rgb="{argb}"/></patternFill></fill>'


class Styles:
    """The cell formats of a book: the fixed ones (0, HEAD, NOTE, the tones), then the made ones."""

    def __init__(self) -> None:
        self.xfs: list[XF] = list(_FIXED)
        self._ids: dict[XF, int] = {x: k for k, x in enumerate(_FIXED)}
        self._fills: list[str] = []
        self._fmts: dict[str, int] = {}

    def make(self, fmt: str | None, fill: str | None, base: int, quote: bool) -> int:
        if not 0 <= base < len(self.xfs):
            raise ValueError(f"no cell style {base}")
        b = self.xfs[base]
        if fmt is None:
            num = b[0]
        else:
            num = _BUILTIN_FMT[fmt] if fmt in _BUILTIN_FMT else self._fmts.setdefault(fmt, 164 + len(self._fmts))
        x: XF = (num, b[1], b[2] if fill is None else self._fill_id(fill), b[3], b[4], quote or b[5])
        k = self._ids.get(x)
        if k is None:
            k = self._ids[x] = len(self.xfs)
            self.xfs.append(x)
        return k

    def _fill_id(self, fill: str) -> int:
        if fill in TONE_IDS:
            return TONE_IDS[fill]
        c = fill.lstrip("#").upper()
        argb = c if len(c) == 8 else "FF" + c
        if not re.fullmatch("[0-9A-F]{8}", argb):
            raise ValueError(f"not a colour: {fill}")
        if argb not in self._fills:
            self._fills.append(argb)
        return 3 + len(TONES) + self._fills.index(argb)

    def xml(self) -> str:
        """styles.xml: the fonts, fills, borders and formats of Э12, then the made ones."""
        fills = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>']
        fills += [_fill(c) for c in ("FFE6E9EE", *TONES.values(), *self._fills)]
        edge = '<{0} style="thin"><color rgb="FF8A99A8"/></{0}>'
        border = "".join(edge.format(s) for s in ("left", "right", "top", "bottom"))
        fm = self._fmts
        codes = "".join(f'<numFmt numFmtId="{i}" formatCode="{_attr(c)}"/>' for c, i in fm.items())
        return (
            f'{_XML}<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + (f'<numFmts count="{len(fm)}">{codes}</numFmts>' if fm else "")
            + '<fonts count="3"><font><sz val="10"/><color rgb="FF1B222B"/><name val="Inter"/></font>'
            '<font><b/><sz val="10"/><color rgb="FF1B222B"/><name val="Montserrat"/></font>'
            '<font><i/><sz val="10"/><color rgb="FF8A99A8"/><name val="Inter"/></font></fonts>'
            f'<fills count="{len(fills)}">{"".join(fills)}</fills>'
            '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
            f"<border>{border}<diagonal/></border></borders>"
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            f'<cellXfs count="{len(self.xfs)}">{"".join(_xf(x) for x in self.xfs)}</cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
        )


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height (pixels) of a PNG image from its IHDR."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG image")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def picture(png: bytes, row: int, col: int, scale: float, width: int | None, height: int | None, name: str) -> Picture:
    """A picture sized `width` × `height` pixels; one of them — the other keeps the aspect; none — the image's
    own size × `scale` (0.5 for a picture rendered at 2×)."""
    w, h = png_size(png)
    if row < 0 or col < 0:
        raise ValueError("the cell of a picture is 0-based and not negative")
    if width is None and height is None:
        cx, cy = w * scale, h * scale
    elif width is None:
        cx, cy = w * (height or 0) / max(h, 1), float(height or 0)
    elif height is None:
        cx, cy = float(width), h * width / max(w, 1)
    else:
        cx, cy = float(width), float(height)
    return bytes(png), row, col, max(round(cx), 1), max(round(cy), 1), name


def sheet_drawing() -> str:
    """The element of a sheet pointing at its drawing (after `mergeCells`); the namespace is declared on it,
    so the head of a sheet stays that of Э12."""
    return f'<drawing xmlns:r="{REL}" r:id="rId1"/>'


def content_override(index: int) -> str:
    return (
        f'<Override PartName="/xl/drawings/drawing{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
    )


def _anchor(k: int, pic: Picture) -> str:
    _, row, col, w, h, name = pic
    cx, cy = w * _EMU, h * _EMU
    name = _attr(name)
    return (
        f"<xdr:oneCellAnchor><xdr:from><xdr:col>{col}</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{row}</xdr:row>"
        f'<xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:ext cx="{cx}" cy="{cy}"/><xdr:pic><xdr:nvPicPr>'
        f'<xdr:cNvPr id="{k + 2}" name="{name}"/><xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr>'
        f'</xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId{k + 1}"/><a:stretch><a:fillRect/></a:stretch>'
        f'</xdr:blipFill><xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/></xdr:oneCellAnchor>'
    )


def write_pictures(put: Callable[[str, bytes | str, bool], None], index: int, pics: list[Picture], media: int) -> int:
    """Writes the pictures of sheet `index` through `put(name, data, stored)`; `media` — images written before
    in the book (their numbers go on). Returns the new count."""
    rels = []
    for k, pic in enumerate(pics):
        media += 1
        put(f"xl/media/image{media}.png", pic[0], True)
        rels.append(f'<Relationship Id="rId{k + 1}" Type="{REL}/image" Target="../media/image{media}.png"/>')
    anchors = "".join(_anchor(k, pic) for k, pic in enumerate(pics))
    put(
        f"xl/drawings/drawing{index}.xml",
        f'{_XML}<xdr:wsDr xmlns:xdr="{_XDR}" xmlns:a="{_A}" xmlns:r="{REL}">{anchors}</xdr:wsDr>',
        False,
    )
    put(
        f"xl/drawings/_rels/drawing{index}.xml.rels",
        f'{_XML}<Relationships xmlns="{_PKG}">{"".join(rels)}</Relationships>',
        False,
    )
    put(
        f"xl/worksheets/_rels/sheet{index}.xml.rels",
        f'{_XML}<Relationships xmlns="{_PKG}"><Relationship Id="rId1" Type="{REL}/drawing" '
        f'Target="../drawings/drawing{index}.xml"/></Relationships>',
        False,
    )
    return media
