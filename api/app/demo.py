"""A small synthetic project (48 drill holes) so the interface can be tried without real data."""

import math
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.engine import run_audit
from app.formats.wireframes import mesh_stats
from app.models import Assay, Collar, Component, Dataset, Project, Survey, Wireframe

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
