# JARVIS v1 — Windows Native Desktop AI Agent

An Iron Man‑style personal AI assistant that runs natively on your Windows
desktop. A glowing arc‑reactor **orb** floats on a dark HUD; talk to it or type,
and it can reason with an LLM, monitor your system, control your mouse/keyboard,
manage apps, drive a browser — and **rewrite itself** to learn brand‑new skills
on the fly.

```
        ╭──────────────────────────────╮
        │        ◌  J A R V I S  ◌       │
        │   "Add the ability to …"       │
        ╰──────────────────────────────╯
```

---

## ✨ Features

| Capability | What it does |
|---|---|
| 🔵 **Iron Man Orb UI** | Fully hand‑drawn PyQt6 orb — radial‑gradient core, layered glow, rotating segmented rings, orbiting particles, and reactive voice waveforms. Runs at 60 FPS. |
| 🧠 **LLM Brain** | OpenAI‑compatible chat + strict JSON intent parsing. Graceful **offline mode** with rule‑based intents and canned replies when no API key is set. |
| 📊 **System Monitoring** | CPU, RAM, disk, GPU, network and top processes — spoken aloud and shown in sliding HUD panels with animated gauges. |
| 🖱️ **Mouse & Keyboard Control** | Human‑like movement/clicks/typing/scrolling/hotkeys and screenshots via PyAutoGUI (fail‑safe enabled). |
| 📦 **App Management** | Launch, close, install (winget) and uninstall apps by name, with a built‑in alias table for common programs. |
| 🧩 **Self‑Modification Engine** | Say *“Jarvis, add the ability to …”* → it writes a new Python plugin, validates & safety‑scans it, saves it to `plugins/`, and **hot‑reloads it live** — no restart. |
| 🗣️ **Voice I/O** | Speech recognition (SpeechRecognition) + text‑to‑speech (pyttsx3), each on its own thread, with mic auto‑pause while speaking. |
| 🌐 **Browser Automation** | Open, navigate, search, click and type on web pages via Playwright. |
| 🤖 **Autonomous Tasks** | Breaks a high‑level goal into steps, executes them one‑by‑one, and reports back. |

---

## 🖥️ Requirements

- **Windows 10 / 11**
- **Python 3.11+**  (add it to PATH during install)
- A microphone (optional, for voice input)
- An OpenAI‑compatible **API key** (optional, but unlocks full reasoning)

---

## 🚀 Quick Start

```bat
:: 1. Clone / copy this folder, then run the installer:
setup.bat

:: 2. Set your API key (recommended: environment variable)
setx OPENAI_API_KEY "sk-your-key-here"

:: 3. Launch
run.bat
```

`setup.bat` creates a virtual environment in `.venv`, installs everything in
`requirements.txt`, and downloads the Playwright Chromium browser.

> **PyAudio trouble?** If `pip` can’t build PyAudio, run:
> `python -m pip install pipwin && pipwin install pyaudio`

---

## 🔑 Configuration — `config/settings.json`

```jsonc
{
  "llm": {
    "api_key_env": "OPENAI_API_KEY",  // env var to read the key from
    "api_key": "",                     // or paste the key directly (less safe)
    "base_url": "",                    // set for Azure / local / Abacus gateways
    "model": "gpt-4o-mini",
    "temperature": 0.6,
    "max_tokens": 800
  },
  "voice": {
    "enabled": true,
    "wake_word": "jarvis",
    "require_wake_word": false,   // true = only respond after hearing "jarvis"
    "speak_responses": true,
    "rate": 178, "volume": 1.0, "voice_hint": "",
    "language": "en-US", "stt_engine": "google"  // or "sphinx" for offline STT
  },
  "ui": { "start_fullscreen": false, "width": 1100, "height": 720, "fps": 60,
          "show_hud_on_start": false },
  "plugins": { "hot_reload": true, "allow_unsafe_plugins": false },
  "browser": { "headless": false,
               "search_engine": "https://www.google.com/search?q=" }
}
```

**Using a non‑OpenAI endpoint** (Azure OpenAI, LM Studio, Ollama’s OpenAI shim,
Abacus AI, etc.): set `base_url` to the endpoint and put the key in the env var.

---

## 🎮 Usage

Launch with `run.bat` (or `python main.py`). The orb appears on a dark HUD.

- **Type** in the box at the bottom and press **Enter**, or
- **Speak** — click the 🎙 button or press **Ctrl+Space**.

