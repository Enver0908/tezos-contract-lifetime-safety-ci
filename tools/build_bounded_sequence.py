from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.bounded_sequence import PROTOCOL_PATH, build_sequence_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fresh 4096-transition bounded-list evidence corpus")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    parser.add_argument("--runtime", type=Path, default=Path(__file__).resolve().parents[1] / "runtime.lock.json")
    args = parser.parse_args()

    def progress(current: int, total: int) -> None:
        print(f"Octez transitions: {current}/{total}", flush=True)

    try:
        result = build_sequence_corpus(
            args.output_dir,
            protocol_path=args.protocol,
            runtime_path=args.runtime,
            progress=progress,
        )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(json.dumps({"result": "incomplete", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps({"result": "complete", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

