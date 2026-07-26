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

import os
import platform
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import wave
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

try:
    import requests  # type: ignore
    _REQUESTS = True
except Exception:  # pragma: no cover
    requests = None  # type: ignore
    _REQUESTS = False

# A classic refined British male voice for the Iron-Man "JARVIS" feel.
# This is an ElevenLabs stock voice id ("George" — British, warm/authoritative).
DEFAULT_JARVIS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"

# A small curated set of ElevenLabs *stock* voices that suit a JARVIS-style
# assistant. These ids are public premade voices in every ElevenLabs account,
# so the user never has to hunt for a "voice id" — they just pick a name.
JARVIS_VOICES = [
    {"id": "JBFqnCBsd6RMkjVDRZzb", "label": "George — British, warm (classic JARVIS)"},
    {"id": "onwK4e9ZLuTAKqWW03F9", "label": "Daniel — British, authoritative news"},
    {"id": "nPczCjzI2devNBz1zQrb", "label": "Brian — deep, cinematic narrator"},
    {"id": "pNInz6obpgDQGcFmaJgB", "label": "Adam — deep American"},
    {"id": "IKne3meq5aSn9XLyUdCD", "label": "Charlie — natural, conversational"},
    {"id": "TX3LPaxmHKxFdv7VOQHJ", "label": "Liam — crisp, youthful"},
]


def voice_label_for(voice_id: str) -> str:
    for v in JARVIS_VOICES:
        if v["id"] == voice_id:
            return v["label"]
    return voice_id or DEFAULT_JARVIS_VOICE_ID


# --------------------------------------------------------------------------- #
# Module-level ElevenLabs helpers (shared by VoiceSpeaker + the Settings "Test
# voice" button, so both use exactly the same synthesis + playback path).
# --------------------------------------------------------------------------- #
def _pcm_to_wav_file(pcm: bytes, rate: int = 24000) -> Optional[str]:
    try:
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis_tts_")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # 16-bit
            w.setframerate(rate)
            w.writeframes(pcm)
        return path
    except Exception:
        return None


def play_wav_file(path: str) -> bool:
    """Play a WAV file blocking until done, cross-platform, best-effort."""
    system = platform.system()
    try:
        if system == "Windows":
            import winsound  # type: ignore
            winsound.PlaySound(path, winsound.SND_FILENAME)
            return True
    except Exception:
        pass
    for player in ("afplay", "aplay", "ffplay", "paplay"):
        exe = shutil.which(player)
        if not exe:
            continue
        try:
            if player == "ffplay":
                cmd = [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
            elif player == "aplay":
                cmd = [exe, "-q", path]
            else:
                cmd = [exe, path]
            subprocess.run(cmd, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    return False


def eleven_synthesize(api_key: str, voice_id: str, text: str,
                      model: str = "eleven_turbo_v2_5"):
    """Call ElevenLabs TTS. Returns (pcm_bytes|None, error_message)."""
    if not _REQUESTS:
        return None, "The 'requests' library isn't installed."
    if not api_key:
        return None, "No ElevenLabs API key set."
    voice_id = voice_id or DEFAULT_JARVIS_VOICE_ID
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
           f"?output_format=pcm_24000")
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/pcm",
    }
    payload = {
        "text": text,
        "model_id": model or "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.85,
            "style": 0.15,
            "use_speaker_boost": True,
        },
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except Exception as exc:
        return None, f"Network error reaching ElevenLabs: {exc}"
    if resp.status_code == 200 and resp.content:
        return resp.content, ""
    if resp.status_code in (401, 403):
        return None, "API key rejected (401/403). Check the key is correct."
    if resp.status_code == 422:
        return None, "Voice id not accepted (422). Pick a different voice."
    if resp.status_code == 429:
        return None, "ElevenLabs quota/limit reached (429)."
    detail = ""
    try:
        detail = resp.text[:160]
    except Exception:
        pass
    return None, f"ElevenLabs error {resp.status_code}. {detail}"


