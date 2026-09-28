from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write immutable identity metadata for a hosted evidence runner")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--workflow-ref", required=True)
    args = parser.parse_args(argv)

    metadata = {
        "schema_version": 1,
        "runner_id": args.runner_id,
        "commit_sha": args.commit,
        "workflow_run_id": args.run_id,
        "workflow_run_attempt": args.run_attempt,
        "workflow_ref": args.workflow_ref,
        "event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
        "runner_os": platform.platform(),
        "runner_architecture": platform.machine(),
        "python_version": platform.python_version(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(metadata, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
