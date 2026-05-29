"""csv2rdf-mcp self-built FastMCP server (Phase 2 #3).

Provides starrydata-specific tools that complement togomcp's generic
``run_sparql``. Today the only tool is :func:`tools.template_curve_fetch`;
more will be added in Phase 2/3 (see ``docs/architecture/``).
"""
from csv2rdf_mcp.server import build_server

__all__ = ["build_server"]
