"""Unit tests: ADB device listing parsing, readiness and health checks."""

from __future__ import annotations

import subprocess

import pytest

from adc_lib.transport import AdbTransport, TransportError, parse_adb_devices

DEVICES_HEADER = "List of devices attached\n"


# ---------------------------------------------------------------- parsing
def test_parse_adb_devices_ready_device_with_details():
    output = (
        "List of devices attached\n"
        "R58M12345\tdevice product:mock model:MockPhone device:mock transport_id:1\n"
    )
    devices = parse_adb_devices(output)
    assert devices == [
        {
            "serial": "R58M12345",
            "state": "device",
            "details": "product:mock model:MockPhone device:mock transport_id:1",
        }
    ]


def test_parse_adb_devices_states():
    output = (
        "List of devices attached\n"
        "AAA\tdevice\n"
        "BBB\tunauthorized\n"
        "CCC\toffline\n"
        "DDD\trecovery\n"
        "EEE\tsideload\n"
        "FFF\tbootloader\n"
    )
    devices = parse_adb_devices(output)
    assert [(d["serial"], d["state"]) for d in devices] == [
        ("AAA", "device"),
        ("BBB", "unauthorized"),
        ("CCC", "offline"),
        ("DDD", "recovery"),
        ("EEE", "sideload"),
        ("FFF", "bootloader"),
    ]


def test_parse_adb_devices_no_permissions():
    output = (
        "List of devices attached\n"
        "1234abcd\tno permissions (user in plugdev group; are your udev rules wrong?)\n"
    )
    devices = parse_adb_devices(output)
    assert len(devices) == 1
    assert devices[0]["serial"] == "1234abcd"
    assert devices[0]["state"] == "no permissions"
    assert devices[0]["details"].startswith("permissions")


def test_parse_adb_devices_multiple_devices():
    output = (
        "List of devices attached\n"
        "AAA\tdevice transport_id:1\n"
        "BBB\tdevice transport_id:2\n"
    )
    devices = parse_adb_devices(output)
    assert [d["serial"] for d in devices] == ["AAA", "BBB"]
    assert all(d["state"] == "device" for d in devices)


@pytest.mark.parametrize(
    "output",
    [
        "",
        DEVICES_HEADER,
        DEVICES_HEADER + "\n\n",
        "* daemon not running; starting now at tcp:5037\n* daemon started successfully\n",
    ],
)
def test_parse_adb_devices_empty_outputs(output):
    assert parse_adb_devices(output) == []


def test_parse_adb_devices_ignores_garbage_and_unknown_states():
    output = (
        "List of devices attached\n"
        "justoneword\n"
        "CHARGING\tcharging\n"
        "OK\tdevice\n"
    )
    devices = parse_adb_devices(output)
    assert [d["serial"] for d in devices] == ["OK"]


def test_parse_adb_devices_handles_crlf():
    output = "List of devices attached\r\nR58M12345\tdevice product:mock\r\n"
    devices = parse_adb_devices(output)
    assert len(devices) == 1
    assert devices[0]["serial"] == "R58M12345"
    assert devices[0]["state"] == "device"


# ---------------------------------------------------------------- fake transport
def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)


class FakeAdb:
    """Replaces AdbTransport._run with scripted adb responses."""

    def __init__(self, *, devices_output="", devices_rc=0, get_state="device", props="", fail=None):
        self.devices_output = devices_output
        self.devices_rc = devices_rc
        self.get_state = get_state
        self.props = props
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, timeout=120, stdin_data=None):
        self.calls.append([str(item) for item in argv])
        if self.fail is not None:
            raise self.fail
        if argv[1:] == ["devices", "-l"]:
            return _completed(argv, self.devices_rc, self.devices_output, "adb failure" if self.devices_rc else "")
        if argv[-1] == "get-state":
            if self.get_state is None:
                return _completed(argv, 1, "", "device offline")
            return _completed(argv, 0, self.get_state + "\n")
        if argv[-2:] == ["shell", "getprop"]:
            return _completed(argv, 0, self.props)
        if argv[-2:] == ["shell", "true"]:
            return _completed(argv, 0, "")
        return _completed(argv, 1, "", "unexpected command")


