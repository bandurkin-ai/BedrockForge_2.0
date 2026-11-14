"""A small synthetic project (48 drill holes) so the interface can be tried without real data."""

import math
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.engine import run_audit
from app.models import Assay, Collar, Component, Project, Survey

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
    pid = project.id
    for i in range(48):
        row, col = divmod(i, 8)
        hole = f"DH-{i + 1:03d}"
        x = 5000 + col * 50 + rng.uniform(-5, 5)
        y = 8000 + row * 50 + rng.uniform(-5, 5)
        td = rng.choice([120, 150, 180, 220])
        az = 90 + rng.uniform(-8, 8)
        dip = -60 + rng.uniform(-5, 5)
        session.add(Collar(
            project_id=pid, hole_id=hole, x=x, y=y, z=420 + rng.uniform(-6, 6), td=td, azimuth=az, dip=dip,
            status="Завершена" if i % 7 else "В бурении", stage="2025",
        ))
        for depth in range(0, td + 1, 30):
            session.add(Survey(project_id=pid, hole_id=hole, depth=float(depth), azimuth=az + depth * 0.02, dip=dip + depth * 0.03))
        if i == 5:  # deliberate problems, so the audit has something to show
            session.add(Survey(project_id=pid, hole_id=hole, depth=td + 40.0, azimuth=az, dip=dip))
        depth = 0.0
        while depth < td:
            ore = math.exp(-((depth - 90 - col * 6) ** 2) / 900)
            au = round(max(0.005, rng.lognormvariate(-2.2, 0.9) + 4 * ore * rng.random()), 3)
            cu = round(max(0.01, rng.gauss(0.2, 0.08) + 0.6 * ore), 3)
            session.add(Assay(
                project_id=pid, hole_id=hole, from_m=depth, to_m=depth + 2.0,
                sample_id=f"{hole}-{int(depth / 2):03d}", analytes={"AU": au, "CU": cu},
            ))
            depth += 2.0
        if i == 11:
            session.add(Assay(project_id=pid, hole_id=hole, from_m=10.0, to_m=13.0, sample_id=f"{hole}-X", analytes={"AU": -1.0}))
    session.add_all([Component(project_id=pid, name="AU", unit="г/т"), Component(project_id=pid, name="CU", unit="%")])
    session.flush()
    run_audit(session, pid)
    session.commit()
    return project, True
