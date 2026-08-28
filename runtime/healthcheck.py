import argparse
import json
import os
import sys
from pathlib import Path


def is_healthy(path: Path) -> bool:
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, ValueError, TypeError):
        return False
    return payload.get("status") == "ready" and isinstance(payload.get("backend"), str)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check H3 Worker runtime readiness")
    parser.add_argument("--runtime-config", default=os.getenv("H3_RUNTIME_CONFIG", "/run/h3/runtime.json"))
    args = parser.parse_args(argv)
    return 0 if is_healthy(Path(args.runtime_config)) else 1


if __name__ == "__main__":
    sys.exit(main())
