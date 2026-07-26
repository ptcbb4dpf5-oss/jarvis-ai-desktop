"""
ui/projects_panel.py
====================
The "Projects" workspace dialog — Jarvis's mini Abacus/Dyad.

From here the (non-technical) user can:
  * see all projects Jarvis has built (in ~/JarvisProjects),
  * create a brand-new project from a plain-English description,
  * run / test a project and read the output,
  * ask for a change ("iterate") on a project,
  * and use SELF-DEVELOPMENT: have Jarvis read its own code, build a safe
    improved duplicate of itself, self-test it, then review → Update or Discard.

All slow work (LLM calls, running code) happens on a background QThread so the
window never freezes. Nothing touches the live app until the user clicks
"Update (apply)".
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTextEdit, QLineEdit, QInputDialog,
    QMessageBox, QFrame, QTabWidget,
)

CYAN = "#00e5ff"
BG = "#0a1017"
FG = "#c8f6ff"
GREEN = "#38e08f"
AMBER = "#ffb84d"

_QSS = f"""
QDialog {{ background:{BG}; }}
QLabel {{ color:{FG}; font-family:Consolas,monospace; background:transparent; }}
QTabWidget::pane {{ border:1px solid rgba(0,229,255,90); border-radius:6px; top:-1px; }}
QTabBar::tab {{ background:rgba(0,229,255,20); color:{FG}; font-family:Consolas,monospace;
    padding:8px 20px; border:1px solid rgba(0,229,255,60); border-bottom:none;
    border-top-left-radius:6px; border-top-right-radius:6px; margin-right:2px; }}
