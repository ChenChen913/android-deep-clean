"""Transport abstraction for Android device access.

Supported transports:
- adb    : USB / Wi-Fi ADB (default), optional root through adb root or su
- rish   : Shizuku's rish shell (shell UID, no root)
- local  : run on the Android device itself (Termux), sh + toybox
- mock   : local directory fixture used by the test suite

Every method is read-only unless it is explicitly named move/delete/rmdir/
mkdirs/pull.  The scanner only calls read methods.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shlex
import shutil
import stat as stat_module
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import AccessProbe, DeviceInfo, Entry, PackageIndex, StatInfo
from .util import (
    AdcError,
    TransportError,
    batch_iter,
    display_path,
    ext_of,
    is_under,
    normalize_device_path,
    shell_quote,
    sqlite_int64,
)

STAT_FORMAT = "%f|%s|%Y|%X|%Z|%i|%d|%h|%a|%u|%n"
_STAT_FIELDS = 11
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_ADB_STATES = {"device", "unauthorized", "offline", "recovery", "sideload", "bootloader", "no"}
_SCAN_ERR_RE = re.compile(r"^(?:find|stat|ls|du|xargs):\s*(?P<path>.+?):\s*(?P<message>.+)$")
_SCAN_PERMISSION_MARKERS = ("Permission denied", "Operation not permitted", "permission denied")
_SCAN_ERR_PREFIXES = ("cannot statx ", "cannot stat ", "cannot access ", "cannot open directory ", "cannot read ")
_SCAN_SKIP_LIMIT = 200
_ADB_STATE_LABELS = {
    "device": "已授权，可执行 adb shell",
    "unauthorized": "未授权：请在手机屏幕上允许此电脑的 USB 调试",
    "offline": "离线：请重新插拔数据线或在手机上重新开启 USB 调试",
    "no permissions": "无权限：请检查 USB 驱动/数据线/手机 USB 模式",
    "recovery": "设备处于 recovery 模式",
    "sideload": "设备处于 sideload 模式",
    "bootloader": "设备处于 bootloader/fastboot 模式",
}


def parse_adb_devices(output: str) -> list[dict[str, str]]:
    """Parse 'adb devices -l' output into serial/state/details records."""
    devices: list[dict[str, str]] = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        details = " ".join(parts[2:])
        if state == "no" and details.startswith("permissions"):
            state = "no permissions"
        elif state not in _ADB_STATES:
            continue
        devices.append({"serial": serial, "state": state, "details": details})
    return devices


def _mode_kind(mode: int) -> str:
    if stat_module.S_ISREG(mode):
        return "file"
    if stat_module.S_ISDIR(mode):
        return "dir"
    if stat_module.S_ISLNK(mode):
        return "symlink"
    return "other"


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_stat_line(line: str) -> Entry | None:
    """Parse one output line of stat -c '%f|...|%n' (path is last, may contain '|')."""
    line = line.rstrip("\n")
    if not line or "|" not in line:
        return None
    parts = line.split("|", _STAT_FIELDS - 1)
    if len(parts) != _STAT_FIELDS:
        return None
    mode_hex, size, mtime, atime, ctime, inode, dev, nlink, perms, uid, path = parts
    if not _HEX_RE.match(mode_hex) or not path.startswith("/"):
        return None
    try:
        mode = int(mode_hex, 16)
    except ValueError:
        return None
    name = posixpath.basename(path)
    parent = posixpath.dirname(path) or "/"
    return Entry(
        path=normalize_device_path(path),
        parent=parent,
        name=name,
        kind=_mode_kind(mode),
        size=_int_or_none(size) or 0,
        mtime=_int_or_none(mtime),
        atime=_int_or_none(atime),
        ctime=_int_or_none(ctime),
        inode=sqlite_int64(inode),
        dev=sqlite_int64(dev),
        nlink=_int_or_none(nlink),
        mode=mode,
        uid=_int_or_none(uid),
        ext=ext_of(name),
    )


def parse_scan_errors(lines: Iterable[str], *, root: str = "") -> tuple[list[dict[str, str]], int]:
    """Turn ``find``/``stat`` stderr into skip records for unreadable paths.

    ``find`` keeps walking when a subdirectory cannot be opened, so a scan can
    silently miss a whole app's data while the coverage report still says
    "good".  Only permission failures are reported here: transient "No such
    file" races during an active scan must not downgrade coverage.

    Returns ``(records, denied_total)`` where ``records`` is capped at
    ``_SCAN_SKIP_LIMIT`` and ``denied_total`` counts every distinct path.
    """
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    denied = 0
    for raw in lines:
        text = (raw or "").strip()
        if not text:
            continue
        match = _SCAN_ERR_RE.match(text)
        if not match:
            continue
        path = match.group("path").strip()
        # GNU coreutils/phrasebook wording, e.g. "stat: cannot statx '/x': ..."
        for prefix in _SCAN_ERR_PREFIXES:
            if path.startswith(prefix):
                path = path[len(prefix):].strip()
                break
        # toybox uses '...', GNU find uses ‘...’ (U+2018/2019) when locales allow.
        path = path.strip("'\"‘’“”")
        message = match.group("message").strip()
        if not path.startswith("/"):
            continue
        if not any(message.startswith(marker) for marker in _SCAN_PERMISSION_MARKERS):
            continue
        if path in seen:
            continue
        seen.add(path)
        denied += 1
        if len(records) < _SCAN_SKIP_LIMIT:
            records.append({"path": path, "reason": f"权限不足，无法读取（{message}）", "root": root})
    return records, denied


class Transport:
    name = "base"

    def __init__(self) -> None:
        self._privilege = "shell"

    @property
    def privilege(self) -> str:
        return self._privilege

    def ensure_ready(self) -> None:
        """Validate connection/authorization before any device operation."""
        return None

    def health_ok(self) -> bool:
        """Cheap connection check. Transports may override; default assumes OK."""
        return True

    def device_serial(self) -> str:
        serial = getattr(self, "serial", "") or ""
        if serial:
            return str(serial)
        try:
            return str(self.device_info().serial or "")
        except Exception:  # noqa: BLE001
            return ""

    def configure_privilege(self, mode: str = "auto") -> str:
        raise NotImplementedError

    def shell(self, command: str, *, timeout: int = 120, check: bool = True) -> str:
        raise NotImplementedError

    def shell_rc(self, command: str, *, timeout: int = 120) -> tuple[int, str, str]:
        raise NotImplementedError

    def getprop(self, key: str) -> str:
        raise NotImplementedError

    def device_info(self) -> DeviceInfo:
        raise NotImplementedError

    def list_packages(self) -> PackageIndex:
        raise NotImplementedError

    def access(self, path: str) -> AccessProbe:
        raise NotImplementedError

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
        raise NotImplementedError

    def stat(self, path: str) -> StatInfo | None:
        raise NotImplementedError

    def listdir(self, path: str) -> list[Entry]:
        raise NotImplementedError

    def find_dirs(self, root: str, *, max_depth: int, names: set[str]) -> list[str]:
        raise NotImplementedError

    def hash_files(self, paths: Iterable[str], *, timeout: int = 600) -> dict[str, str | None]:
        raise NotImplementedError

    def hash_tool(self) -> str:
        """Return 'sha256sum', 'md5sum', or '' when the device has no hash tool."""
        return ""

    def df(self, path: str = "/sdcard") -> dict[str, Any]:
        raise NotImplementedError

    def realpath(self, path: str) -> str | None:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def mkdirs(self, path: str) -> None:
        raise NotImplementedError

    def move(self, src: str, dst: str) -> None:
        raise NotImplementedError

    def delete_file(self, path: str) -> None:
        raise NotImplementedError

    def rmdir(self, path: str) -> None:
        raise NotImplementedError

    def pull(self, src: str, dst: str, *, max_bytes: int | None = None) -> int:
        raise NotImplementedError

    def read_head(self, path: str, length: int = 65536) -> bytes:
        raise NotImplementedError

    def try_adb_root(self) -> bool:
        return False


class ShellTransport(Transport):
    """Base for transports that execute a POSIX shell (adb shell / rish / local)."""

    name = "shell"

    def __init__(self, *, timeout: int = 120, su_command: str = "su") -> None:
        super().__init__()
        self.timeout = timeout
        self.su_command = su_command
        self._privilege = "shell"
        self._hash_tool_cache: str | None = None

    # -- process plumbing -------------------------------------------------
    def _shell_argv(self, command: str) -> list[str]:
        raise NotImplementedError

    def _exec_out_argv(self, command: str) -> list[str]:
        raise NotImplementedError

    def _base_shell_argv(self, command: str) -> list[str]:
        return self._shell_argv(command)

    def _wrap(self, command: str) -> str:
        if self._privilege == "su":
            return f"{self.su_command} -c {shell_quote(command)}"
        return command

    def _run(self, argv: list[str], *, timeout: int = 120, stdin_data: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                argv,
                input=stdin_data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise TransportError(f"找不到命令或设备工具：{argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TransportError(f"设备命令超时（{timeout}s）：{argv[:3]}") from exc
        except OSError as exc:
            raise TransportError(f"设备命令执行失败：{exc}") from exc

    def shell(self, command: str, *, timeout: int | None = None, check: bool = True) -> str:
        result = self.shell_rc(command, timeout=timeout)
        if check and result[0] != 0:
            stderr = (result[2] or result[1]).strip()
            raise TransportError(f"设备 shell 命令失败（退出码 {result[0]}）：{stderr[:500]}")
        return result[1]

    def shell_rc(self, command: str, *, timeout: int | None = None) -> tuple[int, str, str]:
        wrapped = self._wrap(command)
        result = self._run(self._shell_argv(wrapped), timeout=timeout or self.timeout)
        return result.returncode, result.stdout, result.stderr

    def _raw_shell(self, command: str, *, timeout: int | None = None) -> tuple[int, str, str]:
        result = self._run(self._shell_argv(command), timeout=timeout or self.timeout)
        return result.returncode, result.stdout, result.stderr

    def getprop(self, key: str) -> str:
        for command in ("getprop", "/system/bin/getprop"):
            rc, out, _ = self.shell_rc(f"{command} {shell_quote(key)}")
            if rc == 0 and out.strip():
                return out.strip()
        return ""

    def device_info(self) -> DeviceInfo:
        rc, out = 1, ""
        for command in ("getprop", "/system/bin/getprop"):
            rc, out, _ = self.shell_rc(command)
            if rc == 0 and out.strip():
                break
        props: dict[str, str] = {}
        if rc == 0:
            for line in out.splitlines():
                match = re.match(r"^\[([^\]]+)\]: \[(.*)\]$", line.strip())
                if match:
                    props[match.group(1)] = match.group(2)
        sdk = 0
        try:
            sdk = int(props.get("ro.build.version.sdk", "0") or 0)
        except ValueError:
            pass
        harmony_version = props.get("hw_sc.build.platform.version", "")
        if not harmony_version:
            # Some HarmonyOS ROMs keep this outside the default getprop dump.
            harmony_version = self.getprop("hw_sc.build.platform.version")
        return DeviceInfo(
            sdk=sdk,
            release=props.get("ro.build.version.release", ""),
            manufacturer=props.get("ro.product.manufacturer", ""),
            model=props.get("ro.product.model", ""),
            build_type=props.get("ro.build.type", ""),
            abi=props.get("ro.product.cpu.abi", ""),
            product=props.get("ro.product.name", ""),
            fingerprint=props.get("ro.build.fingerprint", ""),
            os_version=harmony_version,
            api_version=props.get("const.product.os.dist.apiversion", "") or props.get("const.ohos.apiversion", ""),
            distribution=props.get("const.product.os.dist.name", ""),
            harmony_version=harmony_version,
            shell=self.name,
        )

    def list_packages(self) -> PackageIndex:
        index = PackageIndex()
        all_packages, all_versions = self._list_pm("-u", index)
        if not index.available:
            return index
        installed, installed_versions = self._list_pm("", index)
        if not index.available:
            return index
        system, system_versions = self._list_pm("-s", index)
        index.installed = installed
        index.uninstalled = all_packages - installed
        index.system = system
        index.versions = {**all_versions, **installed_versions, **system_versions}
        index.version_available = bool(index.versions)
        return index

    def _list_pm(self, flag: str, index: PackageIndex) -> tuple[set[str], dict[str, str]]:
        command = "pm list packages" + (f" {flag}" if flag else "") + " --show-versioncode"
        rc, out, err = self.shell_rc(command, timeout=180)
        if rc != 0:
            command = "pm list packages" + (f" {flag}" if flag else "")
            rc, out, err = self.shell_rc(command, timeout=180)
            if rc != 0:
                index.available = False
                index.error = (err or out).strip()[:500] or "pm list packages 失败，无法可靠区分已安装包"
                return set(), {}
        return self._parse_pm(out)

    @staticmethod
    def _parse_pm(output: str) -> tuple[set[str], dict[str, str]]:
        packages: set[str] = set()
        versions: dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            payload = line.split(":", 1)[1].strip()
            parts = payload.split()
            if not parts:
                continue
            name = parts[0]
            packages.add(name)
            for part in parts[1:]:
                if part.startswith("versionCode:"):
                    versions[name] = part.split(":", 1)[1]
        return packages, versions

    def access(self, path: str) -> AccessProbe:
        path = normalize_device_path(path)
        script = (
            f"p={shell_quote(path)}; "
            'if [ -e "$p" ]; then echo E; fi; '
            'if [ -d "$p" ]; then echo D; fi; '
            'if [ -r "$p" ]; then echo R; fi; '
            'if [ -w "$p" ]; then echo W; fi; '
            'if [ -x "$p" ]; then echo X; fi; '
            'ls -1 "$p" 2>/dev/null | head -n 3'
        )
        rc, out, err = self.shell_rc(script, timeout=30)
        if rc != 0:
            return AccessProbe(path=path, exists=False, is_dir=False, readable=False,
                               writable=False, executable=False, listable=False,
                               error=(err or out).strip()[:300])
        lines = out.splitlines()
        markers = {line.strip() for line in lines if line.strip() in {"E", "D", "R", "W", "X"}}
        sample = [line for line in lines if line.strip() not in {"E", "D", "R", "W", "X"}][:3]
        exists = "E" in markers
        is_dir = "D" in markers
        readable = "R" in markers
        executable = "X" in markers
        listable = bool(sample) or (is_dir and readable and executable)
        return AccessProbe(path=path, exists=exists, is_dir=is_dir, readable=readable,
                           writable="W" in markers, executable=executable, listable=listable,
                           sample=sample)

    def _find_script(self, root: str, *, max_depth: int, excludes: Iterable[str]) -> str:
        root = normalize_device_path(root)
        clauses: list[str] = []
        for excluded in excludes:
            try:
                ex = normalize_device_path(excluded)
            except AdcError:
                continue
            clauses.append(f"-path {shell_quote(ex)}")
        # -H follows a symlink given on the command line (e.g. /sdcard ->
        # /storage/self/primary) without following links found during the walk.
        find_expr = f"find -H {shell_quote(root)} -xdev"
        if max_depth and max_depth > 0:
            find_expr += f" -maxdepth {int(max_depth)}"
        if clauses:
            find_expr += " \\( " + " -o ".join(clauses) + " \\) -prune -o"
        find_expr += " -print0"
        # stderr is intentionally NOT redirected: find keeps walking after a
        # "Permission denied" on a subdirectory, and those lines are the only
        # evidence that part of the tree was skipped.  iter_tree() drains them
        # into skip_sink so coverage degrades to "partial".
        # xargs batches keep the per-file stat overhead low on toybox.
        return (
            find_expr
            + " | xargs -0 -n 200 stat -c "
            + shell_quote(STAT_FORMAT)
        )

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
        script = self._find_script(root, max_depth=max_depth, excludes=excludes)
        argv = self._shell_argv(self._wrap(script))
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                # stderr stays on its own pipe: merging it into stdout would
                # corrupt the NUL-delimited path stream that find emits.
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise TransportError(f"找不到 shell/ADB 可执行文件：{argv[0]}") from exc
        except OSError as exc:
            raise TransportError(f"无法启动设备扫描进程：{exc}") from exc
        count = 0
        stderr_tail: list[str] = []
        scan_errors: list[str] = []
        reported = False

        def _drain_stderr() -> None:
            stream = proc.stderr
            if stream is None:
                return
            try:
                for raw in stream:
                    line = raw.strip()
                    if line:
                        scan_errors.append(line[:300])
                        if len(scan_errors) > 500:
                            del scan_errors[:250]
            except Exception:  # noqa: BLE001 - draining must never break a scan
                pass

        reader = threading.Thread(target=_drain_stderr, name="adc-scan-stderr", daemon=True)
        reader.start()

        def report_scan_errors() -> None:
            nonlocal reported
            if reported or skip_sink is None:
                return
            reported = True
            records, denied = parse_scan_errors(scan_errors, root=root)
            skip_sink.extend(records)
            if denied > len(records):
                skip_sink.append({
                    "path": root,
                    "reason": f"另有 {denied - len(records)} 个路径权限不足（明细已省略）",
                    "root": root,
                })
            stderr_tail.extend(scan_errors[-20:])

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if deadline is not None and time.monotonic() > deadline:
                    if skip_sink is not None:
                        skip_sink.append({"path": root, "reason": "扫描超时，结果可能不完整", "root": root})
                    proc.kill()
                    break
                entry = parse_stat_line(line)
                if entry is None:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("stat:"):
                        stderr_tail.append(stripped[:300])
                        if len(stderr_tail) > 20:
                            stderr_tail.pop(0)
                    continue
                entry.root = root
                entry.depth = max(0, entry.path.count("/") - root.count("/"))
                if max_files and count >= max_files:
                    if skip_sink is not None:
                        skip_sink.append({"path": root, "reason": f"达到 max_files={max_files} 限制，扫描被截断", "root": root})
                    proc.kill()
                    break
                count += 1
                yield entry
        finally:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            reader.join(timeout=5)
            report_scan_errors()
            if proc.returncode not in (0, None) and count == 0:
                tail = " | ".join(stderr_tail[-5:])
                raise TransportError(f"扫描 {root} 失败（退出码 {proc.returncode}）：{tail[:800]}")

    def _stat_many(self, paths: Iterable[str]) -> list[Entry]:
        items: list[Entry] = []
        for batch in batch_iter(list(paths), 100):
            quoted = " ".join(shell_quote(p) for p in batch)
            rc, out, _ = self.shell_rc(f"stat -c {shell_quote(STAT_FORMAT)} {quoted}", timeout=120)
            if rc != 0:
                continue
            for line in out.splitlines():
                entry = parse_stat_line(line)
                if entry:
                    items.append(entry)
        return items

    def stat(self, path: str) -> StatInfo | None:
        path = normalize_device_path(path)
        entries = self._stat_many([path])
        if not entries:
            return None
        entry = entries[0]
        return StatInfo(path=entry.path, kind=entry.kind, size=entry.size, mtime=entry.mtime,
                        atime=entry.atime, ctime=entry.ctime, inode=entry.inode, dev=entry.dev,
                        nlink=entry.nlink, mode=entry.mode, uid=entry.uid)

    def listdir(self, path: str) -> list[Entry]:
        path = normalize_device_path(path)
        # Find the children (depth 1) and stat them.
        rc, out, _ = self.shell_rc(
            f"find -H {shell_quote(path)} -mindepth 1 -maxdepth 1 -print0 2>/dev/null | xargs -0 -n 100 stat -c {shell_quote(STAT_FORMAT)} 2>/dev/null",
            timeout=120,
        )
        entries: list[Entry] = []
        if rc == 0:
            for line in out.splitlines():
                entry = parse_stat_line(line)
                if entry:
                    entries.append(entry)
        return entries

    def find_dirs(self, root: str, *, max_depth: int, names: set[str]) -> list[str]:
        root = normalize_device_path(root)
        name_expr = " -o ".join(f"-name {shell_quote(n)}" for n in sorted(names))
        cmd = (
            f"find {shell_quote(root)} -mindepth 1 -maxdepth {int(max_depth)} -type d "
            f"\\( {name_expr} \\) -print 2>/dev/null"
        )
        rc, out, _ = self.shell_rc(cmd, timeout=300)
        return [line.strip() for line in out.splitlines() if line.strip().startswith("/")] if rc == 0 else []

    def hash_tool(self) -> str:
        if self._hash_tool_cache is not None:
            return self._hash_tool_cache
        tool = ""
        rc, out, _ = self.shell_rc("command -v sha256sum >/dev/null 2>&1; echo $?", timeout=30)
        if rc == 0 and out.strip() == "0":
            tool = "sha256sum"
        else:
            rc2, out2, _ = self.shell_rc("command -v md5sum >/dev/null 2>&1; echo $?", timeout=30)
            if rc2 == 0 and out2.strip() == "0":
                tool = "md5sum"
        self._hash_tool_cache = tool
        return tool

    def hash_files(self, paths: Iterable[str], *, timeout: int = 600) -> dict[str, str | None]:
        path_list = [normalize_device_path(p) for p in paths]
        result: dict[str, str | None] = {p: None for p in path_list}
        if not path_list:
            return result
        tool = self.hash_tool()
        if not tool:
            return result
        expected_len = 64 if tool == "sha256sum" else 32
        pattern = re.compile(rf"^([0-9a-fA-F]{{{expected_len}}})\s+(.+)$")
        for batch in batch_iter(path_list, 50):
            quoted = " ".join(shell_quote(p) for p in batch)
            rc, out, _ = self.shell_rc(f"{tool} {quoted}", timeout=timeout)
            if rc != 0:
                continue
            for line in out.splitlines():
                match = pattern.match(line.strip())
                if not match:
                    continue
                digest, path = match.group(1).lower(), normalize_device_path(match.group(2).strip())
                if path in result:
                    result[path] = digest
        return result

    def df(self, path: str = "/sdcard") -> dict[str, Any]:
        rc, out, _ = self.shell_rc(f"df -k {shell_quote(path)}", timeout=60)
        if rc != 0:
            return {"path": path, "error": (out or "").strip()[:300]}
        for line in reversed(out.splitlines()):
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                total, used, avail = int(parts[1]), int(parts[2]), int(parts[3])
            except ValueError:
                continue
            return {
                "path": path,
                "filesystem": parts[0],
                "total_bytes": total * 1024,
                "used_bytes": used * 1024,
                "available_bytes": avail * 1024,
            }
        return {"path": path, "error": "无法解析 df 输出"}

    def realpath(self, path: str) -> str | None:
        path = normalize_device_path(path)
        rc, out, _ = self.shell_rc(f"realpath {shell_quote(path)} 2>/dev/null || readlink -f {shell_quote(path)} 2>/dev/null")
        value = out.strip().splitlines()[0].strip() if out.strip() else ""
        if rc == 0 and value.startswith("/"):
            return normalize_device_path(value)
        return None

    def exists(self, path: str) -> bool:
        path = normalize_device_path(path)
        rc, out, _ = self.shell_rc(f"[ -e {shell_quote(path)} ] && echo 1")
        return rc == 0 and out.strip() == "1"

    def mkdirs(self, path: str) -> None:
        path = normalize_device_path(path)
        self.shell(f"mkdir -p {shell_quote(path)}", timeout=120)

    def move(self, src: str, dst: str) -> None:
        src = normalize_device_path(src)
        dst = normalize_device_path(dst)
        self.shell(f"mv -f {shell_quote(src)} {shell_quote(dst)}", timeout=300)

    def delete_file(self, path: str) -> None:
        path = normalize_device_path(path)
        self.shell(f"rm -f {shell_quote(path)}", timeout=120)

    def rmdir(self, path: str) -> None:
        path = normalize_device_path(path)
        self.shell(f"rmdir {shell_quote(path)}", timeout=120)

    def pull(self, src: str, dst: str, *, max_bytes: int | None = None) -> int:
        src = normalize_device_path(src)
        command = f"cat {shell_quote(src)}"
        if max_bytes is not None:
            command = f"head -c {int(max_bytes)} {shell_quote(src)}"
        argv = self._exec_out_argv(self._wrap(command))
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(dst, "wb") as fh:
                proc = subprocess.run(argv, stdout=fh, stderr=subprocess.PIPE, timeout=600, check=False)
        except FileNotFoundError as exc:
            raise TransportError(f"无法拉取文件，执行器缺失：{argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TransportError(f"拉取文件超时：{src}") from exc
        if proc.returncode != 0:
            raise TransportError(f"拉取文件失败：{src}")
        return os.path.getsize(dst)

    def read_head(self, path: str, length: int = 65536) -> bytes:
        path = normalize_device_path(path)
        argv = self._exec_out_argv(self._wrap(f"head -c {int(length)} {shell_quote(path)}"))
        try:
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise TransportError(f"读取文件头失败：{path}") from exc
        if result.returncode != 0:
            raise TransportError(f"读取文件头失败：{path}")
        return result.stdout[:length]

    # -- privilege --------------------------------------------------------
    def _is_root_id(self) -> bool:
        rc, out, _ = self._raw_shell("id", timeout=30)
        return rc == 0 and "uid=0" in out

    def _su_works(self) -> bool:
        rc, out, _ = self._raw_shell(f"{self.su_command} -c id", timeout=45)
        return rc == 0 and "uid=0" in out

    def _android_shell_host(self) -> bool:
        """True when local shell is Android/Termux, not a proot/container."""
        rc, out, _ = self._raw_shell("uname -o", timeout=20)
        return rc == 0 and "android" in out.lower()

    def configure_privilege(self, mode: str = "auto") -> str:
        mode = (mode or "auto").lower()
        self._hash_tool_cache = None
        real_android = self.name != "local" or self._android_shell_host()
        if mode == "none":
            self._privilege = "shell"
            return self._privilege
        if mode in {"auto", "adb-root"}:
            if real_android and self._is_root_id():
                self._privilege = "root"
                return self._privilege
            if real_android and self._su_works():
                self._privilege = "su"
                return self._privilege
            self._privilege = "shell"
            return self._privilege
        if mode == "su":
            if not real_android:
                raise TransportError("local 环境不是 Android shell（可能在 proot/容器中），不能获得真实 root")
            if self._su_works():
                self._privilege = "su"
                return self._privilege
            raise TransportError("指定了 --root-mode su，但 su -c id 未返回 uid=0")
        raise TransportError(f"未知 root mode：{mode}")

    def try_adb_root(self) -> bool:
        return False


class AdbTransport(ShellTransport):
    name = "adb"

    def __init__(self, adb_path: str = "adb", serial: str = "", *, timeout: int = 120, su_command: str = "su") -> None:
        super().__init__(timeout=timeout, su_command=su_command)
        self.adb_path = adb_path
        self.serial = serial
        self._prefix = [adb_path] + (["-s", serial] if serial else [])
        self._ready = False
        self.adb_devices: list[dict[str, str]] = []

    def _shell_argv(self, command: str) -> list[str]:
        return self._prefix + ["shell", command]

    def _exec_out_argv(self, command: str) -> list[str]:
        return self._prefix + ["exec-out", command]

    def _set_serial(self, serial: str) -> None:
        self.serial = serial
        self._prefix = [self.adb_path] + (["-s", serial] if serial else [])

    def list_devices(self) -> list[dict[str, str]]:
        try:
            result = self._run([self.adb_path, "devices", "-l"], timeout=60)
        except TransportError as exc:
            raise TransportError(
                f"无法运行 adb（{self.adb_path}）。请安装 Android 官方 Platform Tools，"
                "把 adb.exe 加入 PATH，或用 --adb-path 指定 adb.exe 的完整路径。"
                f" 详情：{exc}"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:500]
            raise TransportError(
                "无法运行 adb devices。请确认已安装 Android Platform Tools，并把 adb 加入 PATH 或使用 --adb-path 指定 adb.exe。"
                + (f" 详情：{detail}" if detail else "")
            )
        self.adb_devices = parse_adb_devices(result.stdout)
        return self.adb_devices

    def ensure_ready(self) -> None:
        """Resolve exactly one authorized USB device or fail with actionable instructions."""
        if self._ready:
            return
        devices = self.list_devices()
        if not devices:
            raise TransportError(
                "未检测到 Android 设备。请：1) 用支持数据传输的 USB 线连接手机；"
                "2) 手机开发者选项开启 USB 调试；3) 解锁手机并在 RSA 授权弹窗点允许；"
                "4) 运行 adb devices 确认状态为 device。"
            )
        ready = [d for d in devices if d["state"] == "device"]
        if self.serial:
            target = next((d for d in devices if d["serial"] == self.serial), None)
            if target is None:
                listing = "; ".join(f"{d['serial']}({d['state']})" for d in devices)
                raise TransportError(f"指定设备 {self.serial} 未连接。当前设备：{listing or '无'}")
            if target["state"] != "device":
                label = _ADB_STATE_LABELS.get(target["state"], target["state"])
                raise TransportError(f"设备 {self.serial} 当前状态为 {target['state']}：{label}")
            self._ready = True
            return
        if not ready:
            states = "; ".join(f"{d['serial']}({d['state']})" for d in devices)
            hint = "、".join(sorted({_ADB_STATE_LABELS.get(d["state"], d["state"]) for d in devices}))
            raise TransportError(f"设备存在但不可用：{states}。{hint}")
        if len(ready) > 1:
            listing = "; ".join(d["serial"] for d in ready)
            raise TransportError(
                f"检测到多个已授权设备：{listing}。为避免误操作其他设备，必须用 --serial <序列号> 明确指定目标。"
            )
        self._set_serial(ready[0]["serial"])
        self._ready = True

    def device_serial(self) -> str:
        return self.serial or ""

    def health_ok(self) -> bool:
        if not self.serial:
            return False
        try:
            result = self._run([self.adb_path, "-s", self.serial, "get-state"], timeout=15)
        except TransportError:
            return False
        return result.returncode == 0 and result.stdout.strip() == "device"

    def device_info(self) -> DeviceInfo:
        info = super().device_info()
        info.serial = self.serial or info.serial
        return info

    def try_adb_root(self) -> bool:
        try:
            result = self._run(self._prefix + ["root"], timeout=60)
        except TransportError:
            return False
        if result.returncode != 0:
            return False
        try:
            self._run(self._prefix + ["wait-for-device"], timeout=120)
        except TransportError:
            return False
        self._privilege = "shell"
        return self._is_root_id()


class RishTransport(ShellTransport):
    name = "rish"

    def __init__(self, rish_path: str = "rish", *, timeout: int = 120, su_command: str = "su") -> None:
        super().__init__(timeout=timeout, su_command=su_command)
        self.rish_path = rish_path

    def _shell_argv(self, command: str) -> list[str]:
        return [self.rish_path, "-c", command]

    def _exec_out_argv(self, command: str) -> list[str]:
        return [self.rish_path, "-c", command]


class LocalTransport(ShellTransport):
    name = "local"

    def __init__(self, *, timeout: int = 120, su_command: str = "su", shell_path: str | None = None) -> None:
        super().__init__(timeout=timeout, su_command=su_command)
        self.shell_path = shell_path or ("sh" if os.name != "nt" else "bash")

    def _shell_argv(self, command: str) -> list[str]:
        return [self.shell_path, "-c", command]

    def _exec_out_argv(self, command: str) -> list[str]:
        return [self.shell_path, "-c", command]


def create_transport(args: Any, config: dict[str, Any] | None = None) -> Transport:
    """Build a transport from parsed CLI args (or an argparse Namespace)."""
    name = getattr(args, "transport", "adb")
    timeout = int(getattr(args, "timeout", 120) or 120)
    su_command = str(getattr(args, "su_command", "su") or "su")
    if name == "mock":
        from .mock_transport import MockTransport

        return MockTransport(
            mock_root=str(getattr(args, "mock_root", "") or ""),
            packages_path=getattr(args, "mock_packages", None),
            caps_path=getattr(args, "mock_caps", None),
            device_path=getattr(args, "mock_device", None),
            config=config,
        )
    if name == "adb":
        return AdbTransport(
            adb_path=str(getattr(args, "adb_path", "adb") or "adb"),
            serial=str(getattr(args, "serial", "") or ""),
            timeout=timeout,
            su_command=su_command,
        )
    if name == "hdc":
        from .hdc_transport import HdcTransport

        port = getattr(args, "hdc_server_port", None)
        return HdcTransport(
            hdc_path=str(getattr(args, "hdc_path", "hdc") or "hdc"),
            serial=str(getattr(args, "serial", "") or ""),
            timeout=timeout,
            server_port=int(port) if port else None,
        )
    if name == "rish":
        return RishTransport(
            rish_path=str(getattr(args, "rish_path", "rish") or "rish"),
            timeout=timeout,
            su_command=su_command,
        )
    if name == "local":
        return LocalTransport(timeout=timeout, su_command=su_command, shell_path=getattr(args, "sh_path", None))
    raise TransportError(f"未知 transport：{name}")
