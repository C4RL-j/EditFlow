from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import json
from time import monotonic

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QFileSystemWatcher,
    QObject,
    QPoint,
    QPauseAnimation,
    QPropertyAnimation,
    QRect,
    QSequentialAnimationGroup,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QDoubleSpinBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from core.file_manager import LINKED_ASSETS_FOLDER_NAME, is_mp4_file, normalize_file_path
from core.project_service import ProjectService
from core.thumbnail_cache import ThumbnailProvider
from models.project import (
    Asset,
    Client,
    ClientSummary,
    DetectionEntry,
    FOLDER_TYPES,
    FileIssue,
    Project,
    ProjectExport,
    ProjectRevision,
    PUBLISHED_STATUS,
    STATUSES,
    WatchedFolder,
    WorkflowSuggestion,
    workflow_move_block_reason,
    workflow_stage_label,
)
from ui.asset_widgets import (
    ASSET_ID_ROLE,
    ASSET_PATH_ROLE,
    ASSET_TYPE_ROLE,
    AssetLibraryTree,
    AssetListWidget,
)
from ui.dialogs import (
    AddRawVideoDialog,
    AssetDialog,
    AssetPickerDialog,
    NotesDialog,
    ProjectChoiceDialog,
)
from ui.kanban_board import KanbanBoard
from ui.project_details import ProjectDetails
from ui.sidebar import Sidebar
from ui.sound_effects import SoundEffects
from ui.video_preview_dialog import VideoPreviewDialog


class BackgroundTaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class BackgroundTask(QRunnable):
    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback
        self.signals = BackgroundTaskSignals()

    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.callback())
        except Exception as error:
            self.signals.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self, project_service: ProjectService) -> None:
        super().__init__()
        self.project_service = project_service
        self.thread_pool = QThreadPool.globalInstance()
        self._background_tasks: set[BackgroundTask] = set()
        self._loading_jobs = 0
        self._loading_started_at = 0.0
        self._loading_minimum_ms = 260
        self.sound_effects = SoundEffects(parent=self)
        self.thumbnail_provider = ThumbnailProvider(
            project_service.database.db_path.parent / "thumbnails",
            self,
        )
        self.thumbnail_provider.thumbnail_ready.connect(self._handle_thumbnail_ready)
        self.selected_project_id: int | None = None
        self.selected_client_id: int | None = None
        self.project_cache: dict[int, Project] = {}
        self._prompted_detected_paths: set[str] = set()
        self._detection_queue: list[dict[str, object]] = []
        self._active_detection: dict[str, object] | None = None
        self._scan_running = False
        self._logged_file_issues: set[str] = set()
        self.projects_right_rail_reserve = 46
        self._earnings_animation_queue: list[float] = []
        self._earnings_animation_running = False
        self._earnings_animation_group: QSequentialAnimationGroup | None = None
        self._total_earnings_count_animation: QVariantAnimation | None = None

        self.project_refresh_timer = QTimer(self)
        self.project_refresh_timer.setSingleShot(True)
        self.project_refresh_timer.setInterval(180)
        self.project_refresh_timer.timeout.connect(self.refresh_projects)

        self.folder_watcher = QFileSystemWatcher(self)
        self.folder_watcher.directoryChanged.connect(self._schedule_selected_folder_scan)
        self.watched_folders_watcher = QFileSystemWatcher(self)
        self.watched_folders_watcher.directoryChanged.connect(
            lambda _path: self._schedule_detection_scan()
        )

        self.detection_scan_timer = QTimer(self)
        self.detection_scan_timer.setSingleShot(True)
        self.detection_scan_timer.setInterval(1200)
        self.detection_scan_timer.timeout.connect(self._scan_for_detections)

        self.periodic_scan_timer = QTimer(self)
        self.periodic_scan_timer.setInterval(45000)
        self.periodic_scan_timer.timeout.connect(self._schedule_detection_scan)

        self.loading_hide_timer = QTimer(self)
        self.loading_hide_timer.setSingleShot(True)
        self.loading_hide_timer.timeout.connect(self._hide_loading_now)
        self.toast_hide_timer = QTimer(self)
        self.toast_hide_timer.setSingleShot(True)
        self.toast_hide_timer.timeout.connect(lambda: self.toast_label.setVisible(False))

        self.setWindowTitle("EditFlow")
        self._apply_window_icon()
        self.resize(1280, 720)

        self.sidebar = Sidebar()
        self.sidebar.view_requested.connect(self._show_view)
        self.sidebar.add_raw_requested.connect(self._add_raw_video)

        self.stack = QStackedWidget()
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        self.projects_page = self._build_projects_page()
        self.dashboard_page = self._scrollable_page(self._build_dashboard_page())
        self.assets_page = self._scrollable_page(self._build_assets_page())
        self.publish_page = self._scrollable_page(self._build_publish_page())
        self.clients_page = self._scrollable_page(self._build_clients_page())
        self.settings_page = self._scrollable_page(self._build_settings_page())
        self.stack.addWidget(self.projects_page)
        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.assets_page)
        self.stack.addWidget(self.publish_page)
        self.stack.addWidget(self.clients_page)
        self.stack.addWidget(self.settings_page)

        self.details = ProjectDetails(self.thumbnail_provider)
        self.details.status_change_requested.connect(self._change_status)
        self.details.edited_video_requested.connect(self._select_edited_video)
        self.details.notes_save_requested.connect(self._save_notes)
        self.details.open_folder_requested.connect(self._open_project_folder)
        self.details.open_raw_requested.connect(self._open_raw_video)
        self.details.open_edited_requested.connect(self._open_edited_video)
        self.details.refresh_folder_requested.connect(self._scan_project_folder)
        self.details.link_asset_requested.connect(self._link_asset_from_dialog)
        self.details.link_suggested_asset_requested.connect(self._link_suggested_asset)
        self.details.unlink_asset_requested.connect(self._unlink_asset)
        self.details.open_asset_location_requested.connect(self._open_asset_location)
        self.details.revision_add_requested.connect(self._add_project_revision)
        self.details.revision_toggle_requested.connect(self._toggle_project_revision)
        self.details.apply_status_suggestion_requested.connect(
            self._apply_status_suggestion
        )
        self.details.export_open_requested.connect(self._open_export_video)
        self.details.export_show_requested.connect(self._show_export_in_folder)
        self.details.export_set_current_requested.connect(self._set_export_current)
        self.details.relink_requested.connect(self._relink_missing_file)
        self.details.toggle_requested.connect(self._toggle_details_overlay)

        self.content_widget = QWidget()
        self.content_widget.installEventFilter(self)
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.stack, 1)
        self.loading_panel = self._build_loading_panel(self.content_widget)
        self._position_loading_panel()
        self.toast_label = QLabel(self.content_widget)
        self.toast_label.setObjectName("ToastLabel")
        self.toast_label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.toast_label.setWordWrap(True)
        self.toast_label.setVisible(False)
        self._position_toast()
        self.earnings_animation_stage = QWidget(self.content_widget)
        self.earnings_animation_stage.setObjectName("EarningsAnimationStage")
        self.earnings_animation_stage.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.earnings_animation_stage.setVisible(False)
        self.earnings_animation_label = QLabel(self.earnings_animation_stage)
        self.earnings_animation_label.setObjectName("EarningsAnimationLabel")
        self.earnings_animation_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.earnings_animation_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.earnings_animation_label.setVisible(False)

        self._details_expanded = False
        self.details.setParent(self.content_widget)
        self.details.installEventFilter(self)
        self.details.setMouseTracking(True)
        self.details.set_collapsed(True)
        self.details.show()
        self.details_collapse_timer = QTimer(self)
        self.details_collapse_timer.setSingleShot(True)
        self.details_collapse_timer.setInterval(190)
        self.details_collapse_timer.timeout.connect(self._collapse_details_if_mouse_left)
        self.details_animation = QPropertyAnimation(self.details, b"geometry", self)
        self.details_animation.setDuration(190)
        self.details_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.details_animation.finished.connect(self._finish_details_animation)

        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.sidebar)
        shell_layout.addWidget(self.content_widget, 1)

        self.setCentralWidget(shell)
        self.setStyleSheet(STYLESHEET)
        self._position_details_overlay(animated=False)
        self.refresh_projects()
        self.refresh_dashboard()
        self.refresh_assets()
        self.refresh_publish()
        self._ensure_published_earnings_seen_baseline()
        self.refresh_clients()
        self.refresh_workflow_settings()
        self.refresh_watched_folders()
        self.refresh_activity()
        self.periodic_scan_timer.start()
        QTimer.singleShot(1500, self._schedule_detection_scan)

    def _apply_window_icon(self) -> None:
        logo_path = self._editflow_logo_path()
        if logo_path is not None:
            self.setWindowIcon(QIcon(str(logo_path)))

    def _editflow_logo_path(self) -> Path | None:
        assets_path = Path(__file__).resolve().parent.parent / "assets"
        logo_candidates = (
            assets_path / "logos" / "Logo.png",
            assets_path / "logos" / "editflow_logo.png",
            assets_path / "editflow_logo.png",
        )
        return next((path for path in logo_candidates if path.exists()), None)

    def _build_projects_page(self) -> QWidget:
        self.project_search_edit = QLineEdit()
        self.project_search_edit.setPlaceholderText("🔍 Search projects, clients, files, notes...")
        self.project_search_edit.setMinimumWidth(300)
        self.project_search_edit.setMaximumWidth(380)
        self.project_search_edit.textChanged.connect(lambda _text: self._schedule_project_refresh())

        self.client_filter_combo = QComboBox()
        self.client_filter_combo.setMinimumWidth(150)
        self.client_filter_combo.setMaximumWidth(190)
        self.client_filter_combo.currentIndexChanged.connect(
            lambda _index: self._schedule_project_refresh()
        )

        publish_button = QPushButton(workflow_stage_label(PUBLISHED_STATUS))
        publish_button.setObjectName("PublishButton")
        publish_button.setToolTip("Open Published Projects")
        publish_button.clicked.connect(lambda: self._show_view("publish"))

        title = QPushButton("📁 Projects")
        title.setObjectName("PageTitleButton")
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.setToolTip(f"Open {self.project_service.projects_root()}")
        title.clicked.connect(self._open_projects_root)
        subtitle = QLabel("Drag cards between stages. Drop files onto cards to link assets or set edited videos.")
        subtitle.setObjectName("MutedLabel")

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)
        top_row.addWidget(self.project_search_edit)
        top_row.addStretch(1)
        top_row.addWidget(QLabel("👤 Client"))
        top_row.addWidget(self.client_filter_combo)
        top_row.addWidget(publish_button)

        self.notification_panel = self._build_notification_panel()

        heading_layout = QVBoxLayout()
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(4)
        heading_layout.addWidget(title)
        heading_layout.addWidget(subtitle)

        self.board = KanbanBoard(self.thumbnail_provider, self.sound_effects)
        self.board.project_selected.connect(self._select_project)
        self.board.add_raw_video_requested.connect(self._add_raw_video)
        self.board.publish_requested.connect(self._publish_project)
        self.board.project_status_dropped.connect(self._drop_project_status)
        self.board.project_reordered.connect(self._drop_project_ordered)
        self.board.files_dropped.connect(self._handle_card_files_dropped)
        self.board.raw_files_dropped.connect(self._handle_need_edit_files_dropped)
        self.board.asset_dropped.connect(self._handle_asset_dropped_on_project)
        self.board.assets_dropped.connect(self._handle_assets_dropped_on_project)
        self.board.video_preview_requested.connect(self._preview_video)
        self.board.edited_video_requested.connect(self._select_edited_video)
        self.board.open_folder_requested.connect(self._open_project_folder)
        self.board.copy_folder_path_requested.connect(self._copy_project_folder_path)
        self.board.priority_toggle_requested.connect(self._toggle_project_priority)
        self.board.video_type_edit_requested.connect(self._edit_project_video_type)
        self.board.payment_edit_requested.connect(self._edit_project_payment_amount)
        self.board.revision_requested.connect(self._add_project_revision)
        self.board.note_edit_requested.connect(self._edit_project_notes)
        self.board.link_assets_requested.connect(self._link_asset_from_dialog)
        self.board.open_linked_assets_requested.connect(self._open_project_linked_assets)
        self.board.remove_project_requested.connect(self._remove_project)
        self.board.flow_toggle_requested.connect(self._toggle_project_flow)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, self.projects_right_rail_reserve, 16)
        layout.setSpacing(12)
        layout.addLayout(top_row)
        layout.addWidget(self.notification_panel)
        layout.addLayout(heading_layout)
        layout.addWidget(self.board, 1)
        return page

    def _scrollable_page(self, page: QWidget) -> QScrollArea:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        scroll_area.setWidget(page)
        return scroll_area

    def _build_publish_page(self) -> QWidget:
        self.publish_search_edit = QLineEdit()
        self.publish_search_edit.setPlaceholderText("🔍 Search published projects...")
        self.publish_search_edit.textChanged.connect(lambda _text: self.refresh_publish())

        restore_button = QPushButton("Restore")
        restore_button.setObjectName("PrimaryButton")
        restore_button.setToolTip("Restore to Done")
        restore_button.clicked.connect(self._restore_selected_published_project)

        open_folder_button = QPushButton("📁 Open Folder")
        open_folder_button.clicked.connect(self._open_selected_published_folder)

        self.publish_total_earnings_label = QLabel("Total $0.00")
        self.publish_total_earnings_label.setObjectName("TotalEarningsLabel")
        self.publish_total_earnings_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.publish_total_earnings_label.setMinimumWidth(220)
        self.publish_total_earnings_label.setToolTip(
            "Total earned from all published projects"
        )

        title = QPushButton(workflow_stage_label(PUBLISHED_STATUS))
        title.setObjectName("PageTitleButton")
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.setToolTip(f"Open {self.project_service.projects_root() / PUBLISHED_STATUS}")
        title.clicked.connect(self._open_published_root)
        subtitle = QLabel("Published projects are stored in the Published folder and can be restored to Done.")
        subtitle.setObjectName("MutedLabel")

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(title)
        header_row.addStretch(1)

        earnings_row = QHBoxLayout()
        earnings_row.setContentsMargins(0, 0, 20, 0)
        earnings_row.setAlignment(Qt.AlignmentFlag.AlignRight)
        earnings_row.addWidget(self.publish_total_earnings_label)

        top_row = QHBoxLayout()
        top_row.addWidget(self.publish_search_edit, 1)
        top_row.addWidget(open_folder_button)
        top_row.addWidget(restore_button)

        self.publish_table = QTableWidget(0, 8)
        self.publish_table.setHorizontalHeaderLabels(
            ["Project", "Client", "Status", "Earned", "Raw Video", "Edited Video", "Folder", "ID"]
        )
        self.publish_table.setColumnHidden(7, True)
        self.publish_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.publish_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.publish_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.publish_table.customContextMenuRequested.connect(
            self._show_published_table_context_menu
        )
        self.publish_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.publish_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.publish_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.publish_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.publish_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.publish_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.publish_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(top_row)
        layout.addLayout(header_row)
        layout.addWidget(subtitle)
        layout.addLayout(earnings_row)
        layout.addWidget(self.publish_table, 1)
        return page

    def _build_notification_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("NotificationPanel")
        panel.setVisible(False)

        self.notification_title = QLabel()
        self.notification_title.setObjectName("SectionTitle")
        self.notification_title.setWordWrap(True)
        self.notification_body = QLabel()
        self.notification_body.setObjectName("MutedLabel")
        self.notification_body.setWordWrap(True)

        self.notification_primary_button = QPushButton()
        self.notification_secondary_button = QPushButton()
        self.notification_ignore_button = QPushButton("Ignore")
        self.notification_primary_button.clicked.connect(lambda: self._handle_detection_action("primary"))
        self.notification_secondary_button.clicked.connect(lambda: self._handle_detection_action("secondary"))
        self.notification_ignore_button.clicked.connect(lambda: self._handle_detection_action("ignore"))

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.notification_primary_button)
        button_row.addWidget(self.notification_secondary_button)
        button_row.addWidget(self.notification_ignore_button)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self.notification_title)
        layout.addWidget(self.notification_body)
        layout.addLayout(button_row)
        return panel

    def _build_loading_panel(self, parent: QWidget) -> QFrame:
        panel = QFrame(parent)
        panel.setObjectName("LoadingOverlay")
        panel.setVisible(False)
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        card = QFrame()
        card.setObjectName("LoadingCard")
        card.setMaximumWidth(420)

        self.loading_label = QLabel("Working...")
        self.loading_label.setObjectName("LoadingLabel")

        self.loading_progress = QProgressBar()
        self.loading_progress.setObjectName("LoadingProgress")
        self.loading_progress.setRange(0, 0)
        self.loading_progress.setTextVisible(False)
        self.loading_progress.setFixedHeight(6)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        label_row = QHBoxLayout()
        label_row.setContentsMargins(0, 0, 0, 0)
        label_row.setSpacing(8)
        loading_icon = QLabel("•")
        loading_icon.setObjectName("LoadingIcon")
        label_row.addWidget(loading_icon)
        label_row.addWidget(self.loading_label, 1)

        card_layout.addLayout(label_row)
        card_layout.addWidget(self.loading_progress)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(0)
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        return panel

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_details_overlay(animated=False)
        self._position_loading_panel()
        self._position_toast()

    def eventFilter(self, watched, event) -> bool:
        if hasattr(self, "content_widget") and watched == self.content_widget:
            if event.type() == QEvent.Type.Resize:
                self._position_details_overlay(animated=False)
                self._position_loading_panel()
                self._position_toast()
        if hasattr(self, "details") and watched == self.details:
            if event.type() == QEvent.Type.Enter:
                self._expand_details_overlay()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_details_collapse()
        return super().eventFilter(watched, event)

    def _details_overlay_allowed(self) -> bool:
        return (
            hasattr(self, "stack")
            and hasattr(self, "projects_page")
            and self.stack.currentWidget() == self.projects_page
        )

    def _position_details_overlay(self, *, animated: bool) -> None:
        if not hasattr(self, "details") or not hasattr(self, "content_widget"):
            return
        if not self._details_overlay_allowed():
            self.details.hide()
            return

        width = (
            self.details.expanded_width
            if getattr(self, "_details_expanded", False)
            else self.details.collapsed_width
        )
        width = min(width, max(self.details.collapsed_width, self.content_widget.width()))
        target = QRect(
            max(0, self.content_widget.width() - width),
            0,
            width,
            self.content_widget.height(),
        )
        self.details.show()
        self.details.raise_()
        if hasattr(self, "loading_panel") and self.loading_panel.isVisible():
            self.loading_panel.raise_()
        if hasattr(self, "toast_label") and self.toast_label.isVisible():
            self.toast_label.raise_()

        if animated and self.details.geometry().isValid():
            self.details_animation.stop()
            self.details_animation.setStartValue(self.details.geometry())
            self.details_animation.setEndValue(target)
            self.details_animation.start()
        else:
            self.details.setGeometry(target)

    def _expand_details_overlay(self) -> None:
        if not self._details_overlay_allowed():
            return
        self.details_collapse_timer.stop()
        if self._details_expanded:
            return
        self._details_expanded = True
        self.details.set_collapsed(False)
        self._position_details_overlay(animated=True)

    def _collapse_details_overlay(self, *, animated: bool = True) -> None:
        if not hasattr(self, "details"):
            return
        if not self._details_expanded:
            self.details.set_collapsed(True)
            self._position_details_overlay(animated=False)
            return
        self._details_expanded = False
        self._position_details_overlay(animated=animated)
        if not animated:
            self.details.set_collapsed(True)

    def _toggle_details_overlay(self) -> None:
        if self._details_expanded:
            self._collapse_details_overlay(animated=True)
        else:
            self._expand_details_overlay()

    def _schedule_details_collapse(self) -> None:
        if self._details_expanded:
            self.details_collapse_timer.start()

    def _collapse_details_if_mouse_left(self) -> None:
        if self._cursor_over_details_overlay():
            return
        self._collapse_details_overlay(animated=True)

    def _cursor_over_details_overlay(self) -> bool:
        if not self.details.isVisible():
            return False
        local_position = self.details.mapFromGlobal(QCursor.pos())
        return self.details.rect().contains(local_position)

    def _finish_details_animation(self) -> None:
        if not self._details_expanded:
            self.details.set_collapsed(True)
            self._position_details_overlay(animated=False)

    def _update_details_overlay_visibility(self) -> None:
        if not self._details_overlay_allowed():
            self.details_collapse_timer.stop()
            self._details_expanded = False
            self.details.set_collapsed(True)
            self.details.hide()
            return
        self.details.show()
        self._position_details_overlay(animated=False)

    def _queue_earnings_animation(self, amount: float) -> None:
        if amount <= 0:
            return
        self._earnings_animation_queue.append(float(amount))
        if not self._earnings_animation_running:
            self._play_next_earnings_animation()

    def _play_next_earnings_animation(self) -> None:
        if not self._earnings_animation_queue:
            self._earnings_animation_running = False
            self.earnings_animation_stage.hide()
            self.earnings_animation_label.hide()
            return
        self._earnings_animation_running = True
        amount = self._earnings_animation_queue.pop(0)
        self.earnings_animation_label.setText(f"+${amount:.2f}")
        self.earnings_animation_label.adjustSize()
        self.earnings_animation_label.resize(
            max(96, self.earnings_animation_label.width()),
            max(34, self.earnings_animation_label.height()),
        )

        hidden_position, visible_position = self._position_earnings_animation_stage()
        self.earnings_animation_label.move(hidden_position)
        self.earnings_animation_label.show()
        self.earnings_animation_stage.show()
        self.earnings_animation_stage.raise_()

        entrance = QPropertyAnimation(self.earnings_animation_label, b"pos", self)
        entrance.setDuration(320)
        entrance.setStartValue(hidden_position)
        entrance.setEndValue(visible_position)
        entrance.setEasingCurve(QEasingCurve.Type.OutCubic)

        hold = QPauseAnimation(2000, self)

        exit_animation = QPropertyAnimation(self.earnings_animation_label, b"pos", self)
        exit_animation.setDuration(300)
        exit_animation.setStartValue(visible_position)
        exit_animation.setEndValue(hidden_position)
        exit_animation.setEasingCurve(QEasingCurve.Type.InCubic)

        group = QSequentialAnimationGroup(self)
        group.addAnimation(entrance)
        group.addAnimation(hold)
        group.addAnimation(exit_animation)
        group.finished.connect(self._finish_earnings_animation)
        self._earnings_animation_group = group
        group.start()

    def _position_earnings_animation_stage(self) -> tuple[QPoint, QPoint]:
        label_width = self.earnings_animation_label.width()
        label_height = self.earnings_animation_label.height()
        stage_width = label_width + 18
        stage_height = label_height + 36

        if hasattr(self, "board") and "Done" in self.board.columns:
            column = self.board.columns["Done"]
            top_left = column.mapTo(self.content_widget, QPoint(0, 0))
            done_center_x = top_left.x() + column.width() // 2
            x = done_center_x - stage_width // 2
            y = top_left.y() - stage_height - 3
            max_stage_height = max(1, top_left.y() - 11)
            if y < 8 and max_stage_height < stage_height:
                stage_height = max_stage_height
                y = max(8, top_left.y() - stage_height - 3)
        elif hasattr(self, "board"):
            board_top_left = self.board.mapTo(self.content_widget, QPoint(0, 0))
            x = board_top_left.x() + self.board.width() - stage_width - 24
            y = board_top_left.y() - stage_height - 3
        else:
            x = (self.content_widget.width() - stage_width) // 2
            y = 8

        x = max(8, min(x, self.content_widget.width() - stage_width - 8))
        y = max(8, min(y, self.content_widget.height() - stage_height - 8))
        self.earnings_animation_stage.setGeometry(x, y, stage_width, stage_height)

        label_x = (stage_width - label_width) // 2
        visible_y = max(0, stage_height - label_height - 4)
        hidden_y = stage_height + 2
        return QPoint(label_x, hidden_y), QPoint(label_x, visible_y)

    def _finish_earnings_animation(self) -> None:
        if self._earnings_animation_group is not None:
            self._earnings_animation_group.stop()
        self.earnings_animation_label.hide()
        self.earnings_animation_stage.hide()
        self._earnings_animation_group = None
        self._earnings_animation_running = False
        self._play_next_earnings_animation()

    def _position_loading_panel(self) -> None:
        if not hasattr(self, "loading_panel") or not hasattr(self, "content_widget"):
            return
        self.loading_panel.setGeometry(self.content_widget.rect())
        self.loading_panel.raise_()

    def _position_toast(self) -> None:
        if not hasattr(self, "toast_label") or not hasattr(self, "content_widget"):
            return
        self.toast_label.adjustSize()
        board_width = self.board.width() if hasattr(self, "board") else self.content_widget.width()
        max_width = min(360, max(220, board_width - 40))
        self.toast_label.setMaximumWidth(max_width)
        self.toast_label.adjustSize()

        if (
            hasattr(self, "board")
            and hasattr(self, "stack")
            and hasattr(self, "projects_page")
            and self.stack.currentWidget() == self.projects_page
            and self.board.isVisible()
        ):
            board_top_left = self.board.mapTo(self.content_widget, QPoint(0, 0))
            x = board_top_left.x() + (self.board.width() - self.toast_label.width()) // 2
            x = max(18, min(x, self.content_widget.width() - self.toast_label.width() - 18))
            y = board_top_left.y() - self.toast_label.height() - 8
            if y < 18:
                y = board_top_left.y() + 10
            self.toast_label.move(x, y)
        else:
            x = (self.content_widget.width() - self.toast_label.width()) // 2
            self.toast_label.move(max(18, x), 18)
        self.toast_label.raise_()

    def _build_dashboard_page(self) -> QWidget:
        title = QLabel("Dashboard")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Actionable workflow signals for local editing jobs.")
        subtitle.setObjectName("MutedLabel")

        counts_row = QHBoxLayout()
        self.dashboard_count_labels: dict[str, QLabel] = {}
        for status in STATUSES[:3]:
            card = QFrame()
            card.setObjectName("MetricCard")
            value_label = QLabel("0")
            value_label.setObjectName("MetricNumber")
            name_label = QLabel(status)
            name_label.setObjectName("MutedLabel")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.addWidget(value_label)
            card_layout.addWidget(name_label)
            counts_row.addWidget(card)
            self.dashboard_count_labels[status] = value_label
        counts_row.addStretch(1)

        detected_title = QLabel("Recently Detected")
        detected_title.setObjectName("SectionTitle")
        self.dashboard_detected_list = QListWidget()
        self.dashboard_detected_list.setMinimumHeight(120)

        actions_title = QLabel("Suggested Actions")
        actions_title.setObjectName("SectionTitle")
        self.dashboard_actions_list = QListWidget()
        self.dashboard_actions_list.setMinimumHeight(130)

        broken_title = QLabel("Broken Links")
        broken_title.setObjectName("SectionTitle")
        self.dashboard_broken_list = QListWidget()
        self.dashboard_broken_list.setMinimumHeight(130)

        activity_title = QLabel("Recent Activity")
        activity_title.setObjectName("SectionTitle")
        self.dashboard_activity_list = QListWidget()
        self.dashboard_activity_list.setMinimumHeight(160)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(counts_row)
        layout.addWidget(detected_title)
        layout.addWidget(self.dashboard_detected_list)
        layout.addWidget(actions_title)
        layout.addWidget(self.dashboard_actions_list)
        layout.addWidget(broken_title)
        layout.addWidget(self.dashboard_broken_list)
        layout.addWidget(activity_title)
        layout.addWidget(self.dashboard_activity_list)
        layout.addStretch(1)
        return page

    def _build_assets_page(self) -> QWidget:
        self.asset_search_edit = QLineEdit()
        self.asset_search_edit.setPlaceholderText("🔍 Search assets, folders, clients, tags...")
        self.asset_search_edit.textChanged.connect(lambda _text: self.refresh_assets())

        import_files_button = QPushButton("📄 Files")
        import_files_button.setToolTip("Import Files")
        import_files_button.clicked.connect(self._import_asset_files)

        import_folder_button = QPushButton("📁 Folder")
        import_folder_button.setToolTip("Import Folder")
        import_folder_button.clicked.connect(self._import_asset_folder)

        new_folder_button = QPushButton("📁 Folder")
        new_folder_button.setToolTip("New Folder")
        new_folder_button.clicked.connect(self._new_asset_folder)

        rename_button = QPushButton("Rename")
        rename_button.clicked.connect(self._rename_selected_asset)

        move_button = QPushButton("Move")
        move_button.clicked.connect(self._move_selected_assets)

        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self._delete_selected_assets)

        open_button = QPushButton("📁 Explorer")
        open_button.setToolTip("Open in Explorer")
        open_button.clicked.connect(self._open_selected_asset_location)

        edit_tags_button = QPushButton("🏷 Tags")
        edit_tags_button.clicked.connect(self._edit_selected_asset_tags)

        title = QLabel("📦 Assets")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Managed EditFlow Asset Library. Drag assets or folders onto project cards to link them.")
        subtitle.setObjectName("MutedLabel")
        self.asset_library_root_label = QLabel()
        self.asset_library_root_label.setObjectName("PathLabel")
        self.asset_library_root_label.setWordWrap(True)

        top_row = QHBoxLayout()
        top_row.addWidget(self.asset_search_edit, 1)
        top_row.addWidget(edit_tags_button)
        top_row.addWidget(open_button)
        top_row.addWidget(rename_button)
        top_row.addWidget(move_button)
        top_row.addWidget(delete_button)
        top_row.addWidget(new_folder_button)
        top_row.addWidget(import_files_button)
        top_row.addWidget(import_folder_button)

        recent_title = QLabel("🕘 Recent Assets")
        recent_title.setObjectName("SectionTitle")
        self.recent_assets_list = AssetListWidget()
        self.recent_assets_list.setMaximumHeight(120)
        self.recent_assets_list.setViewMode(self.recent_assets_list.ViewMode.IconMode)
        self.recent_assets_list.setIconSize(QSize(96, 54))

        most_used_title = QLabel("📌 Most Used Assets")
        most_used_title.setObjectName("SectionTitle")
        self.most_used_assets_list = AssetListWidget()
        self.most_used_assets_list.setMaximumHeight(120)
        self.most_used_assets_list.setViewMode(self.most_used_assets_list.ViewMode.IconMode)
        self.most_used_assets_list.setIconSize(QSize(96, 54))

        self.assets_tree = AssetLibraryTree()
        self.assets_tree.setColumnCount(7)
        self.assets_tree.setHeaderLabels(
            ["Name", "Type", "Client", "Category", "Tags", "Path", "ID"]
        )
        self.assets_tree.setColumnHidden(6, True)
        self.assets_tree.setEditTriggers(AssetLibraryTree.EditTrigger.NoEditTriggers)
        self.assets_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.assets_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.assets_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.assets_tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.assets_tree.header().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.assets_tree.header().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.assets_tree.assets_moved.connect(self._move_assets_to_folder)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        layout.addLayout(top_row)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.asset_library_root_label)
        layout.addWidget(self.assets_tree, 1)
        layout.addWidget(recent_title)
        layout.addWidget(self.recent_assets_list)
        layout.addWidget(most_used_title)
        layout.addWidget(self.most_used_assets_list)
        return page

    def _build_clients_page(self) -> QWidget:
        title = QLabel("👤 Clients")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Client profiles collect project counts, recent assets, exports, and default folders.")
        subtitle.setObjectName("MutedLabel")

        set_asset_button = QPushButton("📦 Asset Folder")
        set_asset_button.clicked.connect(self._set_client_asset_folder)
        set_export_button = QPushButton("📁 Export Folder")
        set_export_button.clicked.connect(self._set_client_export_folder)

        actions = QHBoxLayout()
        actions.addWidget(set_asset_button)
        actions.addWidget(set_export_button)
        actions.addStretch(1)

        self.clients_table = QTableWidget(0, 6)
        self.clients_table.setHorizontalHeaderLabels(
            ["Client", "Active", "Done", "Asset Folder", "Export Folder", "ID"]
        )
        self.clients_table.setColumnHidden(5, True)
        self.clients_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.clients_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.clients_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.clients_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.clients_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.clients_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.clients_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.clients_table.itemSelectionChanged.connect(self._refresh_selected_client_details)

        details_title = QLabel("👤 Client Activity")
        details_title.setObjectName("SectionTitle")
        self.client_assets_list = QListWidget()
        self.client_assets_list.setMinimumHeight(100)
        self.client_exports_list = QListWidget()
        self.client_exports_list.setMinimumHeight(100)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(actions)
        layout.addWidget(self.clients_table, 1)
        layout.addWidget(details_title)
        layout.addWidget(QLabel("📦 Most recently used assets"))
        layout.addWidget(self.client_assets_list)
        layout.addWidget(QLabel("Recent exports"))
        layout.addWidget(self.client_exports_list)
        return page

    def _build_settings_page(self) -> QWidget:
        title = QLabel("⚙ Settings")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Local data only. No cloud sync, accounts, calendars, or analytics.")
        subtitle.setObjectName("MutedLabel")

        backup_button = QPushButton("💾 Backup Data")
        backup_button.setObjectName("PrimaryButton")
        backup_button.clicked.connect(self._backup_data)

        workflow_title = QLabel("📁 Folder Workflow")
        workflow_title.setObjectName("SectionTitle")
        self.projects_root_label = QLabel()
        self.projects_root_label.setObjectName("PathLabel")
        self.projects_root_label.setWordWrap(True)
        choose_root_button = QPushButton("📁 Root")
        choose_root_button.setToolTip("Choose Projects Root")
        choose_root_button.clicked.connect(self._choose_projects_root)

        self.settings_asset_library_root_label = QLabel()
        self.settings_asset_library_root_label.setObjectName("PathLabel")
        self.settings_asset_library_root_label.setWordWrap(True)
        choose_asset_root_button = QPushButton("📦 Assets")
        choose_asset_root_button.setToolTip("Choose Asset Library Root")
        choose_asset_root_button.clicked.connect(self._choose_asset_library_root)

        self.raw_mode_combo = QComboBox()
        self.raw_mode_combo.addItem("Copy raw video into project folder", "copy")
        self.raw_mode_combo.addItem("Move raw video into project folder", "move")
        self.raw_mode_combo.currentIndexChanged.connect(
            lambda _index: self._save_file_mode_settings()
        )

        self.edited_mode_combo = QComboBox()
        self.edited_mode_combo.addItem("Copy edited video into project folder", "copy")
        self.edited_mode_combo.addItem("Move edited video into project folder", "move")
        self.edited_mode_combo.currentIndexChanged.connect(
            lambda _index: self._save_file_mode_settings()
        )

        earnings_title = QLabel("Earnings Rates")
        earnings_title.setObjectName("SectionTitle")
        self.ugc_rate_spin = self._earning_rate_spin()
        self.ugc_rate_spin.editingFinished.connect(self._save_earning_rate_settings)
        self.personal_brand_rate_spin = self._earning_rate_spin()
        self.personal_brand_rate_spin.editingFinished.connect(
            self._save_earning_rate_settings
        )

        ugc_rate_row = QHBoxLayout()
        ugc_rate_row.addWidget(QLabel("UGC"))
        ugc_rate_row.addWidget(self.ugc_rate_spin)
        ugc_rate_row.addStretch(1)

        personal_brand_rate_row = QHBoxLayout()
        personal_brand_rate_row.addWidget(QLabel("Personal Brand"))
        personal_brand_rate_row.addWidget(self.personal_brand_rate_spin)
        personal_brand_rate_row.addStretch(1)

        root_row = QHBoxLayout()
        root_row.addWidget(self.projects_root_label, 1)
        root_row.addWidget(choose_root_button)

        asset_root_row = QHBoxLayout()
        asset_root_row.addWidget(self.settings_asset_library_root_label, 1)
        asset_root_row.addWidget(choose_asset_root_button)

        watched_title = QLabel("👀 Watched Folders")
        watched_title.setObjectName("SectionTitle")
        self.watched_type_combo = QComboBox()
        for value, label in FOLDER_TYPES:
            self.watched_type_combo.addItem(label, value)
        add_watched_button = QPushButton("📁 Folder")
        add_watched_button.clicked.connect(self._add_watched_folder)
        remove_watched_button = QPushButton("Remove")
        remove_watched_button.setToolTip("Remove Selected")
        remove_watched_button.clicked.connect(self._remove_watched_folder)

        watched_actions = QHBoxLayout()
        watched_actions.addWidget(self.watched_type_combo)
        watched_actions.addWidget(add_watched_button)
        watched_actions.addWidget(remove_watched_button)
        watched_actions.addStretch(1)

        self.watched_table = QTableWidget(0, 4)
        self.watched_table.setHorizontalHeaderLabels(["Type", "Folder", "Enabled", "ID"])
        self.watched_table.setColumnHidden(3, True)
        self.watched_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.watched_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.watched_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.watched_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.watched_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        activity_title = QLabel("Recent Activity")
        activity_title.setObjectName("SectionTitle")
        self.activity_list = QListWidget()
        self.activity_list.setMinimumHeight(160)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(backup_button)
        layout.addSpacing(8)
        layout.addWidget(workflow_title)
        layout.addWidget(QLabel("Projects root"))
        layout.addLayout(root_row)
        layout.addWidget(QLabel("Asset Library root"))
        layout.addLayout(asset_root_row)
        layout.addWidget(QLabel("Raw video import mode"))
        layout.addWidget(self.raw_mode_combo)
        layout.addWidget(QLabel("Edited video import mode"))
        layout.addWidget(self.edited_mode_combo)
        layout.addSpacing(8)
        layout.addWidget(earnings_title)
        layout.addLayout(ugc_rate_row)
        layout.addLayout(personal_brand_rate_row)
        layout.addSpacing(8)
        layout.addWidget(watched_title)
        layout.addLayout(watched_actions)
        layout.addWidget(self.watched_table)
        layout.addSpacing(8)
        layout.addWidget(activity_title)
        layout.addWidget(self.activity_list)
        layout.addStretch(1)
        return page

    def _show_view(self, view_name: str) -> None:
        message = {
            "dashboard": "Loading dashboard...",
            "assets": "Loading assets...",
            "publish": "Loading published projects...",
            "archive": "Loading published projects...",
            "clients": "Loading clients...",
            "settings": "Loading settings...",
        }.get(view_name, "Loading projects...")

        def switch_view() -> None:
            if view_name == "dashboard":
                self.stack.setCurrentWidget(self.dashboard_page)
                self.refresh_dashboard()
            elif view_name == "assets":
                self.stack.setCurrentWidget(self.assets_page)
                self.refresh_assets()
            elif view_name in {"publish", "archive"}:
                self.stack.setCurrentWidget(self.publish_page)
                self.refresh_publish(allow_earnings_animation=True)
            elif view_name == "clients":
                self.stack.setCurrentWidget(self.clients_page)
                self.refresh_clients()
            elif view_name == "settings":
                self.stack.setCurrentWidget(self.settings_page)
                self.refresh_workflow_settings()
                self.refresh_watched_folders()
                self.refresh_activity()
            else:
                self.stack.setCurrentWidget(self.projects_page)
                self.refresh_projects()
            self._update_details_overlay_visibility()

        self._run_inline_with_loading(message, switch_view)

    def _earning_rate_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setMinimum(0.0)
        spin.setMaximum(100000.0)
        spin.setSingleStep(0.25)
        spin.setPrefix("$")
        spin.setFixedWidth(110)
        return spin

    def _play_sound(self, name: str) -> None:
        self.sound_effects.play(name)

    def _schedule_project_refresh(self) -> None:
        self.project_refresh_timer.start()

    def refresh_projects(self) -> None:
        self._sync_client_filter()
        projects = self.project_service.list_projects(
            self.project_search_edit.text(),
            client=self._current_client_filter(),
            status=self._current_status_filter(),
        )
        self.project_cache = {
            project.id: project
            for project in projects
            if project.id is not None
        }
        ids = set(self.project_cache)
        if self.selected_project_id not in ids:
            self.selected_project_id = None
            self.details.set_project(None, [], [], [], [])
            self._update_details_overlay_visibility()
            self._watch_project_folder(None)
        else:
            selected = self.project_cache[self.selected_project_id]
            self._set_selected_project_details(selected)

        self.board.set_projects(projects, self.selected_project_id)

    def refresh_dashboard(self) -> None:
        if not hasattr(self, "dashboard_count_labels"):
            return

        counts = self.project_service.get_dashboard_counts()
        for status, label in self.dashboard_count_labels.items():
            label.setText(str(counts.get(status, 0)))

        self.dashboard_detected_list.clear()
        detections = self.project_service.list_recent_detections(limit=8)
        if not detections:
            self._add_muted_item(self.dashboard_detected_list, "No recent detections")
        for detection in detections:
            item = QListWidgetItem(self._detection_label(detection))
            item.setToolTip(str(detection.file_path))
            self.dashboard_detected_list.addItem(item)

        self.dashboard_actions_list.clear()
        actions = self.project_service.list_dashboard_suggestions(limit=10)
        if not actions:
            self._add_muted_item(self.dashboard_actions_list, "No suggested actions")
        for suggestion in actions:
            text = f"{suggestion.project_name}\n{suggestion.message}"
            if suggestion.target_status:
                text = f"{text}\nTarget: {suggestion.target_status}"
            item = QListWidgetItem(text)
            item.setToolTip(suggestion.severity)
            self.dashboard_actions_list.addItem(item)

        self.dashboard_broken_list.clear()
        issues = self.project_service.list_broken_links(limit=10)
        if not issues:
            self._add_muted_item(self.dashboard_broken_list, "No broken links")
        for issue in issues:
            item = QListWidgetItem(f"{issue.label}\n{issue.path}")
            item.setToolTip(issue.kind)
            self.dashboard_broken_list.addItem(item)

        self.dashboard_activity_list.clear()
        for entry in self.project_service.list_activity(limit=12):
            timestamp = entry.created_at[11:16] if len(entry.created_at) >= 16 else ""
            label = f"{timestamp}  {entry.message}" if timestamp else entry.message
            item = QListWidgetItem(label)
            item.setToolTip(entry.event_type)
            self.dashboard_activity_list.addItem(item)

    def refresh_assets(self) -> None:
        self.project_service.sync_asset_library()
        asset_root = self.project_service.asset_library_root()
        if hasattr(self, "asset_library_root_label"):
            self.asset_library_root_label.setText(f"Asset Library: {asset_root}")
        if hasattr(self, "assets_tree"):
            self.assets_tree.set_root_path(asset_root)

        assets = self.project_service.list_assets(self.asset_search_edit.text())
        asset_ids = [asset.id for asset in assets if asset.id is not None]
        tag_map = self.project_service.list_asset_tag_map(asset_ids)
        if hasattr(self, "assets_tree"):
            self._populate_asset_tree(assets, tag_map, asset_root)

        self._refresh_recent_assets()
        self._refresh_most_used_assets()

    def _populate_asset_tree(
        self,
        assets: list[Asset],
        tag_map: dict[int, list[str]],
        asset_root: Path,
    ) -> None:
        self.assets_tree.clear()
        items_by_path: dict[str, QTreeWidgetItem] = {}
        filtered_ids = {asset.id for asset in assets if asset.id is not None}
        show_filtered = bool(self.asset_search_edit.text().strip())

        all_assets = self.project_service.list_assets("")
        assets_by_path = {
            normalize_file_path(asset.file_path): asset
            for asset in all_assets
            if asset.id is not None and _is_under_path(asset.file_path, asset_root)
        }
        ordered_assets = sorted(
            assets_by_path.values(),
            key=lambda asset: (
                len(asset.file_path.parts),
                not asset.is_collection,
                str(asset.file_path).casefold(),
            ),
        )

        for asset in ordered_assets:
            if show_filtered and asset.id not in filtered_ids:
                continue
            parent_item = None
            parent_path = asset.file_path.parent
            while parent_path != asset_root and parent_path != parent_path.parent:
                parent_key = normalize_file_path(parent_path)
                parent_item = items_by_path.get(parent_key)
                if parent_item is not None:
                    break
                parent_path = parent_path.parent

            values = [
                asset.name,
                "Folder" if asset.is_collection else "File",
                asset.client,
                asset.category,
                ", ".join(tag_map.get(asset.id or 0, [])),
                str(asset.file_path),
                str(asset.id or ""),
            ]
            item = QTreeWidgetItem(values)
            item.setData(0, ASSET_ID_ROLE, asset.id)
            item.setData(0, ASSET_PATH_ROLE, str(asset.file_path))
            item.setData(0, ASSET_TYPE_ROLE, asset.asset_type)
            if asset.is_collection:
                item.setText(0, f"📁 {asset.name}")
            else:
                cached = self.thumbnail_provider.cached_thumbnail(asset.file_path)
                if cached is not None:
                    item.setIcon(0, QIcon(str(cached)))
                self.thumbnail_provider.request(asset.file_path)

            if parent_item is None:
                self.assets_tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            items_by_path[normalize_file_path(asset.file_path)] = item

        self.assets_tree.expandToDepth(1)

    def _refresh_recent_assets(self) -> None:
        self.recent_assets_list.clear()
        for asset in self.project_service.get_recent_assets():
            widget_item = QListWidgetItem(asset.name)
            widget_item.setToolTip(str(asset.file_path))
            widget_item.setData(Qt.ItemDataRole.UserRole, asset.id)
            cached = self.thumbnail_provider.cached_thumbnail(asset.file_path)
            if cached is not None:
                widget_item.setIcon(QIcon(str(cached)))
            self.recent_assets_list.addItem(widget_item)
            self.thumbnail_provider.request(asset.file_path)

    def _refresh_most_used_assets(self) -> None:
        self.most_used_assets_list.clear()
        for asset, usage_count in self.project_service.get_most_used_assets():
            widget_item = QListWidgetItem(f"{asset.name}\nUsed {usage_count}x")
            widget_item.setToolTip(str(asset.file_path))
            widget_item.setData(Qt.ItemDataRole.UserRole, asset.id)
            cached = self.thumbnail_provider.cached_thumbnail(asset.file_path)
            if cached is not None:
                widget_item.setIcon(QIcon(str(cached)))
            self.most_used_assets_list.addItem(widget_item)
            self.thumbnail_provider.request(asset.file_path)

    def animate_total_earnings(
        self,
        start_amount: float,
        end_amount: float,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        if not hasattr(self, "publish_total_earnings_label"):
            if on_finished is not None:
                on_finished()
            return

        start_value = float(start_amount)
        end_value = float(end_amount)
        if self._total_earnings_count_animation is not None:
            self._total_earnings_count_animation.stop()
            self._total_earnings_count_animation.deleteLater()
            self._total_earnings_count_animation = None

        if round(start_value, 2) == round(end_value, 2):
            self.publish_total_earnings_label.setText(f"Total ${end_value:.2f}")
            if on_finished is not None:
                on_finished()
            return

        animation = QVariantAnimation(self)
        animation.setStartValue(start_value)
        animation.setEndValue(end_value)
        animation.setDuration(850)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def update_total(value: object) -> None:
            self.publish_total_earnings_label.setText(f"Total ${float(value):.2f}")

        def finish_total() -> None:
            self.publish_total_earnings_label.setText(f"Total ${end_value:.2f}")
            self._total_earnings_count_animation = None
            if on_finished is not None:
                on_finished()

        animation.valueChanged.connect(update_total)
        animation.finished.connect(finish_total)
        self._total_earnings_count_animation = animation
        update_total(start_value)
        animation.start()

    def _published_earnings_state(
        self,
        projects: list[Project],
    ) -> tuple[float, set[str]]:
        total = sum(project.earned_amount for project in projects)
        signatures = {
            self._published_earning_signature(project)
            for project in projects
            if project.id is not None
        }
        return total, signatures

    def _published_earning_signature(self, project: Project) -> str:
        earned_at = project.earned_at or project.updated_at or project.created_at or ""
        return f"{project.id}:{earned_at}"

    def _load_seen_published_earnings_state(self) -> tuple[float, set[str]] | None:
        raw_state = self.project_service.get_setting("published_earnings_seen_state", "")
        if not raw_state:
            return None
        try:
            state = json.loads(raw_state)
        except (TypeError, ValueError):
            return None

        try:
            total = float(state.get("total", 0.0))
        except (TypeError, ValueError, AttributeError):
            total = 0.0
        signatures = state.get("signatures", []) if isinstance(state, dict) else []
        if not isinstance(signatures, list):
            signatures = []
        return total, {str(signature) for signature in signatures}

    def _save_seen_published_earnings_state(
        self,
        total: float,
        signatures: set[str],
    ) -> None:
        state = {
            "total": round(float(total), 2),
            "signatures": sorted(signatures),
        }
        self.project_service.set_setting(
            "published_earnings_seen_state",
            json.dumps(state, separators=(",", ":")),
        )

    def _mark_current_published_earnings_seen(self) -> None:
        projects = self.project_service.list_published_projects()
        total, signatures = self._published_earnings_state(projects)
        self._save_seen_published_earnings_state(total, signatures)

    def _ensure_published_earnings_seen_baseline(self) -> None:
        if self._load_seen_published_earnings_state() is None:
            self._mark_current_published_earnings_seen()

    def _update_publish_total_earnings(
        self,
        published_projects: list[Project],
        *,
        allow_animation: bool,
    ) -> None:
        current_total, current_signatures = self._published_earnings_state(
            published_projects
        )
        seen_state = self._load_seen_published_earnings_state()

        if not allow_animation:
            if self._total_earnings_count_animation is None:
                self.publish_total_earnings_label.setText(f"Total ${current_total:.2f}")
            return

        if seen_state is None:
            self.publish_total_earnings_label.setText(f"Total ${current_total:.2f}")
            self._save_seen_published_earnings_state(
                current_total,
                current_signatures,
            )
            return

        seen_total, seen_signatures = seen_state
        has_unseen_publish = bool(current_signatures - seen_signatures)
        if has_unseen_publish:
            self.animate_total_earnings(
                seen_total,
                current_total,
                lambda: self._save_seen_published_earnings_state(
                    current_total,
                    current_signatures,
                ),
            )
            return

        if self._total_earnings_count_animation is None:
            self.publish_total_earnings_label.setText(f"Total ${current_total:.2f}")
        self._save_seen_published_earnings_state(current_total, current_signatures)

    def refresh_publish(self, *, allow_earnings_animation: bool = False) -> None:
        if not hasattr(self, "publish_table"):
            return
        projects = self.project_service.list_published_projects(
            self.publish_search_edit.text()
        )
        total_projects = self.project_service.list_published_projects()
        self._update_publish_total_earnings(
            total_projects,
            allow_animation=allow_earnings_animation,
        )
        self.publish_table.setRowCount(len(projects))
        for row, project in enumerate(projects):
            values = [
                project.name,
                project.client,
                workflow_stage_label(project.status),
                self._earned_payment_text(project),
                project.raw_video_name,
                project.edited_video_name,
                str(project.folder_path),
                str(project.id or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, project)
                if column == 3 and project.earned_amount > 0:
                    item.setForeground(QColor("#35d07f"))
                self.publish_table.setItem(row, column, item)
        if projects:
            self.publish_table.selectRow(0)

    def _open_published_root(self) -> None:
        self._open_path(self.project_service.projects_root() / PUBLISHED_STATUS)

    def _show_published_table_context_menu(self, position: QPoint) -> None:
        item = self.publish_table.itemAt(position)
        if item is None:
            return
        self.publish_table.selectRow(item.row())
        project = self._selected_published_project()
        if project is None:
            return

        menu = QMenu(self.publish_table)
        restore_action = menu.addAction("↩ Restore to Done")
        selected_action = menu.exec(self.publish_table.viewport().mapToGlobal(position))
        if selected_action == restore_action:
            self._restore_selected_published_project()

    def _earned_payment_text(self, project: Project) -> str:
        if project.earned_amount <= 0:
            return ""
        return f"${project.earned_amount:.2f}"

    def _selected_published_project(self) -> Project | None:
        row = self.publish_table.currentRow()
        if row < 0:
            return None
        id_item = self.publish_table.item(row, 7)
        if id_item is None or not id_item.text():
            return None
        try:
            project_id = int(id_item.text())
        except ValueError:
            return None
        project = self.project_service.get_project(project_id)
        if project is None or project.status != PUBLISHED_STATUS:
            return None
        return project

    def refresh_clients(self) -> None:
        if not hasattr(self, "clients_table"):
            return
        summaries = self.project_service.list_client_summaries()
        self.clients_table.blockSignals(True)
        self.clients_table.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            client = summary.client
            values = [
                client.name,
                str(summary.active_projects),
                str(summary.completed_projects),
                str(client.default_asset_folder or ""),
                str(client.default_export_folder or ""),
                str(client.id or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, summary)
                self.clients_table.setItem(row, column, item)
        self.clients_table.blockSignals(False)
        if summaries and self.selected_client_id is None:
            self.clients_table.selectRow(0)
        else:
            self._restore_selected_client_row()
        self._refresh_selected_client_details()

    def _restore_selected_client_row(self) -> None:
        if self.selected_client_id is None:
            return
        for row in range(self.clients_table.rowCount()):
            item = self.clients_table.item(row, 5)
            if item is not None and item.text() == str(self.selected_client_id):
                self.clients_table.selectRow(row)
                return

    def _selected_client_id(self) -> int | None:
        row = self.clients_table.currentRow()
        if row < 0:
            return None
        item = self.clients_table.item(row, 5)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def _refresh_selected_client_details(self) -> None:
        if not hasattr(self, "client_assets_list"):
            return
        client_id = self._selected_client_id()
        self.selected_client_id = client_id
        self.client_assets_list.clear()
        self.client_exports_list.clear()
        if client_id is None:
            return

        for asset in self.project_service.get_client_recent_assets(client_id):
            item = QListWidgetItem(asset.name)
            item.setToolTip(str(asset.file_path))
            self.client_assets_list.addItem(item)

        for export in self.project_service.get_client_recent_exports(client_id):
            item = QListWidgetItem(f"{export.version_label.upper()}  {export.filename}")
            item.setToolTip(str(export.file_path))
            self.client_exports_list.addItem(item)

    def _set_client_asset_folder(self) -> None:
        client_id = self._selected_client_id()
        if client_id is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose Default Asset Folder")
        if not folder:
            return
        try:
            self.project_service.update_client_defaults(
                client_id,
                default_asset_folder=Path(folder),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Save Client Folder", str(error))
            return
        self.refresh_clients()
        self.refresh_activity()

    def _set_client_export_folder(self) -> None:
        client_id = self._selected_client_id()
        if client_id is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose Default Export Folder")
        if not folder:
            return
        try:
            self.project_service.update_client_defaults(
                client_id,
                default_export_folder=Path(folder),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Save Client Folder", str(error))
            return
        self.refresh_clients()
        self.refresh_activity()

    def refresh_watched_folders(self) -> None:
        folders = self.project_service.list_watched_folders()
        self.watched_table.setRowCount(len(folders))
        for row, folder in enumerate(folders):
            values = [
                folder.label,
                str(folder.path),
                "Yes" if folder.enabled else "No",
                str(folder.id or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, folder)
                self.watched_table.setItem(row, column, item)
        self._refresh_watched_folder_watcher(folders)

    def refresh_workflow_settings(self) -> None:
        if not hasattr(self, "projects_root_label"):
            return
        self.projects_root_label.setText(str(self.project_service.projects_root()))
        self.settings_asset_library_root_label.setText(
            str(self.project_service.asset_library_root())
        )
        self.raw_mode_combo.blockSignals(True)
        self.edited_mode_combo.blockSignals(True)
        self.raw_mode_combo.setCurrentIndex(
            max(0, self.raw_mode_combo.findData(self.project_service.raw_file_mode()))
        )
        self.edited_mode_combo.setCurrentIndex(
            max(0, self.edited_mode_combo.findData(self.project_service.edited_file_mode()))
        )
        self.raw_mode_combo.blockSignals(False)
        self.edited_mode_combo.blockSignals(False)
        if hasattr(self, "ugc_rate_spin"):
            rates = self.project_service.earning_rates()
            self.ugc_rate_spin.setValue(rates["ugc"])
            self.personal_brand_rate_spin.setValue(rates["personal_brand"])

    def _choose_projects_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Projects Root",
            str(self.project_service.projects_root()),
        )
        if not folder:
            return
        try:
            self.project_service.update_file_workflow_settings(
                projects_root=Path(folder),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Save Projects Root", str(error))
            return
        self.refresh_workflow_settings()
        self.refresh_activity()

    def _choose_asset_library_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Asset Library Root",
            str(self.project_service.asset_library_root()),
        )
        if not folder:
            return
        try:
            self.project_service.update_file_workflow_settings(
                asset_library_root=Path(folder),
            )
            self.project_service.sync_asset_library()
        except Exception as error:
            QMessageBox.critical(self, "Could Not Save Asset Library Root", str(error))
            return
        self.refresh_workflow_settings()
        self.refresh_assets()
        self.refresh_activity()

    def _save_file_mode_settings(self) -> None:
        if not hasattr(self, "raw_mode_combo"):
            return
        raw_mode = str(self.raw_mode_combo.currentData() or "copy")
        edited_mode = str(self.edited_mode_combo.currentData() or "copy")
        raw_will_move = self.project_service.raw_file_mode() != "move" and raw_mode == "move"
        edited_will_move = (
            self.project_service.edited_file_mode() != "move"
            and edited_mode == "move"
        )
        if raw_will_move or edited_will_move:
            answer = QMessageBox.question(
                self,
                "Confirm Move Mode",
                "Move mode removes the original file after placing it in the project folder. Use Move mode?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.refresh_workflow_settings()
                return
        try:
            self.project_service.update_file_workflow_settings(
                raw_file_mode=raw_mode,
                edited_file_mode=edited_mode,
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Save File Mode", str(error))
            self.refresh_workflow_settings()
            return

    def _save_earning_rate_settings(self) -> None:
        if not hasattr(self, "ugc_rate_spin"):
            return
        try:
            self.project_service.update_earning_rates(
                ugc_rate=self.ugc_rate_spin.value(),
                personal_brand_rate=self.personal_brand_rate_spin.value(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Save Earnings Rates", str(error))
            self.refresh_workflow_settings()
            return
        self.refresh_projects()
        self.refresh_publish()
        self.refresh_activity()

    def refresh_activity(self) -> None:
        if not hasattr(self, "activity_list"):
            return
        self.activity_list.clear()
        for entry in self.project_service.list_activity(limit=30):
            timestamp = entry.created_at[11:16] if len(entry.created_at) >= 16 else ""
            label = f"{timestamp}  {entry.message}" if timestamp else entry.message
            item = QListWidgetItem(label)
            item.setToolTip(entry.event_type)
            self.activity_list.addItem(item)
        self.refresh_dashboard()

    def _add_muted_item(self, list_widget: QListWidget, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        list_widget.addItem(item)

    def _detection_label(self, detection: DetectionEntry) -> str:
        type_label = {
            "asset": "Asset",
            "export": "Export",
            "raw": "Raw video",
        }.get(detection.detection_type, detection.detection_type.title())
        timestamp = detection.created_at[:16] if detection.created_at else ""
        text = f"{type_label}: {detection.file_path.name}"
        if detection.status:
            text = f"{text}\nStatus: {detection.status}"
        if timestamp:
            text = f"{text}\n{timestamp}"
        return text

    def _refresh_watched_folder_watcher(self, folders: list[WatchedFolder]) -> None:
        watched = self.watched_folders_watcher.directories()
        if watched:
            self.watched_folders_watcher.removePaths(watched)
        paths = [
            str(folder.path)
            for folder in folders
            if folder.enabled and folder.path.exists() and folder.path.is_dir()
        ]
        if paths:
            self.watched_folders_watcher.addPaths(paths)

    def _add_raw_video(self) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select Raw MP4",
            "",
            "MP4 Videos (*.mp4)",
        )
        if not file_name:
            return

        self._create_project_from_raw_path(Path(file_name), quick_confirm=False)

    def _handle_need_edit_files_dropped(self, paths: list[Path]) -> None:
        mp4_paths = [
            path
            for path in paths
            if path.exists() and path.is_file() and is_mp4_file(path)
        ]
        if not mp4_paths:
            QMessageBox.warning(
                self,
                "No Raw MP4 Found",
                "Drop one or more MP4 files onto Need Edit.",
            )
            return
        for raw_path in mp4_paths:
            self._create_project_from_raw_path(raw_path, quick_confirm=True)

    def _create_project_from_raw_path(
        self,
        raw_path: Path,
        *,
        quick_confirm: bool,
    ) -> None:
        dialog = self._raw_video_dialog(raw_path)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        values = dialog.values()
        self._run_background(
            "Adding raw video...",
            lambda: self.project_service.create_project_from_raw(
                raw_video_path=raw_path,
                name=values["name"],
                client=values["client"],
                video_type=values["video_type"],
            ),
            self._raw_video_added,
            "Could Not Add Raw Video",
        )

    def _raw_video_added(self, result: object) -> None:
        project = result if isinstance(result, Project) else None
        if project is None:
            return
        if project.id is None:
            return
        self.selected_project_id = project.id
        self.stack.setCurrentWidget(self.projects_page)
        self._clear_project_filters_if_hidden(project)
        self.project_cache[project.id] = project
        self.board.update_project(project, self.selected_project_id)
        self.board.updateGeometry()
        self.board.update()
        self._set_selected_project_details(project)
        self.refresh_projects()
        QTimer.singleShot(0, self.refresh_projects)
        self.refresh_clients()
        self.refresh_activity()

    def _clear_project_filters_if_hidden(self, project: Project) -> None:
        if self._project_matches_current_filters(project):
            return
        self.project_search_edit.blockSignals(True)
        self.client_filter_combo.blockSignals(True)
        self.project_search_edit.clear()
        self.client_filter_combo.setCurrentIndex(0)
        self.project_search_edit.blockSignals(False)
        self.client_filter_combo.blockSignals(False)

    def _raw_video_dialog(
        self,
        raw_path: Path,
    ) -> AddRawVideoDialog:
        return AddRawVideoDialog(
            raw_path,
            self,
            suggested_name=raw_path.stem,
            client_options=self.project_service.list_project_clients(),
        )

    def _select_project(self, project: Project) -> None:
        self.selected_project_id = project.id
        self._set_selected_project_details(project)
        self.board.set_selected_project(self.selected_project_id)
        self._scan_project_folder(project)

    def _set_selected_project_details(self, project: Project) -> None:
        self.project_service.ensure_project_structure(project)
        assets = self.project_service.get_project_assets(project.id or 0)
        exports = self.project_service.list_project_exports(project.id or 0)
        issues = self.project_service.list_file_issues(project)
        suggestions = self.project_service.suggest_assets_for_project(project)
        revisions = self.project_service.list_project_revisions(project.id or 0)
        workflow_suggestions = self.project_service.list_workflow_suggestions(project)
        timeline = self.project_service.list_project_timeline(project.id or 0)
        self._log_new_file_issues(project, issues)
        self.details.set_project(
            project,
            assets,
            exports,
            issues,
            suggestions,
            revisions,
            workflow_suggestions,
            timeline,
        )
        self._update_details_overlay_visibility()
        self._watch_project_folder(project)

    def _change_status(self, project: Project, status: str) -> None:
        block_reason = workflow_move_block_reason(project, status)
        if block_reason:
            self._show_move_blocked(block_reason)
            self._set_selected_project_details(project)
            return
        try:
            updated = self.project_service.update_status(project, status)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Change Status", str(error))
            self._set_selected_project_details(project)
            return
        self._after_project_changed(updated)

    def _drop_project_status(self, project_id: int, status: str) -> None:
        existing = self.project_cache.get(project_id) or self.project_service.get_project(project_id)
        if existing is None:
            return
        block_reason = workflow_move_block_reason(existing, status)
        if block_reason:
            self._show_move_blocked(block_reason)
            return
        previous_status = existing.status if existing is not None else ""
        try:
            updated = self._run_inline_with_loading(
                "Saving card move...",
                lambda: self.project_service.update_status_by_id(project_id, status),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Move Card", str(error))
            return
        self._after_project_changed(updated)
        if previous_status and previous_status != status and updated.id is not None:
            self._play_sound("drop_card")
            self.board.play_drop_confirmation(updated.id)

    def _drop_project_ordered(
        self,
        project_id: int,
        status: str,
        target_index: int,
    ) -> None:
        existing = self.project_cache.get(project_id) or self.project_service.get_project(project_id)
        if existing is None:
            return
        block_reason = workflow_move_block_reason(existing, status)
        if block_reason:
            self._show_move_blocked(block_reason)
            return
        try:
            updated = self._run_inline_with_loading(
                "Saving card position...",
                lambda: self.project_service.move_project_to_kanban_position(
                    project_id,
                    status,
                    target_index,
                ),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Move Card", str(error))
            return

        self.refresh_projects()
        self.refresh_publish()
        self.refresh_clients()
        self.refresh_activity()
        if updated.id is not None:
            self._play_sound("drop_card")
            self.board.play_drop_confirmation(updated.id)

    def _show_move_blocked(self, reason: str) -> None:
        self._show_toast(f"Move blocked: {reason}")

    def _publish_project(self, project: Project) -> None:
        if project.status != "Done":
            QMessageBox.information(
                self,
                "Not Ready to Publish",
                "Only projects in the Done column can be marked as published.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Mark as Published",
            (
                f"Mark {project.name} as published?\n\n"
                "EditFlow will move the entire project folder into "
                "EditFlow Projects/Published and update saved paths. "
                "No project files will be deleted."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            updated = self.project_service.publish_project(project.id or 0)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Publish Project", str(error))
            return
        self._play_sound("publish")
        self._queue_earnings_animation(updated.earned_amount)
        self._after_project_changed(updated)
        self.refresh_publish()
        self.refresh_dashboard()

    def _remove_project(self, project: Project) -> None:
        if project.id is None:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Remove Project")
        box.setText(f"Remove {project.name} from EditFlow?")
        box.setInformativeText(
            "Choose the safe default to keep every project file on disk.\n\n"
            "Only choose file deletion if you want to permanently delete the "
            "project folder."
        )
        box.setDetailedText(f"Project folder:\n{project.folder_path}")
        keep_button = box.addButton(
            "Remove from EditFlow only",
            QMessageBox.ButtonRole.AcceptRole,
        )
        delete_button = box.addButton(
            "Remove and delete project files",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep_button)
        box.setEscapeButton(cancel_button)
        box.exec()

        clicked_button = box.clickedButton()
        if clicked_button is None or clicked_button == cancel_button:
            return

        delete_files = clicked_button == delete_button
        try:
            removed = self.project_service.remove_project(
                project.id,
                delete_files=delete_files,
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Remove Project", str(error))
            return

        if removed.id is not None:
            self.project_cache.pop(removed.id, None)
            self.board.remove_project(removed.id)
            if self.selected_project_id == removed.id:
                self.selected_project_id = None
                self.details.set_project(None, [], [], [], [])
                self._update_details_overlay_visibility()
                self._watch_project_folder(None)

        self.refresh_projects()
        self.refresh_publish()
        if removed.status == PUBLISHED_STATUS:
            self._mark_current_published_earnings_seen()
        self.refresh_dashboard()
        self.refresh_clients()
        self.refresh_assets()
        self.refresh_activity()

    def _restore_selected_published_project(self) -> None:
        project = self._selected_published_project()
        if project is None:
            QMessageBox.information(
                self,
                "Select Published Project",
                "Select a published project to restore.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Restore Project",
            (
                f"Restore {project.name} to Done?\n\n"
                "EditFlow will move the project folder back to the normal "
                "EditFlow Projects root and update saved paths."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            restored = self.project_service.restore_published_project(project.id or 0)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Restore Project", str(error))
            return

        self.refresh_publish()
        self._mark_current_published_earnings_seen()
        self.refresh_projects()
        self.refresh_dashboard()
        self.refresh_clients()
        self.refresh_activity()
        self.statusBar().showMessage(f"Restored {restored.name} to Done", 3500)

    def _open_selected_published_folder(self) -> None:
        project = self._selected_published_project()
        if project is None:
            QMessageBox.information(
                self,
                "Select Published Project",
                "Select a published project first.",
            )
            return
        self._open_project_folder(project)

    def _preview_video(self, project: Project, video_kind: str) -> None:
        if project.id is not None:
            project = self.project_service.get_project(project.id) or project
        video_path = self._project_video_path(project, video_kind)
        if video_path is None:
            QMessageBox.information(
                self,
                "No Video Selected",
                f"This project does not have a {video_kind} video linked.",
            )
            return
        if not video_path.exists() or not video_path.is_file():
            self._play_sound("warning")
            QMessageBox.warning(
                self,
                "Video Not Found",
                f"This video could not be found:\n{video_path}",
            )
            return
        dialog = VideoPreviewDialog(video_path, video_kind=video_kind, parent=self)
        dialog.replace_requested.connect(
            lambda kind: self._replace_preview_video_file(dialog, project, kind)
        )
        dialog.remove_requested.connect(
            lambda kind, delete_file: self._remove_preview_video_file(
                dialog,
                project,
                kind,
                delete_file,
            )
        )
        dialog.exec()

    def _project_video_path(self, project: Project, video_kind: str) -> Path | None:
        if video_kind == "raw":
            if not project.raw_video_path.name or not project.raw_video_path.suffix:
                return None
            return project.raw_video_path
        if video_kind == "edited":
            return project.edited_video_path
        return None

    def _replace_preview_video_file(
        self,
        dialog: VideoPreviewDialog,
        project: Project,
        video_kind: str,
    ) -> None:
        current = self.project_service.get_project(project.id or 0) if project.id else project
        if current is None:
            return
        current_path = self._project_video_path(current, video_kind)
        start_folder = str(current_path.parent if current_path is not None else current.folder_path)
        title = "Replace Raw MP4" if video_kind == "raw" else "Replace Edited MP4"
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            title,
            start_folder,
            "MP4 Videos (*.mp4)",
        )
        if not file_name:
            return

        def replace() -> Project:
            if video_kind == "raw":
                return self.project_service.replace_raw_video(current, Path(file_name))
            return self.project_service.replace_edited_video(current, Path(file_name))

        def handle_success(result: object) -> None:
            updated = result if isinstance(result, Project) else None
            if updated is None:
                return
            self._after_project_changed(updated)
            new_path = self._project_video_path(updated, video_kind)
            if new_path is not None and new_path.exists():
                dialog.set_video_path(new_path, autoplay=True)
            self._show_toast("✓ Video file replaced")
            if video_kind == "edited" and updated.status not in {"Need Upload", "Done"}:
                answer = QMessageBox.question(
                    self,
                    "Update Status",
                    "Edited video is set. Move this project to Need Upload?",
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._change_status(updated, "Need Upload")

        self._run_background(
            "Replacing video file...",
            replace,
            handle_success,
            "Could Not Replace Video",
        )

    def _remove_preview_video_file(
        self,
        dialog: VideoPreviewDialog,
        project: Project,
        video_kind: str,
        delete_file: bool,
    ) -> None:
        current = self.project_service.get_project(project.id or 0) if project.id else project
        if current is None:
            return

        def remove() -> Project:
            if video_kind == "raw":
                return self.project_service.remove_raw_video(
                    current,
                    delete_file=delete_file,
                )
            return self.project_service.remove_edited_video(
                current,
                delete_file=delete_file,
            )

        def handle_success(result: object) -> None:
            updated = result if isinstance(result, Project) else None
            if updated is None:
                return
            self._after_project_changed(updated)
            dialog.accept()
            self._show_toast(
                "✓ Video file deleted" if delete_file else "✓ Video unlinked from project"
            )

        self._run_background(
            "Removing video file...",
            remove,
            handle_success,
            "Could Not Remove Video",
        )

    def _toggle_project_priority(self, project: Project) -> None:
        try:
            updated = self.project_service.update_priority(project, not project.priority)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Update Priority", str(error))
            return
        if updated.priority:
            self._play_sound("mark")
        self._after_project_changed(updated)

    def _toggle_project_flow(self, project: Project) -> None:
        try:
            if project.in_flow:
                self.project_service.clear_flow_project(project)
            else:
                self.project_service.set_flow_project(project)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Update Flow Mode", str(error))
            return
        self._play_sound("warning")
        self.refresh_projects()
        self.refresh_activity()

    def _select_edited_video(self, project: Project) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select Edited MP4",
            str(project.folder_path),
            "MP4 Videos (*.mp4)",
        )
        if not file_name:
            return
        self._assign_edited_video(project, Path(file_name))

    def _assign_edited_video(self, project: Project, video_path: Path) -> None:
        self._run_background(
            "Setting edited video...",
            lambda: self.project_service.set_edited_video(project, video_path),
            self._edited_video_assigned,
            "Could Not Set Edited Video",
        )

    def _edited_video_assigned(self, result: object) -> None:
        project = result if isinstance(result, Project) else None
        if project is None:
            return
        self._after_project_changed(project)
        if project.status not in {"Need Upload", "Done"}:
            answer = QMessageBox.question(
                self,
                "Update Status",
                "Edited video is set. Move this project to Need Upload?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._change_status(project, "Need Upload")

    def _save_notes(self, project: Project, notes: str) -> None:
        try:
            updated = self.project_service.update_notes(project, notes)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Save Notes", str(error))
            return
        self._after_project_changed(updated)

    def _edit_project_notes(self, project: Project) -> None:
        if project.id is None:
            return
        current = self.project_service.get_project(project.id) or project
        revisions = self.project_service.list_project_revisions(project.id)
        dialog = NotesDialog(current, revisions, self)
        dialog.revision_completion_changed.connect(self._set_project_revision_completed)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        if dialog.notes() != current.notes:
            self._save_notes(current, dialog.notes())
        else:
            refreshed = self.project_service.get_project(project.id)
            if refreshed is not None:
                self._after_project_changed(refreshed)

    def _edit_project_payment_amount(self, project: Project) -> None:
        if project.id is None:
            return
        current = self.project_service.get_project(project.id) or project
        current_amount = (
            current.earned_amount
            if current.status == PUBLISHED_STATUS and current.earned_amount > 0
            else current.estimated_payment
        )
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Edit Earnings Amount")
        dialog.setLabelText(f"Amount for {current.name}:")
        dialog.setInputMode(QInputDialog.InputMode.DoubleInput)
        dialog.setDoubleMinimum(0.0)
        dialog.setDoubleMaximum(1000000.0)
        dialog.setDoubleDecimals(2)
        dialog.setDoubleValue(max(0.0, float(current_amount)))
        dialog.setOkButtonText("Save")
        dialog.setCancelButtonText("Cancel")
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            updated = self.project_service.update_project_payment_amount(
                current,
                dialog.doubleValue(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Update Earnings", str(error))
            return
        self._after_project_changed(updated)
        if updated.status == PUBLISHED_STATUS:
            self._mark_current_published_earnings_seen()

    def _edit_project_video_type(self, project: Project) -> None:
        options = ["UGC", "Personal Brand"]
        current_type = project.video_type.strip()
        current_index = options.index(current_type) if current_type in options else 0
        video_type, accepted = QInputDialog.getItem(
            self,
            "Edit Video Type",
            "Video type:",
            options,
            current_index,
            False,
        )
        if not accepted:
            return
        try:
            updated = self.project_service.update_video_type(project, video_type)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Update Video Type", str(error))
            return
        self._after_project_changed(updated)

    def _add_project_revision(self, project: Project) -> None:
        note = ""
        while True:
            note, accepted = QInputDialog.getMultiLineText(
                self,
                "Add Revision Feedback",
                "Revision note is required:",
                text=note,
            )
            if not accepted:
                return
            if note.strip():
                break
            QMessageBox.warning(
                self,
                "Revision Note Required",
                "Please enter the client revision feedback before saving.",
            )
        try:
            self.project_service.add_project_revision(project, note)
            if project.id is None:
                return
            if project.status != "Editing":
                updated = self.project_service.update_status_by_id(
                    project.id,
                    "Editing",
                )
            else:
                updated = self.project_service.get_project(project.id)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Add Revision", str(error))
            return
        if updated is not None:
            self._after_project_changed(updated)

    def _toggle_project_revision(self, revision: ProjectRevision) -> None:
        self._set_project_revision_completed(revision, not revision.completed)

    def _set_project_revision_completed(
        self,
        revision: ProjectRevision,
        completed: bool,
    ) -> None:
        if revision.id is None:
            return
        try:
            self.project_service.set_revision_completed(revision.id, completed)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Update Revision", str(error))
            return
        project = self.project_service.get_project(revision.project_id)
        if project is not None:
            self._after_project_changed(project)

    def _apply_status_suggestion(
        self,
        project: Project,
        suggestion: WorkflowSuggestion,
    ) -> None:
        if not suggestion.target_status:
            return
        answer = QMessageBox.question(
            self,
            "Apply Status Suggestion",
            f"Move {project.name} to {suggestion.target_status}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._change_status(project, suggestion.target_status)

    def _add_asset(self) -> None:
        dialog = AssetDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            self.project_service.create_asset(**dialog.values())
        except Exception as error:
            QMessageBox.critical(self, "Could Not Add Asset", str(error))
            return
        self.refresh_assets()

    def _import_asset_files(self) -> None:
        file_names, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Import Asset Files",
            "",
            "Assets (*.*)",
        )
        if not file_names:
            return
        try:
            self.project_service.import_asset_files(
                [Path(file_name) for file_name in file_names],
                self.assets_tree.current_folder_path(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Import Assets", str(error))
            return
        self.refresh_assets()
        self.refresh_activity()

    def _import_asset_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Import Asset Folder")
        if not folder:
            return
        path = Path(folder)
        answer = QMessageBox.question(
            self,
            "Import Asset Folder",
            (
                f"Move this folder into the EditFlow Asset Library?\n\n"
                f"{path}\n\n"
                "The complete folder structure will be preserved."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.project_service.import_asset_folder(
                path,
                self.assets_tree.current_folder_path(),
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Import Asset Folder", str(error))
            return
        self.refresh_assets()
        self.refresh_activity()

    def _new_asset_folder(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "New Asset Folder",
            "Folder name:",
        )
        if not accepted or not name.strip():
            return
        try:
            self.project_service.create_asset_folder(
                self.assets_tree.current_folder_path(),
                name,
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Create Folder", str(error))
            return
        self.refresh_assets()
        self.refresh_activity()

    def _rename_selected_asset(self) -> None:
        asset_id = self.assets_tree.current_asset_id()
        if asset_id is None:
            return
        asset = self.project_service.get_asset(asset_id)
        if asset is None:
            return
        current_name = asset.file_path.name if asset.is_collection else asset.file_path.name
        name, accepted = QInputDialog.getText(
            self,
            "Rename Asset",
            "New name:",
            text=current_name,
        )
        if not accepted or not name.strip():
            return
        try:
            self.project_service.rename_asset(asset_id, name)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Rename Asset", str(error))
            return
        self._show_file_warnings()
        self.refresh_assets()
        self.refresh_projects()
        self.refresh_activity()

    def _move_selected_assets(self) -> None:
        asset_ids = self.assets_tree.selected_asset_ids()
        if not asset_ids:
            return
        folders = self.project_service.list_asset_folders()
        labels = ["Asset Library Root"]
        values: list[Path] = [self.project_service.asset_library_root()]
        for folder in folders:
            labels.append(str(folder.file_path.relative_to(self.project_service.asset_library_root())))
            values.append(folder.file_path)
        label, accepted = QInputDialog.getItem(
            self,
            "Move Assets",
            "Move selected assets to:",
            labels,
            0,
            False,
        )
        if not accepted:
            return
        destination = values[labels.index(label)]
        self._move_assets_to_folder(asset_ids, destination)

    def _move_assets_to_folder(self, asset_ids: list[int], destination: Path) -> None:
        if not asset_ids:
            return
        try:
            self.project_service.move_assets(asset_ids, destination)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Move Assets", str(error))
            return
        self._show_file_warnings()
        self.refresh_assets()
        self.refresh_projects()
        self.refresh_activity()

    def _delete_selected_assets(self) -> None:
        asset_ids = self.assets_tree.selected_asset_ids()
        if not asset_ids:
            return
        usage_count = self.project_service.asset_usage_count(asset_ids)
        message = (
            f"Delete {len(asset_ids)} selected asset(s) from the EditFlow Asset Library?\n\n"
            "This deletes the selected library files/folders and removes their SQLite asset records."
        )
        if usage_count:
            message = (
                f"{message}\n\n"
                f"These assets are linked to {usage_count} project(s). "
                "Deleting will remove those project relationships and project-side shortcuts."
            )
        answer = QMessageBox.warning(
            self,
            "Delete Assets",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.project_service.delete_assets(asset_ids)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Delete Assets", str(error))
            return
        self._show_file_warnings()
        self.refresh_assets()
        self.refresh_projects()
        self.refresh_activity()

    def _add_watched_folder(self) -> None:
        folder_type = str(self.watched_type_combo.currentData() or "asset")
        folder = QFileDialog.getExistingDirectory(self, "Choose Watched Folder")
        if not folder:
            return
        try:
            self.project_service.add_watched_folder(Path(folder), folder_type)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Add Watched Folder", str(error))
            return
        self.refresh_watched_folders()
        self.refresh_activity()
        self._schedule_detection_scan()

    def _remove_watched_folder(self) -> None:
        row = self.watched_table.currentRow()
        if row < 0:
            return
        item = self.watched_table.item(row, 3)
        if item is None:
            return
        try:
            folder_id = int(item.text())
        except ValueError:
            return
        self.project_service.remove_watched_folder(folder_id)
        self.refresh_watched_folders()
        self.refresh_activity()

    def _link_asset_from_dialog(self, project: Project) -> None:
        dialog = AssetPickerDialog(
            self.project_service,
            self,
            project=project,
            project_client=project.client,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        selected_ids = dialog.selected_asset_ids()
        linked_ids = {
            asset.id
            for asset in self.project_service.get_project_assets(project.id or 0)
            if asset.id is not None
        }
        already_linked_count = len(
            [asset_id for asset_id in selected_ids if asset_id in linked_ids]
        )
        new_assets = [
            asset
            for asset_id in selected_ids
            if asset_id not in linked_ids
            if (asset := self.project_service.get_asset(asset_id)) is not None
        ]
        if not new_assets:
            self._show_toast("✓ Selected assets are already linked")
            return
        try:
            updated = self.project_service.link_assets(project, new_assets)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Link Assets", str(error))
            return
        self._show_file_warnings()
        self._after_project_changed(updated, refresh_assets=True)
        linked_text = (
            f"Linked {len(new_assets)} asset"
            if len(new_assets) == 1
            else f"Linked {len(new_assets)} assets"
        )
        if already_linked_count:
            linked_text = f"{linked_text} - {already_linked_count} already linked"
        self._show_toast(f"✓ {linked_text}")

    def _link_single_asset_from_dialog(self, project: Project) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose Asset to Link",
            "",
            "Assets (*.*)",
        )
        if not file_name:
            return

        dialog = AssetDialog(Path(file_name), self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        try:
            asset = self.project_service.create_asset(**dialog.values())
            updated = self.project_service.link_asset(project, asset)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Link Asset", str(error))
            return

        self._after_project_changed(updated, refresh_assets=True)

    def _link_suggested_asset(self, project: Project, asset: Asset) -> None:
        if not self._confirm_asset_link(project, asset.name):
            return
        try:
            updated = self.project_service.link_asset(project, asset)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Link Asset", str(error))
            return
        self._show_file_warnings()
        self._after_project_changed(updated, refresh_assets=True)

    def _unlink_asset(self, project: Project, asset: Asset) -> None:
        try:
            updated = self.project_service.unlink_asset(project, asset)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Unlink Asset", str(error))
            return
        self._show_file_warnings()
        self._after_project_changed(updated, refresh_assets=True)

    def _handle_asset_dropped_on_project(self, project: Project, asset_id: int) -> None:
        asset = self.project_service.get_asset(asset_id)
        if asset is None:
            return
        if not self._confirm_asset_link(project, asset.name):
            return
        try:
            updated = self.project_service.link_asset(project, asset)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Link Asset", str(error))
            return
        self._show_file_warnings()
        self._after_project_changed(updated, refresh_assets=True)

    def _handle_assets_dropped_on_project(self, project: Project, asset_ids: list[int]) -> None:
        assets = [
            asset
            for asset_id in asset_ids
            if (asset := self.project_service.get_asset(asset_id)) is not None
        ]
        if not assets:
            return
        if not self._confirm_asset_batch_link_by_name(project, assets):
            return
        try:
            updated = self.project_service.link_assets(project, assets)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Link Assets", str(error))
            return
        self._show_file_warnings()
        self._after_project_changed(updated, refresh_assets=True)

    def _handle_card_files_dropped(self, project: Project, paths: list[Path]) -> None:
        asset_paths: list[Path] = []
        for path in paths:
            if not path.exists():
                continue
            if path.is_file() and is_mp4_file(path):
                self._handle_dropped_mp4(project, path)
            else:
                asset_paths.append(path)
        if asset_paths:
            self._link_asset_paths(project, asset_paths)

    def _handle_dropped_mp4(self, project: Project, path: Path) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("MP4 Dropped")
        if project.has_edited_video:
            box.setText(f"{project.name} already has an edited video.")
            box.setInformativeText(f"What should EditFlow do with {path.name}?")
            replace_button = box.addButton("Replace Current", QMessageBox.ButtonRole.AcceptRole)
            export_button = box.addButton("Add as Another Export", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()

            clicked = box.clickedButton()
            if clicked == replace_button:
                self._assign_edited_video(project, path)
            elif clicked == export_button:
                self._add_export_only(project, path)
            return
        else:
            box.setText(f"Set {path.name} as the edited video?")
        box.setInformativeText("The raw video will not be replaced.")
        edited_button = box.addButton("Set Edited Video", QMessageBox.ButtonRole.AcceptRole)
        asset_button = box.addButton("Link as Asset", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked == edited_button:
            self._assign_edited_video(project, path)
        elif clicked == asset_button:
            self._link_asset_path(project, path, confirm=False)

    def _add_export_only(self, project: Project, path: Path) -> None:
        self._run_background(
            "Adding export...",
            lambda: self.project_service.add_export_file(project, path),
            lambda _result: self._export_file_added(project),
            "Could Not Add Export",
        )

    def _export_file_added(self, project: Project) -> None:
        updated = self.project_service.get_project(project.id or 0) or project
        self._after_project_changed(updated)
        if updated.status not in {"Need Upload", "Done"}:
            answer = QMessageBox.question(
                self,
                "Update Status",
                "Export added. Move this project to Need Upload?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._change_status(updated, "Need Upload")

    def _link_asset_path(
        self,
        project: Project,
        path: Path,
        *,
        confirm: bool = True,
    ) -> None:
        self._link_asset_paths(project, [path], confirm=confirm)

    def _link_asset_paths(
        self,
        project: Project,
        paths: list[Path],
        *,
        confirm: bool = True,
    ) -> None:
        valid_paths = [path for path in paths if path.exists()]
        if not valid_paths:
            return
        unregistered = [
            path
            for path in valid_paths
            if self.project_service.get_asset_by_path(path) is None
        ]
        try:
            if confirm:
                if unregistered:
                    if not self._confirm_register_and_link_assets(project, unregistered):
                        return
                elif not self._confirm_asset_batch_link(project, valid_paths):
                    return

            assets = [
                self.project_service.get_or_create_asset_for_path(path)
                for path in valid_paths
            ]
            updated = self.project_service.link_assets(project, assets)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Link Assets", str(error))
            return
        self._show_file_warnings()
        self._after_project_changed(updated, refresh_assets=True)

    def _confirm_asset_link(self, project: Project, asset_name: str) -> bool:
        answer = QMessageBox.question(
            self,
            "Link Asset",
            f"Link {asset_name} to {project.name}?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_register_and_link_assets(
        self,
        project: Project,
        paths: list[Path],
    ) -> bool:
        sample = "\n".join(path.name for path in paths[:5])
        if len(paths) > 5:
            sample = f"{sample}\n...and {len(paths) - 5} more"
        answer = QMessageBox.question(
            self,
            "Add and Link Assets",
            "Add these files/folders to the managed Asset Library and link them to this project?\n"
            "Files are copied into the library; folders are moved into the library.\n\n"
            f"{sample}\n\nProject: {project.name}",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_asset_batch_link(
        self,
        project: Project,
        paths: list[Path],
    ) -> bool:
        answer = QMessageBox.question(
            self,
            "Link Assets",
            f"Link {len(paths)} asset(s) to {project.name}?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_asset_batch_link_by_name(
        self,
        project: Project,
        assets: list[Asset],
    ) -> bool:
        sample = "\n".join(asset.name for asset in assets[:6])
        if len(assets) > 6:
            sample = f"{sample}\n...and {len(assets) - 6} more"
        answer = QMessageBox.question(
            self,
            "Link Assets",
            f"Link {len(assets)} asset(s) to {project.name}?\n\n{sample}",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _show_file_warnings(self) -> None:
        warnings = self.project_service.consume_file_warnings()
        if warnings:
            self._play_sound("warning")
            QMessageBox.warning(self, "File Link Warning", "\n\n".join(warnings))

    def _scan_project_folder(self, project: Project | None = None) -> None:
        self._schedule_detection_scan()

    def _schedule_detection_scan(self) -> None:
        self.detection_scan_timer.start()

    def _scan_for_detections(self) -> None:
        if self._scan_running:
            return
        self._scan_running = True
        task = BackgroundTask(lambda: self.project_service.scan_for_file_detections())

        def handle_success(result: object) -> None:
            self._scan_running = False
            detections = result if isinstance(result, list) else []
            for detection in detections:
                if not isinstance(detection, dict):
                    continue
                file_path = detection.get("file_path")
                if isinstance(file_path, Path) and self._detection_already_queued(file_path):
                    continue
                self._detection_queue.append(detection)
            self._show_next_detection()
            self.refresh_activity()

        def handle_error(_error: str) -> None:
            self._scan_running = False

        task.signals.succeeded.connect(handle_success)
        task.signals.failed.connect(handle_error)
        self.thread_pool.start(task)

    def _detection_already_queued(self, file_path: Path) -> bool:
        normalized = normalize_file_path(file_path)
        if self._active_detection is not None:
            active_path = self._active_detection.get("file_path")
            if isinstance(active_path, Path) and normalize_file_path(active_path) == normalized:
                return True
        for detection in self._detection_queue:
            queued_path = detection.get("file_path")
            if isinstance(queued_path, Path) and normalize_file_path(queued_path) == normalized:
                return True
        return False

    def _show_next_detection(self) -> None:
        if self._active_detection is not None:
            return
        while self._detection_queue:
            detection = self._detection_queue.pop(0)
            file_path = detection.get("file_path")
            if isinstance(file_path, Path) and file_path.exists():
                self._active_detection = detection
                self._render_detection_notification(detection)
                return
        self.notification_panel.setVisible(False)

    def _render_detection_notification(self, detection: dict[str, object]) -> None:
        file_path = detection.get("file_path")
        detection_type = detection.get("detection_type")
        project = detection.get("project")
        folder = detection.get("folder")
        path = file_path if isinstance(file_path, Path) else Path("")
        folder_path = folder.path if isinstance(folder, WatchedFolder) else path.parent

        if detection_type == "raw":
            self.notification_title.setText("New raw video detected")
            self.notification_body.setText(
                f"{path.name}\nFolder: {folder_path}"
            )
            self.notification_primary_button.setText("Create Project")
            self.notification_secondary_button.setVisible(False)
        elif detection_type == "export":
            project_name = project.name if isinstance(project, Project) else "No confident match"
            self.notification_title.setText("Possible edited video detected")
            self.notification_body.setText(
                f"{path.name}\nPossible project: {project_name}"
            )
            self.notification_primary_button.setText("Set as Edited Video")
            self.notification_primary_button.setVisible(isinstance(project, Project))
            self.notification_secondary_button.setText("Choose Project")
            self.notification_secondary_button.setVisible(True)
        else:
            self.notification_title.setText("New asset detected")
            self.notification_body.setText(f"{path.name}\nFolder: {folder_path}")
            self.notification_primary_button.setText("Add Asset")
            self.notification_primary_button.setVisible(True)
            self.notification_secondary_button.setVisible(False)

        self.notification_panel.setVisible(True)

    def _handle_detection_action(self, action: str) -> None:
        detection = self._active_detection
        if detection is None:
            return
        file_path = detection.get("file_path")
        if not isinstance(file_path, Path):
            self._dismiss_detection()
            return

        if action == "ignore":
            self.project_service.mark_detection_status(file_path, "ignored")
            self._dismiss_detection()
            return

        detection_type = detection.get("detection_type")
        if detection_type == "raw" and action == "primary":
            self._create_project_from_detected_raw(file_path)
        elif detection_type == "export":
            project = detection.get("project")
            if action == "primary" and isinstance(project, Project):
                self._confirm_export_for_project(file_path, project)
            elif action == "secondary":
                self._choose_project_for_export(file_path)
        elif detection_type == "asset" and action == "primary":
            self._register_detected_asset(file_path)

    def _dismiss_detection(self) -> None:
        self._active_detection = None
        self.notification_panel.setVisible(False)
        self._show_next_detection()

    def _create_project_from_detected_raw(self, raw_path: Path) -> None:
        dialog = self._raw_video_dialog(raw_path)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self.project_service.mark_detection_status(raw_path, "accepted")
        self._run_background(
            "Adding raw video...",
            lambda: self.project_service.create_project_from_raw(
                raw_video_path=raw_path,
                name=values["name"],
                client=values["client"],
                video_type=values["video_type"],
            ),
            self._raw_video_added,
            "Could Not Add Raw Video",
        )
        self._dismiss_detection()

    def _confirm_export_for_project(self, export_path: Path, project: Project) -> None:
        try:
            updated = self.project_service.set_edited_video_reference(
                project,
                export_path,
                add_export=True,
            )
            self.project_service.mark_detection_status(export_path, "accepted")
        except Exception as error:
            QMessageBox.critical(self, "Could Not Assign Export", str(error))
            return

        self._after_project_changed(updated)
        self.refresh_activity()
        self._dismiss_detection()
        answer = QMessageBox.question(
            self,
            "Update Status",
            "Change this project status to Need Upload?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._change_status(updated, "Need Upload")

    def _choose_project_for_export(self, export_path: Path) -> None:
        projects = self.project_service.list_projects()
        dialog = ProjectChoiceDialog(projects, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        project_id = dialog.selected_project_id()
        if project_id is None:
            return
        project = self.project_service.get_project(project_id)
        if project is not None:
            self._confirm_export_for_project(export_path, project)

    def _register_detected_asset(self, asset_path: Path) -> None:
        try:
            self.project_service.get_or_create_asset_for_path(asset_path)
            self.project_service.mark_detection_status(asset_path, "accepted")
        except Exception as error:
            QMessageBox.critical(self, "Could Not Add Asset", str(error))
            return
        self.refresh_assets()
        self.refresh_activity()
        self._dismiss_detection()

    def _schedule_selected_folder_scan(self, _folder: str) -> None:
        QTimer.singleShot(600, self._schedule_detection_scan)

    def _watch_project_folder(self, project: Project | None) -> None:
        watched = self.folder_watcher.directories()
        if watched:
            self.folder_watcher.removePaths(watched)
        if project is None:
            return
        if project.folder_path.exists() and project.folder_path.is_dir():
            self.folder_watcher.addPath(str(project.folder_path))

    def _open_project_folder(self, project: Project) -> None:
        self._open_path(project.folder_path)

    def _copy_project_folder_path(self, project: Project) -> None:
        QApplication.clipboard().setText(str(project.folder_path))
        self._show_toast("✓ Folder path copied")

    def _open_project_linked_assets(self, project: Project) -> None:
        try:
            self.project_service.ensure_project_structure(project)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Open Linked Assets", str(error))
            return
        self._open_path(project.folder_path / LINKED_ASSETS_FOLDER_NAME)

    def _open_projects_root(self) -> None:
        try:
            root = self.project_service.projects_root()
            root.mkdir(parents=True, exist_ok=True)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Open Projects Folder", str(error))
            return
        self._open_path(root)

    def _open_raw_video(self, project: Project) -> None:
        self._open_path(project.raw_video_path)

    def _open_edited_video(self, project: Project) -> None:
        if project.edited_video_path is not None:
            self._open_path(project.edited_video_path)

    def _open_asset_location(self, asset: Asset) -> None:
        self._open_path(asset.file_path if asset.file_path.is_dir() else asset.file_path.parent)

    def _open_export_video(self, export: ProjectExport) -> None:
        self._open_path(export.file_path)

    def _show_export_in_folder(self, export: ProjectExport) -> None:
        self._open_path(export.file_path.parent)

    def _set_export_current(self, export: ProjectExport) -> None:
        project = self.project_service.get_project(export.project_id)
        if project is None:
            return
        try:
            updated = self.project_service.set_edited_video_reference(
                project,
                export.file_path,
                add_export=False,
            )
        except Exception as error:
            QMessageBox.critical(self, "Could Not Set Export", str(error))
            return
        self._after_project_changed(updated)
        self.refresh_activity()

    def _open_selected_asset_location(self) -> None:
        asset_id = self.assets_tree.current_asset_id()
        if asset_id is None:
            return
        asset = self.project_service.get_asset(asset_id)
        if asset is not None:
            self._open_asset_location(asset)

    def _edit_selected_asset_tags(self) -> None:
        asset_id = self.assets_tree.current_asset_id()
        if asset_id is None:
            return
        asset = self.project_service.get_asset(asset_id)
        if asset is None:
            return
        current_tags = ", ".join(self.project_service.list_asset_tags(asset_id))
        tags, accepted = QInputDialog.getText(
            self,
            "Edit Asset Tags",
            f"Tags for {asset.name}",
            QLineEdit.EchoMode.Normal,
            current_tags,
        )
        if not accepted:
            return
        self.project_service.set_asset_tags(asset_id, tags)
        self.refresh_assets()
        if self.selected_project_id is not None:
            project = self.project_service.get_project(self.selected_project_id)
            if project is not None:
                self._set_selected_project_details(project)

    def _relink_missing_file(self, issue: FileIssue) -> None:
        if issue.kind == "project_folder":
            folder = QFileDialog.getExistingDirectory(self, "Relink Project Folder")
            if not folder or issue.project_id is None:
                return
            try:
                project = self.project_service.relink_project_folder(
                    issue.project_id,
                    Path(folder),
                )
            except Exception as error:
                QMessageBox.critical(self, "Could Not Relink Folder", str(error))
                return
            self._after_project_changed(project)
            self.refresh_activity()
            return

        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Relink File",
            str(issue.path.parent if str(issue.path) else Path.home()),
            "All Files (*.*)",
        )
        if not file_name:
            return
        replacement = Path(file_name)
        try:
            if issue.kind in {"raw_video", "edited_video"} and issue.project_id is not None:
                project = self.project_service.relink_project_file(
                    issue.project_id,
                    issue.kind,
                    replacement,
                )
                self._after_project_changed(project)
            elif issue.kind == "asset" and issue.asset_id is not None:
                self.project_service.relink_asset(issue.asset_id, replacement)
                if self.selected_project_id is not None:
                    project = self.project_service.get_project(self.selected_project_id)
                    if project is not None:
                        self._set_selected_project_details(project)
                self.refresh_assets()
            elif issue.kind == "export" and issue.export_id is not None:
                export = self.project_service.relink_export(issue.export_id, replacement)
                project = self.project_service.get_project(export.project_id)
                if project is not None:
                    self._set_selected_project_details(project)
        except Exception as error:
            QMessageBox.critical(self, "Could Not Relink File", str(error))
            return
        self.refresh_activity()

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            self._play_sound("warning")
            QMessageBox.warning(
                self,
                "Location Not Found",
                f"This location does not exist:\n{path}",
            )
            return

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if not opened:
            self._play_sound("warning")
            QMessageBox.warning(self, "Could Not Open Location", str(path))

    def _backup_data(self) -> None:
        default_name = f"editflow-backup-{datetime.now():%Y%m%d-%H%M%S}.db"
        file_name, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Backup EditFlow Data",
            default_name,
            "SQLite Database (*.db)",
        )
        if not file_name:
            return
        self._run_background(
            "Backing up EditFlow data...",
            lambda: self.project_service.backup_data(Path(file_name)),
            lambda result: QMessageBox.information(
                self,
                "Backup Complete",
                f"Saved backup to:\n{result}",
            ),
            "Backup Failed",
        )

    def _after_project_changed(
        self,
        project: Project,
        *,
        refresh_assets: bool = False,
    ) -> None:
        if project.id is None:
            return
        if project.status == PUBLISHED_STATUS:
            self.project_cache.pop(project.id, None)
        else:
            self.project_cache[project.id] = project
        if self._project_matches_current_filters(project):
            self.board.update_project(project, self.selected_project_id)
        else:
            self.board.remove_project(project.id)

        if self.selected_project_id == project.id and project.status == PUBLISHED_STATUS:
            self.selected_project_id = None
            self.details.set_project(None, [], [], [], [])
            self._update_details_overlay_visibility()
            self._watch_project_folder(None)
        elif self.selected_project_id == project.id:
            self._set_selected_project_details(project)

        if refresh_assets:
            self.refresh_assets()
        self.refresh_publish()
        self.refresh_clients()
        self.refresh_activity()

    def _log_new_file_issues(self, project: Project, issues: list[FileIssue]) -> None:
        for issue in issues:
            key = f"{issue.kind}:{issue.path}"
            if key in self._logged_file_issues:
                continue
            self._logged_file_issues.add(key)
            self.project_service.log_activity(
                "missing_file_detected",
                f"{issue.label} for {project.name}",
                project_id=project.id,
                asset_id=issue.asset_id,
            )
        if issues:
            self.refresh_activity()

    def _project_matches_current_filters(self, project: Project) -> bool:
        if project.status == PUBLISHED_STATUS:
            return False
        search = self.project_search_edit.text().strip().casefold()
        if search:
            haystack = " ".join(
                [
                    project.name,
                    project.client,
                    project.notes,
                    str(project.raw_video_path),
                    str(project.edited_video_path or ""),
                ]
            ).casefold()
            if search not in haystack:
                return False
        client = self._current_client_filter()
        if client and project.client != client:
            return False
        status = self._current_status_filter()
        if status and project.status != status:
            return False
        return True

    def _sync_client_filter(self) -> None:
        current = self._current_client_filter()
        clients = self.project_service.list_clients()
        self.client_filter_combo.blockSignals(True)
        self.client_filter_combo.clear()
        self.client_filter_combo.addItem("All clients", "")
        for client in clients:
            self.client_filter_combo.addItem(client, client)
        index = self.client_filter_combo.findData(current)
        self.client_filter_combo.setCurrentIndex(index if index >= 0 else 0)
        self.client_filter_combo.blockSignals(False)

    def _current_client_filter(self) -> str:
        value = self.client_filter_combo.currentData()
        return str(value or "")

    def _current_status_filter(self) -> str:
        return ""

    def _handle_thumbnail_ready(self, normalized_path: str, cache_path: str) -> None:
        icon = QIcon(cache_path)
        if hasattr(self, "assets_tree"):
            iterator = QTreeWidgetItemIterator(self.assets_tree)
            while iterator.value() is not None:
                item = iterator.value()
                path_value = item.data(0, ASSET_PATH_ROLE)
                if isinstance(path_value, str) and normalize_file_path(Path(path_value)) == normalized_path:
                    item.setIcon(0, icon)
                iterator += 1

        for index in range(self.recent_assets_list.count()):
            item = self.recent_assets_list.item(index)
            path = Path(item.toolTip())
            if item.toolTip() and normalize_file_path(path) == normalized_path:
                item.setIcon(icon)

        for index in range(self.most_used_assets_list.count()):
            item = self.most_used_assets_list.item(index)
            path = Path(item.toolTip())
            if item.toolTip() and normalize_file_path(path) == normalized_path:
                item.setIcon(icon)

    def _run_background(
        self,
        message: str,
        callback,
        on_success,
        error_title: str,
    ) -> None:
        self._show_loading(message)
        task = BackgroundTask(callback)
        self._background_tasks.add(task)

        def handle_success(result: object) -> None:
            self._background_tasks.discard(task)
            self._finish_loading()
            on_success(result)

        def handle_error(error: str) -> None:
            self._background_tasks.discard(task)
            self._finish_loading()
            QMessageBox.critical(self, error_title, error)

        task.signals.succeeded.connect(handle_success)
        task.signals.failed.connect(handle_error)
        self.thread_pool.start(task)

    def _run_inline_with_loading(self, message: str, callback):
        self._show_loading(message)
        QApplication.processEvents()
        try:
            return callback()
        finally:
            self._finish_loading()
            QApplication.processEvents()

    def _show_loading(self, message: str) -> None:
        self._loading_jobs += 1
        self._loading_started_at = monotonic()
        self.loading_hide_timer.stop()
        self.loading_label.setText(message)
        self._position_loading_panel()
        self.loading_panel.setVisible(True)
        self.loading_panel.raise_()

    def _finish_loading(self) -> None:
        self._loading_jobs = max(0, self._loading_jobs - 1)
        if self._loading_jobs:
            return
        elapsed_ms = int((monotonic() - self._loading_started_at) * 1000)
        remaining_ms = max(0, self._loading_minimum_ms - elapsed_ms)
        if remaining_ms:
            self.loading_hide_timer.start(remaining_ms)
            return
        self._hide_loading_now()

    def _hide_loading_now(self) -> None:
        if self._loading_jobs:
            return
        self.loading_panel.setVisible(False)

    def _show_toast(self, message: str, duration_ms: int = 2600) -> None:
        self.toast_label.setText(message)
        self.toast_label.setVisible(True)
        self._position_toast()
        self.toast_label.raise_()
        self.toast_hide_timer.start(duration_ms)


STYLESHEET = """
QMainWindow, QWidget {
    background: #0c1118;
    color: #eef3ff;
    font-family: Segoe UI, Arial, sans-serif;
    font-size: 13px;
}
QFrame#Sidebar {
    background: #111824;
    border-right: 1px solid #2d3a4f;
}
QFrame#DetailsPanel {
    background: #111824;
    border-left: 1px solid #3a4a63;
}
QFrame#DetailsGutter {
    background: #0c1118;
    border: none;
}
QFrame#DetailsRail {
    background: #172334;
    border-left: 1px solid #34445e;
    border-right: 1px solid #0b1017;
}
QFrame#LoadingOverlay {
    background: rgba(7, 11, 18, 95);
    border: none;
}
QFrame#LoadingCard {
    background: #111824;
    border: 1px solid #3a4a63;
    border-radius: 8px;
}
QLabel#LoadingIcon {
    color: #8fb0ff;
    font-size: 14px;
}
QLabel#LoadingLabel {
    color: #dce6f6;
    font-weight: 700;
}
QWidget#EarningsAnimationStage {
    background: transparent;
    border: none;
}
QLabel#EarningsAnimationLabel {
    background: transparent;
    border: none;
    color: #56e39f;
    font-size: 24px;
    font-weight: 900;
    padding: 0;
}
QProgressBar#LoadingProgress {
    background: #0b1017;
    border: 1px solid #2d3a4f;
    border-radius: 3px;
}
QProgressBar#LoadingProgress::chunk {
    background: #5f7dff;
    border-radius: 3px;
}
QLabel#ToastLabel {
    background: #10251b;
    border: 1px solid #2a7a56;
    border-radius: 8px;
    color: #b8ffd4;
    font-weight: 800;
    padding: 9px 12px;
}
QFrame#AssetPreviewPanel {
    background: #111824;
    border: 1px solid #303d52;
    border-radius: 8px;
}
QLabel#AssetPreviewMedia {
    background: #0b1017;
    border: 1px solid #26344a;
    border-radius: 8px;
    color: #9fb0c7;
    font-size: 18px;
    font-weight: 700;
    padding: 10px;
}
QVideoWidget#AssetVideoPreview {
    background: #0b1017;
    border: 1px solid #26344a;
    border-radius: 8px;
}
QFrame#AssetMediaControls {
    background: transparent;
    border: 0;
}
QSplitter::handle {
    background: #172334;
}
QSplitter::handle:hover {
    background: #2b3f5d;
}
QLabel#AssetBadge {
    background: #172334;
    border: 1px solid #34445e;
    border-radius: 8px;
    color: #dce6f6;
    font-size: 11px;
    font-weight: 700;
    padding: 3px 7px;
}
QPushButton#PickerFilterButton {
    background: #151d2a;
    border: 1px solid #34445e;
    border-radius: 8px;
    color: #b9c7dc;
    padding: 5px 9px;
}
QPushButton#PickerFilterButton:checked {
    background: #24344b;
    border-color: #5f7dff;
    color: #ffffff;
    font-weight: 800;
}
QLabel#LogoBadge {
    background: #4567ff;
    border: 1px solid #6d84ff;
    border-radius: 8px;
    color: #ffffff;
    font-weight: 700;
}
QPushButton#LogoButton {
    background: transparent;
    border: none;
    color: #ffffff;
    font-weight: 800;
    padding: 0;
}
QPushButton#LogoButton:hover {
    background: transparent;
}
QLabel#AppTitle {
    font-size: 24px;
    font-weight: 700;
}
QLabel#PageTitle {
    font-size: 24px;
    font-weight: 700;
}
QPushButton#PageTitleButton {
    background: transparent;
    border: none;
    color: #eef4ff;
    font-size: 24px;
    font-weight: 700;
    padding: 0;
    text-align: left;
}
QPushButton#PageTitleButton:hover {
    color: #8fb0ff;
}
QLabel#SectionTitle {
    font-size: 16px;
    font-weight: 700;
}
QLabel#DetailsTitle {
    font-size: 21px;
    font-weight: 700;
}
QLabel#MetricNumber {
    font-size: 30px;
    font-weight: 700;
}
QLabel#TotalEarningsLabel {
    color: #35d07f;
    font-size: 30px;
    font-weight: 800;
}
QLabel#CardTitle {
    font-size: 15px;
    font-weight: 700;
}
QLabel#MutedLabel, QLabel#PathLabel {
    color: #aab6c9;
}
QLabel#PathLabel {
    font-size: 12px;
}
QLabel#AssetCountLink {
    color: #c7d4e8;
    font-size: 12px;
    padding: 2px 0;
}
QLabel#AssetCountLink:hover {
    color: #8fb0ff;
}
QLabel#EarningsBadge {
    background: transparent;
    border: none;
    color: #35d07f;
    font-size: 11px;
    font-weight: 800;
    padding: 0;
}
QLabel#EarningsBadge[earned="true"] {
    background: transparent;
    border: none;
    color: #35d07f;
}
QPushButton#CardIconButton {
    background: #1b2637;
    border: 1px solid #34445e;
    border-radius: 6px;
    color: #d8e1f0;
    font-size: 12px;
    padding: 0;
}
QPushButton#CardIconButton:hover {
    background: #24344b;
    border-color: #5f7dff;
}
QLabel#Thumbnail, QLabel#LargeThumbnail {
    background: #0b1017;
    border: 1px solid #26344a;
    border-radius: 6px;
    color: #72819a;
}
QPushButton#AddEditedVideoButton {
    background: #101827;
    border: 1px dashed #3b4d69;
    border-radius: 6px;
    color: #b9c7dc;
    font-size: 8px;
    font-weight: 700;
    padding: 0;
    text-align: center;
}
QPushButton#AddEditedVideoButton:hover {
    background: #172238;
    border-color: #5f7dff;
    color: #eef4ff;
}
QLabel#StatusPill, QLabel#NeutralPill, QLabel#GoodPill {
    background: #243044;
    border: 1px solid #34445e;
    border-radius: 8px;
    padding: 3px 8px;
    color: #d8e1f0;
}
QLabel#VideoTypePill {
    background: #243044;
    border: 1px solid #34445e;
    border-radius: 8px;
    padding: 3px 6px;
    color: #d8e1f0;
    font-size: 11px;
}
QLabel#VideoTypePill[compact="true"] {
    padding: 3px 4px;
    font-size: 9px;
}
QLabel#VideoTypePill[kind="ugc"] {
    background: #38BDF8;
    border-color: #38BDF8;
    color: #06111f;
}
QLabel#VideoTypePill[kind="personal_brand"] {
    background: #A78BFA;
    border-color: #A78BFA;
    color: #130f24;
}
QLabel#GoodPill {
    background: #153728;
    border-color: #2a7a56;
    color: #8ff0bd;
}
QLabel#ColumnTitle {
    font-size: 17px;
    font-weight: 700;
}
QLabel#ColumnCount {
    background: #273348;
    border-radius: 8px;
    padding: 3px 8px;
    color: #cbd7ea;
}
QFrame#KanbanColumn {
    background: #151d2a;
    border: 1px solid #303d52;
    border-radius: 8px;
}
QFrame#ProjectCard {
    background: #111824;
    border: 1px solid #30405a;
    border-radius: 8px;
}
QFrame#ProjectCard[readiness="blocked"] {
    border: 1px solid #3a465a;
    background: #111824;
}
QFrame#ProjectCard[readiness="ready"] {
    border: 2px solid #35d07f;
    background: #101f1b;
}
QFrame#ProjectCard[readiness="revision"] {
    border: 2px solid #c48924;
    background: #21190d;
}
QFrame#ProjectCard[selected="true"] {
    border: 2px solid #5577ff;
    background: #172238;
}
QFrame#ProjectCard[selected="true"][readiness="blocked"] {
    border: 2px solid #5577ff;
    background: #172238;
}
QFrame#ProjectCard[selected="true"][readiness="ready"] {
    border: 2px solid #35d07f;
    background: #142a23;
}
QFrame#ProjectCard[selected="true"][readiness="revision"] {
    border: 2px solid #d19a33;
    background: #2a200f;
}
QFrame#ProjectCard[flow="true"] {
    border: 2px solid #7dd3fc;
    background: #162537;
}
QFrame#ProjectCard[flow="true"][selected="true"] {
    border: 2px solid #fbbf24;
    background: #1d2d43;
}
QFrame#ProjectCard[hovered="true"] {
    background: #172233;
    border-color: #465b78;
}
QFrame#ProjectCard[hovered="true"][readiness="ready"] {
    background: #142b23;
    border-color: #57e091;
}
QFrame#ProjectCard[hovered="true"][readiness="revision"] {
    background: #2a210f;
    border-color: #d69a2b;
}
QFrame#ProjectCard[hovered="true"][selected="true"] {
    background: #1b2a45;
    border-color: #6d89ff;
}
QFrame#ProjectCard[hovered="true"][selected="true"][readiness="ready"] {
    background: #173229;
    border-color: #61e89b;
}
QFrame#ProjectCard[hovered="true"][selected="true"][readiness="revision"] {
    background: #302611;
    border-color: #e3aa3a;
}
QFrame#ProjectCard[hovered="true"][flow="true"] {
    background: #1a2d43;
    border-color: #9be2ff;
}
QFrame#ProjectCard[hovered="true"][flow="true"][selected="true"] {
    background: #223753;
    border-color: #ffd166;
}
QFrame#ProjectCard[dragging="true"] {
    background: #242833;
    border: 2px dashed #6f7786;
}
QFrame#DropIndicator {
    background: #7aa2ff;
    border-radius: 1px;
    min-height: 3px;
    max-height: 3px;
}
QFrame#RevisionNoteRow {
    background: #2a210f;
    border: 1px solid #c48924;
    border-radius: 8px;
}
QFrame#RevisionNoteRow[completed="true"] {
    background: #141b26;
    border: 1px solid #2d3a4f;
    color: #7f8ca3;
}
QFrame#RevisionNoteRow[completed="true"] QLabel {
    color: #7f8ca3;
}
QFrame#RevisionNoteRow QCheckBox {
    spacing: 6px;
}
QFrame#MetricCard {
    background: #151d2a;
    border: 1px solid #303d52;
    border-radius: 8px;
}
QGroupBox {
    border: 1px solid #344158;
    border-radius: 8px;
    margin-top: 10px;
    padding: 12px 8px 8px 8px;
    color: #dce6f6;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #9fb0c7;
}
QLineEdit, QTextEdit, QComboBox, QListWidget, QTableWidget, QTreeWidget {
    background: #151d2a;
    border: 1px solid #2a3850;
    border-radius: 6px;
    padding: 8px;
    selection-background-color: #4968ff;
}
QHeaderView::section {
    background: #1a2332;
    color: #dce6f6;
    border: 0;
    border-right: 1px solid #2a3850;
    padding: 8px;
}
QToolTip {
    background: #172238;
    border: 1px solid #4a5b74;
    border-radius: 6px;
    color: #edf3ff;
    padding: 8px;
}
QPushButton {
    background: #1d2838;
    border: 1px solid #33435d;
    border-radius: 7px;
    padding: 6px 9px;
    color: #edf3ff;
    font-size: 12px;
}
QPushButton:hover {
    background: #26344a;
}
QPushButton#PrimaryButton {
    background: #4567ff;
    border: 1px solid #6680ff;
    font-weight: 700;
}
QPushButton#PublishButton {
    background: #233142;
    border: 1px solid #4a5b74;
    color: #dce6f6;
    font-weight: 700;
}
QPushButton#PublishButton:hover {
    background: #2d3d52;
}
QPushButton#PriorityButton {
    background: #192232;
    border: 1px solid #3a4a63;
    border-radius: 11px;
    color: #8f9bb0;
    font-size: 12px;
    font-weight: 700;
    padding: 0;
}
QPushButton#PriorityButton:hover {
    background: #253249;
    color: #f2c94c;
}
QPushButton#PriorityButton[priority="true"] {
    background: #33280d;
    border-color: #c99724;
    color: #ffd166;
}
QLabel#NoteIndicator {
    background: #192232;
    border: 1px solid #3a4a63;
    border-radius: 11px;
    color: #7f8ca3;
    font-size: 12px;
}
QLabel#NoteIndicator[has_notes="true"] {
    background: #21351f;
    border-color: #53a464;
    color: #8ff0bd;
}
QPushButton#IconButton {
    padding: 0;
    min-width: 24px;
    max-width: 28px;
}
QPushButton#PanelCollapseButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    color: #dce6f6;
    font-size: 16px;
    font-weight: 800;
    padding: 0;
    min-width: 20px;
    max-width: 22px;
}
QPushButton#PanelCollapseButton:hover {
    background: #24344b;
    color: #ffffff;
}
QPushButton#SubtleButton, QPushButton#NavButton {
    text-align: left;
    padding: 6px 8px;
}
QFrame#Sidebar QPushButton#NavButton,
QFrame#Sidebar QPushButton#PrimaryButton {
    min-height: 40px;
    max-height: 40px;
    padding: 0;
    text-align: left;
}
QFrame#Sidebar QLabel#SidebarButtonIcon {
    background: transparent;
    color: #dce6f6;
    font-size: 17px;
}
QFrame#Sidebar QLabel#SidebarButtonLabel {
    background: transparent;
    color: #dce6f6;
    font-size: 13px;
    font-weight: 650;
    padding-left: 8px;
}
QFrame#Sidebar QPushButton#PrimaryButton QLabel#SidebarButtonIcon,
QFrame#Sidebar QPushButton#PrimaryButton QLabel#SidebarButtonLabel {
    color: #ffffff;
    font-weight: 800;
}
QFrame#Sidebar QPushButton#NavButton[collapsed="true"],
QFrame#Sidebar QPushButton#PrimaryButton[collapsed="true"] {
    background: transparent;
    border: none;
    border-radius: 10px;
    color: #dce6f6;
    font-size: 17px;
    padding: 0;
    text-align: center;
}
QFrame#Sidebar QPushButton#NavButton[collapsed="true"]:hover,
QFrame#Sidebar QPushButton#PrimaryButton[collapsed="true"]:hover {
    background: #172334;
    border: none;
    color: #ffffff;
}
QFrame#Sidebar QPushButton#PrimaryButton[collapsed="true"] {
    font-weight: 800;
}
QScrollArea {
    background: transparent;
}
QScrollArea#KanbanCardsScroll {
    border-top: 1px solid #243044;
}
QWidget#KanbanCardsViewport {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #46566f;
    border-radius: 3px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: #6a7da0;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    border: 0;
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #46566f;
    border-radius: 3px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover {
    background: #6a7da0;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    border: 0;
    background: transparent;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
"""


def _is_under_path(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False
