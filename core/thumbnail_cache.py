from __future__ import annotations

from pathlib import Path
import hashlib

from PySide6.QtCore import (
    QCoreApplication,
    QEventLoop,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPen, QPolygon
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

from core.file_manager import is_image_file, is_video_file, normalize_file_path


THUMBNAIL_SIZE = QSize(160, 90)
THUMBNAIL_CACHE_VERSION = "video-frame-v2"
VIDEO_THUMBNAIL_TIMEOUT_MS = 4500


class ThumbnailWorkerSignals(QObject):
    ready = Signal(str, str)
    failed = Signal(str)


class ThumbnailWorker(QRunnable):
    def __init__(self, source_path: Path, cache_path: Path, normalized_path: str) -> None:
        super().__init__()
        self.source_path = source_path
        self.cache_path = cache_path
        self.normalized_path = normalized_path
        self.signals = ThumbnailWorkerSignals()

    def run(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            image = render_thumbnail(self.source_path)
            if image.save(str(self.cache_path), "PNG"):
                self.signals.ready.emit(self.normalized_path, str(self.cache_path))
            else:
                self.signals.failed.emit(self.normalized_path)
        except Exception:
            self.signals.failed.emit(self.normalized_path)


class ThumbnailProvider(QObject):
    thumbnail_ready = Signal(str, str)

    def __init__(self, cache_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.pool = QThreadPool.globalInstance()
        self._pending: set[str] = set()

    def normalized(self, path: Path) -> str:
        return normalize_file_path(path)

    def cached_thumbnail(self, path: Path) -> Path | None:
        try:
            cache_path = self._cache_path(path)
        except OSError:
            return None
        return cache_path if cache_path.exists() else None

    def request(self, path: Path) -> None:
        try:
            normalized_path = self.normalized(path)
            cache_path = self._cache_path(path)
        except OSError:
            return

        if cache_path.exists():
            self.thumbnail_ready.emit(normalized_path, str(cache_path))
            return
        if normalized_path in self._pending:
            return

        self._pending.add(normalized_path)
        worker = ThumbnailWorker(path, cache_path, normalized_path)
        worker.signals.ready.connect(self._handle_ready)
        worker.signals.failed.connect(self._handle_failed)
        self.pool.start(worker)

    def _handle_ready(self, normalized_path: str, cache_path: str) -> None:
        self._pending.discard(normalized_path)
        self.thumbnail_ready.emit(normalized_path, cache_path)

    def _handle_failed(self, normalized_path: str) -> None:
        self._pending.discard(normalized_path)

    def _cache_path(self, path: Path) -> Path:
        source = path.expanduser()
        stat = source.stat()
        normalized_path = normalize_file_path(source)
        digest = hashlib.sha1(
            (
                f"{THUMBNAIL_CACHE_VERSION}|"
                f"{normalized_path}|"
                f"{stat.st_mtime_ns}|"
                f"{stat.st_size}"
            ).encode("utf-8")
        ).hexdigest()
        return self.cache_dir / f"{digest}.png"


def render_thumbnail(path: Path) -> QImage:
    if is_image_file(path):
        image = _read_image_thumbnail(path)
        if not image.isNull():
            return image
    if is_video_file(path):
        image = _read_video_thumbnail(path)
        if not image.isNull():
            return image
        return _render_video_tile(path)
    return _render_asset_tile(path)


def _read_image_thumbnail(path: Path) -> QImage:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    original_size = reader.size()
    if original_size.isValid():
        reader.setScaledSize(
            original_size.scaled(
                THUMBNAIL_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
            )
        )
    image = reader.read()
    if image.isNull():
        return image
    return _center_on_canvas(image)


def _center_on_canvas(image: QImage) -> QImage:
    canvas = QImage(THUMBNAIL_SIZE, QImage.Format.Format_RGB32)
    canvas.fill(QColor("#101820"))
    scaled = image.scaled(
        THUMBNAIL_SIZE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    x = (THUMBNAIL_SIZE.width() - scaled.width()) // 2
    y = (THUMBNAIL_SIZE.height() - scaled.height()) // 2
    painter.drawImage(x, y, scaled)
    painter.end()
    return canvas


def _read_video_thumbnail(path: Path) -> QImage:
    if QCoreApplication.instance() is None:
        return QImage()

    result = {"image": QImage()}
    loop = QEventLoop()

    player = QMediaPlayer()
    sink = QVideoSink()
    timeout = QTimer()
    timeout.setSingleShot(True)

    player.setVideoSink(sink)

    def capture_frame(frame) -> None:
        if not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        result["image"] = image.copy()
        loop.quit()

    def handle_status(status) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            duration = player.duration()
            if duration > 0:
                player.setPosition(min(1200, max(0, duration // 3)))
        elif status in {
            QMediaPlayer.MediaStatus.InvalidMedia,
            QMediaPlayer.MediaStatus.EndOfMedia,
        }:
            loop.quit()

    sink.videoFrameChanged.connect(capture_frame)
    player.mediaStatusChanged.connect(handle_status)
    player.errorOccurred.connect(lambda _error, _message: loop.quit())
    timeout.timeout.connect(loop.quit)

    player.setSource(QUrl.fromLocalFile(str(path.expanduser().resolve())))
    timeout.start(VIDEO_THUMBNAIL_TIMEOUT_MS)
    player.play()
    QTimer.singleShot(350, lambda: player.setPosition(1200))
    loop.exec()

    timeout.stop()
    for action in (
        player.stop,
        lambda: player.setVideoSink(None),
        lambda: player.setSource(QUrl()),
    ):
        try:
            action()
        except (RuntimeError, TypeError):
            pass

    image = result["image"]
    if image.isNull():
        return image
    return _center_on_canvas(image)


def _render_video_tile(path: Path) -> QImage:
    canvas = _base_tile("#102236", "#243b62")
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#FFFFFF"))
    painter.setPen(QPen(QColor("#FFFFFF")))
    triangle = QPolygon(
        [
            QPoint(67, 32),
            QPoint(67, 58),
            QPoint(94, 45),
        ]
    )
    painter.drawPolygon(triangle)
    _draw_filename(painter, path.name)
    painter.end()
    return canvas


def _render_asset_tile(path: Path) -> QImage:
    canvas = _base_tile("#1d2430", "#384253")
    painter = QPainter(canvas)
    _draw_filename(painter, path.name)
    painter.end()
    return canvas


def _base_tile(top: str, bottom: str) -> QImage:
    canvas = QImage(THUMBNAIL_SIZE, QImage.Format.Format_RGB32)
    canvas.fill(QColor(top))
    painter = QPainter(canvas)
    painter.fillRect(0, 56, THUMBNAIL_SIZE.width(), 34, QColor(bottom))
    painter.end()
    return canvas


def _draw_filename(painter: QPainter, filename: str) -> None:
    painter.setPen(QColor("#dfe8f7"))
    font = QFont("Segoe UI", 8)
    painter.setFont(font)
    painter.drawText(
        8,
        64,
        THUMBNAIL_SIZE.width() - 16,
        20,
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        filename,
    )
