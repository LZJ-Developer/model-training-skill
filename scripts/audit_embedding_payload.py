#!/usr/bin/env python3
"""Read-only, bounded-memory payload audit for a dense real 2-D .npy matrix.

Requires NumPy; never installs dependencies. Thresholds are a dataset contract,
not a universal embedding-quality test. A sampled pass is not a full-file proof.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import struct
import sys
import tempfile


SCHEMA = "embedding-payload-audit/v1"
PROBE_LIMIT = 4096


class AuditError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _error(report, code, message, status="fail"):
    report["errors"].append({"code": code, "message": str(message)})
    if report["status"] != "inconclusive":
        report["status"] = status


def _stat(path):
    value = path.stat()
    return {key: int(getattr(value, key)) for key in
            ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}


def _same_file(left, right):
    if left.resolve() == right.resolve():
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _counts(lengths, target):
    """Allocate coverage at both stratum boundaries, then interior probes."""
    base = [min(2, length) for length in lengths]
    if target < sum(base):
        raise AuditError("insufficient_sample_rows",
                         "sample-rows must cover both boundaries of every stratum "
                         "(at least %d rows for this matrix)" % sum(base))
    target = min(target, sum(lengths))
    capacities = [length - count for length, count in zip(lengths, base)]
    capacity = sum(capacities)
    remaining = target - sum(base)
    if not capacity:
        return base
    extra = [remaining * value // capacity for value in capacities]
    left = remaining - sum(extra)
    order = sorted(range(len(lengths)),
                   key=lambda i: (remaining * capacities[i]) % capacity,
                   reverse=True)
    for index in order:
        if left and extra[index] < capacities[index]:
            extra[index] += 1
            left -= 1
    return [a + b for a, b in zip(base, extra)]


def _indices(start, stop, count, rng):
    """Floyd sampling uses O(count) space even for a very long stratum."""
    length = stop - start
    if count == length:
        return list(range(start, stop))
    if length == 1:
        return [start]
    selected = set()
    for upper in range(length - 2 - (count - 2), length - 2):
        choice = rng.randrange(upper + 1)
        selected.add(upper if choice in selected else choice)
    return [start] + [start + 1 + value for value in sorted(selected)] + [stop - 1]


class Moments:
    """Mergeable float64 population moments, one vector per feature."""
    def __init__(self, np):
        self.np = np
        self.count = 0
        self.mean = None
        self.m2 = None

    def add(self, values):
        if not len(values):
            return
        mean = values.mean(axis=0)
        centered = values - mean
        m2 = self.np.sum(centered * centered, axis=0)
        self.merge(len(values), mean, m2)

    def merge(self, count, mean, m2):
        if not count:
            return
        if not self.count:
            self.count, self.mean, self.m2 = count, mean.copy(), m2.copy()
            return
        delta = mean - self.mean
        total = self.count + count
        self.m2 += m2 + delta * delta * (self.count / total) * count
        self.mean += delta * (count / total)
        self.count = total

    def features(self):
        if not self.count:
            return {"finite_rows": 0, "estimable_between_rows": False}
        variance = self.np.maximum(self.m2 / self.count, 0)
        std = self.np.sqrt(variance)
        return {
            "finite_rows": self.count,
            "ddof": 0,
            "estimable_between_rows": self.count >= 2,
            "std_min": float(std.min()),
            "std_mean": float(std.mean()),
            "std_max": float(std.max()),
            "variance_mean": float(variance.mean()),
            "rms_feature_std": float(self.np.sqrt(variance.mean())),
            "exact_constant_feature_fraction": float(self.np.mean(std == 0)),
        }


class RowStats:
    def __init__(self, np):
        self.np = np
        self.rows = self.zero = self.nonfinite_rows = self.nonfinite_values = 0
        self.norms = Moments(np)
        self.norm_min = self.norm_max = None
        self.features = Moments(np)
        self.probe_norms = []

    def add(self, values, row_ids, probe_ids, diversity):
        np = self.np
        finite = np.isfinite(values)
        finite_rows = finite.all(axis=1)
        self.rows += len(values)
        self.zero += int(np.count_nonzero((values == 0).all(axis=1)))
        self.nonfinite_rows += int(np.count_nonzero(~finite_rows))
        self.nonfinite_values += int(finite.size - np.count_nonzero(finite))
        clean = values if finite_rows.all() else values[finite_rows]
        self.features.add(clean)
        if not len(clean):
            return
        # Scale before squaring: float16 underflow/overflow cannot change norms.
        scale = np.abs(clean).max(axis=1)
        safe_scale = np.where(scale == 0, 1, scale)
        scaled = clean / safe_scale[:, None]
        norms = scale * np.sqrt(np.sum(scaled * scaled, axis=1))
        self.norms.add(norms)
        low, high = float(norms.min()), float(norms.max())
        self.norm_min = low if self.norm_min is None else min(self.norm_min, low)
        self.norm_max = high if self.norm_max is None else max(self.norm_max, high)
        clean_ids = (row_id for row_id, valid in zip(row_ids, finite_rows) if valid)
        for offset, row_id in enumerate(clean_ids):
            if row_id in probe_ids:
                self.probe_norms.append(float(norms[offset]))
                # Hashes stay in memory; no raw vectors or vector hashes are emitted.
                key = hashlib.sha256(clean[offset].tobytes()).digest()
                diversity[key] = diversity.get(key, 0) + 1

    def summary(self):
        result = {
            "checked_rows": self.rows,
            "zero_rows": self.zero,
            "zero_row_fraction": self.zero / self.rows if self.rows else None,
            "nonfinite_rows": self.nonfinite_rows,
            "nonfinite_values": self.nonfinite_values,
            "feature_variation": self.features.features(),
        }
        norm = {"finite_rows": self.norms.count, "min": self.norm_min,
                "max": self.norm_max, "mean": None, "std": None,
                "quantile_probe_rows": len(self.probe_norms),
                "quantiles_from_bounded_probe": None}
        if self.norms.count:
            norm["mean"] = float(self.norms.mean)
            norm["std"] = math.sqrt(max(0, float(self.norms.m2 / self.norms.count)))
        if self.probe_norms:
            quantiles = self.np.quantile(self.probe_norms, [0, .25, .5, .75, 1])
            norm["quantiles_from_bounded_probe"] = dict(zip(
                ["p0", "p25", "p50", "p75", "p100"], map(float, quantiles)))
        result["row_norm"] = norm
        return result


def _validate(args):
    for name in ("sample_rows", "strata", "chunk_rows", "max_working_mib"):
        if getattr(args, name) <= 0:
            raise AuditError("invalid_policy", "%s must be positive" % name)
    if args.strata > PROBE_LIMIT:
        raise AuditError("invalid_policy", "strata must be at most %d" % PROBE_LIMIT)
    if args.max_working_mib < 8:
        raise AuditError("invalid_policy", "max-working-mib must be at least 8")
    if not math.isfinite(args.max_zero_row_fraction) or not 0 <= args.max_zero_row_fraction <= 1:
        raise AuditError("invalid_policy", "max-zero-row-fraction must be finite in [0, 1]")
    if args.min_feature_std is not None and (
            not math.isfinite(args.min_feature_std) or args.min_feature_std < 0):
        raise AuditError("invalid_policy", "min-feature-std must be finite and nonnegative")
    if args.expected_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_sha256):
        raise AuditError("invalid_policy", "expected-sha256 must contain exactly 64 hexadecimal digits")


def _apply_policy(report, summary, label, args):
    if summary["nonfinite_values"]:
        _error(report, "nonfinite_payload", "%s contains NaN or infinity" % label)
    if summary["zero_row_fraction"] > args.max_zero_row_fraction:
        _error(report, "zero_row_fraction_exceeded",
               "%s observed zero-row fraction %.6g exceeds %.6g" %
               (label, summary["zero_row_fraction"], args.max_zero_row_fraction))
    variation = summary["feature_variation"]
    if args.min_feature_std is not None and variation.get("estimable_between_rows"):
        if variation["rms_feature_std"] < args.min_feature_std:
            _error(report, "feature_variation_below_minimum",
                   "%s RMS feature standard deviation %.6g is below %.6g" %
                   (label, variation["rms_feature_std"], args.min_feature_std))


def _audit(args, report, source):
    try:
        import numpy as np
    except ImportError as exc:
        raise AuditError("missing_dependency", "NumPy is required; install it in the intended environment") from exc
    report["runtime"] = {"numpy_version": np.__version__}
    if source.suffix.lower() != ".npy":
        raise AuditError("unsupported_format", "only a dense 2-D real numeric .npy file is supported")
    report["source"]["stat_before"] = _stat(source)
    try:
        matrix = np.load(source, mmap_mode="r", allow_pickle=False)
    except (ValueError, EOFError, TypeError) as exc:
        raise AuditError("invalid_npy", "cannot mmap a safe numeric .npy (object/pickle is unsupported): %s" % exc) from exc
    if not isinstance(matrix, np.ndarray) or matrix.ndim != 2:
        raise AuditError("unsupported_shape", "expected a dense 2-D matrix")
    report["source"].update({"shape": list(matrix.shape), "dtype": str(matrix.dtype),
                              "array_payload_bytes": int(matrix.nbytes)})
    if matrix.dtype.kind not in "iuf" or matrix.dtype.itemsize > 8:
        raise AuditError("unsupported_dtype", "only real integer/floating dtypes up to 64 bits are supported; bool/object/complex/wider floats are not")
    rows, features = map(int, matrix.shape)
    if not rows or not features:
        raise AuditError("empty_matrix", "both matrix dimensions must be nonzero")
    strata = min(args.strata, rows)
    bounds = [(i * rows // strata, (i + 1) * rows // strata) for i in range(strata)]
    lengths = [end - start for start, end in bounds]
    target = min(rows, args.sample_rows) if args.mode == "sampled" else rows
    counts = _counts(lengths, target) if args.mode == "sampled" else lengths
    # Conservative allowance for Python index objects, moment vectors, masks,
    # float64 conversions and intermediate arrays. Refuse widths over the budget.
    budget = args.max_working_mib * 1024 * 1024
    index_bytes = (max(counts) if args.mode == "sampled" else 0) * 128
    vector_bytes = features * 8 * 20
    remaining = budget - index_bytes - vector_bytes - 2 * 1024 * 1024
    bytes_per_row = features * (8 * 10 + matrix.dtype.itemsize) + 256
    chunk_rows = min(args.chunk_rows, remaining // bytes_per_row)
    if chunk_rows < 1:
        raise AuditError("working_memory_budget_exceeded",
                         "feature width/sample indices exceed working-memory budget; "
                         "reduce sample-rows/strata or explicitly increase max-working-mib")
    probe_target = min(PROBE_LIMIT, target)
    # At most two probes per stratum can require 8192 rows with 4096 strata;
    # use one stratum midpoint in that corner, keeping the diversity probe bounded.
    probe_counts = _counts(counts, probe_target) if probe_target >= sum(min(2, x) for x in counts) else None
    rng = random.Random(args.seed)
    probe_rng = random.Random(args.seed ^ 0x57A7A)
    diversity = {}
    total = RowStats(np)
    report["check_scope"] = {
        "mode": args.mode, "completed": False, "population_rows": rows, "planned_checked_rows": target,
        "checked_rows": 0, "coverage_fraction": 0,
        "scope_statement": ("all matrix rows inspected in bounded blocks" if args.mode == "full" else
                            "only recorded stratified samples inspected; unobserved rows may be corrupt"),
        "row_axis_sampling": "each nonempty stratum includes first and last row; seeded interior samples without replacement",
        "effective_strata": strata, "effective_chunk_rows": chunk_rows,
        "working_memory_budget_mib": args.max_working_mib,
        "checked_row_indices_digest_encoding": "SHA-256 of ordered uint64 little-endian row indices per stratum",
    }
    for index, ((start, end), count) in enumerate(zip(bounds, counts)):
        selected = _indices(start, end, count, rng) if args.mode == "sampled" else None
        population = count if selected is not None else end - start
        if probe_counts is None:
            positions = [population // 2] if index < PROBE_LIMIT else []
        else:
            positions = _indices(0, population, probe_counts[index], probe_rng)
        probe_ids = {selected[pos] if selected is not None else start + pos for pos in positions}
        local = RowStats(np)
        digest = hashlib.sha256()
        for offset in range(0, count, chunk_rows):
            if selected is None:
                row_ids = range(start + offset, min(start + offset + chunk_rows, end))
                block = matrix[row_ids.start:row_ids.stop]
            else:
                row_ids = selected[offset:offset + chunk_rows]
                block = matrix[row_ids]
            for row_id in row_ids:
                digest.update(struct.pack("<Q", row_id))
            values = np.asarray(block, dtype=np.float64)
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                local.add(values, row_ids, probe_ids, diversity)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            summary = local.summary()
            total.features.merge(local.features.count, local.features.mean, local.features.m2)
            total.norms.merge(local.norms.count, local.norms.mean, local.norms.m2)
        total.rows += local.rows
        total.zero += local.zero
        total.nonfinite_rows += local.nonfinite_rows
        total.nonfinite_values += local.nonfinite_values
        if local.norm_min is not None:
            total.norm_min = local.norm_min if total.norm_min is None else min(total.norm_min, local.norm_min)
            total.norm_max = local.norm_max if total.norm_max is None else max(total.norm_max, local.norm_max)
        total.probe_norms.extend(local.probe_norms)
        summary.update({"index": index, "row_start_inclusive": start, "row_stop_exclusive": end,
                        "checked_row_indices_sha256": digest.hexdigest()})
        report["strata"].append(summary)
        report["check_scope"]["checked_rows"] += local.rows
        report["check_scope"]["coverage_fraction"] = report["check_scope"]["checked_rows"] / rows
        _apply_policy(report, summary, "stratum %d" % index, args)
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        report["summary"] = total.summary()
    _apply_policy(report, report["summary"], "overall", args)
    if args.min_feature_std is not None and total.features.count < 2:
        _error(report, "insufficient_rows_for_variation", "min-feature-std requires at least two finite checked rows")
    probe_rows = sum(diversity.values())
    report["diversity_probe"] = {
        "finite_probe_rows": probe_rows, "unique_rows": len(diversity),
        "duplicate_row_fraction": 1 - len(diversity) / probe_rows if probe_rows else None,
        "maximum_row_multiplicity": max(diversity.values()) if diversity else None,
        "scope": "at most 4096 stratified checked rows; equality is descriptive, not an automatic failure",
    }
    if args.hash_source or args.expected_sha256:
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        actual = digest.hexdigest()
        report["source"]["content_sha256"] = actual
        report["source"]["identity_level"] = "sha256_observed"
        if args.expected_sha256:
            if actual != args.expected_sha256.lower():
                _error(report, "sha256_mismatch", "source does not match the supplied expected SHA-256")
            else:
                report["source"]["identity_level"] = "sha256_matches_supplied_expected"


def _write_report(path, report):
    # Atomic replacement prevents leaving a truncated report after an IO failure.
    encoded = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".embedding-audit-", suffix=".json", delete=False) as handle:
            temporary = handle.name
            handle.write(encoded)
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-zero-row-fraction", type=float, required=True,
                        help="contract threshold in [0,1], applied overall and to every observed stratum")
    parser.add_argument("--mode", choices=("sampled", "full"), default="sampled")
    parser.add_argument("--sample-rows", type=int, default=4096)
    parser.add_argument("--strata", type=int, default=16)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--chunk-rows", type=int, default=1024)
    parser.add_argument("--max-working-mib", type=int, default=64)
    parser.add_argument("--min-feature-std", type=float,
                        help="optional minimum RMS of per-feature population std, overall and per stratum with >=2 finite rows")
    parser.add_argument("--hash-source", action="store_true")
    parser.add_argument("--expected-sha256", help="trusted expected digest; hashes the entire source")
    args = parser.parse_args(argv)
    source = args.source.absolute()
    destination = args.report.absolute()
    if _same_file(source, destination):
        print("audit error: report must not alias the read-only source", file=sys.stderr)
        return 2
    report = {
        "schema": SCHEMA, "status": "pass", "errors": [],
        "source": {"path": str(source), "identity_level": "stat_only"},
        "check_scope": {"mode": args.mode, "completed": False},
        "policy": {"max_zero_row_fraction": args.max_zero_row_fraction,
                   "min_rms_feature_std": args.min_feature_std,
                   "zero_threshold_scope": "overall and each observed stratum",
                   "variation_threshold_scope": "overall and strata with at least two finite rows",
                   "nonfinite_values_allowed": 0, "sample_rows": args.sample_rows,
                   "strata": args.strata, "seed": args.seed,
                   "chunk_rows": args.chunk_rows, "max_working_mib": args.max_working_mib,
                   "expected_sha256": args.expected_sha256},
        "strata": [],
        "limitations": [
            "Sampled mode cannot establish full-file integrity or guarantee finding small/local corruption.",
            "Stat-before/after detects some changes, not concurrent same-stat writes; mmap is not a snapshot.",
            "An observed hash is not a trusted reference; a supplied expected digest must come from a trusted source.",
            "File statistics and hashes do not prove embedding-to-ID/lookup alignment or biological usefulness.",
            "Exact duplicate rows and zero rows can be legitimate; thresholds require a data contract.",
            "No network calls, unpickling, dependency installation, source writes or training launch occur.",
        ],
    }
    exit_code = 0
    try:
        _validate(args)
        _audit(args, report, source)
        report["check_scope"]["completed"] = True
    except AuditError as exc:
        _error(report, exc.code, exc)
        exit_code = 2
    except (OSError, ValueError, EOFError, FloatingPointError, OverflowError, MemoryError) as exc:
        _error(report, "audit_io_format_or_numeric_error", "%s: %s" % (type(exc).__name__, exc))
        exit_code = 2
    finally:
        if "stat_before" in report["source"]:
            try:
                report["source"]["stat_after"] = _stat(source)
                if report["source"]["stat_after"] != report["source"]["stat_before"]:
                    _error(report, "source_changed", "source identity/stat changed during audit", "inconclusive")
                    report["status"] = "inconclusive"
                    exit_code = 2
            except OSError as exc:
                _error(report, "source_changed_or_unavailable", str(exc), "inconclusive")
                report["status"] = "inconclusive"
                exit_code = 2
    if report["status"] != "pass" and not exit_code:
        exit_code = 1
    # Invalid NaN/Inf policy values still produce strict JSON, not nonstandard JSON tokens.
    for key, value in report["policy"].items():
        if isinstance(value, float) and not math.isfinite(value):
            report["policy"][key] = str(value)
    try:
        # Repeat the collision check in case an existing destination changed mid-audit.
        if _same_file(source, destination):
            raise OSError("report aliases source; refusing to write")
        _write_report(destination, report)
    except (OSError, ValueError) as exc:
        print("audit error: report write failed: %s" % exc, file=sys.stderr)
        return 2
    print("%s: %s (%s)" % (report["status"], source.name, destination))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
