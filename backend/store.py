from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schemaVersion": 1,
                "lastLibraryVersion": 0,
                "annotations": {},
                "canvasRequests": {},
            }
        value = json.loads(self.path.read_text(encoding="utf-8"))
        value.setdefault("schemaVersion", 1)
        value.setdefault("lastLibraryVersion", 0)
        value.setdefault("annotations", {})
        value.setdefault("canvasRequests", {})
        return value

    def save(self) -> None:
        with self.lock:
            atomic_write_json(self.path, self.data)

    def summary(self) -> dict[str, Any]:
        with self.lock:
            counts: dict[str, int] = {}
            for item in self.data["annotations"].values():
                status = str(item.get("status", "unknown"))
                counts[status] = counts.get(status, 0) + 1
            return {
                "lastLibraryVersion": self.data["lastLibraryVersion"],
                "counts": counts,
                "trackedAnnotations": len(self.data["annotations"]),
            }


class MappingStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        if not path.exists():
            atomic_write_json(path, {})

    def read(self) -> dict[str, str]:
        with self.lock:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("paper_canvas_map.json must contain a JSON object")
            return {str(key): str(value) for key, value in raw.items()}

    def bind(self, parent_item_key: str, canvas_path: str) -> None:
        if not parent_item_key or not canvas_path:
            raise ValueError("parent item key and canvas path are required")
        with self.lock:
            mapping = self.read()
            mapping[parent_item_key] = canvas_path.replace("\\", "/")
            atomic_write_json(self.path, mapping)
