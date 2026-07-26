"""
ui/hud_panel.py
===============
Sliding HUD stat panels rendered in the Iron Man aesthetic: translucent dark
cards with cyan borders, corner brackets, monospace text and animated slide
in/out transitions.

Two classes:
  * HudPanel      — a single sliding panel (left or right anchored) that can show
                    arbitrary key/value stat rows with mini bar gauges.
  * StatBar       — a small animated horizontal gauge used inside panels.

The panels animate their position with a QPropertyAnimation and fade with a
QGraphicsOpacityEffect so they feel like a real heads-up display.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QRect, pyqtProperty, QTimer,
)
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QLinearGradient
from PyQt6.QtWidgets import (
    QWidget, QGraphicsOpacityEffect, QVBoxLayout, QLabel, QSizePolicy,
)


CYAN = QColor(0, 229, 255)
CYAN_SOFT = QColor(0, 229, 255, 40)
PANEL_BG = QColor(6, 14, 20, 210)
TEXT = QColor(150, 235, 255)
WARN = QColor(255, 176, 32)
DANGER = QColor(255, 70, 70)
MONO = "Consolas"


def _gauge_color(pct: float) -> QColor:
    if pct >= 85:
        return DANGER
    if pct >= 65:
        return WARN
    return CYAN


class StatBar(QWidget):
    """A labelled horizontal gauge: LABEL  [#######----]  62%."""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._value = 0.0
        self._display = 0.0
        self._suffix = "%"
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(30)

    def set_value(self, value: float, suffix: str = "%") -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self._suffix = suffix

    def set_label(self, label: str) -> None:
        self._label = label

    def _animate(self) -> None:
        if abs(self._display - self._value) > 0.3:
            self._display += (self._value - self._display) * 0.2
            self.update()
        elif self._display != self._value:
            self._display = self._value
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        font = QFont(MONO, 9)
        p.setFont(font)

        # Label (left).
        p.setPen(QPen(TEXT))
        label_w = 70
        p.drawText(QRect(0, 0, label_w, h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._label)

        # Gauge track.
        bar_x = label_w + 4
        val_w = 46
        bar_w = max(20, w - bar_x - val_w)
        bar_h = 8
        bar_y = (h - bar_h) / 2
        track = QRect(bar_x, int(bar_y), bar_w, bar_h)
        p.setPen(QPen(QColor(0, 229, 255, 60), 1))
        p.setBrush(QBrush(QColor(0, 40, 55, 120)))
        p.drawRect(track)

        # Filled portion.
        col = _gauge_color(self._display)
        fill_w = int(bar_w * (self._display / 100.0))
        if fill_w > 0:
            grad = QLinearGradient(bar_x, 0, bar_x + fill_w, 0)
            grad.setColorAt(0.0, QColor(col.red(), col.green(), col.blue(), 120))
            grad.setColorAt(1.0, col)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawRect(QRect(bar_x, int(bar_y), fill_w, bar_h))

        # Value text (right).
        p.setPen(QPen(col))
        p.drawText(QRect(bar_x + bar_w + 2, 0, val_w, h),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._display:0.0f}{self._suffix}")
        p.end()


class HudPanel(QWidget):
    """A sliding translucent HUD panel anchored to the left or right edge."""

    def __init__(self, title: str = "SYSTEM", side: str = "left",
                 width: int = 260, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._side = side
        self._panel_w = width
        self._shown = False

        self.setFixedWidth(width)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        # Opacity effect for fades.
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # Layout for content.
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 40, 18, 18)
        self._layout.setSpacing(6)

        self._bars: Dict[str, StatBar] = {}
        self._text_rows: List[QLabel] = []

        self._slide_anim = QPropertyAnimation(self, b"geometry")
        self._slide_anim.setDuration(420)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_anim.setDuration(420)

        self.hide()

    # ------------------------------------------------------------------ #
    # Content management
    # ------------------------------------------------------------------ #
    def set_title(self, title: str) -> None:
        self._title = title
        self.update()

    def add_bar(self, key: str, label: str) -> StatBar:
        bar = StatBar(label, self)
        self._bars[key] = bar
        self._layout.addWidget(bar)
        return bar

    def update_bar(self, key: str, value: float, suffix: str = "%") -> None:
        if key in self._bars:
            self._bars[key].set_value(value, suffix)

    def add_text_row(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setFont(QFont(MONO, 9))
        lbl.setStyleSheet("color: rgba(150,235,255,220); background: transparent;")
        lbl.setWordWrap(True)
        self._text_rows.append(lbl)
        self._layout.addWidget(lbl)
        return lbl

    def set_text_rows(self, lines: List[str]) -> None:
        # Ensure we have enough labels.
        while len(self._text_rows) < len(lines):
            self.add_text_row("")
        for i, lbl in enumerate(self._text_rows):
            if i < len(lines):
                lbl.setText(lines[i])
                lbl.show()
            else:
                lbl.hide()

    def clear_rows(self) -> None:
        for lbl in self._text_rows:
            lbl.hide()

    # ------------------------------------------------------------------ #
    # Slide animation
    # ------------------------------------------------------------------ #
    def _target_geometries(self) -> Tuple[QRect, QRect]:
        parent = self.parentWidget()
        if parent is None:
            r = self.geometry()
            return r, r
        ph = parent.height()
        margin = 24
        panel_h = min(max(self.sizeHint().height(), 160), ph - 2 * margin)
        y = int((ph - panel_h) / 2)
        if self._side == "left":
            shown = QRect(margin, y, self._panel_w, panel_h)
            hidden = QRect(-self._panel_w - 10, y, self._panel_w, panel_h)
        else:
            pw = parent.width()
            shown = QRect(pw - self._panel_w - margin, y, self._panel_w, panel_h)
            hidden = QRect(pw + 10, y, self._panel_w, panel_h)
        return shown, hidden

    def slide_in(self) -> None:
        if self._shown:
            self._reposition()
            return
        self._shown = True
        self.show()
        self.raise_()
        shown, hidden = self._target_geometries()
        self.setGeometry(hidden)
        self._slide_anim.stop()
        self._slide_anim.setStartValue(hidden)
        self._slide_anim.setEndValue(shown)
        self._slide_anim.start()
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def slide_out(self) -> None:
        if not self._shown:
            return
        self._shown = False
        shown, hidden = self._target_geometries()
        self._slide_anim.stop()
        self._slide_anim.setStartValue(self.geometry())
        self._slide_anim.setEndValue(hidden)
        self._slide_anim.start()
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity.opacity())
        self._fade_anim.setEndValue(0.0)
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._fade_anim.finished.connect(self._on_hidden)
        self._fade_anim.start()

    def toggle(self) -> None:
        if self._shown:
            self.slide_out()
        else:
            self.slide_in()

    def _on_hidden(self) -> None:
        if not self._shown:
            self.hide()

    def _reposition(self) -> None:
        if self._shown:
            shown, _ = self._target_geometries()
            self.setGeometry(shown)

    @property
    def is_shown(self) -> bool:
        return self._shown

    # ------------------------------------------------------------------ #
    # Painting: frame + corner brackets + title bar
    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # Background.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(PANEL_BG))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # Border.
        pen = QPen(QColor(0, 229, 255, 150), 1.4)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # Corner brackets.
        self._draw_brackets(p, w, h)

        # Title bar.
        p.setPen(QPen(CYAN))
        p.setFont(QFont(MONO, 10, QFont.Weight.Bold))
        p.drawText(QRect(16, 12, w - 32, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"▸ {self._title}")
        # Underline under title.
        grad = QLinearGradient(16, 0, w - 16, 0)
        grad.setColorAt(0.0, CYAN)
        grad.setColorAt(1.0, QColor(0, 229, 255, 0))
        p.setPen(QPen(QBrush(grad), 1.2))
        p.drawLine(16, 32, w - 16, 32)
        p.end()

    def _draw_brackets(self, p: QPainter, w: int, h: int) -> None:
        p.setPen(QPen(CYAN, 2.0))
        L = 16
        # top-left
        p.drawLine(4, 4, 4 + L, 4)
        p.drawLine(4, 4, 4, 4 + L)
        # top-right
        p.drawLine(w - 4, 4, w - 4 - L, 4)
        p.drawLine(w - 4, 4, w - 4, 4 + L)
        # bottom-left
        p.drawLine(4, h - 4, 4 + L, h - 4)
        p.drawLine(4, h - 4, 4, h - 4 - L)
        # bottom-right
        p.drawLine(w - 4, h - 4, w - 4 - L, h - 4)
        p.drawLine(w - 4, h - 4, w - 4, h - 4 - L)
