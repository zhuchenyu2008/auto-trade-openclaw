from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tg_okx_auto_trade.fixture_suite import (
    SEED_HTML_FIXTURES,
    SEED_MESSAGE_FIXTURES,
    SEED_SCENARIO_FIXTURES,
    run_fixture_suite,
    write_seed_fixture_corpus,
)


class FixtureSuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.fixture_root = Path(self.tempdir.name) / "tests" / "fixtures" / "public_web"
        self.manifest = write_seed_fixture_corpus(self.fixture_root)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_seed_generator_writes_manifest_and_expected_counts(self) -> None:
        manifest_path = self.fixture_root / "manifests" / "corpus-index.json"
        self.assertTrue(manifest_path.is_file())
        self.assertEqual(self.manifest["counts"]["messages"], len(SEED_MESSAGE_FIXTURES))
        self.assertEqual(self.manifest["counts"]["scenarios"], len(SEED_SCENARIO_FIXTURES))
        self.assertEqual(self.manifest["counts"]["html"], len(SEED_HTML_FIXTURES))
        self.assertEqual(self.manifest["counts"]["total_samples"], 123)
        self.assertEqual(
            self.manifest["coverage"]["summary"]["action_coverage"],
            {
                "target": 66,
                "actual": 66,
                "delta": 0,
                "meets_target": True,
            },
        )
        self.assertEqual(
            self.manifest["coverage"]["summary"]["noise_ignore_coverage"],
            {
                "target": 24,
                "actual": 24,
                "delta": 0,
                "meets_target": True,
            },
        )
        self.assertEqual(
            self.manifest["coverage"]["summary"]["edit_version_coverage"],
            {
                "target": 18,
                "actual": 18,
                "delta": 0,
                "meets_target": True,
            },
        )
        self.assertEqual(
            self.manifest["coverage"]["summary"]["replay_reconcile_dedup_coverage"],
            {
                "target": 12,
                "actual": 12,
                "delta": 0,
                "meets_target": True,
            },
        )
        self.assertEqual(
            self.manifest["coverage"]["bucket_allocations"]["action_coverage"]["actual"],
            66,
        )
        self.assertEqual(
            self.manifest["coverage"]["bucket_allocations"]["noise_ignore_coverage"]["actual"],
            24,
        )
        self.assertEqual(
            self.manifest["coverage"]["bucket_allocations"]["edit_version_coverage"]["actual"],
            18,
        )
        self.assertEqual(
            self.manifest["coverage"]["bucket_allocations"]["replay_reconcile_dedup_coverage"]["actual"],
            12,
        )
        stale_message = self.fixture_root / "messages" / "stale.json"
        stale_message.write_text("{}", encoding="utf-8")
        regenerated = write_seed_fixture_corpus(self.fixture_root)
        self.assertFalse(stale_message.exists())
        self.assertEqual(regenerated, self.manifest)
        persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted_manifest, self.manifest)

    def test_run_fixture_suite_passes_messages(self) -> None:
        result = run_fixture_suite(self.fixture_root / "messages")
        self.assertEqual(result["suite_status"], "passed")
        self.assertEqual(result["passed_count"], len(SEED_MESSAGE_FIXTURES))
        self.assertEqual(result["failed_count"], 0)

    def test_run_fixture_suite_passes_scenarios(self) -> None:
        result = run_fixture_suite(self.fixture_root / "scenarios")
        self.assertEqual(result["suite_status"], "passed")
        self.assertEqual(result["passed_count"], len(SEED_SCENARIO_FIXTURES))
        self.assertEqual(result["failed_count"], 0)

    def test_run_fixture_suite_passes_html(self) -> None:
        result = run_fixture_suite(self.fixture_root / "html")
        self.assertEqual(result["suite_status"], "passed")
        self.assertEqual(result["passed_count"], len(SEED_HTML_FIXTURES))
        self.assertEqual(result["failed_count"], 0)

    def test_run_fixture_suite_script_returns_zero_for_messages(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                str(repo_root / "scripts" / "run_fixture_suite.py"),
                "--fixtures",
                str(self.fixture_root / "messages"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["suite_status"], "passed")
        self.assertEqual(payload["fixture_type"], "messages")


if __name__ == "__main__":
    unittest.main()
