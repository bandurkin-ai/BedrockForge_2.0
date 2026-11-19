"""Small synthetic OMF v1 files for tests, assembled from JSON and zlib blocks with `struct` — independent of the
reader and of the writer (ported from BF `tests/test_omf.py::RawOmf`)."""

from __future__ import annotations

import json
import struct
import uuid
import zlib
from pathlib import Path
from typing import Any

import numpy as np

MAGIC = b"\x84\x83\x82\x81"
SAMPLE = Path(__file__).resolve().parents[3] / "tests" / "data" / "ext" / "omf"
EXPECTED = Path(__file__).resolve().parents[3] / "tests" / "data" / "ext" / "expected"
V1 = SAMPLE / "v1" / "test_file.omf"


class RawOmf:
    def __init__(self) -> None:
        self.body = bytearray()
        self.objects: dict[str, dict[str, Any]] = {}
        self.elements: list[str] = []

    def add(self, cls: str, **fields: Any) -> str:
        uid = str(uuid.uuid4())
        stamp = "2026-01-01T00:00:00Z"
        self.objects[uid] = {"date_created": stamp, "date_modified": stamp, **fields, "__class__": cls}
        return uid

    def raw_block(self, data: bytes, dtype: str) -> dict[str, Any]:
        start = 60 + len(self.body)
        self.body += data
        return {"start": start, "dtype": dtype, "length": len(data)}

    def array(self, cls: str, values: Any, dtype: str) -> str:
        data = zlib.compress(np.ascontiguousarray(np.asarray(values, dtype=dtype)).tobytes())
        return self.add(cls, array=self.raw_block(data, dtype))

    def strings(self, values: list[Any], cls: str = "StringArray") -> str:
        return self.add(cls, array=values)

    def data(self, cls: str, name: str, array: str, location: str, description: str = "", **extra: Any) -> str:
        return self.add(cls, name=name, description=description, array=array, location=location, **extra)

    def scalar(self, name: str, values: Any, location: str, dtype: str = "<f8", description: str = "") -> str:
        return self.data("ScalarData", name, self.array("ScalarArray", values, dtype), location, description)

    def mapped(self, name: str, codes: Any, labels: list[Any] | None, location: str, colours: bool = False) -> str:
        legends = []
        if colours:
            legends.append(
                self.add(
                    "Legend",
                    name="c",
                    description="",
                    values=self.strings([[1, 2, 3]] * len(labels or [0]), "ColorArray"),
                )
            )
        if labels is not None:
            legends.append(self.add("Legend", name=name, description="", values=self.strings(labels)))
        return self.data("MappedData", name, self.array("ScalarArray", codes, "<i8"), location, legends=legends)

    def element(
        self, cls: str, name: str, geometry: str, data: Any = (), color: Any = (10, 20, 30), **extra: Any
    ) -> str:
        uid = self.add(cls, name=name, description=extra.pop("description", ""), data=list(data),
                       color=list(color) if isinstance(color, tuple) else color, subtype="x", geometry=geometry,
                       **extra)  # fmt: skip
        self.elements.append(uid)
        return uid

    def surface(self, name: str, v: Any, t: Any, origin: Any = (0.0, 0.0, 0.0), data: Any = (), **extra: Any) -> str:
        geom = self.add("SurfaceGeometry", origin=list(origin), vertices=self.array("Vector3Array", v, "<f8"),
                        triangles=self.array("Int3Array", t, "<i8"))  # fmt: skip
        return self.element("SurfaceElement", name, geom, data, **extra)

    def lines(self, name: str, v: Any, s: Any, origin: Any = (0.0, 0.0, 0.0), data: Any = (), **extra: Any) -> str:
        geom = self.add("LineSetGeometry", origin=list(origin), vertices=self.array("Vector3Array", v, "<f8"),
                        segments=self.array("Int2Array", s, "<i8"))  # fmt: skip
        return self.element("LineSetElement", name, geom, data, **extra)

    def volume(self, name: str, tensors: Any, origin: Any = (0.0, 0.0, 0.0), axes: Any = None, data: Any = ()) -> str:
        axes = axes or ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])
        geom = self.add(
            "VolumeGridGeometry",
            origin=list(origin),
            tensor_u=list(tensors[0]),
            tensor_v=list(tensors[1]),
            tensor_w=list(tensors[2]),
            axis_u=list(axes[0]),
            axis_v=list(axes[1]),
            axis_w=list(axes[2]),
        )
        return self.element("VolumeElement", name, geom, data)

    def json_bytes(
        self, origin: Any = (0.0, 0.0, 0.0), description: str = "", name: str = "Ручной"
    ) -> tuple[str, bytes]:
        project = str(uuid.uuid4())
        self.objects[project] = {
            "name": name,
            "description": description,
            "author": "",
            "revision": "",
            "units": "m",
            "elements": self.elements,
            "origin": list(origin),
            "__class__": "Project",
            "date_created": "2026-01-01T00:00:00Z",
            "date_modified": "2026-01-01T00:00:00Z",
        }
        return project, json.dumps(self.objects, ensure_ascii=False).encode()

    def write(
        self,
        path: Path,
        version: bytes = b"OMF-v0.9.0",
        origin: Any = (0.0, 0.0, 0.0),
        description: str = "",
        name: str = "Ручной",
    ) -> Path:
        project, js = self.json_bytes(origin, description, name)
        path.write_bytes(MAGIC + struct.pack("<32s", version) + uuid.UUID(project).bytes
                         + struct.pack("<Q", 60 + len(self.body)) + bytes(self.body) + js)  # fmt: skip
        return path


def sample_objects() -> tuple[bytes, int, dict[str, Any]]:
    """(bytes of the v1 sample, the offset of its JSON, its objects) — decoded with the standard library only."""
    raw = V1.read_bytes()
    (offset,) = struct.unpack("<Q", raw[52:60])
    return raw, offset, json.loads(raw[offset:])


def sample_array(name_of_element: str, key: str) -> np.ndarray:
    """An array of the v1 sample inflated with `zlib.decompress` (the independent reference)."""
    raw, _offset, objs = sample_objects()
    element = next(v for v in objs.values() if v.get("name") == name_of_element and v["__class__"].endswith("Element"))
    arr = objs[objs[element["geometry"]][key]]
    blk = arr["array"]
    width = {"Vector3Array": 3, "Int3Array": 3, "Int2Array": 2}.get(arr["__class__"], 1)
    values = np.frombuffer(zlib.decompress(raw[blk["start"] : blk["start"] + blk["length"]]), dtype=blk["dtype"])
    return values.reshape(-1, width) if width > 1 else values
