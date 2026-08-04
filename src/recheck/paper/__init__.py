"""Paper side: fetch e-print source and extract tables from LaTeX."""

from .fetch import FetchError, Source, fetch, load_local, parse_arxiv_id
from .tables import extract_tables, find_main_tex, resolve_inputs

__all__ = [
    "FetchError",
    "Source",
    "fetch",
    "load_local",
    "parse_arxiv_id",
    "extract_tables",
    "find_main_tex",
    "resolve_inputs",
]
