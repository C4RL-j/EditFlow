from __future__ import annotations

from pathlib import Path
import subprocess

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
)


class VideoPreviewDialog(QDialog):
    replace_requested = Signal(str)
    remove_requested = Signal(str, bool)

    def __init__(
        self,
        video_path: Path,
        *,
        video_kind: str = "video",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.video_kind = video_kind
        self.video_path = video_path
        self._duration = 0
        self._seeking = False

        self.setMinimumSize(720, 430)
        self.resize(820, 500)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(560, 300)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.75)
        self._outputs_attached = False
        self._attach_outputs()

        self.play_button = QPushButton("Pause")
        self.play_button.clicked.connect(self._toggle_playback)

        back_button = QPushButton("-5s")
        back_button.clicked.connect(lambda: self._seek_relative(-5000))
        forward_button = QPushButton("+5s")
        forward_button.clicked.connect(lambda: self._seek_relative(5000))

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self._begin_seek)
        self.position_slider.sliderMoved.connect(self._preview_seek_position)
        self.position_slider.sliderReleased.connect(self._finish_seek)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("MutedLabel")
        self.time_label.setMinimumWidth(96)

        self.mute_button = QPushButton("Mute")
        self.mute_button.clicked.connect(self._toggle_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(75)
        self.volume_slider.setFixedWidth(92)
        self.volume_slider.valueChanged.connect(self._set_volume)

        self.path_label = QLabel("")
        self.path_label.setObjectName("PathLabel")
        self.path_label.setWordWrap(True)

        open_button = QPushButton("Open in Explorer")
        open_button.clicked.connect(self._open_in_explorer)

        replace_button = QPushButton("Replace File")
        replace_button.clicked.connect(lambda: self.replace_requested.emit(self.video_kind))

        remove_button = QPushButton("Remove File")
        remove_button.clicked.connect(self._confirm_remove)

        playback_row = QHBoxLayout()
        playback_row.setContentsMargins(0, 0, 0, 0)
        playback_row.setSpacing(6)
        playback_row.addWidget(self.play_button)
        playback_row.addWidget(back_button)
        playback_row.addWidget(self.position_slider, 1)
        playback_row.addWidget(forward_button)
        playback_row.addWidget(self.time_label)
        playback_row.addWidget(self.mute_button)
        playback_row.addWidget(self.volume_slider)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        action_row.addWidget(open_button)
        action_row.addWidget(replace_button)
        action_row.addWidget(remove_button)
        action_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self.video_widget, 1)
        layout.addLayout(playback_row)
        layout.addWidget(self.path_label)
        layout.addLayout(action_row)

        self.player.durationChanged.connect(self._set_duration)
        self.player.positionChanged.connect(self._set_position)
        self.player.playbackStateChanged.connect(self._update_play_button)
        self.player.errorOccurred.connect(self._handle_error)

        self.set_video_path(video_path, autoplay=True)

    def _attach_outputs(self) -> None:
        if self._outputs_attached:
            return
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self._outputs_attached = True

    def set_video_path(self, video_path: Path, *, autoplay: bool = True) -> None:
        self.release_media()
        self._attach_outputs()
        self.video_path = video_path
        label = "Raw" if self.video_kind == "raw" else "Edited"
        self.setWindowTitle(f"{label} Preview - {video_path.name}")
        self.path_label.setText(str(video_path))
        self.position_slider.setValue(0)
        self.position_slider.setRange(0, 0)
        self.time_label.setText("00:00 / 00:00")
        self.audio_output.setMuted(False)
        self.audio_output.setVolume(max(0, min(self.volume_slider.value(), 100)) / 100)
        self.player.setSource(QUrl.fromLocalFile(str(video_path)))
        if autoplay:
            self.player.play()

    def release_media(self) -> None:
        if not hasattr(self, "player"):
            return
        for action in (
            self.player.stop,
            lambda: self.player.setSource(QUrl()),
            lambda: self.player.setVideoOutput(None),
            lambda: self.player.setAudioOutput(None),
        ):
            try:
                action()
            except (RuntimeError, TypeError):
                pass
        self._outputs_attached = False
        if hasattr(self, "audio_output"):
            for action in (
                lambda: self.audio_output.setMuted(True),
                lambda: self.audio_output.setVolume(0.0),
            ):
                try:
                    action()
                except RuntimeError:
                    pass
        self._duration = 0
        self._seeking = False
        self.play_button.setText("Play")
        self.position_slider.setRange(0, 0)
        self.position_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def done(self, result: int) -> None:
        self.release_media()
        super().done(result)

    def closeEvent(self, event) -> None:
        self.release_media()
        super().closeEvent(event)

    def _toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _seek_relative(self, offset_ms: int) -> None:
        target = max(0, min(self.player.position() + offset_ms, self._duration))
        self.player.setPosition(target)

    def _begin_seek(self) -> None:
        self._seeking = True

    def _preview_seek_position(self, position: int) -> None:
        self._update_time_label(position)

    def _finish_seek(self) -> None:
        self._seeking = False
        self.player.setPosition(self.position_slider.value())

    def _set_duration(self, duration: int) -> None:
        self._duration = max(0, duration)
        self.position_slider.setRange(0, self._duration)
        self._update_time_label(self.player.position())

    def _set_position(self, position: int) -> None:
        if not self._seeking:
            self.position_slider.setValue(position)
        self._update_time_label(position)

    def _update_time_label(self, position: int) -> None:
        self.time_label.setText(
            f"{self._format_millis(position)} / {self._format_millis(self._duration)}"
        )

    def _update_play_button(self, state) -> None:
        self.play_button.setText(
            "Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play"
        )

    def _toggle_mute(self) -> None:
        muted = not self.audio_output.isMuted()
        self.audio_output.setMuted(muted)
        self.mute_button.setText("Unmute" if muted else "Mute")

    def _set_volume(self, value: int) -> None:
        self.audio_output.setVolume(max(0, min(value, 100)) / 100)
        if value > 0 and self.audio_output.isMuted():
            self.audio_output.setMuted(False)
            self.mute_button.setText("Mute")

    def _open_in_explorer(self) -> None:
        if self.video_path.exists():
            try:
                subprocess.Popen(["explorer", "/select,", str(self.video_path)])
                return
            except OSError:
                pass
        target = self.video_path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _confirm_remove(self) -> None:
        self.player.pause()
        box = QMessageBox(self)
        box.setWindowTitle("Remove File")
        box.setText(f"Remove this {self.video_kind} video from the project?")
        box.setInformativeText(
            "Unlink only keeps the physical file. Permanent delete removes it from disk."
        )
        unlink_button = box.addButton("Unlink Only", QMessageBox.ButtonRole.AcceptRole)
        delete_button = box.addButton(
            "Delete File Permanently",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(unlink_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked == cancel_button:
            return
        delete_file = clicked == delete_button
        if delete_file:
            answer = QMessageBox.warning(
                self,
                "Permanently Delete File?",
                f"This will delete the file from disk:\n{self.video_path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.remove_requested.emit(self.video_kind, delete_file)

    def _handle_error(self, _error, message: str) -> None:
        self.play_button.setText("Play")
        self.path_label.setText(message or f"Could not preview {self.video_path.name}")

    def _format_millis(self, value: int) -> str:
        total_seconds = max(0, int(value / 1000))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
