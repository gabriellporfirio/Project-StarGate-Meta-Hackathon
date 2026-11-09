"""Offline tests for the stargate build gate (M4 sections 1, 5a, 8).

The gate runs before any build step: a stub build function records its
calls and must never be called on a gate failure.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from stargate import cli, plan
from stargate.build import gate

REPO = Path(__file__).resolve().parent.parent


def _research(name):
    return str(REPO / "fixtures" / "research" / ("%s.json" % name))


def _write_temp(payload):
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8")
    with tmp:
        json.dump(payload, tmp)
    return tmp.name


def _real_plan(services, sim="flagship", catalog_path=None,
              profile=None):
    """A real plan-v1 dict built by M3's code (no explanation)."""
    params = {"services_text": services, "json_only": True}
    if profile is not None:
        params["profile_path"] = profile
    else:
        params["sim"] = sim
        params["research_path"] = _research(sim)
    if catalog_path is not None:
        params["catalog_path"] = catalog_path
    code, stdout, _err = plan.run_plan(**params)
    assert code in (0, 7), stdout
    report = json.loads(stdout)
    assert plan.validate_plan(report) == []
    return report


def _tiny_catalog_path():
    """A catalog with one small test model that fits the flagship."""
    catalog = {
        "schema_version": "catalog-v1",
        "models": [{
            "id": "tiny-test",
            "source": "Example/tiny-test",
            "revision": "rev1",
            "files": [{"name": "tiny.pte", "bytes": 4096}],
            "format": "pte",
            "runtime": "test runtime",
            "abi": "arm64-v8a",
            "licence": "apache-2.0",
            "estimates": ["runtime", "abi"],
            "provenance": {"kind": "synthetic",
                           "note": "SIMULATED test entry"},
        }],
    }
    return _write_temp(catalog)


def _manifest_for(model, **overrides):
    """An Assistant manifest matching a handoff model, plus extras."""
    manifest = {
        "model_id": model["id"],
        "source_revision": model["revision"],
        "files": [{
            "name": model["files"][0]["name"],
            "bytes": model["files"][0]["bytes"],
            "sha256": hashlib.sha256(b"tiny").hexdigest(),
        }],
        "runtime_version": "test runtime 1.0",
        "assistant_apk": {"package": "com.stargate.assistant",
                          "version": "1.0"},
    }
    manifest.update(overrides)
    return manifest


def _stub(result=(0, "built\n", "")):
    def build(plan_dict, out_dir, labels, ctx):
        build.calls.append((plan_dict, out_dir, labels, ctx))
        return result

    build.calls = []
    return build


