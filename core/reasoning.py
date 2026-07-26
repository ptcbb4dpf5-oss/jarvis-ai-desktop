"""
core/reasoning.py
=================
Makes Jarvis "think harder" by reasoning with himself and with other
models/agents before answering.

Three strategies, all built on top of ``Brain`` (which already routes to whatever
provider/agent is connected):

  * ``reflect``    — draft -> self-critique -> improved final answer (one model).
  * ``debate``     — two different connected models answer, a third (or the best
                     one) judges/merges them into a stronger final answer.
  * ``auto``       — pick reflect vs debate based on how many providers are
                     connected and how hard the question looks.

Everything degrades gracefully: if only one provider is connected, ``debate``
falls back to ``reflect``; if the brain is offline, we return a single best-effort
answer. The engine emits progress via an optional ``log_cb`` so the UI can show
"thinking with 2 models…" style status.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core import providers as _providers


class ReasoningEngine:
    def __init__(self, brain: Any, log_cb: Optional[Callable[[str], None]] = None) -> None:
        self.brain = brain
        self._log = log_cb or (lambda m: None)

    # ------------------------------------------------------------------ #
    def log(self, msg: str) -> None:
        try:
            self._log(f"[reason] {msg}")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    def answer(self, question: str, context: Optional[List[Dict[str, str]]] = None,
               mode: str = "auto") -> str:
        """Return an improved answer to ``question`` using the chosen strategy."""
        if not getattr(self.brain, "is_online", False):
            # Offline — just use the brain's own fallback.
            return self.brain.chat((context or []) + [{"role": "user", "content": question}])

        llm_cfg = self.brain.config.get("llm", {})
        providers_connected = _providers.connected_providers(llm_cfg)
        agents_connected = _providers.connected_agents(llm_cfg)
        pool = providers_connected + [f"agent:{a}" for a in agents_connected]

        if mode == "auto":
            mode = "debate" if (len(pool) >= 2 and self._looks_hard(question)) else "reflect"

        if mode == "debate" and len(pool) >= 2:
            return self._debate(question, context, pool)
        return self._reflect(question, context)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _looks_hard(q: str) -> bool:
        ql = q.lower()
        if len(q) > 160:
            return True
        hard = ("why", "how", "design", "plan", "compare", "debug", "optimis",
                "optimize", "architect", "prove", "strategy", "trade-off",
                "tradeoff", "best way", "explain", "analyse", "analyze", "reason")
        return any(h in ql for h in hard)

    # ------------------------------------------------------------------ #
    def _reflect(self, question: str, context: Optional[List[Dict[str, str]]]) -> str:
        """Draft -> critique -> refine, all on the best single model."""
        self.log("reflecting (draft → self-critique → final)")
        base = context or []

        draft = self.brain.chat(base + [{"role": "user", "content": question}])
        if not draft:
            return draft

        critique_prompt = (
            "You are your own toughest reviewer. Critically evaluate the draft "
            "answer below for correctness, gaps, faulty reasoning and anything "
            "missing. List concrete issues. Be terse.\n\n"
            f"QUESTION:\n{question}\n\nDRAFT:\n{draft}"
        )
        critique = self.brain.chat([{"role": "user", "content": critique_prompt}])

        refine_prompt = (
            "Rewrite the answer to the question, fixing every valid issue from the "
            "critique. Return only the improved final answer — no preamble, no "
            "mention of the critique.\n\n"
            f"QUESTION:\n{question}\n\nDRAFT:\n{draft}\n\nCRITIQUE:\n{critique}"
        )
        final = self.brain.chat(base + [{"role": "user", "content": refine_prompt}])
        return final or draft

    # ------------------------------------------------------------------ #
    def _debate(self, question: str, context: Optional[List[Dict[str, str]]],
                pool: List[str]) -> str:
        """Ask two different models, then have a judge merge into a final answer."""
        a_id, b_id = pool[0], pool[1]
        self.log(f"debating with {a_id} vs {b_id}")

        ans_a = self._ask_specific(a_id, question, context)
        ans_b = self._ask_specific(b_id, question, context)

        if not ans_a and not ans_b:
            return self._reflect(question, context)
        if not ans_b:
            return ans_a
        if not ans_a:
            return ans_b

        # Judge with the strongest available model (prefer a reasoning/paid one).
        judge_id = self._pick_judge(pool, exclude=None)
        judge_prompt = (
            "Two AI assistants answered the same question. Produce the single best "
            "final answer: keep what each got right, fix mistakes, resolve any "
            "disagreement using sound reasoning, and drop fluff. Return only the "
            "final answer.\n\n"
            f"QUESTION:\n{question}\n\n"
            f"ANSWER A:\n{ans_a}\n\nANSWER B:\n{ans_b}"
        )
        merged = self._ask_specific(judge_id, judge_prompt, context)
        return merged or ans_a

    # ------------------------------------------------------------------ #
    def _ask_specific(self, selection: str, question: str,
                      context: Optional[List[Dict[str, str]]]) -> str:
        """Force a specific provider/agent for one call, then restore config."""
        llm_cfg = self.brain.config.get("llm", {})
        prev = llm_cfg.get("active", "auto")
        llm_cfg["active"] = selection
        try:
            return self.brain.chat((context or []) + [{"role": "user", "content": question}])
        except Exception as exc:
            self.log(f"{selection} failed: {exc}")
            return ""
        finally:
            llm_cfg["active"] = prev

    @staticmethod
    def _pick_judge(pool: List[str], exclude: Optional[str]) -> str:
        # Prefer a reasoning-strong provider as judge.
        order = ["anthropic", "openai", "google", "openrouter", "groq", "mistral"]
        for pid in order:
            if pid in pool and pid != exclude:
                return pid
        for x in pool:
            if x != exclude:
                return x
        return pool[0]
