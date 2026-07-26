"""
modules/input_control.py
========================
Human-like mouse & keyboard control via PyAutoGUI, plus screenshots.

All movements use easing and small randomized durations so automation feels
natural rather than teleporting. Every public action returns a short status
string suitable for speech.

Safety:
  * PyAutoGUI's FAILSAFE stays ON — slamming the mouse into a screen corner
    aborts automation.
  * A configurable pause between actions prevents runaway input.
"""

from __future__ import annotations

import os
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except Exception:  # pragma: no cover
    pyautogui = None  # type: ignore
    _PYAUTOGUI = False


class InputControl:
    def __init__(self, screenshot_dir: str = "screenshots") -> None:
        self.screenshot_dir = os.path.abspath(screenshot_dir)
        os.makedirs(self.screenshot_dir, exist_ok=True)
        if _PYAUTOGUI:
            try:
                self.screen_w, self.screen_h = pyautogui.size()
            except Exception:
                self.screen_w, self.screen_h = (0, 0)
        else:
            self.screen_w, self.screen_h = (0, 0)

    def available(self) -> bool:
        return _PYAUTOGUI

    # ------------------------------------------------------------------ #
    def execute(self, action: str, args: Dict[str, Any]) -> str:
        """Dispatch a named action with a dict of args (used by the Agent)."""
        if not _PYAUTOGUI:
            return "Input control unavailable (pyautogui not installed)."

        action = (action or "").lower()
        try:
            if action == "move":
                return self.move(int(args.get("x", 0)), int(args.get("y", 0)))
            if action == "click":
                return self.click(args.get("x"), args.get("y"),
                                  button=args.get("button", "left"))
            if action == "double_click":
                return self.double_click(args.get("x"), args.get("y"))
            if action == "right_click":
                return self.click(args.get("x"), args.get("y"), button="right")
            if action == "type":
                return self.type_text(args.get("text", ""))
            if action == "scroll":
                return self.scroll(int(args.get("amount", -3)))
            if action == "hotkey":
                keys = args.get("keys") or []
                return self.hotkey(*keys)
            if action == "press":
                return self.press(args.get("keys") or args.get("text", ""))
            if action == "screenshot":
                return self.screenshot(args.get("path"))
            if action == "position":
                return self.position()
            return f"Unknown input action: {action}"
        except pyautogui.FailSafeException:  # type: ignore
            return "Fail-safe triggered (mouse hit a corner). Automation aborted."
        except Exception as exc:
            return f"Input action failed: {exc}"

    # ------------------------------------------------------------------ #
    # Mouse
    # ------------------------------------------------------------------ #
    def move(self, x: int, y: int, duration: Optional[float] = None) -> str:
        dur = duration if duration is not None else random.uniform(0.25, 0.6)
        pyautogui.moveTo(x, y, duration=dur, tween=pyautogui.easeInOutQuad)
        return f"Moved cursor to ({x}, {y})."

    def click(self, x: Optional[int] = None, y: Optional[int] = None,
              button: str = "left", clicks: int = 1) -> str:
        if x is not None and y is not None:
            self.move(int(x), int(y))
        pyautogui.click(button=button, clicks=clicks)
        where = f" at ({x}, {y})" if x is not None and y is not None else ""
        return f"{button.capitalize()} click{where}."

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> str:
        if x is not None and y is not None:
            self.move(int(x), int(y))
        pyautogui.doubleClick()
        return "Double-clicked."

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> str:
        self.move(x1, y1)
        pyautogui.dragTo(x2, y2, duration=random.uniform(0.4, 0.8),
                         button="left", tween=pyautogui.easeInOutQuad)
        return f"Dragged from ({x1}, {y1}) to ({x2}, {y2})."

    def scroll(self, amount: int) -> str:
        pyautogui.scroll(amount)
        direction = "down" if amount < 0 else "up"
        return f"Scrolled {direction} {abs(amount)} clicks."

    def position(self) -> str:
        p = pyautogui.position()
        return f"Cursor is at ({p.x}, {p.y}). Screen is {self.screen_w}x{self.screen_h}."

    # ------------------------------------------------------------------ #
    # Keyboard
    # ------------------------------------------------------------------ #
    def type_text(self, text: str, wpm: int = 300) -> str:
        if not text:
            return "Nothing to type."
        # Interval derived roughly from a target words-per-minute for realism.
        interval = max(0.005, 60.0 / (wpm * 5))
        pyautogui.typewrite(text, interval=interval)
        preview = text if len(text) <= 40 else text[:40] + "…"
        return f'Typed: "{preview}"'

    def press(self, key: Any) -> str:
        if isinstance(key, list):
            for k in key:
                pyautogui.press(str(k))
            return f"Pressed {', '.join(map(str, key))}."
        pyautogui.press(str(key))
        return f"Pressed {key}."

    def hotkey(self, *keys: str) -> str:
        if not keys:
            return "No hotkey combination given."
        pyautogui.hotkey(*[str(k) for k in keys])
        return f"Sent hotkey {'+'.join(keys)}."

    # ------------------------------------------------------------------ #
    # Screen
    # ------------------------------------------------------------------ #
    def screenshot(self, path: Optional[str] = None) -> str:
        if not path:
            fname = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            path = os.path.join(self.screenshot_dir, fname)
        img = pyautogui.screenshot()
        img.save(path)
        return f"Screenshot saved to {path}."

    def locate_on_screen(self, image_path: str, confidence: float = 0.8) -> str:
        """Find an image on screen (requires opencv for confidence)."""
        try:
            box = pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
        except Exception as exc:
            return f"Could not search for image: {exc}"
        if box:
            return f"Found at ({int(box.x)}, {int(box.y)})."
        return "Image not found on screen."
