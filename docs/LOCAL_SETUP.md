# Local setup notes

Notes for running this project on a Mac whose Desktop is synced by iCloud Drive. Other machines can
ignore this file.

## Keep the Python environment outside iCloud

iCloud Drive marks dot-folders (such as `.venv`) as hidden, and Python then skips the `.pth` file of
the editable install. A plain `venv/` inside the synced folder collects sync-conflict copies (files
ending in ` 2`) whenever packages change. Both failures were measured in an earlier project.

Use an environment outside the synced folder:

```sh
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/csi500-reversal-turnover"
uv sync --all-extras
```

A shell hook can set this variable only inside the project folder. Shells that do not run the
hook (some IDEs, cron, CI) need the variable set explicitly.

## Keep data readable offline

`data/` and `runs/` are git-ignored but sit inside the synced folder. With "Optimize Mac Storage"
on, iCloud can evict them, and every read then waits for a download. Either set Finder's "Keep
Downloaded" on the project folder, or put the data elsewhere:

```sh
export REVTURN_DATA_DIR="$HOME/Data/csi500-reversal-turnover"
```

`REVTURN_DATA_DIR` replaces `data_dir` from the config. `runs_dir` stays as configured.

## BaoStock etiquette

BaoStock allows 50,000 requests a day and no concurrent connections. `revturn download` keeps one
session and is resumable: it skips files that already exist, so an interrupted download can be
restarted. A full download took 2 h 25 min on 2026-10-27 (1,711 requests).
