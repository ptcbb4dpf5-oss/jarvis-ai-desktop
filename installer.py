#!/usr/bin/env python3
"""
JARVIS Installer
================
A tiny self-contained installer with a folder picker. It downloads the latest
Jarvis code from GitHub into a location you choose, then (optionally) creates a
Python virtual environment and installs all dependencies — so you never have to
clone anything by hand.

Build it into a double-clickable ``JarvisInstaller.exe`` with ``build_installer.bat``,
or just run ``python installer.py``.

Only the Python standard library is used, so this runs on a clean machine that
only has Python installed.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import urllib.request
import zipfile

REPO_OWNER = "ptcbb4dpf5-oss"
REPO_NAME = "jarvis-ai-desktop"
REPO_BRANCH = "main"
ZIP_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{REPO_BRANCH}.zip"

try:
    import tkinter as tk
    from tkinter import filedialog, ttk
    HAVE_TK = True
except Exception:
    HAVE_TK = False


# --------------------------------------------------------------------------- #
def default_location() -> str:
    """Default to the user's Desktop if it exists, else home."""
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    return desktop if os.path.isdir(desktop) else home


def download_zip(log) -> bytes:
    log("Downloading Jarvis from GitHub…")
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "Jarvis-Installer"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()
    log(f"Downloaded {len(data) // 1024} KB.")
    return data


def extract_zip(data: bytes, dest_parent: str, log) -> str:
    log("Extracting files…")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        tmp_names = zf.namelist()
        top = tmp_names[0].split("/")[0] if tmp_names else f"{REPO_NAME}-{REPO_BRANCH}"
        zf.extractall(dest_parent)
    extracted = os.path.join(dest_parent, top)
    final = os.path.join(dest_parent, "Jarvis")
    # Rename <repo>-main -> Jarvis for a clean folder name.
    if os.path.abspath(extracted) != os.path.abspath(final):
        if os.path.exists(final):
            log(f"Note: '{final}' already exists — installing into it.")
            _merge(extracted, final)
            import shutil
            shutil.rmtree(extracted, ignore_errors=True)
        else:
            os.rename(extracted, final)
    log(f"Installed to: {final}")
    return final


def _merge(src: str, dst: str) -> None:
    import shutil
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(target, f))


