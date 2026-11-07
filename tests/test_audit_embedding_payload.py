"""Synthetic fault injection; no production embeddings or training are needed.

Run: python -m unittest discover -s tests -v
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_embedding_payload.py"
SPEC = importlib.util.spec_from_file_location("embedding_audit", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class PayloadAuditTests(unittest.TestCase):
    def setUp(self):
        # A workspace-local directory also works under restricted Windows temp ACLs.
        self.directory = tempfile.TemporaryDirectory(prefix=".embedding-audit-test-",
                                                    dir=os.environ.get("AUDIT_TEST_TMPDIR", SCRIPT.parents[1]))
        self.root = Path(self.directory.name)
        self.source = self.root / "embedding.npy"
        self.report = self.root / "report.json"
        self.healthy = np.random.default_rng(19).normal(size=(1000, 8)).astype(np.float16)

    def tearDown(self):
        self.directory.cleanup()

    def command(self, *extra):
        return [sys.executable, str(SCRIPT), str(self.source), "--report", str(self.report),
                "--max-zero-row-fraction", "0", *map(str, extra)]

    def run_audit(self, values=None, *extra, expected=None):
        if values is not None:
            np.save(self.source, values)
        process = subprocess.run(self.command(*extra), capture_output=True, text=True)
        if expected is not None:
            self.assertEqual(process.returncode, expected, process.stdout + process.stderr)
        report = None
        if self.report.exists():
            # Python json.loads ordinarily accepts NaN; reject it explicitly here.
            report = json.loads(self.report.read_text(encoding="utf-8"),
                                parse_constant=lambda token: self.fail("nonstandard JSON token: " + token))
        return process, report

    def test_healthy_sampled_reproducible_and_source_unchanged(self):
        np.save(self.source, self.healthy)
        original = hashlib.sha256(self.source.read_bytes()).hexdigest()
        _, first = self.run_audit(None, "--sample-rows", 128, "--seed", 47, expected=0)
        _, second = self.run_audit(None, "--sample-rows", 128, "--seed", 47, expected=0)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "pass")
        self.assertEqual(first["source"]["shape"], [1000, 8])
        self.assertEqual(first["source"]["dtype"], "float16")
        self.assertEqual(first["check_scope"]["checked_rows"], 128)
        self.assertEqual(len(first["strata"]), 16)
        self.assertTrue(all(item["checked_rows"] >= 2 for item in first["strata"]))
        self.assertEqual(original, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(first["source"]["stat_before"], first["source"]["stat_after"])
        _, changed_seed = self.run_audit(None, "--sample-rows", 128, "--seed", 48, expected=0)
        self.assertNotEqual(first["strata"][0]["checked_row_indices_sha256"],
                            changed_seed["strata"][0]["checked_row_indices_sha256"])

    def test_metadata_correct_normal_prefix_and_93_percent_zero_tail(self):
        corrupt = self.healthy.copy()
        corrupt[66:] = 0
        np.save(self.source, self.healthy)
        size = self.source.stat().st_size
        _, report = self.run_audit(corrupt, "--sample-rows", 128, expected=1)
        self.assertEqual(size, self.source.stat().st_size)
        self.assertEqual(report["source"]["shape"], [1000, 8])
        self.assertEqual(report["source"]["dtype"], "float16")
        self.assertEqual(report["summary"]["nonfinite_values"], 0)
        self.assertGreater(report["summary"]["zero_row_fraction"], .85)
        self.assertGreater(report["strata"][-1]["zero_row_fraction"], .99)

    def test_local_missing_block_rejected_in_both_modes(self):
        corrupt = self.healthy.copy()
        corrupt[375:500] = 0
        for mode in ("sampled", "full"):
            with self.subTest(mode=mode):
                _, report = self.run_audit(corrupt, "--mode", mode,
                                           "--sample-rows", 128, "--chunk-rows", 17, expected=1)
                self.assertGreater(report["summary"]["zero_rows"], 0)
                self.assertTrue(any(item["zero_row_fraction"] == 1 for item in report["strata"]))

    def test_zero_fraction_contract_inclusive_threshold(self):
        values = self.healthy[:100].copy()
        values[10:12] = 0
        _, report = self.run_audit(values, "--mode", "full", "--strata", 1,
                                   "--max-zero-row-fraction", ".02", expected=0)
        self.assertEqual(report["summary"]["zero_rows"], 2)
        self.run_audit(values, "--mode", "full", "--strata", 1,
                       "--max-zero-row-fraction", ".019", expected=1)

    def test_local_zero_policy_not_diluted_by_global_average(self):
        values = self.healthy.copy()
        values[750:800] = 0
        _, report = self.run_audit(values, "--mode", "full", "--strata", 10,
                                   "--max-zero-row-fraction", ".1", expected=1)
        self.assertLess(report["summary"]["zero_row_fraction"], .1)
        self.assertTrue(any(error["code"] == "zero_row_fraction_exceeded" for error in report["errors"]))

    def test_nan_and_infinity_always_fail_even_zero_threshold_one(self):
        for value in (np.nan, np.inf, -np.inf):
            with self.subTest(value=value):
                values = self.healthy.copy()
                values[-1, 0] = value
                _, report = self.run_audit(values, "--max-zero-row-fraction", 1, expected=1)
                self.assertEqual(report["summary"]["nonfinite_values"], 1)
                self.assertTrue(any(error["code"] == "nonfinite_payload" for error in report["errors"]))

    def test_all_nonfinite_is_strict_json_and_fails(self):
        _, report = self.run_audit(np.full((5, 3), np.nan), expected=1)
        self.assertEqual(report["summary"]["row_norm"]["finite_rows"], 0)
        self.assertIsNone(report["summary"]["row_norm"]["mean"])

    def test_constant_nonzero_payload_needs_explicit_variation_contract(self):
        values = np.ones((100, 8), dtype=np.float16)
        _, report = self.run_audit(values, expected=0)
        self.assertEqual(report["summary"]["zero_rows"], 0)
        self.assertEqual(report["summary"]["feature_variation"]["rms_feature_std"], 0)
        self.assertGreater(report["diversity_probe"]["duplicate_row_fraction"], .95)
        _, report = self.run_audit(values, "--min-feature-std", "0.0001", expected=1)
        self.assertTrue(any(error["code"] == "feature_variation_below_minimum" for error in report["errors"]))

    def test_local_constant_tail_rejected_by_variation_policy(self):
        values = self.healthy.copy()
        values[500:] = 1
        _, report = self.run_audit(values, "--min-feature-std", ".01", expected=1)
        self.assertGreater(report["summary"]["feature_variation"]["rms_feature_std"], .01)
        self.assertTrue(any(item["feature_variation"]["rms_feature_std"] == 0 for item in report["strata"]))

    def test_full_and_sampled_are_honest_about_scope(self):
        values = self.healthy.copy()
        # With exactly two samples per stratum, only boundaries are checked.
        values[42] = 0
        _, sampled = self.run_audit(values, "--sample-rows", 32, expected=0)
        self.assertEqual(sampled["check_scope"]["checked_rows"], 32)
        self.assertLess(sampled["check_scope"]["coverage_fraction"], 1)
        self.assertIn("unobserved", sampled["check_scope"]["scope_statement"])
        _, full = self.run_audit(values, "--mode", "full", "--chunk-rows", 7, expected=1)
        self.assertEqual(full["check_scope"]["checked_rows"], 1000)
        self.assertEqual(full["check_scope"]["coverage_fraction"], 1)
        self.assertEqual(full["summary"]["zero_rows"], 1)

    def test_float16_norms_do_not_underflow_or_overflow(self):
        for scalar in (np.nextafter(np.float16(0), np.float16(1)), np.float16(60000)):
            with self.subTest(scalar=float(scalar)):
                _, report = self.run_audit(np.full((4, 8), scalar, dtype=np.float16), expected=0)
                norm = report["summary"]["row_norm"]["mean"]
                self.assertGreater(norm, 0)
                self.assertAlmostEqual(norm / (float(scalar) * np.sqrt(8)), 1)

    def test_single_row_small_integer_matrix_and_fortran_order(self):
        _, report = self.run_audit(np.array([[1, 2]], dtype=np.int32), expected=0)
        self.assertEqual(report["summary"]["checked_rows"], 1)
        self.run_audit(np.array([[1, 2]], dtype=np.int32), "--min-feature-std", ".01", expected=1)
        self.run_audit(np.asfortranarray(self.healthy[:30]), "--mode", "full", expected=0)

    def test_empty_wrong_dim_object_complex_bool_and_truncated_inputs_fail(self):
        invalid = [np.empty((0, 8)), np.empty((8, 0)), np.ones(10),
                   np.array([[{"unsafe": "never unpickle"}]], dtype=object),
                   np.ones((4, 2), dtype=np.complex64), np.ones((4, 2), dtype=bool)]
        if np.dtype(np.longdouble).itemsize > 8:
            invalid.append(np.ones((4, 2), dtype=np.longdouble))
        for values in invalid:
            with self.subTest(shape=values.shape, dtype=str(values.dtype)):
                process, report = self.run_audit(values)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(report["status"], "fail")
        self.source.write_bytes(b"not a numpy matrix")
        process, report = self.run_audit()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["status"], "fail")

    def test_invalid_thresholds_modes_and_memory_policy_fail(self):
        np.save(self.source, self.healthy)
        cases = [("--max-zero-row-fraction", "-0.1"), ("--max-zero-row-fraction", "1.1"),
                 ("--max-zero-row-fraction", "nan"), ("--min-feature-std", "inf"),
                 ("--min-feature-std", "-.1"), ("--sample-rows", "0"),
                 ("--sample-rows", "3"), ("--strata", "0"),
                 ("--max-working-mib", "1"), ("--expected-sha256", "invalid")]
        for extra in cases:
            with self.subTest(extra=extra):
                process, report = self.run_audit(None, *extra)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(report["status"], "fail")
        process = subprocess.run(self.command("--mode", "magic"), capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        # A single extremely wide row is rejected before conversion exceeds the budget.
        self.run_audit(np.ones((2, 60000), dtype=np.float16), "--max-working-mib", 8, expected=2)

    def test_missing_source_wrong_extension_and_report_write_failure(self):
        process, report = self.run_audit()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["status"], "fail")
        self.source = self.root / "embedding.bin"
        self.source.write_bytes(b"unsupported")
        self.run_audit(None, expected=2)
        self.source = self.root / "embedding.npy"
        np.save(self.source, self.healthy)
        self.report = self.root / "missing-parent" / "report.json"
        process = subprocess.run(self.command(), capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("report write failed", process.stderr)

    def test_missing_numpy_does_not_install_dependencies(self):
        np.save(self.source, self.healthy)
        command = self.command()
        command.insert(1, "-S")
        process = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(report["errors"][0]["code"], "missing_dependency")

    def test_hash_identity_match_and_mismatch(self):
        np.save(self.source, self.healthy)
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        _, report = self.run_audit(None, "--hash-source", expected=0)
        self.assertEqual(report["source"]["content_sha256"], digest)
        self.assertEqual(report["source"]["identity_level"], "sha256_observed")
        _, report = self.run_audit(None, "--expected-sha256", digest, expected=0)
        self.assertEqual(report["source"]["identity_level"], "sha256_matches_supplied_expected")
        self.run_audit(None, "--expected-sha256", "0" * 64, expected=1)

    def test_report_cannot_overwrite_source_or_resolved_alias(self):
        np.save(self.source, self.healthy)
        original = self.source.read_bytes()
        self.report = self.source
        process = subprocess.run(self.command(), capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.source.read_bytes(), original)
        (self.root / "child").mkdir()
        self.report = self.root / "child" / ".." / self.source.name
        process = subprocess.run(self.command(), capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.source.read_bytes(), original)

    def test_report_hardlink_alias_refused_when_supported(self):
        np.save(self.source, self.healthy)
        original = self.source.read_bytes()
        self.report = self.root / "alias.npy"
        try:
            os.link(self.source, self.report)
        except OSError:
            self.skipTest("creating hardlinks is not permitted on this host")
        process = subprocess.run(self.command(), capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.source.read_bytes(), original)

    def test_report_symlink_alias_refused_when_supported(self):
        np.save(self.source, self.healthy)
        self.report = self.root / "alias.json"
        try:
            self.report.symlink_to(self.source)
        except OSError:
            self.skipTest("creating symlinks is not permitted on this host")
        original = self.source.read_bytes()
        process = subprocess.run(self.command(), capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.source.read_bytes(), original)

    def test_changed_stat_makes_result_inconclusive(self):
        np.save(self.source, self.healthy)
        original_stat = AUDIT._stat(self.source)
        changed = dict(original_stat, st_mtime_ns=original_stat["st_mtime_ns"] + 1)
        with patch.object(AUDIT, "_stat", side_effect=[original_stat, changed]):
            code = AUDIT.main(self.command()[2:])
        self.assertNotEqual(code, 0)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "inconclusive")

    def test_numeric_interruption_keeps_scope_explicitly_incomplete(self):
        _, report = self.run_audit(np.full((2, 3), 1e308), "--mode", "full", "--strata", 1, expected=2)
        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["check_scope"]["completed"])
        self.assertEqual(report["check_scope"]["checked_rows"], 0)

    def test_checked_launcher_prevents_training_marker_on_failure(self):
        marker = self.root / "training-started.txt"
        launcher = (
            "import pathlib,subprocess,sys; "
            "subprocess.run(sys.argv[2:],check=True); "
            "subprocess.run([sys.executable,'-c',"
            "'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(\"started\")',sys.argv[1]],check=True)"
        )
        corrupt = self.healthy.copy()
        corrupt[66:] = 0
        np.save(self.source, corrupt)
        process = subprocess.run([sys.executable, "-c", launcher, str(marker), *self.command()],
                                 capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(marker.exists())
        np.save(self.source, self.healthy)
        process = subprocess.run([sys.executable, "-c", launcher, str(marker), *self.command()],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(marker.read_text(), "started")


if __name__ == "__main__":
    unittest.main()
