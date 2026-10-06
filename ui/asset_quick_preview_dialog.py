from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from models.project import Asset
from ui.dialogs import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ui.icons import ASSET_ICON_SIZE, editflow_icon


class ImagePreviewCanvas(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("QuickPreviewCanvas")
        self.setMinimumSize(420, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._pixmap = QPixmap()
        self._message = "No image preview"
        self._scale = 1.0
        self._fit_to_window = True
        self._offset = QPointF(0, 0)
        self._dragging = False
        self._last_drag_position = QPointF(0, 0)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_pixmap(self, pixmap: QPixmap, message: str = "No image preview") -> None:
        self._pixmap = pixmap
        self._message = message
        self.fit_to_window()

    def clear(self, message: str = "No image preview") -> None:
        self._pixmap = QPixmap()
        self._message = message
        self._fit_to_window = True
        self._scale = 1.0
        self._offset = QPointF(0, 0)
        self.update()

    def fit_to_window(self) -> None:
        self._fit_to_window = True
        self._scale = self._fit_scale()
        self._offset = QPointF(0, 0)
        self.update()

    def actual_size(self) -> None:
        if self._pixmap.isNull():
            return
        self._fit_to_window = False
        self._scale = 1.0
        self._offset = QPointF(0, 0)
        self.update()

    def zoom_in(self) -> None:
        self.zoom_by(1.25)

    def zoom_out(self) -> None:
        self.zoom_by(0.8)

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        if self._pixmap.isNull():
            return
        anchor = anchor or QPointF(self.width() / 2, self.height() / 2)
        old_scale = self._scale
        new_scale = max(0.05, min(old_scale * factor, 12.0))
        if abs(new_scale - old_scale) < 0.001:
            return
        center = QPointF(self.width() / 2, self.height() / 2)
        pixmap_size = QSizeF(self._pixmap.size())
        old_top_left = center + self._offset - QPointF(pixmap_size.width() * old_scale / 2, pixmap_size.height() * old_scale / 2)
        image_point = QPointF(
            (anchor.x() - old_top_left.x()) / old_scale,
            (anchor.y() - old_top_left.y()) / old_scale,
        )
        new_top_left = anchor - QPointF(image_point.x() * new_scale, image_point.y() * new_scale)
        self._offset = new_top_left - center + QPointF(pixmap_size.width() * new_scale / 2, pixmap_size.height() * new_scale / 2)
        self._scale = new_scale
        self._fit_to_window = False
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_to_window:
            self._scale = self._fit_scale()
            self._offset = QPointF(0, 0)

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() == 0:
            event.ignore()
            return
        self.zoom_by(1.12 if event.angleDelta().y() > 0 else 1 / 1.12, event.position())
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._pixmap.isNull():
            self._dragging = True
            self._last_drag_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            delta = event.position() - self._last_drag_position
            self._offset += delta
            self._last_drag_position = event.position()
            self._fit_to_window = False
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#07111F"))
        if self._pixmap.isNull():
            painter.setPen(QColor("#8EA4C2"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            painter.end()
            return
        pixmap_size = QSizeF(self._pixmap.size())
        target_size = QSizeF(pixmap_size.width() * self._scale, pixmap_size.height() * self._scale)
        top_left = QPointF(
            (self.width() - target_size.width()) / 2 + self._offset.x(),
            (self.height() - target_size.height()) / 2 + self._offset.y(),
        )
        painter.drawPixmap(QRectF(top_left, target_size), self._pixmap, QRectF(self._pixmap.rect()))
        painter.end()

    def _fit_scale(self) -> float:
        if self._pixmap.isNull():
            return 1.0
        margin = 24
        available_width = max(1, self.width() - margin)
        available_height = max(1, self.height() - margin)
        width_scale = available_width / max(1, self._pixmap.width())
        height_scale = available_height / max(1, self._pixmap.height())
        return max(0.05, min(width_scale, height_scale))


class AssetQuickPreviewDialog(QDialog):
    def __init__(self, assets: list[Asset], start_asset_id: int | None = None, parent=None, *, autoplay_media: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("AssetQuickPreviewDialog")
        self.setWindowTitle("Quick Preview")
        self.setMinimumSize(720, 480)
        self.resize(1040, 720)
        self.assets = [asset for asset in assets if asset.id is not None and not asset.is_collection]
        self._index = self._index_for_asset(start_asset_id)
        self._title_text = "Quick Preview"
        self._media_source_path: Path | None = None
        self._media_outputs_attached = False
        self._autoplay_media = autoplay_media

        self.previous_button = QPushButton("Previous")
        self.previous_button.setIcon(editflow_icon("chevron-left"))
        self.previous_button.setIconSize(ASSET_ICON_SIZE)
        self.previous_button.clicked.connect(self._show_previous)
        self.next_button = QPushButton("Next")
        self.next_button.setIcon(editflow_icon("chevron-right"))
        self.next_button.setIconSize(ASSET_ICON_SIZE)
        self.next_button.clicked.connect(self._show_next)
        self.filename_label = QLabel("Quick Preview")
        self.filename_label.setObjectName("QuickPreviewTitle")
        self.filename_label.setMinimumWidth(0)
        self.filename_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(8)
        top_bar.addWidget(self.previous_button)
        top_bar.addWidget(self.filename_label, 1)
        top_bar.addWidget(self.next_button)

        self.stack = QStackedWidget()
        self.stack.setObjectName("QuickPreviewStack")
        self.image_canvas = ImagePreviewCanvas()
        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("QuickPreviewVideo")
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.message_label = QLabel("No preview available")
        self.message_label.setObjectName("QuickPreviewMessage")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        self.stack.addWidget(self.image_canvas)
        self.stack.addWidget(self.video_widget)
        self.stack.addWidget(self.message_label)

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.65)
        self._attach_outputs()
        self.media_player.durationChanged.connect(self._set_duration)
        self.media_player.positionChanged.connect(self._set_position)
        self.media_player.playbackStateChanged.connect(self._update_play_button)
        self.media_player.errorOccurred.connect(self._handle_error)

        self.image_controls = QFrame()
        self.image_controls.setObjectName("QuickPreviewControls")
        image_controls_layout = QHBoxLayout(self.image_controls)
        image_controls_layout.setContentsMargins(0, 0, 0, 0)
        image_controls_layout.setSpacing(6)
        self.fit_button = QPushButton("Fit")
        self.fit_button.clicked.connect(self.image_canvas.fit_to_window)
        self.actual_size_button = QPushButton("100%")
        self.actual_size_button.clicked.connect(self.image_canvas.actual_size)
        self.zoom_out_button = QPushButton("-")
        self.zoom_out_button.setToolTip("Zoom out")
        self.zoom_out_button.clicked.connect(self.image_canvas.zoom_out)
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setToolTip("Zoom in")
        self.zoom_in_button.clicked.connect(self.image_canvas.zoom_in)
        image_controls_layout.addStretch(1)
        image_controls_layout.addWidget(self.fit_button)
        image_controls_layout.addWidget(self.actual_size_button)
        image_controls_layout.addWidget(self.zoom_out_button)
        image_controls_layout.addWidget(self.zoom_in_button)
        image_controls_layout.addStretch(1)

        self.media_controls = QFrame()
        self.media_controls.setObjectName("QuickPreviewControls")
        media_controls_layout = QHBoxLayout(self.media_controls)
        media_controls_layout.setContentsMargins(0, 0, 0, 0)
        media_controls_layout.setSpacing(6)
        self.play_button = QPushButton("Play")
        self.play_button.setFixedWidth(58)
        self.play_button.clicked.connect(self._toggle_playback)
        self.back_5_button = QPushButton("-5s")
        self.back_5_button.clicked.connect(lambda: self._skip_millis(-5000))
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.position_slider.sliderMoved.connect(self.media_player.setPosition)
        self.forward_5_button = QPushButton("+5s")
        self.forward_5_button.clicked.connect(lambda: self._skip_millis(5000))
        self.duration_label = QLabel("00:00 / 00:00")
        self.duration_label.setObjectName("MutedLabel")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.duration_label.setFixedWidth(104)
        self.volume_button = QPushButton()
        self.volume_button.setObjectName("AssetVolumeButton")
        self.volume_button.setFixedSize(28, 28)
        self.volume_button.setIconSize(ASSET_ICON_SIZE)
        self.volume_button.clicked.connect(self._toggle_mute)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(65)
        self.volume_slider.setFixedWidth(96)
        self.volume_slider.valueChanged.connect(self._set_volume)
        media_controls_layout.addWidget(self.play_button)
        media_controls_layout.addWidget(self.back_5_button)
        media_controls_layout.addWidget(self.position_slider, 1)
        media_controls_layout.addWidget(self.forward_5_button)
        media_controls_layout.addWidget(self.duration_label)
        media_controls_layout.addWidget(self.volume_button)
        media_controls_layout.addWidget(self.volume_slider)
        self._update_volume_state()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addLayout(top_bar)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.image_controls)
        layout.addWidget(self.media_controls)

        self._load_current_asset()

    @property
    def current_media_path(self) -> Path | None:
        return self._media_source_path

    def release_media(self) -> None:
        if not hasattr(self, "media_player"):
            return
        for action in (
            self.media_player.stop,
            lambda: self.media_player.setSource(QUrl()),
            lambda: self.media_player.setVideoOutput(None),
            lambda: self.media_player.setAudioOutput(None),
        ):
            try:
                action()
            except (RuntimeError, TypeError):
                pass
        self._media_outputs_attached = False
        self._media_source_path = None
        self.play_button.setText("Play")
        self.position_slider.setRange(0, 0)
        self.position_slider.setValue(0)
        self.duration_label.setText("00:00 / 00:00")
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def closeEvent(self, event) -> None:
        self.release_media()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elided_title()

    def _index_for_asset(self, asset_id: int | None) -> int:
        if not self.assets:
            return -1
        if asset_id is None:
            return 0
        for index, asset in enumerate(self.assets):
            if asset.id == asset_id:
                return index
        return 0

    def _current_asset(self) -> Asset | None:
        if self._index < 0 or self._index >= len(self.assets):
            return None
        return self.assets[self._index]

    def _load_current_asset(self) -> None:
        asset = self._current_asset()
        self.release_media()
        self.image_canvas.clear("")
        self.previous_button.setEnabled(self._index > 0)
        self.next_button.setEnabled(0 <= self._index < len(self.assets) - 1)
        if asset is None:
            self._set_title_text("Quick Preview")
            self._show_message("No assets to preview.")
            return
        self._set_title_text(asset.file_path.name or asset.name)
        if not self._exists(asset):
            self._show_message(f"Missing from disk\n{asset.file_path}")
            return
        kind = self._kind(asset)
        if kind == "Image":
            pixmap = QPixmap(str(asset.file_path))
            if pixmap.isNull():
                self._show_message(f"Could not load image\n{asset.file_path}")
                return
            self.image_canvas.set_pixmap(pixmap, asset.file_path.name)
            self.stack.setCurrentWidget(self.image_canvas)
            self.image_controls.show()
            self.media_controls.hide()
            return
        if kind in {"Video", "Audio"}:
            self._attach_outputs()
            self._media_source_path = asset.file_path
            self.audio_output.setMuted(False)
            self.audio_output.setVolume(max(0, min(self.volume_slider.value(), 100)) / 100)
            self._update_volume_state()
            if kind == "Video":
                self.stack.setCurrentWidget(self.video_widget)
            else:
                self._show_message(f"Audio\n{asset.file_path.name}", show_media_controls=True)
            self.media_player.setSource(QUrl.fromLocalFile(str(asset.file_path)))
            self.image_controls.hide()
            self.media_controls.show()
            if self._autoplay_media:
                self.media_player.play()
            return
        self._show_message(f"No quick preview for this file type\n{asset.file_path.name}")

    def _show_message(self, text: str, *, show_media_controls: bool = False) -> None:
        self.message_label.setText(text)
        self.stack.setCurrentWidget(self.message_label)
        self.image_controls.hide()
        self.media_controls.setVisible(show_media_controls)

    def _show_previous(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._load_current_asset()

    def _show_next(self) -> None:
        if self._index < len(self.assets) - 1:
            self._index += 1
            self._load_current_asset()

    def _attach_outputs(self) -> None:
        if self._media_outputs_attached:
            return
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self._media_outputs_attached = True

    def _toggle_playback(self) -> None:
        if self._media_source_path is None:
            return
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def _skip_millis(self, delta: int) -> None:
        if self._media_source_path is None:
            return
        duration = max(0, self.media_player.duration())
        target = max(0, min(duration, self.media_player.position() + delta))
        self.media_player.setPosition(target)

    def _set_duration(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, duration))
        self._update_duration(self.media_player.position(), duration)

    def _set_position(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self._update_duration(position, self.media_player.duration())

    def _update_play_button(self, state) -> None:
        self.play_button.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    def _toggle_mute(self) -> None:
        self.audio_output.setMuted(not self.audio_output.isMuted())
        self._update_volume_state()

    def _set_volume(self, value: int) -> None:
        self.audio_output.setVolume(max(0, min(value, 100)) / 100)
        if value > 0 and self.audio_output.isMuted():
            self.audio_output.setMuted(False)
        self._update_volume_state()

    def _update_volume_state(self) -> None:
        muted = self.audio_output.isMuted() or self.volume_slider.value() <= 0
        self.volume_button.setIcon(editflow_icon("speaker-muted" if muted else "speaker"))
        self.volume_button.setToolTip("Unmute" if muted else "Mute")

    def _handle_error(self, _error, message: str) -> None:
        self.message_label.setText(message or "Could not preview this media file.")
        if self.stack.currentWidget() != self.video_widget:
            self.stack.setCurrentWidget(self.message_label)
        self.play_button.setText("Play")

    def _update_duration(self, position: int, duration: int) -> None:
        self.duration_label.setText(f"{self._format_millis(position)} / {self._format_millis(duration)}")

    def _format_millis(self, value: int) -> str:
        total_seconds = max(0, int(value / 1000))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _set_title_text(self, text: str) -> None:
        self._title_text = text or "Quick Preview"
        self.filename_label.setToolTip(self._title_text)
        self.setWindowTitle(f"Quick Preview - {self._title_text}")
        self._apply_elided_title()

    def _apply_elided_title(self) -> None:
        width = max(40, self.filename_label.width())
        elided = QFontMetrics(self.filename_label.font()).elidedText(
            self._title_text,
            Qt.TextElideMode.ElideRight,
            width,
        )
        self.filename_label.setText(elided)

    def _kind(self, asset: Asset) -> str:
        suffix = asset.file_path.suffix.casefold()
        if suffix in IMAGE_EXTENSIONS:
            return "Image"
        if suffix in VIDEO_EXTENSIONS:
            return "Video"
        if suffix in AUDIO_EXTENSIONS:
            return "Audio"
        return "File"

    def _exists(self, asset: Asset) -> bool:
        return asset.file_path.exists() or asset.file_path.is_symlink()
