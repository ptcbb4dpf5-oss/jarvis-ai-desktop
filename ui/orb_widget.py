"""
ui/orb_widget.py
================
The centerpiece: a stunning Iron Man-style arc-reactor orb rendered entirely with
PyQt6 QPainter — radial gradients, layered glow, a breathing pulse, rotating
segmented rings, and reactive voice-waveform rings.

States (set via `set_state`):
  idle       — slow breathing pulse, gentle ring rotation
  listening  — brighter core, expanding capture ring
  thinking   — faster counter-rotating rings + sweeping arc
  speaking   — amplitude-driven waveform rings (feed levels via set_amplitude)
  working    — pulsing amber-tinted rings, progress sweep

Everything is driven by a single ~60 FPS timer advancing a phase accumulator, so
animation stays smooth and CPU-light. The widget is transparent-friendly and
sizes itself to its parent.
"""

from __future__ import annotations

import math
import random
from typing import List, Tuple

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, pyqtProperty
from PyQt6.QtGui import (
    QColor, QPainter, QRadialGradient, QConicalGradient, QPen, QBrush,
    QPainterPath, QLinearGradient, QFont,
)
from PyQt6.QtWidgets import QWidget


# State constants (mirror core.agent)
IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"
SPEAKING = "speaking"
WORKING = "working"


