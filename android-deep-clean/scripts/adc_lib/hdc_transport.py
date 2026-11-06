"""HarmonyOS NEXT transport through hdc (HarmonyOS Device Connector).

hdc is the official debugging bridge for HarmonyOS 5 / NEXT.  It is not adb:
the command set, device selection (-t <connect-key>) and file transfer
(file recv) are different.  Only documented operations are used here.

Capability notes (must still be smoke-tested on a real device):
- hdc shell provides a toybox-based shell; available commands are probed by doctor.
- Public storage: /storage/media/100/local/files (Docs/Desktop/Download/...).
- Media library: /mnt/data/<uid>/media_fuse/Photo (API 22+).
- App sandboxes are private. Debug apps can be inspected with hdc shell -b,
  but this transport never deletes inside a sandbox.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .models import DeviceInfo, PackageIndex
from .platform import HARMONY_NEXT
from .transport import ShellTransport
from .util import TransportError, normalize_device_path, shell_quote

_EMPTY_MARKERS = {"[empty]", "empty"}
_BUNDLE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")


class HdcTransport(ShellTransport):
    name = "hdc"

    def __init__(self, hdc_path: str = "hdc", serial: str = "", *, timeout: int = 120, server_port: int | None = None) -> None:
        super().__init__(timeout=timeout, su_command="su")
        self.hdc_path = hdc_path or "hdc"
        self.serial = serial or ""
        self.server_port = server_port
        self._privilege = "shell"

    # -- argv -------------------------------------------------------------
    def _hdc_argv(self, extra: list[str]) -> list[str]:
        argv = [self.hdc_path]
        if self.server_port:
            argv += ["-s", f"127.0.0.1:{int(self.server_port)}"]
        if self.serial:
            argv += ["-t", self.serial]
        return argv + extra

    def _shell_argv(self, command: str) -> list[str]:
        return self._hdc_argv(["shell", command])

    def _exec_out_argv(self, command: str) -> list[str]:
        return self._shell_argv(command)

    # -- device discovery -------------------------------------------------
    def list_targets(self, *, timeout: int = 30) -> list[dict[str, str]]:
        result = self._run([self.hdc_path, "list", "targets", "-v"], timeout=timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:400]
            raise TransportError(
                f"hdc list targets 失败：{detail or '请用 --hdc-path 指定 hdc，或把 Command Line Tools 的 toolchains 加入 PATH'}"
            )
        targets: list[dict[str, str]] = []
        for raw in result.stdout.splitlines():
            line = raw.strip()
            if not line or line.startswith("*") or line.lower() in _EMPTY_MARKERS:
                continue
            parts = line.split()
            key = parts[0]
            if key.lower() in _EMPTY_MARKERS:
                continue
            if key.lower() in {"list", "targets"}:
                continue
            targets.append({"serial": key, "state": "device", "details": " ".join(parts[1:])})
        return targets

    def list_devices(self) -> list[dict[str, str]]:
        return self.list_targets()

    def ensure_ready(self) -> None:
        targets = [item["serial"] for item in self.list_targets()]
        if self.serial:
            if self.serial not in targets:
                raise TransportError(
                    "指定的 HarmonyOS 设备未连接：" + self.serial
                    + (("；当前设备：" + ", ".join(targets)) if targets else "；请检查 USB 调试与 hdc list targets")
                )
            return
        if not targets:
            raise TransportError("未检测到 HarmonyOS 设备：请开启开发者模式/USB 调试，并确认 hdc list targets 有输出")
        if len(targets) > 1:
            raise TransportError("检测到多台 HarmonyOS 设备，必须用 --serial 指定目标：" + ", ".join(targets))
        self.serial = targets[0]

    def device_serial(self) -> str:
        return self.serial

    def health_ok(self) -> bool:
        try:
            return bool(self.serial) and self.serial in [item["serial"] for item in self.list_targets(timeout=20)]
        except TransportError:
            return False

    # -- identity ---------------------------------------------------------
    def _param(self, key: str) -> str:
        for command in (f"param get {shell_quote(key)}", f"getprop {shell_quote(key)}"):
            rc, out, _ = self.shell_rc(command, timeout=20)
            if rc == 0:
                value = out.strip().splitlines()[-1].strip() if out.strip() else ""
                if value:
                    return value
        return ""

    def device_info(self) -> DeviceInfo:
        model = self._param("const.product.model") or self._param("ro.product.model")
        manufacturer = self._param("const.product.manufacturer") or self._param("ro.product.manufacturer")
        os_version = self._param("const.product.os.dist.version") or self._param("const.product.software.version")
        api_version = self._param("const.product.os.dist.apiversion") or self._param("const.ohos.apiversion")
        distribution = self._param("const.product.os.dist.name") or "HarmonyOS"
        fingerprint = "|".join(
            item for item in (self.serial, model, os_version, self._param("const.product.fingerprint")) if item
        )
        return DeviceInfo(
            serial=self.serial,
            sdk=0,
            release="",
            manufacturer=manufacturer,
            model=model,
            build_type=self._param("const.product.build.type"),
            abi=self._param("const.product.cpu.abilist"),
            product=self._param("const.product.name"),
            fingerprint=fingerprint,
            platform=HARMONY_NEXT,
            os_version=os_version,
            api_version=api_version,
            distribution=distribution,
            harmony_version=os_version,
            shell="hdc",
        )

    def list_packages(self) -> PackageIndex:
        index = PackageIndex()
        rc, out, err = self.shell_rc("bm dump -a", timeout=180)
        if rc != 0:
            index.available = False
            index.error = (err or out).strip()[:500] or "bm dump -a 不可用"
            return index
        names = set()
        for line in out.splitlines():
            matches = _BUNDLE_RE.findall(line)
            for name in matches:
                if not name.startswith(("com.", "ohos.", "cn.")) and "." not in name:
                    continue
                names.add(name)
        if not names:
            index.available = False
            index.error = "bm dump -a 输出无法解析 bundleName"
            return index
        index.installed = names
        index.system = set()
        return index

    # -- privilege / files -------------------------------------------------
    def configure_privilege(self, mode: str = "auto") -> str:
        self._privilege = "shell"
        return "shell"

    def try_adb_root(self) -> bool:
        return False

    def pull(self, src: str, dst: str, *, max_bytes: int | None = None) -> int:
        src = normalize_device_path(src)
        if not self.serial:
            self.ensure_ready()
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        argv = self._hdc_argv(["file", "recv", src, str(dst)])
        try:
            proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, check=False)
        except FileNotFoundError as exc:
            raise TransportError(f"找不到 hdc 可执行文件：{self.hdc_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TransportError(f"hdc file recv 超时：{src}") from exc
        if proc.returncode != 0 or not Path(dst).exists():
            detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()[:300]
            raise TransportError(f"hdc 拉取文件失败：{src} {detail}")
        size = os.path.getsize(dst)
        if max_bytes is not None and size > int(max_bytes):
            with open(dst, "r+b") as fh:
                fh.truncate(int(max_bytes))
            size = int(max_bytes)
        return size

    def read_head(self, path: str, length: int = 65536) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "head.bin"
            self.pull(path, str(target), max_bytes=length)
            return target.read_bytes()[:length]
