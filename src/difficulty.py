"""Standalone star-rating fallback (optional ``rosu-pp-py`` dependency).

Used when a beatmap row has no star rating -- e.g. lazer blob-sniff rows
(all ``stars == 0.0``) or stable rows scanned without ``osu!.db``.

Design notes
------------
* ``rosu-pp-py`` is OPTIONAL at runtime. Every public function works when
  the package is not installed (returning ``None`` / leaving rows
  untouched). The import is therefore done lazily *inside* functions --
  never at module top level.
* ``GameMode`` mapping (verified against the ``rosu-pp-py==4.0.2``
  manylinux wheel's ``__init__.pyi``)::

      Osu = 0, Taiko = 1, Catch = 2, Mania = 3

* ``Beatmap`` rows (see ``src/library.py``) carry ``folder`` like
  ``"1001 Artist - Title"`` but NOT the ``.osu`` filename, so the ``.osu``
  path cannot be derived from the row alone. Callers must therefore supply
  a ``resolve(row) -> path | None`` callback (see :func:`fill_rows`).
* Results are cached in a tiny LRU keyed by
  ``(abspath, mtime_ns, mode)`` (cap ~4096 entries) so rescans do not
  recompute unchanged files.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from collections.abc import Callable


_CACHE_MAX = 4096

# key: (abspath, mtime_ns, mode) -> stars (float) or None (failed calc)
_CACHE: OrderedDict[tuple[str, int, int], float | None] = OrderedDict()

__all__ = ["available", "stars_for_file", "fill_rows", "clear_cache"]


def available() -> bool:
    """Return True iff ``rosu_pp_py`` can be imported."""
    try:
        import rosu_pp_py  # noqa: F401
        return True
    except Exception:
        return False


def clear_cache() -> None:
    """Empty the internal ``(path, mtime, mode)`` LRU (mainly for tests)."""
    _CACHE.clear()


def _mode_member(rosu_pp_py, mode_int: int):
    """Map 0..3 to the wheel's ``GameMode`` member, or None if unknown."""
    mapping = {
        0: rosu_pp_py.GameMode.Osu,
        1: rosu_pp_py.GameMode.Taiko,
        2: rosu_pp_py.GameMode.Catch,
        3: rosu_pp_py.GameMode.Mania,
    }
    return mapping.get(mode_int)


def _compute_uncached(path_str: str, mode_int: int) -> float | None:
    """Run the rosu calculation once (no cache). None on any error."""
    try:
        import rosu_pp_py
    except Exception:
        return None
    try:
        target = _mode_member(rosu_pp_py, mode_int)
        if target is None:
            return None
        beatmap = rosu_pp_py.Beatmap(path=path_str)
        try:
            current = int(beatmap.mode)  # GameMode supports int(...)
        except Exception:
            current = None
        if current is not None and current != mode_int:
            # Requested mode differs from the map's native mode: convert.
            # e.g. an osu! map rated for taiko. Convert errors -> None.
            try:
                beatmap.convert(target, None)
            except Exception:
                return None
        attrs = rosu_pp_py.Difficulty().calculate(beatmap)
        return float(attrs.stars)
    except Exception:
        return None


def stars_for_file(path: str, mode: int = 0) -> float | None:
    """No-mods star rating for a local ``.osu`` file.

    Parses via ``rosu_pp_py.Beatmap(path=...)`` then a default (no-mods)
    ``rosu_pp_py.Difficulty().calculate(...)``. ``mode`` is ``0..3``
    (Osu/Taiko/Catch/Mania); when it differs from the map's native mode
    the map is converted before calculating.

    Returns ``float(stars)``, or ``None`` on any error: missing file,
    corrupt map, invalid mode, or ``rosu-pp-py`` not installed. Never
    raises for those cases.

    Results (including ``None`` failures for an existing file) are cached
    by ``(abspath, mtime_ns, mode)`` so unchanged files are not
    recomputed.
    """
    try:
        if not isinstance(path, (str, os.PathLike)):
            return None
        path_str = os.fspath(path)
        if not path_str:
            return None
        try:
            mode_int = int(mode)  # type: ignore[arg-type]
        except Exception:
            return None
        if mode_int not in (0, 1, 2, 3):
            return None
        # Optional-dependency gate (lazy import, no top-level import).
        try:
            import rosu_pp_py  # noqa: F401
        except Exception:
            return None
        try:
            st = os.stat(path_str)
        except OSError:
            return None
        key = (os.path.abspath(path_str), st.st_mtime_ns, mode_int)
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
        value = _compute_uncached(path_str, mode_int)
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        return value
    except Exception:
        return None


def fill_rows(rows: list[dict], resolve: Callable[[dict], str | None]) -> int:
    """Fill falsy ``stars`` on row dicts via a caller-provided resolver.

    Why a ``resolve`` callback?
    A :class:`src.library.Beatmap` row (serialised as ``dict``) carries
    ``folder`` like ``"1001 Artist - Title"`` but NOT the ``.osu``
    filename, so the ``.osu`` path cannot be derived from the row alone.
    The caller (which knows the Songs dir / lazer store layout) must
    supply ``resolve(row) -> path | None`` mapping a row to its local
    ``.osu`` file (or ``None`` when unknown/missing).

    For each row with falsy ``stars`` (``0.0``/``None``/missing) and a
    resolvable path, computes :func:`stars_for_file` with the row's
    numeric ``mode`` (default ``0``) and sets ``row["stars"]`` when the
    calculation succeeds (non-``None``). Rows already rated, unresolvable
    rows, and failed calculations are left untouched.

    Returns the number of rows filled. Never raises on bad rows (wrong
    types, raising resolver, bad paths/modes are all skipped). A
    non-list ``rows`` or non-callable ``resolve`` yields ``0``.
    """
    if not isinstance(rows, list):
        return 0
    if not callable(resolve):
        return 0
    filled = 0
    for row in rows:
        try:
            if not isinstance(row, dict):
                continue
            if row.get("stars"):
                continue  # already rated (truthy)
            try:
                found = resolve(row)
            except Exception:
                continue
            if not found:
                continue
            if not isinstance(found, (str, os.PathLike)):
                continue
            found_str = os.fspath(found)
            if not found_str:
                continue
            try:
                mode = int(row.get("mode", 0))  # type: ignore[arg-type]
            except Exception:
                mode = 0
            stars = stars_for_file(found_str, mode)
            if stars is None:
                continue
            row["stars"] = stars
            filled += 1
        except Exception:
            continue
    return filled
