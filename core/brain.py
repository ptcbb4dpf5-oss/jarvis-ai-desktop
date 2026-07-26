"""
core/brain.py
=============
The LLM "brain" of Jarvis.

Responsibilities:
  * Talk to an OpenAI-compatible chat completion API (OpenAI, Abacus AI,
    local LM Studio / Ollama gateways, etc.).
  * Provide structured "intent" parsing so the agent can decide which module
    to invoke (system stats, input control, app management, self-modify, ...).
  * Degrade gracefully to an OFFLINE mode with canned/rule-based responses when
    no API key is configured or the network is unavailable.

The brain never imports UI code — it is pure logic and can be unit-tested.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

try:  # openai is optional at import time; offline mode still works without it.
    from openai import OpenAI  # type: ignore
    _OPENAI_AVAILABLE = True
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore
    _OPENAI_AVAILABLE = False


SYSTEM_PROMPT = """You are JARVIS, a witty, hyper-capable Iron Man-style AI assistant
running natively on the user's Windows PC. You are concise, confident, and helpful,
addressing the user as "sir" occasionally but never overdoing it.

You can control the computer through a set of capabilities:
  - system stats (CPU, RAM, disk, GPU, network, processes)
  - mouse & keyboard control
  - launching / closing / installing apps
  - browser automation
  - self-modification: writing new Python plugins to extend yourself

When the user asks you to DO something actionable, respond conversationally AND
the host program will separately parse intent. Keep spoken replies short (1-3
sentences) since they will be read aloud by text-to-speech.
"""

# Intent classification prompt — asks the model to return strict JSON.
INTENT_PROMPT = """You are the intent parser for the JARVIS assistant.
Given the user's message, output ONLY a JSON object (no prose, no markdown fences)
with this schema:

{
  "intent": one of [
      "chat", "system_stats", "input_control", "app_launch", "app_close",
      "app_install", "app_uninstall", "browser", "self_modify",
      "autonomous_task", "exit"
  ],
  "args": { ... intent-specific arguments ... },
  "speak": "a short natural-language reply to say to the user"
}

Guidelines for args:
  - system_stats: {"metric": "cpu"|"ram"|"disk"|"gpu"|"network"|"processes"|"all"}
  - input_control: {"action": "move"|"click"|"double_click"|"right_click"|"type"|"scroll"|"hotkey"|"screenshot",
                    "x": int?, "y": int?, "text": str?, "keys": [str]?, "amount": int?}
  - app_launch / app_close: {"name": "app name"}
  - app_install / app_uninstall: {"name": "app name"}
  - browser: {"action": "open"|"navigate"|"search"|"click"|"type"|"close",
              "url": str?, "query": str?, "selector": str?, "text": str?}
  - self_modify: {"capability": "plain-English description of the new ability"}
  - autonomous_task: {"goal": "the high-level goal to accomplish"}
  - chat / exit: {}

