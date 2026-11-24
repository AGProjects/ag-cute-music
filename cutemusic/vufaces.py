"""The analog VU meters' look, shared by the amplifier (amp.py) and the VU meter's Needles style (visuals.py): one
face across a black-bezelled window for both channels, lit from behind by a bulb under the middle of each, the VU
scale (-20..+3, linear in voltage), the needles and the PEAK lamps."""

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainterPath, QPen, QRadialGradient


__all__ = ['AnalogFaces']


class AnalogFaces:
    """A mixin for a QWidget: _face_rects(window), _draw_meter_window(painter, window, faces), _draw_needle(painter,
    face, ratio, peak lit), _needle_ratio(VU)."""

    VU_MIN, VU_MAX, SWEEP = -20.0, 3.0, 42.0
    PEAK_VU = 1.0           # the PEAK lamp lit while a needle is past it (in the red): it follows the needle
    FACE_ASPECT = 2.6       # a channel's half of the face, its width to its height (the amplifier's)

    @staticmethod
    def _face_rects(window, stacked=False):
        """The two channels' halves of the face in the window, edge to edge, a thin bezel around them: side by side,
        or stacked (left over right: a narrow column)."""
        gap = (window.height() if stacked else window.width()) * 0.015
        if stacked:
            height = (window.height() - 2 * gap) / 2
            return [QRectF(window.left() + gap, window.top() + gap + index * height, window.width() - 2 * gap, height)
                    for index in range(2)]
        width = (window.width() - 2 * gap) / 2
        return [QRectF(window.left() + gap + index * width, window.top() + gap, width, window.height() - 2 * gap)
                for index in range(2)]

    def _draw_meter_window(self, painter, window, faces):
        """The black bezel, the face lit from behind, the scales, and the glass over them."""
        painter.setPen(QPen(QColor('#7d8188'), 1.2))
        painter.setBrush(QColor('#14161a'))
        painter.drawRoundedRect(window.adjusted(-2, -2, 2, 2), 5, 5)
        self._draw_backlight(painter, faces)
        for channel, face in enumerate(faces):
            self._draw_face(painter, face, 'LEFT' if channel == 0 else 'RIGHT')
        glare = QLinearGradient(window.topLeft(), window.bottomLeft())
        glare.setColorAt(0.0, QColor(255, 255, 255, 46))
        glare.setColorAt(0.45, QColor(255, 255, 255, 8))
        glare.setColorAt(0.46, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glare)
        painter.drawRoundedRect(window, 4, 4)

    def _draw_needle(self, painter, face, ratio, peak):
        pivot, radius = self._pivot(face)
        painter.save()
        painter.setClipRect(face)
        ratio = max(-0.02, ratio)
        painter.setPen(QPen(QColor('#16110b'), max(1.4, face.height() * 0.018)))
        painter.drawLine(self._point(pivot, ratio, radius * 0.36), self._point(pivot, ratio, radius * 0.97))
        painter.restore()
        small = max(6.0, face.height() * 0.085)
        self._draw_lamp(painter, QPointF(face.right() - small * 1.6, face.bottom() - small * 1.1), small * 0.42,
                        QColor('#ff3b30'), peak)

    @classmethod
    def _needle_ratio(cls, vu):
        vu = max(cls.VU_MIN - 1.5, min(cls.VU_MAX + 1, vu))
        low, high = 10 ** (cls.VU_MIN / 20), 10 ** (cls.VU_MAX / 20)
        return (10 ** (vu / 20) - low) / (high - low)

    def _font(self, pixels, bold=False, spacing=0.0, weight=None):
        font = QFont(self.font())
        font.setPixelSize(max(6, round(pixels)))
        font.setBold(bold)
        if weight is not None:
            font.setWeight(weight)
        if spacing:
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + spacing)
        return font

    def _draw_backlight(self, painter, faces):
        """The one face of both meters, lit from behind by a bulb under the middle of each channel: bright there,
        dimmer between them and at the ends, not evenly lit."""
        whole = faces[0].united(faces[1])
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#8c5f26'))         # the paper where the bulbs do not reach
        painter.drawRoundedRect(whole, 3, 3)
        clip = QPainterPath()
        clip.addRoundedRect(whole, 3, 3)
        painter.setClipPath(clip)
        for face in faces:
            bulb = QRadialGradient(face.center().x(), face.top() + face.height() * 0.7, face.width() * 0.62)
            bulb.setColorAt(0.0, QColor(255, 230, 168, 255))
            bulb.setColorAt(0.35, QColor(246, 204, 120, 235))
            bulb.setColorAt(0.75, QColor(214, 158, 72, 120))
            bulb.setColorAt(1.0, QColor(185, 127, 47, 0))
            painter.setBrush(bulb)
            painter.drawRect(whole)
        painter.restore()

    def _draw_face(self, painter, face, label):
        """The scale, its numbers, VU, the channel's name and PEAK: each text measured, and left out where it would
        run into another or out of the face (a small dial: fewer numbers, then no VU, PEAK, name)."""
        pivot, radius = self._pivot(face)
        small = max(6.0, face.height() * 0.085)
        painter.save()
        painter.setClipRect(face)
        font = self._font(small)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        placed = []                 # the texts drawn so far, a hair larger: none drawn over another

        # the scale's lines as points along them (the arc, the ticks): no text over them either
        lines = [self._point(pivot, index / 96, radius * 0.86) for index in range(97)]
        for vu in (-20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3):
            ratio = self._needle_ratio(vu)
            lines += [self._point(pivot, ratio, radius * (0.86 + 0.08 * step / 4)) for step in range(5)]

        def fits(rect):
            return (face.contains(rect) and not any(rect.intersects(other) for other in placed)
                    and not any(rect.contains(point) for point in lines))

        for vu in (-20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3):
            ratio = self._needle_ratio(vu)
            color = QColor('#b3261e') if vu > 0 else QColor('#2a2219')
            painter.setPen(QPen(color, 1.6))
            long = vu in (-20, -10, -5, 0, 3)
            painter.drawLine(self._point(pivot, ratio, radius * 0.86), self._point(pivot, ratio, radius * (0.94 if long else 0.9)))
        pad = small * 0.15
        for vu in (0, -20, 3, -10, -5, -7, -3):     # the ones that matter most first
            text = f'+{vu}' if vu > 0 else str(abs(vu))
            at = self._point(pivot, self._needle_ratio(vu), radius * 1.02)
            width, height = metrics.horizontalAdvance(text), metrics.ascent()
            rect = QRectF(at.x() - width / 2 - pad, at.y() - height / 2 - pad, width + 2 * pad, height + 2 * pad)
            if fits(rect):
                placed.append(rect)
                painter.setPen(QColor('#b3261e') if vu > 0 else QColor('#2a2219'))
                painter.drawText(QRectF(at.x() - small * 1.5, at.y() - small * 0.8, small * 3, small * 1.6),
                                 Qt.AlignmentFlag.AlignCenter, text)
        steps = 48
        for index in range(steps):
            ratio = index / steps
            red = ratio >= self._needle_ratio(0)
            painter.setPen(QPen(QColor('#b3261e') if red else QColor('#2a2219'), 3 if red else 1.4))
            painter.drawLine(self._point(pivot, ratio, radius * 0.86), self._point(pivot, (index + 1) / steps, radius * 0.86))
        # the bottom row: the channel's name on the left, PEAK right of it, left of the lamp
        row_font = self._font(small * 0.85, bold=True, spacing=15)
        row = QFontMetricsF(row_font)
        row_top, row_height = face.bottom() - small * 1.9, small * 1.6
        painter.setFont(row_font)
        peak_width = row.horizontalAdvance('PEAK')
        peak_right = face.right() - small * 2.6
        peak = QRectF(peak_right - peak_width, row_top, peak_width, row_height)
        if fits(peak):
            placed.append(peak)
            painter.setPen(QColor('#b3261e'))
            painter.drawText(peak, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 'PEAK')
        if label:
            name = QRectF(face.left() + small * 0.6, row_top, row.horizontalAdvance(label) + small * 0.6, row_height)
            if fits(name) and name.right() < (peak.left() if peak in placed else peak_right - small):
                placed.append(name)
                painter.setPen(QColor('#2a2219'))
                painter.drawText(name, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        # VU in the middle, between the numbers and the bottom row
        vu_font = self._font(face.height() * 0.15, bold=True)
        vu_metrics = QFontMetricsF(vu_font)
        middle = QPointF(face.center().x(), face.top() + face.height() * 0.6)
        width, height = vu_metrics.horizontalAdvance('VU'), vu_metrics.ascent()
        vu_rect = QRectF(middle.x() - width / 2, middle.y() - height / 2, width, height)
        if fits(vu_rect):
            painter.setFont(vu_font)
            painter.setPen(QColor('#2a2219'))
            painter.drawText(QRectF(face.left(), face.top() + face.height() * 0.5, face.width(), face.height() * 0.2),
                             Qt.AlignmentFlag.AlignCenter, 'VU')
        painter.restore()

    def _pivot(self, face):
        """The needles' pivot (under the face) and radius: the scale as wide as the face allows, its numbers just
        under the face's top."""
        small = max(6.0, face.height() * 0.085)
        radius = min(face.width() * 0.6, face.height() * 1.5)
        return QPointF(face.center().x(), face.top() + small * 1.7 + radius * 1.02), radius

    def _point(self, pivot, ratio, radius):
        angle = math.radians(-90 - self.SWEEP + 2 * self.SWEEP * ratio)
        return QPointF(pivot.x() + radius * math.cos(angle), pivot.y() + radius * math.sin(angle))

    def _draw_lamp(self, painter, centre, size, color, lit):
        glow = QRadialGradient(centre, size * 2.6)
        if lit:
            glow.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 150))
            glow.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            painter.setBrush(glow)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(centre, size * 2.6, size * 2.6)
        jewel = QRadialGradient(centre - QPointF(size * 0.3, size * 0.3), size * 1.2)
        base = color if lit else color.darker(320)
        jewel.setColorAt(0.0, base.lighter(170) if lit else base.lighter(130))
        jewel.setColorAt(1.0, base)
        painter.setBrush(jewel)
        painter.setPen(QPen(QColor('#3a3d42'), 0.8))
        painter.drawEllipse(centre, size, size)
