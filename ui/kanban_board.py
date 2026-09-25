from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from models.project import Project, STATUSES, workflow_stage_label
from ui.project_card import PROJECT_MIME, ProjectCard


STATUS_ACCENTS: dict[str, str] = {
    "Need Edit": "#7aa2ff",
    "Editing": "#5ed1ff",
    "Need Upload": "#ffd166",
    "Done": "#56e39f",
}


class ColumnScrollArea(QScrollArea):
    def __init__(self, column: "KanbanColumn", parent=None) -> None:
        super().__init__(parent)
        self.column = column
        self._scroll_target = 0
        self._scroll_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_animation.setDuration(150)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if watched == self.viewport():
            if event.type() == QEvent.Type.DragEnter:
                self.column.handle_drag_enter(event, self.viewport())
                return event.isAccepted()
            if event.type() == QEvent.Type.DragMove:
                self.column.handle_drag_move(event, self.viewport())
                return event.isAccepted()
            if event.type() == QEvent.Type.Drop:
                self.column.handle_drop(event, self.viewport())
                return event.isAccepted()
            if event.type() == QEvent.Type.DragLeave:
                self.column.hide_drop_indicator()
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event) -> None:
        self.column.handle_drag_enter(event, self)

    def dragMoveEvent(self, event) -> None:
        self.column.handle_drag_move(event, self)

    def dropEvent(self, event) -> None:
        self.column.handle_drop(event, self)

    def dragLeaveEvent(self, event) -> None:
        self.column.hide_drop_indicator()
        super().dragLeaveEvent(event)

    def wheelEvent(self, event) -> None:
        scrollbar = self.verticalScrollBar()
        if scrollbar.maximum() <= scrollbar.minimum():
            super().wheelEvent(event)
            return

        pixel_delta = event.pixelDelta().y()
        if pixel_delta:
            distance = -pixel_delta
        else:
            notches = event.angleDelta().y() / 120
            distance = int(-notches * scrollbar.singleStep() * 6)

        if distance == 0:
            super().wheelEvent(event)
            return

        if self._scroll_animation.state() == QPropertyAnimation.State.Running:
            start_value = scrollbar.value()
            target = self._scroll_target + distance
        else:
            start_value = scrollbar.value()
            target = start_value + distance

        self._scroll_target = max(scrollbar.minimum(), min(scrollbar.maximum(), target))
        self._scroll_animation.stop()
        self._scroll_animation.setStartValue(start_value)
        self._scroll_animation.setEndValue(self._scroll_target)
        self._scroll_animation.start()
        event.accept()


