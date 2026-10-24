"""The benchmark's tasks. Each sets the desktop up, says what a person would say, and is
judged by what the app itself holds afterwards: the form's state file, the web pages' event
log, or the file system. Nothing here asks hypruse whether it worked."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Ctx = dict[str, Any]


@dataclass(frozen=True)
class Task:
    id: str
    kind: str  # "form", "app", "web" or "files": which fixture it needs
    prompt: str  # formatted with the context setup returns
    setup: Callable[[Any, Ctx], None]
    check: Callable[[Any, Ctx, str], tuple[bool, str]]


# --- the form -----------------------------------------------------------------


def form_setup(agree: bool = False) -> Callable[[Any, Ctx], None]:
    def setup(env, ctx):
        ctx["state"] = env.run_dir / f"form-{ctx['task']}.json"
        ctx["window"] = env.launch_form(ctx["state"], agree)
    return setup


def form_check(**want: Any) -> Callable[[Any, Ctx, str], tuple[bool, str]]:
    def check(env, ctx, answer):
        try:
            state = json.loads(Path(ctx["state"]).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"no form state: {exc}"
        wrong = {k: state.get(k) for k, v in want.items() if state.get(k) != v}
        return not wrong, "ok" if not wrong else f"form holds {wrong}, wanted {want}"
    return check


# --- the Electron app -------------------------------------------------------------


def app_setup(env, ctx):
    ctx["state"] = env.run_dir / f"app-{ctx['task']}.json"
    ctx["window"] = env.launch_app(ctx["state"])


# --- the web pages ----------------------------------------------------------------


def web_setup(path: str) -> Callable[[Any, Ctx], None]:
    def setup(env, ctx):
        ctx["since"] = time.time()
        env.browser_at(f"{env.origin_a}{path}")
    return setup


def events(env, ctx, name: str) -> list[dict[str, Any]]:
    out = []
    if env.web_log.exists():
        for line in env.web_log.read_text().splitlines():
            event = json.loads(line)
            if event.get("event") == name and event.get("at", 0) >= ctx["since"]:
                out.append(event)
    return out


def search_check(env, ctx, answer):
    qs = [e.get("q", "") for e in events(env, ctx, "search")]
    return ("lamp" in [q.strip().lower() for q in qs]), f"searches seen: {qs}"


def second_result_check(env, ctx, answer):
    seen = [e.get("n") for e in events(env, ctx, "product")]
    return seen == [2], f"products opened: {seen}"


def pay_check(env, ctx, answer):
    names = [e.get("name") for e in events(env, ctx, "pay")]
    return names == ["Ada Lovelace"], f"payments: {names}"


def apply_setup(env, ctx):
    web_setup("/careers")(env, ctx)
    resume = env.run_dir / "resume-jordan-lee.pdf"
    resume.write_bytes(b"%PDF-1.4\n% benchmark resume\n" + bytes(range(256)) * 64)
    ctx["resume"] = resume


def apply_check(env, ctx, answer):
    import hashlib
    sent = events(env, ctx, "apply")
    want = hashlib.sha256(Path(ctx["resume"]).read_bytes()).hexdigest()
    ok = len(sent) == 1 and sent[0].get("sha256") == want
    return ok, f"applications: {[(e.get('name'), e.get('size')) for e in sent]}"


def news_setup(env, ctx):
    ctx["since"] = time.time()
    ctx["url"] = f"{env.origin_a}/news"
    env.browser_at(f"{env.origin_a}/")  # the page to read is not open yet


def news_check(env, ctx, answer):
    said = answer.split("ANSWER:", 1)[1].strip() if "ANSWER:" in answer else ""
    return env.headline.lower() in said.lower(), f"answered {said[:80]!r}"


# --- the Files app ------------------------------------------------------------------


def files_setup(env, ctx):
    folder = env.run_dir / "Reports"
    folder.mkdir(exist_ok=True)
    for name in ("report-draft.txt", "budget.ods", "notes.md"):
        (folder / name).write_text(name)
    ctx["dir"] = folder
    ctx["window"] = env.launch_files(folder)


def files_check(env, ctx, answer):
    names = sorted(p.name for p in Path(ctx["dir"]).iterdir())
    want = sorted(["report-final.txt", "budget.ods", "notes.md"])
    return names == want, f"folder holds {names}"


def files_teardown(env, ctx):
    shutil.rmtree(ctx["dir"], ignore_errors=True)


FORM = "In the window titled 'Bench form', "
APP = "In the window titled 'Bench app', "
TASKS = [
    Task("form_volume", "form", FORM + "set the Volume slider to 60.",
         form_setup(), form_check(volume=60.0)),
    Task("form_colour", "form", FORM + "choose Blue in the Colour drop-down.",
         form_setup(), form_check(colour="Blue")),
    Task("form_tab", "form", FORM + "switch to the Advanced tab.",
         form_setup(), form_check(tab="Advanced")),
    Task("form_agree", "form", FORM + "tick the Agree check box.",
         form_setup(), form_check(agree=True)),
    Task("form_title", "form", FORM + "change the Title to 'Quarterly report'.",
         form_setup(), form_check(title="Quarterly report")),
    Task("form_multi", "form",
         FORM + "set Count to 12, choose Green as the colour, and make sure Agree is unticked.",
         form_setup(agree=True), form_check(count=12, colour="Green", agree=False)),
    Task("app_tab", "app", APP + "switch to the Privacy tab.", app_setup,
         form_check(tab="Privacy")),
    Task("app_theme", "app", APP + "set the Theme to Dark.", app_setup,
         form_check(theme="Dark")),
    Task("app_save", "app", APP + "turn Notifications on, turn Sync off, and save.",
         app_setup, form_check(saved={"notifications": True, "sync": False, "theme": "Light"})),
    Task("web_search", "web", "In my browser, the 'Bench shop' tab is open. Search the shop "
         "for lamp.", web_setup("/"), search_check),
    Task("web_second_result", "web", "In my browser, the 'Bench shop results' tab shows search "
         "results. Open the second result.", web_setup("/results?q=lamp"), second_result_check),
    Task("web_frame_pay", "web", "In my browser, the 'Bench checkout' tab is open. Enter Ada "
         "Lovelace as the cardholder name in the payment form and press Pay.",
         web_setup("/checkout"), pay_check),
    Task("web_upload", "web", "In my browser, the 'Bench careers' tab is open. Attach my résumé, "
         "{resume}, and submit the application.", apply_setup, apply_check),
    Task("web_read", "web", "Open {url} in my browser without disturbing me, and tell me its "
         "headline. End your reply with ANSWER: followed by the headline.",
         news_setup, news_check),
    Task("files_rename", "files", "The Files app has a window open on the folder {dir}. In it, "
         "rename report-draft.txt to report-final.txt.", files_setup, files_check),
]
TEARDOWN = {"files_rename": files_teardown}
