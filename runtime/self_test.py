import argparse
import json
import sys
from pathlib import Path

from runtime.compatibility import load_compatibility, select_backend
from runtime.optimizer import build_runtime_profile
from runtime.types import GpuInfo


FIXTURES = {
    "sm86": ("RTX 3090", (8, 6), 24 * 1024),
    "sm89": ("RTX 4090", (8, 9), 24 * 1024),
    "sm90": ("H100", (9, 0), 80 * 1024),
    "sm120": ("RTX 5090", (12, 0), 32 * 1024),
}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_fixture(fixture: str, matrix_path: Path, backend_root: Path) -> dict:
    if fixture not in FIXTURES:
        raise ValueError(f"unknown fixture: {fixture}")
    name, capability, memory = FIXTURES[fixture]
    gpu = GpuInfo(name, capability, memory)
    matrix = load_compatibility(matrix_path)
    choice = select_backend(gpu, matrix, backend_root=backend_root)
    profile = build_runtime_profile(gpu, choice, matrix)
    return {
        "gpu": name,
        "compute_capability": fixture,
        "backend": choice.name,
        "fallback_reason": choice.fallback_reason,
        "profile": profile.__dict__,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a mock-GPU runtime self-test")
    parser.add_argument("--fixture", choices=sorted(FIXTURES), required=True)
    parser.add_argument("--compatibility", default=str(PROJECT_ROOT / "config" / "compatibility.json"))
    parser.add_argument("--backend-root", default=str(PROJECT_ROOT / "backends"))
    args = parser.parse_args(argv)
    try:
        result = run_fixture(args.fixture, Path(args.compatibility), Path(args.backend_root))
    except Exception as exc:
        print(json.dumps({"event": "self_test_failed", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"event": "self_test_passed", **result}, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
