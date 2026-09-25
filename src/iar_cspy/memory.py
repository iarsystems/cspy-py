"""Target memory through the ``debugger.memory`` service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Union

from ._session import requires_session
from .errors import CSpyError

if TYPE_CHECKING:
    from .client import Client

#: An address: an int, a ``shared.Location``, or an ``ExprValue`` from
#: ``client.debugger.eval`` (the address of the evaluated object).
Address = Union[int, Any]


def location(client: Client, address: Address, zone: int | None = None) -> Any:
    """A ``shared.Location``. Zone 0 is normally the main memory zone.

    ``address`` may also be a Location or an ExprValue, whose zone is used
    unless ``zone`` is given.
    """
    shared = client.idl.shared
    if not isinstance(address, int):
        loc = address
        if hasattr(loc, "hasLocation") and hasattr(loc, "location"):  # an ExprValue
            if not loc.hasLocation:
                raise CSpyError(f"{getattr(loc, 'expression', 'The expression')!r} has no address in memory")
            loc = loc.location
        if not (hasattr(loc, "address") and hasattr(loc, "zone")):
            raise CSpyError(f"Expected an address, a Location or an ExprValue, not {type(address).__name__}")
        address = int(loc.address)
        if zone is None:
            zone = int(getattr(loc.zone, "id", 0) or 0)
    return shared.Location(zone=shared.Zone(id=int(zone or 0)), address=int(address))


class Memory:
    """Read and write target memory (``client.memory``).

    ``address`` is in the units of the zone (bytes, for the usual zone 0),
    or a Location / ExprValue: ``client.memory.read(dbg.eval("buf"), 16)``.
    ``count`` is a number of words of ``wordsize`` bytes; with the default
    ``wordsize=1`` that is bytes. ``zone`` defaults to the address's zone, or
    0. See ``client.debugger.zones()`` for the zones a target has.
    """

    _api_name = "memory"

    def __init__(self, client: Client) -> None:
        self._client = client

    @requires_session
    def read(
        self, address: Address, count: int = 16, *, zone: int | None = None, wordsize: int = 1, bitsize: int = 8
    ) -> bytes:
        """Read ``count`` words (bytes, by default) at ``address``."""
        loc = location(self._client, address, zone)
        return bytes(self._client.call("memory", "readMemory", loc, int(wordsize), int(bitsize), int(count)))

    @requires_session
    def write(
        self,
        address: Address,
        data: bytes,
        *,
        zone: int | None = None,
        wordsize: int = 1,
        bitsize: int = 8,
        count: int | None = None,
    ) -> int:
        """Write ``data``; returns the word count written."""
        n_count = int(count) if count is not None else len(data) // max(1, int(wordsize))
        loc = location(self._client, address, zone)
        self._client.call("memory", "writeMemory", loc, int(wordsize), int(bitsize), n_count, bytes(data))
        return n_count

    def read_u32(self, address: Address, *, zone: int | None = None, byteorder: str = "little") -> int:
        return int.from_bytes(self.read(address, 4, zone=zone), byteorder)  # type: ignore[arg-type]

    def write_u32(self, address: Address, value: int, *, zone: int | None = None, byteorder: str = "little") -> None:
        self.write(address, int(value).to_bytes(4, byteorder), zone=zone)  # type: ignore[arg-type]
