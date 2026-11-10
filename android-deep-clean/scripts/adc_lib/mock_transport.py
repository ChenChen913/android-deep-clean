"""Mock transport: a local directory tree that behaves like an Android device.

Used for deterministic, reversible tests on Windows without adb or a phone.
The fixture root contains e.g.:

    fixture/sdcard/DCIM/...
    fixture/data/data/com.example/cache/...
    fixture/system/...

Device paths are mapped into those directories.  Permission limitations and
per-operation denials are configured through small JSON files.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat as stat_module
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import AccessProbe, DeviceInfo, Entry, PackageIndex, StatInfo
from .transport import Transport
from .util import (
    TransportError,
    ext_of,
    is_under,
    normalize_device_path,
    read_json,
    sqlite_int64,
)

_MOUNT_ALIASES = (
    ("/storage/emulated/0", "sdcard"),
    ("/storage/self/primary", "sdcard"),
    ("/mnt/sdcard", "sdcard"),
    ("/sdcard", "sdcard"),
    ("/data", "data"),
    ("/system", "system"),
    ("/vendor", "vendor"),
    ("/product", "product"),
    ("/apex", "apex"),
    ("/sdcard/Android", "sdcard/Android"),
)
_ALIASES_SORTED = sorted(_MOUNT_ALIASES, key=lambda item: len(item[0]), reverse=True)


def _kind_from_mode(mode: int) -> str:
    if stat_module.S_ISREG(mode):
        return "file"
    if stat_module.S_ISDIR(mode):
        return "dir"
    if stat_module.S_ISLNK(mode):
        return "symlink"
    return "other"


class MockTransport(Transport):
    name = "mock"

    def __init__(
        self,
        *,
        mock_root: str,
        packages_path: str | None = None,
        caps_path: str | None = None,
        device_path: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        if not mock_root:
            raise TransportError("--transport mock 需要 --mock-root <目录>")
        self.root = Path(mock_root).resolve()
        if not self.root.is_dir():
            raise TransportError(f"mock root 不存在或不是目录：{mock_root}")
        self.config = config or {}
        self.packages_path = packages_path
        self.caps_path = caps_path
        self.device_path = device_path
        self.caps: dict[str, Any] = read_json(caps_path, default={}) if caps_path else {}
        self._device_data: dict[str, Any] = read_json(device_path, default={}) if device_path else {}
        self._packages: PackageIndex | None = None
        self._privilege = str(self.caps.get("privilege", "shell"))
        if self._privilege not in {"shell", "root", "su"}:
            self._privilege = "shell"

    # -- path mapping -----------------------------------------------------
    def _map(self, device_path: str) -> tuple[Path, str]:
        path = normalize_device_path(device_path)
        for alias, base in _ALIASES_SORTED:
            if path == alias or path.startswith(alias + "/"):
                rel = path[len(alias):].lstrip("/")
                host = self.root / base / rel if rel else self.root / base
                self._ensure_inside(host)
                return host, base
        first = path.lstrip("/").split("/", 1)[0]
        rel = path.lstrip("/")[len(first):].lstrip("/")
        host = self.root / first / rel if rel else self.root / first
        self._ensure_inside(host)
        return host, first

    def _ensure_inside(self, host: Path) -> None:
        try:
            host.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise TransportError(f"mock 路径逃逸：{host}") from exc

    def _to_device(self, host: Path) -> str:
        try:
            # Do NOT resolve symlinks here: the scan must preserve the symlink
            # entry itself, otherwise a link is reported under its target path.
            rel = Path(os.path.normpath(str(host))).relative_to(self.root)
        except ValueError as exc:
            raise TransportError(f"路径不在 mock root 内：{host}") from exc
        parts = rel.parts
        if not parts:
            return "/"
        first = parts[0]
        if first == "sdcard":
            return "/sdcard" + ("/" + "/".join(parts[1:]) if len(parts) > 1 else "")
        return "/" + "/".join(parts)

    def _cap_blocked(self, path: str) -> bool:
        path = normalize_device_path(path)
        for blocked in self.caps.get("inaccessible") or []:
            try:
                if is_under(path, str(blocked)):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _denied(self, operation: str, path: str) -> bool:
        path = normalize_device_path(path)
        for rule in self.caps.get("denied_ops") or []:
            if not isinstance(rule, str) or ":" not in rule:
                continue
            op, target = rule.split(":", 1)
            if op not in {operation, "*"}:
                continue
            try:
                if is_under(path, target):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # -- environment ------------------------------------------------------
    def health_ok(self) -> bool:
        return bool(self.caps.get("connected", True))

    def device_serial(self) -> str:
        return str(self._device_data.get("serial", "mock-device"))

    def configure_privilege(self, mode: str = "auto") -> str:
        mode = (mode or "auto").lower()
        if mode == "none":
            self._privilege = "shell"
            return self._privilege
        if mode in {"auto", "adb-root"}:
            return self._privilege
        if mode == "su":
            if self._privilege in {"su", "root"}:
                return self._privilege
            raise TransportError("mock 能力未开启 su/root")
        if mode == "adb-root":
            return self._privilege
        raise TransportError(f"未知 root mode：{mode}")

    def shell(self, command: str, *, timeout: int = 120, check: bool = True) -> str:
        raise TransportError("mock transport 不执行任意 shell 命令")

    def shell_rc(self, command: str, *, timeout: int = 120) -> tuple[int, str, str]:
        try:
            parts = shlex.split(command, posix=True)
        except ValueError as exc:
            raise TransportError(f"mock: 无法解析 shell 命令：{exc}") from exc
        if parts[:2] == ["content", "query"]:
            return self._mock_content_query(parts)
        if parts[:1] == ["stat"]:
            return self._mock_stat(parts)
        if parts[:2] == ["pm", "path"] and len(parts) >= 3:
            package = parts[2]
            data = self._load_packages()
            paths = data.apk_path_list.get(package) or []
            if not paths and data.apk_paths.get(package):
                paths = [data.apk_paths[package]]
            if not paths:
                return 1, "", f"Error: package {package} not found"
            return 0, "".join(f"package:{path}\n" for path in paths), ""
        if parts[:3] == ["pm", "list", "packages"]:
            data = self._load_packages()
            lines = [
                f"package:{data.apk_paths[package]}={package}"
                for package in sorted(data.installed)
                if data.apk_paths.get(package)
            ]
            return 0, "\n".join(lines) + ("\n" if lines else ""), ""
        if parts[:2] == ["command", "-v"]:
            return 0, "0\n", ""
        if parts[:2] == ["du", "-sk"]:
            lines: list[str] = []
            for device_path in [item for item in parts[2:] if item.startswith("/")]:
                if self._cap_blocked(device_path):
                    lines.append(f"du: {device_path}: Permission denied")
                    continue
                try:
                    host, _ = self._map(device_path)
                except TransportError:
                    continue
                if not host.exists():
                    continue
                if host.is_dir():
                    total = sum(path.stat().st_size for path in host.rglob("*") if path.is_file())
                else:
                    total = host.stat().st_size
                kb = (total + 1023) // 1024 if total else 0
                lines.append(f"{kb}\t{device_path}")
            # Test hooks: simulate du failing as a whole (exit code + message).
            extra_error = str(self.caps.get("du_error") or "").strip()
            if extra_error:
                lines.append(extra_error)
            try:
                du_exit = int(self.caps.get("du_exit_code") or 0)
            except (TypeError, ValueError):
                du_exit = 0
            return du_exit, "\n".join(lines) + ("\n" if lines else ""), ""
        raise TransportError("mock transport 不执行任意 shell 命令")

    def _mock_stat(self, parts: list[str]) -> tuple[int, str, str]:
        format_text = ""
        paths: list[str] = []
        index = 1
        while index < len(parts):
            token = parts[index]
            if token in {"-c", "--format"} and index + 1 < len(parts):
                format_text = parts[index + 1]
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            paths.append(token)
            index += 1
        if format_text not in {"%n|%s", "%s|%n"}:
            raise TransportError("mock: 只支持 stat 的 %n|%s 格式")
        lines: list[str] = []
        errors: list[str] = []
        for device_path in paths:
            if self._cap_blocked(device_path):
                errors.append(f"stat: {device_path}: permission denied")
                continue
            try:
                host, _ = self._map(device_path)
            except TransportError as exc:
                errors.append(str(exc))
                continue
            if not (host.exists() or host.is_symlink()):
                errors.append(f"stat: {device_path}: No such file or directory")
                continue
            try:
                size = int(host.stat().st_size)
            except OSError as exc:
                errors.append(f"stat: {device_path}: {exc}")
                continue
            if format_text == "%s|%n":
                lines.append(f"{size}|{device_path}")
            else:
                lines.append(f"{device_path}|{size}")
        return (0 if lines else 1), ("\n".join(lines) + ("\n" if lines else "")), "\n".join(errors)

    def _mock_content_query(self, parts: list[str]) -> tuple[int, str, str]:
        uri = ""
        projection: list[str] = []
        for index, part in enumerate(parts):
            if part == "--uri" and index + 1 < len(parts):
                uri = parts[index + 1]
            elif part == "--projection" and index + 1 < len(parts):
                projection = [item for item in parts[index + 1].split(":") if item]
        match = re.match(r"^content://media/([^/]+)/([^/]+)/media$", uri)
        if not match:
            return 1, "", f"mock: 不支持的 MediaStore URI：{uri}"
        collection = match.group(2)
        media = self.caps.get("media_store")
        if not isinstance(media, dict):
            return 1, "", "mock: MediaStore fixture 未提供（caps.media_store）"
        if media.get("unavailable"):
            return 1, "", str(media.get("reason") or "mock: MediaStore 不可用")
        if media.get("reject_sort") and "--sort" in parts:
            return 1, "", "Error: Unknown option --sort"
        rejected = [
            column for column in projection
            if column in (media.get("reject_columns") or {}).get(collection, [])
        ]
        if rejected:
            return 1, "", f"Error: Invalid column {rejected[0]}"
        rows = media.get(collection) or []
        if not isinstance(rows, list):
            return 1, "", f"mock: media_store[{collection!r}] 必须是列表"
        if not projection:
            projection = ["_id", "_display_name", "_size", "date_modified", "mime_type"]
        lines: list[str] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            cells = []
            for column in projection:
                value = row.get(column)
                if value is None:
                    text = "null"
                elif isinstance(value, bool):
                    text = "1" if value else "0"
                else:
                    text = str(value)
                cells.append(f"{column}={text}")
            lines.append(f"Row: {index} " + ", ".join(cells))
        return 0, "\n".join(lines) + ("\n" if lines else ""), ""

    def getprop(self, key: str) -> str:
        props = self._device_data.get("props") or {}
        if key in props:
            return str(props[key])
        mapping = {
            "ro.build.version.sdk": self._device_data.get("sdk", 0),
            "ro.build.version.release": self._device_data.get("release", ""),
            "ro.product.manufacturer": self._device_data.get("manufacturer", ""),
            "ro.product.model": self._device_data.get("model", ""),
            "ro.build.type": self._device_data.get("build_type", ""),
            "ro.product.cpu.abi": self._device_data.get("abi", ""),
            "ro.product.name": self._device_data.get("product", ""),
            "ro.build.fingerprint": self._device_data.get("fingerprint", ""),
        }
        value = mapping.get(key, "")
        return "" if value is None else str(value)

    def device_info(self) -> DeviceInfo:
        sdk = self._device_data.get("sdk", 0)
        try:
            sdk = int(sdk)
        except (TypeError, ValueError):
            sdk = 0
        return DeviceInfo(
            serial=str(self._device_data.get("serial", "mock-device")),
            sdk=sdk,
            release=str(self._device_data.get("release", "")),
            manufacturer=str(self._device_data.get("manufacturer", "")),
            model=str(self._device_data.get("model", "")),
            build_type=str(self._device_data.get("build_type", "")),
            abi=str(self._device_data.get("abi", "")),
            product=str(self._device_data.get("product", "")),
            fingerprint=str(self._device_data.get("fingerprint", "")),
            platform=str(self._device_data.get("platform", "")),
            os_version=str(self._device_data.get("os_version", "")),
            api_version=str(self._device_data.get("api_version", "")),
            distribution=str(self._device_data.get("distribution", "")),
            harmony_version=str(self._device_data.get("harmony_version", "")),
            shell=str(self._device_data.get("shell", "mock")),
        )

    def _load_packages(self) -> PackageIndex:
        if self._packages is not None:
            return self._packages
        data = read_json(self.packages_path, default=None) if self.packages_path else None
        if data is None:
            self._packages = PackageIndex(available=False, error="mock packages fixture 缺失")
            return self._packages
        if isinstance(data, list):
            installed = set(str(x) for x in data)
            self._packages = PackageIndex(installed=installed)
            return self._packages
        self._packages = PackageIndex.from_dict(data)
        return self._packages

    def list_packages(self) -> PackageIndex:
        return self._load_packages()

    def access(self, path: str) -> AccessProbe:
        path = normalize_device_path(path)
        host, _ = self._map(path)
        blocked = self._cap_blocked(path)
        exists = host.exists() or host.is_symlink()
        is_dir = host.is_dir() and not host.is_symlink()
        readable = exists and not blocked and os.access(host, os.R_OK)
        writable = exists and not blocked and os.access(host, os.W_OK)
        executable = exists and not blocked and os.access(host, os.X_OK)
        sample: list[str] = []
        if is_dir and readable:
            try:
                sample = sorted(os.listdir(host))[:3]
            except OSError:
                sample = []
        return AccessProbe(
            path=path,
            exists=exists,
            is_dir=is_dir,
            readable=readable,
            writable=writable,
            executable=executable,
            listable=is_dir and readable and executable,
            sample=sample,
            error="" if not blocked else "mock: 路径被标记为不可访问",
        )

    # -- scanning ---------------------------------------------------------
    def iter_tree(
        self,
        root: str,
        *,
        max_depth: int = 0,
        max_files: int = 0,
        excludes: Iterable[str] = (),
        skip_sink: list[dict[str, str]] | None = None,
        deadline: float | None = None,
    ) -> Iterator[Entry]:
        root = normalize_device_path(root)
        host_root, _ = self._map(root)
        excluded = []
        for raw in excludes:
            try:
                excluded.append(normalize_device_path(str(raw)))
            except Exception:  # noqa: BLE001
                continue
        count = 0
        if self._cap_blocked(root):
            if skip_sink is not None:
                skip_sink.append({"path": root, "reason": "权限不足（mock inaccessible）", "root": root})
            return
        try:
            root_stat = host_root.lstat()
        except OSError as exc:
            if skip_sink is not None:
                skip_sink.append({"path": root, "reason": f"无法访问：{exc}", "root": root})
            return
        stack: list[tuple[Path, str, int]] = [(host_root, root, 0)]
        while stack:
            if deadline is not None and time.monotonic() > deadline:
                if skip_sink is not None:
                    skip_sink.append({"path": root, "reason": "扫描超时，结果可能不完整", "root": root})
                return
            host, device, depth = stack.pop()
            try:
                st = host.lstat()
            except OSError as exc:
                if skip_sink is not None:
                    skip_sink.append({"path": device, "reason": f"无法 stat：{exc}", "root": root})
                continue
            if self._cap_blocked(device):
                if skip_sink is not None:
                    skip_sink.append({"path": device, "reason": "权限不足（mock inaccessible）", "root": root})
                continue
            entry = self._entry_from_host(host, device, st, root, depth)
            yield entry
            count += 1
            if max_files and count >= max_files:
                if skip_sink is not None:
                    skip_sink.append({"path": root, "reason": f"达到 max_files={max_files} 限制，扫描被截断", "root": root})
                return
            if entry.kind != "dir":
                continue
            if max_depth and depth >= max_depth:
                continue
            try:
                with os.scandir(host) as it:
                    children = sorted(list(it), key=lambda item: item.name)
            except OSError as exc:
                if skip_sink is not None:
                    skip_sink.append({"path": device, "reason": f"无法列目录：{exc}", "root": root})
                continue
            for child in reversed(children):
                child_device = self._to_device(Path(child.path))
                if any(is_under(child_device, ex) or child_device == ex for ex in excluded):
                    continue
                if self._cap_blocked(child_device):
                    if skip_sink is not None:
                        skip_sink.append({"path": child_device, "reason": "权限不足（mock inaccessible）", "root": root})
                    continue
                try:
                    child_st = child.stat(follow_symlinks=False)
                except OSError as exc:
                    if skip_sink is not None:
                        skip_sink.append({"path": child_device, "reason": f"无法 stat：{exc}", "root": root})
                    continue
                stack.append((Path(child.path), child_device, depth + 1))

    def _entry_from_host(self, host: Path, device: str, st: os.stat_result, root: str, depth: int) -> Entry:
        name = posixpath.basename(device)
        parent = posixpath.dirname(device) or "/"
        return Entry(
            path=device,
            parent=parent,
            name=name,
            kind=_kind_from_mode(st.st_mode),
            size=int(st.st_size) if _kind_from_mode(st.st_mode) == "file" else 0,
            mtime=int(st.st_mtime),
            atime=int(st.st_atime),
            ctime=int(st.st_ctime),
            inode=sqlite_int64(st.st_ino),
            dev=sqlite_int64(st.st_dev),
            nlink=int(st.st_nlink),
            mode=int(st.st_mode),
            uid=int(st.st_uid),
            depth=depth,
            root=root,
            ext=ext_of(name),
        )

    def stat(self, path: str) -> StatInfo | None:
        path = normalize_device_path(path)
        if self._cap_blocked(path):
            return None
        host, _ = self._map(path)
        try:
            st = host.lstat()
        except OSError:
            return None
        kind = _kind_from_mode(st.st_mode)
        return StatInfo(
            path=path,
            kind=kind,
            size=int(st.st_size) if kind == "file" else 0,
            mtime=int(st.st_mtime),
            atime=int(st.st_atime),
            ctime=int(st.st_ctime),
            inode=sqlite_int64(st.st_ino),
            dev=sqlite_int64(st.st_dev),
            nlink=int(st.st_nlink),
            mode=int(st.st_mode),
            uid=int(st.st_uid),
        )

    def listdir(self, path: str) -> list[Entry]:
        path = normalize_device_path(path)
        host, _ = self._map(path)
        result: list[Entry] = []
        try:
            with os.scandir(host) as it:
                for child in it:
                    child_device = self._to_device(Path(child.path))
                    try:
                        st = child.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    result.append(self._entry_from_host(Path(child.path), child_device, st, path, 1))
        except OSError as exc:
            raise TransportError(f"无法列目录 {path}：{exc}") from exc
        return sorted(result, key=lambda entry: entry.name)

    def find_dirs(self, root: str, *, max_depth: int, names: set[str]) -> list[str]:
        root = normalize_device_path(root)
        host_root, _ = self._map(root)
        found: list[str] = []
        if not host_root.is_dir():
            return found
        base_depth = len(host_root.parts)
        for current, dirs, _files in os.walk(host_root, topdown=True):
            current_path = Path(current)
            depth = len(current_path.parts) - base_depth
            if max_depth and depth >= max_depth:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not self._cap_blocked(self._to_device(current_path / d))]
            if depth > 0 and current_path.name in names:
                found.append(self._to_device(current_path))
            if len(found) >= 2000:
                break
        return found

    def hash_tool(self) -> str:
        mode = str(self.caps.get("hash_mode") or "ok")
        if mode == "unavailable":
            return ""
        if mode == "md5":
            return "md5sum"
        return "sha256sum"

    def hash_files(self, paths: Iterable[str], *, timeout: int = 600) -> dict[str, str | None]:
        mode = str(self.caps.get("hash_mode") or "ok")
        if mode == "error":
            raise TransportError("模拟哈希计算异常")
        result: dict[str, str | None] = {}
        for raw in paths:
            path = normalize_device_path(raw)
            if mode in {"unavailable", "none"}:
                result[path] = None
                continue
            if self._cap_blocked(path):
                result[path] = None
                continue
            host, _ = self._map(path)
            try:
                if host.is_symlink() or not host.is_file():
                    result[path] = None
                    continue
                digest = hashlib.md5() if mode == "md5" else hashlib.sha256()
                with open(host, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
                result[path] = digest.hexdigest()
            except OSError:
                result[path] = None
        return result

    def df(self, path: str = "/sdcard") -> dict[str, Any]:
        configured = self.caps.get("df")
        if isinstance(configured, dict):
            return {
                "path": path,
                "filesystem": str(configured.get("filesystem", "mockfs")),
                "total_bytes": int(configured.get("total_bytes", 0)),
                "used_bytes": int(configured.get("used_bytes", 0)),
                "available_bytes": int(configured.get("available_bytes", 0)),
            }
        usage = shutil.disk_usage(self.root)
        return {
            "path": path,
            "filesystem": "mockfs",
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "available_bytes": int(usage.free),
        }

    # -- path/fs operations ----------------------------------------------
    def realpath(self, path: str) -> str | None:
        path = normalize_device_path(path)
        if self._cap_blocked(path):
            return None
        host, _ = self._map(path)
        try:
            resolved = host.resolve(strict=False)
        except OSError:
            return None
        try:
            resolved.relative_to(self.root)
        except ValueError:
            return None
        try:
            return self._to_device(resolved)
        except TransportError:
            return None

    def exists(self, path: str) -> bool:
        path = normalize_device_path(path)
        if self._cap_blocked(path):
            return False
        host, _ = self._map(path)
        return host.exists() or host.is_symlink()

    def mkdirs(self, path: str) -> None:
        path = normalize_device_path(path)
        if self._cap_blocked(path):
            raise TransportError(f"mock: 无权限创建 {path}")
        host, _ = self._map(path)
        try:
            host.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TransportError(f"mock: 无法创建目录 {path}：{exc}") from exc

    def move(self, src: str, dst: str) -> None:
        src = normalize_device_path(src)
        dst = normalize_device_path(dst)
        if self._denied("move", src) or self._cap_blocked(src):
            raise TransportError(f"mock: 移动被拒绝 {src}")
        if self._cap_blocked(dst) or self._denied("move", dst):
            raise TransportError(f"mock: 目标不可写 {dst}")
        src_host, _ = self._map(src)
        dst_host, _ = self._map(dst)
        if src_host.is_symlink() or not src_host.is_file():
            raise TransportError(f"mock: 源不是普通文件 {src}")
        if dst_host.exists() or dst_host.is_symlink():
            raise TransportError(f"mock: 目标已存在 {dst}")
        try:
            dst_host.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src_host, dst_host)
        except OSError as exc:
            raise TransportError(f"mock: 移动失败 {src} -> {dst}：{exc}") from exc

    def delete_file(self, path: str) -> None:
        path = normalize_device_path(path)
        if self._denied("delete", path) or self._cap_blocked(path):
            raise TransportError(f"mock: 删除被拒绝 {path}")
        host, _ = self._map(path)
        if host.is_symlink() or not host.is_file():
            raise TransportError(f"mock: 目标不是普通文件 {path}")
        try:
            host.unlink()
        except OSError as exc:
            raise TransportError(f"mock: 删除失败 {path}：{exc}") from exc

    def rmdir(self, path: str) -> None:
        path = normalize_device_path(path)
        if self._denied("rmdir", path) or self._cap_blocked(path):
            raise TransportError(f"mock: 删除目录被拒绝 {path}")
        host, _ = self._map(path)
        if host.is_symlink() or not host.is_dir():
            raise TransportError(f"mock: 目标不是目录 {path}")
        try:
            host.rmdir()
        except OSError as exc:
            raise TransportError(f"mock: 目录非空或无法删除 {path}：{exc}") from exc

    def pull(self, src: str, dst: str, *, max_bytes: int | None = None) -> int:
        src = normalize_device_path(src)
        host, _ = self._map(src)
        if self._cap_blocked(src) or host.is_symlink() or not host.is_file():
            raise TransportError(f"mock: 无法拉取 {src}")
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(host, "rb") as source, open(dst, "wb") as target:
                remaining = max_bytes
                while True:
                    if remaining is not None and remaining <= 0:
                        break
                    chunk = source.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    target.write(chunk)
                    if remaining is not None:
                        remaining -= len(chunk)
        except OSError as exc:
            raise TransportError(f"mock: 拉取失败 {src}：{exc}") from exc
        return os.path.getsize(dst)

    def read_head(self, path: str, length: int = 65536) -> bytes:
        path = normalize_device_path(path)
        host, _ = self._map(path)
        if self._cap_blocked(path) or host.is_symlink():
            raise TransportError(f"mock: 无法读取 {path}")
        try:
            with open(host, "rb") as fh:
                return fh.read(length)
        except OSError as exc:
            raise TransportError(f"mock: 读取失败 {path}：{exc}") from exc
