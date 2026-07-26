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
import json
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


class SafeBoot:
    """Crash-recovery guard — rolls Jarvis back to the last safe version.

    How it works (all stdlib, so it runs even if third-party packages break):

    * Right before the UI starts, ``mark_boot_start()`` drops a small flag file
      (``config/.boot_incomplete``).
    * A few seconds after the window is shown, ``mark_boot_ok()`` deletes it —
      meaning "this version booted fine".
    * If Jarvis crashes during startup the flag is left behind. On the NEXT
      launch ``needs_recovery()`` sees the leftover flag and Jarvis restores the
      code files from the most recent safe backup (recorded by
      ``ProjectManager.promote_pending`` in ``config/last_safe.json``), so a bad
      self-update never leaves the user with a broken app.
    """

    FLAG_NAME = ".boot_incomplete"
    POINTER_NAME = "last_safe.json"

    def __init__(self, root: Optional[str] = None, log: Optional[Logger] = None):
        self.root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.log = log or (lambda m: print(m))
        self.cfg_dir = os.path.join(self.root, "config")

    # -- flag file ------------------------------------------------------- #
    def _flag_path(self) -> str:
        return os.path.join(self.cfg_dir, self.FLAG_NAME)

    def mark_boot_start(self) -> None:
        try:
            os.makedirs(self.cfg_dir, exist_ok=True)
            with open(self._flag_path(), "w", encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as exc:
            self.log(f"SafeBoot: could not write boot flag: {exc}")

    def mark_boot_ok(self) -> None:
        try:
            p = self._flag_path()
            if os.path.exists(p):
                os.remove(p)
        except Exception as exc:
            self.log(f"SafeBoot: could not clear boot flag: {exc}")

    def needs_recovery(self) -> bool:
        return os.path.exists(self._flag_path())

    # -- safe-point pointer --------------------------------------------- #
    def _pointer_path(self) -> str:
        return os.path.join(self.cfg_dir, self.POINTER_NAME)

    def record_safe_point(self, backup_dir: str) -> None:
        try:
            os.makedirs(self.cfg_dir, exist_ok=True)
            with open(self._pointer_path(), "w", encoding="utf-8") as fh:
                json.dump({"backup": backup_dir, "time": time.strftime("%Y-%m-%d %H:%M:%S")},
                          fh, indent=2)
        except Exception as exc:
            self.log(f"SafeBoot: could not record safe point: {exc}")

    def _last_safe_backup(self) -> Optional[str]:
        try:
            with open(self._pointer_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            backup = data.get("backup")
            if backup and os.path.isdir(backup):
                return backup
        except Exception:
            pass
        return None

    # -- recovery -------------------------------------------------------- #
    def recover(self) -> dict:
        """Restore code files from the last safe backup. Returns a result dict."""
        backup = self._last_safe_backup()
        if not backup:
            # Nothing to roll back to — clear the flag so we don't loop.
            self.mark_boot_ok()
            return {"ok": False, "restored": 0,
                    "message": "Previous start didn't finish, but no safe "
                               "version was on record to roll back to."}
        restored = 0
        skip_dirs = {".git", "__pycache__", ".venv", "venv", "screenshots"}
        skip_rel = {os.path.join("config", "settings.json")}
        try:
            for dirpath, dirs, fnames in os.walk(backup):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith("_jarvis_")]
                for fn in fnames:
                    if fn.endswith(".pyc"):
                        continue
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, backup)
                    if rel in skip_rel or rel.startswith("plugins" + os.sep):
                        continue
                    dst = os.path.join(self.root, rel)
                    try:
                        os.makedirs(os.path.dirname(dst) or self.root, exist_ok=True)
                        shutil.copy2(full, dst)
                        restored += 1
                    except Exception as exc:
                        self.log(f"SafeBoot: restore failed {rel}: {exc}")
        except Exception as exc:
            self.mark_boot_ok()
            return {"ok": False, "restored": restored,
                    "message": f"Roll back hit an error: {exc}"}
        # Clear the flag so the recovered version gets a clean boot attempt.
        self.mark_boot_ok()
        return {"ok": True, "restored": restored,
                "message": f"The last update wouldn't start, so Jarvis rolled "
                           f"back to the previous safe version "
                           f"({restored} file(s) restored)."}


if __name__ == "__main__":
    up = Updater()
    print(f"Current version: {up.current_version()}")
    result = up.update()
    print(result["message"])
    if result.get("changed"):
        print("Restart Jarvis to load the update.")
