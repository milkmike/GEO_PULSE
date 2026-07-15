"""Validation for untrusted URLs exposed by public API responses."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse


_ENCODED_UNSAFE_BYTE = re.compile(r"%(?:0[0-9a-f]|1[0-9a-f]|5c|7f)", re.I)
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def safe_public_url(value: object) -> str | None:
    """Return an unchanged safe absolute HTTP(S) URL, otherwise ``None``."""

    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or _ENCODED_UNSAFE_BYTE.search(value)
    ):
        return None

    try:
        parsed = urlparse(value)
        parsed.port  # Force validation of an explicit port.
        hostname = parsed.hostname
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None

        if ":" in hostname:
            ipaddress.IPv6Address(hostname)
        else:
            canonical_hostname = (
                hostname[:-1] if hostname.endswith(".") else hostname
            )
            ascii_hostname = canonical_hostname.encode("idna").decode("ascii")
            if not ascii_hostname or len(ascii_hostname) > 253:
                return None
            if any(
                not _HOST_LABEL.fullmatch(label)
                for label in ascii_hostname.split(".")
            ):
                return None
    except (ipaddress.AddressValueError, UnicodeError, ValueError):
        return None

    return value
