"""AI-assisted Step 0 tools for csv2rdf-mcp Phase 3.

Modules:
  - inspect: CSV inspection (column types, JSON detection, uniqueness stats)
  - (future) propose: AI-driven schema proposal (rdf-config model.yaml output)
  - (future) validate: 8-trap validator (per ai-assisted-step0-workflow.md §6)
"""

from csv2rdf_step0.inspect import (
    ColumnSummary,
    CSVInspection,
    ForeignKeyCandidate,
    UniquenessReport,
    inspect_csv,
    inspect_csv_set,
)

__all__ = [
    "CSVInspection",
    "ColumnSummary",
    "ForeignKeyCandidate",
    "UniquenessReport",
    "inspect_csv",
    "inspect_csv_set",
]
