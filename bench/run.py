"""Run the hypruse benchmark: each task is set up on the agent's own workspaces, handed to a
separate Claude Code run that has nothing but hypruse's tools, and judged by the app's own
state. The person at the desk keeps working: the agent runs under HYPRUSE_BACKGROUND=1, and
the browser tasks use a throwaway Chromium that only the benchmark's agent can reach.

    uv run python bench/run.py [--model M] [--only id,id] [--budget 1.0]
                               [--extension ~/hypruse-browser/dist]

Writes bench/results/<time>/report.md and results.json. Costs real API money: each task is
one `claude -p` run, capped by --budget dollars.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))

from tasks import TASKS, TEARDOWN  # noqa: E402

from hypruse.browser import BrowserClient  # noqa: E402

HYPRUSE = str(REPO / ".venv" / "bin" / "hypruse")
# how Claude Code says the account cannot run anything more for now
LIMIT = re.compile(r"(session|usage|rate) limit|limit reached|resets \d", re.I)
RULES = """You are working on a Linux desktop (Hyprland) through the hypruse tools only. A person
is using this computer at the same time: never take over their screen, keyboard focus or
workspace. Do the task, check that it took effect, then reply with one short sentence saying
what you did. If you cannot do it, say why."""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def hypr(*args: str) -> Any:
    return json.loads(subprocess.run(["hyprctl", "-j", *args], capture_output=True,
                                     text=True, check=True).stdout)


def seat() -> tuple[Any, ...]:
    monitors = hypr("monitors")
    return (hypr("activewindow").get("address"),
            tuple(m["activeWorkspace"]["id"] for m in monitors))


def electron() -> str:
    """An Electron to run the app fixture with: `electron`, else the newest of
    the versioned builds Arch installs (electron43, ...)."""
    names = ["electron"] + sorted(
        {p.name for d in os.environ.get("PATH", "").split(":") if os.path.isdir(d)
         for p in Path(d).glob("electron[0-9]*")},
        key=lambda n: int(n.removeprefix("electron")), reverse=True)
    for name in names:
        if shutil.which(name):
            return name
    raise RuntimeError("no Electron found for the app fixture (install electron)")


class Env:
    """The desktop a run uses: its directory, the web fixture, the throwaway browser."""

    def __init__(self, run_dir: Path, extension: Path):
        self.run_dir = run_dir
        self.extension = extension
        self.web_log = run_dir / "web-events.ndjson"
        self.headline = f"Harbour bridge reopens after {int(time.time()) % 97 + 3} days"
        self.browser_dir = run_dir / "browser"
        self.port_a, self.port_b = free_port(), free_port()
        self.origin_a = f"http://127.0.0.1:{self.port_a}"
        self.procs: list[subprocess.Popen] = []
        self.windows: list[str] = []
        self.chromium: str | None = None
        # the agent has no tool to open a file, so a screenshot must come back as an image
        self.agent_env = {"HYPRUSE_BACKGROUND": "1", "HYPRUSE_BROWSER_DIR": str(self.browser_dir),
                          "HYPRUSE_SCREENSHOT_MODE": "image"}

    # every helper launches through hypruse itself, onto the agent's workspaces
    def _launch(self, *command: str, wait: int = 15) -> str:
        env = dict(os.environ, HYPRUSE_BACKGROUND="1")
        out = subprocess.run([HYPRUSE, "launch", "--wait", str(wait), "--json", "--", *command],
                             capture_output=True, text=True, env=env)
        try:
            result = json.loads(out.stdout)["result"]
            address = result["address"]
        except (ValueError, KeyError, TypeError):
            raise RuntimeError(
                f"launch failed: {out.stdout.strip()} {out.stderr.strip()}") from None
        self.windows.append(address)
        return address

    def close(self, address: str) -> None:
        subprocess.run([HYPRUSE, "hypr", "close_window", address], capture_output=True,
                       env=dict(os.environ, HYPRUSE_BACKGROUND="1"))
        if address in self.windows:
            self.windows.remove(address)

    def launch_form(self, state: Path, agree: bool) -> str:
        args = [str(state)] + (["agree"] if agree else [])
        address = self._launch("/usr/bin/python3", str(HERE / "fixtures" / "form.py"), *args)
        time.sleep(0.5)
        return address

    def launch_app(self, state: Path) -> str:
        # plain: hypruse's launch is what turns the app's accessibility tree on
        address = self._launch(electron(), str(HERE / "fixtures" / "electron"), str(state))
        time.sleep(1.0)
        return address

    def launch_files(self, folder: Path) -> str:
        return self._launch("nautilus", "--new-window", str(folder))

    def start_web(self) -> None:
        self.procs.append(subprocess.Popen(
            [sys.executable, str(HERE / "fixtures" / "web.py"), str(self.web_log),
             str(self.port_a), str(self.port_b), self.headline]))
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port_a), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("the web fixture did not start")

    def start_browser(self) -> None:
        profile = self.run_dir / "chromium-profile"
        hosts = profile / "NativeMessagingHosts"
        hosts.mkdir(parents=True)
        launcher = self.run_dir / "browser-host.sh"
        launcher.write_text(f"#!/bin/sh\nexport HYPRUSE_BROWSER_DIR={self.browser_dir}\n"
                            f"exec {HYPRUSE} browser-host \"$@\"\n")
        launcher.chmod(0o755)
        (hosts / "dev.hypruse.browser.json").write_text(json.dumps({
            "name": "dev.hypruse.browser", "description": "hypruse benchmark host",
            "path": str(launcher), "type": "stdio",
            "allowed_origins": ["chrome-extension://pcfjlemgcbbpppclachnobhnnnahpgoj/"]}))
        self.chromium = self._launch(
            "chromium", f"--user-data-dir={profile}", f"--load-extension={self.extension}",
            "--disable-features=DisableLoadExtensionCommandLineSwitch", "--no-first-run",
            "--no-default-browser-check", "--ozone-platform=wayland", "about:blank")
        sock = self.browser_dir / "browser.sock"
        for _ in range(100):
            if sock.exists():
                return
            time.sleep(0.2)
        raise RuntimeError("the throwaway browser's extension never connected")

    def browser_at(self, url: str) -> None:
        """One tab, on `url`, loaded: a task starts from a known page."""
        with BrowserClient(self.browser_dir / "browser.sock", timeout=10) as c:
            tab = c.open(url, "current")
            for t in c.tabs():
                if t["id"] != tab:
                    c.close_tab(t["id"])
            for _ in range(50):
                if any(t["id"] == tab and t["url"].startswith(url[:30]) for t in c.tabs()):
                    break
                time.sleep(0.1)
        time.sleep(0.8)

    def stop(self) -> None:
        for address in list(self.windows):
            self.close(address)
        for p in self.procs:
            p.terminate()


def journal_stats(path: Path) -> dict[str, Any]:
    calls, refused, warnings = [], 0, 0
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "tool" not in e:
                continue
            calls.append(e["tool"] + (f".{e['args'].get('action')}"
                                      if e["tool"] in ("browser", "hypr") and
                                      isinstance(e.get("args"), dict) else ""))
            refused += e.get("outcome") == "refused"
            warnings += "WARNING" in str(e.get("result", ""))
    return {"tool_calls": len(calls), "tools": calls, "refused": refused,
            "focus_warnings": warnings}


def run_agent(prompt: str, env: Env, journal: Path, args) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="hypruse-bench-", dir="/tmp"))  # no CLAUDE.md above it
    config = work / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"hypruse": {
        "type": "stdio", "command": HYPRUSE, "args": [],
        "env": dict(env.agent_env, HYPRUSE_JOURNAL=str(journal))}}}))
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--tools", "",
           "--strict-mcp-config", "--mcp-config", str(config),
           "--allowedTools", "mcp__hypruse__*", "--permission-mode", "dontAsk",
           "--setting-sources", "", "--no-session-persistence",
           "--max-budget-usd", str(args.budget), "--append-system-prompt", RULES]
    if args.model:
        cmd += ["--model", args.model]
    started = time.time()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=work, timeout=args.timeout)
        data = json.loads(out.stdout)
    except subprocess.TimeoutExpired:
        data = {"result": "", "is_error": True, "subtype": "timeout"}
    except json.JSONDecodeError:
        data = {"result": out.stdout[-500:] + out.stderr[-500:], "is_error": True,
                "subtype": "bad output"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"answer": str(data.get("result") or ""), "error": bool(data.get("is_error")),
            "end": data.get("subtype"), "turns": data.get("num_turns"),
            "cost_usd": data.get("total_cost_usd"), "seconds": round(time.time() - started, 1)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--model", default="", help="the model the agent runs on (default: Claude's)")
    p.add_argument("--only", default="", help="comma-separated task ids")
    p.add_argument("--budget", type=float, default=1.0, help="dollars per task, at most")
    p.add_argument("--timeout", type=int, default=420, help="seconds per task, at most")
    p.add_argument("--extension", default=str(Path.home() / "hypruse-browser" / "dist"))
    args = p.parse_args()
    tasks = [t for t in TASKS if not args.only or t.id in args.only.split(",")]
    run_dir = HERE / "results" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    env = Env(run_dir, Path(args.extension).expanduser())
    results = []
    try:
        if any(t.kind == "web" for t in tasks):
            env.start_web()
            env.start_browser()
        for task in tasks:
            ctx: dict[str, Any] = {"task": task.id}
            print(f"[{task.id}] setting up", flush=True)
            try:
                task.setup(env, ctx)
            except Exception as exc:
                results.append({"id": task.id, "status": "ERROR", "passed": False,
                                "detail": f"setup failed: {exc}"})
                continue
            journal = run_dir / f"journal-{task.id}.ndjson"
            before = seat()
            agent = run_agent(task.prompt.format(**ctx), env, journal, args)
            moved = seat() != before
            time.sleep(0.5)
            passed, detail = task.check(env, ctx, agent["answer"])
            # an agent that ended in an error (a usage limit, a crash) did not attempt the task:
            # that is no verdict on hypruse, unless the task got done anyway
            status = "PASS" if passed else "ERROR" if agent["error"] else "FAIL"
            if status == "ERROR":
                detail = f"the agent did not run: {agent['answer'][:120]}"
            row = {"id": task.id, "status": status, "passed": passed, "detail": detail, **agent,
                   **journal_stats(journal), "seat_changed": moved}
            results.append(row)
            print(f"[{task.id}] {status} {detail} "
                  f"({agent['seconds']}s, {row['tool_calls']} calls, ${agent['cost_usd']})",
                  flush=True)
            if "window" in ctx:
                env.close(ctx["window"])
            if task.id in TEARDOWN:
                TEARDOWN[task.id](env, ctx)
            if status == "ERROR" and LIMIT.search(agent["answer"]):
                print("stopping: the account's usage limit is reached, so no further task can "
                      "run", flush=True)
                break
    finally:
        env.stop()
    write_report(run_dir, results, args)
    print(f"report: {run_dir / 'report.md'}")
    return 0


def write_report(run_dir: Path, results: list[dict[str, Any]], args) -> None:
    (run_dir / "results.json").write_text(json.dumps(results, indent=1))
    passed = sum(r["status"] == "PASS" for r in results)
    ran = sum(r["status"] != "ERROR" for r in results)
    errors = len(results) - ran
    cost = sum(r.get("cost_usd") or 0 for r in results)
    lines = [f"# hypruse benchmark, {run_dir.name}", "",
             f"**{passed} of {ran} passed**"
             + (f" ({errors} did not run: see ERROR rows)" if errors else "")
             + f", model {args.model or 'default'}, ${cost:.2f} total.", "",
             "| task | result | detail | calls | refused | focus warnings | seat changed | "
             "seconds | cost |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['status']} | "
                     f"{str(r['detail']).replace('|', '/')[:90]} | {r.get('tool_calls', '')} | "
                     f"{r.get('refused', '')} | {r.get('focus_warnings', '')} | "
                     f"{r.get('seat_changed', '')} | {r.get('seconds', '')} | "
                     f"{r.get('cost_usd') or 0:.3f} |")
    lines += ["", "`seat changed` compares the focused window and each monitor's workspace "
              "before and after a task; the person using the computer meanwhile changes it "
              "too, so read it with the focus warnings, which hypruse itself raises."]
    (run_dir / "report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
