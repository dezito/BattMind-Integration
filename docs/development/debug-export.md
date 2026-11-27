# Debug export

For contributors and coding agents. Linked from `AGENTS.md`, which keeps the
rules for what the export may contain.

- `management/debug_export.py`, service `battmind.export_debug`, diagnostic
  button `export_debug`, and a discreet "Problem or error?" section at the
  bottom of the last column of the dashboard's Settings view (not a badge on
  the overview). Writes `<config>/battmind_debug/<prefix>_debug_<ts>.json.gz`
  (newest 5 kept) plus a persistent notification with a signed 24 h download
  link and a prefilled `.github/ISSUE_TEMPLATE/bug_report.yml` link.
- To analyse a file handed in by the user, run from the repo root:
  `PYTHONPATH=../ha-dev/windows %LOCALAPPDATA%/ha-dev-tests/venv/Scripts/python.exe tests/debug_replay.py <file> [failed|planning|<index>]`
  (default: failed, else planning, else last). Older exports may
  lack `planning_job`/`price_data` and replay less
  faithfully.
