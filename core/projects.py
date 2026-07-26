"""
core/projects.py
================
Jarvis's project workspace + self-development engine (think a mini Abacus / Dyad
that lives inside Jarvis).

Two big capabilities:

1. PROJECTS — Jarvis can spin up a new coding project from a plain-English
   description. It asks the LLM (optionally sharpened by the ReasoningEngine) for
   a JSON manifest of files, writes them to ``~/JarvisProjects/<name>/``, then can
   run / test the project and report the output. Projects can be iterated on
   ("add X to my todo app") and the code is regenerated file-by-file.

2. SELF-DEV — Jarvis can read and understand its OWN source code, make a *safe
   duplicate* of itself into a sandbox, apply requested changes there, test that
   the duplicate still imports/compiles, let the user review/test it, and only if
   approved PROMOTE those changes back onto the live app (with a timestamped
   backup so it can always roll back).

Nothing here touches the live app until the user explicitly approves a promote,
and every promote is backed up first. All heavy lifting (code generation) goes
through ``Brain`` / ``ReasoningEngine`` so it uses whatever provider is connected.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


def _home_projects_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "JarvisProjects")


class ProjectManager:
    def __init__(
        self,
        brain: Any,
        reasoning: Any = None,
        app_root: Optional[str] = None,
        projects_dir: Optional[str] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.brain = brain
        self.reasoning = reasoning
        self.app_root = os.path.abspath(app_root or os.path.dirname(os.path.dirname(__file__)))
        self.projects_dir = os.path.abspath(projects_dir or _home_projects_dir())
        self._log = log_cb or (lambda m: None)
        os.makedirs(self.projects_dir, exist_ok=True)
        # The most recent self-duplicate sandbox awaiting review/promotion.
        self._pending_sandbox: Optional[str] = None
        self._pending_summary: str = ""

    # ------------------------------------------------------------------ #
    def log(self, msg: str) -> None:
        try:
            self._log(f"[projects] {msg}")
        except Exception:
            pass

    def _slug(self, name: str) -> str:
        s = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").lower()).strip("_")
        return (s or "project")[:48]

    # ================================================================== #
    # Projects
    # ================================================================== #
    def list_projects(self) -> List[str]:
        try:
            return sorted(
                d for d in os.listdir(self.projects_dir)
                if os.path.isdir(os.path.join(self.projects_dir, d))
            )
        except Exception:
            return []

    def project_path(self, name: str) -> str:
        return os.path.join(self.projects_dir, self._slug(name))

    def read_project(self, name: str, max_bytes: int = 20000) -> Dict[str, Any]:
        """Return the file tree + (truncated) contents of a project."""
        root = self.project_path(name)
        if not os.path.isdir(root):
            return {"error": f"No project named '{name}'."}
        files: Dict[str, str] = {}
        tree: List[str] = []
        for dirpath, _dirs, fnames in os.walk(root):
            for fn in sorted(fnames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                tree.append(rel)
                try:
                    if os.path.getsize(full) <= max_bytes and self._is_text(fn):
                        with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                            files[rel] = fh.read()
                except Exception:
                    pass
        return {"root": root, "tree": tree, "files": files}

    @staticmethod
    def _is_text(fname: str) -> bool:
        return fname.lower().endswith((
            ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json",
            ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh",
            ".bat", ".env", ".xml", ".csv",
        ))

    # ------------------------------------------------------------------ #
    def create_project(self, name: str, description: str,
                       progress_cb: Optional[Callable[[str], None]] = None) -> str:
        """Generate a new project from a description. Returns a summary string."""
        prog = progress_cb or (lambda m: None)
        if not getattr(self.brain, "is_online", False):
            return ("I need an AI provider connected to write code. Open Settings "
                    "(gear) and paste a key (Groq is free), then try again.")

        slug = self._slug(name)
        root = self.project_path(slug)
        os.makedirs(root, exist_ok=True)
        self.log(f"creating project '{slug}'")
        prog(f"Designing '{slug}'…")

        manifest = self._generate_manifest(name, description)
        if not manifest or not manifest.get("files"):
            return (f"I couldn't design that project — the model didn't return a "
                    f"valid file plan. Try describing it more concretely.")

        written: List[str] = []
        for f in manifest["files"]:
            rel = f.get("path", "").lstrip("/\\")
            content = f.get("content", "")
            if not rel:
                continue
            dest = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dest) or root, exist_ok=True)
            try:
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(content)
                written.append(rel)
                prog(f"  wrote {rel}")
            except Exception as exc:
                self.log(f"write failed {rel}: {exc}")

        # Save a manifest note.
        try:
            with open(os.path.join(root, ".jarvis_project.json"), "w", encoding="utf-8") as fh:
                json.dump({"name": name, "description": description,
                           "created": datetime.now().isoformat(timespec="seconds"),
                           "run": manifest.get("run", "")}, fh, indent=2)
        except Exception:
            pass

        run_hint = manifest.get("run", "")
        summary = (f"Created project '{slug}' with {len(written)} file(s): "
                   f"{', '.join(written[:8])}"
                   + (" …" if len(written) > 8 else "") + ".")
        if run_hint:
            summary += f"\nRun it with: {run_hint}"
        summary += f"\nLocation: {root}"
        return summary

    def _generate_manifest(self, name: str, description: str) -> Optional[Dict[str, Any]]:
        prompt = (
            "You are a senior engineer. Design a small, COMPLETE, runnable project "
            "for the request below. Return ONLY a JSON object (no markdown) with "
            "this schema:\n"
            '{ "files": [ {"path": "relative/path.ext", "content": "FULL FILE"} ], '
            '"run": "the exact command to run it" }\n'
            "Rules: include every file needed to run; keep it minimal but working; "
            "prefer a single language; put dependencies in requirements.txt or "
            "package.json; no placeholders or TODOs — write real code.\n\n"
            f"PROJECT NAME: {name}\nREQUEST: {description}"
        )
        raw = self._gen(prompt)
        return self._parse_manifest(raw)

    def iterate_project(self, name: str, change: str) -> str:
        """Apply a change request to an existing project."""
        if not getattr(self.brain, "is_online", False):
            return "Connect an AI provider first (Settings → paste a key)."
        data = self.read_project(name)
        if "error" in data:
            return data["error"]
        current = "\n\n".join(
            f"=== {rel} ===\n{content}" for rel, content in data["files"].items()
        )[:12000]
        prompt = (
            "Modify the existing project to satisfy the change request. Return ONLY "
            "a JSON object {\"files\":[{\"path\",\"content\"}], \"run\": \"...\"} "
            "containing the FULL new content of every file you change or add. Do not "
            "include files that are unchanged.\n\n"
            f"CHANGE REQUEST: {change}\n\nCURRENT PROJECT:\n{current}"
        )
        manifest = self._parse_manifest(self._gen(prompt))
        if not manifest or not manifest.get("files"):
            return "I couldn't produce a valid change set for that."
        root = self.project_path(name)
        changed = []
        for f in manifest["files"]:
            rel = f.get("path", "").lstrip("/\\")
            if not rel:
                continue
            dest = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dest) or root, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(f.get("content", ""))
            changed.append(rel)
        return f"Updated '{self._slug(name)}': {', '.join(changed)}."

    # ------------------------------------------------------------------ #
    def run_project(self, name: str, timeout: int = 30) -> str:
        """Best-effort run/test of a project; returns captured output."""
        root = self.project_path(name)
        if not os.path.isdir(root):
            return f"No project named '{name}'."
        cmd = self._detect_run_command(root)
        if not cmd:
            return ("I couldn't detect how to run this project. Check the "
                    "'run' hint in .jarvis_project.json.")
        self.log(f"running {cmd} in {root}")
        try:
            proc = subprocess.run(
                cmd, cwd=root, shell=True, capture_output=True, text=True,
                timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            out = out.strip()[-3000:]
            status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
            return f"[{status}] `{cmd}`\n{out or '(no output)'}"
        except subprocess.TimeoutExpired:
            return f"`{cmd}` is still running after {timeout}s (likely a server/UI). That usually means it launched fine."
        except Exception as exc:
            return f"Couldn't run it: {exc}"

    def _detect_run_command(self, root: str) -> str:
        note = os.path.join(root, ".jarvis_project.json")
        if os.path.exists(note):
            try:
                with open(note, encoding="utf-8") as fh:
                    run = json.load(fh).get("run", "")
                if run:
                    return run
            except Exception:
                pass
        if os.path.exists(os.path.join(root, "package.json")):
            return "npm install && npm start"
        for entry in ("main.py", "app.py", "run.py", "__main__.py"):
            if os.path.exists(os.path.join(root, entry)):
                return f"{sys.executable} {entry}"
        pys = [f for f in os.listdir(root) if f.endswith(".py")]
        if len(pys) == 1:
            return f"{sys.executable} {pys[0]}"
        return ""

    # ================================================================== #
    # Self-development (read / duplicate / test / promote own code)
    # ================================================================== #
    def read_own_code(self, query: str = "") -> str:
        """Summarise Jarvis's own architecture, optionally focused by a query."""
        files = self._collect_source(self.app_root)
        index = "\n".join(f"  {rel} ({len(src.splitlines())} lines)" for rel, src in files.items())
        if not getattr(self.brain, "is_online", False):
            return "My source files:\n" + index
        # Feed a compact index + the most relevant files to the model.
        relevant = self._pick_relevant(files, query)
        blob = "\n\n".join(f"=== {rel} ===\n{src}" for rel, src in relevant.items())[:14000]
        prompt = (
            "You are inspecting your own source code (you are JARVIS). "
            + (f"Focus on: {query}. " if query else "")
            + "Explain concisely how the relevant parts work and where a change "
            "would go. Reference real file names.\n\n"
            f"FILE INDEX:\n{index}\n\nKEY FILES:\n{blob}"
        )
        if self.reasoning:
            return self.reasoning.answer(prompt, mode="reflect")
        return self.brain.chat([{"role": "user", "content": prompt}])

    def create_self_duplicate(self, changes: str) -> str:
        """Copy the live app into a sandbox and apply requested changes there.

        The live app is NOT touched. Returns a review summary; call
        ``promote_pending()`` to apply it for real.
        """
        if not getattr(self.brain, "is_online", False):
            return "Connect an AI provider first so I can write the changes."

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sandbox = os.path.join(self.projects_dir, f"_jarvis_sandbox_{stamp}")
        self.log(f"duplicating self -> {sandbox}")
        try:
            shutil.copytree(
                self.app_root, sandbox,
                ignore=shutil.ignore_patterns(
                    ".git", "__pycache__", ".venv", "venv", "screenshots",
                    "*.pyc", "JarvisProjects", "_jarvis_sandbox_*",
                ),
            )
        except Exception as exc:
            return f"Couldn't create the sandbox copy: {exc}"

        # Ask the model for a change set against the real source.
        files = self._collect_source(self.app_root)
        relevant = self._pick_relevant(files, changes)
        blob = "\n\n".join(f"=== {rel} ===\n{src}" for rel, src in relevant.items())[:14000]
        prompt = (
            "You are modifying your own source code (JARVIS). Apply the requested "
            "change. Return ONLY a JSON object {\"files\":[{\"path\",\"content\"}], "
            "\"summary\": \"what changed\"} with the FULL new content of each file "
            "you modify (paths relative to the app root). Keep everything else "
            "working; do not break imports.\n\n"
            f"CHANGE REQUEST: {changes}\n\nRELEVANT SOURCE:\n{blob}"
        )
        manifest = self._parse_manifest(self._gen(prompt))
        if not manifest or not manifest.get("files"):
            shutil.rmtree(sandbox, ignore_errors=True)
            return "I couldn't produce a valid change set for that."

        applied = []
        for f in manifest["files"]:
            rel = f.get("path", "").lstrip("/\\")
            if not rel or ".." in rel:
                continue
            dest = os.path.join(sandbox, rel)
            os.makedirs(os.path.dirname(dest) or sandbox, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(f.get("content", ""))
            applied.append(rel)

        # Verify the sandbox compiles.
        ok, detail = self._compile_check(sandbox, applied)
        self._pending_sandbox = sandbox
        self._pending_summary = manifest.get("summary", "") or ", ".join(applied)

        verdict = "compiles cleanly ✓" if ok else f"has a problem: {detail}"
        msg = (
            f"I made a safe duplicate and applied the change there (live app "
            f"untouched).\nChanged: {', '.join(applied)}\nSelf-test: {verdict}\n"
            f"Summary: {self._pending_summary}\n\n"
            f"Sandbox: {sandbox}\n"
        )
        if ok:
            msg += "Say 'update' / 'apply it' to promote these changes to the live app, or 'discard'."
        else:
            msg += "I won't offer to promote this until it compiles. Want me to try fixing it?"
        return msg

    def promote_pending(self) -> str:
        """Apply the pending sandbox's changed files onto the live app (backed up)."""
        if not self._pending_sandbox or not os.path.isdir(self._pending_sandbox):
            return "There's no reviewed change waiting to be applied."
        sandbox = self._pending_sandbox

        # Backup the live app first.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(self.projects_dir, f"_jarvis_backup_{stamp}")
        try:
            shutil.copytree(
                self.app_root, backup,
                ignore=shutil.ignore_patterns(
                    ".git", "__pycache__", ".venv", "venv", "screenshots",
                    "*.pyc", "JarvisProjects", "_jarvis_sandbox_*", "_jarvis_backup_*",
                ),
            )
        except Exception as exc:
            return f"Aborting — couldn't create a safety backup: {exc}"

        # Copy every file that differs from live (skip config/plugins/secrets).
        promoted = []
        skip_dirs = {".git", "__pycache__", ".venv", "venv", "screenshots"}
        skip_rel = {os.path.join("config", "settings.json")}
        for dirpath, dirs, fnames in os.walk(sandbox):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith("_jarvis_")]
            for fn in fnames:
                if fn.endswith(".pyc"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, sandbox)
                if rel in skip_rel or rel.startswith("plugins" + os.sep):
                    continue
                live = os.path.join(self.app_root, rel)
                try:
                    if self._differs(full, live):
                        os.makedirs(os.path.dirname(live) or self.app_root, exist_ok=True)
                        shutil.copy2(full, live)
                        promoted.append(rel)
                except Exception as exc:
                    self.log(f"promote failed {rel}: {exc}")

        self._pending_sandbox = None
        summary = self._pending_summary
        self._pending_summary = ""
        if not promoted:
            return "Nothing to promote — the sandbox matched the live app."
        return (f"Applied {len(promoted)} file(s) to the live app: "
                f"{', '.join(promoted)}.\nBackup saved at {backup}\n"
                f"Restart Jarvis (or press ⟳) to load the new code. ({summary})")

    def discard_pending(self) -> str:
        if self._pending_sandbox and os.path.isdir(self._pending_sandbox):
            shutil.rmtree(self._pending_sandbox, ignore_errors=True)
        self._pending_sandbox = None
        self._pending_summary = ""
        return "Discarded the pending changes. Live app is unchanged."

    def has_pending(self) -> bool:
        return bool(self._pending_sandbox and os.path.isdir(self._pending_sandbox))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _collect_source(self, root: str) -> Dict[str, str]:
        files: Dict[str, str] = {}
        skip = {".git", "__pycache__", ".venv", "venv", "screenshots", "JarvisProjects"}
        for dirpath, dirs, fnames in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith("_jarvis_")]
            for fn in fnames:
                if not fn.endswith(".py"):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                try:
                    with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                        files[rel] = fh.read()
                except Exception:
                    pass
        return files

    def _pick_relevant(self, files: Dict[str, str], query: str, k: int = 4) -> Dict[str, str]:
        if not query:
            # Default to the core files.
            keys = [r for r in files if any(p in r for p in ("brain", "agent", "main", "self_modify"))]
            return {r: files[r] for r in keys[:k]} or dict(list(files.items())[:k])
        q = set(re.findall(r"[a-zA-Z]{3,}", query.lower()))
        scored = []
        for rel, src in files.items():
            score = sum(src.lower().count(w) for w in q) + 5 * sum(w in rel.lower() for w in q)
            scored.append((score, rel))
        scored.sort(reverse=True)
        top = [rel for score, rel in scored if score > 0][:k]
        return {r: files[r] for r in top} or {r: files[r] for r in list(files)[:k]}

    def _compile_check(self, sandbox: str, changed: List[str]):
        for rel in changed:
            if not rel.endswith(".py"):
                continue
            path = os.path.join(sandbox, rel)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    ast.parse(fh.read())
            except SyntaxError as exc:
                return False, f"{rel} line {exc.lineno}: {exc.msg}"
            except Exception as exc:
                return False, f"{rel}: {exc}"
        return True, ""

    @staticmethod
    def _differs(a: str, b: str) -> bool:
        if not os.path.exists(b):
            return True
        try:
            with open(a, "rb") as fa, open(b, "rb") as fb:
                return fa.read() != fb.read()
        except Exception:
            return True

    def _gen(self, prompt: str) -> str:
        try:
            return self.brain.generate_code(prompt)
        except Exception as exc:
            self.log(f"generation error: {exc}")
            return ""

    @staticmethod
    def _parse_manifest(raw: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        # Grab the outermost JSON object.
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("files"), list):
                return data
        except Exception:
            pass
        return None
