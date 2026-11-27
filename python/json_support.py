"""Strict JSON decoding primitives shared by the offline Python adapters.

The duplicate-key hook is defined once here so every consumer rejects the
same malformed documents with the same diagnostic. No network I/O and no
provider data are involved.
"""

from __future__ import annotations


def reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Build a JSON object, rejecting duplicate keys instead of overwriting."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            message = f"duplicate JSON key: {key}"
            raise ValueError(message)
        result[key] = value
    return result
