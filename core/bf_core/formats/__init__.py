"""File formats of BedrockForge 2: readers and writers (ТЗ раздел 9).

Tables of drillholes (Э3): `registry.detect`, `registry.read_table` → `table.TableRead`;
Micromine DAT — `micromine.read_dat`, `micromine.write_dat`; CSV/TXT — `text.read_text`;
Excel — `excel.read_excel`; numbers typed by people — `numbers`; previews — `inspect`.

Nothing here imports numpy, pandas, pyarrow, openpyxl, xlrd or numba at import time: the heavy
libraries are loaded inside the functions (the core must reach BF-READY first, ADR 0002).
"""
