"""Per-run state directory layout and small manifest helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .util import atomic_write_json, ensure_dir, now_iso, read_json

STATE_DIRS = {
    "doctor": "doctor",
    "scan": "scan",
    "analysis": "analysis",
    "plan": "plan",
    "selection": "selection",
    "execution": "execution",
    "recheck": "recheck",
}


class State:
    def __init__(self, root: str | None = None) -> None:
        raw = root or os.environ.get("ADC_STATE") or ".adc-state"
        self.root = Path(raw).resolve()
        ensure_dir(self.root)
        self.manifest_path = self.root / "state.json"
        self.data: dict[str, Any] = read_json(self.manifest_path, default=None) or {
            "schema": 1,
            "created_at": now_iso(),
            "files": {},
        }
        if "files" not in self.data or not isinstance(self.data["files"], dict):
            self.data["files"] = {}
        self.data.setdefault("state_id", self.root.name)
        self.save()

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        atomic_write_json(self.manifest_path, self.data)

    def dir(self, name: str) -> Path:
        directory = self.root / STATE_DIRS.get(name, name)
        ensure_dir(directory)
        return directory

    def file(self, name: str, filename: str) -> Path:
        return self.dir(name) / filename

    def set_file(self, key: str, path: str | Path) -> None:
        self.data["files"][key] = str(path)
        self.save()

    def get_file(self, key: str) -> str:
        value = (self.data.get("files") or {}).get(key, "")
        return str(value) if value else ""

    def require(self, key: str, label: str | None = None) -> Path:
        label = label or key
        value = self.get_file(key)
        if not value:
            from .util import ValidationError

            raise ValidationError(f"{label} 尚未生成。请先按流程执行前置步骤。")
        path = Path(value)
        if not path.exists():
            from .util import ValidationError

            raise ValidationError(f"{label} 文件不存在：{value}。请重新生成。")
        return path

    def reset_outputs(self, names: list[str] | None = None) -> None:
        for name in names or ["analysis", "plan", "selection", "execution"]:
            path = self.root / STATE_DIRS.get(name, name)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            self.data.get("files", {}).pop(name, None)
        self.save()

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)
