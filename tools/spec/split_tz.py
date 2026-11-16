"""Split the TZ (docx converted by pandoc to GFM) into docs/spec/*.md.

pandoc docs/TZ/BF2-TZ.docx -t gfm --wrap=none -o /tmp/tz.md
python3 tools/spec/split_tz.py /tmp/tz.md docs/spec

Sections 1-6 and 9-17 -> one file each; sections 7 and 8 -> one file per ### module
(7.0 / 8.0 go into the section intro file); appendices -> one file each.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CYR = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
LAT = ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
       "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]
TRANSLIT = dict(zip(CYR, LAT, strict=True))
APPENDIX = {"А": "a", "Б": "b", "В": "v", "Г": "g", "Д": "d", "Е": "e", "Ж": "zh"}


def slug(text: str, words: int = 5) -> str:
    s = "".join(TRANSLIT.get(c, c) for c in text.lower())
    s = re.sub(r"\\\[.*?\\\]|\[.*?\]|\(.*?\)", " ", s)
    return "-".join(re.findall(r"[a-z0-9]+", s)[:words])


def split(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    files: list[tuple[str, str, list[str]]] = []
    cur: list[str] | None = None

    def start(name: str, title: str) -> list[str]:
        body: list[str] = []
        files.append((name, title, body))
        return body

    for line in lines:
        if re.match(r"^# ", line):
            cur = None
            continue
        m2 = re.match(r"^## (\d+)\. (.*)", line)
        ma = re.match(r"^## Приложение (\S)\. (.*)", line)
        m3 = re.match(r"^### ([78])\.(\d+)\. (.*)", line)
        if m2:
            n = int(m2.group(1))
            name = f"{n:02d}-00-intro-{slug(m2.group(2), 4)}" if n in (7, 8) else f"{n:02d}-{slug(m2.group(2))}"
            cur = start(name, line[3:])
        elif ma:
            cur = start(f"pril-{APPENDIX[ma.group(1)]}-{slug(ma.group(2), 4)}", line[3:])
        elif m3 and int(m3.group(2)) != 0 and cur is not None:
            cur = start(f"{int(m3.group(1)):02d}-{int(m3.group(2)):02d}-{slug(m3.group(3))}", line[4:])
        if cur is None:
            if not line.strip():
                continue
            cur = start("00-preamble", "Преамбула")
        cur.append(line)
    return files


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for name, title, body in split(src.read_text(encoding="utf-8").splitlines()):
        (out / f"{name}.md").write_text("\n".join(body).strip() + "\n", encoding="utf-8")
        print(f"{name}.md\t{title}")


if __name__ == "__main__":
    main()
