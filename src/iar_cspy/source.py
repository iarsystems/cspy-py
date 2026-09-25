"""Disassembly and source lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .context import ContextLike, context_ref
from .memory import location

if TYPE_CHECKING:
    from .client import Client


class Disassembly:
    """Instructions for an address range (``client.disassembly``)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def range(
        self,
        start: int,
        end: int,
        *,
        zone: int = 0,
        end_zone: int | None = None,
        context: ContextLike = None,
    ) -> list[Any]:
        """``DisassembledLocation`` structs covering ``start`` to ``end``."""
        frm = location(self._client, start, zone)
        to = location(self._client, end, zone if end_zone is None else end_zone)
        return self._client.call("disassembly", "disassembleRange", frm, to, context_ref(self._client, context))


class SourceLookup:
    """Map code addresses to source (``client.source``)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def ranges(self, address: int, *, zone: int = 0) -> list[Any]:
        """``SourceRange`` structs for the code at ``address``."""
        return self._client.call("sourcelookup", "getSourceRanges", location(self._client, address, zone))
