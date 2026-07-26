"""
core/memory.py
==============
Conversation memory + long-term context store for Jarvis.

Keeps a rolling short-term window of the most recent turns (used to build the
LLM prompt) and persists the full conversation to disk so context survives
restarts. Also stores small key/value "facts" that Jarvis learns about the user.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional


class Memory:
    """Thread-safe conversation + fact memory."""

    def __init__(
        self,
        store_path: str = "config/memory.json",
        short_term_size: int = 20,
        max_persisted: int = 2000,
    ) -> None:
        self.store_path = os.path.abspath(store_path)
        self.short_term_size = short_term_size
        self.max_persisted = max_persisted

        self._lock = threading.RLock()
        self._short_term: deque = deque(maxlen=short_term_size)
        self._history: List[Dict[str, Any]] = []
        self._facts: Dict[str, Any] = {}

        self._load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """Load prior conversation + facts from disk if present."""
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._history = data.get("history", [])
            self._facts = data.get("facts", {})
            # Rebuild short-term window from the tail of history.
            for turn in self._history[-self.short_term_size:]:
                self._short_term.append(turn)
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable store — start fresh but don't crash.
            self._history = []
            self._facts = {}

    def _save(self) -> None:
        """Persist history + facts atomically."""
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        tmp = self.store_path + ".tmp"
        payload = {
            "history": self._history[-self.max_persisted:],
            "facts": self._facts,
            "updated_at": datetime.utcnow().isoformat(),
        }
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.store_path)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # Turn management
    # ------------------------------------------------------------------ #
    def add(self, role: str, content: str, **meta: Any) -> None:
        """Add a single message turn.

        role: 'user' | 'assistant' | 'system' | 'tool'
        """
        if not content:
            return
        turn = {
            "role": role,
            "content": content,
            "ts": time.time(),
        }
        if meta:
            turn["meta"] = meta
        with self._lock:
            self._short_term.append(turn)
            self._history.append(turn)
            self._save()

    def add_user(self, content: str, **meta: Any) -> None:
        self.add("user", content, **meta)

    def add_assistant(self, content: str, **meta: Any) -> None:
        self.add("assistant", content, **meta)

    # ------------------------------------------------------------------ #
    # Prompt building
    # ------------------------------------------------------------------ #
    def context_messages(self, system_prompt: Optional[str] = None) -> List[Dict[str, str]]:
        """Return an OpenAI-style messages list built from short-term memory."""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Inject known facts as a compact system note.
        with self._lock:
            if self._facts:
                fact_lines = "\n".join(f"- {k}: {v}" for k, v in self._facts.items())
                messages.append(
                    {
                        "role": "system",
                        "content": "Known facts about the user / environment:\n" + fact_lines,
                    }
                )
            for turn in self._short_term:
                role = turn["role"]
                # Only pass roles the chat API understands.
                if role not in ("user", "assistant", "system"):
                    role = "user"
                messages.append({"role": role, "content": turn["content"]})
        return messages

    def recent(self, n: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._short_term)[-n:]

    # ------------------------------------------------------------------ #
    # Facts
    # ------------------------------------------------------------------ #
    def remember_fact(self, key: str, value: Any) -> None:
        with self._lock:
            self._facts[key] = value
            self._save()

    def get_fact(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._facts.get(key, default)

    def all_facts(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._facts)

    # ------------------------------------------------------------------ #
    # Maintenance
    # ------------------------------------------------------------------ #
    def clear(self, keep_facts: bool = True) -> None:
        with self._lock:
            self._short_term.clear()
            self._history.clear()
            if not keep_facts:
                self._facts.clear()
            self._save()

    def summary(self) -> str:
        with self._lock:
            return (
                f"{len(self._history)} total turns, "
                f"{len(self._short_term)} in short-term window, "
                f"{len(self._facts)} facts."
            )
