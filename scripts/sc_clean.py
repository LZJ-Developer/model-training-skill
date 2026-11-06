#!/usr/bin/env python3
"""Single-cell cleaning CLI: inspect / qc / pseudobulk, with a cleaning ledger.

This is a re-integration of the useful parts of two upstream skills:

  * K-Dense ``scanpy`` skill (BSD-3-Clause): count validation, explicit
    counts-layer selection, gene-class annotation, pseudobulk aggregation.
  * Anthropic ``single-cell-rna-qc`` (Apache-2.0): MAD outlier detection and
    the before/after QC summary.

What changed in the re-integration:

  * scanpy is optional. ``inspect``, ``qc`` and ``pseudobulk`` run on
    anndata + numpy + scipy only; only ``qc --scrublet`` imports scanpy.
  * QC **flags** cells instead of dropping them. The original object is never
    overwritten; every downstream step reads ``pass_qc`` / ``qc_reason``.
  * MAD thresholds are computed **per batch** when ``--group-by`` is given.
    Upstream Anthropic applies one global threshold; upstream K-Dense's
    ``--batch-key`` only affects Scrublet.
  * Every stage appends a JSONL ledger row:
    ``step / why / before / after / config / artifact``.

Usage:
    python sc_clean.py inspect raw.h5ad
    python sc_clean.py qc raw.h5ad --out qc/ --group-by batch --ledger ledger.jsonl
    python sc_clean.py pseudobulk raw.h5ad --by context perturbation \
        --counts-layer counts --min-cells 30 --out-prefix pb/hepg2

Dependencies: anndata, numpy, pandas, scipy. Optional: scanpy (Scrublet),
matplotlib (--plots).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import median_abs_deviation


# --------------------------------------------------------------------------- utils

def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def info(msg: str) -> None:
    print(msg, flush=True)


def append_ledger(path: Path | None, *, step: str, why: str, before: dict,
                  after: dict, config: dict | None = None,
                  artifact: str | None = None) -> None:
    """Append one machine-readable row. Cleaning without a ledger is a silent confound."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "step": step, "why": why, "before": before, "after": after,
        "config": config or {}, "artifact": artifact,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_anndata(path: Path):
    import anndata as ad

    if path.suffix == ".h5ad":
        return ad.read_h5ad(path)
    die(f"unsupported input {path.suffix}; this script reads .h5ad. "
        "Use the scanpy skill's convert.py for 10x mtx/h5/rds.")


def matrix_of(adata, layer: str | None):
    if layer is not None:
        if layer not in adata.layers:
            die(f"counts layer {layer!r} is missing; refusing to fall back to X")
        return adata.layers[layer], f"layers[{layer!r}]"
    return adata.X, "X"


def is_count_like(arr, n: int = 100_000) -> bool:
    vals = arr.data[:n] if sparse.issparse(arr) else np.asarray(arr).ravel()[:n]
    if vals.size == 0:
        return False
    return bool(np.isfinite(vals).all() and (vals >= 0).all()
                and np.equal(vals, np.floor(vals)).all())


def validate_counts(arr, label: str, require_nonzero_cells: bool = True) -> None:
    """Finite, non-negative, integral. Does NOT establish provenance."""
    if arr is None or len(arr.shape) != 2 or min(arr.shape) == 0:
        die(f"{label}: expected a nonempty cells x genes matrix")
    vals = arr.data if sparse.issparse(arr) else np.asarray(arr)
    if (not np.isfinite(vals).all() or (vals < 0).any()
            or not np.equal(vals, np.floor(vals)).all()):
        die(f"{label}: expected finite, nonnegative integer counts; "
            "verify whether the matrix is already normalised")
    if require_nonzero_cells:
        totals = np.asarray(arr.sum(axis=1)).ravel()
        if (totals <= 0).any():
            die(f"{label}: {int((totals <= 0).sum())} zero-total cells; "
                "filter them before normalisation")


def select_counts(adata, layer: str | None) -> str:
    """Validate the chosen matrix and mirror it into layers['counts']."""
    arr, label = matrix_of(adata, layer)
    validate_counts(arr, label)
    if layer is None:
        if "log1p" in adata.uns:
            die("X carries log1p provenance; pass --counts-layer explicitly")
        if "counts" in adata.layers:
            delta = adata.layers["counts"] - arr
            differs = delta.nnz != 0 if sparse.issparse(delta) else np.any(delta != 0)
            if differs:
                die("X differs from layers['counts']; pass --counts-layer counts")
    adata.layers["counts"] = arr.copy()
    return label


