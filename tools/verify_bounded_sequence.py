from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.bounded_sequence import PROTOCOL_PATH, SequenceError, save_new_json, verify_bounded_artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a bounded-sequence-v1 run and its gas boundaries")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    parser.add_argument("--runtime", type=Path, default=Path(__file__).resolve().parents[1] / "runtime.lock.json")
    parser.add_argument("--skip-direct-replays", action="store_true")
    args = parser.parse_args()
    try:
        result = verify_bounded_artifacts(
            args.corpus_dir,
            args.report,
            protocol_path=args.protocol,
            runtime_path=args.runtime,
            replay_direct_boundaries=not args.skip_direct_replays,
        )
        save_new_json(args.output, result)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(json.dumps({"result": "invalid", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["technical_mvp_bounded_go"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())

