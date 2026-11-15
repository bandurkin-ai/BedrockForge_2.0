"""Where the samples of a drillhole set come from: the built-in assays or an assay table of its own."""

from app.samples.source import Source, default_table, resolve_source, source_sql, source_tables

__all__ = ["Source", "default_table", "resolve_source", "source_sql", "source_tables"]