def annotate_gene_classes(adata) -> list[str]:
    names = adata.var_names.astype(str)
    adata.var["mt"] = names.str.startswith(("MT-", "mt-", "Mt-"))
    adata.var["ribo"] = names.str.startswith(("RPS", "RPL", "Rps", "Rpl"))
    adata.var["hb"] = names.str.contains(r"^HB[^P]|^Hb[^p]", regex=True)
    return [k for k in ("mt", "ribo", "hb") if bool(adata.var[k].any())]


# --------------------------------------------------------------------------- inspect

def cmd_inspect(args) -> None:
    import anndata as ad  # noqa: F401  (kept for the import error message)

    adata = load_anndata(args.input)
    X = adata.X
    info("=" * 72)
    info(f"{args.input}: {adata.n_obs} cells x {adata.n_vars} genes")
    info("=" * 72)

    if X is None:
        info("X is absent; inspect named layers and their provenance")
    else:
        vals = X.data if sparse.issparse(X) else np.asarray(X)
        info(f"X dtype={X.dtype} min={float(vals.min()):.3g} max={float(vals.max()):.3g} "
             f"count_like={is_count_like(X)}")
        info("  count_like is a shape check, not proof of raw-count provenance.")
    info(f"layers: {list(adata.layers.keys())}")
    info(f"uns.log1p: {'present' if 'log1p' in adata.uns else 'absent'}")
    info(f"raw: {'present' if adata.raw is not None else 'none'}")

    info(f"\nobs columns ({len(adata.obs.columns)}):")
    for col in adata.obs.columns[: args.max_cols]:
        s = adata.obs[col]
        if pd.api.types.is_numeric_dtype(s.dtype):
            info(f"  - {col} ({s.dtype}) range=[{s.min():.3g}, {s.max():.3g}] "
                 f"missing={int(s.isna().sum())}")
        else:
            nun = s.nunique(dropna=True)
            head = list(s.dropna().unique()[:8]) if nun <= 8 else ""
            info(f"  - {col} ({s.dtype}) {nun} categories {head} "
                 f"missing={int(s.isna().sum())}")

    info(f"\nvar columns: {list(adata.var.columns[: args.max_cols])}")

    if args.group_by:
        for key in args.group_by:
            if key not in adata.obs:
                die(f"group column {key!r} not in obs")
        sizes = adata.obs.groupby(args.group_by, observed=True).size()
        info(f"\ngroup sizes by {args.group_by}: n={len(sizes)} "
             f"min={int(sizes.min())} median={int(sizes.median())} max={int(sizes.max())}")

    if args.json:
        out = {
            "input": str(args.input), "n_cells": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "x_dtype": str(X.dtype) if X is not None else None,
            "count_like": bool(is_count_like(X)) if X is not None else None,
            "layers": list(adata.layers.keys()),
            "log1p": "log1p" in adata.uns,
            "obs_columns": list(adata.obs.columns),
            "var_columns": list(adata.var.columns),
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1))
        info(f"\nwrote {args.json}")


# --------------------------------------------------------------------------- qc

def _mad_mask(values: pd.Series, n_mads: float) -> np.ndarray:
    arr = values.to_numpy(dtype=float)
    med = np.nanmedian(arr)
    mad = median_abs_deviation(arr, nan_policy="omit")
    if not np.isfinite(mad) or mad == 0:
        return np.zeros(len(arr), dtype=bool)
    return (arr < med - n_mads * mad) | (arr > med + n_mads * mad)


def _qc_metrics(adata) -> None:
    X = adata.layers["counts"]
    totals = np.asarray(X.sum(axis=1)).ravel().astype(float)
    adata.obs["total_counts"] = totals
    adata.obs["n_genes_by_counts"] = np.asarray((X > 0).sum(axis=1)).ravel().astype(int)
    safe = np.where(totals > 0, totals, 1.0)
    for key in ("mt", "ribo", "hb"):
        if key in adata.var and bool(adata.var[key].any()):
            sub = X[:, adata.var[key].to_numpy()]
            adata.obs[f"pct_counts_{key}"] = (
                np.asarray(sub.sum(axis=1)).ravel().astype(float) / safe * 100.0
            )
        else:
            adata.obs[f"pct_counts_{key}"] = 0.0


