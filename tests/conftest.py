"""pytest fixtures for the android-deep-clean test suite.

Every test gets its own throw-away mock Android device (temporary directory)
and its own state directory; nothing outside pytest's tmp dirs is written.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "android-deep-clean" / "scripts"

for _path in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from adc_testkit import AdcEnv  # noqa: E402


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AdcEnv:
    """Isolated mock Android device + CLI state directory."""
    # Never leak a developer's real ADC_STATE into the suite.
    monkeypatch.delenv("ADC_STATE", raising=False)
    return AdcEnv(tmp_path)
