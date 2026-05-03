# myHealth

Local-only macOS CLI for analyzing Apple Health export data and generating strength-focused workout and recovery plans.

## Install for Development

```bash
python3 -m pip install -e ".[test]"
```

If test dependencies are not installed, the CLI still runs with the base dependencies listed in `pyproject.toml`.

## Export Apple Health Data

On iPhone: Health app -> profile picture -> Export All Health Data. Copy the resulting ZIP to your Mac.

## Usage

```bash
myhealth import export.zip
myhealth report --period 30d
myhealth plan --days 7 --style balanced
myhealth config set planning_style aggressive
myhealth config show
```

Data stays local. The app reads Apple Health export files and stores a SQLite cache in:

```text
~/Library/Application Support/myhealth/myhealth.sqlite
```

Reports are written to `./reports/` by default.
