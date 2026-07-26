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

from core import providers as _providers


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
      "project_create", "project_run", "project_change", "self_read",
      "self_duplicate", "self_promote", "self_discard",
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
  - project_create: {"name": "project name", "description": "what it should do"}
  - project_run: {"name": "project name"}
  - project_change: {"name": "project name", "change": "what to change"}
  - self_read: {"query": "optional focus, e.g. 'how does the voice work'"}
  - self_duplicate: {"changes": "the improvement to make to Jarvis itself"}
  - self_promote: {}   (user approves applying the reviewed self-change: "update", "apply it", "do it")
  - self_discard: {}   (user rejects the pending self-change: "discard", "cancel that")
  - autonomous_task: {"goal": "the high-level goal to accomplish"}
  - chat / exit: {}

Notes:
  - Use project_create when the user asks to build/make/create an app, game, script or website.
  - Use self_duplicate when the user asks YOU (Jarvis) to change/improve YOURSELF or your own code.
  - Use self_promote only for short approvals AFTER a self-change was proposed.

Return strictly valid JSON.
"""

VALID_INTENTS = {
    "chat", "system_stats", "input_control", "app_launch", "app_close",
    "app_install", "app_uninstall", "browser", "self_modify",
    "project_create", "project_run", "project_change", "self_read",
    "self_duplicate", "self_promote", "self_discard",
    "autonomous_task", "exit",
}


class Brain:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        self.temperature = 0.6
        self.max_tokens = 800
        # Cache of OpenAI clients keyed by (api_key, base_url) so we don't rebuild
        # a client on every request.
        self._clients: Dict[tuple, Any] = {}
        # The last provider we actually routed to (for UI display).
        self.last_provider: Optional[str] = None
        self.configure(self.config.get("llm", {}))

    # ------------------------------------------------------------------ #
    def configure(self, llm_cfg: Dict[str, Any]) -> bool:
        """Re-apply LLM settings live (e.g. after the user connects a provider in
        the Settings dialog). Returns is_online."""
        llm_cfg = llm_cfg or {}
        # --- Back-compat: migrate the old single-key schema into providers. ---
        llm_cfg = self._migrate(llm_cfg)
        self.config["llm"] = llm_cfg
        self.temperature = float(llm_cfg.get("temperature", 0.6))
        self.max_tokens = int(llm_cfg.get("max_tokens", 800))
        self._clients.clear()
        return self.is_online

    @staticmethod
    def _migrate(llm_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Fold a legacy {api_key, base_url, model} into the providers map, and
        pull any provider keys from environment variables."""
        llm_cfg = dict(llm_cfg)
        provs = dict(llm_cfg.get("providers", {}))

        # Legacy single key -> OpenAI provider slot.
        legacy_key = llm_cfg.get("api_key")
        if legacy_key and not provs.get("openai", {}).get("api_key"):
            provs["openai"] = {"api_key": legacy_key}
            if llm_cfg.get("model"):
                provs["openai"]["model"] = llm_cfg["model"]

        # Environment variables per provider (never overwrite an explicit key).
        env_map = {
            "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
            "google": "GEMINI_API_KEY", "mistral": "MISTRAL_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY",
        }
        for pid, env in env_map.items():
            val = os.environ.get(env)
            if val and not provs.get(pid, {}).get("api_key"):
                provs.setdefault(pid, {})["api_key"] = val

        llm_cfg["providers"] = provs
        llm_cfg.setdefault("active", "auto")
        return llm_cfg

    # ------------------------------------------------------------------ #
    def _get_client(self, api_key: str, base_url: str):
        key = (api_key, base_url)
        if key in self._clients:
            return self._clients[key]
        if not _OPENAI_AVAILABLE or not api_key:
            return None
        try:
            kwargs: Dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = OpenAI(**kwargs)
            self._clients[key] = client
            return client
        except Exception:
            return None

    def _route(self, hint: str = ""):
        """Pick a provider for this request. Returns (client, model, label) or
        (None, None, None) if nothing is connected."""
        creds = _providers.resolve(self.config.get("llm", {}), hint=hint)
        if not creds:
            return None, None, None
        client = self._get_client(creds["api_key"], creds["base_url"])
        if not client:
            return None, None, None
        self.last_provider = creds["label"]
        return client, creds["model"], creds["label"]

    @property
    def is_online(self) -> bool:
        """Online if at least one provider is connected and openai SDK present."""
        if not _OPENAI_AVAILABLE:
            return False
        llm = self.config.get("llm", {})
        return bool(_providers.connected_providers(llm)
                    or _providers.connected_agents(llm))

    def status_line(self) -> str:
        """Human-readable summary of connected providers for the UI."""
        llm = self.config.get("llm", {})
        provs = _providers.connected_providers(llm)
        agents = _providers.connected_agents(llm)
        if not provs and not agents:
            return "OFFLINE — no AI provider connected. Open Settings (gear) to add a key."
        names = [ _providers.PROVIDERS[p]["label"] for p in provs ]
        names += [ _providers.AGENTS[a]["label"] for a in agents ]
        return "Connected: " + ", ".join(names)

    # ------------------------------------------------------------------ #
    # Raw chat
    # ------------------------------------------------------------------ #
    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Return a natural-language completion for the given messages."""
        # Route based on the latest user message so Jarvis picks the best
        # connected provider for the request.
        hint = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                hint = m.get("content", "")
                break
        client, model, _label = self._route(hint)
        if not client:
            return self._offline_reply(messages)
        try:
            resp = client.chat.completions.create(
                model=model,
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

        client, model, _label = self._route(user_text)
        if not client:
            return rule

        messages: List[Dict[str, str]] = [{"role": "system", "content": INTENT_PROMPT}]
        if context:
            # Include a little conversation context to disambiguate references.
            messages.extend(context[-6:])
        messages.append({"role": "user", "content": user_text})

        try:
            resp = client.chat.completions.create(
                model=model,
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
        # Prefer a code-strong provider for code generation.
        client, model, _label = self._route("write python code " + instruction)
        if not client:
            return ""
        try:
            resp = client.chat.completions.create(
                model=model,
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
