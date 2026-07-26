#!/usr/bin/env python3
"""
JARVIS Settings dialog
======================
An Iron Man-styled modal dialog for editing ``config/settings.json`` from inside
the app — primarily so the user can paste in LLM API keys without hand-editing
JSON. Grouped into tabs: LLM, Voice, UI, Browser.

The dialog receives the live config dict, lets the user edit it, and returns the
updated dict on Save. Persisting to disk is handled by the caller.
"""

from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QFormLayout, QVBoxLayout, QHBoxLayout,
    QLineEdit, QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox, QPushButton,
    QLabel, QToolButton,
)

CYAN = "#00e5ff"
BG = "#0a1017"
FG = "#c8f6ff"

_DIALOG_QSS = f"""
QDialog {{ background:{BG}; }}
QLabel {{ color:{FG}; font-family:Consolas,monospace; font-size:12px; background:transparent; }}
QTabWidget::pane {{ border:1px solid rgba(0,229,255,90); border-radius:6px; top:-1px; }}
QTabBar::tab {{ background:rgba(0,229,255,20); color:{FG}; font-family:Consolas,monospace;
    padding:8px 18px; border:1px solid rgba(0,229,255,60); border-bottom:none;
    border-top-left-radius:6px; border-top-right-radius:6px; margin-right:2px; }}
QTabBar::tab:selected {{ background:rgba(0,229,255,60); color:#04222a; font-weight:bold; }}
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background:#05252b; color:{FG}; border:1px solid rgba(0,229,255,120);
    border-radius:5px; padding:6px 8px; font-family:Consolas,monospace; font-size:12px; }}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border:1px solid {CYAN}; }}
QComboBox QAbstractItemView {{ background:#05252b; color:{FG}; selection-background-color:rgba(0,229,255,90); }}
QCheckBox {{ color:{FG}; font-family:Consolas,monospace; font-size:12px; }}
QCheckBox::indicator {{ width:16px; height:16px; border:1px solid {CYAN}; border-radius:3px; background:#05252b; }}
QCheckBox::indicator:checked {{ background:{CYAN}; }}
QPushButton {{ background:rgba(0,229,255,30); color:{CYAN}; border:1px solid {CYAN};
    border-radius:6px; padding:8px 20px; font-family:Consolas,monospace; font-size:12px; }}
QPushButton:hover {{ background:rgba(0,229,255,70); }}
QPushButton#save {{ background:{CYAN}; color:#04222a; font-weight:bold; }}
QPushButton#save:hover {{ background:#66f0ff; }}
QToolButton {{ background:transparent; color:{CYAN}; border:none; font-size:14px; }}
"""