QTabBar::tab:selected {{ background:rgba(0,229,255,60); color:#04222a; font-weight:bold; }}
QListWidget {{ background:#05252b; color:{FG}; border:1px solid rgba(0,229,255,120);
    border-radius:6px; font-family:Consolas,monospace; font-size:12px; padding:4px; }}
QListWidget::item:selected {{ background:rgba(0,229,255,80); color:#04222a; }}
QTextEdit {{ background:#05181d; color:#9be8ff; border:1px solid rgba(0,229,255,90);
    border-radius:6px; font-family:Consolas,monospace; font-size:11px; padding:8px; }}
QLineEdit {{ background:#05252b; color:{FG}; border:1px solid rgba(0,229,255,120);
    border-radius:5px; padding:7px 10px; font-family:Consolas,monospace; font-size:12px; }}
QLineEdit:focus {{ border:1px solid {CYAN}; }}
QPushButton {{ background:rgba(0,229,255,30); color:{CYAN}; border:1px solid {CYAN};
    border-radius:6px; padding:8px 16px; font-family:Consolas,monospace; font-size:12px; }}
QPushButton:hover {{ background:rgba(0,229,255,70); }}
QPushButton:disabled {{ color:#5f7f8f; border-color:#3a5560; background:rgba(0,229,255,10); }}
QPushButton#go {{ background:{CYAN}; color:#04222a; font-weight:bold; }}
QPushButton#go:hover {{ background:#66f0ff; }}
QPushButton#update {{ background:{GREEN}; color:#04140c; font-weight:bold; border-color:{GREEN}; }}
QPushButton#danger {{ color:#ff8080; border-color:#ff8080; }}
"""


class _Worker(QThread):
    """Runs one blocking callable off the UI thread and returns its string result."""
    done = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, fn: Callable[..., str], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit)
        except TypeError:
            # fn doesn't accept a progress callback.
            try:
                result = self._fn()
            except Exception as exc:  # noqa: BLE001
                result = f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001
            result = f"Error: {exc}"
        self.done.emit(result or "(done)")


class ProjectsPanel(QDialog):
    """Projects + Self-Development workspace."""

    def __init__(self, manager: Any, parent=None) -> None:
        super().__init__(parent)
        self.pm = manager
        self._worker: Optional[_Worker] = None
        self.setWindowTitle("JARVIS — Projects")
        self.setModal(False)
        self.setMinimumSize(720, 560)
        self.setStyleSheet(_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel("🛠  PROJECTS  &  SELF-DEVELOPMENT")
        title.setStyleSheet(f"color:{CYAN}; font-size:16px; font-weight:bold;")
        root.addWidget(title)

        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs, 1)
        self._build_projects_tab()
        self._build_selfdev_tab()

        # Shared status line.
        self.status = QLabel("Ready.")
        self.status.setStyleSheet("color:#8fbfcf; font-size:11px;")
        root.addWidget(self.status)

        self._refresh_list()

    # ================================================================== #
    # Projects tab
    # ================================================================== #
    def _build_projects_tab(self) -> None:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(6, 8, 6, 6)
        lay.setSpacing(10)

        # Left: list + new.
        left = QVBoxLayout()
        left.addWidget(QLabel("Your projects:"))
        self.proj_list = QListWidget()
        self.proj_list.itemSelectionChanged.connect(self._on_select_project)
        left.addWidget(self.proj_list, 1)
        newbtn = QPushButton("＋  New project")
        newbtn.setObjectName("go")
        newbtn.clicked.connect(self._new_project)
        left.addWidget(newbtn)
        reff = QPushButton("⟳  Refresh")
        reff.clicked.connect(self._refresh_list)
        left.addWidget(reff)
        lw = QWidget(); lw.setLayout(left); lw.setFixedWidth(240)
        lay.addWidget(lw)

        # Right: output + actions.
        right = QVBoxLayout()
        self.proj_out = QTextEdit()
        self.proj_out.setReadOnly(True)
        self.proj_out.setPlaceholderText(
            "Select a project, or create a new one.\n\n"
            "Tip: describe what you want in plain English — e.g. "
            "\"a snake game in python\" or \"a personal budget tracker webpage\".")
        right.addWidget(self.proj_out, 1)

        row = QHBoxLayout()
        self.run_btn = QPushButton("▶  Run / Test")
        self.run_btn.clicked.connect(self._run_project)
        self.iterate_btn = QPushButton("✎  Change it")
        self.iterate_btn.clicked.connect(self._iterate_project)
        self.open_btn = QPushButton("📁  Open folder")
        self.open_btn.clicked.connect(self._open_folder)
        for b in (self.run_btn, self.iterate_btn, self.open_btn):
            row.addWidget(b)
        right.addLayout(row)
        lay.addLayout(right, 1)

        self.tabs.addTab(page, "Projects")

    # ================================================================== #
    # Self-development tab
    # ================================================================== #
    def _build_selfdev_tab(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(6, 8, 6, 6)
        lay.setSpacing(8)

        blurb = QLabel(
            "Jarvis can read and improve his OWN code. He builds a safe copy, "
            "makes the change there, tests it, and only touches the real app "
            "when you press <b>Update</b>. A backup is always made first.")
        blurb.setStyleSheet("color:#8fbfcf; font-size:11px;")
        blurb.setWordWrap(True)
        lay.addWidget(blurb)

        self.selfdev_out = QTextEdit()
        self.selfdev_out.setReadOnly(True)
        self.selfdev_out.setPlaceholderText(
            "Ask Jarvis to understand himself or to improve himself…")
        lay.addWidget(self.selfdev_out, 1)

        # Change request box.
        crow = QHBoxLayout()
        self.change_box = QLineEdit()
        self.change_box.setPlaceholderText(
            "Describe an improvement to Jarvis  (e.g. \"make the orb pulse faster when thinking\")")
        crow.addWidget(self.change_box, 1)
        lay.addLayout(crow)

        row = QHBoxLayout()
        self.read_btn = QPushButton("🔍  Understand my code")
        self.read_btn.clicked.connect(self._read_own)
        self.dup_btn = QPushButton("⚙  Build improved duplicate")
        self.dup_btn.setObjectName("go")
        self.dup_btn.clicked.connect(self._make_duplicate)
        row.addWidget(self.read_btn)
        row.addWidget(self.dup_btn)
        lay.addLayout(row)

        # Pending review actions.
        prow = QHBoxLayout()
        self.update_btn = QPushButton("✅  Update (apply to live app)")
        self.update_btn.setObjectName("update")
        self.update_btn.clicked.connect(self._promote)
        self.discard_btn = QPushButton("🗑  Discard")
        self.discard_btn.setObjectName("danger")
        self.discard_btn.clicked.connect(self._discard)
        prow.addWidget(self.update_btn)
        prow.addWidget(self.discard_btn)
        lay.addLayout(prow)

        self.tabs.addTab(page, "Self-Development")
        self._refresh_pending_buttons()

    # ================================================================== #
    # Helpers
    # ================================================================== #
    def _busy(self, on: bool, msg: str = "") -> None:
        for b in (getattr(self, n, None) for n in (
                "run_btn", "iterate_btn", "read_btn", "dup_btn",
                "update_btn", "discard_btn")):
            if b is not None:
                b.setEnabled(not on)
        if on:
            self.status.setText(msg or "Working…  (this can take a moment)")
        else:
            self.status.setText(msg or "Ready.")
            self._refresh_pending_buttons()

    def _run_bg(self, fn: Callable[..., str], out: QTextEdit, busy_msg: str) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "Jarvis is still working on the last task.")
            return
        self._busy(True, busy_msg)
        out.append(f"\n> {busy_msg}\n")
        self._worker = _Worker(fn)
        self._worker.progress.connect(lambda m: out.append(m))
        self._worker.done.connect(lambda r: self._on_bg_done(r, out))
        self._worker.start()

    def _on_bg_done(self, result: str, out: QTextEdit) -> None:
        out.append(result)
        sb = out.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._busy(False)

    def _refresh_list(self) -> None:
        self.proj_list.clear()
        for name in self.pm.list_projects():
            if name.startswith("_jarvis_"):
                continue  # hide sandbox/backup internals
            self.proj_list.addItem(QListWidgetItem(name))

    def _selected(self) -> str:
        it = self.proj_list.currentItem()
        return it.text() if it else ""

    def _refresh_pending_buttons(self) -> None:
        pending = False
        try:
            pending = self.pm.has_pending()
        except Exception:
            pending = False
        self.update_btn.setEnabled(pending)
        self.discard_btn.setEnabled(pending)
        if pending:
            self.update_btn.setText("✅  Update (apply the reviewed change)")

    # ================================================================== #
    # Project actions
    # ================================================================== #
    def _on_select_project(self) -> None:
        name = self._selected()
        if not name:
            return
        data = self.pm.read_project(name)
        if "error" in data:
            self.proj_out.setPlainText(data["error"])
            return
        tree = "\n".join(f"  • {t}" for t in data.get("tree", []))
        self.proj_out.setPlainText(f"PROJECT: {name}\n\nFiles:\n{tree}")

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if not ok or not name.strip():
            return
        desc, ok = QInputDialog.getMultiLineText(
            self, "Describe it", "What should this project do? (plain English)")
        if not ok or not desc.strip():
            return
        n, d = name.strip(), desc.strip()
        self.proj_out.clear()
        self._run_bg(lambda prog: self.pm.create_project(n, d, prog),
                     self.proj_out, f"Building '{n}'…")
        self._worker.done.connect(lambda _r: self._refresh_list())

    def _run_project(self) -> None:
        name = self._selected()
        if not name:
            QMessageBox.information(self, "Pick a project", "Select a project first.")
            return
        self._run_bg(lambda: self.pm.run_project(name), self.proj_out,
                     f"Running '{name}'…")

    def _iterate_project(self) -> None:
        name = self._selected()
        if not name:
            QMessageBox.information(self, "Pick a project", "Select a project first.")
            return
        change, ok = QInputDialog.getMultiLineText(
            self, "Change it", f"What should I change in '{name}'?")
        if not ok or not change.strip():
            return
        c = change.strip()
        self._run_bg(lambda: self.pm.iterate_project(name, c), self.proj_out,
                     f"Updating '{name}'…")

    def _open_folder(self) -> None:
        name = self._selected()
        path = self.pm.project_path(name) if name else self.pm.projects_dir
        self._reveal(path)

    def _reveal(self, path: str) -> None:
        try:
            import subprocess, sys
            if not os.path.exists(path):
                self.status.setText("Folder doesn't exist yet.")
                return
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self.status.setText(f"Couldn't open folder: {exc}")

    # ================================================================== #
    # Self-dev actions
    # ================================================================== #
    def _read_own(self) -> None:
        q = self.change_box.text().strip()
        self._run_bg(lambda: self.pm.read_own_code(q), self.selfdev_out,
                     "Reading my own code…")

    def _make_duplicate(self) -> None:
        change = self.change_box.text().strip()
        if not change:
            QMessageBox.information(
                self, "Describe the change",
                "Type what you'd like me to improve about myself first.")
            return
        self._run_bg(lambda: self.pm.create_self_duplicate(change), self.selfdev_out,
                     "Building a safe duplicate and applying the change…")

    def _promote(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Apply to live app?")
        box.setText("Apply the reviewed change to the REAL Jarvis?\n\n"
                    "A full backup is made first, and you can restart to load it.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self._run_bg(lambda: self.pm.promote_pending(), self.selfdev_out,
                     "Applying to the live app (with backup)…")

    def _discard(self) -> None:
        self._run_bg(lambda: self.pm.discard_pending(), self.selfdev_out,
                     "Discarding pending changes…")