class GateExitCodes(unittest.TestCase):
    def test_missing_plan_file_is_6(self):
        stub = _stub()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            code, _out, err = gate.run_build(
                "/nonexistent-plan.json", out, build_fn=stub)
        self.assertEqual(code, 6)
        self.assertIn("cannot read plan", err)
        self.assertEqual(stub.calls, [])
        self.assertFalse(os.path.exists(out))

    def test_malformed_plan_is_6(self):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False)
        try:
            tmp.write("{oops")
            tmp.close()
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmpdir:
                out = os.path.join(tmpdir, "out")
                code, _out, err = gate.run_build(
                    tmp.name, out, build_fn=stub)
        finally:
            os.unlink(tmp.name)
        self.assertEqual(code, 6)
        self.assertIn("not valid JSON", err)
        self.assertEqual(stub.calls, [])

    def test_invalid_plan_is_6(self):
        report = _real_plan("files")
        del report["handoff"]
        path = _write_temp(report)
        try:
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmp:
                code, _out, err = gate.run_build(
                    path, os.path.join(tmp, "out"), build_fn=stub)
        finally:
            os.unlink(path)
        self.assertEqual(code, 6)
        self.assertIn("invalid plan", err)
        self.assertEqual(stub.calls, [])

    def test_handoff_selection_mismatch_is_6(self):
        report = _real_plan("files")
        bad = copy.deepcopy(report)
        bad["handoff"]["selection_result"] = "refused"
        path = _write_temp(bad)
        try:
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmp:
                code, _out, _err = gate.run_build(
                    path, os.path.join(tmp, "out"), build_fn=stub)
        finally:
            os.unlink(path)
        self.assertEqual(code, 6)
        self.assertEqual(stub.calls, [])

    def test_handoff_reasons_mismatch_is_6(self):
        report = _real_plan("files")
        bad = copy.deepcopy(report)
        bad["handoff"]["reasons"] = ["forged"]
        path = _write_temp(bad)
        try:
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmp:
                code, _out, err = gate.run_build(
                    path, os.path.join(tmp, "out"), build_fn=stub)
        finally:
            os.unlink(path)
        self.assertEqual(code, 6)
        self.assertIn("reasons", err)
        self.assertEqual(stub.calls, [])

    def test_refused_plan_is_7_with_reasons(self):
        report = _real_plan("files,assistant", sim="relic")
        self.assertEqual(report["selection_result"], "refused")
        path = _write_temp(report)
        try:
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out")
                code, _out, err = gate.run_build(
                    path, out, build_fn=stub)
        finally:
            os.unlink(path)
        self.assertEqual(code, 7)
        for reason in report["handoff"]["reasons"]:
            self.assertIn(reason, err)
        self.assertEqual(stub.calls, [])
        self.assertFalse(os.path.exists(out))

    def test_unresolved_assistant_model_is_9(self):
        unknown_catalog = {
            "schema_version": "catalog-v1",
            "models": [{
                "id": "mystery", "source": None, "revision": None,
                "files": [{"name": "w", "bytes": None}],
                "format": None, "runtime": None, "abi": None,
                "licence": None, "estimates": [],
                "provenance": {"kind": "synthetic", "note": "SIMULATED"},
            }],
        }
        catalog_path = _write_temp(unknown_catalog)
        try:
            report = _real_plan("files,assistant",
                                catalog_path=catalog_path)
        finally:
            os.unlink(catalog_path)
        self.assertIsNone(report["handoff"]["model"])
        path = _write_temp(report)
        try:
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out")
                code, _out, err = gate.run_build(
                    path, out, build_fn=stub)
        finally:
            os.unlink(path)
        self.assertEqual(code, 9)
        self.assertIn("concrete catalog entry", err)
        self.assertIn("fresh M3 plan", err)
        self.assertEqual(stub.calls, [])
        self.assertFalse(os.path.exists(out))

    def test_files_only_passes_gate_to_build(self):
        report = _real_plan("files")
        path = _write_temp(report)
        try:
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out")
                code, stdout, _err = gate.run_build(
                    path, out, build_fn=stub)
            with open(path, "rb") as handle:
                want_sha = hashlib.sha256(handle.read()).hexdigest()
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "built\n")
        self.assertEqual(len(stub.calls), 1)
        plan_dict, out_dir, labels, ctx = stub.calls[0]
        self.assertEqual(plan_dict["handoff"]["services"], ["files"])
        self.assertEqual(out_dir, out)
        self.assertEqual(labels, [])
        self.assertEqual(ctx["plan_sha256"], want_sha)
        self.assertEqual(ctx["plan_path"], path)
        self.assertIsNone(ctx["assistant_manifest"])
        self.assertIsNone(ctx["assistant_manifest_sha256"])

    def test_files_only_ignores_manifest(self):
        report = _real_plan("files")
        path = _write_temp(report)
        try:
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmp:
                code, _out, _err = gate.run_build(
                    path, os.path.join(tmp, "out"),
                    assistant_manifest_path="/nonexistent-manifest.json",
                    build_fn=stub)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        self.assertEqual(len(stub.calls), 1)

    def test_unconfirmed_files_only_carries_label(self):
        from stargate import scan

        profile = scan.build_profile("flagship")
        profile["facts"]["resources.ram_total"] = {
            "status": "unknown", "value": None, "unit": "bytes",
            "unknown_reason": "permission_denied"}
        profile_path = _write_temp(profile)
        try:
            report = _real_plan("files", profile=profile_path)
        finally:
            os.unlink(profile_path)
        self.assertEqual(report["selection_result"], "unconfirmed")
        self.assertEqual(report["build_allowed"], "emulator_only")
        path = _write_temp(report)
        try:
            stub = _stub()
            with tempfile.TemporaryDirectory() as tmp:
                code, _out, _err = gate.run_build(
                    path, os.path.join(tmp, "out"), build_fn=stub)
        finally:
            os.unlink(path)
        self.assertEqual(code, 0)
        self.assertEqual(len(stub.calls), 1)
        _plan_dict, _out_dir, labels, _ctx = stub.calls[0]
        self.assertEqual(labels, [gate.UNCONFIRMED_LABEL])
        self.assertEqual(gate.UNCONFIRMED_LABEL,
                         "emulator experiment: fit not confirmed")

    def test_default_build_is_route_a(self):
        # No build_fn: the default Route A builder runs. With a
        # bogus stock path it fails fast with 8 and writes nothing.
        report = _real_plan("files")
        path = _write_temp(report)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out")
                code, _out, err = gate.run_build(
                    path, out,
                    inputs={"stock_path": "/nonexistent-stock.img"})
        finally:
            os.unlink(path)
        self.assertEqual(code, 8)
        self.assertIn("stock image is missing", err)
        self.assertFalse(os.path.exists(out))


