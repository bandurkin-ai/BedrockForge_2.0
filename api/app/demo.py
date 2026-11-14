"""A small synthetic project (48 drill holes) so the interface can be tried without real data."""

import math
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.engine import run_audit
from app.formats.wireframes import mesh_stats
from app.models import Assay, Collar, Component, Dataset, LineLayer, Project, Raster, Survey, Wireframe

import numpy as np

DEMO_NAME = "Демо: участок Северный"


def create_demo_project(session: Session) -> tuple[Project, bool]:
    """Returns (project, created). An existing demo project is reused, never duplicated."""
    existing = session.scalars(select(Project).where(Project.name == DEMO_NAME)).first()
    if existing is not None:
        return existing, False

    rng = random.Random(7)  # fixed seed: the demo is the same every time
    project = Project(name=DEMO_NAME)
    session.add(project)
    session.flush()
    holes = Dataset(project_id=project.id, kind="drillholes", name="Скважины 2025", meta={})
    session.add(holes)
    session.flush()
    pid = holes.id  # drillhole rows reference the set
    for i in range(48):
        row, col = divmod(i, 8)
        hole = f"DH-{i + 1:03d}"
        x = 5000 + col * 50 + rng.uniform(-5, 5)
        y = 8000 + row * 50 + rng.uniform(-5, 5)
        td = rng.choice([120, 150, 180, 220])
        az = 90 + rng.uniform(-8, 8)
        dip = -60 + rng.uniform(-5, 5)
        session.add(Collar(
            set_id=pid, hole_id=hole, x=x, y=y, z=420 + rng.uniform(-6, 6), td=td, azimuth=az, dip=dip,
            status="Завершена" if i % 7 else "В бурении", stage="2025",
        ))
        for depth in range(0, td + 1, 30):
            session.add(Survey(set_id=pid, hole_id=hole, depth=float(depth), azimuth=az + depth * 0.02, dip=dip + depth * 0.03))
        if i == 5:  # deliberate problems, so the audit has something to show
            session.add(Survey(set_id=pid, hole_id=hole, depth=td + 40.0, azimuth=az, dip=dip))
        depth = 0.0
        while depth < td:
            ore = math.exp(-((depth - 90 - col * 6) ** 2) / 900)
            au = round(max(0.005, rng.lognormvariate(-2.2, 0.9) + 4 * ore * rng.random()), 3)
            cu = round(max(0.01, rng.gauss(0.2, 0.08) + 0.6 * ore), 3)
            session.add(Assay(
                set_id=pid, hole_id=hole, from_m=depth, to_m=depth + 2.0,
                sample_id=f"{hole}-{int(depth / 2):03d}", analytes={"AU": au, "CU": cu},
            ))
            depth += 2.0
        if i == 11:
            session.add(Assay(set_id=pid, hole_id=hole, from_m=10.0, to_m=13.0, sample_id=f"{hole}-X", analytes={"AU": -1.0}))
    session.add_all([Component(set_id=pid, name="AU", unit="г/т"), Component(set_id=pid, name="CU", unit="%")])
    session.flush()
    _add_orebody(session, project.id)
    _add_lines(session, project.id)
    _add_map(session, project.id)
    run_audit(session, pid)
    session.commit()
    return project, True


def _ellipsoid(center, radii, rotation_deg: float, rings: int = 18, segments: int = 36):
    """A closed triangulated ellipsoid, tilted around the Y axis (dipping east, like the ore zone)."""
    theta = np.linspace(0, np.pi, rings + 1)[1:-1]
    phi = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    t, p = np.meshgrid(theta, phi, indexing="ij")
    unit = np.column_stack([(np.sin(t) * np.cos(p)).ravel(), (np.sin(t) * np.sin(p)).ravel(), np.cos(t).ravel()])
    unit = np.vstack([[0, 0, 1], unit, [0, 0, -1]])
    pts = unit * np.asarray(radii)
    a = np.radians(rotation_deg)
    rot = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    pts = pts @ rot.T + np.asarray(center)

    def idx(r, s):
        return 1 + r * segments + (s % segments)

    tris = [(0, idx(0, s + 1), idx(0, s)) for s in range(segments)]
    for r in range(rings - 2):
        for s in range(segments):
            a0, a1, b0, b1 = idx(r, s), idx(r, s + 1), idx(r + 1, s), idx(r + 1, s + 1)
            tris += [(a0, a1, b1), (a0, b1, b0)]
    last = len(pts) - 1
    tris += [(last, idx(rings - 2, s), idx(rings - 2, s + 1)) for s in range(segments)]
    return pts, np.array(tris, dtype=np.int32)


