"""
core/agent.py
=============
The orchestration layer that ties the Brain to the concrete capability modules
and (optionally) runs multi-step autonomous tasks.

The Agent is deliberately UI-agnostic: it emits state via a callback so the UI
(the orb) can react (idle / listening / thinking / speaking / working), and it
returns plain strings that the caller can display and/or speak.
"""

from __future__ import annotations

import json
import re
import traceback
from typing import Any, Callable, Dict, List, Optional

from core.brain import Brain, SYSTEM_PROMPT
from core.memory import Memory


# Agent -> UI state signals
STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_WORKING = "working"


class Agent:
    def __init__(
        self,
        brain: Brain,
        memory: Memory,
        modules: Dict[str, Any],
        state_cb: Optional[Callable[[str], None]] = None,
        speak_cb: Optional[Callable[[str], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.brain = brain
        self.memory = memory
        self.modules = modules  # {"system": ..., "input": ..., "apps": ..., "browser": ..., "self_modify": ...}
        self._state_cb = state_cb or (lambda s: None)
        self._speak_cb = speak_cb or (lambda t: None)
        self._log_cb = log_cb or (lambda t: None)

    # ------------------------------------------------------------------ #
    def set_state(self, state: str) -> None:
        try:
            self._state_cb(state)
        except Exception:
            pass

    def log(self, msg: str) -> None:
        try:
            self._log_cb(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def handle(self, user_text: str) -> str:
        """Process a single user utterance end-to-end and return the reply text."""
        user_text = (user_text or "").strip()
        if not user_text:
            return ""

        self.memory.add_user(user_text)
        self.set_state(STATE_THINKING)

        try:
            context = self.memory.context_messages(SYSTEM_PROMPT)
            intent = self.brain.parse_intent(user_text, context)
        except Exception as exc:
            self.log(f"[intent-error] {exc}")
            intent = {"intent": "chat", "args": {}, "speak": ""}

        name = intent.get("intent", "chat")
        args = intent.get("args", {}) or {}
        speak = intent.get("speak", "") or ""

        self.log(f"[intent] {name} args={args}")

        try:
            reply = self._dispatch(name, args, speak, user_text)
        except Exception as exc:
            self.log("[dispatch-error] " + traceback.format_exc())
            reply = f"I hit a snag executing that: {exc}"

        reply = (reply or speak or "").strip()
        if reply:
            self.memory.add_assistant(reply)
        self.set_state(STATE_IDLE)
        return reply

    # ------------------------------------------------------------------ #
    def _dispatch(self, name: str, args: Dict[str, Any], speak: str, user_text: str) -> str:
        if name == "chat":
            return self._chat(user_text)

        if name == "exit":
            return speak or "Powering down."

        if name == "system_stats":
            return self._system_stats(args, speak)

        if name == "input_control":
            return self._input_control(args, speak)

        if name in ("app_launch", "app_close", "app_install", "app_uninstall"):
            return self._app_manage(name, args, speak)

        if name == "browser":
            return self._browser(args, speak)

        if name == "self_modify":
            return self._self_modify(args, speak)

        if name in ("project_create", "project_run", "project_change",
                    "self_read", "self_duplicate", "self_promote", "self_discard"):
            return self._projects(name, args, speak, user_text)

        if name == "autonomous_task":
            return self.run_autonomous(args.get("goal", user_text))

        # Fallback
        return self._chat(user_text)

    # ------------------------------------------------------------------ #
    # Intent handlers
    # ------------------------------------------------------------------ #
    def _chat(self, user_text: str) -> str:
        self.set_state(STATE_THINKING)
        # First, see if a hot-loaded plugin claims this utterance.
        sm = self.modules.get("self_modify")
        if sm is not None:
            handled = sm.try_handle(user_text, agent=self)
            if handled is not None:
                return str(handled)

        messages = self.memory.context_messages(SYSTEM_PROMPT)

        # For harder questions, let the ReasoningEngine think harder (reflect /
        # multi-model debate). It decides automatically and degrades gracefully.
        reasoning = self.modules.get("reasoning")
        if reasoning is not None and getattr(self.brain, "is_online", False):
            try:
                # Split the conversation context from the latest user turn.
                history = messages[:-1] if messages else []
                return reasoning.answer(user_text, context=history, mode="auto")
            except Exception as exc:
                self.log(f"[reasoning-fallback] {exc}")

        return self.brain.chat(messages)

    def _system_stats(self, args: Dict[str, Any], speak: str) -> str:
        sysmod = self.modules.get("system")
        if sysmod is None:
            return "System monitoring module isn't available."
        metric = (args.get("metric") or "all").lower()
        self.set_state(STATE_WORKING)
        report = sysmod.report(metric)
        # Also push a HUD update if a callback exists.
        return (speak + "\n" if speak else "") + report

    def _input_control(self, args: Dict[str, Any], speak: str) -> str:
        inp = self.modules.get("input")
        if inp is None:
            return "Input control module isn't available."
        self.set_state(STATE_WORKING)
        action = (args.get("action") or "").lower()
        result = inp.execute(action, args)
        return speak or result

    def _app_manage(self, name: str, args: Dict[str, Any], speak: str) -> str:
        apps = self.modules.get("apps")
        if apps is None:
            return "App manager isn't available."
        self.set_state(STATE_WORKING)
        target = args.get("name", "")
        if name == "app_launch":
            result = apps.launch(target)
        elif name == "app_close":
            result = apps.close(target)
        elif name == "app_install":
            result = apps.install(target)
        else:
            result = apps.uninstall(target)
        return (speak + "\n" if speak else "") + result

    def _browser(self, args: Dict[str, Any], speak: str) -> str:
        br = self.modules.get("browser")
        if br is None:
            return "Browser module isn't available."
        self.set_state(STATE_WORKING)
        action = (args.get("action") or "open").lower()
        result = br.execute(action, args)
        return (speak + "\n" if speak else "") + str(result)

    def _self_modify(self, args: Dict[str, Any], speak: str) -> str:
        sm = self.modules.get("self_modify")
        if sm is None:
            return "Self-modification engine isn't available."
        self.set_state(STATE_WORKING)
        capability = args.get("capability", "")
        if not capability:
            return "Tell me what new ability you'd like me to add."
        result = sm.create_capability(capability, agent=self)
        return (speak + "\n" if speak else "") + result

    def _projects(self, name: str, args: Dict[str, Any], speak: str, user_text: str) -> str:
        """Route project + self-development intents to the ProjectManager."""
        pm = self.modules.get("projects")
        if pm is None:
            return "The projects engine isn't available."
        self.set_state(STATE_WORKING)
        try:
            if name == "project_create":
                pname = args.get("name") or "new_project"
                desc = args.get("description") or user_text
                self._speak_cb("Designing that now. This can take a moment.")
                return pm.create_project(pname, desc)

            if name == "project_run":
                pname = args.get("name") or ""
                if not pname:
                    projs = pm.list_projects()
                    if not projs:
                        return "You don't have any projects yet. Ask me to build one."
                    pname = projs[-1]
                return pm.run_project(pname)

            if name == "project_change":
                pname = args.get("name") or ""
                change = args.get("change") or user_text
                if not pname:
                    projs = [p for p in pm.list_projects() if not p.startswith("_jarvis_")]
                    if not projs:
                        return "There's no project to change yet."
                    pname = projs[-1]
                return pm.iterate_project(pname, change)

            if name == "self_read":
                return pm.read_own_code(args.get("query", ""))

            if name == "self_duplicate":
                changes = args.get("changes") or user_text
                self._speak_cb("Making a safe copy of myself and applying that. One moment.")
                return pm.create_self_duplicate(changes)

            if name == "self_promote":
                if not pm.has_pending():
                    return "There's no reviewed change waiting. Ask me to improve myself first."
                return pm.promote_pending()

            if name == "self_discard":
                return pm.discard_pending()
        except Exception as exc:
            self.log("[projects-error] " + traceback.format_exc())
            return f"That project action hit a snag: {exc}"
        return speak or "Done."

    # ------------------------------------------------------------------ #
    # Autonomous multi-step task loop
    # ------------------------------------------------------------------ #
    def run_autonomous(self, goal: str, max_steps: int = 8) -> str:
        """Break a goal into steps with the LLM, then execute each step."""
        self.set_state(STATE_WORKING)
        self.log(f"[autonomous] goal: {goal}")

        steps = self._plan(goal, max_steps)
        if not steps:
            # No plan — just answer conversationally.
            return self._chat(goal)

        transcript: List[str] = [f"Goal: {goal}", "Plan:"]
        for i, step in enumerate(steps, 1):
            transcript.append(f"  {i}. {step}")

        self._speak_cb(f"I've broken that into {len(steps)} steps. Executing now.")

        results: List[str] = []
        for i, step in enumerate(steps, 1):
            self.set_state(STATE_WORKING)
            self.log(f"[autonomous] step {i}/{len(steps)}: {step}")
            try:
                intent = self.brain.parse_intent(step)
                name = intent.get("intent", "chat")
                args = intent.get("args", {}) or {}
                # Avoid infinite recursion: don't let a step spawn another task.
                if name == "autonomous_task":
                    name = "chat"
                outcome = self._dispatch(name, args, intent.get("speak", ""), step)
            except Exception as exc:
                outcome = f"failed: {exc}"
            results.append(f"Step {i}: {step}\n   -> {outcome}")
            self.log(f"[autonomous] step {i} result: {outcome[:120]}")

        summary = self._summarize_results(goal, results)
        full = "\n".join(transcript) + "\n\nResults:\n" + "\n".join(results)
        self.log(full)
        self.set_state(STATE_IDLE)
        return summary

    def _plan(self, goal: str, max_steps: int) -> List[str]:
        if not self.brain.is_online:
            # Offline: treat the goal as a single step.
            return [goal]
        prompt = (
            f"Break the following goal into at most {max_steps} concrete, individually "
            f"executable steps for a Windows desktop AI agent. Return ONLY a JSON array "
            f'of strings.\n\nGoal: {goal}'
        )
        try:
            resp = self.brain.chat(
                [
                    {"role": "system", "content": "You are a task planner. Output only a JSON array of step strings."},
                    {"role": "user", "content": prompt},
                ]
            )
            data = self.brain._safe_json(resp)  # reuse robust parser
            if isinstance(data, list):
                return [str(s) for s in data][:max_steps]
            # Sometimes the model wraps it: {"steps": [...]}
            if isinstance(data, dict) and isinstance(data.get("steps"), list):
                return [str(s) for s in data["steps"]][:max_steps]
        except Exception:
            pass
        return [goal]

    def _summarize_results(self, goal: str, results: List[str]) -> str:
        if not self.brain.is_online:
            return f"Task complete. Ran {len(results)} steps toward: {goal}."
        try:
            joined = "\n".join(results)
            return self.brain.chat(
                [
                    {"role": "system", "content": "Summarize the task execution for the user in 2-3 sentences, spoken aloud."},
                    {"role": "user", "content": f"Goal: {goal}\n\nStep results:\n{joined}"},
                ]
            )
        except Exception:
            return f"Task complete. Ran {len(results)} steps toward: {goal}."
