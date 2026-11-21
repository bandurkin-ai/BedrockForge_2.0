"""Modules that expose an HTTP router (`bf_core/<module>/router.py`).

An explicit list, not a scan of the package: `pkgutil.iter_modules` finds nothing inside the frozen
bf-core.exe (PyInstaller PYZ), and a missing router there showed up only as 404 in the packaged app.
tests/test_structure.py checks that this list matches the router.py files of the source tree.
"""

ROUTER_MODULES: tuple[str, ...] = (
    "archive",
    "audit",
    "blocks",
    "charts",
    "composites",
    "drillholes",
    "exporting",
    "importing",
    "intercepts",
    "jobs",
    "license",
    "ops",
    "lines",
    "projects",
    "qaqc",
    "rasters",
    "sections",
    "stats",
    "storage",
    "styles",
    "viewstate",
    "viz",
    "wireframes",
)
