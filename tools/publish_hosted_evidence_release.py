from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _request(url: str, token: str, *, method: str, body: bytes | None, content_type: str) -> tuple[dict, dict[str, str]]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": content_type,
            "User-Agent": "tezos-lifetime-safety-evidence-publisher",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data, {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc


def publish(archive: Path, *, token: str, repository: str, commit: str, run_id: str, attempt: int) -> dict:
    if not token:
        raise ValueError("GITHUB_TOKEN is required")
    if not archive.is_file() or archive.stat().st_size == 0:
        raise FileNotFoundError(f"evidence archive missing: {archive}")
    tag = f"technical-mvp-go-{commit[:12]}-run-{run_id}-a{attempt}"
    api_root = f"https://api.github.com/repos/{repository}"
    notes = (
        "Verified three-clean-runner technical evidence bundle for this source commit. "
        "It covers named fixtures under the pinned Octez mockup and does not establish user demand, payment intent, grant acceptance, or revenue."
    )
    release, _ = _request(
        f"{api_root}/releases",
        token,
        method="POST",
        body=json.dumps({
            "tag_name": tag,
            "target_commitish": commit,
            "name": f"Hosted technical evidence {commit[:12]}",
            "body": notes,
            "draft": False,
            "prerelease": True,
            "make_latest": "false",
        }).encode("utf-8"),
        content_type="application/json",
    )
    upload_url = release.get("upload_url", "").split("{", 1)[0]
    release_id = release.get("id")
    if not upload_url or not isinstance(release_id, int):
        raise RuntimeError("GitHub did not return a release upload URL and id")
    query = urllib.parse.urlencode({"name": archive.name})
    asset, _ = _request(
        f"{upload_url}?{query}",
        token,
        method="POST",
        body=archive.read_bytes(),
        content_type="application/gzip",
    )
    return {
        "release_id": release_id,
        "release_tag": tag,
        "release_url": release.get("html_url"),
        "asset_url": asset.get("browser_download_url"),
        "asset_name": asset.get("name"),
        "asset_size": asset.get("size"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a verified evidence archive as a GitHub prerelease asset")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = publish(
        args.archive,
        token=os.environ.get("GITHUB_TOKEN", ""),
        repository=os.environ["GITHUB_REPOSITORY"],
        commit=os.environ["GITHUB_SHA"],
        run_id=os.environ["GITHUB_RUN_ID"],
        attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
