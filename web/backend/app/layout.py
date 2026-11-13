"""The Overview's card layout, chosen on the Overview itself (Customize) and kept on the server so every screen
shows the same page. Only the order of known cards in two columns is stored: never anything the page would run."""
import json
import logging
import os
import threading

log = logging.getLogger("solis-web")

# Every card the Overview can show; web/frontend/src/lib/layout.ts has the same list (layout.test.ts checks it).
CARD_IDS = ("flow", "battery", "gauges", "grid", "outages", "schedule", "today", "cost",
            "power", "selfsufficiency", "events", "health", "heatmap")
COLUMNS = ("left", "right")


class LayoutError(ValueError):
    """A layout the page should not have sent: unknown cards, a card twice, a missing column."""


def validate(raw) -> dict:
    """{left: [...], right: [...]} of known card ids, each at most once (an empty page is allowed)."""
    if not isinstance(raw, dict) or set(raw) != set(COLUMNS):
        raise LayoutError("layout must be {left: [...], right: [...]}")
    seen: set[str] = set()
    out = {}
    for col in COLUMNS:
        ids = raw[col]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise LayoutError(f"{col} must be a list of card names")
        for i in ids:
            if i not in CARD_IDS:
                raise LayoutError(f"unknown card: {i[:40]}")
            if i in seen:
                raise LayoutError(f"card listed twice: {i}")
            seen.add(i)
        out[col] = list(ids)
    return out


class LayoutStore:
    """The saved layout in a small JSON file (None = never saved: the page uses its default)."""

    def __init__(self, path: str | None):
        self.path = path or None
        self.lock = threading.Lock()
        self.layout = self._load()

    def _load(self) -> dict | None:
        if not self.path or not os.path.exists(self.path):
            return None
        try:
            with open(self.path, encoding="utf-8") as f:
                return validate(json.load(f))
        except (OSError, ValueError) as exc:     # LayoutError is a ValueError too
            log.warning("ignoring the saved Overview layout %s: %s", self.path, exc)
            return None

    def get(self) -> dict | None:
        with self.lock:
            return None if self.layout is None else {c: list(v) for c, v in self.layout.items()}

    def set(self, raw) -> dict | None:
        """Save a layout, or go back to the default with None (the file is removed)."""
        layout = None if raw is None else validate(raw)
        with self.lock:
            if self.path:
                if layout is None:
                    try:
                        os.remove(self.path)
                    except FileNotFoundError:
                        pass
                else:
                    tmp = self.path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(layout, f)
                    os.replace(tmp, self.path)
            self.layout = layout
        return self.get()
