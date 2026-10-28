"""Freeze guard: out-of-sample runs need a recorded hash of the config and of the source code."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from .config import PROJECT_ROOT

SRC = PROJECT_ROOT / "src" / "revturn"


class FreezeError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_hash(src: Path = SRC) -> str:
    h = hashlib.sha256()
    for p in sorted(src.rglob("*.py")):
        h.update(p.relative_to(src).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def freeze_path(cfg_path: Path) -> Path:
    return Path(cfg_path).resolve().parent / "freeze.json"


def write_freeze(cfg_path: Path, dev_run: str, src: Path = SRC) -> Path:
    rec = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "config": Path(cfg_path).name,
        "config_sha256": sha256_file(Path(cfg_path)),
        "source_sha256": source_hash(src),
        "dev_run": dev_run,
    }
    out = freeze_path(cfg_path)
    if out.exists():
        raise FreezeError(f"{out} already exists; a freeze is written once")
    out.write_text(json.dumps(rec, indent=2) + "\n")
    return out


def check_freeze(cfg_path: Path, src: Path = SRC) -> dict:
    p = freeze_path(cfg_path)
    if not p.exists():
        raise FreezeError("no configs/freeze.json: run and log dev, then `revturn freeze` first")
    rec = json.loads(p.read_text())
    if rec["config_sha256"] != sha256_file(Path(cfg_path)):
        raise FreezeError("config changed after the freeze")
    if rec["source_sha256"] != source_hash(src):
        raise FreezeError("source code changed after the freeze")
    return rec
