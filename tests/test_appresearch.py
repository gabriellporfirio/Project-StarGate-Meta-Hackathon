"""Research honesty at the platform layer (M6 Part 1, spec 1 + 6).

No M2 replay has been recorded yet, so Part 1 never binds or shows one;
these tests prove the replay path needs no network, a live step without
a key shows M0's error (not a crash), and no recorded trace exists in
the worktree. Light only: loopback, temp dirs, no live calls.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import unittest
from unittest import mock

from stargate import meta as meta_module
from stargate.research import TRACE_VERSION, run_research

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _no_network(*args, **kwargs):
    raise AssertionError("network used")


class ReplayOfflineTests(unittest.TestCase):
    def test_replay_needs_no_network(self):
        with open(os.path.join(
                REPO, "fixtures", "research", "flagship.json"),
                encoding="utf-8") as handle:
            report = json.load(handle)
        tmp = tempfile.mkdtemp(prefix="appreplay-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        trace_path = os.path.join(tmp, "trace.json")
        with open(trace_path, "w", encoding="utf-8") as handle:
            json.dump({"trace_version": TRACE_VERSION,
                       "report": report}, handle)
        with mock.patch.object(socket, "socket",
                               side_effect=_no_network), \
                mock.patch.object(socket, "create_connection",
                                  side_effect=_no_network):
            exit_code, stdout_text, stderr_text = run_research(
                replay_path=trace_path)
        self.assertEqual(exit_code, 0, stdout_text[:300])
        self.assertIn("no network", stderr_text)

    def test_no_recorded_replay_in_worktree(self):
        found = []
        for root, dirs, files in os.walk(REPO):
            dirs[:] = [name for name in dirs if name != ".git"]
            for name in files:
                if not name.endswith(".json"):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, encoding="utf-8") as handle:
                        text = handle.read()
                except OSError:
                    continue
                if '"trace_version": "%s"' % TRACE_VERSION in text or \
                        "'trace_version'" in text and TRACE_VERSION in text:
                    found.append(os.path.relpath(path, REPO))
        self.assertEqual(found, [])


class LiveWithoutKeyTests(unittest.TestCase):
    def test_missing_key_shows_m0_error_not_crash(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(meta_module.CREDENTIAL_ENV_VAR, None)
            with mock.patch.object(
                    socket, "socket",
                    side_effect=_no_network), \
                    mock.patch.object(
                        socket, "create_connection",
                        side_effect=_no_network):
                exit_code, _stdout, stderr_text = run_research(
                    sim="flagship")
        self.assertNotEqual(exit_code, 0)
        # M0's vocabulary (mirrors stargate doctor): the env var name,
        # never the value.
        self.assertIn("env %s is unset or empty"
                      % meta_module.CREDENTIAL_ENV_VAR, stderr_text)


if __name__ == "__main__":
    unittest.main()