| Shortcut | Action |
|---|---|
| `Ctrl+Space` | Talk / focus mic |
| `Ctrl+H` | Toggle HUD stat panels |
| `F11` | Fullscreen |
| `Esc` | Exit fullscreen / hide HUD |

### Example commands

```
"What's my CPU and memory usage?"
"Show me the top processes."
"Open Notepad."          "Close Chrome."
"Install VLC."           "Uninstall Spotify."
"Search the web for the weather in Tokyo."
"Take a screenshot."
"Type Hello world."
"Do the following: open the browser, search for PyQt6 tutorials."
"Jarvis, add the ability to tell me a random programming joke."
```

---

## 🧩 The Self‑Modification Engine (how it works)

1. You say **“add the ability to X”**.
2. The **Brain** asks the LLM to generate a plugin that subclasses `JarvisPlugin`.
3. The engine **validates syntax** (with one auto‑repair attempt) and runs a
   **safety scan** (blocks obvious destructive patterns unless
   `allow_unsafe_plugins` is enabled).
4. The file is written to `plugins/<name>.py` and **imported live** via
   `importlib`. A `watchdog` observer also hot‑reloads any plugin you edit by
   hand.
5. New keywords route matching utterances straight to your plugin.

### Plugin anatomy

```python
from modules.self_modify import JarvisPlugin

class JokePlugin(JarvisPlugin):
    name = "programming_joke"
    description = "Tells a random programming joke."
    keywords = ["joke", "make me laugh", "funny"]

    def handle(self, user_text, agent=None):
        import random
        jokes = ["There are 10 kinds of people: those who read binary and those who don't."]
        return random.choice(jokes)
```

Drop a file like that into `plugins/` and it loads instantly — that’s the same
mechanism Jarvis uses on itself.

> **Offline note:** without an API key, the engine still creates a working
> **template plugin** (registered and callable) that you can fill in later.

---

## 📁 Project Structure

```
jarvis/
├── core/
│   ├── brain.py          # LLM orchestration + offline fallback + intent parsing
│   ├── agent.py          # Dispatch + autonomous multi-step task loop
│   └── memory.py         # Conversation history + facts (persisted)
├── modules/
│   ├── system_monitor.py # CPU/RAM/disk/GPU/network/processes (psutil)
│   ├── input_control.py  # Mouse/keyboard/screenshots (PyAutoGUI)
│   ├── app_manager.py    # Launch/close/install/uninstall (winget/subprocess)
│   ├── browser.py        # Playwright browser automation
│   └── self_modify.py    # Plugin base class + hot-reload self-modification engine
├── ui/
│   ├── orb_widget.py     # The animated Iron Man orb (QPainter)
│   ├── hud_panel.py      # Sliding stat panels + animated gauges
│   └── voice_handler.py  # Voice input/output threads
├── plugins/              # Hot-loadable capabilities (starts empty)
├── config/
│   └── settings.json     # User config + API keys + preferences
├── main.py               # Entry point — wires everything together
├── requirements.txt
├── setup.bat             # Windows installer
├── run.bat               # Windows launcher
└── README.md
```

---

## 🧵 Architecture notes

- The **Qt GUI thread** only paints — it never blocks.
- All agent work (LLM, browser, system control) runs on a single persistent
  **AgentWorker** thread; results return via Qt signals.
- **Voice input** and **TTS** each run on their own threads; the mic auto‑pauses
  while Jarvis speaks to avoid feedback.
- Playwright’s sync API is always driven from the one worker thread (its
  requirement), keeping browser sessions stable across commands.

---

## 🩹 Troubleshooting

| Symptom | Fix |
|---|---|
| “offline mode” greeting | No API key detected — set `OPENAI_API_KEY` or edit `settings.json`. |
| No voice input | Install PyAudio (see note above) and check your microphone. |
| No speech output | Ensure `pyttsx3` installed; Windows SAPI5 voices are used by default. |
| Browser commands fail | Run `python -m playwright install chromium`. |
| GPU shows 0/blank | No supported GPU telemetry (needs NVIDIA `nvidia-smi` or GPUtil). |
| App install fails | Requires **winget** (App Installer from the Microsoft Store). |

---

## 🔐 Safety & Privacy

- API keys are read from environment variables or `settings.json` — **never
  hard‑coded**.
- Self‑generated plugins are syntax‑checked and safety‑scanned; destructive
  patterns are blocked unless you explicitly opt in.
- PyAutoGUI’s fail‑safe is on: slam the mouse into a screen corner to abort any
  automation instantly.

---

*JARVIS v1 — “Sometimes you gotta run before you can walk.”*
