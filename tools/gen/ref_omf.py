"""Expected-content summaries of third-party OMF samples, read without the `omf` library.

BF reads OMF v1 only (ADR 0010, 9.8) and must refuse OMF 2 with a clear message. This script
parses the samples with the standard library (plus pyarrow, already in the BF stack, for the
Parquet columns of the OMF 2 tutorial) and writes tests/data/ext/expected/*.json: element
kinds, names and counts. Э11 tests compare BF's importer against these numbers; nothing here
imports `omf`.

    python tools/gen/ref_omf.py tests/data/ext/omf tests/data/ext/expected
"""

from __future__ import annotations

import gzip
import json
import struct
import sys
import zipfile
import zlib
from collections import Counter
from pathlib import Path

DTYPE_SIZE = {"<f8": 8, "<f4": 4, "<i8": 8, "<i4": 4, "|u1": 1, "<u1": 1, "<u4": 4, "<u8": 8}


def omf_kind(path: Path) -> str:
    head = path.read_bytes()[:4]
    if head == bytes.fromhex("84838281"):
        return "v1"
    if head[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        return "v2-index" if "index.json.gz" in names else "v2-project"
    return "unknown"


def summarize_v1(path: Path) -> dict:
    b = path.read_bytes()
    version = b[4:36].rstrip(b"\0").decode()
    (offset,) = struct.unpack("<Q", b[52:60])
    objs = json.loads(b[offset:])

    def count(arr_uid: str) -> int:
        arr = objs[arr_uid]
        blk = arr["array"] if "array" in arr else arr
        raw = zlib.decompress(b[blk["start"] : blk["start"] + blk["length"]])
        return len(raw) // (
            DTYPE_SIZE[blk["dtype"]]
            * (
                3
                if arr["__class__"] in ("Vector3Array", "Int3Array")
                else 2
                if arr["__class__"] in ("Vector2Array", "Int2Array")
                else 1
            )
        )

    proj = next(v for v in objs.values() if v["__class__"] == "Project")
    elements = []
    for uid in proj["elements"]:
        e = objs[uid]
        g = objs[e["geometry"]]
        item = {"class": e["__class__"], "name": e["name"], "geometry": g["__class__"]}
        if g["__class__"] == "PointSetGeometry":
            item["vertices"] = count(g["vertices"])
        elif g["__class__"] == "LineSetGeometry":
            item["vertices"], item["segments"] = count(g["vertices"]), count(g["segments"])
        elif g["__class__"] == "SurfaceGeometry":
            item["vertices"], item["triangles"] = count(g["vertices"]), count(g["triangles"])
        elif g["__class__"] == "SurfaceGridGeometry":
            item["tensor_u"], item["tensor_v"] = len(g["tensor_u"]), len(g["tensor_v"])
        elif g["__class__"] == "VolumeGridGeometry":
            item["tensor"] = [len(g["tensor_u"]), len(g["tensor_v"]), len(g["tensor_w"])]
        item["data"] = [
            {"name": objs[d]["name"], "class": objs[d]["__class__"], "location": objs[d].get("location")}
            for d in e.get("data", [])
        ]
        item["textures"] = len(e.get("textures", []))
        elements.append(item)
    return {
        "format": "OMF v1",
        "file_version": version,
        "project": proj["name"],
        "classes": dict(Counter(v["__class__"] for v in objs.values())),
        "elements": elements,
    }


def summarize_v2_project(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        pj = json.loads(z.read("project.json"))
    elements = [
        {
            "schema": e.get("schema"),
            "name": e.get("name"),
            "attributes": [a.get("name") for a in e.get("attributes", [])],
        }
        for e in pj["elements"]
    ]
    return {"format": "OMF 2 (omf-python 2.0.0a0 layout, project.json)", "project": pj["name"], "elements": elements}


def summarize_v2_index(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        idx = json.loads(gzip.decompress(z.read("index.json.gz")))
        parquet = sorted(n for n in z.namelist() if n.endswith(".parquet"))
        out = {
            "format": "OMF 2 (index.json.gz layout, Incline Design)",
            "project": idx["name"],
            "application": idx.get("application"),
            "parquet_files": len(parquet),
            "elements": [],
        }
        for e in idx["elements"]:
            geo = e.get("geometry", {})
            item = {
                "name": e["name"],
                "geometry": geo.get("type"),
                "attributes": [a["name"] for a in e.get("attributes", [])],
            }
            if geo.get("type") == "BlockModel":
                item["subblocks"] = geo["subblocks"]["subblocks"]["item_count"]
                item["grid"] = geo["grid"]
                try:
                    import io

                    import pyarrow.parquet as pq

                    t = pq.read_table(io.BytesIO(z.read(geo["subblocks"]["subblocks"]["filename"])))
                    item["parquet_columns"] = t.column_names
                    item["parquet_rows"] = t.num_rows
                except ImportError:
                    pass
            out["elements"].append(item)
    return out


def main(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.rglob("*.omf")):
        kind = omf_kind(f)
        fn = {"v1": summarize_v1, "v2-project": summarize_v2_project, "v2-index": summarize_v2_index}[kind]
        data = fn(f)
        data["file"] = str(f.relative_to(src))
        data["bytes"] = f.stat().st_size
        out = dst / (f.stem.replace(" ", "_") + "." + kind + ".json")
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(out.name, "-", data["format"])


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