class ManifestBinding(unittest.TestCase):
    def _assistant_plan(self):
        catalog_path = _tiny_catalog_path()
        self.addCleanup(os.unlink, catalog_path)
        report = _real_plan("files,assistant",
                            catalog_path=catalog_path)
        self.assertEqual(report["selection_result"], "fits")
        self.assertEqual(report["handoff"]["model"]["id"], "tiny-test")
        return report

    def _run(self, report, manifest):
        plan_path = _write_temp(report)
        self.addCleanup(os.unlink, plan_path)
        manifest_path = _write_temp(manifest)
        self.addCleanup(os.unlink, manifest_path)
        stub = _stub()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = os.path.join(tmp.name, "out")
        code, _out, err = gate.run_build(
            plan_path, out, assistant_manifest_path=manifest_path,
            build_fn=stub)
        return code, err, stub

    def test_matching_manifest_passes(self):
        report = self._assistant_plan()
        manifest = _manifest_for(report["handoff"]["model"])
        code, _err, stub = self._run(report, manifest)
        self.assertEqual(code, 0)
        self.assertEqual(len(stub.calls), 1)
        ctx = stub.calls[0][3]
        self.assertEqual(ctx["assistant_manifest"], manifest)
        want_sha = hashlib.sha256(
            json.dumps(manifest).encode("utf-8")).hexdigest()
        self.assertEqual(ctx["assistant_manifest_sha256"], want_sha)

    def test_wrong_model_id_is_9(self):
        report = self._assistant_plan()
        manifest = _manifest_for(report["handoff"]["model"],
                                 model_id="other-model")
        code, err, stub = self._run(report, manifest)
        self.assertEqual(code, 9)
        self.assertIn("model_id", err)
        self.assertEqual(stub.calls, [])

    def test_wrong_revision_is_9(self):
        report = self._assistant_plan()
        manifest = _manifest_for(report["handoff"]["model"],
                                 source_revision="rev2")
        code, err, stub = self._run(report, manifest)
        self.assertEqual(code, 9)
        self.assertIn("source_revision", err)
        self.assertEqual(stub.calls, [])

    def test_wrong_file_name_is_9(self):
        report = self._assistant_plan()
        model = copy.deepcopy(report["handoff"]["model"])
        manifest = _manifest_for(model)
        manifest["files"][0]["name"] = "other.pte"
        mismatches = gate.bind_assistant_manifest(
            report["handoff"]["model"], manifest)
        self.assertTrue(any("other.pte" in item for item in mismatches))
        self.assertTrue(any("tiny.pte" in item for item in mismatches))
        code, _err, stub = self._run(report, manifest)
        self.assertEqual(code, 9)
        self.assertEqual(stub.calls, [])

    def test_wrong_file_size_is_9(self):
        report = self._assistant_plan()
        model = copy.deepcopy(report["handoff"]["model"])
        manifest = _manifest_for(model)
        manifest["files"][0]["bytes"] = 4097
        code, err, stub = self._run(report, manifest)
        self.assertEqual(code, 9)
        self.assertIn("bytes", err)
        self.assertEqual(stub.calls, [])

    def test_missing_file_hash_is_9(self):
        report = self._assistant_plan()
        model = copy.deepcopy(report["handoff"]["model"])
        manifest = _manifest_for(model)
        del manifest["files"][0]["sha256"]
        code, err, stub = self._run(report, manifest)
        self.assertEqual(code, 9)
        self.assertIn("sha256", err)
        self.assertEqual(stub.calls, [])

    def test_missing_manifest_file_is_9(self):
        report = self._assistant_plan()
        plan_path = _write_temp(report)
        self.addCleanup(os.unlink, plan_path)
        stub = _stub()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        code, _out, err = gate.run_build(
            plan_path, os.path.join(tmp.name, "out"),
            assistant_manifest_path="/nonexistent-manifest.json",
            build_fn=stub)
        self.assertEqual(code, 9)
        self.assertIn("cannot read assistant manifest", err)
        self.assertEqual(stub.calls, [])

    def test_no_manifest_flag_before_handover_is_9(self):
        # Without --assistant-manifest the build reads the committed
        # handover file, which M4b has not delivered in Part 1.
        report = self._assistant_plan()
        plan_path = _write_temp(report)
        self.addCleanup(os.unlink, plan_path)
        if gate.default_manifest_path().exists():
            self.skipTest("handover manifest already committed")
        stub = _stub()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        code, _out, err = gate.run_build(
            plan_path, os.path.join(tmp.name, "out"), build_fn=stub)
        self.assertEqual(code, 9)
        self.assertEqual(stub.calls, [])


class BuildCli(unittest.TestCase):
    def test_cli_refused_plan_is_7(self):
        bad = _real_plan("files,assistant", sim="relic")
        path = _write_temp(bad)
        self.addCleanup(os.unlink, path)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["build", "--plan", path,
                             "--out", os.path.join(tmp.name, "out")])
        self.assertEqual(code, 7)

    def test_cli_needs_plan_and_out(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["build", "--plan", "/tmp/x.json"])
        self.assertEqual(code, 6)

    def test_cli_valid_plan_attempts_route_a(self):
        # An assistant plan from the tiny test catalog can never
        # match ambient APKs baked for real plans, so the default
        # Route A attempt always fails fast (missing inputs or APK
        # config mismatch) with 8, never starts image work, and
        # creates no out dir.
        catalog_path = _tiny_catalog_path()
        self.addCleanup(os.unlink, catalog_path)
        report = _real_plan("files,assistant",
                            catalog_path=catalog_path)
        manifest = _manifest_for(report["handoff"]["model"])
        plan_path = _write_temp(report)
        self.addCleanup(os.unlink, plan_path)
        manifest_path = _write_temp(manifest)
        self.addCleanup(os.unlink, manifest_path)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = os.path.join(tmp.name, "out")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["build", "--plan", plan_path,
                             "--out", out,
                             "--assistant-manifest", manifest_path])
        self.assertEqual(code, 8)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
