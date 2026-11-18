"""Import of a georeferenced image (ADR 0011 «Замеры»: «Импорт растра — 10 / 100 Мпикс JPEG + JGW ≤ 3 с / ≤ 15 с;
≤ 1,5 ГБ; 401 Мпикс → отказ ≤ 1 с без выделения памяти»; ТЗ 7.19).

A JPEG of `megapixels` (a relief-like picture) with its world file → the job `import.objects` into a project
database: the header, the georeference, the display copies (≤ 4096 and ≤ 1024 px, decoded by bands), the object
and its rows, the source file and the sidecar into the project storage (`import_s`).
`georef_error_m` — how far the centre of the top-left pixel is from where the world file puts it.
Then a BMP header that declares 20050 × 20000 pixels (401 Mpix) goes through the same job: it is refused by
the header alone — `refuse_s`, and `refuse_mb` of memory the process took for it.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

WORLD = (0.25, 0.0, 0.0, -0.25, 512_000.125, 5_912_000.875)  # A, D, B, E, C, F: C and F — the centre of a pixel


def _side(megapixels: float) -> tuple[int, int]:
    import math

    height = int(round(math.sqrt(megapixels * 1e6 / 1.25)))
    return int(round(megapixels * 1e6 / height)), height


def prepare(params: dict[str, Any], work: Path) -> None:
    import struct

    import numpy as np
    from PIL import Image

    from bf_core.projects.project import create_folder

    width, height = _side(float(params["megapixels"]))
    Image.MAX_IMAGE_PIXELS = None
    # Smooth shapes with fine grain on top (as an orthophoto: about 0.3 MB of JPEG a megapixel), made by bands.
    rng = np.random.default_rng(21)
    small = rng.integers(0, 255, (height // 64 + 2, width // 64 + 2, 3), dtype=np.uint8)
    pixels = np.array(Image.fromarray(small).resize((width, height), Image.Resampling.BICUBIC))
    grain = rng.integers(-24, 25, (512, 512, 1), dtype=np.int16)
    for top in range(0, height, 512):
        band = pixels[top : top + 512]
        noise = np.tile(grain, (1, width // 512 + 1, 1))[: len(band), :width]
        band[:] = np.clip(band.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    Image.fromarray(pixels).save(work / "ortho.jpg", quality=85)
    del pixels
    (work / "ortho.jgw").write_text("\n".join(repr(v) for v in WORLD) + "\n", encoding="ascii")
    info = struct.pack("<IiiHHIIiiII", 40, 20050, 20000, 1, 24, 0, 0, 0, 0, 0, 0)  # 401 Mpix declared, no pixels
    bomb = b"BM" + struct.pack("<IHHI", 54, 0, 0, 54) + info
    (work / "bomb.bmp").write_bytes(bomb)
    (work / "bomb.bpw").write_text("\n".join(repr(v) for v in WORLD) + "\n", encoding="ascii")
    folder = create_folder(work, "Растр", {})
    (work / "folder.txt").write_text(str(folder), encoding="utf-8")


def load() -> None:
    import numpy  # noqa: F401
    import PIL.Image  # noqa: F401
    import PIL.JpegImagePlugin  # noqa: F401

    import bf_core.formats.georef  # noqa: F401
    import bf_core.formats.raster_image  # noqa: F401
    import bf_core.importing.objects  # noqa: F401
    import bf_core.rasters.store  # noqa: F401


def _rss_mb() -> float:
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def run(params: dict[str, Any], work: Path) -> dict[str, Any]:
    import time

    from bf_core.db import dialect
    from bf_core.importing.objects import import_objects
    from bf_core.jobs.context import JobContext, JobError
    from bf_core.rasters import store as rs

    folder = Path((work / "folder.txt").read_text(encoding="utf-8"))
    stamp = time.time_ns() // 1_000_000 % 1_000_000_000  # a repeated run is a new job

    def job(job_id: int, files: list[str]) -> dict[str, Any]:
        spec = {"id": job_id, "params": {"files": [str(work / f) for f in files]}, "project_folder": str(folder)}
        ctx = JobContext(spec, lambda _message: None, threading.Event())
        try:
            return dict(import_objects(ctx))
        finally:
            ctx.close()

    t0 = time.perf_counter()
    result = job(stamp, ["ortho.jpg", "ortho.jgw"])
    out: dict[str, Any] = {"import_s": round(time.perf_counter() - t0, 3)}
    width, height = _side(float(params["megapixels"]))
    assert result["rasters"] == 1 and not result["failed"], result
    engine = dialect.create_engine(dialect.sqlite_url(str(folder / "project.sqlite")))
    try:
        with dialect.read_tx(engine) as conn:
            info = rs.raster_info(conn, int(result["objects"][0]))
    finally:
        engine.dispose()
    assert (info["width"], info["height"]) == (width, height), info
    a, b, c, d, e, f = info["georef"]["affine"]
    out["georef_error_m"] = max(abs(a * 0.5 + b * 0.5 + c - WORLD[4]), abs(d * 0.5 + e * 0.5 + f - WORLD[5]))
    out.update(megapixels=round(width * height / 1e6, 1), file_mb=round((work / "ortho.jpg").stat().st_size / 2**20, 1))
    out["displays"] = [[lvl["width"], lvl["height"]] for lvl in info["displays"]] if "displays" in info else None

    before = _rss_mb()
    t0 = time.perf_counter()
    try:
        job(stamp + 1, ["bomb.bmp", "bomb.bpw"])
        raise AssertionError("401 Mpix was not refused")
    except JobError as exc:
        assert exc.code == "too_large", exc.code
    out["refuse_s"] = round(time.perf_counter() - t0, 4)
    out["refuse_mb"] = round(max(0.0, _rss_mb() - before), 1)
    return out
