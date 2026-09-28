from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


RUNNERS = ("one", "two", "three")
FILES = (
    "metadata.json",
    "technical-mvp-go-v3.json",
    "run-a/corpus/manifest.json",
    "run-a/corpus/state-trace.json",
    "run-a/measurement/report.json",
    "run-a/bounded-acceptance.json",
    "run-b/corpus/manifest.json",
    "run-b/corpus/state-trace.json",
    "run-b/measurement/report.json",
    "run-b/bounded-acceptance.json",
    "growth-v2/report.json",
    "growth-v2/acceptance.json",
)


def package(source: Path, summary_path: Path, output: Path) -> str:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite evidence bundle: {output}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("hosted_technical_go") != "go":
        raise ValueError("only a verified hosted GO may be published")

    members: dict[str, bytes] = {}
    members["hosted-acceptance.json"] = summary_path.read_bytes()
    for runner in RUNNERS:
        base = source / f"bounded-sequence-v1-{runner}"
        for relative in FILES:
            path = base / relative
            if not path.is_file() or path.stat().st_size == 0:
                raise FileNotFoundError(f"missing public evidence file: {path}")
            members[f"{runner}/{relative.replace(chr(92), '/')}"] = path.read_bytes()

    readme = (
        "# Tezos Contract Lifetime Safety CI - hosted evidence\n\n"
        f"Source commit: `{summary['source_commit']}`\n\n"
        f"Workflow: `{summary['workflow_ref']}`\n\n"
        f"Run ID / attempt: `{summary['workflow_run_id']}` / `{summary['workflow_run_attempt']}`\n\n"
        "Three clean Ubuntu hosted runners reproduced two fresh 4,096-transition bounded-state chains and the pinned growth-v2 controls. "
        "The bundle contains per-runner metadata, trace, manifests, reports, and acceptance JSON.\n\n"
        "This is repeatability evidence for named fixtures under the pinned Octez mockup. It is not an independent-user review, user-demand or payment-intent evidence, a mainnet incident, grant acceptance, or revenue.\n"
    ).encode("utf-8")
    members["README.md"] = readme
    checksum_lines = [
        f"{hashlib.sha256(content).hexdigest()}  {name}"
        for name, content in sorted(members.items())
    ]
    members["SHA256SUMS.txt"] = ("\n".join(checksum_lines) + "\n").encode("ascii")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, content in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a checksummed immutable hosted evidence archive")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    digest = package(args.source, args.summary, args.output)
    print(json.dumps({"archive": str(args.output), "sha256": digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
