#!/usr/bin/env python3
"""
JARVIS self-updater
===================
Pulls the latest Jarvis code from the GitHub repository and applies it in place,
so the user can press one "Update" button in the UI instead of re-cloning.

Two strategies, tried in order:
  1. ``git pull`` — used when the install folder is a real git checkout.
  2. ZIP download — downloads the repo's default-branch zipball from GitHub and
     overwrites the code files in place. Works even when git is not installed
     (installer-based installs use this path).

Everything here uses only the Python standard library so an update can run even
if third-party packages are broken.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from typing import Callable, Optional

# --- Repository coordinates ------------------------------------------------- #
REPO_OWNER = "ptcbb4dpf5-oss"
REPO_NAME = "jarvis-ai-desktop"
REPO_BRANCH = "main"
ZIP_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{REPO_BRANCH}.zip"

# Files/folders we never overwrite or delete during an update — user data.
PROTECTED = {
    "config/settings.json",   # user's API keys & preferences
    "plugins",                # self-generated capabilities
    "screenshots",
    ".venv",
    ".git",
    "memory",
}

# Only these top-level items are refreshed from the download.
CODE_ITEMS = {"core", "modules", "ui", "main.py", "requirements.txt",
              "README.md", "run.bat", "setup.bat", "installer.py",
              "Jarvis-Installer.bat", "build_installer.bat", "VERSION"}

Logger = Callable[[str], None]


class Updater:
    """Applies in-place updates to the Jarvis installation."""

    def __init__(self, root: Optional[str] = None, log: Optional[Logger] = None):
        # Project root = parent of this file's folder (core/..).
        self.root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.log = log or (lambda m: print(m))

    # ------------------------------------------------------------------ #
    def current_version(self) -> str:
        vf = os.path.join(self.root, "VERSION")
        try:
            with open(vf, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except Exception:
            return "unknown"

    def _is_git_checkout(self) -> bool:
        return os.path.isdir(os.path.join(self.root, ".git")) and shutil.which("git") is not None

    # ------------------------------------------------------------------ #
    def update(self) -> dict:
        """Run an update. Returns {'ok': bool, 'changed': bool, 'message': str}."""
        try:
            if self._is_git_checkout():
                return self._update_via_git()
            return self._update_via_zip()
        except Exception as exc:  # never crash the app on a failed update
            return {"ok": False, "changed": False,
                    "message": f"Update failed: {exc}"}

    # ------------------------------------------------------------------ #
    def _update_via_git(self) -> dict:
        self.log("Updating via git pull...")
        try:
            subprocess.run(["git", "-C", self.root, "fetch", "--all"],
                           check=True, capture_output=True, text=True, timeout=120)
            before = subprocess.run(["git", "-C", self.root, "rev-parse", "HEAD"],
                                    capture_output=True, text=True).stdout.strip()
            res = subprocess.run(
                ["git", "-C", self.root, "pull", "--ff-only", "origin", REPO_BRANCH],
                capture_output=True, text=True, timeout=180,
            )
            after = subprocess.run(["git", "-C", self.root, "rev-parse", "HEAD"],
                                   capture_output=True, text=True).stdout.strip()
            if res.returncode != 0:
                # Fall back to ZIP if the pull cannot fast-forward.
                self.log("git pull failed, falling back to ZIP update...")
                return self._update_via_zip()
            changed = before != after
            msg = "Updated to the latest version." if changed else "Already up to date."
            return {"ok": True, "changed": changed, "message": msg}
        except Exception as exc:
            self.log(f"git update error: {exc}; falling back to ZIP.")
            return self._update_via_zip()

    # ------------------------------------------------------------------ #
    def _update_via_zip(self) -> dict:
        self.log("Downloading latest code from GitHub...")
        data = self._download(ZIP_URL)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            tmp = tempfile.mkdtemp(prefix="jarvis_update_")
            zf.extractall(tmp)
            # Zip contains a single top folder: <repo>-<branch>/
            entries = [d for d in os.listdir(tmp)
                       if os.path.isdir(os.path.join(tmp, d))]
            if not entries:
                shutil.rmtree(tmp, ignore_errors=True)
                return {"ok": False, "changed": False,
                        "message": "Downloaded archive was empty."}
            src_root = os.path.join(tmp, entries[0])

            changed = self._apply(src_root)
            shutil.rmtree(tmp, ignore_errors=True)

        msg = ("Updated to the latest version." if changed
               else "Already up to date.")
        return {"ok": True, "changed": changed, "message": msg}

    # ------------------------------------------------------------------ #
    def _apply(self, src_root: str) -> bool:
        """Copy refreshed code items from src_root into self.root. Returns True if anything changed."""
        changed = False
        # Back up existing code once, so a bad update can be rolled back manually.
        backup = os.path.join(self.root, f".backup_{time.strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(backup, exist_ok=True)

        for item in sorted(CODE_ITEMS):
            if item in PROTECTED:
                continue
            src = os.path.join(src_root, item)
            if not os.path.exists(src):
                continue
            dst = os.path.join(self.root, item)

            if self._same(src, dst):
                continue
            changed = True

            # Back up then replace.
            if os.path.exists(dst):
                try:
                    bdst = os.path.join(backup, item)
                    os.makedirs(os.path.dirname(bdst), exist_ok=True)
                    if os.path.isdir(dst):
                        shutil.copytree(dst, bdst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(dst, bdst)
                except Exception:
                    pass

            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dst) or self.root, exist_ok=True)
                shutil.copy2(src, dst)
            self.log(f"Updated {item}")

        if not changed:
            shutil.rmtree(backup, ignore_errors=True)
        return changed

    # ------------------------------------------------------------------ #
    @staticmethod
    def _same(src: str, dst: str) -> bool:
        if not os.path.exists(dst):
            return False
        if os.path.isfile(src) and os.path.isfile(dst):
            try:
                with open(src, "rb") as a, open(dst, "rb") as b:
                    return a.read() == b.read()
            except Exception:
                return False
        return False  # for directories, always merge

    @staticmethod
    def _download(url: str, timeout: int = 120) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-Updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    # ------------------------------------------------------------------ #
    @staticmethod
    def restart() -> None:
        """Relaunch the current Python process to load the new code."""
        python = sys.executable
        os.execl(python, python, *sys.argv)


if __name__ == "__main__":
    up = Updater()
    print(f"Current version: {up.current_version()}")
    result = up.update()
    print(result["message"])
    if result.get("changed"):
        print("Restart Jarvis to load the update.")
