"""Core library for parsing, ingesting and exporting RCN GML data.

The parser and exporters are adapted from a peer-reviewed internal QGIS
viewer tool.  Behaviour (parsing, EPSG detection, XY swap heuristic,
XLSX output format) must remain observably identical.
"""

from rcn_core.parser import NS, ParsedData, RcnGmlParser

__all__ = ["NS", "ParsedData", "RcnGmlParser"]
