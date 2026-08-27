"""Single entry point for parsing a cell that may be JSON *or* a Python literal repr.

Why this exists
----------------
Real-world CSV exports do not always hold valid JSON. In particular, a pandas
``DataFrame`` with a ``dict``/``list``-valued column written via ``to_csv()``
serializes each cell with Python's ``repr()`` — single-quoted keys/strings,
``True``/``False``/``None`` instead of ``true``/``false``/``null`` — which
``json.loads`` rejects outright. This module is the *only* place in the Tier 0
library that reaches for ``ast.literal_eval`` to read that shape, so the
security review for "do we ever interpret untrusted text as Python" has exactly
one place to look.

Safety notes
------------
- ``ast.literal_eval`` is not ``eval``. It compiles the input to an AST with
  ``compile(..., mode="eval", flags=ast.PyCF_ONLY_AST)`` and then recursively
  rebuilds *only* literal nodes (numbers, strings, bytes, tuples, lists, dicts,
  sets, booleans, ``None``, and unary +/- on numeric literals). A function call,
  a name lookup, an attribute access, a subscript, or any other executable node
  makes the walk raise ``ValueError`` — there is no code path from "input string"
  to "arbitrary Python runs". This is the decisive difference from ``eval``.
- That leaves only a *denial-of-service* attack surface: an attacker cannot run
  code, but could still try to make parsing itself expensive or crash the
  process. Two bounds close that off, checked *before* the expensive call:

  1. ``_MAX_INPUT_BYTES`` (1 MiB) rejects an oversized cell outright — a bound
     on parse time and on the size of the resulting object graph.
  2. ``_MAX_DEPTH`` (64) rejects a deeply nested literal (``"[" * N + "]" * N``)
     *before* calling ``ast.literal_eval``: CPython's own AST compiler and
     ``literal_eval``'s recursive reconstruction both blow the C stack on a
     sufficiently deep nesting, which raises ``RecursionError`` — or, on some
     platforms, crashes the interpreter without a catchable exception at all.
     Measuring nesting depth on the raw string first (a single linear pass, no
     recursion) means we refuse before ever reaching that danger zone. The scan
     counts every bracket, quoted or not, so it can only over-count — see
     :func:`_max_nesting_depth` for why a quote-aware scan is the wrong trade.

  Every exception ``ast.literal_eval`` can raise on malformed or hostile input
  (``ValueError``, ``SyntaxError``, ``MemoryError``, ``RecursionError``,
  ``TypeError``) is caught and turned into ``None`` — "no result", the same
  convention as the rest of Tier 0.
"""
from __future__ import annotations

import ast
import json
from typing import Any

# A cell above this size is refused before either parser touches it: a bound on
# worst-case parse time / memory for one value, independent of what the value is.
_MAX_INPUT_BYTES = 1 << 20  # 1 MiB

# Maximum nesting depth of brackets we are willing to walk into. Real data (even
# a pymatgen ``Structure.as_dict()`` repr) nests a handful of levels deep; 64 is
# generous headroom while still refusing an adversarial ``"[" * N + "]" * N``
# before it can exhaust the AST compiler's / literal_eval's recursion budget.
_MAX_DEPTH = 64

_OPEN = "[{("
_CLOSE = "]})"


def _max_nesting_depth(text: str) -> int:
    """Maximum bracket nesting depth: a single linear pass, no recursion.

    Every ``[ { (`` counts +1 and every ``] } )`` counts -1, **including brackets
    that sit inside string literals**. Skipping quoted content would mean
    re-implementing Python's string lexer faithfully (both quote styles, triple
    quotes, escapes, prefixes) — and getting that subtly wrong lets an attacker
    hide real nesting behind a mis-parsed quote, which is precisely the failure
    this bound exists to prevent. Counting everything can only *overestimate*
    depth, so the error falls on the side of refusing input; reaching the cap
    that way would take dozens of unmatched opening brackets inside string
    values, which is not a shape real data takes. Returns as soon as the running
    depth passes the cap — the caller only needs "too deep or not".
    """
    depth = 0
    max_depth = 0
    for ch in text:
        if ch in _OPEN:
            depth += 1
            if depth > max_depth:
                max_depth = depth
                if max_depth > _MAX_DEPTH:
                    return max_depth
        elif ch in _CLOSE:
            depth -= 1
    return max_depth


def loads_relaxed(value: str) -> Any | None:
    """Parse ``value`` as JSON, falling back to a Python literal repr.

    Tries ``json.loads`` first (the common, fast case — well-formed JSON never
    touches the fallback). If that fails, treats ``value`` as a Python literal
    (``ast.literal_eval``) so a pandas ``dict``/``list`` column round-tripped
    through ``to_csv()`` (single-quoted keys, ``True``/``False``/``None``) still
    parses. Returns ``None`` — never raises — for an empty/blank/oversized input,
    an over-nested input, or anything neither parser accepts. See the module
    docstring for why ``ast.literal_eval`` cannot execute code and what bounds
    the residual DoS surface.
    """
    if not value or not value.strip():
        return None
    if len(value) > _MAX_INPUT_BYTES:
        return None
    # Depth is checked before *either* parser runs: both json.loads (a recursive
    # descent parser) and ast.literal_eval can blow the C stack on sufficiently
    # deep nesting, even when the input is otherwise well-formed JSON.
    if _max_nesting_depth(value) > _MAX_DEPTH:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
        return None
