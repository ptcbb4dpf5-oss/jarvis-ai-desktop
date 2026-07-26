"""
modules/self_modify.py
======================
The self-modification / plugin engine — the heart of Jarvis's extensibility.

When the user says "Jarvis, add the ability to X":
  1. `create_capability()` asks the LLM (via Brain.generate_code) to write a new
     plugin module that subclasses `JarvisPlugin`.
  2. The generated source is validated (syntax + a safety scan) and written to
     `plugins/<slug>.py`.
  3. The plugin is imported and registered live via importlib — no restart.
  4. A `watchdog` observer also hot-reloads any plugin whose file changes on disk.

Plugins declare which utterances they handle (`can_handle`) and implement
`handle(user_text, agent)`. The registry routes free-text chat through loaded
plugins before falling back to the LLM.

Design goals: robust (never crash the app on a bad plugin), transparent (keeps a
record of created capabilities), and safe-by-default (rejects obviously dangerous
generated code unless explicitly allowed in config).
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import re
import sys
import threading
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

try:
    from watchdog.observers import Observer  # type: ignore
    from watchdog.events import FileSystemEventHandler  # type: ignore
    _WATCHDOG = True
except Exception:  # pragma: no cover
    Observer = None  # type: ignore
    FileSystemEventHandler = object  # type: ignore
    _WATCHDOG = False


# --------------------------------------------------------------------------- #
# Plugin base class — generated plugins subclass this.
# --------------------------------------------------------------------------- #
class JarvisPlugin:
    """Base class every Jarvis plugin must inherit from.

    Subclasses should set:
        name        : short unique identifier
        description : one-line human description
        keywords    : list of trigger words/phrases (lowercase)

    And implement:
        handle(self, user_text, agent) -> str
    Optionally override can_handle() for custom matching.
    """

    name: str = "unnamed_plugin"
    description: str = "No description provided."
    keywords: List[str] = []

    def can_handle(self, user_text: str) -> bool:
        t = (user_text or "").lower()
        return any(k.lower() in t for k in self.keywords)

    def handle(self, user_text: str, agent: Any = None) -> str:  # pragma: no cover
        raise NotImplementedError

    # Optional lifecycle hooks.
    def on_load(self, agent: Any = None) -> None:
        pass

    def on_unload(self, agent: Any = None) -> None:
        pass


# --------------------------------------------------------------------------- #
# Basic static safety scan for generated code.
# --------------------------------------------------------------------------- #
_DANGEROUS_PATTERNS = [
    r"\bos\.remove\b", r"\bshutil\.rmtree\b", r"\bos\.rmdir\b",
    r"\bformat\s*\(\s*['\"]?[a-zA-Z]:", r"rd\s+/s\s+/q", r"del\s+/f\s+/q",
    r"\bsubprocess\.[a-z_]+\([^)]*format",
    r"DROP\s+TABLE", r"\brm\s+-rf\s+/",
]


def _scan_code_safety(code: str) -> Optional[str]:
    """Return a reason string if code looks dangerous, else None."""
    low = code
    for pat in _DANGEROUS_PATTERNS:
        if re.search(pat, low, re.IGNORECASE):
            return f"blocked pattern: {pat}"
    return None


# --------------------------------------------------------------------------- #
# Plugin file-change watcher.
# --------------------------------------------------------------------------- #
if _WATCHDOG:
    class _PluginWatcher(FileSystemEventHandler):
        def __init__(self, engine: "SelfModifier") -> None:
            self.engine = engine

        def on_modified(self, event):  # type: ignore
            if getattr(event, "is_directory", False):
                return
            if str(event.src_path).endswith(".py"):
                self.engine._on_file_changed(event.src_path)

        def on_created(self, event):  # type: ignore
            if getattr(event, "is_directory", False):
                return
            if str(event.src_path).endswith(".py"):
                self.engine._on_file_changed(event.src_path)


# --------------------------------------------------------------------------- #
class SelfModifier:
    def __init__(
        self,
        brain: Any,
        plugins_dir: str = "plugins",
        allow_unsafe: bool = False,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.brain = brain
        self.plugins_dir = os.path.abspath(plugins_dir)
        self.allow_unsafe = allow_unsafe
        self._log_cb = log_cb or (lambda m: None)

        os.makedirs(self.plugins_dir, exist_ok=True)
        # Ensure `plugins` is importable as a package.
        init_file = os.path.join(self.plugins_dir, "__init__.py")
        if not os.path.exists(init_file):
            with open(init_file, "w", encoding="utf-8") as fh:
                fh.write("# Jarvis plugins package\n")

        # Make the plugins parent dir importable.
        parent = os.path.dirname(self.plugins_dir)
        if parent not in sys.path:
            sys.path.insert(0, parent)

        self._plugins: Dict[str, JarvisPlugin] = {}
        self._modules: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._agent = None  # set later via set_agent
        self._observer = None

    # ------------------------------------------------------------------ #
    def log(self, msg: str) -> None:
        try:
            self._log_cb(f"[self_modify] {msg}")
        except Exception:
            pass

    def set_agent(self, agent: Any) -> None:
        self._agent = agent

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def load_all(self) -> int:
        """Load every plugin currently in the plugins directory."""
        count = 0
        for fname in sorted(os.listdir(self.plugins_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            slug = fname[:-3]
            if self._load_plugin(slug):
                count += 1
        self.log(f"loaded {count} plugin(s)")
        return count

    def _load_plugin(self, slug: str) -> bool:
        path = os.path.join(self.plugins_dir, slug + ".py")
        if not os.path.exists(path):
            return False
        module_name = f"plugins.{slug}"
        try:
            with self._lock:
                if module_name in sys.modules:
                    module = importlib.reload(sys.modules[module_name])
                else:
                    spec = importlib.util.spec_from_file_location(module_name, path)
                    if spec is None or spec.loader is None:
                        return False
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)

                # Find JarvisPlugin subclasses defined in this module.
                registered = False
                for attr in vars(module).values():
                    if (isinstance(attr, type)
                            and issubclass(attr, JarvisPlugin)
                            and attr is not JarvisPlugin):
                        instance = attr()
                        # Unload previous instance with same name.
                        prev = self._plugins.get(instance.name)
                        if prev is not None:
                            try:
                                prev.on_unload(self._agent)
                            except Exception:
                                pass
                        self._plugins[instance.name] = instance
                        self._modules[instance.name] = module
                        try:
                            instance.on_load(self._agent)
                        except Exception:
                            pass
                        registered = True
                        self.log(f"registered plugin '{instance.name}' from {slug}.py")
                return registered
        except Exception:
            self.log(f"failed to load {slug}: {traceback.format_exc()}")
            return False

    def _on_file_changed(self, path: str) -> None:
        fname = os.path.basename(path)
        if fname.startswith("_") or not fname.endswith(".py"):
            return
        slug = fname[:-3]
        self.log(f"detected change in {fname}, hot-reloading…")
        self._load_plugin(slug)

    # ------------------------------------------------------------------ #
    # Watcher
    # ------------------------------------------------------------------ #
    def start_watching(self) -> None:
        if not _WATCHDOG or self._observer is not None:
            return
        try:
            self._observer = Observer()
            self._observer.schedule(_PluginWatcher(self), self.plugins_dir, recursive=False)
            self._observer.daemon = True
            self._observer.start()
            self.log("plugin hot-reload watcher started")
        except Exception as exc:
            self.log(f"couldn't start watcher: {exc}")

    def stop_watching(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass
            self._observer = None

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #
    def try_handle(self, user_text: str, agent: Any = None) -> Optional[str]:
        """If a loaded plugin claims this utterance, run it and return the result."""
        with self._lock:
            plugins = list(self._plugins.values())
        for plugin in plugins:
            try:
                if plugin.can_handle(user_text):
                    self.log(f"routing to plugin '{plugin.name}'")
                    return plugin.handle(user_text, agent or self._agent)
            except Exception as exc:
                self.log(f"plugin '{plugin.name}' errored: {exc}")
                return f"The '{plugin.name}' plugin hit an error: {exc}"
        return None

    def list_capabilities(self) -> str:
        with self._lock:
            if not self._plugins:
                return "I have no custom plugins loaded yet."
            lines = ["Custom capabilities:"]
            for p in self._plugins.values():
                lines.append(f"  • {p.name}: {p.description}")
            return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Capability creation (the self-modification act)
    # ------------------------------------------------------------------ #
    def create_capability(self, capability: str, agent: Any = None) -> str:
        slug = self._slugify(capability)
        path = os.path.join(self.plugins_dir, slug + ".py")

        self.log(f"creating capability '{capability}' -> {slug}.py")

        # Be honest up-front: the plugin engine adds *voice/text commands*, it
        # cannot restructure Jarvis's own live window (add panels, move the orb,
        # change buttons). Pretending otherwise is exactly the "it said it did it
        # but nothing happened" problem. Detect those requests and say so.
        if self._is_ui_request(capability):
            return (
                "Heads up — my self-modify engine adds new *commands* (plugins), "
                "it can't rebuild my own on-screen interface live. So I won't "
                "pretend I added a UI element.\n"
                "• A live system-info tray is already built into the top-left "
                "corner.\n"
                "• For other interface changes, ask the developer to edit the app "
                "and press ⟳ Update.\n"
                "If instead you want a new command (e.g. 'tell me the weather'), "
                "just describe what it should DO and I'll build it."
            )

        code = self._generate_plugin_code(capability, slug)
        if not code:
            return ("I couldn't generate that capability — my code generator is "
                    "offline. Set an LLM API key and try again.")

        # Validate syntax.
        syntax_err = self._validate_syntax(code)
        if syntax_err:
            # One repair attempt via the LLM.
            self.log(f"syntax error, attempting repair: {syntax_err}")
            code = self._repair_code(code, syntax_err) or code
            syntax_err = self._validate_syntax(code)
            if syntax_err:
                return f"I wrote the plugin but it has a syntax error I couldn't fix: {syntax_err}"

        # Safety scan.
        if not self.allow_unsafe:
            reason = _scan_code_safety(code)
            if reason:
                return (f"I generated code for '{capability}' but blocked it for safety "
                        f"({reason}). Enable 'allow_unsafe_plugins' in settings to override.")

        # Ensure it actually defines a JarvisPlugin subclass.
        if "JarvisPlugin" not in code:
            code = self._wrap_as_plugin(code, capability, slug)

        # Write to disk (atomic).
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(code)
            os.replace(tmp, path)
        except OSError as exc:
            return f"Couldn't write the plugin file: {exc}"

        # Load it live.
        ok = self._load_plugin(slug)
        if not ok:
            return (f"I saved plugins/{slug}.py, but it didn't register a valid plugin. "
                    "Check the file — it may need a JarvisPlugin subclass.")

        plugin = self._plugins.get(self._match_plugin_name(slug)) or None
        pname = plugin.name if plugin else slug

        # Actually VERIFY it works before claiming success — don't just assume.
        verdict, sample = self._verify_plugin(plugin)
        if verdict == "ok":
            trig = ", ".join((plugin.keywords or [])[:3]) if plugin else ""
            trig_txt = f" Try saying: \"{plugin.keywords[0]}\"." if plugin and plugin.keywords else ""
            return (f"Done and verified. The '{pname}' capability is loaded and "
                    f"responded correctly in a test run.{trig_txt}")
        if verdict == "stub":
            return (f"I registered '{pname}', but it's only a placeholder stub right "
                    f"now (no real logic yet). Connect an AI key so I can write the "
                    f"full version, or edit plugins/{slug}.py.")
        # verdict == "error"
        return (f"I saved and loaded '{pname}', but my test run failed: {sample} "
                f"The file is at plugins/{slug}.py if you want to inspect it.")

    # ------------------------------------------------------------------ #
    _UI_HINTS = (
        "tray", "panel", "widget", "button", "window", "orb", "hud", "layout",
        "top left", "top-left", "top right", "top-right", "bottom", "corner",
        "sidebar", "menu bar", "titlebar", "on screen", "on the screen",
        "display it on", "show it on", "add a box", "gui", "interface",
        "move the", "resize", "recolor", "re-color", "change the color of",
        "theme", "skin",
    )

    def _is_ui_request(self, capability: str) -> bool:
        """Heuristic: is the user asking to change Jarvis's own live interface?"""
        c = (capability or "").lower()
        if not any(h in c for h in self._UI_HINTS):
            return False
        # If it also clearly asks to fetch/compute data, treat as a real command
        # only when there's no explicit "add/create ... <ui element>" phrasing.
        wants_ui_element = bool(re.search(
            r"\b(add|create|make|build|put|place|show|display)\b.*"
            r"\b(tray|panel|widget|button|box|window|orb|hud|sidebar|menu|corner)\b",
            c))
        return wants_ui_element or any(
            k in c for k in ("move the", "resize", "recolor", "change the color",
                             "theme", "skin", "layout"))

    def _verify_plugin(self, plugin: "JarvisPlugin"):
        """Smoke-test a freshly loaded plugin. Returns (verdict, detail).

        verdict is one of: "ok", "stub", "error".
        """
        if plugin is None:
            return "error", "plugin object was None after load."
        # Build a sample utterance from its own keywords.
        sample = (plugin.keywords[0] if getattr(plugin, "keywords", None)
                  else plugin.name.replace("_", " "))
        try:
            if not plugin.can_handle(sample):
                # Not fatal, but note it — routing may never reach it.
                self.log(f"verify: '{plugin.name}' can_handle('{sample}') is False")
            result = plugin.handle(sample, self._agent)
            text = ("" if result is None else str(result)).strip()
        except Exception as exc:
            return "error", f"{type(exc).__name__}: {exc}"
        if not text:
            return "error", "handle() returned nothing."
        # Detect the offline placeholder stub so we don't over-promise.
        low = text.lower()
        if "not yet implemented" in low or "placeholder" in low or "todo" in low:
            return "stub", text
        return "ok", text

    # ------------------------------------------------------------------ #
    def _generate_plugin_code(self, capability: str, slug: str) -> str:
        """Use the Brain to generate plugin source; fall back to a template."""
        instruction = self._build_generation_prompt(capability, slug)
        code = ""
        try:
            if getattr(self.brain, "is_online", False):
                code = self.brain.generate_code(instruction)
        except Exception as exc:
            self.log(f"generation error: {exc}")
            code = ""

        if not code:
            # Offline fallback: a working echo-style template the user can edit.
            code = self._template_plugin(capability, slug)
        return code

    def _build_generation_prompt(self, capability: str, slug: str) -> str:
        return f"""Write a complete Python plugin for the JARVIS desktop assistant.

Requirements:
- Import the base class exactly like this: `from modules.self_modify import JarvisPlugin`
- Define ONE class that subclasses JarvisPlugin.
- Set class attributes: name (a short snake_case id, e.g. "{slug}"),
  description (one line), and keywords (a list of lowercase trigger phrases the
  user might say to invoke this).
- Implement `handle(self, user_text, agent=None) -> str` that performs the
  capability and returns a short spoken-friendly result string.
- You may use the Python standard library and common packages (requests, psutil,
  datetime, etc.). Handle errors gracefully and never raise out of handle().
- Do NOT include markdown fences or commentary — output only Python source.

The capability to implement: {capability}
"""

    def _template_plugin(self, capability: str, slug: str) -> str:
        safe_desc = capability.replace('"', "'")
        keywords = self._keywords_from_capability(capability)
        kw_repr = ", ".join(f'"{k}"' for k in keywords)
        class_name = "".join(w.capitalize() for w in re.split(r"[^a-zA-Z0-9]+", slug) if w) or "Custom"
        return f'''"""
Auto-generated Jarvis plugin (offline template).
Capability: {safe_desc}
Generated: {datetime.now().isoformat(timespec="seconds")}

This is a working stub created offline. Edit `handle()` to implement the real
behaviour, or regenerate it later with an LLM API key configured.
"""

from modules.self_modify import JarvisPlugin


class {class_name}Plugin(JarvisPlugin):
    name = "{slug}"
    description = "{safe_desc}"
    keywords = [{kw_repr}]

    def handle(self, user_text, agent=None):
        try:
            # TODO: implement "{safe_desc}"
            return ("The '{slug}' capability is registered but not yet implemented. "
                    "Configure an LLM key so I can write the full logic, or edit "
                    "plugins/{slug}.py yourself.")
        except Exception as exc:
            return f"The '{slug}' plugin failed: {{exc}}"
'''

    def _wrap_as_plugin(self, code: str, capability: str, slug: str) -> str:
        """If the LLM returned loose code, wrap it in a minimal plugin shell."""
        header = ("from modules.self_modify import JarvisPlugin\n\n\n"
                  "# --- original generated code below ---\n")
        return header + code

    # ------------------------------------------------------------------ #
    def _repair_code(self, code: str, error: str) -> str:
        if not getattr(self.brain, "is_online", False):
            return ""
        instruction = (
            "The following Python plugin has a syntax error. Fix it and return "
            "ONLY the corrected full Python source (no fences, no commentary).\n\n"
            f"Error: {error}\n\nCode:\n{code}"
        )
        try:
            return self.brain.generate_code(instruction)
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_syntax(code: str) -> Optional[str]:
        try:
            ast.parse(code)
            return None
        except SyntaxError as exc:
            return f"line {exc.lineno}: {exc.msg}"

    @staticmethod
    def _slugify(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^a-z0-9]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return (text or "capability")[:40]

    @staticmethod
    def _keywords_from_capability(capability: str) -> List[str]:
        words = re.findall(r"[a-zA-Z]{4,}", capability.lower())
        stop = {"the", "with", "that", "this", "into", "from", "your", "have",
                "ability", "able", "using", "which", "what", "when", "make"}
        kws = [w for w in words if w not in stop]
        # Also keep the whole phrase as a trigger.
        result = list(dict.fromkeys(kws))[:5]
        if capability.lower() not in result:
            result.insert(0, capability.lower())
        return result[:6]

    def _match_plugin_name(self, slug: str) -> str:
        # Best-effort: find a loaded plugin whose module came from this slug.
        for name, mod in self._modules.items():
            if getattr(mod, "__name__", "").endswith(slug):
                return name
        return slug

    # ------------------------------------------------------------------ #
    def shutdown(self) -> None:
        self.stop_watching()
        with self._lock:
            for p in self._plugins.values():
                try:
                    p.on_unload(self._agent)
                except Exception:
                    pass
