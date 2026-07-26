"""
ui/voice_handler.py
===================
Voice input (speech recognition) and output (text-to-speech), each on its own
QThread so the GUI never blocks.

  * VoiceListener  — a QThread that continuously listens on the microphone using
                     the `speech_recognition` library, emits `recognized(str)`
                     for each finalized utterance, and `wake_detected()` when the
                     configured wake word is heard. Emits `level(float)` for a
                     rough input amplitude the orb can visualise.
  * VoiceSpeaker   — a QThread-backed TTS queue using `pyttsx3`. Call `say(text)`
                     from anywhere; it serialises speech and emits
                     `speaking_started` / `speaking_finished` and `amplitude`
                     pulses so the orb shows a waveform while talking.

All hardware access is optional: if a microphone, PyAudio, or pyttsx3 is missing,
the classes degrade to no-ops and emit `unavailable(str)` so the UI can inform
the user rather than crash.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

try:
    import speech_recognition as sr  # type: ignore
    _SR = True
except Exception:  # pragma: no cover
    sr = None  # type: ignore
    _SR = False

try:
    import pyttsx3  # type: ignore
    _TTS = True
except Exception:  # pragma: no cover
    pyttsx3 = None  # type: ignore
    _TTS = False


# --------------------------------------------------------------------------- #
class VoiceListener(QThread):
    recognized = pyqtSignal(str)
    wake_detected = pyqtSignal()
    level = pyqtSignal(float)
    listening_started = pyqtSignal()
    listening_stopped = pyqtSignal()
    unavailable = pyqtSignal(str)

    def __init__(
        self,
        wake_word: str = "jarvis",
        require_wake_word: bool = False,
        language: str = "en-US",
        engine: str = "google",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.wake_word = (wake_word or "").lower().strip()
        self.require_wake_word = require_wake_word
        self.language = language
        self.engine = engine
        self._running = False
        self._paused = False
        self._recognizer = None
        self._mic = None

    # ------------------------------------------------------------------ #
    def pause(self) -> None:
        """Pause listening (e.g. while Jarvis is speaking, to avoid feedback)."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D401
        if not _SR:
            self.unavailable.emit("speech_recognition not installed; voice input disabled.")
            return
        try:
            self._recognizer = sr.Recognizer()
            self._recognizer.dynamic_energy_threshold = True
            self._recognizer.pause_threshold = 0.8
            self._mic = sr.Microphone()
        except Exception as exc:
            self.unavailable.emit(f"No microphone available: {exc}")
            return

        try:
            with self._mic as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.8)
        except Exception as exc:
            self.unavailable.emit(f"Mic calibration failed: {exc}")
            return

        self._running = True
        while self._running:
            if self._paused:
                time.sleep(0.15)
                continue
            try:
                with self._mic as source:
                    self.listening_started.emit()
                    audio = self._recognizer.listen(
                        source, timeout=5, phrase_time_limit=12
                    )
                self.listening_stopped.emit()
            except sr.WaitTimeoutError:
                continue
            except Exception:
                time.sleep(0.2)
                continue

            if self._paused or not self._running:
                continue

            # Rough amplitude cue for the orb.
            try:
                raw = audio.get_raw_data()
                self.level.emit(min(1.0, len(raw) / 60000.0))
            except Exception:
                pass

            text = self._transcribe(audio)
            if not text:
                continue
            text = text.strip()
            lower = text.lower()

            if self.require_wake_word:
                if self.wake_word and self.wake_word in lower:
                    self.wake_detected.emit()
                    # Strip the wake word from the command.
                    cmd = lower.split(self.wake_word, 1)[-1].strip(" ,.")
                    if cmd:
                        self.recognized.emit(cmd)
                    # else: wake word only — UI will prompt / listen again.
                # ignore utterances without the wake word
            else:
                if self.wake_word and lower.startswith(self.wake_word):
                    self.wake_detected.emit()
                    text = text[len(self.wake_word):].strip(" ,.")
                if text:
                    self.recognized.emit(text)

    # ------------------------------------------------------------------ #
    def _transcribe(self, audio) -> Optional[str]:
        r = self._recognizer
        try:
            if self.engine == "sphinx":
                return r.recognize_sphinx(audio)  # type: ignore[attr-defined]
            # Default: Google Web Speech (free, needs internet).
            return r.recognize_google(audio, language=self.language)  # type: ignore[attr-defined]
        except sr.UnknownValueError:
            return None
        except sr.RequestError:
            # Network/engine failure — try offline sphinx if present.
            try:
                return r.recognize_sphinx(audio)  # type: ignore[attr-defined]
            except Exception:
                return None
        except Exception:
            return None


# --------------------------------------------------------------------------- #
class VoiceSpeaker(QThread):
    speaking_started = pyqtSignal()
    speaking_finished = pyqtSignal()
    amplitude = pyqtSignal(float)
    unavailable = pyqtSignal(str)

    def __init__(self, rate: int = 178, volume: float = 1.0,
                 voice_hint: str = "", parent=None) -> None:
        super().__init__(parent)
        self.rate = rate
        self.volume = volume
        self.voice_hint = (voice_hint or "").lower()
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._running = False
        self._engine = None
        self._amp_thread = None
        self._speaking = False

    # ------------------------------------------------------------------ #
    def say(self, text: str) -> None:
        if text:
            self._queue.put(text)

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)

    def set_properties(self, rate=None, volume=None, voice_hint=None) -> None:
        """Update TTS properties live (from the Settings dialog)."""
        if rate is not None:
            self.rate = int(rate)
        if volume is not None:
            self.volume = float(volume)
        if voice_hint is not None:
            self.voice_hint = (voice_hint or "").lower()
        try:
            if self._engine:
                self._engine.setProperty("rate", self.rate)
                self._engine.setProperty("volume", self.volume)
                if self.voice_hint:
                    for v in self._engine.getProperty("voices"):
                        meta = f"{getattr(v, 'name', '')} {getattr(v, 'id', '')}".lower()
                        if self.voice_hint in meta:
                            self._engine.setProperty("voice", v.id)
                            break
        except Exception:
            pass

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    # ------------------------------------------------------------------ #
    def _init_engine(self) -> bool:
        if not _TTS:
            self.unavailable.emit("pyttsx3 not installed; voice output disabled.")
            return False
        try:
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.rate)
            self._engine.setProperty("volume", self.volume)
            if self.voice_hint:
                for v in self._engine.getProperty("voices"):
                    meta = f"{getattr(v, 'name', '')} {getattr(v, 'id', '')}".lower()
                    if self.voice_hint in meta:
                        self._engine.setProperty("voice", v.id)
                        break
            return True
        except Exception as exc:
            self.unavailable.emit(f"TTS engine failed to start: {exc}")
            return False

    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D401
        if not self._init_engine():
            return
        self._running = True
        while self._running:
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break
            self._speak_one(text)

        try:
            if self._engine:
                self._engine.stop()
        except Exception:
            pass

    def _speak_one(self, text: str) -> None:
        self._speaking = True
        self.speaking_started.emit()
        # Emit synthetic amplitude pulses while speaking so the orb reacts.
        stop_amp = threading.Event()

        def pulse():
            import math
            t = 0.0
            while not stop_amp.is_set():
                t += 0.05
                val = 0.4 + 0.5 * abs(math.sin(t * 9))
                self.amplitude.emit(val)
                time.sleep(0.045)

        amp_t = threading.Thread(target=pulse, daemon=True)
        amp_t.start()
        try:
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception:
            pass
        finally:
            stop_amp.set()
            self._speaking = False
            self.amplitude.emit(0.0)
            self.speaking_finished.emit()
