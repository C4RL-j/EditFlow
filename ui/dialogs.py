from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QHeaderView,
    QScrollArea,
    QSlider,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from core.file_manager import normalize_file_path
from models.project import Asset, Project, ProjectRevision


PICKER_ASSET_ID_ROLE = Qt.ItemDataRole.UserRole
PICKER_ASSET_PATH_ROLE = Qt.ItemDataRole.UserRole + 1
PICKER_ASSET_TYPE_ROLE = Qt.ItemDataRole.UserRole + 2

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"}
ASSET_FILTERS = ("All", "Images", "Video", "Audio", "Folders")
SOURCE_FILTERS = ("All Assets", "Favorites", "Recent")


class AddRawVideoDialog(QDialog):
    def __init__(
        self,
        raw_video_path: Path,
        parent=None,
        suggested_name: str = "",
        client_options: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.raw_video_path = raw_video_path
        self.setWindowTitle("Add Raw Video")
        self.setMinimumWidth(460)

        file_label = QLabel(raw_video_path.name)
        file_label.setObjectName("PathLabel")
        file_label.setWordWrap(True)

        self.name_edit = QLineEdit(suggested_name or raw_video_path.stem)
        self.client_combo = QComboBox()
        self.client_combo.setEditable(True)
        self.client_combo.addItems(client_options or [])
        self.client_combo.setCurrentIndex(-1)
        if self.client_combo.lineEdit() is not None:
            self.client_combo.lineEdit().setPlaceholderText("Choose or type client")

        self.video_type_combo = QComboBox()
        self.video_type_combo.setEditable(True)
        self.video_type_combo.addItems(["UGC", "Personal Brand"])
        self.video_type_combo.setCurrentIndex(-1)
        if self.video_type_combo.lineEdit() is not None:
            self.video_type_combo.lineEdit().setPlaceholderText("UGC, Personal Brand, etc.")

        form = QFormLayout()
        form.addRow("Raw MP4", file_label)
        form.addRow("Project/video name", self.name_edit)
        form.addRow("Client", self.client_combo)
        form.addRow("Video type", self.video_type_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {
            "name": self.name_edit.text().strip(),
            "client": self.client_combo.currentText().strip(),
            "video_type": self.video_type_combo.currentText().strip(),
        }

    def accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing Name", "Enter a project/video name.")
            return
        if not self.video_type_combo.currentText().strip():
            QMessageBox.warning(self, "Missing Video Type", "Enter the video type.")
            return
        super().accept()


class AssetDialog(QDialog):
    def __init__(self, file_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Asset")
        self.setMinimumWidth(520)

        self.file_edit = QLineEdit(str(file_path or ""))
        self.file_edit.setPlaceholderText("Choose an asset file")
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self._browse_file)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(browse_button)

        self.name_edit = QLineEdit(file_path.stem if file_path else "")
        self.name_edit.setPlaceholderText("Asset name")

        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText("Optional client")

        self.category_edit = QLineEdit()
        self.category_edit.setPlaceholderText("Image, music, logo, reference, etc.")

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("logo, anxiety, intro, music")

        form = QFormLayout()
        form.addRow("File", file_row)
        form.addRow("Name", self.name_edit)
        form.addRow("Client", self.client_edit)
        form.addRow("Category", self.category_edit)
        form.addRow("Tags", self.tags_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        file_path = Path(self.file_edit.text().strip())
        return {
            "file_path": file_path,
            "name": self.name_edit.text().strip() or file_path.stem,
            "client": self.client_edit.text().strip(),
            "category": self.category_edit.text().strip(),
            "tags": self.tags_edit.text().strip(),
        }

    def accept(self) -> None:
        file_text = self.file_edit.text().strip()
        if not file_text:
            QMessageBox.warning(self, "Missing File", "Choose an asset file.")
            return
        if not Path(file_text).exists():
            QMessageBox.warning(self, "File Not Found", "Choose an existing asset file.")
            return
        if not self.name_edit.text().strip():
            self.name_edit.setText(Path(file_text).stem)
        super().accept()

    def _browse_file(self) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose Asset",
            "",
            "Assets (*.*)",
        )
        if file_name:
            path = Path(file_name)
            self.file_edit.setText(str(path))
            if not self.name_edit.text().strip():
                self.name_edit.setText(path.stem)



class NotesDialog(QDialog):
    revision_completion_changed = Signal(object, bool)

    def __init__(
        self,
        project: Project,
        revisions: list[ProjectRevision],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.revisions = revisions
        self._revision_rows: dict[int, tuple[QFrame, QLabel, QCheckBox]] = {}
        self.setWindowTitle(f"Notes - {project.name}")
        self.setMinimumWidth(560)
        self.resize(620, 560)

        title = QLabel(project.name)
        title.setObjectName("SectionTitle")
        client = QLabel(project.client or "No client")
        client.setObjectName("MutedLabel")

        revisions_title = QLabel("Revision Notes")
        revisions_title.setObjectName("SectionTitle")
        revisions_hint = QLabel("Incomplete revision notes block forward workflow movement.")
        revisions_hint.setObjectName("MutedLabel")
        revisions_hint.setWordWrap(True)

        revisions_body = QWidget()
        revisions_layout = QVBoxLayout(revisions_body)
        revisions_layout.setContentsMargins(0, 0, 0, 0)
        revisions_layout.setSpacing(8)

        if not revisions:
            empty = QLabel("No revision notes yet.")
            empty.setObjectName("MutedLabel")
            revisions_layout.addWidget(empty)
        else:
            for revision in revisions:
                row = self._build_revision_row(revision)
                revisions_layout.addWidget(row)
        revisions_layout.addStretch(1)

        revision_scroll = QScrollArea()
        revision_scroll.setWidgetResizable(True)
        revision_scroll.setFrameShape(QFrame.Shape.NoFrame)
        revision_scroll.setMinimumHeight(160)
        revision_scroll.setMaximumHeight(240)
        revision_scroll.setWidget(revisions_body)

        normal_title = QLabel("Notes")
        normal_title.setObjectName("SectionTitle")
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Project notes")
        self.notes_edit.setPlainText(project.notes)
        self.notes_edit.setMinimumHeight(150)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(client)
        layout.addSpacing(4)
        layout.addWidget(revisions_title)
        layout.addWidget(revisions_hint)
        layout.addWidget(revision_scroll)
        layout.addSpacing(8)
        layout.addWidget(normal_title)
        layout.addWidget(self.notes_edit, 1)
        layout.addWidget(buttons)

    def notes(self) -> str:
        return self.notes_edit.toPlainText()

    def _build_revision_row(self, revision: ProjectRevision) -> QFrame:
        row = QFrame()
        row.setObjectName("RevisionNoteRow")
        row.setProperty("completed", revision.completed)

        checkbox = QCheckBox()
        checkbox.setChecked(revision.completed)
        checkbox.setToolTip("Mark revision complete")

        note = QLabel(revision.note)
        note.setWordWrap(True)
        note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        timestamp = revision.created_at[:16] if revision.created_at else ""
        meta_text = "Completed" if revision.completed else "Needs revision"
        if timestamp:
            meta_text = f"{meta_text} • {timestamp}"
        meta = QLabel(meta_text)
        meta.setObjectName("MutedLabel")

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(note)
        text_layout.addWidget(meta)

        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(10)
        row_layout.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignTop)
        row_layout.addLayout(text_layout, 1)

        if revision.id is not None:
            self._revision_rows[revision.id] = (row, meta, checkbox)
        self._style_revision_row(row, meta, revision.completed)
        checkbox.toggled.connect(
            lambda checked, item=revision: self._revision_toggled(item, checked)
        )
        return row

    def _revision_toggled(self, revision: ProjectRevision, completed: bool) -> None:
        revision.completed = completed
        if revision.id is not None:
            row, meta, _checkbox = self._revision_rows[revision.id]
            self._style_revision_row(row, meta, completed)
        self.revision_completion_changed.emit(revision, completed)

    def _style_revision_row(self, row: QFrame, meta: QLabel, completed: bool) -> None:
        row.setProperty("completed", completed)
        row.style().unpolish(row)
        row.style().polish(row)
        if completed:
            meta.setText("Completed")
        elif not meta.text().startswith("Needs revision"):
            meta.setText("Needs revision")

class AssetPickerDialog(QDialog):
    def __init__(
        self,
        project_service,
        parent=None,
        project: Project | None = None,
        project_client: str = "",
    ) -> None:
        super().__init__(parent)
        self.project_service = project_service
        self.project = project
        self.project_client = project_client.strip()
        self.asset_root = self.project_service.asset_library_root()
        self.assets: list[Asset] = []
        self.asset_by_id: dict[int, Asset] = {}
        self.asset_by_path: dict[str, Asset] = {}
        self._pending_selected_ids: set[int] = set()
        self.linked_asset_ids: set[int] = set()
        self.favorite_asset_ids: set[int] = set()
        self.usage_counts: dict[int, int] = {}
        self._restoring_selection = False
        self._restoring_layout = False
        self._media_source_path: Path | None = None

        self.setWindowTitle("Link Assets")
        self.setMinimumSize(1040, 620)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search assets, folders, clients, tags...")
        self.search_edit.textChanged.connect(self._refresh_assets)

        self.client_filter_combo = QComboBox()
        self.client_filter_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_assets()
        )

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.addWidget(self.search_edit, 1)
        filter_row.addWidget(QLabel("Client"))
        filter_row.addWidget(self.client_filter_combo)

        self.type_filter_group = QButtonGroup(self)
        self.type_filter_group.setExclusive(True)
        type_row = QHBoxLayout()
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(6)
        for filter_name in ASSET_FILTERS:
            button = QPushButton(filter_name)
            button.setObjectName("PickerFilterButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=filter_name: self._set_type_filter(value)
            )
            self.type_filter_group.addButton(button)
            type_row.addWidget(button)
        type_row.addStretch(1)

        saved_filter = self.project_service.get_setting("asset_picker_type_filter", "All")
        self._set_checked_type_filter(saved_filter if saved_filter in ASSET_FILTERS else "All")

        last_folder_value = self.project_service.get_setting("asset_picker_last_folder", "")
        self._last_folder_path = Path(last_folder_value) if last_folder_value else None

        self.source_filter_group = QButtonGroup(self)
        self.source_filter_group.setExclusive(True)
        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        for filter_name in SOURCE_FILTERS:
            button = QPushButton(filter_name)
            button.setObjectName("PickerFilterButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=filter_name: self._set_source_filter(value)
            )
            self.source_filter_group.addButton(button)
            source_row.addWidget(button)
        source_row.addStretch(1)

        saved_source = self.project_service.get_setting(
            "asset_picker_source_filter",
            "All Assets",
        )
        self._set_checked_source_filter(
            saved_source if saved_source in SOURCE_FILTERS else "All Assets"
        )

        self.asset_tree = QTreeWidget()
        self.asset_tree.setHeaderLabels(["Name", "State", "Type", "Category", "ID"])
        self.asset_tree.setColumnHidden(4, True)
        self.asset_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.asset_tree.itemSelectionChanged.connect(self._update_selection_ui)
        self.asset_tree.currentItemChanged.connect(
            lambda current, _previous: self._remember_current_folder(current)
        )
        self.asset_tree.itemDoubleClicked.connect(lambda _item, _column: self.accept())
        self.asset_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.asset_tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        header = self.asset_tree.header()
        header.setStretchLastSection(False)
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.sectionResized.connect(
            lambda _logical, _old_size, _new_size: self._save_column_widths()
        )
        self._restore_column_widths()

        library_panel = QVBoxLayout()
        library_panel.setContentsMargins(0, 0, 0, 0)
        library_panel.setSpacing(8)
        library_title = QLabel("Asset Library")
        library_title.setObjectName("SectionTitle")
        self.preview_toggle_button = QPushButton("Hide Preview")
        self.preview_toggle_button.clicked.connect(self._toggle_preview_panel)

        library_title_row = QHBoxLayout()
        library_title_row.setContentsMargins(0, 0, 0, 0)
        library_title_row.addWidget(library_title)
        library_title_row.addStretch(1)
        library_title_row.addWidget(self.preview_toggle_button)

        library_panel.addLayout(library_title_row)
        library_panel.addLayout(source_row)
        library_panel.addWidget(self.asset_tree)
        library_widget = QWidget()
        library_widget.setLayout(library_panel)

        self.preview_panel = self._build_preview_panel()

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.addWidget(library_widget)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(0, 5)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(
            lambda _position, _index: self._save_splitter_sizes()
        )
        self._restore_splitter_state()

        import_button = QPushButton("Import New Asset")
        import_button.clicked.connect(self._import_new_assets)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.link_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.link_button.setText("Link Selected")
        buttons.addButton(import_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(filter_row)
        layout.addLayout(type_row)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(buttons)

        self._load_assets()
        self._load_project_state()
        self._populate_client_filter()
        self._refresh_assets()
        self._update_selection_ui()

    def selected_asset_ids(self) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        for item in self.asset_tree.selectedItems():
            value = item.data(0, PICKER_ASSET_ID_ROLE)
            if isinstance(value, int) and value not in seen:
                ids.append(value)
                seen.add(value)
        return ids

    def accept(self) -> None:
        selected_ids = self.selected_asset_ids()
        if not selected_ids:
            QMessageBox.information(
                self,
                "Select Assets",
                "Choose one or more assets or folders to link.",
            )
            return
        missing = [
            self.asset_by_id[asset_id]
            for asset_id in selected_ids
            if asset_id in self.asset_by_id and not self._asset_exists(self.asset_by_id[asset_id])
        ]
        if missing:
            sample = "\n".join(asset.name for asset in missing[:5])
            if len(missing) > 5:
                sample = f"{sample}\n...and {len(missing) - 5} more"
            answer = QMessageBox.warning(
                self,
                "Missing Asset Files",
                (
                    f"{len(missing)} selected asset(s) are missing from disk.\n\n"
                    f"{sample}\n\nLink their records anyway?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().accept()

    def done(self, result: int) -> None:
        self._stop_media_preview()
        self._save_column_widths()
        self._save_splitter_sizes()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._stop_media_preview()
        super().closeEvent(event)

    def _restore_column_widths(self) -> None:
        saved = self.project_service.get_setting("asset_picker_column_widths", "")
        widths: list[int] = []
        for value in saved.split(","):
            try:
                widths.append(int(value.strip()))
            except ValueError:
                continue
        if len(widths) != 4:
            widths = [360, 120, 90, 150]
        self._restoring_layout = True
        try:
            for column, width in enumerate(widths[:4]):
                self.asset_tree.setColumnWidth(column, max(48, width))
        finally:
            self._restoring_layout = False

    def _save_column_widths(self) -> None:
        if self._restoring_layout:
            return
        widths = [str(self.asset_tree.columnWidth(column)) for column in range(4)]
        self.project_service.set_setting("asset_picker_column_widths", ",".join(widths))

    def _restore_splitter_state(self) -> None:
        visible = self.project_service.get_setting("asset_picker_preview_visible", "1") != "0"
        self.preview_panel.setVisible(visible)
        self.preview_toggle_button.setText("Hide Preview" if visible else "Show Preview")
        saved = self.project_service.get_setting("asset_picker_splitter_sizes", "")
        sizes: list[int] = []
        for value in saved.split(","):
            try:
                sizes.append(int(value.strip()))
            except ValueError:
                continue
        if len(sizes) != 2:
            sizes = [780, 240] if visible else [1020, 0]
        self._restoring_layout = True
        try:
            self.splitter.setSizes(sizes)
        finally:
            self._restoring_layout = False

    def _save_splitter_sizes(self) -> None:
        if self._restoring_layout:
            return
        self.project_service.set_setting(
            "asset_picker_splitter_sizes",
            ",".join(str(size) for size in self.splitter.sizes()),
        )
        self.project_service.set_setting(
            "asset_picker_preview_visible",
            "1" if not self.preview_panel.isHidden() else "0",
        )

    def _toggle_preview_panel(self) -> None:
        visible = self.preview_panel.isHidden()
        self.preview_panel.setVisible(visible)
        self.preview_toggle_button.setText("Hide Preview" if visible else "Show Preview")
        if visible and (not self.splitter.sizes() or self.splitter.sizes()[-1] < 80):
            self.splitter.setSizes([780, 240])
        self._save_splitter_sizes()

    def _build_preview_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("AssetPreviewPanel")
        panel.setMinimumWidth(0)

        self.preview_title = QLabel("Preview")
        self.preview_title.setObjectName("SectionTitle")
        self.preview_title.setWordWrap(True)

        self.preview_media = QLabel("Select an asset")
        self.preview_media.setObjectName("AssetPreviewMedia")
        self.preview_media.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_media.setMinimumHeight(150)
        self.preview_media.setWordWrap(True)

        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("AssetVideoPreview")
        self.video_widget.setMinimumHeight(150)
        self.video_widget.hide()

        self.media_player = QMediaPlayer(panel)
        self.media_audio_output = QAudioOutput(panel)
        self.media_audio_output.setVolume(0.65)
        self._media_outputs_attached = False
        self._attach_media_outputs()
        self.media_player.durationChanged.connect(self._set_media_duration)
        self.media_player.positionChanged.connect(self._set_media_position)
        self.media_player.playbackStateChanged.connect(self._update_media_play_button)
        self.media_player.errorOccurred.connect(self._handle_media_error)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._toggle_media_playback)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self._seek_media)

        self.duration_label = QLabel("00:00 / 00:00")
        self.duration_label.setObjectName("MutedLabel")

        self.mute_button = QPushButton("Mute")
        self.mute_button.clicked.connect(self._toggle_media_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(65)
        self.volume_slider.setFixedWidth(72)
        self.volume_slider.valueChanged.connect(self._set_media_volume)

        self.media_controls = QFrame()
        self.media_controls.setObjectName("AssetMediaControls")
        media_controls_layout = QHBoxLayout(self.media_controls)
        media_controls_layout.setContentsMargins(0, 0, 0, 0)
        media_controls_layout.setSpacing(6)
        media_controls_layout.addWidget(self.play_button)
        media_controls_layout.addWidget(self.position_slider, 1)
        media_controls_layout.addWidget(self.duration_label)
        media_controls_layout.addWidget(self.mute_button)
        media_controls_layout.addWidget(self.volume_slider)
        self.media_controls.hide()

        self.preview_state = QLabel("")
        self.preview_state.setObjectName("MutedLabel")
        self.preview_state.setWordWrap(True)

        self.preview_summary = QLabel("")
        self.preview_summary.setObjectName("MutedLabel")
        self.preview_summary.setWordWrap(True)

        self.client_badge = QLabel("")
        self.client_badge.setObjectName("AssetBadge")
        self.category_badge = QLabel("")
        self.category_badge.setObjectName("AssetBadge")

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(6)
        badge_row.addWidget(self.client_badge)
        badge_row.addWidget(self.category_badge)
        badge_row.addStretch(1)

        self.favorite_button = QPushButton("☆ Favorite")
        self.favorite_button.clicked.connect(self._toggle_selected_favorite)
        self.open_preview_button = QPushButton("📁 Open in Explorer")
        self.open_preview_button.clicked.connect(self._open_selected_in_explorer)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addWidget(self.favorite_button)
        action_row.addWidget(self.open_preview_button)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self.preview_title)
        layout.addWidget(self.preview_media)
        layout.addWidget(self.video_widget)
        layout.addWidget(self.media_controls)
        layout.addLayout(badge_row)
        layout.addWidget(self.preview_state)
        layout.addWidget(self.preview_summary)
        layout.addStretch(1)
        layout.addLayout(action_row)
        return panel

    def _load_assets(self) -> None:
        self.project_service.sync_asset_library()
        self.asset_root = self.project_service.asset_library_root()
        self.assets = self.project_service.list_assets("")
        asset_ids = [asset.id for asset in self.assets if asset.id is not None]
        self.asset_by_id = {
            asset.id: asset
            for asset in self.assets
            if asset.id is not None
        }
        self.asset_by_path = {
            normalize_file_path(asset.file_path): asset
            for asset in self.assets
            if asset.id is not None
        }
        self.favorite_asset_ids = self.project_service.favorite_asset_ids()
        self.usage_counts = self.project_service.asset_usage_counts(asset_ids)

    def _load_project_state(self) -> None:
        if self.project is None or self.project.id is None:
            self.linked_asset_ids = set()
            return
        self.linked_asset_ids = {
            asset.id
            for asset in self.project_service.get_project_assets(self.project.id)
            if asset.id is not None
        }

    def _populate_client_filter(self) -> None:
        current = self.client_filter_combo.currentData()
        clients = sorted(
            {asset.client for asset in self.assets if asset.client.strip()},
            key=str.casefold,
        )
        self.client_filter_combo.blockSignals(True)
        self.client_filter_combo.clear()
        self.client_filter_combo.addItem("All clients", "")
        for client in clients:
            self.client_filter_combo.addItem(client, client)
        preferred = self.project_client if self.project_client in clients else current
        index = self.client_filter_combo.findData(preferred)
        self.client_filter_combo.setCurrentIndex(index if index >= 0 else 0)
        self.client_filter_combo.blockSignals(False)

    def _refresh_assets(self) -> None:
        search = self.search_edit.text().strip()
        client_filter = str(self.client_filter_combo.currentData() or "").strip()
        type_filter = self._current_type_filter()
        source_filter = self._current_source_filter()
        searched_assets = self.project_service.list_assets(search) if search else self.assets
        searched_ids = {
            asset.id
            for asset in searched_assets
            if asset.id is not None
        }
        source_ids: set[int] | None = None
        if source_filter == "Favorites":
            source_ids = set(self.favorite_asset_ids)
        elif source_filter == "Recent":
            source_ids = {
                asset.id
                for asset in self.project_service.get_recent_assets(limit=200)
                if asset.id is not None
            }

        visible_assets = [
            asset
            for asset in self.assets
            if (
                asset.id in searched_ids
                and (source_ids is None or asset.id in source_ids)
                and self._matches_client(asset, client_filter)
                and self._matches_type(asset, type_filter)
            )
        ]
        visible_ids = {
            asset.id
            for asset in visible_assets
            if asset.id is not None
        }
        self._include_visible_ancestors(visible_assets, visible_ids)
        self._populate_asset_tree(visible_ids)
        self._update_selection_ui()

    def _matches_client(self, asset: Asset, client_filter: str) -> bool:
        if not client_filter:
            return True
        return asset.client.casefold() == client_filter.casefold()

    def _matches_type(self, asset: Asset, type_filter: str) -> bool:
        if type_filter == "All":
            return True
        kind = self._asset_kind(asset)
        if type_filter == "Images":
            return kind == "Image"
        if type_filter == "Video":
            return kind == "Video"
        if type_filter == "Audio":
            return kind == "Audio"
        if type_filter == "Folders":
            return kind == "Folder"
        return True

    def _current_type_filter(self) -> str:
        checked = self.type_filter_group.checkedButton()
        return checked.text() if checked is not None else "All"

    def _set_type_filter(self, filter_name: str) -> None:
        self.project_service.set_setting("asset_picker_type_filter", filter_name)
        self._refresh_assets()

    def _set_checked_type_filter(self, filter_name: str) -> None:
        for button in self.type_filter_group.buttons():
            button.setChecked(button.text() == filter_name)

    def _current_source_filter(self) -> str:
        checked = self.source_filter_group.checkedButton()
        return checked.text() if checked is not None else "All Assets"

    def _set_source_filter(self, filter_name: str) -> None:
        self.project_service.set_setting("asset_picker_source_filter", filter_name)
        self._refresh_assets()

    def _set_checked_source_filter(self, filter_name: str) -> None:
        for button in self.source_filter_group.buttons():
            button.setChecked(button.text() == filter_name)

    def _include_visible_ancestors(
        self,
        visible_assets: list[Asset],
        visible_ids: set[int],
    ) -> None:
        for asset in visible_assets:
            parent = asset.file_path.parent
            while parent != self.asset_root and parent != parent.parent:
                parent_asset = self.asset_by_path.get(normalize_file_path(parent))
                if parent_asset is not None and parent_asset.id is not None:
                    visible_ids.add(parent_asset.id)
                parent = parent.parent

    def _populate_asset_tree(self, visible_ids: set[int]) -> None:
        self.asset_tree.clear()
        items_by_path: dict[str, QTreeWidgetItem] = {}
        ordered_assets = sorted(
            (
                asset
                for asset in self.assets
                if asset.id is not None and asset.id in visible_ids
            ),
            key=lambda asset: (
                len(asset.file_path.parts),
                not asset.is_collection,
                str(asset.file_path).casefold(),
            ),
        )

        for asset in ordered_assets:
            linked_text = "✓ Linked" if asset.id in self.linked_asset_ids else ""
            missing_text = "Missing" if not self._asset_exists(asset) else ""
            state_text = "  ".join(value for value in [linked_text, missing_text] if value)
            category_text = asset.category or "-"
            values = [
                self._asset_display_name(asset),
                state_text,
                self._asset_kind(asset),
                category_text,
                str(asset.id or ""),
            ]
            item = QTreeWidgetItem(values)
            item.setData(0, PICKER_ASSET_ID_ROLE, asset.id)
            item.setData(0, PICKER_ASSET_PATH_ROLE, str(asset.file_path))
            item.setData(0, PICKER_ASSET_TYPE_ROLE, asset.asset_type)
            if asset.id in self.linked_asset_ids:
                item.setForeground(1, QBrush(QColor("#35d07f")))
            if not self._asset_exists(asset):
                item.setForeground(0, QBrush(QColor("#f05252")))
                item.setForeground(1, QBrush(QColor("#f05252")))
            if asset.id in self._pending_selected_ids:
                item.setSelected(True)

            parent_item = self._nearest_parent_item(asset.file_path, items_by_path)
            if parent_item is None:
                self.asset_tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            items_by_path[normalize_file_path(asset.file_path)] = item

        self.asset_tree.expandToDepth(1)
        self._restore_last_folder_item(items_by_path)

    def _nearest_parent_item(
        self,
        path: Path,
        items_by_path: dict[str, QTreeWidgetItem],
    ) -> QTreeWidgetItem | None:
        parent = path.parent
        while parent != self.asset_root and parent != parent.parent:
            item = items_by_path.get(normalize_file_path(parent))
            if item is not None:
                return item
            parent = parent.parent
        return None

    def _relative_asset_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.asset_root))
        except ValueError:
            return str(path)

    def _restore_last_folder_item(
        self,
        items_by_path: dict[str, QTreeWidgetItem],
    ) -> None:
        if self._last_folder_path is None or self._pending_selected_ids:
            return
        item = items_by_path.get(normalize_file_path(self._last_folder_path))
        if item is not None:
            self.asset_tree.setCurrentItem(
                item,
                0,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
            self.asset_tree.scrollToItem(item)

    def _current_folder_path(self) -> Path | None:
        item = self.asset_tree.currentItem()
        if item is None:
            return self.asset_root
        path_value = item.data(0, PICKER_ASSET_PATH_ROLE)
        type_value = item.data(0, PICKER_ASSET_TYPE_ROLE)
        if not isinstance(path_value, str):
            return self.asset_root
        path = Path(path_value)
        return path if type_value == "folder" else path.parent

    def _remember_current_folder(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        path_value = item.data(0, PICKER_ASSET_PATH_ROLE)
        type_value = item.data(0, PICKER_ASSET_TYPE_ROLE)
        if not isinstance(path_value, str):
            return
        path = Path(path_value)
        folder = path if type_value == "folder" else path.parent
        self._last_folder_path = folder
        self.project_service.set_setting("asset_picker_last_folder", str(folder))

    def _import_new_assets(self) -> None:
        file_names, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Import New Asset Files",
            "",
            "Assets (*.*)",
        )
        if not file_names:
            return
        try:
            imported = self.project_service.import_asset_files(
                [Path(file_name) for file_name in file_names],
                self._current_folder_path(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Import Assets", str(error))
            return
        self._pending_selected_ids.update(
            asset.id for asset in imported if asset.id is not None
        )
        self._load_assets()
        self._populate_client_filter()
        self.client_filter_combo.setCurrentIndex(0)
        self._refresh_assets()

    def _select_tree_ids(self, asset_ids: set[int]) -> None:
        iterator = QTreeWidgetItemIterator(self.asset_tree)
        while iterator.value() is not None:
            item = iterator.value()
            value = item.data(0, PICKER_ASSET_ID_ROLE)
            item.setSelected(isinstance(value, int) and value in asset_ids)
            iterator += 1

    def _update_selection_ui(self) -> None:
        if self._restoring_selection:
            return
        selected_ids = self.selected_asset_ids()
        self._pending_selected_ids = set(selected_ids)
        count = len(selected_ids)
        if count == 0:
            self.link_button.setText("Link Selected")
        elif count == 1:
            self.link_button.setText("Link 1 Asset")
        else:
            self.link_button.setText(f"Link {count} Assets")
        self._update_preview(selected_ids)

    def _update_preview(self, selected_ids: list[int]) -> None:
        assets = [
            self.asset_by_id[asset_id]
            for asset_id in selected_ids
            if asset_id in self.asset_by_id
        ]
        if not assets:
            self._stop_media_preview()
            self.video_widget.hide()
            self.media_controls.hide()
            self.preview_media.show()
            self.preview_title.setText("Preview")
            self.preview_media.setPixmap(QPixmap())
            self.preview_media.setText("Select an asset")
            self.preview_state.setText("")
            self.preview_summary.setText("")
            self.client_badge.setText("")
            self.category_badge.setText("")
            self.favorite_button.setEnabled(False)
            self.open_preview_button.setEnabled(False)
            return

        self.favorite_button.setEnabled(True)
        self.open_preview_button.setEnabled(True)
        if len(assets) > 1:
            self._show_multi_preview(assets)
            return

        asset = assets[0]
        self.preview_title.setText(asset.name)
        self.client_badge.setText(f"Client: {asset.client or 'None'}")
        self.category_badge.setText(f"Category: {asset.category or 'None'}")
        states = []
        if asset.id in self.linked_asset_ids:
            states.append("✓ Linked to this project")
        if asset.id in self.favorite_asset_ids:
            states.append("Favorite")
        if not self._asset_exists(asset):
            states.append("Missing from disk")
        usage = self.usage_counts.get(asset.id or 0, 0)
        states.append(self._usage_text(asset) if usage else "Not linked to any projects yet")
        self.preview_state.setText("  |  ".join(states))
        self.preview_summary.setText(
            "\n".join(
                [
                    f"Type: {self._asset_kind(asset)}",
                    f"Path: {asset.file_path}",
                ]
            )
        )
        self.favorite_button.setText(
            "Remove Favorite" if asset.id in self.favorite_asset_ids else "Favorite"
        )
        self._render_preview_media(asset)

    def _show_multi_preview(self, assets: list[Asset]) -> None:
        self._stop_media_preview()
        self.video_widget.hide()
        self.media_controls.hide()
        self.preview_media.show()
        kinds: dict[str, int] = {}
        missing = 0
        linked = 0
        favorites = 0
        total_usage = 0
        for asset in assets:
            kinds[self._asset_kind(asset)] = kinds.get(self._asset_kind(asset), 0) + 1
            if not self._asset_exists(asset):
                missing += 1
            if asset.id in self.linked_asset_ids:
                linked += 1
            if asset.id in self.favorite_asset_ids:
                favorites += 1
            total_usage += self.usage_counts.get(asset.id or 0, 0)
        kind_text = ", ".join(f"{count} {kind.lower()}" for kind, count in sorted(kinds.items()))
        self.preview_title.setText(f"{len(assets)} assets selected")
        self.preview_media.setPixmap(QPixmap())
        self.preview_media.setText("Multiple selection")
        self.client_badge.setText("")
        self.category_badge.setText("")
        self.preview_state.setText(
            f"{linked} already linked  |  {favorites} favorites  |  {missing} missing"
        )
        self.preview_summary.setText(
            f"{kind_text}\nUsed in {total_usage} total project link(s)."
        )
        self.favorite_button.setText("Favorite")

    def _attach_media_outputs(self) -> None:
        if getattr(self, "_media_outputs_attached", False):
            return
        self.media_player.setAudioOutput(self.media_audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self._media_outputs_attached = True

    def _render_preview_media(self, asset: Asset) -> None:
        self._stop_media_preview()
        self.preview_media.setPixmap(QPixmap())
        self.preview_media.show()
        self.video_widget.hide()
        self.media_controls.hide()
        kind = self._asset_kind(asset)
        if not self._asset_exists(asset):
            self.preview_media.setText(f"{kind}\nMissing from disk")
            return
        if kind == "Image" and asset.file_path.exists():
            pixmap = QPixmap(str(asset.file_path))
            if not pixmap.isNull():
                width = max(220, self.preview_media.width() or 260)
                height = max(140, self.preview_media.height() or 160)
                self.preview_media.setPixmap(
                    pixmap.scaled(
                        width,
                        height,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.preview_media.setText("")
                return
        if kind in {"Video", "Audio"} and asset.file_path.exists():
            self._attach_media_outputs()
            self._media_source_path = asset.file_path
            self.media_audio_output.setMuted(False)
            self.media_audio_output.setVolume(max(0, min(self.volume_slider.value(), 100)) / 100)
            self.media_player.setSource(QUrl.fromLocalFile(str(asset.file_path)))
            self.position_slider.setRange(0, 0)
            self.position_slider.setValue(0)
            self.duration_label.setText("00:00 / 00:00")
            self.play_button.setText("Play")
            self.media_controls.show()
            if kind == "Video":
                self.preview_media.hide()
                self.video_widget.show()
            else:
                self.video_widget.hide()
                self.preview_media.setText(f"Audio\n{asset.file_path.name}")
            return
        icon = {
            "Folder": "[Folder]",
            "Video": "[Video]",
            "Audio": "[Audio]",
            "Image": "[Image]",
        }.get(kind, "[Asset]")
        self.preview_media.setText(f"{icon}\n{kind}")

    def _toggle_media_playback(self) -> None:
        if self._media_source_path is None:
            return
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def _seek_media(self, position: int) -> None:
        self.media_player.setPosition(position)

    def _set_media_duration(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, duration))
        self._update_duration_label(self.media_player.position(), duration)

    def _set_media_position(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self._update_duration_label(position, self.media_player.duration())

    def _update_media_play_button(self, state) -> None:
        self.play_button.setText(
            "Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play"
        )

    def _toggle_media_mute(self) -> None:
        muted = not self.media_audio_output.isMuted()
        self.media_audio_output.setMuted(muted)
        self.mute_button.setText("Unmute" if muted else "Mute")

    def _set_media_volume(self, value: int) -> None:
        self.media_audio_output.setVolume(max(0, min(value, 100)) / 100)
        if value > 0 and self.media_audio_output.isMuted():
            self.media_audio_output.setMuted(False)
            self.mute_button.setText("Mute")

    def _handle_media_error(self, _error, message: str) -> None:
        if not message:
            message = "Could not preview this media file."
        current = self.preview_state.text().strip()
        self.preview_state.setText(f"{current}\n{message}" if current else message)
        self.play_button.setText("Play")

    def _update_duration_label(self, position: int, duration: int) -> None:
        self.duration_label.setText(
            f"{self._format_millis(position)} / {self._format_millis(duration)}"
        )

    def _format_millis(self, value: int) -> str:
        total_seconds = max(0, int(value / 1000))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _stop_media_preview(self) -> None:
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
        if hasattr(self, "media_audio_output"):
            for action in (
                lambda: self.media_audio_output.setMuted(True),
                lambda: self.media_audio_output.setVolume(0.0),
            ):
                try:
                    action()
                except RuntimeError:
                    pass
        if hasattr(self, "play_button"):
            self.play_button.setText("Play")
        if hasattr(self, "position_slider"):
            self.position_slider.setRange(0, 0)
            self.position_slider.setValue(0)
        if hasattr(self, "duration_label"):
            self.duration_label.setText("00:00 / 00:00")
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _toggle_selected_favorite(self) -> None:
        selected_ids = self.selected_asset_ids()
        if not selected_ids:
            return
        asset_id = selected_ids[0]
        favorite = asset_id not in self.favorite_asset_ids
        self.project_service.set_asset_favorite(asset_id, favorite)
        self._load_assets()
        self._refresh_assets()

    def _open_selected_in_explorer(self) -> None:
        selected_ids = self.selected_asset_ids()
        if not selected_ids:
            return
        asset = self.asset_by_id.get(selected_ids[0])
        if asset is not None:
            self._open_asset_in_explorer(asset)

    def _show_tree_context_menu(self, position) -> None:
        item = self.asset_tree.itemAt(position)
        if item is None:
            return
        asset = self._asset_from_tree_item(item)
        if asset is None:
            return
        self._show_asset_context_menu(asset, self.asset_tree.mapToGlobal(position))

    def _show_asset_context_menu(self, asset: Asset, global_position) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("📁 Open in Explorer")
        favorite_action = menu.addAction(
            "★ Remove Favorite" if asset.id in self.favorite_asset_ids else "☆ Favorite"
        )
        selected = menu.exec(global_position)
        if selected == open_action:
            self._open_asset_in_explorer(asset)
        elif selected == favorite_action and asset.id is not None:
            self.project_service.set_asset_favorite(
                asset.id,
                asset.id not in self.favorite_asset_ids,
            )
            self._load_assets()
            self._refresh_assets()

    def _open_asset_in_explorer(self, asset: Asset) -> None:
        target = asset.file_path if asset.file_path.is_dir() else asset.file_path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _asset_from_tree_item(self, item: QTreeWidgetItem) -> Asset | None:
        value = item.data(0, PICKER_ASSET_ID_ROLE)
        return self.asset_by_id.get(value) if isinstance(value, int) else None

    def _asset_display_name(self, asset: Asset) -> str:
        prefix = "📁 " if asset.is_collection else ""
        favorite = "★ " if asset.id in self.favorite_asset_ids else ""
        return f"{favorite}{prefix}{asset.name}"

    def _asset_kind(self, asset: Asset) -> str:
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

    def _usage_text(self, asset: Asset) -> str:
        count = self.usage_counts.get(asset.id or 0, 0)
        return f"Used in {count} project{'s' if count != 1 else ''}"

    def _asset_exists(self, asset: Asset) -> bool:
        return asset.file_path.exists() or asset.file_path.is_symlink()


class ProjectChoiceDialog(QDialog):
    def __init__(self, projects: list[Project], parent=None) -> None:
        super().__init__(parent)
        self.projects = projects
        self.setWindowTitle("Choose Project")
        self.setMinimumWidth(420)

        self.project_combo = QComboBox()
        for project in projects:
            label = project.name
            if project.client:
                label = f"{label} - {project.client}"
            self.project_combo.addItem(label, project.id)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Project"))
        layout.addWidget(self.project_combo)
        layout.addWidget(buttons)

    def selected_project_id(self) -> int | None:
        value = self.project_combo.currentData()
        return int(value) if value is not None else None
