from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models.project import (
    Asset,
    AssetSuggestion,
    FileIssue,
    Project,
    ProjectExport,
    ProjectRevision,
    STATUSES,
    TimelineEntry,
    WorkflowSuggestion,
)


class ProjectDetails(QFrame):
    toggle_requested = Signal()
    status_change_requested = Signal(object, str)
    edited_video_requested = Signal(object)
    notes_save_requested = Signal(object, str)
    open_folder_requested = Signal(object)
    open_raw_requested = Signal(object)
    open_edited_requested = Signal(object)
    refresh_folder_requested = Signal(object)
    link_asset_requested = Signal(object)
    link_suggested_asset_requested = Signal(object, object)
    unlink_asset_requested = Signal(object, object)
    open_asset_location_requested = Signal(object)
    revision_add_requested = Signal(object)
    revision_toggle_requested = Signal(object)
    apply_status_suggestion_requested = Signal(object, object)
    export_open_requested = Signal(object)
    export_show_requested = Signal(object)
    export_set_current_requested = Signal(object)
    relink_requested = Signal(object)

    def __init__(self, thumbnail_provider=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DetailsPanel")
        self.gutter_width = 8
        self.rail_width = 26
        self.expanded_width = 368
        self.collapsed_width = self.gutter_width + self.rail_width
        self.collapsed = True
        self.setMinimumWidth(self.collapsed_width)
        self.setMaximumWidth(self.expanded_width)
        self.project: Project | None = None
        self.thumbnail_provider = thumbnail_provider
        self._loading = False
        self._thumbnail_labels: dict[str, QLabel] = {}

        self.collapse_button = QPushButton("≪")
        self.collapse_button.setObjectName("PanelCollapseButton")
        self.collapse_button.setFixedSize(22, 42)
        self.collapse_button.setToolTip("Expand details")
        self.collapse_button.clicked.connect(self.toggle_requested.emit)

        self.empty_label = QLabel("Select a project to view details.")
        self.empty_label.setObjectName("MutedLabel")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.name_label = QLabel()
        self.name_label.setObjectName("DetailsTitle")
        self.name_label.setWordWrap(True)

        self.client_label = QLabel()
        self.client_label.setObjectName("MutedLabel")

        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUSES)
        self.status_combo.currentTextChanged.connect(self._emit_status_change)

        self.status_suggestions_list = QListWidget()
        self.status_suggestions_list.setMinimumHeight(90)
        self.apply_status_suggestion_button = QPushButton("📌 Apply")
        self.apply_status_suggestion_button.clicked.connect(
            self._emit_apply_status_suggestion
        )

        self.raw_label = QLabel()
        self.raw_label.setObjectName("PathLabel")
        self.raw_label.setWordWrap(True)
        self.raw_thumbnail = QLabel()
        self.raw_thumbnail.setObjectName("LargeThumbnail")
        self.raw_thumbnail.setFixedSize(144, 81)
        self.raw_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.raw_thumbnail.setText("Raw")
        self.open_raw_button = QPushButton("▶ Raw")
        self.open_raw_button.clicked.connect(self._emit_open_raw)

        self.edited_label = QLabel()
        self.edited_label.setObjectName("PathLabel")
        self.edited_label.setWordWrap(True)
        self.edited_thumbnail = QLabel()
        self.edited_thumbnail.setObjectName("LargeThumbnail")
        self.edited_thumbnail.setFixedSize(144, 81)
        self.edited_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edited_thumbnail.setText("Edited")
        self.open_edited_button = QPushButton("▶ Edited")
        self.open_edited_button.clicked.connect(self._emit_open_edited)

        self.select_edited_button = QPushButton("🎬 Select Edited")
        self.select_edited_button.clicked.connect(self._emit_select_edited)
        self.refresh_folder_button = QPushButton("📁 Refresh")
        self.refresh_folder_button.clicked.connect(self._emit_refresh_folder)

        self.folder_label = QLabel()
        self.folder_label.setObjectName("PathLabel")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.open_folder_button = QPushButton("📁 Open Folder")
        self.open_folder_button.setObjectName("PrimaryButton")
        self.open_folder_button.clicked.connect(self._emit_open_folder)

        self.notes_edit = QTextEdit()
        self.notes_edit.setMinimumHeight(100)
        self.notes_edit.setPlaceholderText("📝 Notes")
        self.save_notes_button = QPushButton("📝 Save")
        self.save_notes_button.clicked.connect(self._emit_save_notes)

        self.assets_list = QListWidget()
        self.assets_list.setMinimumHeight(130)

        self.suggested_assets_list = QListWidget()
        self.suggested_assets_list.setMinimumHeight(130)
        self.link_suggested_asset_button = QPushButton("🔗 Suggested")
        self.link_suggested_asset_button.clicked.connect(self._emit_link_suggested_asset)

        self.link_asset_button = QPushButton("🔗 Link")
        self.link_asset_button.clicked.connect(self._emit_link_asset)
        self.unlink_asset_button = QPushButton("Unlink")
        self.unlink_asset_button.clicked.connect(self._emit_unlink_asset)
        self.open_asset_button = QPushButton("📁 Asset")
        self.open_asset_button.clicked.connect(self._emit_open_asset_location)

        self.revisions_list = QListWidget()
        self.revisions_list.setMinimumHeight(130)
        self.add_revision_button = QPushButton("📝 Revision")
        self.add_revision_button.clicked.connect(self._emit_add_revision)
        self.toggle_revision_button = QPushButton("Toggle Complete")
        self.toggle_revision_button.clicked.connect(self._emit_toggle_revision)

        self.exports_list = QListWidget()
        self.exports_list.setMinimumHeight(120)
        self.open_export_button = QPushButton("▶ Open Video")
        self.open_export_button.clicked.connect(self._emit_open_export)
        self.show_export_button = QPushButton("📁 Folder")
        self.show_export_button.clicked.connect(self._emit_show_export)
        self.set_export_button = QPushButton("Set as Current Edited Video")
        self.set_export_button.clicked.connect(self._emit_set_export_current)

        self.health_list = QListWidget()
        self.health_list.setMinimumHeight(100)
        self.relink_button = QPushButton("Relink")
        self.relink_button.clicked.connect(self._emit_relink)

        self.timeline_list = QListWidget()
        self.timeline_list.setMinimumHeight(150)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(14)
        content_layout.addWidget(self.name_label)
        content_layout.addWidget(self.client_label)
        content_layout.addWidget(self._build_status_group())
        content_layout.addWidget(self._build_status_suggestions_group())
        content_layout.addWidget(self._build_video_group())
        content_layout.addWidget(self._build_folder_group())
        content_layout.addWidget(self._build_notes_group())
        content_layout.addWidget(self._build_revisions_group())
        content_layout.addWidget(self._build_exports_group())
        content_layout.addWidget(self._build_suggested_assets_group())
        content_layout.addWidget(self._build_assets_group())
        content_layout.addWidget(self._build_timeline_group())
        content_layout.addWidget(self._build_health_group())
        content_layout.addStretch(1)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(content)

        self.content_container = QWidget()
        content_container_layout = QVBoxLayout(self.content_container)
        content_container_layout.setContentsMargins(0, 0, 0, 0)
        content_container_layout.setSpacing(0)
        content_container_layout.addWidget(self.empty_label, 1)
        content_container_layout.addWidget(self.scroll_area, 1)

        gutter = QFrame()
        gutter.setObjectName("DetailsGutter")
        gutter.setFixedWidth(self.gutter_width)

        rail = QFrame()
        rail.setObjectName("DetailsRail")
        rail.setFixedWidth(self.rail_width)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(2, 8, 2, 8)
        rail_layout.addStretch(1)
        rail_layout.addWidget(self.collapse_button)
        rail_layout.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(gutter)
        layout.addWidget(rail)
        layout.addWidget(self.content_container, 1)

        self.set_collapsed(True)
        self.set_project(None, [], [], [], [])

        if self.thumbnail_provider is not None:
            self.thumbnail_provider.thumbnail_ready.connect(self._handle_thumbnail_ready)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self.content_container.setVisible(not collapsed)
        self.collapse_button.setText("≪" if collapsed else "≫")
        self.collapse_button.setToolTip(
            "Expand details" if collapsed else "Collapse details"
        )

    def set_project(
        self,
        project: Project | None,
        assets: list[Asset],
        exports: list[ProjectExport],
        issues: list[FileIssue],
        suggestions: list[AssetSuggestion] | None = None,
        revisions: list[ProjectRevision] | None = None,
        workflow_suggestions: list[WorkflowSuggestion] | None = None,
        timeline: list[TimelineEntry] | None = None,
    ) -> None:
        self.project = project
        self._loading = True
        self._thumbnail_labels.clear()
        self.empty_label.setVisible(project is None)
        self.scroll_area.setVisible(project is not None)

        if project is not None:
            self.name_label.setText(project.name)
            self.client_label.setText(f"👤 Client: {project.client or 'No client'}")
            self.status_combo.setCurrentText(project.status)
            self.raw_label.setText(project.raw_video_name)
            self.edited_label.setText(project.edited_video_name)
            self.open_edited_button.setEnabled(project.has_edited_video)
            self.folder_label.setText(str(project.folder_path))
            self.notes_edit.setPlainText(project.notes)
            self._set_workflow_suggestions(workflow_suggestions or [])
            self._set_assets(assets)
            self._set_suggestions(suggestions or [])
            self._set_revisions(revisions or [])
            self._set_exports(exports)
            self._set_timeline(timeline or [])
            self._set_issues(issues)
            self.raw_thumbnail.clear()
            self.raw_thumbnail.setText("Raw")
            self.edited_thumbnail.clear()
            self.edited_thumbnail.setText("Edited")
            self._request_label_thumbnail(project.raw_video_path, self.raw_thumbnail)
            if project.edited_video_path is not None:
                self._request_label_thumbnail(project.edited_video_path, self.edited_thumbnail)
            else:
                self.edited_thumbnail.clear()
                self.edited_thumbnail.setText("No edited video")
        else:
            self.status_suggestions_list.clear()
            self.apply_status_suggestion_button.setEnabled(False)
            self.assets_list.clear()
            self.suggested_assets_list.clear()
            self.link_suggested_asset_button.setEnabled(False)
            self.revisions_list.clear()
            self.toggle_revision_button.setEnabled(False)
            self.exports_list.clear()
            self.timeline_list.clear()
            self.health_list.clear()
            self._thumbnail_labels.clear()

        self._loading = False

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Status")
        layout = QVBoxLayout(group)
        layout.addWidget(self.status_combo)
        return group

    def _build_status_suggestions_group(self) -> QGroupBox:
        group = QGroupBox("📌 Status Suggestions")
        layout = QVBoxLayout(group)
        layout.addWidget(self.status_suggestions_list)
        layout.addWidget(self.apply_status_suggestion_button)
        return group

    def _build_video_group(self) -> QGroupBox:
        group = QGroupBox("🎬 Videos")
        layout = QVBoxLayout(group)
        layout.addWidget(QLabel("🎬 Raw"))
        layout.addWidget(self.raw_thumbnail)
        layout.addWidget(self.raw_label)
        layout.addWidget(self.open_raw_button)
        layout.addWidget(QLabel("🎬 Edited"))
        layout.addWidget(self.edited_thumbnail)
        layout.addWidget(self.edited_label)
        layout.addWidget(self.open_edited_button)
        layout.addWidget(self.select_edited_button)
        layout.addWidget(self.refresh_folder_button)
        return group

    def _build_folder_group(self) -> QGroupBox:
        group = QGroupBox("📁 Project Folder")
        layout = QVBoxLayout(group)
        layout.addWidget(self.folder_label)
        layout.addWidget(self.open_folder_button)
        return group

    def _build_notes_group(self) -> QGroupBox:
        group = QGroupBox("📝 Notes")
        layout = QVBoxLayout(group)
        layout.addWidget(self.notes_edit)
        layout.addWidget(self.save_notes_button)
        return group

    def _build_revisions_group(self) -> QGroupBox:
        group = QGroupBox("Revision History")
        layout = QVBoxLayout(group)
        layout.addWidget(self.revisions_list)
        row = QHBoxLayout()
        row.addWidget(self.add_revision_button)
        row.addWidget(self.toggle_revision_button)
        layout.addLayout(row)
        return group

    def _build_assets_group(self) -> QGroupBox:
        group = QGroupBox("📦 Linked Assets")
        layout = QVBoxLayout(group)
        layout.addWidget(self.assets_list)

        row = QHBoxLayout()
        row.addWidget(self.link_asset_button)
        row.addWidget(self.unlink_asset_button)
        layout.addLayout(row)
        layout.addWidget(self.open_asset_button)
        return group

    def _build_suggested_assets_group(self) -> QGroupBox:
        group = QGroupBox("📦 Suggested Assets")
        layout = QVBoxLayout(group)
        layout.addWidget(self.suggested_assets_list)
        layout.addWidget(self.link_suggested_asset_button)
        return group

    def _build_exports_group(self) -> QGroupBox:
        group = QGroupBox("Export History")
        layout = QVBoxLayout(group)
        layout.addWidget(self.exports_list)

        row = QHBoxLayout()
        row.addWidget(self.open_export_button)
        row.addWidget(self.show_export_button)
        layout.addLayout(row)
        layout.addWidget(self.set_export_button)
        return group

    def _build_health_group(self) -> QGroupBox:
        group = QGroupBox("File Health")
        layout = QVBoxLayout(group)
        layout.addWidget(self.health_list)
        layout.addWidget(self.relink_button)
        return group

    def _build_timeline_group(self) -> QGroupBox:
        group = QGroupBox("Project Timeline")
        layout = QVBoxLayout(group)
        layout.addWidget(self.timeline_list)
        return group

    def _set_assets(self, assets: list[Asset]) -> None:
        self.assets_list.clear()
        for asset in assets:
            text = asset.name
            if asset.category:
                text = f"{text} - {asset.category}"
            item = QListWidgetItem(text)
            item.setToolTip(str(asset.file_path))
            item.setData(Qt.ItemDataRole.UserRole, asset)
            cached = self._cached_thumbnail(asset.file_path)
            if cached is not None:
                item.setIcon(QIcon(str(cached)))
            self.assets_list.addItem(item)
            self._request_thumbnail(asset.file_path)

    def _set_workflow_suggestions(
        self,
        suggestions: list[WorkflowSuggestion],
    ) -> None:
        self.status_suggestions_list.clear()
        actionable = False
        if not suggestions:
            item = QListWidgetItem("No status suggestions")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.status_suggestions_list.addItem(item)
            self.apply_status_suggestion_button.setEnabled(False)
            return

        for suggestion in suggestions:
            text = suggestion.message
            if suggestion.target_status:
                text = f"{text}\nTarget: {suggestion.target_status}"
                actionable = True
            item = QListWidgetItem(text)
            item.setToolTip(suggestion.severity)
            item.setData(Qt.ItemDataRole.UserRole, suggestion)
            self.status_suggestions_list.addItem(item)
        self.status_suggestions_list.setCurrentRow(0)
        self.apply_status_suggestion_button.setEnabled(actionable)

    def _set_suggestions(self, suggestions: list[AssetSuggestion]) -> None:
        self.suggested_assets_list.clear()
        if not suggestions:
            item = QListWidgetItem("No suggestions yet")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.suggested_assets_list.addItem(item)
            self.link_suggested_asset_button.setEnabled(False)
            return

        self.link_suggested_asset_button.setEnabled(True)
        for suggestion in suggestions:
            asset = suggestion.asset
            reason_text = "\n".join(suggestion.reasons[:3])
            text = asset.name
            if reason_text:
                text = f"{text}\n{reason_text}"
            item = QListWidgetItem(text)
            item.setToolTip(str(asset.file_path))
            item.setData(Qt.ItemDataRole.UserRole, suggestion)
            cached = self._cached_thumbnail(asset.file_path)
            if cached is not None:
                item.setIcon(QIcon(str(cached)))
            self.suggested_assets_list.addItem(item)
            self._request_thumbnail(asset.file_path)
        self.suggested_assets_list.setCurrentRow(0)

    def _set_revisions(self, revisions: list[ProjectRevision]) -> None:
        self.revisions_list.clear()
        if not revisions:
            item = QListWidgetItem("No revision feedback recorded")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.revisions_list.addItem(item)
            self.toggle_revision_button.setEnabled(False)
            return

        self.toggle_revision_button.setEnabled(True)
        for revision in revisions:
            marker = "[x]" if revision.completed else "[ ]"
            timestamp = revision.created_at[:16] if revision.created_at else ""
            text = f"{marker} {revision.note}"
            if timestamp:
                text = f"{text}\n{timestamp}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, revision)
            self.revisions_list.addItem(item)
        self.revisions_list.setCurrentRow(0)

    def _set_exports(self, exports: list[ProjectExport]) -> None:
        self.exports_list.clear()
        for export in exports:
            text = f"{export.version_label.upper()}  {export.filename}"
            if export.detected_at:
                text = f"{text}\n{export.detected_at}"
            item = QListWidgetItem(text)
            item.setToolTip(str(export.file_path))
            item.setData(Qt.ItemDataRole.UserRole, export)
            cached = self._cached_thumbnail(export.file_path)
            if cached is not None:
                item.setIcon(QIcon(str(cached)))
            self.exports_list.addItem(item)
            self._request_thumbnail(export.file_path)

    def _set_timeline(self, timeline: list[TimelineEntry]) -> None:
        self.timeline_list.clear()
        if not timeline:
            item = QListWidgetItem("No timeline entries yet")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.timeline_list.addItem(item)
            return

        for entry in timeline:
            timestamp = entry.created_at[:16] if entry.created_at else ""
            text = entry.label
            if entry.detail:
                text = f"{text}\n{entry.detail}"
            if timestamp:
                text = f"{text}\n{timestamp}"
            item = QListWidgetItem(text)
            item.setToolTip(entry.kind)
            self.timeline_list.addItem(item)

    def _set_issues(self, issues: list[FileIssue]) -> None:
        self.health_list.clear()
        if not issues:
            item = QListWidgetItem("All linked files found")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.health_list.addItem(item)
            self.relink_button.setEnabled(False)
            return

        self.relink_button.setEnabled(True)
        for issue in issues:
            item = QListWidgetItem(f"! {issue.label}\n{issue.path}")
            item.setToolTip(str(issue.path))
            item.setData(Qt.ItemDataRole.UserRole, issue)
            self.health_list.addItem(item)

    def _selected_asset(self) -> Asset | None:
        item = self.assets_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, Asset) else None

    def _selected_suggestion(self) -> AssetSuggestion | None:
        item = self.suggested_assets_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, AssetSuggestion) else None

    def _selected_workflow_suggestion(self) -> WorkflowSuggestion | None:
        item = self.status_suggestions_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, WorkflowSuggestion) else None

    def _selected_revision(self) -> ProjectRevision | None:
        item = self.revisions_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, ProjectRevision) else None

    def _selected_export(self) -> ProjectExport | None:
        item = self.exports_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, ProjectExport) else None

    def _selected_issue(self) -> FileIssue | None:
        item = self.health_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, FileIssue) else None

    def _emit_status_change(self, status: str) -> None:
        if not self._loading and self.project is not None:
            self.status_change_requested.emit(self.project, status)

    def _emit_select_edited(self) -> None:
        if self.project is not None:
            self.edited_video_requested.emit(self.project)

    def _emit_save_notes(self) -> None:
        if self.project is not None:
            self.notes_save_requested.emit(self.project, self.notes_edit.toPlainText())

    def _emit_open_folder(self) -> None:
        if self.project is not None:
            self.open_folder_requested.emit(self.project)

    def _emit_open_raw(self) -> None:
        if self.project is not None:
            self.open_raw_requested.emit(self.project)

    def _emit_open_edited(self) -> None:
        if self.project is not None and self.project.has_edited_video:
            self.open_edited_requested.emit(self.project)

    def _emit_refresh_folder(self) -> None:
        if self.project is not None:
            self.refresh_folder_requested.emit(self.project)

    def _emit_link_asset(self) -> None:
        if self.project is not None:
            self.link_asset_requested.emit(self.project)

    def _emit_link_suggested_asset(self) -> None:
        suggestion = self._selected_suggestion()
        if self.project is not None and suggestion is not None:
            self.link_suggested_asset_requested.emit(self.project, suggestion.asset)

    def _emit_apply_status_suggestion(self) -> None:
        suggestion = self._selected_workflow_suggestion()
        if (
            self.project is not None
            and suggestion is not None
            and suggestion.target_status
        ):
            self.apply_status_suggestion_requested.emit(self.project, suggestion)

    def _emit_add_revision(self) -> None:
        if self.project is not None:
            self.revision_add_requested.emit(self.project)

    def _emit_toggle_revision(self) -> None:
        revision = self._selected_revision()
        if revision is not None:
            self.revision_toggle_requested.emit(revision)

    def _emit_unlink_asset(self) -> None:
        asset = self._selected_asset()
        if self.project is not None and asset is not None:
            self.unlink_asset_requested.emit(self.project, asset)

    def _emit_open_asset_location(self) -> None:
        asset = self._selected_asset()
        if asset is not None:
            self.open_asset_location_requested.emit(asset)

    def _emit_open_export(self) -> None:
        export = self._selected_export()
        if export is not None:
            self.export_open_requested.emit(export)

    def _emit_show_export(self) -> None:
        export = self._selected_export()
        if export is not None:
            self.export_show_requested.emit(export)

    def _emit_set_export_current(self) -> None:
        export = self._selected_export()
        if export is not None:
            self.export_set_current_requested.emit(export)

    def _emit_relink(self) -> None:
        issue = self._selected_issue()
        if issue is not None and issue.can_relink:
            self.relink_requested.emit(issue)

    def _request_label_thumbnail(self, path: Path, label: QLabel) -> None:
        if self.thumbnail_provider is None:
            return
        normalized = self.thumbnail_provider.normalized(path)
        self._thumbnail_labels[normalized] = label
        cached = self.thumbnail_provider.cached_thumbnail(path)
        if cached is not None:
            self._set_label_thumbnail(label, cached)
            return
        self.thumbnail_provider.request(path)

    def _request_thumbnail(self, path: Path) -> None:
        if self.thumbnail_provider is not None:
            self.thumbnail_provider.request(path)

    def _cached_thumbnail(self, path: Path) -> Path | None:
        if self.thumbnail_provider is None:
            return None
        return self.thumbnail_provider.cached_thumbnail(path)

    def _normalized(self, path: Path) -> str:
        if self.thumbnail_provider is None:
            return str(path)
        return self.thumbnail_provider.normalized(path)

    def _handle_thumbnail_ready(self, normalized_path: str, cache_path: str) -> None:
        label = self._thumbnail_labels.get(normalized_path)
        if label is not None:
            self._set_label_thumbnail(label, Path(cache_path))

        for index in range(self.assets_list.count()):
            item = self.assets_list.item(index)
            asset = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(asset, Asset) and self._normalized(asset.file_path) == normalized_path:
                item.setIcon(QIcon(cache_path))

        for index in range(self.suggested_assets_list.count()):
            item = self.suggested_assets_list.item(index)
            suggestion = item.data(Qt.ItemDataRole.UserRole)
            if (
                isinstance(suggestion, AssetSuggestion)
                and self._normalized(suggestion.asset.file_path) == normalized_path
            ):
                item.setIcon(QIcon(cache_path))

        for index in range(self.exports_list.count()):
            item = self.exports_list.item(index)
            export = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(export, ProjectExport) and self._normalized(export.file_path) == normalized_path:
                item.setIcon(QIcon(cache_path))

    def _set_label_thumbnail(self, label: QLabel, thumbnail_path: Path) -> None:
        pixmap = QPixmap(str(thumbnail_path))
        if not pixmap.isNull():
            label.setPixmap(
                pixmap.scaled(
                    label.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
