"""Command line: revturn download | build | run | freeze | reproduce."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import PROJECT_ROOT, load_config

DEFAULT_CONFIG = str(PROJECT_ROOT / "configs" / "default.json")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="revturn", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("download", "build", "run", "freeze", "reproduce"):
        s = sub.add_parser(name)
        s.add_argument("--config", default=DEFAULT_CONFIG)
        if name == "download":
            s.add_argument("--source", choices=["baostock", "akshare", "all"], default="all")
        if name == "run":
            s.add_argument("--split", required=True)
            s.add_argument("--note", default="")
        if name == "freeze":
            s.add_argument("--dev-run", required=True)
        if name == "reproduce":
            s.add_argument("--run", required=True, help="existing run directory to compare with")
    a = p.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "download":
        from .download import download_akshare, download_baostock, manifest

        if a.source in ("baostock", "all"):
            download_baostock(cfg.raw_dir / "baostock")
        if a.source in ("akshare", "all"):
            download_akshare(cfg.raw_dir / "akshare")
        m = manifest(cfg.raw_dir)
        (cfg.raw_dir / "manifest.json").write_text(json.dumps(m, indent=1))
        print(f"{m['n_files']} raw files, {m['bytes'] / 1e6:.1f} MB")
    elif a.cmd == "build":
        from .panel import build_panel, save_panel

        t0 = time.time()
        panel = build_panel(cfg.raw_dir)
        save_panel(panel, cfg.processed_dir)
        print(
            f"panel built in {time.time() - t0:.1f}s: {panel.meta['n_dates']} dates x "
            f"{panel.meta['n_codes']} codes"
        )
    elif a.cmd == "run":
        from .pipeline import run

        out = run(a.config, a.split, note=a.note)
        s = json.loads((out / "summary.json").read_text())
        print(
            f"{out}\nprimary mean IC {s['primary']['mean_ic']:.4f}, "
            f"HAC t {s['primary']['hac_t']:.2f}, verdict "
            f"{'PASS' if s['verdict']['pass'] else 'FAIL'}"
        )
    elif a.cmd == "freeze":
        from .freeze import write_freeze

        print(write_freeze(Path(a.config), a.dev_run))
    elif a.cmd == "reproduce":
        from .pipeline import run
        from .reproduce import compare_runs

        old = Path(a.run)
        split = json.loads((old / "summary.json").read_text())["split"]
        new = run(a.config, split, note=f"reproduction of {old.name}")
        diffs = compare_runs(old, new)
        print(
            f"{new}\n{diffs['n_compared']} scalar fields compared, "
            f"{len(diffs['mismatches'])} mismatches"
        )
        for k in diffs["mismatches"][:20]:
            print("  ", k)
        return 1 if diffs["mismatches"] else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
