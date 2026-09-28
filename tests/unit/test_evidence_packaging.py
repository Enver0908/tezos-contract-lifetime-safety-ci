from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.package_hosted_evidence import FILES, RUNNERS, package
from tools.publish_hosted_evidence_release import publish
from tools.write_hosted_metadata import main as write_metadata_main


class EvidencePackagingTests(unittest.TestCase):
    def test_package_contains_whitelisted_evidence_and_valid_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            summary = root / "summary.json"
            archive = root / "public/evidence.tar.gz"
            summary.write_text(json.dumps({
                "hosted_technical_go": "go",
                "source_commit": "a" * 40,
                "workflow_ref": "owner/repo/workflow.yml@refs/heads/main",
                "workflow_run_id": "123",
                "workflow_run_attempt": 1,
            }), encoding="utf-8")
            for runner in RUNNERS:
                for relative in FILES:
                    path = source / f"bounded-sequence-v1-{runner}" / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"{runner}:{relative}\n", encoding="utf-8")
            digest = package(source, summary, archive)
            self.assertEqual(digest, hashlib.sha256(archive.read_bytes()).hexdigest())
            with tarfile.open(archive, "r:gz") as tar:
                members = {item.name: tar.extractfile(item).read() for item in tar.getmembers() if item.isfile()}
            sums = members["SHA256SUMS.txt"].decode("ascii").splitlines()
            for line in sums:
                expected_hash, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256(members[name]).hexdigest(), expected_hash)
            self.assertIn("three/technical-mvp-go-v3.json", members)
            self.assertNotIn("unlisted-user-data.txt", members)
            with self.assertRaises(FileExistsError):
                package(source, summary, archive)

    def test_package_refuses_non_go_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            summary = root / "summary.json"
            summary.write_text(json.dumps({"hosted_technical_go": "no-go"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only a verified hosted GO"):
                package(source, summary, root / "bundle.tar.gz")

    def test_metadata_writer_records_run_identity_and_is_create_only(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "metadata.json"
            args = [
                "--output", str(output), "--runner-id", "two", "--commit", "a" * 40,
                "--run-id", "123", "--run-attempt", "2", "--workflow-ref", "owner/repo/w.yml@refs/heads/main",
            ]
            with patch.dict("os.environ", {"GITHUB_EVENT_NAME": "workflow_dispatch"}):
                self.assertEqual(write_metadata_main(args), 0)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["runner_id"], "two")
            self.assertEqual(data["workflow_run_attempt"], 2)
            with self.assertRaises(FileExistsError):
                write_metadata_main(args)

    def test_release_publisher_creates_release_and_uploads_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "evidence.tar.gz"
            archive.write_bytes(b"verified-bundle")
            responses = [
                ({"id": 44, "upload_url": "https://uploads.github.com/repos/o/r/releases/44/assets{?name,label}", "html_url": "https://github.com/o/r/releases/tag/evidence-tag"}, {}),
                ({"browser_download_url": "https://github.com/o/r/releases/download/evidence-tag/evidence.tar.gz", "name": "evidence.tar.gz", "size": len(b"verified-bundle")}, {}),
            ]
            with patch("tools.publish_hosted_evidence_release._request", side_effect=responses) as request:
                result = publish(archive, token="token", repository="o/r", commit="a" * 40, run_id="123", attempt=1)
            self.assertEqual(result["release_id"], 44)
            self.assertTrue(result["asset_url"].endswith("evidence.tar.gz"))
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args_list[1].kwargs["body"], b"verified-bundle")

    def test_release_publisher_rejects_missing_token(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "evidence.tar.gz"
            archive.write_bytes(b"bundle")
            with self.assertRaisesRegex(ValueError, "GITHUB_TOKEN"):
                publish(archive, token="", repository="o/r", commit="a" * 40, run_id="123", attempt=1)


if __name__ == "__main__":
    unittest.main()