def _adb(devices_output="", *, serial="", devices_rc=0, get_state="device", props="", fail=None):
    transport = AdbTransport(adb_path="adb", serial=serial)
    transport._run = FakeAdb(
        devices_output=devices_output,
        devices_rc=devices_rc,
        get_state=get_state,
        props=props,
        fail=fail,
    )
    return transport


# ---------------------------------------------------------------- list_devices
def test_list_devices_parses_and_caches():
    output = DEVICES_HEADER + "AAA\tdevice transport_id:1\n"
    transport = _adb(output)
    devices = transport.list_devices()
    assert devices[0]["serial"] == "AAA"
    assert transport.adb_devices == devices


def test_list_devices_adb_failure_raises_transport_error():
    transport = _adb(devices_rc=1)
    with pytest.raises(TransportError) as excinfo:
        transport.list_devices()
    message = str(excinfo.value)
    assert "adb devices" in message
    assert "Platform Tools" in message


def test_list_devices_missing_adb_binary_raises_transport_error():
    transport = _adb(devices_output="", fail=TransportError("找不到命令或设备工具：adb"))
    with pytest.raises(TransportError) as excinfo:
        transport.list_devices()
    assert "adb" in str(excinfo.value)


# ---------------------------------------------------------------- ensure_ready
def test_ensure_ready_no_devices():
    transport = _adb(DEVICES_HEADER)
    with pytest.raises(TransportError) as excinfo:
        transport.ensure_ready()
    message = str(excinfo.value)
    assert "未检测到" in message
    assert "USB" in message


def test_ensure_ready_single_device_sets_serial_and_prefix():
    transport = _adb(DEVICES_HEADER + "R58M12345\tdevice product:mock\n")
    transport.ensure_ready()
    assert transport.serial == "R58M12345"
    assert transport._prefix[:3] == ["adb", "-s", "R58M12345"]
    # second call must short-circuit (only one adb devices invocation)
    transport.ensure_ready()
    assert len(transport._run.calls) == 1


def test_ensure_ready_unauthorized_device_is_actionable():
    transport = _adb(DEVICES_HEADER + "AAA\tunauthorized usb:1-1\n")
    with pytest.raises(TransportError) as excinfo:
        transport.ensure_ready()
    message = str(excinfo.value)
    assert "不可用" in message
    assert "未授权" in message
    assert "AAA" in message


def test_ensure_ready_offline_device_is_actionable():
    transport = _adb(DEVICES_HEADER + "AAA\toffline\n")
    with pytest.raises(TransportError) as excinfo:
        transport.ensure_ready()
    assert "离线" in str(excinfo.value)


def test_ensure_ready_no_permissions_state_is_actionable():
    transport = _adb(DEVICES_HEADER + "AAA\tno permissions (udev rules)\n")
    with pytest.raises(TransportError) as excinfo:
        transport.ensure_ready()
    assert "无权限" in str(excinfo.value)


def test_ensure_ready_multiple_devices_requires_serial():
    transport = _adb(DEVICES_HEADER + "AAA\tdevice\nBBB\tdevice\n")
    with pytest.raises(TransportError) as excinfo:
        transport.ensure_ready()
    message = str(excinfo.value)
    assert "多个" in message
    assert "--serial" in message
    assert "AAA" in message and "BBB" in message


def test_ensure_ready_explicit_serial():
    transport = _adb(DEVICES_HEADER + "AAA\tdevice\nBBB\tdevice\n", serial="BBB")
    transport.ensure_ready()
    assert transport.serial == "BBB"
    assert transport._prefix[:3] == ["adb", "-s", "BBB"]


def test_ensure_ready_explicit_serial_not_connected():
    transport = _adb(DEVICES_HEADER + "AAA\tdevice\n", serial="MISSING")
    with pytest.raises(TransportError) as excinfo:
        transport.ensure_ready()
    message = str(excinfo.value)
    assert "MISSING" in message
    assert "未连接" in message


def test_ensure_ready_explicit_serial_wrong_state():
    transport = _adb(DEVICES_HEADER + "AAA\tunauthorized\n", serial="AAA")
    with pytest.raises(TransportError) as excinfo:
        transport.ensure_ready()
    assert "unauthorized" in str(excinfo.value)


