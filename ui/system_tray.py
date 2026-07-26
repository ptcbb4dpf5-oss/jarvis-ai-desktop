"""
ui/system_tray.py
=================
A modern, futuristic system-telemetry tray for Jarvis.

Instead of text rows, it draws a row of circular ring gauges (CPU, RAM, GPU,
DISK) — each an arc that fills clockwise and is colour-coded green -> amber ->
red. Core temperatures and a couple of readouts (NET, UPTIME) sit underneath.

All drawing is programmatic via QPainter to keep the Iron-Man HUD aesthetic and
avoid any image assets. Values are fed in via ``update_snapshot(snap)`` using the
same dict returned by ``SystemMonitor.snapshot()``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QRectF, QTimer
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QConicalGradient, QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

CYAN = QColor(0, 229, 255)
GREEN = QColor(56, 224, 143)
AMBER = QColor(255, 184, 77)
RED = QColor(255, 85, 85)
DIM = QColor(0, 229, 255, 40)
TEXT = QColor(155, 232, 255)
FAINT = QColor(127, 191, 208)
MONO = "Consolas"


def _lerp_color(pct: float) -> QColor:
    """Green -> amber -> red as pct goes 0 -> 100."""
    pct = max(0.0, min(100.0, pct))
    if pct <= 60:
        return GREEN
    if pct <= 85:
        return AMBER
    return RED


class _Gauge:
    """Lightweight holder for one ring gauge's animated state."""

    def __init__(self, key: str, label: str) -> None:
        self.key = key
        self.label = label
        self.target = 0.0
        self.shown = 0.0
        self.sub = ""          # small caption under the number (e.g. temp)
        self.available = True

    def step(self) -> None:
        # Smooth ease toward the target.
        self.shown += (self.target - self.shown) * 0.18
        if abs(self.target - self.shown) < 0.1:
            self.shown = self.target


