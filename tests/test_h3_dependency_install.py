"""Regression coverage for dependency failures reported by Vast deployments."""
import os
from pathlib import Path
import subprocess

import pytest

from tests.test_h3_profiles import BASH

BASE = Path(__file__).resolve().parents[1] / "scripts/h3_comfui_base.sh"
SAGE_REV = "eb615cf6cf4d221338033340ee2de1c37fbdba4a"


def run_bash(body):
    return subprocess.run(
        [BASH, "-c", f'H3_BOOTSTRAP_LIB_ONLY=1\nsource "{BASE.as_posix()}"\n' + body],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("required", [0, 1])
@pytest.mark.parametrize("source_succeeds", [False, True])
def test_missing_sage_package_uses_pinned_source_and_preserves_failure_policy(required, source_succeeds):
    run = run_bash(f'''
COMFY_PYTHON=fake_python
H3_SAGE_REQUIRED={required}
TEST_VERSION=1.0.6
fake_python() {{
  if [[ "$*" == *"pip install"* ]]; then
    printf 'INSTALL %s\\n' "$*"
    if [[ "$*" == *"git+https://github.com/thu-ml/SageAttention.git@{SAGE_REV}"* ]]; then
      {'TEST_VERSION=2.2.0' if source_succeeds else 'return 1'}
    else
      printf 'No matching distribution found\\n' >&2
      return 1
    fi
  elif [[ "$*" == *"importlib.metadata"* ]]; then
    printf '%s\\n' "$TEST_VERSION"
  fi
}}
verify_sageattention_kernel() {{ printf 'KERNEL_VERIFIED\\n'; }}
install_sageattention
printf 'STATUS=%s\\n' "$SAGE_STATUS"
''')
    expected = 0 if source_succeeds or not required else 1
    assert run.returncode == expected, run.stderr
    commands = [line for line in run.stdout.splitlines() if line.startswith("INSTALL ")]
    assert len(commands) == 2
    assert all("--no-deps --no-build-isolation" in command for command in commands)
    assert f"git+https://github.com/thu-ml/SageAttention.git@{SAGE_REV}" in commands[1]
    assert ("KERNEL_VERIFIED" in run.stdout) == source_succeeds
    if source_succeeds:
        assert "STATUS=installed" in run.stdout
    elif not required:
        assert "STATUS=not-installed-no-verified-wheel" in run.stdout


def test_dependencies_constrain_hub_in_both_installs_and_keep_torch_pins(tmp_path):
    (tmp_path / "requirements.txt").write_text("torch\ntransformers\n")
    python = Path(os.sys.executable).as_posix()
    run = run_bash(f'''
COMFY_DIR="{tmp_path.as_posix()}"
COMFY_PYTHON=fake_python
use_vast_comfy_base() {{ return 0; }}
fake_python() {{
  if [[ "$1" == "-" ]]; then
    "{python}" "$@"
  elif [[ "$*" == *"pip install"* ]]; then
    printf 'INSTALL %s\\n' "$*"
    local previous="" arg
    for arg in "$@"; do
      if [[ "$previous" == "-c" ]]; then
        grep -Fx 'huggingface-hub>=0.34,<2' "$arg" || return 99
        printf 'HUB_CONSTRAINED\\n'
      fi
      previous="$arg"
    done
  fi
}}
update_python_dependencies
''')
    assert run.returncode == 0, run.stderr
    assert run.stdout.count("HUB_CONSTRAINED") == 2
    assert "huggingface_hub>=0.34,<2" in run.stdout


def test_source_fallback_still_requires_kernel_verification():
    run = run_bash(f'''
COMFY_PYTHON=fake_python
TEST_VERSION=1.0.6
fake_python() {{
  if [[ "$*" == *"pip install"* ]]; then
    [[ "$*" == *"git+https://github.com/thu-ml/SageAttention.git@{SAGE_REV}"* ]] || return 1
    TEST_VERSION=2.2.0
  elif [[ "$*" == *"importlib.metadata"* ]]; then
    printf '%s\\n' "$TEST_VERSION"
  fi
}}
verify_sageattention_kernel() {{ return 1; }}
install_sageattention
''')
    assert run.returncode != 0
    assert "verification failed" in run.stderr


def test_version_override_does_not_install_different_source_release():
    run = run_bash('''
COMFY_PYTHON=fake_python
H3_SAGE_VERSION=1.2.3
fake_python() {
  if [[ "$*" == *"pip install"* ]]; then
    printf 'INSTALL %s\\n' "$*"
    return 1
  elif [[ "$*" == *"importlib.metadata"* ]]; then
    printf '1.0.6\\n'
  fi
}
install_sageattention
''')
    assert run.returncode != 0
    assert "sageattention==1.2.3" in run.stdout
    assert "git+" not in run.stdout
