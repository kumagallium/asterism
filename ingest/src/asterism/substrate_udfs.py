"""Morph-KGC UDF registration for the declarative substrate.

Morph-KGC loads this file *by path* (see ``asterism.substrate.materialize_to_graph``)
and injects the ``udf`` decorator into the module namespace at load time. The one
line below registers the closed Tier 0 function set from :mod:`asterism.functions`.

Do NOT import this module directly — ``udf`` only exists inside Morph-KGC's loader,
so a plain import would raise ``NameError``.
"""
from asterism.functions import register

register(udf)  # noqa: F821  -- `udf` is injected by Morph-KGC at load time
