#!/usr/bin/env python3
"""
JARVIS v1 — Windows native desktop AI agent
===========================================
Entry point. Wires together the brain, memory, agent, capability modules and the
Iron Man orb UI.

Run:
    python main.py

Architecture
------------
  * Qt GUI thread  — owns the window, orb and HUD panels (never blocks).
  * AgentWorker    — a single persistent QThread that serialises all agent work
                     (LLM calls, browser automation, system control). Requests
                     are pushed via a thread-safe queue; results come back as Qt
                     signals so the UI updates safely.
  * VoiceListener / VoiceSpeaker — their own threads for mic + TTS.

This separation keeps the orb animation buttery-smooth regardless of how long an
LLM call or a browser action takes.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import traceback
from typing import Any, Dict, Optional

# Ensure project root is importable when launched from anywhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QFont, QKeySequence, QShortcut,
    QLinearGradient, QBrush,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLineEdit, QLabel, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QSizePolicy,
)

from core.brain import Brain
from core.memory import Memory
from core.agent import (
    Agent, STATE_IDLE, STATE_LISTENING, STATE_THINKING, STATE_SPEAKING, STATE_WORKING,
)
from modules.system_monitor import SystemMonitor
from modules.input_control import InputControl
from modules.app_manager import AppManager
from modules.browser import Browser
from modules.self_modify import SelfModifier

from ui.orb_widget import OrbWidget
from ui.hud_panel import HudPanel
from ui.voice_handler import VoiceListener, VoiceSpeaker


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config", "settings.json")


# --------------------------------------------------------------------------- #
def load_config() -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "llm": {
            "api_key_env": "OPENAI_API_KEY",
            "api_key": "",
            "base_url": "",
            "model": "gpt-4o-mini",
            "temperature": 0.6,
            "max_tokens": 800,
        },
        "voice": {
            "enabled": True,
            "wake_word": "jarvis",
            "require_wake_word": False,
            "speak_responses": True,
            "rate": 178,
            "volume": 1.0,
            "voice_hint": "",
            "language": "en-US",
            "stt_engine": "google",
        },
        "ui": {
            "start_fullscreen": False,
            "width": 1100,
            "height": 720,
            "fps": 60,
            "show_hud_on_start": False,
        },
        "plugins": {
            "hot_reload": True,
            "allow_unsafe_plugins": False,
        },
        "browser": {
            "headless": False,
            "search_engine": "https://www.google.com/search?q=",
        },
    }
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                user = json.load(fh)
            _deep_merge(defaults, user)
    except Exception as exc:
        print(f"[config] failed to load settings.json: {exc}")
    return defaults


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# --------------------------------------------------------------------------- #
class AgentWorker(QThread):
    """Serialises all agent work on one background thread."""

    reply_ready = pyqtSignal(str)
    state_changed = pyqtSignal(str)
    log_line = pyqtSignal(str)
    exit_requested = pyqtSignal()

    def __init__(self, agent: Agent, parent=None) -> None:
        super().__init__(parent)
        self._agent = agent
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._running = False

    def submit(self, text: str) -> None:
        self._queue.put(text)

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)

    def run(self) -> None:  # noqa: D401
        self._running = True
        while self._running:
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break
            try:
                reply = self._agent.handle(text)
            except Exception:
                self.log_line.emit(traceback.format_exc())
                reply = "Something went wrong processing that, sir."
            if reply:
                self.reply_ready.emit(reply)
            if self._is_exit(text, reply):
                self.exit_requested.emit()

    @staticmethod
    def _is_exit(text: str, reply: str) -> bool:
        t = (text or "").lower()
        return any(w in t for w in ("exit jarvis", "quit jarvis", "shut down jarvis", "goodbye jarvis"))


# --------------------------------------------------------------------------- #
class JarvisWindow(QWidget):
    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__()
        self.config = config

        # ---- Build brain / memory / modules ----
        self.memory = Memory(store_path=os.path.join(APP_DIR, "config", "memory.json"))
        self.brain = Brain(config)

        self.system_monitor = SystemMonitor()
        self.input_control = InputControl(screenshot_dir=os.path.join(APP_DIR, "screenshots"))
        self.app_manager = AppManager()
        self.browser = Browser(
            headless=config["browser"]["headless"],
            search_engine=config["browser"]["search_engine"],
        )
        self.self_modifier = SelfModifier(
            self.brain,
            plugins_dir=os.path.join(APP_DIR, "plugins"),
            allow_unsafe=config["plugins"]["allow_unsafe_plugins"],
            log_cb=self._log,
        )

        self.modules = {
            "system": self.system_monitor,
            "input": self.input_control,
            "apps": self.app_manager,
            "browser": self.browser,
            "self_modify": self.self_modifier,
        }

        self.agent = Agent(
            self.brain, self.memory, self.modules,
            state_cb=self._on_agent_state,   # called from worker thread
            speak_cb=self._speak_from_worker,
            log_cb=self._log,
        )
        self.self_modifier.set_agent(self.agent)

        # Load + hot-reload plugins.
        self.self_modifier.load_all()
        if config["plugins"]["hot_reload"]:
            self.self_modifier.start_watching()

        # ---- Worker thread ----
        self.worker = AgentWorker(self.agent)
        self.worker.reply_ready.connect(self._on_reply)
        self.worker.state_changed.connect(self._set_orb_state)
        self.worker.log_line.connect(self._log)
        self.worker.exit_requested.connect(self.close)
        self.worker.start()

        # ---- Voice ----
        self.speaker: Optional[VoiceSpeaker] = None
        self.listener: Optional[VoiceListener] = None
        self._setup_voice()

        # ---- UI ----
        self._build_ui()
        self._setup_hud_timer()

        # Greeting.
        greeting = "JARVIS online. All systems nominal." if self.brain.is_online \
            else "JARVIS online in offline mode. Set an API key for full reasoning."
        self._append_transcript("JARVIS", greeting)
        if config["voice"]["speak_responses"] and self.speaker:
            self.speaker.say(greeting)

    # ================================================================== #
    # UI construction
    # ================================================================== #
    def _build_ui(self) -> None:
        cfg = self.config["ui"]
        self.setWindowTitle("JARVIS")
        self.resize(cfg["width"], cfg["height"])
        self.setMinimumSize(720, 540)
        self.setStyleSheet("background: #050a0f;")

        # Orb (centered).
        self.orb = OrbWidget(self, fps=cfg["fps"])

        # HUD panels.
        self.hud_left = HudPanel("SYSTEM", side="left", width=250, parent=self)
        self.hud_left.add_bar("cpu", "CPU")
        self.hud_left.add_bar("ram", "RAM")
        self.hud_left.add_bar("gpu", "GPU")
        self.hud_left.add_text_row("")

        self.hud_right = HudPanel("ACTIVITY", side="right", width=270, parent=self)
        self.hud_right.add_text_row("Awaiting command…")

        # Transcript (semi-transparent, below orb).
        self.transcript = QTextEdit(self)
        self.transcript.setReadOnly(True)
        self.transcript.setFrameStyle(0)
        self.transcript.setStyleSheet(
            "QTextEdit { background: rgba(4,10,15,140); color: #9be8ff;"
            " border: 1px solid rgba(0,229,255,80); border-radius: 6px;"
            " font-family: Consolas, 'Courier New', monospace; font-size: 12px;"
            " padding: 8px; }"
        )

        # Status line.
        self.status_label = QLabel("● IDLE", self)
        self.status_label.setStyleSheet(
            "color:#00e5ff; font-family:Consolas,monospace; font-size:11px; background:transparent;"
        )

        # Input row.
        self.input_box = QLineEdit(self)
        self.input_box.setPlaceholderText("Type a command, or speak…  (Ctrl+Space to talk)")
        self.input_box.setStyleSheet(
            "QLineEdit { background: rgba(4,12,18,220); color:#c8f6ff;"
            " border: 1px solid rgba(0,229,255,140); border-radius: 18px;"
            " padding: 10px 16px; font-family: Consolas, monospace; font-size: 13px; }"
            "QLineEdit:focus { border: 1px solid #00e5ff; }"
        )
        self.input_box.returnPressed.connect(self._on_submit)

        self.send_btn = QPushButton("▶", self)
        self.send_btn.setFixedSize(38, 38)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(
            "QPushButton { background: rgba(0,229,255,30); color:#00e5ff;"
            " border: 1px solid #00e5ff; border-radius: 19px; font-size:15px; }"
            "QPushButton:hover { background: rgba(0,229,255,70); }"
        )
        self.send_btn.clicked.connect(self._on_submit)

        self.mic_btn = QPushButton("🎙", self)
        self.mic_btn.setFixedSize(38, 38)
        self.mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_btn.setStyleSheet(self.send_btn.styleSheet())
        self.mic_btn.clicked.connect(self._toggle_listening)

        # Shortcuts.
        QShortcut(QKeySequence("Ctrl+Space"), self, activated=self._toggle_listening)
        QShortcut(QKeySequence("Ctrl+H"), self, activated=self._toggle_hud)
        QShortcut(QKeySequence("Escape"), self, activated=self._on_escape)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)

        if cfg.get("show_hud_on_start"):
            QTimer.singleShot(500, self._toggle_hud)
        if cfg.get("start_fullscreen"):
            self.showFullScreen()

    # ------------------------------------------------------------------ #
    def resizeEvent(self, event):  # noqa: N802
        w, h = self.width(), self.height()
        orb_size = int(min(w, h) * 0.62)
        self.orb.setGeometry(int((w - orb_size) / 2), int((h - orb_size) / 2) - 30,
                             orb_size, orb_size)

        # Transcript below orb.
        t_w = int(w * 0.5)
        t_h = 120
        self.transcript.setGeometry(int((w - t_w) / 2), h - t_h - 78, t_w, t_h)

        # Input row at bottom.
        row_w = int(w * 0.5)
        row_x = int((w - row_w) / 2)
        self.input_box.setGeometry(row_x, h - 56, row_w - 88, 38)
        self.send_btn.setGeometry(row_x + row_w - 84, h - 56, 38, 38)
        self.mic_btn.setGeometry(row_x + row_w - 42, h - 56, 38, 38)

        self.status_label.setGeometry(20, 16, 200, 20)

        self.hud_left._reposition()
        self.hud_right._reposition()
        super().resizeEvent(event)

    # ------------------------------------------------------------------ #
    def paintEvent(self, event):  # noqa: N802
        """Draw the near-black background with subtle scan-line grid."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # Vertical gradient backdrop.
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(6, 12, 18))
        grad.setColorAt(1.0, QColor(2, 5, 8))
        p.fillRect(self.rect(), QBrush(grad))

        # Grid lines.
        p.setPen(QPen(QColor(0, 229, 255, 16), 1))
        step = 42
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)

        # Radial vignette darkening edges.
        p.end()

    # ================================================================== #
    # HUD refresh
    # ================================================================== #
    def _setup_hud_timer(self) -> None:
        self.hud_timer = QTimer(self)
        self.hud_timer.timeout.connect(self._refresh_hud)
        self.hud_timer.start(1500)

    def _refresh_hud(self) -> None:
        if not self.hud_left.is_shown:
            return
        try:
            snap = self.system_monitor.snapshot()
        except Exception:
            return
        if "error" in snap:
            return
        self.hud_left.update_bar("cpu", snap.get("cpu_percent") or 0)
        self.hud_left.update_bar("ram", snap.get("ram_percent") or 0)
        gpu = snap.get("gpu_percent")
        self.hud_left.update_bar("gpu", gpu if gpu is not None else 0)
        self.hud_left.set_text_rows([
            f"MEM {snap.get('ram_used_h','?')}/{snap.get('ram_total_h','?')}",
            f"NET ↓{snap.get('net_down','?')}  ↑{snap.get('net_up','?')}",
            f"UP  {snap.get('uptime','?')}",
        ])

    def _toggle_hud(self) -> None:
        self.hud_left.toggle()
        self.hud_right.toggle()
        if self.hud_left.is_shown:
            self._refresh_hud()

    # ================================================================== #
    # Voice
    # ================================================================== #
    def _setup_voice(self) -> None:
        vcfg = self.config["voice"]
        if not vcfg.get("enabled", True):
            return
        # Speaker.
        self.speaker = VoiceSpeaker(
            rate=vcfg["rate"], volume=vcfg["volume"], voice_hint=vcfg["voice_hint"]
        )
        self.speaker.speaking_started.connect(lambda: self._set_orb_state(STATE_SPEAKING))
        self.speaker.speaking_finished.connect(self._on_speaking_finished)
        self.speaker.amplitude.connect(self.orb_set_amplitude)
        self.speaker.unavailable.connect(self._log)
        self.speaker.start()

        # Listener.
        self.listener = VoiceListener(
            wake_word=vcfg["wake_word"],
            require_wake_word=vcfg["require_wake_word"],
            language=vcfg["language"],
            engine=vcfg["stt_engine"],
        )
        self.listener.recognized.connect(self._on_voice_recognized)
        self.listener.wake_detected.connect(lambda: self._set_orb_state(STATE_LISTENING))
        self.listener.level.connect(self.orb_set_amplitude)
        self.listener.listening_started.connect(lambda: self._set_status("LISTENING"))
        self.listener.unavailable.connect(self._log)
        # Listener is started on demand via the mic button (or auto if configured).
        if not vcfg.get("require_wake_word", False):
            # Auto-start passive listening.
            self.listener.start()

    def orb_set_amplitude(self, amp: float) -> None:
        self.orb.set_amplitude(amp)

    def _toggle_listening(self) -> None:
        if not self.listener:
            self._append_transcript("JARVIS", "Voice input isn't available on this system.")
            return
        if not self.listener.isRunning():
            self.listener.start()
            self._set_status("LISTENING")
        else:
            self.listener.resume()
            self._set_orb_state(STATE_LISTENING)

    def _on_voice_recognized(self, text: str) -> None:
        self._append_transcript("YOU", text)
        self._dispatch(text)

    def _on_speaking_finished(self) -> None:
        self._set_orb_state(STATE_IDLE)
        # Resume listening after speaking to avoid capturing our own voice.
        if self.listener and self.listener.isRunning():
            self.listener.resume()

    # ================================================================== #
    # Input dispatch
    # ================================================================== #
    def _on_submit(self) -> None:
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self._append_transcript("YOU", text)
        self._dispatch(text)

    def _dispatch(self, text: str) -> None:
        # Pause the mic while we think/speak to prevent feedback loops.
        if self.listener and self.listener.isRunning():
            self.listener.pause()
        self._set_orb_state(STATE_THINKING)
        self.worker.submit(text)

    # ================================================================== #
    # Worker callbacks (from worker thread -> marshalled via signals)
    # ================================================================== #
    def _on_agent_state(self, state: str) -> None:
        # Called from worker thread; emit through worker signal for thread safety.
        self.worker.state_changed.emit(state)

    def _speak_from_worker(self, text: str) -> None:
        # Safe: VoiceSpeaker.say is thread-safe (queue).
        if self.config["voice"]["speak_responses"] and self.speaker:
            self.speaker.say(text)

    def _on_reply(self, reply: str) -> None:
        self._append_transcript("JARVIS", reply)
        self.hud_right.set_text_rows(self._wrap(reply, 34)[:8])
        if self.config["voice"]["speak_responses"] and self.speaker:
            self.speaker.say(reply)
        else:
            self._set_orb_state(STATE_IDLE)

    # ================================================================== #
    # Orb / status helpers
    # ================================================================== #
    def _set_orb_state(self, state: str) -> None:
        self.orb.set_state(state)
        self._set_status(state.upper())

    def _set_status(self, text: str) -> None:
        colors = {
            "IDLE": "#00e5ff", "LISTENING": "#00ff9c", "THINKING": "#b48cff",
            "SPEAKING": "#00e5ff", "WORKING": "#ffb020",
        }
        color = colors.get(text, "#00e5ff")
        self.status_label.setStyleSheet(
            f"color:{color}; font-family:Consolas,monospace; font-size:11px; background:transparent;"
        )
        self.status_label.setText(f"● {text}")

    # ================================================================== #
    # Transcript
    # ================================================================== #
    def _append_transcript(self, who: str, text: str) -> None:
        color = "#00e5ff" if who == "JARVIS" else ("#9be8ff" if who == "YOU" else "#8899aa")
        safe = text.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        self.transcript.append(
            f'<span style="color:{color};font-weight:bold;">{who}:</span> '
            f'<span style="color:#c8f6ff;">{safe}</span>'
        )
        sb = self.transcript.verticalScrollBar()
        sb.setValue(sb.maximum())

    @staticmethod
    def _wrap(text: str, width: int):
        import textwrap
        lines = []
        for para in text.splitlines():
            lines.extend(textwrap.wrap(para, width) or [""])
        return lines

    def _log(self, msg: str) -> None:
        print(msg)

    # ================================================================== #
    def _on_escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        elif self.hud_left.is_shown:
            self._toggle_hud()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ================================================================== #
    def closeEvent(self, event):  # noqa: N802
        try:
            if self.listener:
                self.listener.stop()
                self.listener.wait(1500)
        except Exception:
            pass
        try:
            if self.speaker:
                self.speaker.stop()
                self.speaker.wait(1500)
        except Exception:
            pass
        try:
            self.worker.stop()
            self.worker.wait(2000)
        except Exception:
            pass
        try:
            self.self_modifier.shutdown()
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
        super().closeEvent(event)


# --------------------------------------------------------------------------- #
def main() -> int:
    config = load_config()
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    win = JarvisWindow(config)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
