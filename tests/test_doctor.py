"""Binary identification must remain explicit and must not start a session."""

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from dcss_harness import cli
from dcss_harness.doctor import diagnose


class DoctorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.binary = self.root / "crawl"
        self.binary.write_text("#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o755)

    def probe(self, output, returncode=0):
        with patch("dcss_harness.doctor.subprocess.run", return_value=subprocess.CompletedProcess(
                [], returncode, output, "")) as probe:
            result = diagnose(self.binary)
        self.assertEqual(probe.call_args.args[0], [str(self.binary), "-version"])
        self.assertEqual(probe.call_args.kwargs["stdin"], subprocess.DEVNULL)
        return result

    def test_matching_report_requires_version_and_webtiles(self):
        result = self.probe("Crawl version 0.35-a0-1095-g7c31f6e797\nCFLAGS: -O2 -DUSE_TILE_WEB\n")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "matched")

    def test_unknown_or_mismatched_reports_do_not_claim_compatibility(self):
        for output, status in (
            ("Crawl version 0.35-a0-other\nCFLAGS: -DUSE_TILE_WEB\n", "version_mismatch"),
            ("Crawl version 0.35-a0-1095-g7c31f6e797\nCFLAGS: -DUSE_TILE\n", "webtiles_missing"),
            ("Crawl version 0.35-a0-1095-g7c31f6e797\nCFLAGS: -DUSE_TILE_WEB=0\n", "webtiles_missing"),
            ("Crawl version 0.35-a0-1095-g7c31f6e797\n", "unverified"),
            ("Unknown command\n", "unverified"),
        ):
            with self.subTest(output=output):
                result = self.probe(output)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], status)

    def test_failed_probe_stays_unverified(self):
        self.assertEqual(self.probe("", 2)["status"], "probe_failed")
        with patch("dcss_harness.doctor.subprocess.run", side_effect=subprocess.TimeoutExpired([], 5)):
            self.assertEqual(diagnose(self.binary)["status"], "probe_failed")
        self.assertEqual(diagnose(self.root / "missing")["status"], "probe_failed")

    def test_cli_uses_settings_without_creating_a_session_or_contacting_daemon(self):
        # A real disposable executable records the sole argument it receives.
        self.binary.write_text("#!/bin/sh\n[ \"$#\" = 1 ] && [ \"$1\" = -version ] || exit 42\n"
                               "echo 'Crawl version 0.35-a0-1095-g7c31f6e797'\n"
                               "echo 'CFLAGS: -DUSE_TILE_WEB'\n")
        config = self.root / "settings.ini"
        config.write_text("[crawl]\nbinary = crawl\n")
        session = self.root / "no-session"
        output = io.StringIO()
        with patch.object(sys, "argv", ["crawl-agent", "--config", str(config), "--session-dir", str(session), "doctor"]), \
                patch("dcss_harness.cli.serve") as serve, patch("dcss_harness.client.request") as request, \
                redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertFalse(session.exists())
        serve.assert_not_called()
        request.assert_not_called()

    def test_public_help_does_not_expose_worker_or_removed_snapshot_flags(self):
        result = subprocess.run([sys.executable, "-m", "dcss_harness", "--help"],
                                capture_output=True, text=True, check=True)
        self.assertIn("doctor", result.stdout)
        self.assertNotIn("_serve", result.stdout)
        self.assertNotIn("snapshot-tags", result.stdout)


if __name__ == "__main__":
    unittest.main()