def eleven_speak(api_key: str, voice_id: str, text: str,
                 model: str = "eleven_turbo_v2_5"):
    """Synthesize + play a sample. Returns (ok, message)."""
    pcm, err = eleven_synthesize(api_key, voice_id, text, model)
    if pcm is None:
        return False, err
    wav = _pcm_to_wav_file(pcm, rate=24000)
    if not wav:
        return False, "Couldn't build the audio file."
    played = play_wav_file(wav)
    try:
        os.remove(wav)
    except Exception:
        pass
    if not played:
        return False, ("Audio was generated but no player was found to play it "
                       "(this is normal off-Windows).")
    return True, "Voice test played successfully."


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
                 voice_hint: str = "", engine: str = "pyttsx3",
                 eleven_api_key: str = "", eleven_voice_id: str = "",
                 eleven_model: str = "eleven_turbo_v2_5", parent=None) -> None:
        super().__init__(parent)
        self.rate = rate
        self.volume = volume
        # Default hint nudges pyttsx3 toward a male voice for the JARVIS feel.
        self.voice_hint = (voice_hint or "").lower()
        self.engine_name = (engine or "pyttsx3").lower()
        self.eleven_api_key = eleven_api_key or ""
        self.eleven_voice_id = eleven_voice_id or DEFAULT_JARVIS_VOICE_ID
        self.eleven_model = eleven_model or "eleven_turbo_v2_5"
        self._eleven_ok = None  # lazy health flag
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._running = False
        self._engine = None
        self._amp_thread = None
        self._speaking = False

    # ------------------------------------------------------------------ #
    @property
    def _use_eleven(self) -> bool:
        return (self.engine_name == "elevenlabs"
                and bool(self.eleven_api_key) and _REQUESTS)

    def say(self, text: str) -> None:
        if text:
            self._queue.put(text)

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)

    def set_properties(self, rate=None, volume=None, voice_hint=None,
                       engine=None, eleven_api_key=None, eleven_voice_id=None,
                       eleven_model=None) -> None:
        """Update TTS properties live (from the Settings dialog)."""
        if rate is not None:
            self.rate = int(rate)
        if volume is not None:
            self.volume = float(volume)
        if voice_hint is not None:
            self.voice_hint = (voice_hint or "").lower()
        if engine is not None:
            self.engine_name = (engine or "pyttsx3").lower()
        if eleven_api_key is not None:
            self.eleven_api_key = eleven_api_key or ""
            self._eleven_ok = None  # re-test on next use
        if eleven_voice_id is not None:
            self.eleven_voice_id = eleven_voice_id or DEFAULT_JARVIS_VOICE_ID
        if eleven_model is not None:
            self.eleven_model = eleven_model or "eleven_turbo_v2_5"
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
    def _make_engine(self):
        """Create a *fresh* pyttsx3 engine configured with current properties.

        IMPORTANT: On Windows (SAPI5) a single engine instance only speaks
        reliably on the FIRST ``runAndWait()`` — subsequent calls silently do
        nothing, which is why Jarvis used to talk only once (the greeting) and
        then go quiet. Building a new engine per utterance avoids that entirely.
        """
        if not _TTS:
            return None
        try:
            eng = pyttsx3.init()
            eng.setProperty("rate", self.rate)
            eng.setProperty("volume", self.volume)
            if self.voice_hint:
                for v in eng.getProperty("voices"):
                    meta = f"{getattr(v, 'name', '')} {getattr(v, 'id', '')}".lower()
                    if self.voice_hint in meta:
                        eng.setProperty("voice", v.id)
                        break
            return eng
        except Exception as exc:
            self.unavailable.emit(f"TTS engine failed to start: {exc}")
            return None

    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D401
        if not _TTS:
            self.unavailable.emit("pyttsx3 not installed; voice output disabled.")
            return
        # Verify we can build an engine at least once (surfaces missing voices).
        probe = self._make_engine()
        if probe is None:
            return
        try:
            probe.stop()
        except Exception:
            pass
        del probe

        self._running = True
        while self._running:
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break
            self._speak_one(text)

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

        spoke = False
        try:
            # Preferred path: the true Iron-Man "JARVIS" voice via ElevenLabs.
            if self._use_eleven:
                spoke = self._speak_eleven(text)
            # Fallback (or default): local pyttsx3 — fresh engine every time,
            # see _make_engine() docstring for why.
            if not spoke:
                engine = self._make_engine()
                if engine is not None:
                    self._engine = engine  # so set_properties() can peek
                    try:
                        engine.say(text)
                        engine.runAndWait()
                    finally:
                        try:
                            engine.stop()
                        except Exception:
                            pass
                        self._engine = None
        except Exception:
            pass
        finally:
            stop_amp.set()
            self._speaking = False
            self.amplitude.emit(0.0)
            self.speaking_finished.emit()

    # ------------------------------------------------------------------ #
    def _speak_eleven(self, text: str) -> bool:
        """Synthesize `text` with ElevenLabs and play it. Returns True on success.

        On any failure (bad key, no network, playback tool missing) we return
        False so the caller falls back to local pyttsx3 — Jarvis always talks.
        """
        if not (_REQUESTS and self.eleven_api_key):
            return False
        pcm, err = eleven_synthesize(
            self.eleven_api_key, self.eleven_voice_id or DEFAULT_JARVIS_VOICE_ID,
            text, self.eleven_model or "eleven_turbo_v2_5")
        if pcm is None:
            self._eleven_ok = False
            if err:
                self.unavailable.emit(f"ElevenLabs: {err} — using system voice.")
            return False
        self._eleven_ok = True
        # ElevenLabs pcm_24000 = raw 16-bit mono PCM @ 24 kHz. Wrap as WAV.
        wav_path = _pcm_to_wav_file(pcm, rate=24000)
        if not wav_path:
            return False
        played = play_wav_file(wav_path)
        try:
            os.remove(wav_path)
        except Exception:
            pass
        return played