def run_setup(folder: str, log) -> None:
    """Create a venv and install all dependencies.

    Uses ``sys.executable`` (the exact interpreter running this installer) rather
    than a bare ``python`` on PATH. This is essential on a brand-new PC where
    Python was just installed in the same session — PATH isn't refreshed yet, so
    a bare ``python``/``setup.bat`` would fail or hit the Store stub.
    """
    is_win = os.name == "nt"
    venv_dir = os.path.join(folder, ".venv")

    log("Creating virtual environment (.venv)…")
    r = subprocess.run([sys.executable, "-m", "venv", venv_dir],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("venv creation failed; installing into the base interpreter instead.")
        venv_py = sys.executable
    else:
        venv_py = os.path.join(venv_dir, "Scripts" if is_win else "bin",
                               "python.exe" if is_win else "python")
        if not os.path.exists(venv_py):
            venv_py = sys.executable

    def pip(*args) -> int:
        return subprocess.run([venv_py, "-m", "pip", *args]).returncode

    log("Upgrading pip…")
    pip("install", "--upgrade", "pip", "setuptools", "wheel")

    req = os.path.join(folder, "requirements.txt")
    log("Installing dependencies (this can take several minutes)…")
    rc = pip("install", "-r", req)
    if rc != 0:
        log("Some dependencies failed. Retrying PyAudio via pipwin…")
        pip("install", "pipwin")
        subprocess.run([venv_py, "-m", "pipwin", "install", "pyaudio"])

    log("Downloading the Playwright browser (Chromium)…")
    subprocess.run([venv_py, "-m", "playwright", "install", "chromium"])

    log("Dependencies installed.")


def make_shortcut(folder: str, log) -> None:
    """Create a desktop shortcut to run.bat on Windows."""
    if os.name != "nt":
        return
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        lnk = os.path.join(desktop, "Jarvis.lnk")
        target = os.path.join(folder, "run.bat")
        ps = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
            f"$s.TargetPath='{target}';$s.WorkingDirectory='{folder}';"
            f"$s.IconLocation='%SystemRoot%\\System32\\SHELL32.dll,44';$s.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
        log("Created desktop shortcut: Jarvis")
    except Exception as exc:
        log(f"Could not create shortcut: {exc}")


def do_install(dest_parent: str, install_deps: bool, log, done) -> None:
    try:
        os.makedirs(dest_parent, exist_ok=True)
        data = download_zip(log)
        folder = extract_zip(data, dest_parent, log)
        if install_deps:
            run_setup(folder, log)
        make_shortcut(folder, log)
        log("")
        log("✔ Installation complete!")
        log(f"Launch Jarvis by running run.bat in:\n{folder}")
        if not install_deps:
            log("You chose to skip dependencies — run setup.bat before first launch.")
        done(True, folder)
    except Exception as exc:
        log(f"[X] Installation failed: {exc}")
        done(False, "")


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
def run_gui() -> int:
    root = tk.Tk()
    root.title("Jarvis Installer")
    root.configure(bg="#0a1017")
    root.geometry("620x460")
    root.resizable(False, False)

    cyan = "#00e5ff"
    fg = "#c8f6ff"

    tk.Label(root, text="J A R V I S", bg="#0a1017", fg=cyan,
             font=("Consolas", 22, "bold")).pack(pady=(18, 2))
    tk.Label(root, text="Install to your PC", bg="#0a1017", fg=fg,
             font=("Consolas", 10)).pack()

    path_var = tk.StringVar(value=default_location())
    deps_var = tk.BooleanVar(value=True)

    row = tk.Frame(root, bg="#0a1017")
    row.pack(fill="x", padx=24, pady=(18, 4))
    tk.Label(row, text="Install location:", bg="#0a1017", fg=fg,
             font=("Consolas", 9)).pack(anchor="w")
    inner = tk.Frame(row, bg="#0a1017")
    inner.pack(fill="x", pady=4)
    entry = tk.Entry(inner, textvariable=path_var, bg="#05252b", fg=fg,
                     insertbackground=cyan, relief="flat", font=("Consolas", 9))
    entry.pack(side="left", fill="x", expand=True, ipady=5)

    def browse():
        chosen = filedialog.askdirectory(initialdir=path_var.get(),
                                         title="Choose where to install Jarvis")
        if chosen:
            path_var.set(chosen)

    tk.Button(inner, text="Browse", command=browse, bg="#05252b", fg=cyan,
              activebackground="#0a3a44", relief="flat",
              font=("Consolas", 9)).pack(side="right", padx=(8, 0), ipadx=6, ipady=3)

    tk.Checkbutton(root, text="Install Python dependencies now (recommended)",
                   variable=deps_var, bg="#0a1017", fg=fg, selectcolor="#05252b",
                   activebackground="#0a1017", activeforeground=cyan,
                   font=("Consolas", 9)).pack(anchor="w", padx=24, pady=(2, 6))

    log_box = tk.Text(root, height=10, bg="#05161a", fg=fg, relief="flat",
                      font=("Consolas", 8), wrap="word")
    log_box.pack(fill="both", expand=True, padx=24, pady=(4, 6))

    def log(msg: str):
        log_box.insert("end", msg + "\n")
        log_box.see("end")
        root.update_idletasks()

    btns = tk.Frame(root, bg="#0a1017")
    btns.pack(fill="x", padx=24, pady=(0, 14))

    install_btn = tk.Button(btns, text="Install Jarvis", bg=cyan, fg="#04222a",
                            activebackground="#66f0ff", relief="flat",
                            font=("Consolas", 11, "bold"))
    install_btn.pack(side="right", ipadx=14, ipady=6)

    def on_done(ok: bool, folder: str):
        install_btn.config(state="normal", text="Install Jarvis")
        if ok:
            install_btn.config(text="Done ✔")

    def start():
        install_btn.config(state="disabled", text="Installing…")
        dest = path_var.get().strip() or default_location()
        threading.Thread(
            target=do_install,
            args=(dest, deps_var.get(), log, on_done),
            daemon=True,
        ).start()

    install_btn.config(command=start)
    root.mainloop()
    return 0


def run_cli() -> int:
    print("Jarvis Installer (console mode)")
    dest = input(f"Install location [{default_location()}]: ").strip() or default_location()
    deps = (input("Install dependencies now? [Y/n]: ").strip().lower() or "y") == "y"
    done_state = {}
    do_install(dest, deps, print,
               lambda ok, folder: done_state.update(ok=ok, folder=folder))
    return 0 if done_state.get("ok") else 1


def main() -> int:
    if HAVE_TK:
        try:
            return run_gui()
        except Exception as exc:
            print(f"GUI unavailable ({exc}); using console mode.")
    return run_cli()


if __name__ == "__main__":
    sys.exit(main())
