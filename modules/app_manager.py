"""
modules/app_manager.py
======================
Launch, close, install and uninstall Windows applications.

  * launch    — resolves common app aliases, then tries `start`, direct exe,
                and PATH lookup.
  * close     — terminates processes by (fuzzy) name via psutil / taskkill.
  * install   — uses winget (Windows Package Manager).
  * uninstall — uses winget, falling back to registry-discovered uninstall
                strings.

All operations are defensive: they never raise to the caller, returning a short
status string instead.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Dict, List, Optional

try:
    import psutil  # type: ignore
    _PSUTIL = True
except Exception:  # pragma: no cover
    psutil = None  # type: ignore
    _PSUTIL = False

IS_WINDOWS = platform.system() == "Windows"

# Friendly name -> executable / start target.
APP_ALIASES: Dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "settings": "ms-settings:",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe",
    "spotify": "spotify.exe",
    "discord": "discord.exe",
    "vscode": "code.exe",
    "vs code": "code.exe",
    "visual studio code": "code.exe",
    "steam": "steam.exe",
}

# winget package IDs for reliable installs.
WINGET_IDS: Dict[str, str] = {
    "chrome": "Google.Chrome",
    "google chrome": "Google.Chrome",
    "firefox": "Mozilla.Firefox",
    "vlc": "VideoLAN.VLC",
    "spotify": "Spotify.Spotify",
    "discord": "Discord.Discord",
    "zoom": "Zoom.Zoom",
    "vscode": "Microsoft.VisualStudioCode",
    "vs code": "Microsoft.VisualStudioCode",
    "visual studio code": "Microsoft.VisualStudioCode",
    "git": "Git.Git",
    "python": "Python.Python.3.12",
    "node": "OpenJS.NodeJS",
    "nodejs": "OpenJS.NodeJS",
    "7zip": "7zip.7zip",
    "notepad++": "Notepad++.Notepad++",
    "obs": "OBSProject.OBSStudio",
    "steam": "Valve.Steam",
    "telegram": "Telegram.TelegramDesktop",
    "brave": "Brave.Brave",
}


class AppManager:
    def __init__(self) -> None:
        self.is_windows = IS_WINDOWS
        self.has_winget = shutil.which("winget") is not None

    # ------------------------------------------------------------------ #
    def _resolve_target(self, name: str) -> str:
        key = name.strip().lower()
        return APP_ALIASES.get(key, name.strip())

    # ------------------------------------------------------------------ #
    # Launch
    # ------------------------------------------------------------------ #
    def launch(self, name: str) -> str:
        if not name:
            return "Which app should I launch?"
        target = self._resolve_target(name)

        if not self.is_windows:
            # Best-effort on non-Windows (dev environments).
            try:
                subprocess.Popen([target])
                return f"Launched {name}."
            except Exception:
                # try `open`/`xdg-open`
                opener = "open" if platform.system() == "Darwin" else "xdg-open"
                try:
                    subprocess.Popen([opener, target])
                    return f"Launched {name}."
                except Exception as exc:
                    return f"Couldn't launch {name}: {exc}"

        # Windows: `start` handles URIs, aliases and PATH resolution well.
        try:
            if target.endswith(":") or target.startswith("ms-"):
                os.startfile(target)  # type: ignore[attr-defined]
                return f"Opened {name}."
            # Use shell `start` so it searches App Paths registry entries.
            subprocess.Popen(f'start "" "{target}"', shell=True)
            return f"Launched {name}."
        except Exception:
            pass

        # Direct attempt.
        try:
            subprocess.Popen([target])
            return f"Launched {name}."
        except Exception as exc:
            return f"Couldn't find or launch {name}: {exc}"

    # ------------------------------------------------------------------ #
    # Close
    # ------------------------------------------------------------------ #
    def close(self, name: str) -> str:
        if not name:
            return "Which app should I close?"
        target = self._resolve_target(name)
        exe = os.path.basename(target).lower()
        base = exe[:-4] if exe.endswith(".exe") else exe

        killed = 0
        if _PSUTIL:
            for p in psutil.process_iter(["name"]):
                try:
                    pname = (p.info.get("name") or "").lower()
                    if base and (base in pname or pname in base or pname == exe):
                        p.terminate()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if killed:
                # Give them a moment, then force-kill stragglers.
                gone, alive = psutil.wait_procs(
                    [p for p in psutil.process_iter() if False], timeout=0.1
                )
                return f"Closed {killed} '{name}' process(es)."

        # Fallback to taskkill on Windows.
        if self.is_windows and exe:
            try:
                subprocess.run(
                    ["taskkill", "/IM", exe if exe.endswith(".exe") else exe + ".exe", "/F"],
                    check=True, capture_output=True, timeout=10,
                )
                return f"Closed {name}."
            except subprocess.CalledProcessError:
                return f"No running instance of {name} found."
            except Exception as exc:
                return f"Couldn't close {name}: {exc}"

        return f"No running instance of {name} found."

    # ------------------------------------------------------------------ #
    # Install
    # ------------------------------------------------------------------ #
    def install(self, name: str) -> str:
        if not name:
            return "What should I install?"
        if not self.has_winget:
            return ("winget isn't available on this system, so I can't install "
                    f"{name} automatically. Install App Installer from the Microsoft Store.")

        key = name.strip().lower()
        pkg_id = WINGET_IDS.get(key, name.strip())
        cmd = [
            "winget", "install", "-e", "--id", pkg_id,
            "--accept-source-agreements", "--accept-package-agreements",
            "--silent",
        ]
        # If we only have a free-text name (not a known ID), search by name.
        if pkg_id == name.strip() and key not in WINGET_IDS:
            cmd = [
                "winget", "install", name.strip(),
                "--accept-source-agreements", "--accept-package-agreements",
                "--silent",
            ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0 or "successfully installed" in out.lower():
                return f"Installed {name}."
            if "no package found" in out.lower() or "no applicable" in out.lower():
                return f"Couldn't find a package matching '{name}' in winget."
            return f"Install of {name} finished with code {proc.returncode}. {out.strip()[:200]}"
        except subprocess.TimeoutExpired:
            return f"Install of {name} is taking a while; it may still be running in the background."
        except Exception as exc:
            return f"Install failed: {exc}"

    # ------------------------------------------------------------------ #
    # Uninstall
    # ------------------------------------------------------------------ #
    def uninstall(self, name: str) -> str:
        if not name:
            return "What should I uninstall?"
        if self.has_winget:
            key = name.strip().lower()
            pkg_id = WINGET_IDS.get(key)
            cmd = ["winget", "uninstall", "-e",
                   "--id", pkg_id] if pkg_id else ["winget", "uninstall", name.strip()]
            cmd += ["--silent", "--accept-source-agreements"]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                out = (proc.stdout or "") + (proc.stderr or "")
                if proc.returncode == 0 or "successfully uninstalled" in out.lower():
                    return f"Uninstalled {name}."
                if "no installed package" in out.lower():
                    return f"'{name}' doesn't appear to be installed."
                return f"Uninstall finished with code {proc.returncode}. {out.strip()[:200]}"
            except subprocess.TimeoutExpired:
                return f"Uninstall of {name} is still running in the background."
            except Exception as exc:
                return f"Uninstall failed: {exc}"

        return ("winget isn't available, so I can't uninstall automatically. "
                "Use Settings > Apps to remove it.")

    # ------------------------------------------------------------------ #
    def list_installed(self, limit: int = 40) -> str:
        if not self.has_winget:
            return "winget isn't available to list installed apps."
        try:
            proc = subprocess.run(
                ["winget", "list", "--accept-source-agreements"],
                capture_output=True, text=True, timeout=60,
            )
            lines = (proc.stdout or "").strip().splitlines()
            return "\n".join(lines[:limit])
        except Exception as exc:
            return f"Couldn't list apps: {exc}"