Return strictly valid JSON.
"""

VALID_INTENTS = {
    "chat", "system_stats", "input_control", "app_launch", "app_close",
    "app_install", "app_uninstall", "browser", "self_modify",
    "autonomous_task", "exit",
}


class Brain:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        llm_cfg = self.config.get("llm", {})

        # Resolve API key: env var wins, then config file.
        self.api_key = (
            os.environ.get(llm_cfg.get("api_key_env", "OPENAI_API_KEY"))
            or llm_cfg.get("api_key")
            or os.environ.get("ABACUS_API_KEY")
        )
        self.base_url = llm_cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL")
        self.model = llm_cfg.get("model", "gpt-4o-mini")
        self.temperature = float(llm_cfg.get("temperature", 0.6))
        self.max_tokens = int(llm_cfg.get("max_tokens", 800))

        self._client: Optional[Any] = None
        self.online = False
        self._init_client()

    # ------------------------------------------------------------------ #
    def _init_client(self) -> None:
        if not _OPENAI_AVAILABLE or not self.api_key:
            self.online = False
            return
        try:
            kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
            self.online = True
        except Exception:
            self._client = None
            self.online = False

    @property
    def is_online(self) -> bool:
        return self.online and self._client is not None

    # ------------------------------------------------------------------ #
    # Raw chat
    # ------------------------------------------------------------------ #
    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Return a natural-language completion for the given messages."""
        if not self.is_online:
            return self._offline_reply(messages)
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # network / auth / rate limit
            return self._offline_reply(messages, error=str(exc))

    # ------------------------------------------------------------------ #
    # Intent parsing
    # ------------------------------------------------------------------ #
    def parse_intent(self, user_text: str, context: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Classify a user utterance into a structured intent dict."""
        # Always try the fast rule-based parser first — it's free and instant,
        # and it also serves as the offline fallback.
        rule = self._rule_based_intent(user_text)

        if not self.is_online:
            return rule

        messages: List[Dict[str, str]] = [{"role": "system", "content": INTENT_PROMPT}]
        if context:
            # Include a little conversation context to disambiguate references.
            messages.extend(context[-6:])
        messages.append({"role": "user", "content": user_text})

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
            raw = (resp.choices[0].message.content or "").strip()
            data = self._safe_json(raw)
            if data and data.get("intent") in VALID_INTENTS:
                data.setdefault("args", {})
                data.setdefault("speak", "")
                return data
        except Exception:
            pass
        return rule

    # ------------------------------------------------------------------ #
    # Offline / rule-based helpers
    # ------------------------------------------------------------------ #
    def _rule_based_intent(self, text: str) -> Dict[str, Any]:
        t = text.lower().strip()

        def resp(intent: str, args: Dict[str, Any], speak: str) -> Dict[str, Any]:
            return {"intent": intent, "args": args, "speak": speak}

        # Exit
        if re.search(r"\b(exit|quit|shut ?down|goodbye|good night)\b", t):
            return resp("exit", {}, "Powering down. Goodbye, sir.")

        # Self-modification
        m = re.search(r"(?:add (?:the )?ability to|learn to|teach yourself to|add a capability to|extend yourself to)\s+(.*)", t)
        if m:
            cap = m.group(1).strip(" .")
            return resp("self_modify", {"capability": cap},
                        f"Understood. Let me write the code to {cap}.")

        # System stats
        if re.search(r"\b(cpu|processor)\b", t):
            return resp("system_stats", {"metric": "cpu"}, "Checking the processor now.")
        if re.search(r"\b(ram|memory)\b", t):
            return resp("system_stats", {"metric": "ram"}, "Pulling up memory usage.")
        if re.search(r"\b(disk|storage|drive)\b", t):
            return resp("system_stats", {"metric": "disk"}, "Reading disk stats.")
        if re.search(r"\b(gpu|graphics)\b", t):
            return resp("system_stats", {"metric": "gpu"}, "Querying the GPU.")
        if re.search(r"\b(network|bandwidth|internet)\b", t):
            return resp("system_stats", {"metric": "network"}, "Checking the network.")
        if re.search(r"\b(process(es)?|task manager)\b", t):
            return resp("system_stats", {"metric": "processes"}, "Listing top processes.")
        if re.search(r"\b(system status|status report|diagnostics|how are (you|things))\b", t):
            return resp("system_stats", {"metric": "all"}, "Running a full diagnostic.")

        # App management
        m = re.search(r"\b(?:install)\s+(.+)", t)
        if m:
            name = m.group(1).strip(" .")
            return resp("app_install", {"name": name}, f"Installing {name}.")
        m = re.search(r"\b(?:uninstall|remove)\s+(.+)", t)
        if m:
            name = m.group(1).strip(" .")
            return resp("app_uninstall", {"name": name}, f"Uninstalling {name}.")
        m = re.search(r"\b(?:close|quit|kill)\s+(.+)", t)
        if m:
            name = m.group(1).strip(" .")
            return resp("app_close", {"name": name}, f"Closing {name}.")
        m = re.search(r"\b(?:open|launch|start|run)\s+(.+)", t)
        if m:
            name = m.group(1).strip(" .")
            # Heuristic: if it looks like a URL, route to browser.
            if re.search(r"\.(com|net|org|io|dev|ai|gov|edu)\b", name) or name.startswith("http"):
                return resp("browser", {"action": "open", "url": name}, f"Opening {name}.")
            return resp("app_launch", {"name": name}, f"Launching {name}.")

        # Browser
        m = re.search(r"\b(?:search (?:for|the web for)?)\s+(.+)", t)
        if m:
            q = m.group(1).strip(" .")
            return resp("browser", {"action": "search", "query": q}, f"Searching for {q}.")
        if re.search(r"\bbrowser\b", t):
            return resp("browser", {"action": "open"}, "Opening the browser.")

        # Input control
        if re.search(r"\bscreenshot\b", t):
            return resp("input_control", {"action": "screenshot"}, "Taking a screenshot.")
        if re.search(r"\btype\b", t):
            m2 = re.search(r"type\s+(.+)", t)
            return resp("input_control", {"action": "type", "text": m2.group(1) if m2 else ""},
                        "Typing that out.")
        if re.search(r"\bclick\b", t):
            return resp("input_control", {"action": "click"}, "Clicking.")

        # Autonomous task
        if re.search(r"\b(do the following|complete this task|accomplish|carry out|for me:)\b", t):
            return resp("autonomous_task", {"goal": text}, "On it. Breaking that into steps.")

        # Default: chat
        return resp("chat", {}, "")

    def _offline_reply(self, messages: List[Dict[str, str]], error: Optional[str] = None) -> str:
        """Canned responses when the LLM is unreachable."""
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "").lower()
                break

        greetings = ("hello", "hi ", "hey", "good morning", "good evening")
        if any(g in last_user for g in greetings):
            return "Online and standing by, sir. Note: I'm running in offline mode right now."
        if "who are you" in last_user or "your name" in last_user:
            return "I am JARVIS, your desktop assistant. Currently operating offline with limited reasoning."
        if "thank" in last_user:
            return "Always a pleasure, sir."
        if "time" in last_user:
            from datetime import datetime
            return f"It's {datetime.now().strftime('%I:%M %p')}."
        if "date" in last_user:
            from datetime import datetime
            return f"Today is {datetime.now().strftime('%A, %B %d, %Y')}."

        base = ("I'm in offline mode, sir, so my conversational range is limited. "
                "I can still monitor the system, control input, manage apps, and run local commands. "
                "Set an API key to unlock my full reasoning.")
        return base

    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_json(raw: str) -> Optional[Dict[str, Any]]:
        """Extract a JSON object from a possibly noisy model response."""
        if not raw:
            return None
        # Strip markdown fences if present.
        raw = re.sub(r"^```(?:json)?", "", raw.strip())
        raw = re.sub(r"```$", "", raw.strip())
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to find the first {...} block.
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
        return None

    # ------------------------------------------------------------------ #
    def generate_code(self, instruction: str, extra_context: str = "") -> str:
        """Ask the LLM to generate raw Python source (used by self_modify).

        Returns the raw code string. Falls back to a minimal template offline.
        """
        if not self.is_online:
            return ""  # self_modify has its own offline template fallback.

        sys_prompt = (
            "You are an expert Python engineer generating a JARVIS plugin. "
            "Return ONLY valid Python source code, no markdown fences, no commentary."
        )
        user_prompt = instruction
        if extra_context:
            user_prompt += "\n\nContext:\n" + extra_context
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=1500,
            )
            code = (resp.choices[0].message.content or "").strip()
            code = re.sub(r"^```(?:python)?", "", code.strip())
            code = re.sub(r"```$", "", code.strip())
            return code.strip()
        except Exception:
            return ""