# Palette (Iron Man cyan / arc-reactor blue, with amber accent for "working")
CYAN = QColor(0, 229, 255)
CYAN_DIM = QColor(0, 150, 190)
DEEP_BLUE = QColor(6, 40, 70)
CORE_WHITE = QColor(210, 250, 255)
AMBER = QColor(255, 176, 32)
DANGER = QColor(255, 70, 70)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class OrbWidget(QWidget):
    def __init__(self, parent=None, fps: int = 60) -> None:
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        self._state = IDLE
        self._phase = 0.0            # global animation phase
        self._ring_angle = 0.0       # primary ring rotation
        self._ring_angle2 = 0.0      # counter-rotating ring
        self._pulse = 0.0            # 0..1 breathing value
        self._amplitude = 0.0        # smoothed audio amplitude 0..1
        self._amp_target = 0.0
        self._waveform: List[float] = [0.0] * 72
        self._progress = 0.0         # 0..1 for working sweeps
        self._accent = CYAN

        # Precompute a stable set of "particle" angles for orbiting dots.
        self._particles: List[Tuple[float, float, float]] = [
            (random.uniform(0, 360), random.uniform(0.62, 0.95), random.uniform(0.5, 1.5))
            for _ in range(18)
        ]

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._interval_ms = max(8, int(1000 / fps))
        self._timer.start(self._interval_ms)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        if state == WORKING:
            self._accent = AMBER
        elif state == THINKING:
            self._accent = _mix(CYAN, QColor(120, 90, 255), 0.4)
        else:
            self._accent = CYAN

    def state(self) -> str:
        return self._state

    def set_amplitude(self, amp: float) -> None:
        """Feed a 0..1 audio amplitude (from the voice output) for waveform rings."""
        self._amp_target = max(0.0, min(1.0, amp))

    def set_progress(self, p: float) -> None:
        self._progress = max(0.0, min(1.0, p))

    def push_waveform(self, sample: float) -> None:
        self._waveform.pop(0)
        self._waveform.append(max(0.0, min(1.0, sample)))

    # ------------------------------------------------------------------ #
    # Animation tick
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        dt = self._interval_ms / 1000.0
        self._phase += dt

        # State-dependent rotation speeds.
        if self._state == THINKING:
            self._ring_angle += 160 * dt
            self._ring_angle2 -= 220 * dt
        elif self._state == WORKING:
            self._ring_angle += 90 * dt
            self._ring_angle2 -= 60 * dt
        elif self._state == LISTENING:
            self._ring_angle += 45 * dt
            self._ring_angle2 -= 30 * dt
        else:
            self._ring_angle += 22 * dt
            self._ring_angle2 -= 14 * dt
        self._ring_angle %= 360
        self._ring_angle2 %= 360

        # Breathing pulse — faster when active.
        speed = {
            IDLE: 1.4, LISTENING: 2.6, THINKING: 3.4, SPEAKING: 4.5, WORKING: 3.0,
        }.get(self._state, 1.4)
        self._pulse = 0.5 + 0.5 * math.sin(self._phase * speed)

        # Smooth amplitude toward target; decay when not speaking.
        if self._state == SPEAKING:
            self._amp_target *= 0.90  # natural decay between feeds
        else:
            self._amp_target *= 0.80
        self._amplitude += (self._amp_target - self._amplitude) * 0.35

        # Auto-generate a lively waveform when speaking even without real levels.
        if self._state == SPEAKING:
            base = 0.25 + 0.75 * self._amplitude
            sample = base * (0.6 + 0.4 * abs(math.sin(self._phase * 12)))
            self.push_waveform(sample + random.uniform(-0.06, 0.06))
        else:
            self.push_waveform(self._waveform[-1] * 0.85)

        if self._state == WORKING:
            self._progress = (self._progress + dt * 0.25) % 1.0

        self.update()

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        base_r = min(w, h) * 0.30
        accent = self._accent

        p.translate(cx, cy)

        # --- 1. Outer atmospheric glow ---
        glow_r = base_r * (2.15 + 0.18 * self._pulse)
        glow = QRadialGradient(QPointF(0, 0), glow_r)
        g0 = QColor(accent)
        g0.setAlpha(int(60 + 50 * self._pulse))
        glow.setColorAt(0.0, g0)
        mid = QColor(accent); mid.setAlpha(24)
        glow.setColorAt(0.45, mid)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, 0), glow_r, glow_r)

        # --- 2. Waveform rings (speaking) ---
        if self._state == SPEAKING:
            self._draw_waveform_rings(p, base_r, accent)

        # --- 3. Rotating segmented outer ring ---
        self._draw_segmented_ring(p, base_r * 1.62, self._ring_angle, accent,
                                  segments=48, thickness=2.4, gap_ratio=0.35)
        # counter-rotating thinner ring
        self._draw_segmented_ring(p, base_r * 1.42, self._ring_angle2,
                                  _mix(accent, CORE_WHITE, 0.2), segments=32,
                                  thickness=1.6, gap_ratio=0.5)

        # --- 4. Tick ring (fine graduations) ---
        self._draw_tick_ring(p, base_r * 1.25, accent)

        # --- 5. Progress / thinking sweep arc ---
        if self._state in (THINKING, WORKING):
            self._draw_sweep(p, base_r * 1.72, accent)

        # --- 6. Orbiting particles ---
        self._draw_particles(p, base_r, accent)

        # --- 7. The core sphere ---
        self._draw_core(p, base_r, accent)

        p.end()

    # ------------------------------------------------------------------ #
    def _draw_core(self, p: QPainter, base_r: float, accent: QColor) -> None:
        core_r = base_r * (0.92 + 0.10 * self._pulse)

        # Outer shell gradient (sphere shading).
        shell = QRadialGradient(QPointF(-core_r * 0.25, -core_r * 0.25), core_r * 1.4)
        shell.setColorAt(0.0, _mix(CORE_WHITE, accent, 0.25))
        shell.setColorAt(0.35, accent)
        shell.setColorAt(0.75, _mix(accent, DEEP_BLUE, 0.7))
        shell.setColorAt(1.0, DEEP_BLUE)
        p.setBrush(QBrush(shell))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, 0), core_r, core_r)

        # Inner rotating conical shimmer.
        cone = QConicalGradient(QPointF(0, 0), self._ring_angle * 2)
        c1 = QColor(accent); c1.setAlpha(0)
        c2 = QColor(CORE_WHITE); c2.setAlpha(90)
        cone.setColorAt(0.0, c1)
        cone.setColorAt(0.25, c2)
        cone.setColorAt(0.5, c1)
        cone.setColorAt(0.75, c2)
        cone.setColorAt(1.0, c1)
        p.setBrush(QBrush(cone))
        p.drawEllipse(QPointF(0, 0), core_r * 0.82, core_r * 0.82)

        # Bright inner light core.
        inner_r = core_r * (0.42 + 0.12 * self._pulse)
        inner = QRadialGradient(QPointF(0, 0), inner_r)
        inner.setColorAt(0.0, QColor(255, 255, 255, 255))
        inner.setColorAt(0.4, _mix(CORE_WHITE, accent, 0.4))
        c_edge = QColor(accent); c_edge.setAlpha(0)
        inner.setColorAt(1.0, c_edge)
        p.setBrush(QBrush(inner))
        p.drawEllipse(QPointF(0, 0), inner_r, inner_r)

        # Specular highlight.
        hi = QRadialGradient(QPointF(-core_r * 0.35, -core_r * 0.40), core_r * 0.5)
        hi.setColorAt(0.0, QColor(255, 255, 255, 150))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(hi))
        p.drawEllipse(QPointF(-core_r * 0.30, -core_r * 0.34),
                      core_r * 0.42, core_r * 0.30)

        # Rim light.
        rim = QColor(accent); rim.setAlpha(180)
        pen = QPen(rim, 1.8)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(0, 0), core_r, core_r)

    # ------------------------------------------------------------------ #
    def _draw_segmented_ring(self, p: QPainter, radius: float, angle: float,
                             color: QColor, segments: int, thickness: float,
                             gap_ratio: float) -> None:
        p.save()
        p.rotate(angle)
        seg_deg = 360.0 / segments
        span = seg_deg * (1 - gap_ratio)
        rect = QRectF(-radius, -radius, radius * 2, radius * 2)
        col = QColor(color)
        col.setAlpha(int(120 + 90 * self._pulse))
        pen = QPen(col, thickness)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(segments):
            start = int((i * seg_deg) * 16)
            p.drawArc(rect, start, int(span * 16))
        p.restore()

    def _draw_tick_ring(self, p: QPainter, radius: float, color: QColor) -> None:
        p.save()
        col = QColor(color); col.setAlpha(120)
        p.setPen(QPen(col, 1.0))
        n = 60
        for i in range(n):
            ang = math.radians(i * (360.0 / n) + self._ring_angle2 * 0.3)
            long_tick = (i % 5 == 0)
            r1 = radius
            r2 = radius + (7 if long_tick else 3)
            p.drawLine(QPointF(r1 * math.cos(ang), r1 * math.sin(ang)),
                       QPointF(r2 * math.cos(ang), r2 * math.sin(ang)))
        p.restore()

    def _draw_sweep(self, p: QPainter, radius: float, color: QColor) -> None:
        p.save()
        rect = QRectF(-radius, -radius, radius * 2, radius * 2)
        start_angle = -self._ring_angle * 2
        grad = QConicalGradient(QPointF(0, 0), start_angle)
        c_bright = QColor(color); c_bright.setAlpha(200)
        c_fade = QColor(color); c_fade.setAlpha(0)
        grad.setColorAt(0.0, c_bright)
        grad.setColorAt(0.15, c_fade)
        grad.setColorAt(1.0, c_fade)
        pen = QPen(QBrush(grad), 3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, int(start_angle * 16), int(120 * 16))
        p.restore()

    def _draw_particles(self, p: QPainter, base_r: float, color: QColor) -> None:
        for ang0, dist, speed in self._particles:
            ang = math.radians(ang0 + self._ring_angle * speed)
            r = base_r * (1.15 + dist * 0.55)
            x, y = r * math.cos(ang), r * math.sin(ang)
            size = 1.5 + 1.5 * self._pulse
            col = QColor(color)
            col.setAlpha(int(90 + 120 * self._pulse))
            glow = QRadialGradient(QPointF(x, y), size * 3)
            glow.setColorAt(0.0, col)
            edge = QColor(color); edge.setAlpha(0)
            glow.setColorAt(1.0, edge)
            p.setBrush(QBrush(glow))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x, y), size * 3, size * 3)

    def _draw_waveform_rings(self, p: QPainter, base_r: float, color: QColor) -> None:
        n = len(self._waveform)
        inner = base_r * 1.05
        max_len = base_r * 0.9
        col = QColor(color)
        for i, amp in enumerate(self._waveform):
            ang = math.radians(i * (360.0 / n))
            length = max_len * (0.15 + 0.85 * amp)
            r1 = inner
            r2 = inner + length
            a = int(80 + 150 * min(1.0, amp + 0.2))
            col.setAlpha(a)
            pen = QPen(col, 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(r1 * math.cos(ang), r1 * math.sin(ang)),
                       QPointF(r2 * math.cos(ang), r2 * math.sin(ang)))
