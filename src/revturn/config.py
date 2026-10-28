"""Run configuration. Every research choice fixed by docs/RESEARCH_PROTOCOL.md lives here."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Split:
    start: str  # first signal date (inclusive)
    end: str  # last signal date (inclusive)
    label_end: str  # every label window must end on or before this date


@dataclass(frozen=True)
class Config:
    data_dir: Path
    runs_dir: Path
    splits: dict[str, Split]
    oos_splits: tuple[str, ...] = ("oos_a", "oos_b")
    horizons: tuple[int, ...] = (1, 5, 20)
    lookback: int = 20
    min_traded_days: int = 15
    min_listed_days: int = 60
    min_names: int = 100
    mad_k: float = 5.0
    min_industry_size: int = 5
    n_quantiles: int = 5
    cost_bps: float = 20.0
    stress_cost_bps: float = 40.0
    trading_days_per_year: int = 242
    capacity_participation: float = 0.05
    primary_signal: str = "RxT"
    primary_version: str = "neutral"
    primary_horizon: int = 20
    # Registered 2026-10-27 after the dev results and before any OOS data was opened
    # (EXPERIMENT_LOG E6): a labeled post-dev confirmatory test, judged by the OOS criteria only.
    confirmatory_secondary: dict | None = None
    stale_membership_window: tuple[str, str] = ("2022-12-12", "2023-03-17")
    # Found 2026-10-27 before any result (EXPERIMENT_LOG E3): all vendor-late rebalance windows.
    all_stale_membership_windows: tuple[tuple[str, str], ...] = ()
    comparable_subperiod: tuple[str, str] = ("2023-01-01", "2024-12-31")
    pass_thresholds: dict[str, float] = field(
        default_factory=lambda: {
            "min_mean_ic": 0.02,
            "min_hac_t": 2.0,
            "max_bh_q": 0.05,
            "min_positive_years": 5,
            "min_monotonicity": 0.9,
            "min_ir": 0.5,
        }
    )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    def to_json(self) -> dict:
        out = asdict(self)
        out["data_dir"] = str(self.data_dir)
        out["runs_dir"] = str(self.runs_dir)
        return out


def load_config(path: str | Path) -> Config:
    """Read a JSON config. Relative paths resolve against the project root.

    `REVTURN_DATA_DIR` overrides `data_dir`, so data can live outside a synced folder.
    """
    raw = json.loads(Path(path).read_text())
    data_dir = Path(os.environ.get("REVTURN_DATA_DIR", raw.pop("data_dir", "data")))
    runs_dir = Path(raw.pop("runs_dir", "runs"))
    splits = {k: Split(**v) for k, v in raw.pop("splits").items()}
    for key in ("horizons", "oos_splits", "stale_membership_window", "comparable_subperiod"):
        if key in raw:
            raw[key] = tuple(raw[key])
    if "all_stale_membership_windows" in raw:
        raw["all_stale_membership_windows"] = tuple(
            tuple(w) for w in raw["all_stale_membership_windows"]
        )
    return Config(
        data_dir=data_dir if data_dir.is_absolute() else PROJECT_ROOT / data_dir,
        runs_dir=runs_dir if runs_dir.is_absolute() else PROJECT_ROOT / runs_dir,
        splits=splits,
        **raw,
    )