def _add_orebody(session: Session, project_id: int) -> None:
    dataset = Dataset(project_id=project_id, kind="wireframes", name="Каркасы (демо)", meta={"format": "демо"})
    session.add(dataset)
    session.flush()
    for name, color, center, radii, tilt in [
        ("Рудное тело", "#e8873a", (5230, 8125, 330), (210, 150, 22), 6),
        ("Ореол", "#4f9dde", (5230, 8125, 330), (260, 190, 45), 6),
    ]:
        v, t = _ellipsoid(center, radii, tilt)
        session.add(Wireframe(
            dataset_id=dataset.id, name=name, color=color, point_count=len(v), triangle_count=len(t),
            extent={"min": np.round(v.min(axis=0), 2).tolist(), "max": np.round(v.max(axis=0), 2).tolist()},
            stats=mesh_stats(v, t), vertices=v.astype("<f8").tobytes(), triangles=t.astype("<i4").tobytes(),
        ))


def _add_lines(session: Session, project_id: int) -> None:
    """Licence area outlines (closed, flat) and a road along the slope."""
    from app.formats.lines import Polyline, line_stats, pack

    dataset = Dataset(project_id=project_id, kind="lines", name="Контуры (демо)", meta={"format": "демо"})
    session.add(dataset)
    session.flush()
    area = [(4930, 7940), (5450, 7940), (5450, 8320), (4930, 8320), (4930, 7940)]
    zone = [(5080, 8020), (5390, 8030), (5420, 8230), (5100, 8240), (5080, 8020)]
    road = [(4880, 7900, 412), (5000, 7990, 418), (5150, 8080, 424), (5300, 8200, 427), (5500, 8380, 431)]
    for name, color, lines in [
        ("Лицензионный участок", "#46c7c0", [Polyline(np.array([(x, y, 430.0) for x, y in area]), True, {"Участок": "Северный"})]),
        ("Перспективная зона", "#e0c040", [Polyline(np.array([(x, y, 430.0) for x, y in zone]), True, {"Участок": "Зона 1"})]),
        ("Дорога", "#c06fd8", [Polyline(np.array(road, dtype=float), False, {})]),
    ]:
        vertices, offsets, closed = pack(lines)
        session.add(LineLayer(
            dataset_id=dataset.id, name=name, color=color, line_count=len(lines), point_count=len(vertices),
            extent={"min": vertices.min(axis=0).tolist(), "max": vertices.max(axis=0).tolist()},
            stats=line_stats(lines), line_attributes=[line.attributes for line in lines],
            vertices=vertices.astype("<f8").tobytes(), offsets=offsets.astype("<i8").tobytes(), closed=closed.tobytes(),
        ))


def _add_map(session: Session, project_id: int) -> None:
    """A synthetic geological map: rock units in bands, a grid and a frame, georeferenced to the holes."""
    import io

    from PIL import Image, ImageDraw

    width, height, pixel = 800, 640, 0.75  # 600 × 480 m
    x0, y0 = 4900.0, 8380.0  # top-left corner
    image = Image.new("RGB", (width, height), "#d9cfb4")
    draw = ImageDraw.Draw(image)
    for i, color in enumerate(["#c9b98f", "#b7c8a1", "#d4a98a", "#a9bfcf", "#cfc39a"]):
        offset = i * 150 - 120
        draw.polygon([(offset, 0), (offset + 170, 0), (offset + 330, height), (offset + 160, height)], fill=color)
    for g in range(0, width, 80):
        draw.line([(g, 0), (g, height)], fill="#8a8170", width=1)
    for g in range(0, height, 80):
        draw.line([(0, g), (width, g)], fill="#8a8170", width=1)
    draw.rectangle([0, 0, width - 1, height - 1], outline="#4a4436", width=4)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)

    dataset = Dataset(project_id=project_id, kind="rasters", name="Геологическая карта (демо).jpg", meta={"format": "JPG"})
    session.add(dataset)
    session.flush()
    affine = [pixel, 0.0, x0, 0.0, -pixel, y0]
    pts = [(x0, y0), (x0 + width * pixel, y0), (x0 + width * pixel, y0 - height * pixel), (x0, y0 - height * pixel)]
    session.add(Raster(
        dataset_id=dataset.id, name="Геологическая карта", width=width, height=height,
        georef={"affine": affine, "source": "World-файл", "crs": None, "pixel_size": pixel, "rotation": 0.0,
                "corners": [list(p) for p in pts], "checks": [], "notes": []},
        extent={"min": [x0, y0 - height * pixel], "max": [x0 + width * pixel, y0]},
        elevation=405.0, opacity=0.9, image=out.getvalue(), mime="image/jpeg",
    ))
