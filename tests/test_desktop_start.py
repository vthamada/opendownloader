"""Desktop-start command boundary tests; these do not prove browser behavior."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from lume.desktop_start import request_desktop


class DesktopStartTests(unittest.TestCase):
    def setUp(self):
        self.data = Path.cwd() / "data directory with spaces"

    def assert_detached_stdio(self, kwargs):
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["close_fds"])
        self.assertNotIn("env", kwargs)
        if os.name == "nt":
            flags = kwargs["creationflags"]
            self.assertTrue(flags & subprocess.DETACHED_PROCESS)
            self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)
            self.assertTrue(flags & subprocess.CREATE_BREAKAWAY_FROM_JOB)
            self.assertFalse(flags & subprocess.CREATE_NEW_CONSOLE)
        else:
            self.assertTrue(kwargs["start_new_session"])

    def test_frozen_host_launches_only_fixed_gui_companion_without_wait(self):
        host = Path.cwd() / "runtime folder" / "LumeNativeHost.exe"
        with patch.object(sys, "executable", str(host)), patch.object(sys, "frozen", True, create=True), patch(
            "lume.desktop_start.subprocess.Popen"
        ) as spawn:
            request_desktop(self.data)
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], [
            str(host.with_name("LumeDownloads.exe")),
            "--from-browser", "--data-dir", str(self.data),
        ])
        self.assertEqual(kwargs["cwd"], str(host.parent))
        spawn.return_value.wait.assert_not_called()
        spawn.return_value.communicate.assert_not_called()
        self.assert_detached_stdio(kwargs)

    def test_source_mode_uses_existing_adjacent_pythonw_and_fixed_module(self):
        executable = Path.cwd() / "venv folder" / "python.exe"
        with patch.object(sys, "executable", str(executable)), patch.object(
            sys, "frozen", False, create=True
        ), patch("lume.desktop_start.Path.is_file", return_value=True), patch(
            "lume.desktop_start.subprocess.Popen"
        ) as spawn:
            request_desktop(self.data)
        command = spawn.call_args.args[0]
        expected = executable.with_name("pythonw.exe") if os.name == "nt" else executable
        self.assertEqual(command, [
            str(expected), "-m", "lume", "--from-browser", "--data-dir", str(self.data),
        ])
        self.assertEqual(
            spawn.call_args.kwargs["cwd"],
            str(Path(__file__).resolve().parents[1]),
        )
        self.assert_detached_stdio(spawn.call_args.kwargs)

    def test_missing_pythonw_falls_back_to_current_interpreter(self):
        executable = Path.cwd() / "python.exe"
        with patch.object(sys, "executable", str(executable)), patch.object(
            sys, "frozen", False, create=True
        ), patch("lume.desktop_start.Path.is_file", return_value=False), patch(
            "lume.desktop_start.subprocess.Popen"
        ) as spawn:
            request_desktop(self.data)
        self.assertEqual(spawn.call_args.args[0][0], str(executable))

    def test_relative_data_dir_is_anchored_before_child_cwd_changes(self):
        with patch.object(sys, "frozen", False, create=True), patch(
            "lume.desktop_start.subprocess.Popen"
        ) as spawn:
            request_desktop(Path("chosen data"))
        command = spawn.call_args.args[0]
        self.assertEqual(command[-1], str(Path.cwd() / "chosen data"))
        self.assertNotIn("http", " ".join(command).lower())

    def test_spawn_error_propagates_without_retry_or_file_writes(self):
        failure = OSError("desktop launch rejected")
        with patch.object(sys, "frozen", False, create=True), patch(
            "lume.desktop_start.subprocess.Popen", side_effect=failure
        ) as spawn, patch("builtins.open", side_effect=AssertionError("No file writes")):
            with self.assertRaises(OSError) as caught:
                request_desktop(self.data)
        self.assertIs(caught.exception, failure)
        self.assertEqual(spawn.call_count, 1)


if __name__ == "__main__":
    unittest.main()