def cmd_qc(args) -> None:
    import anndata as ad

    adata = load_anndata(args.input)
    layer_label = select_counts(adata, args.counts_layer)
    adata.var_names_make_unique()
    classes = annotate_gene_classes(adata)
    if "mt" not in classes:
        info("warning: no mitochondrial genes matched MT-/mt-; pct_counts_mt stays 0")
    _qc_metrics(adata)

    n_cells0, n_genes0 = adata.n_obs, adata.n_vars
    groups = (adata.obs[args.group_by].astype(str).agg("|".join, axis=1)
              if args.group_by else None)

    def flags_for(index: pd.Index) -> dict[str, np.ndarray]:
        sub = adata.obs.loc[index]
        masks = {k: np.zeros(len(sub), dtype=bool) for k in ("counts", "genes", "mt")}
        if groups is not None and len(sub) >= args.min_group:
            g = groups.loc[index]
            for _, labels in g.groupby(g).groups.items():
                pos = index.get_indexer(labels)
                masks["counts"][pos] |= _mad_mask(sub.loc[labels, "total_counts"], args.mad_counts)
                masks["genes"][pos] |= _mad_mask(sub.loc[labels, "n_genes_by_counts"], args.mad_genes)
                masks["mt"][pos] |= _mad_mask(sub.loc[labels, "pct_counts_mt"], args.mad_mt)
        else:
            if groups is not None:
                info(f"warning: group smaller than {args.min_group} cells; "
                     "falling back to global MAD for this batch")
            masks = {
                "counts": _mad_mask(sub["total_counts"], args.mad_counts),
                "genes": _mad_mask(sub["n_genes_by_counts"], args.mad_genes),
                "mt": _mad_mask(sub["pct_counts_mt"], args.mad_mt),
            }
        return masks

    if groups is not None:
        counts_m = np.zeros(n_cells0, dtype=bool)
        genes_m = np.zeros(n_cells0, dtype=bool)
        mt_m = np.zeros(n_cells0, dtype=bool)
        for _, idx in groups.groupby(groups).groups.items():
            m = flags_for(pd.Index(idx))
            pos = adata.obs.index.get_indexer(idx)
            counts_m[pos] |= m["counts"]
            genes_m[pos] |= m["genes"]
            mt_m[pos] |= m["mt"]
    else:
        m = flags_for(adata.obs.index)
        counts_m, genes_m, mt_m = m["counts"], m["genes"], m["mt"]

    doublet = np.zeros(n_cells0, dtype=bool)
    if args.scrublet:
        try:
            import scanpy as sc
        except ImportError:
            die("--scrublet requires scanpy (`pip install scanpy`)")
        work = ad.AnnData(X=adata.layers["counts"].copy(),
                          obs=pd.DataFrame(index=adata.obs_names),
                          var=adata.var.copy())
        batch_key = None
        if args.group_by and len(args.group_by) == 1:
            work.obs[args.group_by[0]] = adata.obs[args.group_by[0]].to_numpy()
            batch_key = args.group_by[0]
        sc.pp.scrublet(work, batch_key=batch_key, random_state=0)
        doublet = work.obs["predicted_doublet"].to_numpy(dtype=bool)
        adata.obs["predicted_doublet"] = doublet
        info(f"Scrublet flagged {int(doublet.sum())} doublets")

    hard_mt = adata.obs["pct_counts_mt"].to_numpy() > args.mt_threshold
    min_genes = adata.obs["n_genes_by_counts"].to_numpy() < args.min_genes
    mt_m = mt_m | hard_mt
    fail = counts_m | genes_m | mt_m | min_genes | doublet
    adata.obs["outlier_counts"] = counts_m
    adata.obs["outlier_genes"] = genes_m
    adata.obs["outlier_mt"] = mt_m
    adata.obs["below_min_genes"] = min_genes
    adata.obs["pass_qc"] = ~fail
    reasons = []
    for i in range(n_cells0):
        why = []
        if counts_m[i]: why.append("counts_mad")
        if genes_m[i]: why.append("genes_mad")
        if mt_m[i]: why.append("mt")
        if min_genes[i]: why.append("min_genes")
        if doublet[i]: why.append("doublet")
        reasons.append(",".join(why))
    adata.obs["qc_reason"] = reasons

    summary = {
        "input": str(args.input), "counts_source": layer_label,
        "before": {"cells": int(n_cells0), "genes": int(n_genes0)},
        "after": {"cells": int(adata.obs["pass_qc"].sum()),
                  "genes": int(n_genes0)},
        "fail_fraction": float(fail.mean()),
        "fail_by_metric": {
            "counts_mad": int(counts_m.sum()), "genes_mad": int(genes_m.sum()),
            "mt_mad_or_hard": int(mt_m.sum()), "below_min_genes": int(min_genes.sum()),
            "doublet": int(doublet.sum()),
        },
    }
    if groups is not None:
        per_group = adata.obs.assign(_ok=adata.obs["pass_qc"]).groupby(
            args.group_by, observed=True)["_ok"].agg(["size", "sum"])
        summary["groups"] = {
            str(k): {"cells": int(r["size"]), "pass": int(r["sum"])}
            for k, r in per_group.iterrows()
        }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_h5ad = out_dir / f"{args.input.stem}_with_qc.h5ad"
    try:
        ad.settings.allow_write_nullable_strings = True
        adata.write_h5ad(out_h5ad)
    except Exception as exc:  # noqa: BLE001 - report and keep the summary
        info(f"warning: could not write {out_h5ad}: {exc}")
        out_h5ad = None
    (out_dir / "qc_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1))

    info(f"cells {n_cells0} -> pass {summary['after']['cells']} "
         f"({summary['after']['cells'] / max(n_cells0, 1) * 100:.1f}%)")
    for k, v in summary["fail_by_metric"].items():
        info(f"  {k}: {v}")
    info(f"wrote {out_dir / 'qc_summary.json'}"
         + (f" and {out_h5ad}" if out_h5ad else ""))

    append_ledger(
        Path(args.ledger) if args.ledger else None,
        step="qc_flags", why=args.why,
        before={"cells": int(n_cells0), "genes": int(n_genes0)},
        after={"cells_pass": int(adata.obs["pass_qc"].sum()), "genes": int(n_genes0)},
        config={"counts_layer": layer_label, "group_by": args.group_by,
                "mad_counts": args.mad_counts, "mad_genes": args.mad_genes,
                "mad_mt": args.mad_mt, "mt_threshold": args.mt_threshold,
                "min_genes": args.min_genes, "scrublet": args.scrublet},
        artifact=str(out_dir / "qc_summary.json"),
    )


# --------------------------------------------------------------------------- pseudobulk

def cmd_pseudobulk(args) -> None:
    import anndata as ad

    adata = load_anndata(args.input)
    select_counts(adata, None if args.use_x else args.counts_layer)
    if not adata.var_names.is_unique:
        die("gene identifiers must be unique before aggregation")

    for key in list(args.by) + list(args.metadata):
        if key not in adata.obs:
            die(f"obs column {key!r} is missing")
        if adata.obs[key].isna().any():
            die(f"obs column {key!r} contains missing values")

    X = adata.layers["counts"]
    frame = adata.obs[list(args.by)].astype(str)
    codes, uniques = pd.factorize(pd.MultiIndex.from_frame(frame), sort=False)
    n_groups = int(codes.max()) + 1
    sample_ids = pd.Index([f"pb{i:06d}" for i in range(n_groups)], name="sample_id")

    indicator = sparse.csr_matrix(
        (np.ones(len(codes)), (codes, np.arange(len(codes)))),
        shape=(n_groups, len(codes)),
    )
    summed = indicator @ X
    mat = summed.toarray() if sparse.issparse(summed) else np.asarray(summed)

    groups_idx = [np.flatnonzero(codes == i) for i in range(n_groups)]
    rows, keep = [], np.ones(n_groups, dtype=bool)
    for i, idx in enumerate(groups_idx):
        group = adata.obs.iloc[idx]
        for key in args.metadata:
            if group[key].nunique(dropna=False) != 1:
                die(f"metadata {key!r} is not constant in group {sample_ids[i]}")
        row = dict(zip(args.by, frame.iloc[idx[0]].tolist()))
        for key in args.metadata:
            row[key] = group[key].iloc[0]
        row["n_cells"] = int(len(idx))
        rows.append(row)
        if len(idx) < args.min_cells:
            keep[i] = False

    manifest = pd.DataFrame(rows, index=sample_ids)
    if not keep.any():
        die(f"no group reaches --min-cells {args.min_cells}")
    mat = mat[keep]
    manifest = manifest.loc[keep]
    counts = pd.DataFrame(mat.T, index=adata.var_names.astype(str),
                          columns=manifest.index)

    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "npz":
        np.savez_compressed(
            f"{prefix}_counts.npz",
            counts=mat.T.astype(np.int64),
            genes=np.asarray(adata.var_names, dtype=str),
            samples=np.asarray(manifest.index, dtype=str),
        )
        counts_path = f"{prefix}_counts.npz"
    else:
        counts.to_csv(f"{prefix}_counts.csv")
        counts_path = f"{prefix}_counts.csv"
    manifest.to_csv(f"{prefix}_samples.csv")

    info(f"{counts.shape[1]} pseudobulk profiles x {counts.shape[0]} genes "
         f"(dropped {int((~keep).sum())} groups below {args.min_cells} cells)")
    info(f"wrote {counts_path} and {prefix}_samples.csv")

    append_ledger(
        Path(args.ledger) if args.ledger else None,
        step="pseudobulk", why=args.why,
        before={"cells": int(adata.n_obs), "groups": int(n_groups)},
        after={"groups_kept": int(keep.sum()), "genes": int(adata.n_vars),
               "cells_kept": int(manifest["n_cells"].sum())},
        config={"by": args.by, "metadata": args.metadata,
                "min_cells": args.min_cells, "format": args.format},
        artifact=counts_path,
    )


# --------------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    ins = sub.add_parser("inspect", help="structured summary, no mutation")
    ins.add_argument("input", type=Path)
    ins.add_argument("--max-cols", type=int, default=40)
    ins.add_argument("--group-by", nargs="+", default=None)
    ins.add_argument("--json", default=None)
    ins.set_defaults(func=cmd_inspect)

    qc = sub.add_parser("qc", help="QC flags on raw counts (never drops cells)")
    qc.add_argument("input", type=Path)
    qc.add_argument("--out", required=True)
    qc.add_argument("--counts-layer", default=None)
    qc.add_argument("--group-by", nargs="+", default=None,
                    help="compute MAD thresholds within each batch/context")
    qc.add_argument("--min-group", type=int, default=50,
                    help="groups smaller than this fall back to global MAD")
    qc.add_argument("--mad-counts", type=float, default=5.0)
    qc.add_argument("--mad-genes", type=float, default=5.0)
    qc.add_argument("--mad-mt", type=float, default=3.0)
    qc.add_argument("--mt-threshold", type=float, default=8.0)
    qc.add_argument("--min-genes", type=int, default=200)
    qc.add_argument("--scrublet", action="store_true",
                    help="run Scrublet doublet detection (requires scanpy)")
    qc.add_argument("--ledger", default=None)
    qc.add_argument("--why", default="QC flags on raw counts")
    qc.set_defaults(func=cmd_qc)

    pb = sub.add_parser("pseudobulk", help="sum raw counts per group")
    pb.add_argument("input", type=Path)
    pb.add_argument("--by", nargs="+", required=True,
                    help="grouping columns, e.g. --by context perturbation")
    src = pb.add_mutually_exclusive_group()
    src.add_argument("--counts-layer", default="counts")
    src.add_argument("--use-x", action="store_true",
                     help="explicitly declare X as raw counts")
    pb.add_argument("--metadata", nargs="+", default=[])
    pb.add_argument("--min-cells", type=int, default=30)
    pb.add_argument("--format", choices=["npz", "csv"], default="npz")
    pb.add_argument("--out-prefix", required=True)
    pb.add_argument("--ledger", default=None)
    pb.add_argument("--why", default="pseudobulk for DE / training rows")
    pb.set_defaults(func=cmd_pseudobulk)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
