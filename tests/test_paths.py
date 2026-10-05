"""Standalone settings and launch-path regressions; no Crawl checkout required."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dcss_harness import cli
from dcss_harness import client, daemon, worker
from dcss_harness.paths import binary_path, configured_paths, read_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / "settings.ini"
        self.config.write_text("[crawl]\nbinary = game with spaces/crawl%test\nsource = assets\n")
        clean_env = patch.dict(os.environ, {}, clear=True)
        clean_env.start()
        self.addCleanup(clean_env.stop)

    def args(self, **kw):
        return argparse.Namespace(config=self.config, binary=None, crawl_source=None, **kw)

    def test_relative_paths_and_literal_percent_resolve_from_config(self):
        paths = read_settings(self.config)
        self.assertEqual(paths["binary"], self.root / "game with spaces/crawl%test")
        self.assertEqual(paths["source"], self.root / "assets")

    def test_precedence_flags_then_environment_then_config(self):
        args = self.args()
        self.assertEqual(configured_paths(args), (
            self.root / "game with spaces/crawl%test", self.root / "assets"))
        with patch.dict(os.environ, {"CRAWL_BINARY": "/env/crawl", "CRAWL_SOURCE": "/env/assets"}):
            self.assertEqual(configured_paths(args), ("/env/crawl", "/env/assets"))
            args.binary, args.crawl_source = Path("/cli/crawl"), Path("/cli/assets")
            self.assertEqual(configured_paths(args), (Path("/cli/crawl"), Path("/cli/assets")))

    def test_default_file_optional_but_selected_file_required(self):
        with patch("dcss_harness.paths.ROOT", self.root / "absent"):
            self.assertEqual(read_settings(), {})
        with self.assertRaisesRegex(ValueError, "Cannot read settings"):
            read_settings(self.root / "missing.ini")

    def test_config_environment_and_explicit_override(self):
        with patch.dict(os.environ, {"CRAWL_AGENT_CONFIG": str(self.config)}):
            self.assertEqual(read_settings(), read_settings(self.config))
            other = self.root / "other.ini"
            other.write_text("[crawl]\nbinary = other\n")
            self.assertEqual(read_settings(other)["binary"], self.root / "other")

    def test_invalid_and_unknown_settings_report_filename(self):
        for value in ("not ini", "[crawl]\nbinray = wrong\n", "[crawll]\nbinary = wrong\n",
                      "[output]\nsnapshot_tags = sometimes\n", "[output]\nsnapshot_tags =\n",
                      "[output]\nsnapshot_tag = true\n"):
            with self.subTest(value=value):
                self.config.write_text(value)
                with self.assertRaisesRegex(ValueError, str(self.config)):
                    read_settings(self.config)

    def test_output_settings_use_standard_ini_booleans(self):
        for value, expected in (("true", True), ("YES", True), ("on", True), ("1", True),
                                ("false", False), ("NO", False), ("off", False), ("0", False)):
            with self.subTest(value=value):
                self.config.write_text(f"[output]\nsnapshot_tags = {value}\n")
                self.assertIs(read_settings(self.config)["snapshot_tags"], expected)

    def test_empty_source_is_optional(self):
        self.config.write_text("[crawl]\nbinary = game\nsource =\n")
        self.assertNotIn("source", read_settings(self.config))

    def test_executable_binary_needs_no_source(self):
        binary = self.root / "crawl"
        binary.write_text("#!/bin/sh\nexit 0\n")
        with self.assertRaisesRegex(ValueError, "not executable"):
            binary_path(binary)
        binary.chmod(0o755)
        self.assertEqual(binary_path(binary), binary)
        self.assertEqual(binary_path(binary, self.root / "missing"), binary)
        self.assertEqual(binary_path(source=self.root), binary)

    def test_missing_binary_explains_existing_binary_setting(self):
        with self.assertRaisesRegex(ValueError, "settings.ini.*existing WebTiles"):
            binary_path()

    def test_cli_resolves_settings_before_foreground_launch(self):
        binary = self.root / "fake-crawl"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        self.config.write_text("[crawl]\nbinary = fake-crawl\n[output]\nsnapshot_tags = true\n")
        argv = ["crawl-agent", "--config", str(self.config), "--session-dir",
                str(self.root / "session"), "start", "--foreground", "--name", "astra-high"]
        with patch.object(sys, "argv", argv), patch.object(cli, "serve") as serve:
            self.assertEqual(cli.main(), 0)
        self.assertEqual(serve.call_args.args[1]["binary"], str(binary))
        self.assertTrue(serve.call_args.kwargs["output_args"].snapshot_tags)

    def test_internal_worker_uses_launch_configuration_without_rereading_settings(self):
        config = {"binary": "/external/crawl", "name": "test-player"}
        with patch.object(daemon, "serve") as serve, \
                patch.object(cli, "read_settings", side_effect=AssertionError("unexpected settings read")):
            self.assertEqual(worker.main(["--session-dir", str(self.root), json.dumps(config)]), 0)
        self.assertEqual(serve.call_args.args[1], config)

    def test_detached_launch_preserves_resolved_paths_and_output_setting(self):
        binary = self.root / "fake-crawl"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        self.config.write_text("[crawl]\nbinary = fake-crawl\n[output]\nsnapshot_tags = true\n")
        session = self.root / "session"
        session.mkdir()
        argv = ["crawl-agent", "--config", str(self.config), "--session-dir",
                str(session), "start", "--name", "test-player"]
        with patch.object(sys, "argv", argv), patch.object(cli.subprocess, "Popen") as process, \
                patch.object(cli, "output_request") as output:
            process.return_value.poll.return_value = None
            process.return_value.pid = 456
            for status in (0, 1):
                with self.subTest(status=status):
                    (session / "runtime.json").unlink(missing_ok=True)
                    output.reset_mock()
                    output.return_value = status
                    reports = iter(('{', '{"pid":123}', '{"pid":456}'))

                    def publish_runtime(_):
                        output.assert_not_called()
                        (session / "runtime.json").write_text(next(reports))

                    with patch.object(cli.time, "sleep", side_effect=publish_runtime) as wait:
                        self.assertEqual(cli.main(), status)
                    self.assertEqual(wait.call_count, 3)
                    output.assert_called_once()  # Ambiguous request failure is not retried.
        self.assertTrue(output.call_args.args[2].snapshot_tags)
        worker_command = process.call_args.args[0]
        self.assertEqual(worker_command[1:3], ["-m", "dcss_harness.worker"])
        self.assertEqual(process.call_args.kwargs["cwd"], Path(cli.__file__).resolve().parents[1])
        with patch.object(daemon, "serve") as serve:
            self.assertEqual(worker.main(worker_command[3:]), 0)
        self.assertEqual(serve.call_args.args[1]["binary"], str(binary))
        self.assertEqual(serve.call_args.args[1]["name"], "test-player")

    def test_detached_worker_exit_or_timeout_does_not_send_a_request(self):
        binary = self.root / "fake-crawl"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        argv = ["crawl-agent", "--session-dir", str(self.root / "session"),
                "start", "--binary", str(binary), "--name", "test-player"]
        for exited in (True, False):
            with self.subTest(exited=exited), patch.object(sys, "argv", argv), \
                    patch.object(cli.subprocess, "Popen") as process, \
                    patch.object(cli, "output_request") as request, \
                    patch.object(cli.time, "monotonic", side_effect=(0, 66)), \
                    redirect_stdout(io.StringIO()) as output:
                process.return_value.poll.return_value = 1 if exited else None
                self.assertEqual(cli.main(), 1)
                request.assert_not_called()
                self.assertIn("Startup failed" if exited else "startup timed out", output.getvalue())

    def test_cli_output_setting_for_json_and_text(self):
        snapshot = {"player": {"name": "test", "turn": 1}, "inventory": [],
                    "map": {"origin": [0, 0], "rows": ["@"]}, "messages": [],
                    "input_mode": "command", "settled": True, "running": True}
        session = self.root / "session"
        session.mkdir()
        (session / "runtime.json").write_text("{}")
        cases = (("", False), ("false", False), ("true", True))
        for readable in (False, True):
            for index, (setting, tagged) in enumerate(cases):
                with self.subTest(readable=readable, setting=setting):
                    self.config.write_text(f"[output]\nsnapshot_tags = {setting}\n" if setting else "")
                    argv = ["crawl-agent", "--config", str(self.config), "--session-dir", str(session),
                            "--stream", f"case-{readable}-{index}",
                            *(["--text"] if readable else []), "observe"]
                    output = io.StringIO()
                    with patch.object(sys, "argv", argv), \
                            patch.object(client, "request", return_value=snapshot), redirect_stdout(output):
                        self.assertEqual(cli.main(), 0)
                    rendered = output.getvalue()
                    for key in ("crawl/map", "crawl/inventory"):
                        self.assertEqual(f'<codex_snapshot key="{key}">' in rendered, tagged)
                    if not readable:
                        ordinary = json.loads(rendered.splitlines()[0])
                        self.assertEqual("inventory" in ordinary, not tagged)
                        self.assertEqual("map" in ordinary, not tagged)
                    else:
                        self.assertIn("HP", rendered)

    def test_snapshot_output_cannot_be_overridden_by_cli_flags(self):
        for flag in ("--snapshot-tags", "--no-snapshot-tags"):
            with self.subTest(flag=flag):
                error = io.StringIO()
                with patch.object(sys, "argv", ["crawl-agent", flag, "observe"]), \
                        patch.object(client, "request") as request, redirect_stderr(error), \
                        self.assertRaises(SystemExit) as exit:
                    cli.main()
                self.assertEqual(exit.exception.code, 2)
                self.assertIn(f"unrecognized arguments: {flag}", error.getvalue())
                request.assert_not_called()

    def test_editing_settings_switches_existing_stream_without_replaying_messages(self):
        session = self.root / "session"
        session.mkdir()
        (session / "runtime.json").write_text("{}")
        snapshot = {"player": {"name": "test", "turn": 1}, "inventory": [],
                    "map": {"origin": [0, 0], "rows": ["@"]}, "messages": [{"text": "hello"}],
                    "input_mode": "command", "settled": True, "running": True}
        argv = ["crawl-agent", "--config", str(self.config), "--session-dir", str(session), "observe"]
        for index, setting in enumerate(("true", "false", "true")):
            self.config.write_text(f"[output]\nsnapshot_tags = {setting}\n")
            output = io.StringIO()
            with patch.object(sys, "argv", argv), \
                    patch.object(client, "request", return_value=snapshot), redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            rendered = output.getvalue()
            ordinary = json.loads(rendered.splitlines()[0])
            self.assertEqual(ordinary["messages"], snapshot["messages"] if index == 0 else [])
            if setting == "true":
                self.assertIn('<codex_snapshot key="crawl/map">', rendered)
                self.assertIn('<codex_snapshot key="crawl/inventory">', rendered)
            else:
                self.assertEqual(ordinary["map"], snapshot["map"])
                self.assertEqual(ordinary["inventory"], snapshot["inventory"])

    def test_bad_output_setting_rejects_action_without_game_input(self):
        self.config.write_text("[output]\nsnapshot_tags = sometimes\n")
        argv = ["crawl-agent", "--config", str(self.config), "act", "--move", "n"]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), patch.object(client, "request") as request, \
                redirect_stdout(output):
            self.assertEqual(cli.main(), 1)
        request.assert_not_called()
        self.assertIn(str(self.config), json.loads(output.getvalue())["error"])

    def test_stop_does_not_depend_on_valid_output_settings(self):
        self.config.write_text("[output]\nsnapshot_tags = sometimes\n")
        argv = ["crawl-agent", "--config", str(self.config), "stop"]
        with patch.object(sys, "argv", argv), patch.object(client, "request", return_value={}) as request, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(request.call_args.args[1], {"op": "stop"})

    def test_cli_settings_work_from_an_unrelated_directory(self):
        # Validation stops before launching the nonexistent executable.
        result = subprocess.run(
            [sys.executable, str(Path(cli.__file__).resolve().parents[1] / "run.py"), "--config", str(self.config), "start"],
            cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.root / "game with spaces/crawl%test"), result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
