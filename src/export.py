"""Selection exporters: json / txt / stable collection.db (stdlib only)."""
from __future__ import annotations

import struct
import sys

_HEX = frozenset("0123456789abcdefABCDEF")


def is_collection_id(v) -> bool:
    """True iff `v` is representable in collection.db (32-char hex md5)."""
    return (isinstance(v, str) and len(v) == 32
            and all(c in _HEX for c in v))


def collection_stats(rows: list[dict]) -> dict:
    """Count representable vs skipped rows without building bytes."""
    total = len(rows) if isinstance(rows, list) else 0
    exported = 0
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and is_collection_id(r.get("id")):
                exported += 1
    return {"total": total, "exported": exported,
            "skipped": total - exported}


def to_collection_db_with_counts(rows: list[dict],
                                 name: str = "Librarian Export") -> tuple[bytes, dict]:
    """Like :func:`to_collection_db` but also returns a stats dict.

    Returns (payload, {"total": N, "exported": E, "skipped": S}). Skipped
    rows (non-md5 `id`, e.g. short/path fallbacks from the Beatmap id
    contract in src/library.py) are omitted from the payload and reported
    on stderr; callers that already report export counts (X-Export-Count)
    should surface stats["skipped"] alongside.
    """
    items = rows if isinstance(rows, list) else []
    md5s: list[str] = []
    skipped = 0
    for r in items:
        v = r.get("id") if isinstance(r, dict) else None
        if is_collection_id(v):
            md5s.append(v)
        else:
            skipped += 1
    if skipped:
        try:
            print(f"[export] collection: skipped {skipped}/{len(items)} "
                  f"rows without 32-char md5 id", file=sys.stderr)
        except Exception:
            pass
    out = bytearray()
    out += struct.pack("<I", 20150203)  # version
    out += struct.pack("<I", 1)  # one collection
    out += _enc_string(name)
    out += struct.pack("<I", len(md5s))
    for m in md5s:
        out += _enc_string(m)
    stats = {"total": len(items), "exported": len(md5s), "skipped": skipped}
    return bytes(out), stats


def to_json(rows: list[dict]) -> bytes:
    import json
    return json.dumps(rows, indent=1).encode("utf-8")


def _safe_stars(v) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def to_txt(rows: list[dict]) -> bytes:
    lines = []
    for r in rows:
        lines.append(f"{r.get('artist', '')} - {r.get('title', '')} "
                     f"[{r.get('diff', '')}] ({r.get('mode_name', '')} "
                     f"★{_safe_stars(r.get('stars', 0.0)):.2f}) "
                     f"played={'yes' if r.get('played_local') or r.get('played_online') else 'no'} "
                     f"id:{r.get('beatmap_id', -1)}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _enc_string(s: str) -> bytes:
    b = s.encode("utf-8")
    out = bytearray([0x0B])
    n = len(b)
    while True:
        chunk = n & 0x7F
        n >>= 7
        out.append(chunk | (0x80 if n else 0))
        if not n:
            break
    return bytes(out) + b


def to_collection_db(rows: list[dict], name: str = "Librarian Export") -> bytes:
    """Minimal stable collection.db with a single collection of MD5s.

    Rows whose `id` is not a 32-char md5 (short/path fallbacks per the
    Beatmap id contract) are SKIPPED and counted on stderr; see
    :func:`to_collection_db_with_counts` for the (payload, stats) form.
    """
    payload, _stats = to_collection_db_with_counts(rows, name)
    return payload


EXPORTERS = {"json": to_json, "txt": to_txt, "collection": to_collection_db}
FILENAMES = {"json": "selection.json", "txt": "selection.txt",
             "collection": "collection.db"}
MIMES = {"json": "application/json", "txt": "text/plain",
         "collection": "application/octet-stream"}
