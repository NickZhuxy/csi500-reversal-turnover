import json
import shutil

import pytest

from revturn.freeze import FreezeError, check_freeze, source_hash, write_freeze
from revturn.pipeline import run
from revturn.reproduce import compare_runs


def test_freeze_detects_config_and_source_changes(tmp_path, cfg_path):
    src = tmp_path / "src"
    shutil.copytree(__import__("revturn").__path__[0], src)
    with pytest.raises(FreezeError):
        check_freeze(cfg_path, src)
    write_freeze(cfg_path, "dev-x", src)
    assert check_freeze(cfg_path, src)["dev_run"] == "dev-x"
    with pytest.raises(FreezeError):
        write_freeze(cfg_path, "dev-y", src)  # written once
    (src / "factors.py").write_text((src / "factors.py").read_text() + "\n# edit\n")
    with pytest.raises(FreezeError, match="source"):
        check_freeze(cfg_path, src)
    cfg = json.loads(cfg_path.read_text())
    cfg["cost_bps"] = 10.0
    cfg_path.write_text(json.dumps(cfg))
    with pytest.raises(FreezeError, match="config"):
        check_freeze(cfg_path, src)


def test_oos_run_refused_without_freeze(panel, cfg_path):
    with pytest.raises(FreezeError):
        run(cfg_path, "oos_a", panel=panel)


def test_dev_run_end_to_end_and_reproduces(panel, cfg_path):
    out = run(cfg_path, "dev", panel=panel)
    s = json.loads((out / "summary.json").read_text())
    assert s["n_tests_in_family"] == 30
    assert set(s["verdict"]["checks"]) == {
        "P1_mean_ic",
        "P2_hac_t_and_bh",
        "P3_positive_years",
        "P4_monotonic",
        "P5_hedged_net",
    }
    assert s["source_sha256"] == source_hash()
    for f in ("ic_table.csv", "report.md", "figures/hedged_nav.png", "primary_daily.csv"):
        assert (out / f).exists()
    again = run(cfg_path, "dev", panel=panel)
    diff = compare_runs(out, again)
    assert diff["n_compared"] > 300 and diff["mismatches"] == []


def test_oos_run_allowed_after_freeze(panel, cfg_path):
    write_freeze(cfg_path, "dev-test")
    out = run(cfg_path, "oos_a", panel=panel)
    s = json.loads((out / "summary.json").read_text())
    assert s["freeze"]["dev_run"] == "dev-test"
    assert set(s["verdict"]["checks"]) == {"OOS_mean_ic_positive", "OOS_hedged_net_positive"}


def test_costs_hurt_both_legs_of_the_long_short(panel, cfg):
    from revturn.factors import processed_signals
    from revturn.pipeline import long_short_daily
    from revturn.universe import eligible, entry_tradable

    elig = eligible(panel, cfg)
    sig = processed_signals(panel, elig, cfg)[("REV20", "raw")]
    ls = long_short_daily(panel, cfg, sig, elig & entry_tradable(panel), "dev")
    g, n, s = (ls[f"ls_{k}"]["total_return"] for k in ("gross", "net", "stress"))
    assert s < n < g
