"""
core/providers.py
=================
A clean, noob-friendly registry of AI providers (and agent presets) that Jarvis
can connect to. Each provider is OpenAI-compatible, so connecting one is just a
matter of pasting an API key.

The whole point: the user pastes a key next to a provider name, and Jarvis
figures out the rest (endpoint, default model, routing). A single "main free"
provider (Groq) is preferred by default, and Jarvis auto-routes each request to
the best connected provider.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #
# Each entry is fully self-describing so the UI can render a card with zero
# extra logic:
#   label       — friendly name shown on the card
#   tier        — "free" or "paid" (drives the badge)
#   blurb       — one short line under the name
#   get_key_url — where to sign up / grab a key ("Get key" hyperlink)
#   key_prefix  — placeholder hint shown in the paste box (e.g. "gsk_...")
#   base_url    — OpenAI-compatible endpoint
#   model       — sensible default model for this provider
#   strengths   — tags used by the auto-router to pick the right provider
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "groq": {
        "label": "Groq",
        "tier": "free",
        "blurb": "Blazing-fast Llama & Mixtral models. Best free default.",
        "get_key_url": "https://console.groq.com/keys",
        "key_prefix": "gsk_...",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "strengths": ["general", "fast", "code", "reasoning"],
    },
    "google": {
        "label": "Google Gemini",
        "tier": "free",
        "blurb": "Generous free tier. Great all-rounder with huge context.",
        "get_key_url": "https://aistudio.google.com/app/apikey",
        "key_prefix": "AIza...",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.0-flash",
        "strengths": ["general", "vision", "long_context", "reasoning"],
    },
    "mistral": {
        "label": "Mistral",
        "tier": "free",
        "blurb": "Free tier available. Strong, efficient European models.",
        "get_key_url": "https://console.mistral.ai/api-keys/",
        "key_prefix": "...",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-large-latest",
        "strengths": ["general", "code", "fast"],
    },
    "openai": {
        "label": "OpenAI",
        "tier": "paid",
        "blurb": "GPT-4o and friends. Top quality, pay as you go.",
        "get_key_url": "https://platform.openai.com/api-keys",
        "key_prefix": "sk-...",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "strengths": ["general", "code", "reasoning", "vision"],
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "tier": "paid",
        "blurb": "Claude models. Excellent at writing & long reasoning.",
        "get_key_url": "https://console.anthropic.com/settings/keys",
        "key_prefix": "sk-ant-...",
        "base_url": "https://api.anthropic.com/v1/",
        "model": "claude-3-5-sonnet-latest",
        "strengths": ["reasoning", "writing", "long_context", "code"],
    },
    "openrouter": {
        "label": "OpenRouter",
        "tier": "paid",
        "blurb": "One key, hundreds of models (incl. free ones).",
        "get_key_url": "https://openrouter.ai/keys",
        "key_prefix": "sk-or-...",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct",
        "strengths": ["general", "variety", "code", "reasoning"],
    },
}

# --------------------------------------------------------------------------- #
# Agent presets (e.g. Hermes). These are specialised model personalities that
# ride on top of a provider you've already connected — so no separate key is
# needed if the underlying provider is connected.
# --------------------------------------------------------------------------- #
AGENTS: Dict[str, Dict[str, Any]] = {
    "hermes": {
        "label": "Hermes",
        "tier": "free",
        "blurb": "Nous Research Hermes — steerable, uncensored agent brain.",
        "get_key_url": "https://openrouter.ai/keys",
        "key_prefix": "sk-or-...",
        "via": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "nousresearch/hermes-3-llama-3.1-70b",
        "strengths": ["agentic", "roleplay", "reasoning", "tools"],
    },
    "deepseek": {
        "label": "DeepSeek R1",
        "tier": "free",
        "blurb": "Deep step-by-step reasoning agent (via OpenRouter).",
        "get_key_url": "https://openrouter.ai/keys",
        "key_prefix": "sk-or-...",
        "via": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-r1",
        "strengths": ["reasoning", "math", "agentic", "code"],
    },
    "qwen_coder": {
        "label": "Qwen Coder",
        "tier": "free",
        "blurb": "Coding-specialist agent (via OpenRouter).",
        "get_key_url": "https://openrouter.ai/keys",
        "key_prefix": "sk-or-...",
        "via": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen-2.5-coder-32b-instruct",
        "strengths": ["code", "agentic"],
    },
}

# Preferred order when auto-picking a provider (free & fast first).
PREFERENCE_ORDER: List[str] = ["groq", "google", "mistral", "openrouter", "openai", "anthropic"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def all_specs() -> Dict[str, Dict[str, Any]]:
    """Return providers + agents merged (agents keyed with an 'agent:' prefix)."""
    merged: Dict[str, Dict[str, Any]] = dict(PROVIDERS)
    for k, v in AGENTS.items():
        merged[f"agent:{k}"] = v
    return merged


def spec_for(key: str) -> Optional[Dict[str, Any]]:
    if key.startswith("agent:"):
        return AGENTS.get(key.split(":", 1)[1])
    return PROVIDERS.get(key)


def connected_providers(llm_cfg: Dict[str, Any]) -> List[str]:
    """List provider ids that have a non-empty key saved."""
    provs = (llm_cfg or {}).get("providers", {})
    return [pid for pid in PROVIDERS
            if provs.get(pid, {}).get("api_key", "").strip()]


def connected_agents(llm_cfg: Dict[str, Any]) -> List[str]:
    """List agent ids that are usable (own key OR underlying provider connected)."""
    provs = (llm_cfg or {}).get("providers", {})
    usable = []
    for aid, spec in AGENTS.items():
        own = provs.get(f"agent:{aid}", {}).get("api_key", "").strip()
        via = spec.get("via")
        via_key = provs.get(via, {}).get("api_key", "").strip() if via else ""
        if own or via_key:
            usable.append(aid)
    return usable


def resolve(llm_cfg: Dict[str, Any], hint: str = "") -> Optional[Dict[str, Any]]:
    """Choose which provider/agent to actually call for a request.

    Returns a dict: {"api_key", "base_url", "model", "id", "label"} or None if
    nothing is connected.

    Routing rules (simple + predictable):
      1. If the user explicitly named an agent/provider in `hint`, use it.
      2. Else if a specific `active` selection is set (and connected), use it.
      3. Else auto-pick by request type against connected providers' strengths,
         falling back to the preferred free provider.
    """
    llm_cfg = llm_cfg or {}
    provs = llm_cfg.get("providers", {})
    hint_l = (hint or "").lower()

    def creds_for_provider(pid: str) -> Optional[Dict[str, Any]]:
        spec = PROVIDERS.get(pid)
        key = provs.get(pid, {}).get("api_key", "").strip()
        if not spec or not key:
            return None
        model = provs.get(pid, {}).get("model") or spec["model"]
        return {"api_key": key, "base_url": spec["base_url"], "model": model,
                "id": pid, "label": spec["label"]}

    def creds_for_agent(aid: str) -> Optional[Dict[str, Any]]:
        spec = AGENTS.get(aid)
        if not spec:
            return None
        own = provs.get(f"agent:{aid}", {}).get("api_key", "").strip()
        via = spec.get("via")
        via_key = provs.get(via, {}).get("api_key", "").strip() if via else ""
        key = own or via_key
        if not key:
            return None
        return {"api_key": key, "base_url": spec["base_url"], "model": spec["model"],
                "id": f"agent:{aid}", "label": spec["label"]}

    # 1. Explicit mention in the request text.
    for aid, spec in AGENTS.items():
        if aid in hint_l or spec["label"].lower() in hint_l:
            c = creds_for_agent(aid)
            if c:
                return c
    for pid, spec in PROVIDERS.items():
        if pid in hint_l or spec["label"].lower() in hint_l:
            c = creds_for_provider(pid)
            if c:
                return c

    # 2. Explicit active selection.
    active = llm_cfg.get("active", "auto")
    if active and active != "auto":
        if active.startswith("agent:"):
            c = creds_for_agent(active.split(":", 1)[1])
        else:
            c = creds_for_provider(active)
        if c:
            return c

    # 3. Auto-route by request type.
    connected = connected_providers(llm_cfg)
    if not connected:
        # maybe only an agent key is present
        agents = connected_agents(llm_cfg)
        if agents:
            return creds_for_agent(agents[0])
        return None

    want = _classify(hint_l)
    if want:
        best = None
        best_score = -1
        for pid in connected:
            score = len(set(PROVIDERS[pid].get("strengths", [])) & want)
            # Nudge toward preferred/free providers on ties.
            score = score * 10 + (len(PREFERENCE_ORDER) - PREFERENCE_ORDER.index(pid)
                                  if pid in PREFERENCE_ORDER else 0)
            if score > best_score:
                best_score, best = score, pid
        if best:
            return creds_for_provider(best)

    # Fallback: first connected provider in preference order.
    for pid in PREFERENCE_ORDER:
        if pid in connected:
            return creds_for_provider(pid)
    return creds_for_provider(connected[0])


def _classify(text: str) -> set:
    """Very lightweight request classifier -> strength tags."""
    tags = set()
    if any(w in text for w in ("code", "python", "script", "function", "bug", "program")):
        tags.add("code")
    if any(w in text for w in ("why", "reason", "think", "explain", "solve", "math", "calculate")):
        tags.add("reasoning")
    if any(w in text for w in ("write", "essay", "story", "email", "poem", "draft")):
        tags.add("writing")
    if any(w in text for w in ("image", "picture", "photo", "screenshot", "see", "look at")):
        tags.add("vision")
    if any(w in text for w in ("document", "long", "summarize", "book", "transcript")):
        tags.add("long_context")
    if any(w in text for w in ("quick", "fast", "now")):
        tags.add("fast")
    return tags
