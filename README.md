# myHealth

macOS CLI for importing Apple Health export data, summarizing local health metrics, and asking an AI model for strength-focused workout and recovery guidance.

## Install for Development

```bash
python3 -m pip install -e ".[test]"
```

If test dependencies are not installed, the CLI still runs with the base dependencies listed in `pyproject.toml`.

## Export Apple Health Data

On iPhone: Health app -> profile picture -> Export All Health Data. Copy the resulting ZIP to your Mac.

## Usage

The default AI provider is ChatGPT/Codex OAuth — sign in once with:

```bash
myhealth auth login --provider openai-codex
# If localhost callback fails:
myhealth auth login --provider openai-codex --manual
```

After that, plain `myhealth report` and `myhealth plan` Just Work. Equivalent to:

```bash
myhealth report --provider openai-codex --period 30d --days 7 --style balanced
```

Prefer your own OpenAI API key instead of OAuth? Set `OPENAI_API_KEY` and pass `--provider openai`:

```bash
export OPENAI_API_KEY="your_api_key"
myhealth report --provider openai --period 30d
```

Common commands:

```bash
myhealth import export.zip                  # one-time: load Apple Health export
myhealth report                              # 30d window, 7-day plan, OAuth
myhealth plan --days 14 --style aggressive   # custom plan length and style
myhealth plan --model gpt-5.5                # override the Codex model if needed
myhealth config set planning_style aggressive
myhealth config show
myhealth auth status
```

The app reads Apple Health export files and stores a SQLite cache locally in:

```text
~/Library/Application Support/myhealth/myhealth.sqlite
```

AI analysis sends a compact metrics summary to the configured model provider, not the raw Apple Health export XML.

Reports are written to `./reports/` by default.
