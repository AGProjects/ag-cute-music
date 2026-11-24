"""The markers of what plays (a recording's MARKs, any file's chapters: recorder.read_markers) on the play bar's
position slider: a small amber tick on the groove for each, its name and time as the tip; a click on a tick jumps
there, a right-click renames it (written back into the file). [ and ] jump to the previous and the next one
(window.py)."""

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QMenu, QSlider, QStyle, QStyleOptionSlider, QToolTip


__all__ = ['MarkerSlider', 'format_time']


def format_time(seconds):
    seconds = int(seconds)
    return f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60}:{seconds % 60:02d}'


class MarkerSlider(QSlider):
    """A horizontal QSlider in seconds with markers ([(seconds, name)]) drawn on it. marker_clicked(index): a tick
    clicked (jump there); marker_rename(index): Rename... chosen in a tick's menu."""

    marker_clicked = pyqtSignal(int)
    marker_rename = pyqtSignal(int)

    REACH = 5       # px either side of a tick that is still the tick

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.markers = []
        self.setMouseTracking(True)

    def set_markers(self, markers):
        self.markers = list(markers or [])
        self.update()

    def _groove(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
        return groove, handle

    def _x(self, seconds):
        """Where seconds is on the groove (the handle's centre would be there)."""
        groove, handle = self._groove()
        span = groove.width() - handle.width()
        position = QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), int(seconds), span)
        return groove.left() + handle.width() / 2 + position

    def _marker_at(self, x):
        if self.maximum() <= self.minimum():
            return None
        near = [(abs(self._x(seconds) - x), index) for index, (seconds, name) in enumerate(self.markers)]
        near = [item for item in near if item[0] <= self.REACH]
        return min(near)[1] if near else None

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.markers or self.maximum() <= self.minimum():
            return
        groove, handle = self._groove()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#ffb347'))
        middle = groove.center().y()
        size = max(4.0, self.height() * 0.22)
        for seconds, name in self.markers:
            if not self.minimum() <= seconds <= self.maximum():
                continue
            x = self._x(seconds)
            tick = QPainterPath()       # a small triangle pointing down onto the groove, and a line across it
            tick.moveTo(QPointF(x - size / 2, middle - size * 1.3))
            tick.lineTo(QPointF(x + size / 2, middle - size * 1.3))
            tick.lineTo(QPointF(x, middle - size * 0.35))
            tick.closeSubpath()
            painter.drawPath(tick)
            painter.drawRect(QRectF(x - 0.75, middle - size * 0.4, 1.5, size * 0.8))
        painter.end()

    def mouseMoveEvent(self, event):
        index = self._marker_at(event.position().x()) if not self.isSliderDown() else None
        if index is not None:
            seconds, name = self.markers[index]
            QToolTip.showText(event.globalPosition().toPoint(), f'{name}, {format_time(seconds)}', self)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._marker_at(event.position().x())
            if index is not None:
                self.marker_clicked.emit(index)
                event.accept()
                return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        index = self._marker_at(event.pos().x())
        if index is None:
            return super().contextMenuEvent(event)
        seconds, name = self.markers[index]
        menu = QMenu(self)
        menu.addAction(f'{name}, {format_time(seconds)}').setEnabled(False)
        menu.addAction('Rename...', lambda: self.marker_rename.emit(index))
        menu.exec(event.globalPos())
