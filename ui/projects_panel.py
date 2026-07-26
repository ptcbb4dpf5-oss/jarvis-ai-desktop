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
            "This is how Jarvis upgrades <b>himself</b>. Tell him what you want "
            "changed, he plans it, builds it in a safe copy, you preview & test "
            "it, and only when you're happy does he apply it and restart. If a "
            "new version ever fails to start, Jarvis rolls back to the last safe "
            "version automatically.")
        blurb.setStyleSheet("color:#8fbfcf; font-size:11px;")
        blurb.setWordWrap(True)
        lay.addWidget(blurb)

        # Recommendation banner (connect more AIs / agents).
        self.reco_banner = QLabel("")
        self.reco_banner.setStyleSheet(
            f"color:{AMBER}; font-size:11px; background:rgba(255,184,77,18); "
            f"border:1px solid rgba(255,184,77,90); border-radius:6px; padding:8px 10px;")
        self.reco_banner.setWordWrap(True)
        self.reco_banner.setOpenExternalLinks(False)
        lay.addWidget(self.reco_banner)

        self.selfdev_out = QTextEdit()
        self.selfdev_out.setReadOnly(True)
        self.selfdev_out.setPlaceholderText(
            "Tell Jarvis what to change about himself, then follow the steps "
            "below: ① Plan → ② Build & preview → ③ Tweak (optional) → Test → "
            "Update & Restart.")
        lay.addWidget(self.selfdev_out, 1)

        # --- Change request box --------------------------------------- #
        self.change_box = QLineEdit()
        self.change_box.setPlaceholderText(
            "What do you want to change about Jarvis?  "
            "(e.g. \"make the orb pulse faster when thinking\")")
        lay.addWidget(self.change_box)

        # --- Step 1: plan / understand -------------------------------- #
        row1 = QHBoxLayout()
        self.plan_btn = QPushButton("①  Plan it")
        self.plan_btn.setObjectName("go")
        self.plan_btn.clicked.connect(self._plan)
        self.read_btn = QPushButton("🔍  Understand my code")
        self.read_btn.clicked.connect(self._read_own)
        row1.addWidget(self.plan_btn)
        row1.addWidget(self.read_btn)
        lay.addLayout(row1)

        # --- Step 2: build & preview ---------------------------------- #
        row2 = QHBoxLayout()
        self.build_btn = QPushButton("②  Build & preview")
        self.build_btn.setObjectName("go")
        self.build_btn.clicked.connect(self._build)
        self.preview_btn = QPushButton("👁  Preview changes")
        self.preview_btn.clicked.connect(self._preview)
        self.test_btn = QPushButton("🧪  Test it")
        self.test_btn.clicked.connect(self._test)
        row2.addWidget(self.build_btn)
        row2.addWidget(self.preview_btn)
        row2.addWidget(self.test_btn)
        lay.addLayout(row2)

        # --- Step 3: tweak (converse for more changes) ---------------- #
        self.tweak_box = QLineEdit()
        self.tweak_box.setPlaceholderText(
            "Not quite right? Describe a tweak and press ③ Tweak  "
            "(e.g. \"also make it brighter\")")
        lay.addWidget(self.tweak_box)
        row3 = QHBoxLayout()
        self.tweak_btn = QPushButton("③  Tweak the preview")
        self.tweak_btn.clicked.connect(self._tweak)
        row3.addWidget(self.tweak_btn)
        lay.addLayout(row3)

        # --- Final: apply + restart / discard ------------------------- #
        prow = QHBoxLayout()
        self.update_btn = QPushButton("✅  Update & Restart Jarvis")
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
        self._refresh_reco()

    # ================================================================== #
    # Helpers
    # ================================================================== #
    def _busy(self, on: bool, msg: str = "") -> None:
        for b in (getattr(self, n, None) for n in (
                "run_btn", "iterate_btn", "read_btn", "plan_btn", "build_btn",
                "preview_btn", "test_btn", "tweak_btn",
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
        # Preview/test/tweak/apply only make sense once a preview is built.
        for b in (getattr(self, n, None) for n in (
                "preview_btn", "test_btn", "tweak_btn", "update_btn", "discard_btn")):
            if b is not None:
                b.setEnabled(pending)

    def _refresh_reco(self) -> None:
        """Nudge the user to connect more AIs/agents so Jarvis gets smarter."""
        n = 0
        try:
            from core import providers as _pv
            llm = self.pm.brain.config.get("llm", {}) if hasattr(self.pm, "brain") else {}
            n = len(_pv.connected_providers(llm)) + len(_pv.connected_agents(llm))
        except Exception:
            n = 0
        if n == 0:
            self.reco_banner.setText(
                "💡  <b>Make Jarvis smarter:</b> no AI brains are connected yet. "
                "Open <b>Settings → AI Providers / Agents</b> and connect at least one "
                "(Groq, Google and Mistral are free) so Jarvis can plan and write "
                "his own upgrades.")
            self.reco_banner.setVisible(True)
        elif n < 2:
            self.reco_banner.setText(
                "💡  <b>Tip:</b> you have 1 AI brain connected. Connecting more "
                "AIs & coding agents (Settings → AI Providers / Agents) lets Jarvis "
                "cross-check ideas and handle harder self-upgrades and tasks.")
            self.reco_banner.setVisible(True)
        else:
            self.reco_banner.setText(
                f"🧠  {n} AI brains/agents connected. Add more anytime in "
                "Settings → AI Providers / Agents to boost what Jarvis can do.")
            self.reco_banner.setVisible(True)

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

    def _plan(self) -> None:
        change = self.change_box.text().strip()
        if not change:
            QMessageBox.information(
                self, "Describe the change",
                "First, type what you'd like me to improve about myself.")
            return
        self.selfdev_out.append(
            "\n──────── ①  PLANNING ────────\n"
            "Jarvis is working out the best way to do this…")
        self._run_bg(lambda: self.pm.plan_change(change), self.selfdev_out,
                     "Planning the change…")

    def _build(self) -> None:
        change = self.change_box.text().strip()
        if not change:
            QMessageBox.information(
                self, "Describe the change",
                "Type what you'd like me to improve about myself first "
                "(and ideally press ① Plan it before building).")
            return
        self.selfdev_out.append(
            "\n──────── ②  BUILD & PREVIEW ────────\n"
            "Building a safe copy and applying the change there…")
        self._run_bg(lambda prog: self.pm.build_pending(prog), self.selfdev_out,
                     "Building the preview (safe copy)…")

    def _preview(self) -> None:
        self.selfdev_out.append("\n──────── 👁  PREVIEW OF CHANGES ────────")
        self._run_bg(lambda: self.pm.preview_pending(), self.selfdev_out,
                     "Gathering the preview…")

    def _test(self) -> None:
        self.selfdev_out.append("\n──────── 🧪  TESTING ────────")
        self._run_bg(lambda: self.pm.test_pending(), self.selfdev_out,
                     "Testing the preview…")

    def _tweak(self) -> None:
        feedback = self.tweak_box.text().strip()
        if not feedback:
            QMessageBox.information(
                self, "Describe the tweak",
                "Type what else you'd like changed in the preview.")
            return
        if not self.pm.has_pending():
            QMessageBox.information(
                self, "Nothing to tweak yet",
                "Press ② Build & preview first, then you can tweak it.")
            return
        self.selfdev_out.append(f"\n──────── ③  TWEAK ────────\nApplying: {feedback}")
        self.tweak_box.clear()
        self._run_bg(lambda prog: self.pm.refine_pending(feedback, prog),
                     self.selfdev_out, "Applying your tweak to the preview…")

    def _promote(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Update & Restart Jarvis?")
        box.setText(
            "Apply the reviewed change to the REAL Jarvis and restart?\n\n"
            "• A full backup is made first (saved as the last safe version).\n"
            "• Jarvis will close and reopen to load the new code.\n"
            "• If the new version won't start, Jarvis auto-rolls back.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        # Apply on the UI thread (fast, file copies) then restart.
        try:
            result = self.pm.promote_pending()
        except Exception as exc:
            self.selfdev_out.append(f"\nUpdate failed: {exc}")
            return
        self.selfdev_out.append(f"\n{result}")
        self._refresh_pending_buttons()
        # Ask the main window to restart so the new code loads.
        parent = self.parent()
        restart = getattr(parent, "_restart_for_update", None)
        if callable(restart):
            self.status.setText("Restarting Jarvis to load your new version…")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(1200, restart)
        else:
            QMessageBox.information(
                self, "Restart needed",
                "Update applied. Please restart Jarvis to load the new version.")

    def _discard(self) -> None:
        self._run_bg(lambda: self.pm.discard_pending(), self.selfdev_out,
                     "Discarding pending changes…")
