"""A service registry's address: the ``thrift://host:port`` string both
:mod:`iar_cspy.discovery`'s ``*.remote`` files and :class:`~iar_cspy.Config`
use, instead of separate host/port fields.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def format_registry_uri(host: str, port: int) -> str:
    """Build the URI a (host, port) pair is written as, e.g. for ``Config(registry_uri=...)``."""
    return f"thrift://{host}:{port}"


def parse_registry_uri(uri: str) -> tuple[str, int]:
    """Split a ``thrift://host:port`` URI into its (host, port).

    Raises ``ValueError`` if ``uri`` isn't one.
    """
    parts = urlsplit(uri)
    if parts.scheme != "thrift" or not parts.hostname or not parts.port:
        raise ValueError(f"Not a thrift://host:port registry URI: {uri!r}")
    return parts.hostname, parts.port
