from __future__ import annotations

from pathlib import Path
from html import escape

from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtGui import QFontMetrics, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout

from models.project import Asset
from ui.dialogs import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ui.icons import ASSET_ICON_SIZE, ASSET_PREVIEW_ICON_SIZE, editflow_icon, editflow_pixmap


MEDIA_PREVIEW_HEIGHT = 168
SEEK_SLIDER_WIDTH = 96
TIME_LABEL_WIDTH = 76


class AssetPreviewPanel(QFrame):
    quick_preview_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AssetPreviewPanel")
        self.setMinimumWidth(0)
        self.asset_by_id: dict[int, Asset] = {}
        self.tag_map: dict[int, list[str]] = {}
        self.favorite_ids: set[int] = set()
        self.usage_counts: dict[int, int] = {}
        self._media_source_path: Path | None = None
        self._media_outputs_attached = False
        self._title_text = "Preview"

        self.title_label = QLabel("Preview")
        self.title_label.setObjectName("SectionTitle")
        self.title_label.setWordWrap(False)
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        self.media_label = QLabel("Select an asset")
        self.media_label.setObjectName("AssetPreviewMedia")
        self.media_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_label.setFixedHeight(MEDIA_PREVIEW_HEIGHT)
        self.media_label.setWordWrap(True)
        self.media_label.setMinimumWidth(0)
        self.media_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("AssetVideoPreview")
        self.video_widget.setFixedHeight(MEDIA_PREVIEW_HEIGHT)
        self.video_widget.setMinimumWidth(0)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.video_widget.hide()

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.65)
        self._attach_outputs()
        self.media_player.durationChanged.connect(self._set_duration)
        self.media_player.positionChanged.connect(self._set_position)
        self.media_player.playbackStateChanged.connect(self._update_play_button)
        self.media_player.errorOccurred.connect(self._handle_error)

        self.play_button = QPushButton("Play")
        self.play_button.setFixedWidth(50)
        self.play_button.clicked.connect(self._toggle_playback)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setFixedWidth(SEEK_SLIDER_WIDTH)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.media_player.setPosition)
        self.duration_label = QLabel("00:00 / 00:00")
        self.duration_label.setObjectName("MutedLabel")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.duration_label.setFixedWidth(TIME_LABEL_WIDTH)
        self.volume_button = QPushButton()
        self.volume_button.setObjectName("AssetVolumeButton")
        self.volume_button.setFixedSize(26, 26)
        self.volume_button.setIconSize(ASSET_ICON_SIZE)
        self.volume_button.clicked.connect(self._toggle_volume_popup)

        self.volume_popup = QFrame(self)
        self.volume_popup.setObjectName("AssetVolumePopup")
        self.volume_popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.volume_popup.setFixedSize(62, 126)
        volume_layout = QVBoxLayout(self.volume_popup)
        volume_layout.setContentsMargins(8, 8, 8, 8)
        volume_layout.setSpacing(5)
        self.volume_state_label = QLabel("65%")
        self.volume_state_label.setObjectName("MutedLabel")
        self.volume_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.volume_slider = QSlider(Qt.Orientation.Vertical)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(65)
        self.volume_slider.setFixedSize(24, 72)
        self.volume_slider.setToolTip("Volume")
        self.volume_slider.valueChanged.connect(self._set_volume)
        self.volume_mute_button = QPushButton("Mute")
        self.volume_mute_button.setObjectName("AssetVolumeMuteButton")
        self.volume_mute_button.clicked.connect(self._toggle_mute)
        volume_layout.addWidget(self.volume_state_label)
        volume_layout.addWidget(self.volume_slider, 0, Qt.AlignmentFlag.AlignHCenter)
        volume_layout.addWidget(self.volume_mute_button)
        self.volume_popup.hide()

        self.controls = QFrame()
        self.controls.setObjectName("AssetMediaControls")
        controls_layout = QHBoxLayout(self.controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.position_slider)
        controls_layout.addWidget(self.duration_label)
        controls_layout.addWidget(self.volume_button)
        self.controls.hide()
        self._update_volume_state()

        self.state_label = QLabel("")
        self.state_label.setObjectName("MutedLabel")
        self.state_label.setWordWrap(True)
        self.state_label.setMinimumWidth(0)
        self.state_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.metadata_label = QLabel("")
        self.metadata_label.setObjectName("MutedLabel")
        self.metadata_label.setTextFormat(Qt.TextFormat.RichText)
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setMinimumWidth(0)
        self.metadata_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.metadata_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.media_label)
        layout.addWidget(self.video_widget)
        layout.addWidget(self.controls)
        layout.addWidget(self.state_label)
        layout.addWidget(self.metadata_label)
        layout.addStretch(1)

        self._quick_preview_event_widgets = (self.title_label, self.media_label, self.video_widget)
        for widget in self._quick_preview_event_widgets:
            widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if (
            hasattr(self, "_quick_preview_event_widgets")
            and watched in self._quick_preview_event_widgets
            and event.type() == QEvent.Type.MouseButtonDblClick
        ):
            self.quick_preview_requested.emit()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elided_title()
        if hasattr(self, "volume_popup") and self.volume_popup.isVisible():
            self._position_volume_popup()

    def mouseDoubleClickEvent(self, event) -> None:
        self.quick_preview_requested.emit()
        event.accept()

    @property
    def current_media_path(self) -> Path | None:
        return self._media_source_path

    def set_context(
        self,
        asset_by_id: dict[int, Asset],
        tag_map: dict[int, list[str]],
        favorite_ids: set[int],
        usage_counts: dict[int, int],
    ) -> None:
        self.asset_by_id = asset_by_id
        self.tag_map = tag_map
        self.favorite_ids = favorite_ids
        self.usage_counts = usage_counts

    def show_asset_ids(self, asset_ids: list[int], *, autoplay: bool = False) -> None:
        assets = [self.asset_by_id[asset_id] for asset_id in asset_ids if asset_id in self.asset_by_id]
        if not assets:
            self._show_empty()
        elif len(assets) > 1:
            self._show_multiple(assets)
        else:
            self._show_single(assets[0], autoplay=autoplay)

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
        try:
            self.audio_output.setMuted(True)
            self.audio_output.setVolume(0.0)
        except RuntimeError:
            pass
        self.play_button.setText("Play")
        self.position_slider.setRange(0, 0)
        self.position_slider.setValue(0)
        self.duration_label.setText("00:00 / 00:00")
        self._hide_volume_popup()
        self._update_volume_state()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _show_empty(self) -> None:
        self.release_media()
        self.video_widget.hide()
        self.controls.hide()
        self.media_label.show()
        self.media_label.setPixmap(QPixmap())
        self.media_label.setText("Select an asset")
        self.media_label.setToolTip("")
        self._set_title_text("Preview")
        self.state_label.setText("")
        self.metadata_label.setText("")

    def _show_multiple(self, assets: list[Asset]) -> None:
        self.release_media()
        self.video_widget.hide()
        self.controls.hide()
        self.media_label.show()
        self.media_label.setPixmap(QPixmap())
        kinds: dict[str, int] = {}
        missing = 0
        favorites = 0
        total_usage = 0
        for asset in assets:
            kind = self._kind(asset)
            kinds[kind] = kinds.get(kind, 0) + 1
            missing += 0 if self._exists(asset) else 1
            favorites += 1 if asset.id in self.favorite_ids else 0
            total_usage += self.usage_counts.get(asset.id or 0, 0)
        kind_text = ", ".join(f"{count} {kind.lower()}" for kind, count in sorted(kinds.items()))
        self._set_title_text(f"{len(assets)} assets selected")
        self.media_label.setText("Multiple selection")
        self.media_label.setToolTip("")
        self.state_label.setText(f"{favorites} favorites  |  {missing} missing  |  {total_usage} project links")
        self.metadata_label.setText(kind_text)

    def _show_single(self, asset: Asset, *, autoplay: bool = False) -> None:
        self._set_title_text(asset.file_path.name or asset.name)
        tags = ", ".join(self.tag_map.get(asset.id or 0, [])) or "None"
        usage = self.usage_counts.get(asset.id or 0, 0)
        states = []
        if asset.id in self.favorite_ids:
            states.append("Favorite")
        if not self._exists(asset):
            states.append("Missing from disk")
        states.append(f"Used in {usage} project{'s' if usage != 1 else ''}")
        self.state_label.setText("  |  ".join(states))
        self.metadata_label.setText(
            "<br>".join(
                [
                    self._metadata_row("Type", self._kind(asset)),
                    self._metadata_row("Category", asset.category or "None"),
                    self._metadata_row("Client", asset.client or "None"),
                    self._metadata_row("Tags", tags),
                    self._metadata_row("Path", str(asset.file_path)),
                ]
            )
        )
        self._render_media(asset, autoplay=autoplay)

    def _metadata_row(self, label: str, value: str) -> str:
        return f"<b>{escape(label)}:</b> {escape(value)}"

    def _set_title_text(self, text: str) -> None:
        self._title_text = text or "Preview"
        self.title_label.setToolTip(self._title_text)
        self._apply_elided_title()

    def _apply_elided_title(self) -> None:
        if not hasattr(self, "title_label"):
            return
        width = max(20, self.title_label.width() or self.width() - 24)
        elided = QFontMetrics(self.title_label.font()).elidedText(
            self._title_text,
            Qt.TextElideMode.ElideRight,
            width,
        )
        self.title_label.setText(elided)

    def _elided_media_text(self, prefix: str, filename: str) -> str:
        available_width = max(40, self.media_label.width() - 24)
        name = QFontMetrics(self.media_label.font()).elidedText(
            filename,
            Qt.TextElideMode.ElideRight,
            available_width,
        )
        return f"{prefix}\n{name}"

    def _render_media(self, asset: Asset, *, autoplay: bool = False) -> None:
        self.release_media()
        self.media_label.setPixmap(QPixmap())
        self.media_label.show()
        self.video_widget.hide()
        self.controls.hide()
        kind = self._kind(asset)
        if not self._exists(asset):
            self.media_label.setText(f"{kind}\nMissing from disk")
            self.media_label.setToolTip(str(asset.file_path))
            return
        if kind == "Folder":
            self.media_label.setPixmap(editflow_pixmap("folder", size=ASSET_PREVIEW_ICON_SIZE))
            self.media_label.setText("")
            self.media_label.setToolTip(asset.file_path.name)
            return
        if kind == "Image":
            pixmap = QPixmap(str(asset.file_path))
            if not pixmap.isNull():
                width = max(220, self.media_label.width() or 260)
                height = max(140, self.media_label.height() or 160)
                self.media_label.setPixmap(pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.media_label.setText("")
                self.media_label.setToolTip(asset.file_path.name)
                return
        if kind in {"Video", "Audio"}:
            self._attach_outputs()
            self._media_source_path = asset.file_path
            self.audio_output.setMuted(False)
            self.audio_output.setVolume(max(0, min(self.volume_slider.value(), 100)) / 100)
            self._update_volume_state()
            self.media_player.setSource(QUrl.fromLocalFile(str(asset.file_path)))
            self.controls.show()
            if kind == "Video":
                self.media_label.hide()
                self.video_widget.show()
            else:
                self.media_label.setPixmap(editflow_pixmap("audio", size=ASSET_PREVIEW_ICON_SIZE))
                self.media_label.setText("")
                self.media_label.setToolTip(asset.file_path.name)
            if autoplay:
                self.media_player.play()
            return
        self.media_label.setText(self._elided_media_text(kind, asset.file_path.name))
        self.media_label.setToolTip(asset.file_path.name)

    def _attach_outputs(self) -> None:
        if self._media_outputs_attached:
            return
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self._media_outputs_attached = True

    def _toggle_volume_popup(self) -> None:
        if self.volume_popup.isVisible():
            self._hide_volume_popup()
            return
        self._position_volume_popup()
        self.volume_popup.show()
        self.volume_popup.raise_()

    def _hide_volume_popup(self) -> None:
        if self.volume_popup.isVisible():
            self.volume_popup.hide()

    def _position_volume_popup(self) -> None:
        media_widget = self.video_widget if self.video_widget.isVisible() else self.media_label
        media_bottom = media_widget.y() + media_widget.height()
        popup_width = self.volume_popup.width()
        popup_height = self.volume_popup.height()
        button_pos = self.volume_button.mapTo(self, self.volume_button.rect().topLeft())
        x = button_pos.x() + self.volume_button.width() // 2 - popup_width // 2
        x = max(6, min(x, max(6, self.width() - popup_width - 6)))
        y = media_bottom - popup_height - 8
        if y < media_widget.y() + 6:
            y = self.controls.y() - popup_height - 6
        y = max(6, min(y, max(6, self.height() - popup_height - 6)))
        self.volume_popup.move(x, y)
        self.volume_popup.raise_()

    def _update_volume_state(self) -> None:
        muted = self.audio_output.isMuted() or self.volume_slider.value() <= 0
        self.volume_button.setIcon(editflow_icon("speaker-muted" if muted else "speaker"))
        self.volume_button.setToolTip("Muted" if muted else "Volume")
        self.volume_state_label.setText("Muted" if muted else f"{self.volume_slider.value()}%")
        self.volume_mute_button.setText("Unmute" if muted else "Mute")

    def _toggle_playback(self) -> None:
        if self._media_source_path is None:
            return
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

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

    def _handle_error(self, _error, message: str) -> None:
        self.state_label.setText(message or "Could not preview this media file.")
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

    def _kind(self, asset: Asset) -> str:
        if asset.is_collection:
            return "Folder"
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
