# The hypruse benchmark

Fifteen everyday tasks, each handed to a separate Claude Code run that has nothing but
hypruse's tools, and judged by what the app itself holds afterwards. It answers "how often
does a task actually get done", which unit tests and hand-built probes cannot.

```sh
uv run python bench/run.py                       # every task, Claude's default model
uv run python bench/run.py --only form_tab,web_upload --model claude-sonnet-5-5
```

Each run writes `bench/results/<time>/report.md` (a table) and `results.json` (every field,
including the agent's final reply), plus one hypruse journal per task. It costs real API
money: every task is one `claude -p` run, capped by `--budget` dollars (default 1.00).

## What runs where

- **The agent** is `claude -p` in an empty directory under `/tmp` (so no `CLAUDE.md` reaches
  it), with no built-in tools (`--tools ""`), no settings (`--setting-sources ""`), and only
  this repository's hypruse as its MCP server. It sees the task text and nothing else.
- **hypruse** runs under `HYPRUSE_BACKGROUND=1`: the agent works on the agent's workspaces and
  may not take over the screen, so the benchmark can run while someone uses the computer.
- **The form** is a GTK 3 window (`fixtures/form.py`) that writes its state to a file on every
  change.
- **The app** is a small Electron settings window (`fixtures/electron`), run with the
  `electron` on the PATH (else the newest `electronNN`). It is launched plainly through
  hypruse's `launch`, which is what turns its accessibility tree on, and writes its state to
  a file on every change.
- **The web pages** (`fixtures/web.py`) are served on two local origins, so the payment form is
  a cross-origin frame, and report what was done in them to the server's event log.
- **The browser** is a throwaway Chromium with its own profile and the hypruse-browser
  extension (`--extension`, default `~/hypruse-browser/dist`). `HYPRUSE_BROWSER_DIR` gives it a
  bridge socket of its own, so the agent cannot reach the person's browser.
- **The Files task** works in a fresh folder in the run directory.

## The tasks

| id | what it asks | judged by |
|---|---|---|
| form_volume | set a slider to 60 | the form's state |
| form_colour | choose an item in a combo box | the form's state |
| form_tab | switch tabs | the form's state |
| form_agree | tick a check box | the form's state |
| form_title | replace a field's text | the form's state |
| form_multi | three changes, one of them unticking | the form's state |
| app_tab | switch tabs in an Electron app | the app's state |
| app_theme | choose an option in a `<select>` | the app's state |
| app_save | tick one box, untick another, press Save | what Save stored |
| web_search | search a shop | the page's search event |
| web_second_result | open the second of five results | which product page loaded |
| web_frame_pay | fill and submit a cross-origin payment frame | the frame's pay event |
| web_upload | attach a file and submit | the SHA-256 the page computed of what it got |
| web_read | open a page in the background and report its headline | the agent's answer |
| files_rename | rename a file in Nautilus | the folder on disk |

## Reading the report

`seat changed` compares the focused window and each monitor's workspace before and after a
task. The person using the computer changes it too, so read it alongside `focus warnings`,
which hypruse raises itself when an action moved the person's focus. `refused` counts calls a
guard turned down, which is often the agent learning its way round, not a failure.