class SettingsDialog(QDialog):
    """Modal settings editor. Call ``exec()``; on accept read ``self.result_config``."""

    def __init__(self, config: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("JARVIS — Settings")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(_DIALOG_QSS)
        # Work on a copy; only commit on Save.
        import copy
        self._config = copy.deepcopy(config)
        self.result_config: Dict[str, Any] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(12)

        title = QLabel("⚙  SETTINGS")
        title.setStyleSheet(f"color:{CYAN}; font-family:Consolas,monospace; font-size:16px; font-weight:bold;")
        root.addWidget(title)

        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs)

        self._build_llm_tab()
        self._build_voice_tab()
        self._build_ui_tab()
        self._build_browser_tab()

        # Buttons.
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("save")
        save.clicked.connect(self._on_save)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------ #
    def _build_llm_tab(self) -> None:
        llm = self._config.setdefault("llm", {})
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self.api_key = QLineEdit(llm.get("api_key", ""))
        self.api_key.setPlaceholderText("sk-...  (paste your API key here)")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        # Show/hide toggle.
        reveal = QToolButton()
        reveal.setText("👁")
        reveal.setCheckable(True)
        reveal.setCursor(Qt.CursorShape.PointingHandCursor)
        reveal.toggled.connect(
            lambda on: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(reveal)
        key_wrap = QWidget()
        key_wrap.setLayout(key_row)

        self.api_key_env = QLineEdit(llm.get("api_key_env", "OPENAI_API_KEY"))
        self.base_url = QLineEdit(llm.get("base_url", ""))
        self.base_url.setPlaceholderText("optional — e.g. http://localhost:11434/v1 for Ollama")
        self.model = QLineEdit(llm.get("model", "gpt-4o-mini"))

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(float(llm.get("temperature", 0.6)))

        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(64, 32000)
        self.max_tokens.setSingleStep(64)
        self.max_tokens.setValue(int(llm.get("max_tokens", 800)))

        form.addRow("API Key:", key_wrap)
        form.addRow("API Key Env Var:", self.api_key_env)
        form.addRow("Base URL:", self.base_url)
        form.addRow("Model:", self.model)
        form.addRow("Temperature:", self.temperature)
        form.addRow("Max Tokens:", self.max_tokens)

        hint = QLabel("Tip: paste your key above, or leave it blank and set the\n"
                      "environment variable named in \"API Key Env Var\".")
        hint.setStyleSheet(f"color:#7fb8c8; font-size:10px;")
        form.addRow(hint)

        self.tabs.addTab(w, "LLM")

    # ------------------------------------------------------------------ #
    def _build_voice_tab(self) -> None:
        v = self._config.setdefault("voice", {})
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self.voice_enabled = QCheckBox("Enable voice features")
        self.voice_enabled.setChecked(bool(v.get("enabled", True)))
        self.speak_responses = QCheckBox("Speak responses aloud")
        self.speak_responses.setChecked(bool(v.get("speak_responses", True)))
        self.require_wake_word = QCheckBox("Require wake word before responding")
        self.require_wake_word.setChecked(bool(v.get("require_wake_word", False)))

        self.wake_word = QLineEdit(v.get("wake_word", "jarvis"))

        self.rate = QSpinBox()
        self.rate.setRange(80, 350)
        self.rate.setValue(int(v.get("rate", 178)))

        self.volume = QDoubleSpinBox()
        self.volume.setRange(0.0, 1.0)
        self.volume.setSingleStep(0.05)
        self.volume.setValue(float(v.get("volume", 1.0)))

        self.stt_engine = QComboBox()
        self.stt_engine.addItems(["google", "sphinx"])
        idx = self.stt_engine.findText(v.get("stt_engine", "google"))
        if idx >= 0:
            self.stt_engine.setCurrentIndex(idx)

        self.voice_hint = QLineEdit(v.get("voice_hint", ""))
        self.voice_hint.setPlaceholderText("optional — part of a system voice name")

        form.addRow(self.voice_enabled)
        form.addRow(self.speak_responses)
        form.addRow(self.require_wake_word)
        form.addRow("Wake word:", self.wake_word)
        form.addRow("Speech rate:", self.rate)
        form.addRow("Volume:", self.volume)
        form.addRow("STT engine:", self.stt_engine)
        form.addRow("Voice hint:", self.voice_hint)

        self.tabs.addTab(w, "Voice")

    # ------------------------------------------------------------------ #
    def _build_ui_tab(self) -> None:
        u = self._config.setdefault("ui", {})
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self.start_fullscreen = QCheckBox("Start in fullscreen")
        self.start_fullscreen.setChecked(bool(u.get("start_fullscreen", False)))
        self.show_hud_on_start = QCheckBox("Show HUD panels on start")
        self.show_hud_on_start.setChecked(bool(u.get("show_hud_on_start", False)))

        self.win_width = QSpinBox()
        self.win_width.setRange(600, 7680)
        self.win_width.setValue(int(u.get("width", 1100)))
        self.win_height = QSpinBox()
        self.win_height.setRange(400, 4320)
        self.win_height.setValue(int(u.get("height", 720)))
        self.fps = QSpinBox()
        self.fps.setRange(15, 144)
        self.fps.setValue(int(u.get("fps", 60)))

        form.addRow(self.start_fullscreen)
        form.addRow(self.show_hud_on_start)
        form.addRow("Window width:", self.win_width)
        form.addRow("Window height:", self.win_height)
        form.addRow("Animation FPS:", self.fps)

        note = QLabel("Some UI changes take effect after a restart.")
        note.setStyleSheet("color:#7fb8c8; font-size:10px;")
        form.addRow(note)

        self.tabs.addTab(w, "UI")

    # ------------------------------------------------------------------ #
    def _build_browser_tab(self) -> None:
        b = self._config.setdefault("browser", {})
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self.headless = QCheckBox("Run browser in headless mode (invisible)")
        self.headless.setChecked(bool(b.get("headless", False)))
        self.search_engine = QLineEdit(b.get("search_engine", "https://www.google.com/search?q="))

        form.addRow(self.headless)
        form.addRow("Search engine URL:", self.search_engine)

        self.tabs.addTab(w, "Browser")

    # ------------------------------------------------------------------ #
    def _on_save(self) -> None:
        llm = self._config.setdefault("llm", {})
        llm["api_key"] = self.api_key.text().strip()
        llm["api_key_env"] = self.api_key_env.text().strip() or "OPENAI_API_KEY"
        llm["base_url"] = self.base_url.text().strip()
        llm["model"] = self.model.text().strip() or "gpt-4o-mini"
        llm["temperature"] = float(self.temperature.value())
        llm["max_tokens"] = int(self.max_tokens.value())

        v = self._config.setdefault("voice", {})
        v["enabled"] = self.voice_enabled.isChecked()
        v["speak_responses"] = self.speak_responses.isChecked()
        v["require_wake_word"] = self.require_wake_word.isChecked()
        v["wake_word"] = self.wake_word.text().strip() or "jarvis"
        v["rate"] = int(self.rate.value())
        v["volume"] = float(self.volume.value())
        v["stt_engine"] = self.stt_engine.currentText()
        v["voice_hint"] = self.voice_hint.text().strip()

        u = self._config.setdefault("ui", {})
        u["start_fullscreen"] = self.start_fullscreen.isChecked()
        u["show_hud_on_start"] = self.show_hud_on_start.isChecked()
        u["width"] = int(self.win_width.value())
        u["height"] = int(self.win_height.value())
        u["fps"] = int(self.fps.value())

        b = self._config.setdefault("browser", {})
        b["headless"] = self.headless.isChecked()
        b["search_engine"] = self.search_engine.text().strip() or "https://www.google.com/search?q="

        self.result_config = self._config
        self.accept()
