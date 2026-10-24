"""Telling Chromium-based apps apart, against fake app trees: the conftest
stubs the live checks, which these tests restore over tmp_path."""

import importlib
import os

import pytest

from hypruse import chromium


@pytest.fixture
def real(monkeypatch):
    """The module's own checks, not conftest's stand-ins."""
    fresh = importlib.reload(chromium)
    yield fresh
    importlib.reload(chromium)


def _app(root, name="app"):
    d = root / name
    d.mkdir()
    (d / "icudtl.dat").write_bytes(b"")
    (d / "resources.pak").write_bytes(b"")
    exe = d / name
    exe.write_bytes(b"\x7fELF")
    exe.chmod(0o755)
    return exe


def _script(path, body):
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def test_a_binary_beside_chromiums_resources_is_one(real, tmp_path):
    assert real.is_chromium_binary(_app(tmp_path))
    plain = tmp_path / "plain"
    plain.write_bytes(b"\x7fELF")
    assert not real.is_chromium_binary(plain)


def test_a_wrapper_script_is_followed_to_its_binary(real, tmp_path, monkeypatch):
    exe = _app(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _script(bindir / "notes", f"exec {exe} /usr/lib/notes/app.asar \"$@\"\n")
    _script(bindir / "texted", "exec /usr/bin/true \"$@\"\n")
    monkeypatch.setenv("PATH", str(bindir))
    assert real.is_chromium_command("notes")
    assert not real.is_chromium_command("texted")


def test_plain_assignments_are_expanded(real, tmp_path, monkeypatch):
    exe = _app(tmp_path, "electron9")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _script(bindir / "electron9", f"name=electron9\nexec {tmp_path}/${{name}}/$name \"$@\"\n")
    monkeypatch.setenv("PATH", str(bindir))
    assert exe.exists() and real.is_chromium_command("electron9")


def test_a_native_launcher_names_its_binary(real, tmp_path, monkeypatch):
    # Arch's /usr/bin/chromium is a small ELF that execs /usr/lib/chromium/chromium
    monkeypatch.setattr(real, "_ELF_PATH", __import__("re").compile(
        rb"(" + str(tmp_path).encode() + rb"/[\w.+/-]+)\x00"))
    exe = _app(tmp_path, "browser")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    launcher = bindir / "browser"
    launcher.write_bytes(b"\x7fELF\x00junk\x00" + str(exe).encode() + b"\x00more")
    launcher.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert real.is_chromium_command("browser")


def test_the_command_is_rewritten_to_start_with_its_tree(monkeypatch):
    monkeypatch.setattr(chromium, "is_chromium_command", lambda argv0: argv0 == "code")
    w = chromium.with_accessibility
    assert w("code") == "env ACCESSIBILITY_ENABLED=1 code --force-renderer-accessibility"
    assert w("code '/tmp/my notes.md'") == (
        "env ACCESSIBILITY_ENABLED=1 code --force-renderer-accessibility '/tmp/my notes.md'")
    assert w("env LANG=C code") == (
        "env LANG=C ACCESSIBILITY_ENABLED=1 code --force-renderer-accessibility")
    assert w("FOO=1 code -n") == (
        "env FOO=1 ACCESSIBILITY_ENABLED=1 code --force-renderer-accessibility -n")


@pytest.mark.parametrize("command", [
    "nautilus",  # not Chromium
    "code; rm -rf ~",  # shell syntax: left as given
    "code $HOME",
    "sh -c 'code'",
    "code --force-renderer-accessibility",  # already asked for
    "code 'unterminated",
    "",
])
def test_anything_else_is_left_as_given(monkeypatch, command):
    monkeypatch.setattr(chromium, "is_chromium_command", lambda argv0: argv0 == "code")
    assert chromium.with_accessibility(command) is None


def test_a_running_process_is_told_by_its_executable(real, tmp_path, monkeypatch):
    exe = _app(tmp_path)
    monkeypatch.setattr(os, "readlink", lambda p: str(exe) if p == "/proc/7/exe" else "/x")
    assert real.is_chromium_pid(7)
    assert not real.is_chromium_pid(8)
    assert not real.is_chromium_pid(None)