class KanbanColumn(QFrame):
    project_selected = Signal(object)
    add_requested = Signal()
    publish_requested = Signal(object)
    project_status_dropped = Signal(int, str)
    project_reordered = Signal(int, str, int)
    files_dropped = Signal(object, list)
    raw_files_dropped = Signal(list)
    asset_dropped = Signal(object, int)
    assets_dropped = Signal(object, list)
    video_preview_requested = Signal(object, str)
    edited_video_requested = Signal(object)
    open_folder_requested = Signal(object)
    copy_folder_path_requested = Signal(object)
    priority_toggle_requested = Signal(object)
    video_type_edit_requested = Signal(object)
    payment_edit_requested = Signal(object)
    revision_requested = Signal(object)
    note_edit_requested = Signal(object)
    link_assets_requested = Signal(object)
    open_linked_assets_requested = Signal(object)
    remove_project_requested = Signal(object)
    flow_toggle_requested = Signal(object)

    def __init__(
        self,
        status: str,
        thumbnail_provider=None,
        sound_effects=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.status = status
        self.thumbnail_provider = thumbnail_provider
        self.sound_effects = sound_effects
        self.cards: dict[int, ProjectCard] = {}
        self.setObjectName("KanbanColumn")
        self.setAcceptDrops(True)
        self.setMinimumWidth(180)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.title = QLabel(workflow_stage_label(status))
        self.title.setObjectName("ColumnTitle")
        self.count = QLabel("0")
        self.count.setObjectName("ColumnCount")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.count)

        self.cards_widget = QWidget()
        self.cards_widget.setObjectName("KanbanCardsViewport")
        self.cards_widget.setAcceptDrops(True)
        self.cards_widget.installEventFilter(self)
        self.cards_layout = QVBoxLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.cards_scroll = ColumnScrollArea(self)
        self.cards_scroll.setObjectName("KanbanCardsScroll")
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.cards_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.cards_scroll.setWidget(self.cards_widget)
        self.drop_indicator = QFrame(self.cards_widget)
        self.drop_indicator.setObjectName("DropIndicator")
        self.drop_indicator.setFixedHeight(3)
        self.drop_indicator.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addLayout(header)
        if self.status == "Need Edit":
            self.add_button = QPushButton("➕ Raw")
            self.add_button.setObjectName("SubtleButton")
            self.add_button.setToolTip("Add Raw Video")
            self.add_button.clicked.connect(self.add_requested.emit)
        layout.addWidget(self.cards_scroll, 1)
        if self.status == "Need Edit":
            layout.addWidget(self.add_button)

        self._set_accent(STATUS_ACCENTS.get(status, "#7d8ca5"))

    def set_projects(self, projects: list[Project], selected_project_id: int | None) -> None:
        self._clear_cards()
        self.count.setText(str(len(projects)))

        for project in projects:
            self.add_or_update_project(project, selected_project_id)

        self.cards_layout.addStretch(1)

    def add_or_update_project(
        self,
        project: Project,
        selected_project_id: int | None,
    ) -> None:
        if project.id is None:
            return
        self.remove_project(project.id)
        card = ProjectCard(project, self.thumbnail_provider, self.sound_effects)
        card.set_selected(project.id == selected_project_id)
        card.clicked.connect(self.project_selected.emit)
        card.files_dropped.connect(self.files_dropped.emit)
        card.asset_dropped.connect(self.asset_dropped.emit)
        card.assets_dropped.connect(self.assets_dropped.emit)
        card.project_status_dropped.connect(self.project_status_dropped.emit)
        card.project_drag_positioned.connect(self._show_drop_indicator_for_card)
        card.project_order_dropped.connect(self._drop_project_on_card)
        card.project_drag_left.connect(self.hide_drop_indicator)
        card.video_preview_requested.connect(self.video_preview_requested.emit)
        card.edited_video_requested.connect(self.edited_video_requested.emit)
        card.open_folder_requested.connect(self.open_folder_requested.emit)
        card.copy_folder_path_requested.connect(self.copy_folder_path_requested.emit)
        card.priority_toggle_requested.connect(self.priority_toggle_requested.emit)
        card.video_type_edit_requested.connect(self.video_type_edit_requested.emit)
        card.payment_edit_requested.connect(self.payment_edit_requested.emit)
        card.publish_requested.connect(self.publish_requested.emit)
        card.revision_requested.connect(self.revision_requested.emit)
        card.note_edit_requested.connect(self.note_edit_requested.emit)
        card.link_assets_requested.connect(self.link_assets_requested.emit)
        card.open_linked_assets_requested.connect(self.open_linked_assets_requested.emit)
        card.remove_project_requested.connect(self.remove_project_requested.emit)
        card.flow_toggle_requested.connect(self.flow_toggle_requested.emit)
        insert_at = self._insert_index_for_project(project)
        self.cards_layout.insertWidget(insert_at, card)
        self.cards[project.id] = card
        self._update_count()

    def remove_project(self, project_id: int) -> bool:
        card = self.cards.pop(project_id, None)
        if card is None:
            return False
        self.cards_layout.removeWidget(card)
        card.deleteLater()
        self._update_count()
        return True

    def set_selected_project(self, selected_project_id: int | None) -> None:
        for project_id, card in self.cards.items():
            card.set_selected(project_id == selected_project_id)

    def _clear_cards(self) -> None:
        self.hide_drop_indicator()
        self.cards.clear()
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget is self.drop_indicator:
                    widget.hide()
                    widget.setParent(self.cards_widget)
                    continue
                widget.deleteLater()

    def _set_accent(self, color: str) -> None:
        self.setStyleSheet(
            f"""
            QFrame#KanbanColumn {{
                background: #151d2a;
                border: 1px solid #303d52;
                border-top: 2px solid {color};
                border-radius: 8px;
            }}
            QLabel#ColumnTitle {{
                color: {color};
            }}
            """
        )

    def _insert_index_for_project(self, project: Project) -> int:
        for index, card in enumerate(self._card_widgets()):
            if self._project_sorts_before(project, card.project):
                return self._layout_index_for_card_position(index)
        return self._layout_index_for_card_position(len(self._card_widgets()))

    def _card_widgets(self) -> list[ProjectCard]:
        cards: list[ProjectCard] = []
        for index in range(self.cards_layout.count()):
            item = self.cards_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, ProjectCard):
                cards.append(widget)
        return cards

    def _project_sorts_before(self, project: Project, existing: Project) -> bool:
        if project.priority != existing.priority:
            return project.priority and not existing.priority
        if project.sort_order != existing.sort_order:
            return project.sort_order < existing.sort_order
        if project.updated_at != existing.updated_at:
            return project.updated_at > existing.updated_at
        return (project.id or 0) > (existing.id or 0)

    def _layout_index_for_card_position(self, card_index: int) -> int:
        seen_cards = 0
        for layout_index in range(self.cards_layout.count()):
            item = self.cards_layout.itemAt(layout_index)
            if item is None:
                continue
            if item.spacerItem() is not None:
                return layout_index
            widget = item.widget()
            if widget is self.drop_indicator:
                continue
            if isinstance(widget, ProjectCard):
                if seen_cards == card_index:
                    return layout_index
                seen_cards += 1
        return self.cards_layout.count()

    def _card_index(self, card: ProjectCard) -> int:
        for index, existing_card in enumerate(self._card_widgets()):
            if existing_card is card:
                return index
        return -1

    def _position_in_cards(self, event, source_widget: QWidget):
        return self.cards_widget.mapFrom(source_widget, event.position().toPoint())

    def _project_id_from_mime(self, event) -> int | None:
        try:
            return int(bytes(event.mimeData().data(PROJECT_MIME)).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    def _insertion_index_at_position(self, position) -> int:
        for index, card in enumerate(self._card_widgets()):
            center_y = card.y() + card.height() / 2
            if position.y() < center_y:
                return index
        return len(self._card_widgets())

    def _adjust_drop_index_for_source(self, project_id: int, target_index: int) -> int:
        cards = self._card_widgets()
        source_card = self.cards.get(project_id)
        if source_card is None:
            return max(0, min(target_index, len(cards)))

        source_index = self._card_index(source_card)
        if source_index >= 0 and target_index > source_index:
            target_index -= 1
        return max(0, min(target_index, max(0, len(cards) - 1)))

    def _same_column_noop(self, project_id: int, drop_index: int) -> bool:
        source_card = self.cards.get(project_id)
        if source_card is None:
            return False
        return self._card_index(source_card) == drop_index

    def _show_drop_indicator(self, card_index: int) -> None:
        card_index = max(0, min(card_index, len(self._card_widgets())))
        self.cards_layout.removeWidget(self.drop_indicator)
        layout_index = self._layout_index_for_card_position(card_index)
        self.cards_layout.insertWidget(layout_index, self.drop_indicator)
        self.drop_indicator.show()

    def _show_drop_indicator_for_card(
        self,
        target_project: Project,
        before_target: bool,
    ) -> None:
        if target_project.id is None:
            return
        target_card = self.cards.get(target_project.id)
        if target_card is None:
            return
        target_index = self._card_index(target_card)
        if target_index < 0:
            return
        self._show_drop_indicator(target_index if before_target else target_index + 1)

    def hide_drop_indicator(self) -> None:
        self.cards_layout.removeWidget(self.drop_indicator)
        self.drop_indicator.hide()

    def eventFilter(self, watched, event) -> bool:
        if watched == self.cards_widget:
            if event.type() == QEvent.Type.DragEnter:
                self.handle_drag_enter(event, self.cards_widget)
                return event.isAccepted()
            if event.type() == QEvent.Type.DragMove:
                self.handle_drag_move(event, self.cards_widget)
                return event.isAccepted()
            if event.type() == QEvent.Type.Drop:
                self.handle_drop(event, self.cards_widget)
                return event.isAccepted()
            if event.type() == QEvent.Type.DragLeave:
                self.hide_drop_indicator()
        return super().eventFilter(watched, event)

    def _drop_project_on_card(
        self,
        project_id: int,
        target_project: Project,
        before_target: bool,
    ) -> None:
        if target_project.id is None:
            self.hide_drop_indicator()
            return
        target_card = self.cards.get(target_project.id)
        if target_card is None:
            self.hide_drop_indicator()
            return

        target_index = self._card_index(target_card)
        if target_index < 0:
            self.hide_drop_indicator()
            return
        if not before_target:
            target_index += 1
        drop_index = self._adjust_drop_index_for_source(project_id, target_index)
        self.hide_drop_indicator()
        if self._same_column_noop(project_id, drop_index):
            return
        self.project_reordered.emit(project_id, self.status, drop_index)

    def _update_count(self) -> None:
        self.count.setText(str(len(self.cards)))

    def handle_drag_enter(self, event, source_widget: QWidget) -> None:
        if event.mimeData().hasFormat(PROJECT_MIME):
            position = self._position_in_cards(event, source_widget)
            self._show_drop_indicator(self._insertion_index_at_position(position))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        if self.status == "Need Edit" and event.mimeData().hasUrls():
            self.hide_drop_indicator()
            event.acceptProposedAction()
            return
        self.hide_drop_indicator()
        event.ignore()

    def handle_drag_move(self, event, source_widget: QWidget) -> None:
        if event.mimeData().hasFormat(PROJECT_MIME):
            position = self._position_in_cards(event, source_widget)
            self._show_drop_indicator(self._insertion_index_at_position(position))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        if self.status == "Need Edit" and event.mimeData().hasUrls():
            self.hide_drop_indicator()
            event.acceptProposedAction()
            return
        self.hide_drop_indicator()
        event.ignore()

    def handle_drop(self, event, source_widget: QWidget) -> None:
        self.hide_drop_indicator()
        if self.status == "Need Edit" and event.mimeData().hasUrls():
            paths = [
                Path(url.toLocalFile())
                for url in event.mimeData().urls()
                if url.isLocalFile()
            ]
            if paths:
                self.raw_files_dropped.emit(paths)
                event.acceptProposedAction()
                return

        if not event.mimeData().hasFormat(PROJECT_MIME):
            event.ignore()
            return
        project_id = self._project_id_from_mime(event)
        if project_id is None:
            event.ignore()
            return

        position = self._position_in_cards(event, source_widget)
        target_index = self._insertion_index_at_position(position)
        drop_index = self._adjust_drop_index_for_source(project_id, target_index)
        if self._same_column_noop(project_id, drop_index):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        self.project_reordered.emit(project_id, self.status, drop_index)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragEnterEvent(self, event) -> None:
        self.handle_drag_enter(event, self)

    def dragMoveEvent(self, event) -> None:
        self.handle_drag_move(event, self)

    def dropEvent(self, event) -> None:
        self.handle_drop(event, self)

    def dragLeaveEvent(self, event) -> None:
        self.hide_drop_indicator()
        super().dragLeaveEvent(event)


class KanbanBoard(QWidget):
    project_selected = Signal(object)
    add_raw_video_requested = Signal()
    publish_requested = Signal(object)
    project_status_dropped = Signal(int, str)
    project_reordered = Signal(int, str, int)
    files_dropped = Signal(object, list)
    raw_files_dropped = Signal(list)
    asset_dropped = Signal(object, int)
    assets_dropped = Signal(object, list)
    video_preview_requested = Signal(object, str)
    edited_video_requested = Signal(object)
    open_folder_requested = Signal(object)
    copy_folder_path_requested = Signal(object)
    priority_toggle_requested = Signal(object)
    video_type_edit_requested = Signal(object)
    payment_edit_requested = Signal(object)
    revision_requested = Signal(object)
    note_edit_requested = Signal(object)
    link_assets_requested = Signal(object)
    open_linked_assets_requested = Signal(object)
    remove_project_requested = Signal(object)
    flow_toggle_requested = Signal(object)

    def __init__(self, thumbnail_provider=None, sound_effects=None, parent=None) -> None:
        super().__init__(parent)
        self.columns: dict[str, KanbanColumn] = {}
        self.selected_project_id: int | None = None
        self.thumbnail_provider = thumbnail_provider
        self.sound_effects = sound_effects
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        for status in STATUSES:
            column = KanbanColumn(status, thumbnail_provider, sound_effects)
            column.project_selected.connect(self.project_selected.emit)
            column.add_requested.connect(self.add_raw_video_requested.emit)
            column.publish_requested.connect(self.publish_requested.emit)
            column.project_status_dropped.connect(self.project_status_dropped.emit)
            column.project_reordered.connect(self.project_reordered.emit)
            column.files_dropped.connect(self.files_dropped.emit)
            column.raw_files_dropped.connect(self.raw_files_dropped.emit)
            column.asset_dropped.connect(self.asset_dropped.emit)
            column.assets_dropped.connect(self.assets_dropped.emit)
            column.video_preview_requested.connect(self.video_preview_requested.emit)
            column.edited_video_requested.connect(self.edited_video_requested.emit)
            column.open_folder_requested.connect(self.open_folder_requested.emit)
            column.copy_folder_path_requested.connect(
                self.copy_folder_path_requested.emit
            )
            column.priority_toggle_requested.connect(self.priority_toggle_requested.emit)
            column.video_type_edit_requested.connect(self.video_type_edit_requested.emit)
            column.payment_edit_requested.connect(self.payment_edit_requested.emit)
            column.revision_requested.connect(self.revision_requested.emit)
            column.note_edit_requested.connect(self.note_edit_requested.emit)
            column.link_assets_requested.connect(self.link_assets_requested.emit)
            column.open_linked_assets_requested.connect(
                self.open_linked_assets_requested.emit
            )
            column.remove_project_requested.connect(self.remove_project_requested.emit)
            column.flow_toggle_requested.connect(self.flow_toggle_requested.emit)
            self.columns[status] = column
            layout.addWidget(column, 1)

    def set_projects(self, projects: list[Project], selected_project_id: int | None = None) -> None:
        self.selected_project_id = selected_project_id
        grouped = {status: [] for status in STATUSES}
        for project in projects:
            grouped.setdefault(project.status, []).append(project)

        for status, column in self.columns.items():
            column.set_projects(grouped.get(status, []), selected_project_id)

    def update_project(self, project: Project, selected_project_id: int | None = None) -> None:
        if project.id is None:
            return
        self.selected_project_id = selected_project_id
        for column in self.columns.values():
            column.remove_project(project.id)
        target_column = self.columns.get(project.status)
        if target_column is not None:
            target_column.add_or_update_project(project, selected_project_id)
        self.set_selected_project(selected_project_id)

    def play_drop_confirmation(self, project_id: int) -> None:
        for column in self.columns.values():
            card = column.cards.get(project_id)
            if card is not None:
                card.play_drop_confirmation()
                return

    def remove_project(self, project_id: int) -> None:
        for column in self.columns.values():
            column.remove_project(project_id)

    def set_selected_project(self, selected_project_id: int | None) -> None:
        self.selected_project_id = selected_project_id
        for column in self.columns.values():
            column.set_selected_project(selected_project_id)
