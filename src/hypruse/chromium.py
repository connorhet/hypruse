"""Telling Chromium-based apps (Electron, CEF, the browsers) apart.

They matter because they behave unlike the toolkits the rest of hypruse is
built around:

- They expose an accessibility tree only when started for it: the
  ACCESSIBILITY_ENABLED=1 environment variable (or the bus's IsEnabled, which
  a screen reader sets) connects them to the accessibility bus, and the
  --force-renderer-accessibility switch fills in the page content. Neither
  can be turned on later, so launch() adds both.
- They drop keys sent to a window that does not have keyboard focus, so a
  background keyboard path cannot reach them.

An app is recognised by its files, not its name: Chromium's binary sits next
to icudtl.dat and its .pak resource bundles, whatever the app is called. Most
are started through a shell wrapper (/usr/bin/code, /usr/bin/obsidian), so a
script is followed to the binaries it names, two levels deep. A wrapper that
builds its path at run time (Discord's) is not resolved; the running process
still is, by its executable.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path

A11Y_ENV = "ACCESSIBILITY_ENABLED=1"
A11Y_SWITCH = "--force-renderer-accessibility"

_PAKS = ("resources.pak", "chrome_100_percent.pak")
_SCRIPT_MAX = 256 * 1024
# absolute paths, and bare electronNN commands (Arch's shared Electron builds)
_NAMED = re.compile(r"(/[\w.+/-]+|\belectron\d*\b)")


def _is_chromium_dir(d: Path) -> bool:
    return (d / "icudtl.dat").is_file() and any((d / p).is_file() for p in _PAKS)


def is_chromium_binary(path: str | Path) -> bool:
    """Whether this file is a Chromium-based executable (or sits in a
    Chromium app's tree, like VS Code's bin/code beside its build)."""
    real = Path(os.path.realpath(path))
    return any(_is_chromium_dir(d) for d in (real.parent, real.parent.parent))


def is_chromium_pid(pid: int | None) -> bool:
    """Whether a running process is a Chromium-based app, by its executable."""
    if not pid:
        return False
    try:
        return is_chromium_binary(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        return False


_ASSIGN = re.compile(r"^\s*(\w+)=[\"']?([\w./+-]+)[\"']?\s*$", re.M)
_LAUNCHER_MAX = 2 * 1024 * 1024
_ELF_PATH = re.compile(rb"(/(?:usr|opt)/[\w.+/-]+)\x00")


def _expand(script: str) -> str:
    """The script with its plain `name=value` assignments substituted
    (`exec /usr/lib/${name}/electron` names a real path only then)."""
    for var, value in _ASSIGN.findall(script):
        script = script.replace("${" + var + "}", value)
        script = re.sub(rf"\${var}\b", lambda _m, v=value: v, script)
    return script


def _resolves_to_chromium(path: Path, depth: int) -> bool:
    if is_chromium_binary(path):
        return True
    if depth <= 0:
        return False
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(_LAUNCHER_MAX if size <= _LAUNCHER_MAX else _SCRIPT_MAX)
    except OSError:
        return False
    if head.startswith(b"\x7fELF"):
        # a small native launcher (Arch's /usr/bin/chromium) names its binary
        names = [m.decode("utf-8", "replace") for m in _ELF_PATH.findall(head)]
    elif head.startswith(b"#!"):
        names = _NAMED.findall(_expand(head[:_SCRIPT_MAX].decode("utf-8", "replace")))
    else:
        return False
    for name in dict.fromkeys(names):
        found = name if name.startswith("/") else shutil.which(name)
        if found and os.path.isfile(found) and _resolves_to_chromium(Path(found), depth - 1):
            return True
    return False


def is_chromium_command(argv0: str) -> bool:
    """Whether running this program starts a Chromium-based app."""
    found = shutil.which(argv0)
    return bool(found) and _resolves_to_chromium(Path(found), 2)


# a command launch() can rewrite safely: one program and its arguments, no
# shell syntax around them
_SHELL = re.compile(r"[;&|<>`$(){}\n]")


def with_accessibility(command: str) -> str | None:
    """`command` rewritten to start a Chromium-based app with its
    accessibility tree (the environment variable before it, the switch right
    after the program, ahead of a `--` that would make it a file name), or
    None when it is not one or is more than a plain program with arguments."""
    if _SHELL.search(command):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    i = 0
    if argv[:1] == ["env"]:
        i = 1
    while i < len(argv) and re.match(r"^[A-Za-z_]\w*=", argv[i]):
        i += 1
    if i >= len(argv) or not is_chromium_command(argv[i]):
        return None
    if A11Y_SWITCH in argv:
        return None
    head = argv[1:i] if argv[:1] == ["env"] else argv[:i]
    out = ["env", *head]
    if A11Y_ENV not in head:
        out.append(A11Y_ENV)
    out += [argv[i], A11Y_SWITCH, *argv[i + 1:]]
    return shlex.join(out)
