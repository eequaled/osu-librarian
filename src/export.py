"""Selection exporters: json / txt / stable collection.db (stdlib only)."""
from __future__ import annotations

import struct


def to_json(rows: list[dict]) -> bytes:
    import json
    return json.dumps(rows, indent=1).encode("utf-8")


def to_txt(rows: list[dict]) -> bytes:
    lines = []
    for r in rows:
        lines.append(f"{r.get('artist', '')} - {r.get('title', '')} "
                     f"[{r.get('diff', '')}] ({r.get('mode_name', '')} "
                     f"★{float(r.get('stars', 0.0)):.2f}) "
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
    """Minimal stable collection.db with a single collection of MD5s."""
    out = bytearray()
    out += struct.pack("<I", 20150203)  # version
    out += struct.pack("<I", 1)  # one collection
    out += _enc_string(name)
    md5s = [r["id"] for r in rows
            if isinstance(r.get("id"), str) and len(r["id"]) == 32]
    out += struct.pack("<I", len(md5s))
    for m in md5s:
        out += _enc_string(m)
    return bytes(out)


EXPORTERS = {"json": to_json, "txt": to_txt, "collection": to_collection_db}
FILENAMES = {"json": "selection.json", "txt": "selection.txt",
             "collection": "collection.db"}
MIMES = {"json": "application/json", "txt": "text/plain",
         "collection": "application/octet-stream"}
