#!/usr/bin/env python3
"""
JARVIS Settings dialog
======================
Clean, noob-friendly settings. The philosophy: *less is more*.

The main screen is just a list of AI provider cards. Each card shows the
provider name, a FREE/PAID badge, a one-line blurb, a "Get key ↗" hyperlink,
and a single box to paste a key. Paste a key -> that provider is connected.
Jarvis then auto-routes each request to the best connected provider.

A second tab does the same for Agents (Hermes, etc.). A small "More" tab keeps
the occasional extra knob (voice on/off, start fullscreen) out of the way.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QLabel, QPushButton, QScrollArea, QFrame, QCheckBox, QComboBox,
)


class _VoiceTestWorker(QThread):
    """Validate + play an ElevenLabs sample off the UI thread."""
    done = pyqtSignal(bool, str)

    def __init__(self, api_key: str, voice_id: str, parent=None):
        super().__init__(parent)
        self._key = api_key
        self._voice = voice_id

    def run(self) -> None:
        try:
            from ui.voice_handler import eleven_speak
            ok, msg = eleven_speak(
                self._key, self._voice,
                "All systems are online, sir. This is the voice you selected.")
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"Test failed: {exc}"
        self.done.emit(ok, msg)

from core import providers as P

CYAN = "#00e5ff"
BG = "#0a1017"
FG = "#c8f6ff"
GREEN = "#38e08f"
AMBER = "#ffb84d"

_QSS = f"""
QDialog {{ background:{BG}; }}
QLabel {{ color:{FG}; font-family:Consolas,monospace; background:transparent; }}
QScrollArea {{ border:none; background:transparent; }}
QWidget#scrollbody {{ background:transparent; }}
QTabWidget::pane {{ border:1px solid rgba(0,229,255,90); border-radius:6px; top:-1px; }}
QTabBar::tab {{ background:rgba(0,229,255,20); color:{FG}; font-family:Consolas,monospace;
    padding:8px 20px; border:1px solid rgba(0,229,255,60); border-bottom:none;
    border-top-left-radius:6px; border-top-right-radius:6px; margin-right:2px; }}
