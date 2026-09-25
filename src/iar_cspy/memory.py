"""Target memory through the ``debugger.memory`` service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import Client


def location(client: Client, address: int, zone: int = 0) -> Any:
    """A ``shared.Location``. Zone 0 is normally the main memory zone."""
    shared = client.idl.shared
    return shared.Location(zone=shared.Zone(id=int(zone)), address=int(address))


class Memory:
    """Read and write target memory (``client.memory``).

    ``address`` is in the units of the zone (bytes, for the usual zone 0);
    ``count`` is in words of ``wordsize`` bytes. See ``client.debugger.zones()``
    for the zones a target has.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def read(self, address: int, count: int = 16, *, zone: int = 0, wordsize: int = 1, bitsize: int = 8) -> bytes:
        loc = location(self._client, address, zone)
        return bytes(self._client.call("memory", "readMemory", loc, int(wordsize), int(bitsize), int(count)))

    def write(
        self,
        address: int,
        data: bytes,
        *,
        zone: int = 0,
        wordsize: int = 1,
        bitsize: int = 8,
        count: int | None = None,
    ) -> int:
        """Write ``data``; returns the word count written."""
        n_count = int(count) if count is not None else len(data) // max(1, int(wordsize))
        loc = location(self._client, address, zone)
        self._client.call("memory", "writeMemory", loc, int(wordsize), int(bitsize), n_count, bytes(data))
        return n_count

    def read_u32(self, address: int, *, zone: int = 0, byteorder: str = "little") -> int:
        return int.from_bytes(self.read(address, 4, zone=zone), byteorder)  # type: ignore[arg-type]

    def write_u32(self, address: int, value: int, *, zone: int = 0, byteorder: str = "little") -> None:
        self.write(address, int(value).to_bytes(4, byteorder), zone=zone)  # type: ignore[arg-type]
