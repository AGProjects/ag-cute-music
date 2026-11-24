"""Color markers: six colors (Finder's) a song can have, several at once, kept in its file's tags so they go where
the file goes (the organizer, another computer, a library shared on the LAN). An album, an artist: its songs colored.

In the tags: AG_COLORS, the colors' names separated by commas ("red,blue"): a Vorbis comment (FLAC, Ogg, Opus), an
ID3 TXXX frame (MP3, WAV, AIFF), an MP4 freeform atom (----:com.agprojects:AG_COLORS), an APEv2 or ASF item.

The library (browser.py): the colors of a song after its name, an album's those all its songs have; the six dots
beside Genres filter the tree (any of those ticked); right-click -> the dots, to color what is selected."""

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QWidget

from cutemusic.util import log


__all__ = ['COLORS', 'NAMES', 'TAG', 'parse', 'read_from', 'write_colors', 'draw_dots', 'dots_width', 'ColorDot',
           'ColorRow', 'color_icon']


COLORS = (('red', 'Red', '#ff3b30'), ('orange', 'Orange', '#ff9500'), ('yellow', 'Yellow', '#ffcc00'),
          ('green', 'Green', '#34c759'), ('blue', 'Blue', '#007aff'), ('purple', 'Purple', '#af52de'))
NAMES = [key for key, label, color in COLORS]
LABELS = {key: label for key, label, color in COLORS}
QCOLORS = {key: QColor(color) for key, label, color in COLORS}
TAG = 'AG_COLORS'
EASY_KEY = 'agcolors'       # its name in mutagen's easy interface
MP4_MEAN = 'com.agprojects'

_registered = False


def _register():
    """The tag as an easy key of ID3 (TXXX) and MP4 (freeform): the Vorbis comments, APEv2 and ASF take any name."""
    global _registered
    if _registered:
        return
    _registered = True
    try:
        from mutagen.easyid3 import EasyID3
        EasyID3.RegisterTXXXKey(EASY_KEY, TAG)
    except Exception as e:
        log.warning(f'[colors] ID3: {e}')
    try:
        from mutagen.easymp4 import EasyMP4Tags
        EasyMP4Tags.RegisterFreeformKey(EASY_KEY, TAG, mean=MP4_MEAN)
    except Exception as e:
        log.warning(f'[colors] MP4: {e}')


def parse(values):
    """The colors in a tag's values (["red,blue"], "red, Blue"): known ones, each once, in the six's order."""
    if isinstance(values, str):
        values = [values]
    found = set()
    for value in values or ():
        for name in str(value).replace(';', ',').split(','):
            if name.strip().lower() in LABELS:
                found.add(name.strip().lower())
    return [name for name in NAMES if name in found]


def _text(values):
    return ','.join(parse(values))


def read_from(audio):
    """The colors of a file opened with mutagen.File(path, easy=True) (library.read_track)."""
    _register()
    tags = getattr(audio, 'tags', None)
    if not tags:
        return []
    try:
        if hasattr(tags, 'getall'):         # raw ID3 (WAV, AIFF: no easy interface)
            return parse([text for frame in tags.getall(f'TXXX:{TAG}') for text in frame.text])
        for key in (EASY_KEY, TAG):         # easy ID3 and MP4; the Vorbis comments (any case); APEv2, ASF
            if key in tags:
                return parse([str(value) for value in tags[key]] if isinstance(tags[key], list) else [str(tags[key])])
    except Exception as e:
        log.warning(f'[colors] {getattr(audio, "filename", "")}: {e}')
    return []


def write_colors(path, colors):
    """The colors (names; none removes the tag) written in path's tags. None, or the error."""
    try:
        import mutagen
    except ImportError:
        return 'mutagen is missing (python3-mutagen)'
    _register()
    text = _text(colors)
    try:
        audio = mutagen.File(path, easy=True)
        if audio is None:
            return 'unknown format'
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        if hasattr(tags, 'getall'):         # raw ID3
            from mutagen.id3 import TXXX
            tags.delall(f'TXXX:{TAG}')
            if text:
                tags.add(TXXX(encoding=3, desc=TAG, text=[text]))
        else:
            key = EASY_KEY if _easy(tags) else TAG
            for old in {EASY_KEY, TAG}:
                try:
                    if old in tags:
                        del tags[old]
                except (KeyError, ValueError):
                    pass
            if text:
                tags[key] = [text]
        audio.save()
    except Exception as e:
        return str(e) or e.__class__.__name__
    return None


def _easy(tags):
    """Whether tags are an easy interface that knows the key by its easy name (ID3, MP4)."""
    name = type(tags).__name__
    return name in ('EasyID3', 'EasyMP4Tags')


# Drawing

DOT = 8         # px across, at the rows' size
GAP = 3


def dots_width(count, size=DOT):
    return count * size + max(0, count - 1) * GAP if count else 0


def draw_dots(painter, rect, colors, size=DOT):
    """The colors' dots, right-aligned in rect, centred on its height; overlapping a little when many."""
    if not colors:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    x = rect.right() - dots_width(len(colors), size)
    y = rect.center().y() - size / 2
    for name in colors:
        color = QCOLORS.get(name)
        if color is None:
            continue
        painter.setPen(QPen(color.darker(130), 1))
        painter.setBrush(color)
        painter.drawEllipse(QRectF(x + 0.5, y + 0.5, size - 1, size - 1))
        x += size + GAP
    painter.restore()


def color_icon(name, size=14):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QCOLORS[name]
    painter.setPen(QPen(color.darker(130), 1))
    painter.setBrush(color)
    painter.drawEllipse(QRectF(1.5, 1.5, size - 3, size - 3))
    painter.end()
    return QIcon(pixmap)


class ColorDot(QAbstractButton):
    """A color's dot, checkable: full when checked (a ring round it), faint when not. Painted here, not by the
    style: six of them, small."""

    SIZE = 16

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(LABELS[name])
        self.partial = False        # (the menu's: some of the songs have it, not all)

    def sizeHint(self):
        return QSize(self.SIZE + 2, self.SIZE + 2)

    def minimumSizeHint(self):
        return self.sizeHint()

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(QCOLORS[self.name])
        side = min(self.width(), self.height())
        box = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        inner = box.adjusted(3.5, 3.5, -3.5, -3.5)
        if self.isChecked():
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(box.adjusted(1, 1, -1, -1))
        elif not self.underMouse() and not self.partial:
            color.setAlphaF(0.4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(inner)
        if self.partial and not self.isChecked():       # some of them: a dash across
            painter.setPen(QPen(QColor('white'), 1.5))
            middle = inner.center().y()
            painter.drawLine(QPointF(inner.left() + 2.5, middle), QPointF(inner.right() - 2.5, middle))


class ColorRow(QWidget):
    """The six dots in a row. toggled(name, on): a dot clicked."""

    toggled = pyqtSignal(str, bool)

    def __init__(self, parent=None, spacing=1):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        self.dots = {}
        for name in NAMES:
            dot = ColorDot(name, self)
            dot.toggled.connect(lambda on, name=name: self.toggled.emit(name, on))
            layout.addWidget(dot)
            self.dots[name] = dot

    def set_checked(self, names, partial=()):
        for name, dot in self.dots.items():
            dot.blockSignals(True)
            dot.setChecked(name in names)
            dot.blockSignals(False)
            dot.partial = name in partial and name not in names
            dot.update()

    def checked(self):
        return {name for name, dot in self.dots.items() if dot.isChecked()}