class SystemTray(QWidget):
    """Circular-gauge telemetry tray. Sizes itself; place it top-left."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._gauges: List[_Gauge] = [
            _Gauge("cpu", "CPU"),
            _Gauge("ram", "RAM"),
            _Gauge("gpu", "GPU"),
            _Gauge("disk", "DISK"),
        ]
        self._footer: List[str] = ["NET ↓— ↑—", "UP —"]
        self._phase = 0.0
        self._temp_note = ""

        # Layout metrics.
        self._pad = 14
        self._gauge_d = 62          # gauge diameter
        self._gap = 14              # gap between gauges
        self._header_h = 22
        self._footer_h = 42

        cols = len(self._gauges)
        w = self._pad * 2 + cols * self._gauge_d + (cols - 1) * self._gap
        h = self._pad + self._header_h + self._gauge_d + 22 + self._footer_h
        self.setFixedSize(w, h)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(33)  # ~30fps for smooth ring fills

    # ------------------------------------------------------------------ #
    def update_snapshot(self, snap: Dict[str, Any]) -> None:
        if not snap or "error" in snap:
            for g in self._gauges:
                g.available = False
            self._temp_note = "telemetry unavailable"
            self.update()
            return

        def setg(key: str, pct, sub=""):
            g = next((x for x in self._gauges if x.key == key), None)
            if not g:
                return
            if pct is None:
                g.available = False
                g.target = 0.0
                g.sub = "n/a"
            else:
                g.available = True
                g.target = float(pct)
                g.sub = sub

        cpu_t = snap.get("cpu_temp")
        setg("cpu", snap.get("cpu_percent"),
             f"{cpu_t:.0f}°C" if cpu_t is not None else "")
        setg("ram", snap.get("ram_percent"), snap.get("ram_used_h", ""))
        gpu_t = snap.get("gpu_temp")
        setg("gpu", snap.get("gpu_percent"),
             f"{gpu_t:.0f}°C" if gpu_t is not None else "")
        setg("disk", snap.get("disk_percent"),
             (snap.get("disk_free_h", "") + " free") if snap.get("disk_free_h") else "")

        # Footer: core temps (if any), then net + uptime.
        cores = snap.get("core_temps") or []
        if cores:
            shown = "  ".join(f"{c:.0f}°" for c in cores[:6])
            src = snap.get("temp_source") or ""
            self._temp_note = f"CORES {shown}"
        elif cpu_t is not None:
            self._temp_note = f"CPU TEMP {cpu_t:.0f}°C"
        else:
            self._temp_note = "temps: install LibreHardwareMonitor"

        self._footer = [
            f"NET ↓{snap.get('net_down','—')}  ↑{snap.get('net_up','—')}",
            f"UP  {snap.get('uptime','—')}",
        ]
        self.update()

    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        self._phase += 0.03
        moving = False
        for g in self._gauges:
            before = g.shown
            g.step()
            if abs(g.shown - before) > 0.05:
                moving = True
        # Always repaint a little for the rotating accent; cheap at this size.
        self.update()

    # ------------------------------------------------------------------ #
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # Panel backdrop with rounded corners + subtle inner glow.
        panel = QRectF(1, 1, w - 2, h - 2)
        p.setPen(QPen(QColor(0, 229, 255, 110), 1.4))
        p.setBrush(QBrush(QColor(4, 12, 18, 165)))
        p.drawRoundedRect(panel, 14, 14)

        # Corner brackets for that HUD feel.
        self._draw_brackets(p, panel)

        # Header.
        p.setPen(QPen(CYAN))
        p.setFont(QFont(MONO, 9, QFont.Weight.Bold))
        p.drawText(QRectF(self._pad, 8, w - self._pad * 2, self._header_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "◈ SYSTEM")
        # Live dot on the right.
        pulse = 0.5 + 0.5 * math.sin(self._phase * 3)
        dotc = QColor(56, 224, 143, int(120 + 135 * pulse))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(dotc))
        p.drawEllipse(QRectF(w - self._pad - 8, 13, 7, 7))

        # Gauges row.
        y = self._pad + self._header_h
        x = self._pad
        for g in self._gauges:
            self._draw_gauge(p, x, y, self._gauge_d, g)
            x += self._gauge_d + self._gap

        # Temp note line.
        note_y = y + self._gauge_d + 4
        p.setPen(QPen(FAINT))
        p.setFont(QFont(MONO, 7))
        p.drawText(QRectF(self._pad, note_y, w - self._pad * 2, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._temp_note)

        # Footer readouts.
        fy = note_y + 16
        p.setPen(QPen(TEXT))
        p.setFont(QFont(MONO, 8))
        for i, line in enumerate(self._footer):
            p.drawText(QRectF(self._pad, fy + i * 15, w - self._pad * 2, 15),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       line)
        p.end()

    # ------------------------------------------------------------------ #
    def _draw_gauge(self, p: QPainter, x: float, y: float, d: float, g: _Gauge) -> None:
        rect = QRectF(x + 3, y + 3, d - 6, d - 6)
        cx, cy = rect.center().x(), rect.center().y()

        # Track ring.
        p.setPen(QPen(DIM, 5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, 0, 360 * 16)

        pct = max(0.0, min(100.0, g.shown))
        color = _lerp_color(pct) if g.available else QColor(90, 110, 120)

        # Value arc (start at top, go clockwise).
        if g.available and pct > 0:
            span = int(-pct / 100.0 * 360 * 16)
            pen = QPen(color, 5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, span)

            # Glow tip.
            ang = math.radians(90 - pct / 100.0 * 360)
            r = rect.width() / 2
            tipx = cx + r * math.cos(ang)
            tipy = cy - r * math.sin(ang)
            grad = QRadialGradient(tipx, tipy, 6)
            gc = QColor(color); gc.setAlpha(200)
            grad.setColorAt(0, gc)
            gc2 = QColor(color); gc2.setAlpha(0)
            grad.setColorAt(1, gc2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawEllipse(QRectF(tipx - 6, tipy - 6, 12, 12))

        # Center number.
        p.setPen(QPen(color if g.available else QColor(120, 140, 150)))
        p.setFont(QFont(MONO, 11, QFont.Weight.Bold))
        num = f"{pct:.0f}" if g.available else "—"
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, num)

        # Label above.
        p.setPen(QPen(FAINT))
        p.setFont(QFont(MONO, 7, QFont.Weight.Bold))
        p.drawText(QRectF(x, y - 2, d, 12), Qt.AlignmentFlag.AlignHCenter, g.label)

        # Sub caption below number.
        if g.sub:
            p.setPen(QPen(FAINT))
            p.setFont(QFont(MONO, 6))
            p.drawText(QRectF(x - 4, cy + 8, d + 8, 12),
                       Qt.AlignmentFlag.AlignHCenter, g.sub)

    def _draw_brackets(self, p: QPainter, r: QRectF) -> None:
        p.setPen(QPen(CYAN, 1.6))
        s = 12
        # Top-left
        p.drawLine(int(r.left() + 6), int(r.top() + 6), int(r.left() + 6 + s), int(r.top() + 6))
        p.drawLine(int(r.left() + 6), int(r.top() + 6), int(r.left() + 6), int(r.top() + 6 + s))
        # Bottom-right
        p.drawLine(int(r.right() - 6), int(r.bottom() - 6), int(r.right() - 6 - s), int(r.bottom() - 6))
        p.drawLine(int(r.right() - 6), int(r.bottom() - 6), int(r.right() - 6), int(r.bottom() - 6 - s))