# ---------------------------------------------------------------- health / identity
def test_health_ok_requires_serial_and_get_state_device():
    assert _adb(DEVICES_HEADER).health_ok() is False

    healthy = _adb(DEVICES_HEADER, serial="AAA", get_state="device")
    assert healthy.health_ok() is True

    offline = _adb(DEVICES_HEADER, serial="AAA", get_state="offline")
    assert offline.health_ok() is False

    failing = _adb(DEVICES_HEADER, serial="AAA", fail=TransportError("adb gone"))
    assert failing.health_ok() is False


def test_device_serial_and_device_info_serial():
    transport = _adb(
        DEVICES_HEADER + "AAA\tdevice\n",
        props="[ro.build.version.sdk]: [34]\n[ro.product.model]: [MockPhone]\n",
    )
    transport.ensure_ready()
    assert transport.device_serial() == "AAA"
    info = transport.device_info()
    assert info.serial == "AAA"
    assert info.sdk == 34
    assert info.model == "MockPhone"


# ---------------------------------------------------------------- CLI devices command
def _write_fake_adb_cmd(directory, lines, *, rc=0):
    script = directory / "adb.cmd"
    body = ["@echo off"]
    if rc:
        body.append("echo fake adb failure 1>&2")
        body.append(f"exit /b {rc}")
    else:
        body.extend(f"echo {line}" for line in lines)
        body.append("exit /b 0")
    script.write_text("\r\n".join(body) + "\r\n", encoding="ascii")
    return script


def test_devices_command_lists_states(tmp_path):
    if __import__("os").name != "nt":
        pytest.skip("fake adb.cmd 仅用于 Windows")
    env = _make_env(tmp_path)
    fake = _write_fake_adb_cmd(
        tmp_path,
        [
            "List of devices attached",
            "R58M12345\tdevice product:mock model:MockPhone transport_id:1",
            "UNAUTH01\tunauthorized transport_id:2",
        ],
    )
    result = env.ok("devices", "--adb-path", str(fake))
    payload = result.json()
    assert payload["adb_path"] == str(fake)
    states = {item["serial"]: item["state"] for item in payload["devices"]}
    assert states == {"R58M12345": "device", "UNAUTH01": "unauthorized"}


def test_devices_command_human_output(tmp_path):
    if __import__("os").name != "nt":
        pytest.skip("fake adb.cmd 仅用于 Windows")
    env = _make_env(tmp_path)
    fake = _write_fake_adb_cmd(tmp_path, ["List of devices attached", "R58M12345\tdevice"])
    result = env.run("devices", "--adb-path", str(fake), want_json=False)
    result.expect(0)
    assert "R58M12345" in result.stdout
    assert "device" in result.stdout


def test_devices_command_adb_failure_rc4(tmp_path):
    if __import__("os").name != "nt":
        pytest.skip("fake adb.cmd 仅用于 Windows")
    env = _make_env(tmp_path)
    fake = _write_fake_adb_cmd(tmp_path, [], rc=1)
    result = env.run("devices", "--adb-path", str(fake))
    result.expect_error(4)
    assert "adb devices" in result.stderr


def _make_env(tmp_path):
    from adc_testkit import AdcEnv

    return AdcEnv(tmp_path)

def test_doctor_with_adb_no_devices_returns_rc4(tmp_path):
    if __import__("os").name != "nt":
        pytest.skip("fake adb.cmd 仅用于 Windows")
    env = _make_env(tmp_path)
    fake = _write_fake_adb_cmd(tmp_path, ["List of devices attached"])
    result = env.run("doctor", "--transport", "adb", "--adb-path", str(fake))
    result.expect_error(4)
    assert "未检测到" in result.stderr or "USB" in result.stderr


def test_doctor_with_adb_unauthorized_returns_rc4(tmp_path):
    if __import__("os").name != "nt":
        pytest.skip("fake adb.cmd 仅用于 Windows")
    env = _make_env(tmp_path)
    fake = _write_fake_adb_cmd(
        tmp_path, ["List of devices attached", "UNAUTH01\tunauthorized usb:1-1"]
    )
    result = env.run("doctor", "--transport", "adb", "--adb-path", str(fake))
    result.expect_error(4)
    assert "未授权" in result.stderr or "不可用" in result.stderr


def test_doctor_with_missing_adb_binary_returns_rc4(tmp_path):
    env = _make_env(tmp_path)
    result = env.run("doctor", "--transport", "adb", "--adb-path", str(tmp_path / "no-adb-here.exe"))
    result.expect_error(4)
    assert "adb" in result.stderr