QTabBar::tab:selected {{ background:rgba(0,229,255,60); color:#04222a; font-weight:bold; }}
QLineEdit {{ background:#05252b; color:{FG}; border:1px solid rgba(0,229,255,120);
    border-radius:5px; padding:7px 10px; font-family:Consolas,monospace; font-size:12px; }}
QLineEdit:focus {{ border:1px solid {CYAN}; }}
QComboBox {{ background:#05252b; color:{FG}; border:1px solid rgba(0,229,255,120);
    border-radius:5px; padding:5px 8px; font-family:Consolas,monospace; }}
QComboBox QAbstractItemView {{ background:#05252b; color:{FG}; selection-background-color:rgba(0,229,255,90); }}
QCheckBox {{ color:{FG}; font-family:Consolas,monospace; font-size:12px; }}
QCheckBox::indicator {{ width:16px; height:16px; border:1px solid {CYAN}; border-radius:3px; background:#05252b; }}
QCheckBox::indicator:checked {{ background:{CYAN}; }}
QPushButton {{ background:rgba(0,229,255,30); color:{CYAN}; border:1px solid {CYAN};
    border-radius:6px; padding:8px 20px; font-family:Consolas,monospace; font-size:12px; }}
QPushButton:hover {{ background:rgba(0,229,255,70); }}
QPushButton#save {{ background:{CYAN}; color:#04222a; font-weight:bold; }}
QPushButton#save:hover {{ background:#66f0ff; }}
"""


class _ProviderCard(QFrame):
    """One provider/agent card with a paste-a-key box."""

    def __init__(self, spec: Dict[str, Any], saved_key: str, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.setStyleSheet(
            "QFrame { background: rgba(0,229,255,10); border:1px solid rgba(0,229,255,70);"
            " border-radius:10px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        # --- Header row: name + badge ... Get key ↗ ---
        head = QHBoxLayout()
        name = QLabel(spec["label"])
        name.setStyleSheet(f"color:{CYAN}; font-size:15px; font-weight:bold;")
        head.addWidget(name)

        tier = spec.get("tier", "free")
        badge = QLabel("FREE" if tier == "free" else "PAID")
        badge_color = GREEN if tier == "free" else AMBER
        badge.setStyleSheet(
            f"color:{badge_color}; font-size:10px; font-weight:bold;"
            f" border:1px solid {badge_color}; border-radius:8px; padding:1px 8px;")
        head.addWidget(badge)
        head.addStretch(1)

        get = QLabel(f'<a style="color:{CYAN}; text-decoration:none;" '
                     f'href="{spec.get("get_key_url","")}">Get key ↗</a>')
        get.setOpenExternalLinks(True)
        get.setStyleSheet("font-size:12px;")
        head.addWidget(get)
        lay.addLayout(head)

        # --- Blurb ---
        blurb = QLabel(spec.get("blurb", ""))
        blurb.setStyleSheet("color:#8fbfcf; font-size:10px;")
        blurb.setWordWrap(True)
        lay.addWidget(blurb)

        # --- Key box + connected indicator ---
        row = QHBoxLayout()
        self.key_edit = QLineEdit(saved_key)
        self.key_edit.setPlaceholderText(f'Paste a key here to connect  ({spec.get("key_prefix","...")})')
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password if saved_key
                                  else QLineEdit.EchoMode.Normal)
        row.addWidget(self.key_edit, 1)

        self.dot = QLabel()
        self._refresh_dot(bool(saved_key.strip()))
        self.key_edit.textChanged.connect(
            lambda t: self._refresh_dot(bool(t.strip())))
        row.addWidget(self.dot)
        lay.addLayout(row)

    def _refresh_dot(self, connected: bool) -> None:
        if connected:
            self.dot.setText("● connected")
            self.dot.setStyleSheet(f"color:{GREEN}; font-size:11px;")
        else:
            self.dot.setText("○ not set")
            self.dot.setStyleSheet("color:#5f7f8f; font-size:11px;")

    def key(self) -> str:
        return self.key_edit.text().strip()


class SettingsDialog(QDialog):
    """Modal settings editor. Call ``exec()``; on accept read ``self.result_config``."""

    def __init__(self, config: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("JARVIS — Settings")
        self.setModal(True)
        self.setMinimumSize(560, 620)
        self.setStyleSheet(_QSS)
        self._config = copy.deepcopy(config)
        self.result_config: Dict[str, Any] = {}
        self._provider_cards: Dict[str, _ProviderCard] = {}
        self._agent_cards: Dict[str, _ProviderCard] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel("⚙  SETTINGS")
        title.setStyleSheet(f"color:{CYAN}; font-size:17px; font-weight:bold;")
        root.addWidget(title)
        sub = QLabel("Paste a key next to any provider to connect it. "
                     "Jarvis picks the best one for each request automatically.")
        sub.setStyleSheet("color:#8fbfcf; font-size:10px;")
        sub.setWordWrap(True)
        root.addWidget(sub)

        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs, 1)
        self._build_providers_tab()
        self._build_agents_tab()
        self._build_voice_tab()
        self._build_more_tab()

        # Default / active picker.
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("Preferred:"))
        self.active_combo = QComboBox()
        self.active_combo.addItem("Auto (recommended)", "auto")
        for pid, spec in P.PROVIDERS.items():
            self.active_combo.addItem(spec["label"], pid)
        for aid, spec in P.AGENTS.items():
            self.active_combo.addItem(f"{spec['label']} (agent)", f"agent:{aid}")
        cur = self._config.get("llm", {}).get("active", "auto")
        idx = self.active_combo.findData(cur)
        if idx >= 0:
            self.active_combo.setCurrentIndex(idx)
        pick_row.addWidget(self.active_combo, 1)
        root.addLayout(pick_row)

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
    def _scroll_body(self):
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("scrollbody")
        v = QVBoxLayout(body)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(10)
        area.setWidget(body)
        return area, v

    def _saved_key(self, pid: str) -> str:
        return (self._config.get("llm", {}).get("providers", {})
                .get(pid, {}).get("api_key", ""))

    def _build_providers_tab(self) -> None:
        area, v = self._scroll_body()
        for pid, spec in P.PROVIDERS.items():
            card = _ProviderCard(spec, self._saved_key(pid))
            self._provider_cards[pid] = card
            v.addWidget(card)
        v.addStretch(1)
        self.tabs.addTab(area, "AI Providers")

    def _build_agents_tab(self) -> None:
        area, v = self._scroll_body()
        note = QLabel("Agents are specialised brains. If you've connected the "
                      "provider they run on (e.g. OpenRouter), they light up "
                      "automatically — or paste a key here.")
        note.setStyleSheet("color:#8fbfcf; font-size:10px;")
        note.setWordWrap(True)
        v.addWidget(note)
        for aid, spec in P.AGENTS.items():
            card = _ProviderCard(spec, self._saved_key(f"agent:{aid}"))
            self._agent_cards[aid] = card
            v.addWidget(card)
        v.addStretch(1)
        self.tabs.addTab(area, "Agents")

    def _build_voice_tab(self) -> None:
        from ui.voice_handler import JARVIS_VOICES, DEFAULT_JARVIS_VOICE_ID
        area, v = self._scroll_body()
        vcfg = self._config.setdefault("voice", {})
        ecfg = vcfg.setdefault("elevenlabs", {})

        intro = QLabel("Pick how Jarvis sounds. <b>System voice</b> works "
                       "offline out of the box. The <b>JARVIS voices</b> are "
                       "cinematic Iron-Man style voices from ElevenLabs — paste "
                       "a free key below, pick a voice, and press <b>Test</b>.")
        intro.setStyleSheet("color:#8fbfcf; font-size:11px;")
        intro.setWordWrap(True)
        v.addWidget(intro)

        # --- ElevenLabs API key card ---
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: rgba(0,229,255,10); border:1px solid rgba(0,229,255,70);"
            " border-radius:10px; }")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)

        head = QHBoxLayout()
        nm = QLabel("ElevenLabs key")
        nm.setStyleSheet(f"color:{CYAN}; font-size:15px; font-weight:bold;")
        head.addWidget(nm)
        badge = QLabel("FREE TIER")
        badge.setStyleSheet(
            f"color:{GREEN}; font-size:10px; font-weight:bold;"
            f" border:1px solid {GREEN}; border-radius:8px; padding:1px 8px;")
        head.addWidget(badge)
        head.addStretch(1)
        get = QLabel(f'<a style="color:{CYAN}; text-decoration:none;" '
                     f'href="https://elevenlabs.io/app/settings/api-keys">Get key ↗</a>')
        get.setOpenExternalLinks(True)
        get.setStyleSheet("font-size:12px;")
        head.addWidget(get)
        cl.addLayout(head)

        blurb = QLabel("Only needed for the JARVIS voices below. The System "
                       "voice needs no key.")
        blurb.setStyleSheet("color:#8fbfcf; font-size:10px;")
        blurb.setWordWrap(True)
        cl.addWidget(blurb)

        krow = QHBoxLayout()
        saved_key = ecfg.get("api_key", "")
        self.eleven_key = QLineEdit(saved_key)
        self.eleven_key.setPlaceholderText("Paste ElevenLabs API key here  (sk_...)")
        self.eleven_key.setEchoMode(QLineEdit.EchoMode.Password if saved_key
                                    else QLineEdit.EchoMode.Normal)
        krow.addWidget(self.eleven_key, 1)
        self.eleven_dot = QLabel()
        self._set_dot(self.eleven_dot, bool(saved_key.strip()))
        self.eleven_key.textChanged.connect(
            lambda t: self._set_dot(self.eleven_dot, bool(t.strip())))
        krow.addWidget(self.eleven_dot)
        cl.addLayout(krow)
        v.addWidget(card)

        # --- Voice picker (system + preloaded JARVIS voices) ---
        vr = QHBoxLayout()
        vr.addWidget(QLabel("Jarvis voice:"))
        self.voice_pick = QComboBox()
        self.voice_pick.addItem("System voice (offline, no key)", "system")
        for spec in JARVIS_VOICES:
            self.voice_pick.addItem(f"JARVIS · {spec['label']}", spec["id"])
        # Restore current selection.
        cur_engine = (vcfg.get("engine", "pyttsx3") or "pyttsx3").lower()
        cur_voice = ecfg.get("voice_id", "") or DEFAULT_JARVIS_VOICE_ID
        if cur_engine == "elevenlabs":
            idx = self.voice_pick.findData(cur_voice)
            self.voice_pick.setCurrentIndex(idx if idx >= 0 else 1)
        else:
            self.voice_pick.setCurrentIndex(0)
        vr.addWidget(self.voice_pick, 1)
        v.addLayout(vr)

        # --- Test button + status ---
        trow = QHBoxLayout()
        self.voice_test_btn = QPushButton("🔊  Test voice")
        self.voice_test_btn.clicked.connect(self._on_test_voice)
        trow.addWidget(self.voice_test_btn)
        self.voice_test_status = QLabel("")
        self.voice_test_status.setStyleSheet("color:#8fbfcf; font-size:11px;")
        self.voice_test_status.setWordWrap(True)
        trow.addWidget(self.voice_test_status, 1)
        v.addLayout(trow)
        self._voice_test_worker = None

        # --- Rate (applies to system voice) ---
        self.voice_rate = QLineEdit(str(vcfg.get("rate", 178)))
        self.voice_rate.setPlaceholderText("178")
        rr = QHBoxLayout()
        rr.addWidget(QLabel("Speaking rate (system voice):"))
        rr.addWidget(self.voice_rate, 1)
        v.addLayout(rr)

        self.voice_speak = QCheckBox("Speak responses aloud")
        self.voice_speak.setChecked(bool(vcfg.get("speak_responses", True)))
        v.addWidget(self.voice_speak)

        v.addStretch(1)
        self.tabs.addTab(area, "Voice")

    # ------------------------------------------------------------------ #
    def _on_test_voice(self) -> None:
        data = self.voice_pick.currentData()
        if data == "system":
            self.voice_test_status.setText(
                "System voice uses your Windows/OS voice — no key or internet "
                "needed. Save to hear it on the next reply.")
            return
        key = self.eleven_key.text().strip()
        if not key:
            self.voice_test_status.setText(
                "Paste your ElevenLabs API key first, then Test.")
            return
        if self._voice_test_worker and self._voice_test_worker.isRunning():
            return
        self.voice_test_btn.setEnabled(False)
        self.voice_test_status.setText("Testing… synthesising a sample.")
        self._voice_test_worker = _VoiceTestWorker(key, data, self)
        self._voice_test_worker.done.connect(self._on_test_done)
        self._voice_test_worker.start()

    def _on_test_done(self, ok: bool, msg: str) -> None:
        self.voice_test_btn.setEnabled(True)
        color = GREEN if ok else "#ff8080"
        self.voice_test_status.setStyleSheet(f"color:{color}; font-size:11px;")
        self.voice_test_status.setText(("✓ " if ok else "✗ ") + msg)

    def _set_dot(self, dot: QLabel, connected: bool) -> None:
        if connected:
            dot.setText("● connected")
            dot.setStyleSheet(f"color:{GREEN}; font-size:11px;")
        else:
            dot.setText("○ not set")
            dot.setStyleSheet("color:#5f7f8f; font-size:11px;")

    def _build_more_tab(self) -> None:
        area, v = self._scroll_body()
        vcfg = self._config.setdefault("voice", {})
        ucfg = self._config.setdefault("ui", {})

        self.voice_enabled = QCheckBox("Enable voice (listen + speak)")
        self.voice_enabled.setChecked(bool(vcfg.get("enabled", True)))
        self.start_fullscreen = QCheckBox("Start in fullscreen")
        self.start_fullscreen.setChecked(bool(ucfg.get("start_fullscreen", False)))

        for w in (self.voice_enabled, self.start_fullscreen):
            v.addWidget(w)
        v.addStretch(1)
        self.tabs.addTab(area, "More")

    # ------------------------------------------------------------------ #
    def _on_save(self) -> None:
        llm = self._config.setdefault("llm", {})
        provs = llm.setdefault("providers", {})

        for pid, card in self._provider_cards.items():
            key = card.key()
            if key:
                provs.setdefault(pid, {})["api_key"] = key
            elif pid in provs:
                provs[pid]["api_key"] = ""

        for aid, card in self._agent_cards.items():
            key = card.key()
            slot = f"agent:{aid}"
            if key:
                provs.setdefault(slot, {})["api_key"] = key
            elif slot in provs:
                provs[slot]["api_key"] = ""

        llm["active"] = self.active_combo.currentData()

        vcfg = self._config.setdefault("voice", {})
        vcfg["enabled"] = self.voice_enabled.isChecked()
        vcfg["speak_responses"] = self.voice_speak.isChecked()
        try:
            vcfg["rate"] = int(self.voice_rate.text().strip() or 178)
        except Exception:
            vcfg["rate"] = 178
        ecfg = vcfg.setdefault("elevenlabs", {})
        ecfg["api_key"] = self.eleven_key.text().strip()
        # The single voice picker drives BOTH the engine and the voice id, so a
        # non-technical user never has to think about "engines" or "voice ids".
        pick = self.voice_pick.currentData()
        if pick and pick != "system":
            vcfg["engine"] = "elevenlabs"
            ecfg["voice_id"] = pick
        else:
            vcfg["engine"] = "pyttsx3"
        ecfg.setdefault("model", "eleven_turbo_v2_5")
        ucfg = self._config.setdefault("ui", {})
        ucfg["start_fullscreen"] = self.start_fullscreen.isChecked()

        self.result_config = self._config
        self.accept()
