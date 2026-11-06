"""Plain data models shared across transports, scanners and analyzers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DeviceInfo:
    serial: str = ""
    sdk: int = 0
    release: str = ""
    manufacturer: str = ""
    model: str = ""
    build_type: str = ""
    abi: str = ""
    product: str = ""
    fingerprint: str = ""
    platform: str = ""
    os_version: str = ""
    api_version: str = ""
    distribution: str = ""
    harmony_version: str = ""
    shell: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def android(self) -> str:
        if self.platform in {"harmonyos_next", "harmonyos_compat"}:
            version = self.os_version or self.harmony_version or self.release
            label = "HarmonyOS NEXT" if self.platform == "harmonyos_next" else "HarmonyOS"
            if self.api_version:
                return f"{label} {version} (API {self.api_version})".replace("  ", " ").strip()
            return f"{label} {version}".strip()
        if self.release:
            return f"Android {self.release} (API {self.sdk})" if self.sdk else f"Android {self.release}"
        return f"API {self.sdk}" if self.sdk else "未知 Android 版本"


@dataclass
class StatInfo:
    path: str
    kind: str  # file | dir | symlink | other
    size: int = 0
    mtime: int | None = None
    atime: int | None = None
    ctime: int | None = None
    inode: int | None = None
    dev: int | None = None
    nlink: int | None = None
    mode: int | None = None
    uid: int | None = None
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Entry:
    path: str
    parent: str
    name: str
    kind: str
    size: int = 0
    mtime: int | None = None
    atime: int | None = None
    ctime: int | None = None
    inode: int | None = None
    dev: int | None = None
    nlink: int | None = None
    mode: int | None = None
    uid: int | None = None
    depth: int = 0
    root: str = ""
    ext: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackageIndex:
    installed: set[str] = field(default_factory=set)
    uninstalled: set[str] = field(default_factory=set)
    system: set[str] = field(default_factory=set)
    apk_paths: dict[str, str] = field(default_factory=dict)
    available: bool = True
    error: str = ""

    @property
    def all_known(self) -> set[str]:
        return set(self.installed) | set(self.uninstalled) | set(self.system)

    def is_installed(self, package: str) -> bool:
        return package in self.installed

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": sorted(self.installed),
            "uninstalled": sorted(self.uninstalled),
            "system": sorted(self.system),
            "apk_paths": dict(sorted(self.apk_paths.items())),
            "available": self.available,
            "error": self.error,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PackageIndex":
        return PackageIndex(
            installed=set(data.get("installed") or []),
            uninstalled=set(data.get("uninstalled") or []),
            system=set(data.get("system") or []),
            apk_paths=dict(data.get("apk_paths") or {}),
            available=bool(data.get("available", True)),
            error=str(data.get("error") or ""),
        )


@dataclass
class AccessProbe:
    path: str
    exists: bool
    is_dir: bool
    readable: bool
    writable: bool
    executable: bool
    listable: bool
    sample: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateFile:
    path: str
    size: int
    mtime: int | None = None
    inode: int | None = None
    dev: int | None = None
    kind: str = "file"
    role: str = "delete"  # delete | keep | dir

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    id: str
    category: str
    title: str
    risk: str
    action: str
    primary_path: str
    files: list[CandidateFile] = field(default_factory=list)
    confidence: float = 0.5
    evidence: list[dict[str, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    keep_suggestion: str = ""
    ai_review_required: bool = False
    app_pkg: str = ""
    app_kind: str = ""
    notes: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(max(0, f.size) for f in self.files)

    @property
    def file_count(self) -> int:
        return sum(1 for f in self.files if f.kind == "file")

    @property
    def dir_count(self) -> int:
        return sum(1 for f in self.files if f.kind == "dir")
