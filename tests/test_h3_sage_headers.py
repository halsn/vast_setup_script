"""Exercise the real shell preflight with a minimal CUDA filesystem."""
import os
from pathlib import Path
import subprocess

from tests.test_h3_profiles import BASH

BASE = Path(__file__).resolve().parents[1] / "scripts/h3_comfui_base.sh"
HEADERS = "cuda_runtime.h cuda_fp16.h cublas_v2.h cublasLt.h cusparse.h cusolverDn.h curand_kernel.h"


def environment(tmp_path, headers=False):
    cuda = tmp_path / "cuda"
    (cuda / "bin").mkdir(parents=True)
    nvcc = cuda / "bin/nvcc"
    nvcc.write_text("#!/usr/bin/env bash\nif [[ $1 == --version ]]; then echo 'Cuda compilation tools, release 13.2, V13.2'; else cat >/dev/null; echo PROBE; fi\n")
    subprocess.run([BASH, "-c", f'chmod +x "{nvcc.as_posix()}"'], check=True)
    if headers:
        (cuda / "include").mkdir()
        for name in HEADERS.split():
            (cuda / "include" / name).touch()
    return f'''H3_BOOTSTRAP_LIB_ONLY=1
source "{BASE.as_posix()}"
CUDA_HOME="{cuda.as_posix()}"
COMFY_PYTHON="{Path(os.sys.executable).as_posix()}"
'''


def run(body):
    return subprocess.run([BASH, "-c", body], text=True, capture_output=True)


def test_complete_headers_need_no_install_and_keep_existing_flags(tmp_path):
    result = run(environment(tmp_path, headers=True) + '''
apt-get() { echo UNEXPECTED_APT; return 99; }
CPATH=/existing/include
NVCC_APPEND_FLAGS=--threads=2
prepare_sage_build_environment
printf 'CPATH=%s\\nFLAGS=%s\\n' "$CPATH" "$NVCC_APPEND_FLAGS"
''')
    assert result.returncode == 0, result.stderr
    assert "UNEXPECTED_APT" not in result.stdout
    assert "--threads=2" in result.stdout
    assert "/cuda/include" in result.stdout
    assert "/existing/include" in result.stdout


def test_missing_headers_install_only_matching_cuda_development_packages(tmp_path):
    result = run(environment(tmp_path) + f'''
apt-get() {{
  printf 'APT %s\\n' "$*"
  if [[ "$1" == install ]]; then
    mkdir -p "$CUDA_HOME/include"
    for header in {HEADERS}; do touch "$CUDA_HOME/include/$header"; done
  fi
}}
sudo() {{ "$@"; }}
prepare_sage_build_environment
''')
    assert result.returncode == 0, result.stderr
    assert "libcusparse-dev-13-2" in result.stdout
    assert "libcublas-dev-13-2" in result.stdout
    assert "libcusolver-dev-13-2" in result.stdout
    assert "libcurand-dev-13-2" in result.stdout
    assert "cuda-cudart-dev-13-2" in result.stdout
    assert "cuda-drivers" not in result.stdout
    assert "pip install" not in result.stdout


def test_missing_headers_fail_before_build_when_repair_fails(tmp_path):
    result = run(environment(tmp_path) + '''
apt-get() { return 1; }
sudo() { "$@"; }
prepare_sage_build_environment
echo UNEXPECTED_BUILD
''')
    assert result.returncode != 0
    assert "UNEXPECTED_BUILD" not in result.stdout
    assert "CUDA" in result.stderr


def test_installed_packages_are_not_enough_without_the_headers(tmp_path):
    result = run(environment(tmp_path) + '''
apt-get() { return 0; }
sudo() { "$@"; }
prepare_sage_build_environment
echo UNEXPECTED_BUILD
''')
    assert result.returncode != 0
    assert "cusparse.h" in result.stderr
    assert "UNEXPECTED_BUILD" not in result.stdout


def test_header_check_detects_target_include_directory(tmp_path):
    body = environment(tmp_path)
    target = tmp_path / "cuda/targets/x86_64-linux/include"
    target.mkdir(parents=True)
    for name in HEADERS.split():
        (target / name).touch()
    result = run(body + '''
apt-get() { return 99; }
prepare_sage_build_environment
printf '%s\\n' "$NVCC_APPEND_FLAGS"
''')
    assert result.returncode == 0, result.stderr
    assert "targets/x86_64-linux/include" in result.stdout


def test_sage_source_installer_runs_header_preflight_first():
    body = f'''H3_BOOTSTRAP_LIB_ONLY=1
source "{BASE.as_posix()}"
COMFY_PYTHON=fake_python
VERSION=1.0.6
prepare_sage_build_environment() {{ echo PREFLIGHT; }}
fake_python() {{
  if [[ "$*" == *"pip install"* ]]; then
    if [[ "$*" != *"git+"* ]]; then return 1; fi
    echo SOURCE_BUILD
    VERSION=2.2.0
  elif [[ "$*" == *importlib.metadata* ]]; then echo "$VERSION"; fi
}}
verify_sageattention_kernel() {{ echo KERNEL; }}
install_sageattention
'''
    result = run(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["PREFLIGHT", "SOURCE_BUILD", "KERNEL"]


def test_failed_header_preflight_prevents_source_build_and_kernel_check():
    body = f'''H3_BOOTSTRAP_LIB_ONLY=1
source "{BASE.as_posix()}"
COMFY_PYTHON=fake_python
H3_SAGE_REQUIRED=1
prepare_sage_build_environment() {{ echo PREFLIGHT_FAILED; return 1; }}
fake_python() {{
  if [[ "$*" == *"pip install"* ]]; then
    if [[ "$*" == *"git+"* ]]; then echo UNEXPECTED_SOURCE_BUILD; fi
    return 1
  elif [[ "$*" == *importlib.metadata* ]]; then echo 1.0.6; fi
}}
verify_sageattention_kernel() {{ echo UNEXPECTED_KERNEL; }}
install_sageattention
'''
    result = run(body)
    assert result.returncode != 0
    assert "PREFLIGHT_FAILED" in result.stdout
    assert "UNEXPECTED_SOURCE_BUILD" not in result.stdout
    assert "UNEXPECTED_KERNEL" not in result.stdout


def test_first_package_attempt_cannot_trigger_an_unchecked_source_build():
    body = f'''H3_BOOTSTRAP_LIB_ONLY=1
source "{BASE.as_posix()}"
COMFY_PYTHON=fake_python
VERSION=1.0.6
prepare_sage_build_environment() {{ echo PREFLIGHT; }}
fake_python() {{
  if [[ "$*" == *"pip install"* ]]; then
    printf 'INSTALL %s\\n' "$*"
    if [[ "$*" != *"git+"* ]]; then return 1; fi
    VERSION=2.2.0
  elif [[ "$*" == *importlib.metadata* ]]; then echo "$VERSION"; fi
}}
verify_sageattention_kernel() {{ :; }}
install_sageattention
'''
    result = run(body)
    assert result.returncode == 0, result.stderr
    commands = [line for line in result.stdout.splitlines() if line.startswith("INSTALL ")]
    assert len(commands) == 2
    assert "--only-binary=:all:" in commands[0]
    assert "--only-binary" not in commands[1]
