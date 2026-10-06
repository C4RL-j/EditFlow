from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QProxyStyle, QStyle, QStyleOption, QTableWidget, QTreeWidget

from ui.project_card import ASSET_IDS_MIME, ASSET_MIME


ASSET_ID_ROLE = Qt.ItemDataRole.UserRole
ASSET_PATH_ROLE = Qt.ItemDataRole.UserRole + 1
ASSET_TYPE_ROLE = Qt.ItemDataRole.UserRole + 2
ASSET_BRANCH_COLOR = "#168BFF"
ASSET_BRANCH_HOVER_COLOR = "#25D7F2"


class AssetBranchStyle(QProxyStyle):
    def drawPrimitive(self, element, option: QStyleOption, painter: QPainter, widget=None) -> None:
        if element != QStyle.PrimitiveElement.PE_IndicatorBranch or not option.state & QStyle.StateFlag.State_Children:
            super().drawPrimitive(element, option, painter, widget)
            return

        color = ASSET_BRANCH_HOVER_COLOR if option.state & QStyle.StateFlag.State_MouseOver else ASSET_BRANCH_COLOR
        rect = option.rect
        size = max(7, min(11, rect.width() - 4, rect.height() - 4))
        center = rect.center()
        half = size // 2
        if option.state & QStyle.StateFlag.State_Open:
            points = QPolygon([
                QPoint(center.x() - half, center.y() - half // 2),
                QPoint(center.x(), center.y() + half // 2),
                QPoint(center.x() + half, center.y() - half // 2),
            ])
        else:
            points = QPolygon([
                QPoint(center.x() - half // 2, center.y() - half),
                QPoint(center.x() + half // 2, center.y()),
                QPoint(center.x() - half // 2, center.y() + half),
            ])

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(points)
        painter.restore()


class AssetTableWidget(QTableWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)

    def startDrag(self, supported_actions) -> None:
        asset_id = self.current_asset_id()
        if asset_id is None:
            return
        _start_asset_drag(self, [asset_id], supported_actions)

    def current_asset_id(self) -> int | None:
        selected = self.currentRow()
        if selected < 0:
            return None
        item = self.item(selected, self.columnCount() - 1)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def selected_asset_ids(self) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        for item in self.selectedItems():
            row = item.row()
            id_item = self.item(row, self.columnCount() - 1)
            if id_item is None:
                continue
            try:
                asset_id = int(id_item.text())
            except ValueError:
                continue
            if asset_id not in seen:
                seen.add(asset_id)
                ids.append(asset_id)
        return ids


class AssetLibraryTree(QTreeWidget):
    assets_moved = Signal(list, object)
    external_paths_dropped = Signal(list, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.root_path: Path | None = None
        self._branch_style = AssetBranchStyle()
        self.setStyle(self._branch_style)
        self.setMouseTracking(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def set_root_path(self, root_path: Path) -> None:
        self.root_path = root_path

    def current_asset_id(self) -> int | None:
        item = self.currentItem()
        if item is None:
            return None
        value = item.data(0, ASSET_ID_ROLE)
        return int(value) if isinstance(value, int) else None

    def selected_asset_ids(self) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        for item in self.selectedItems():
            value = item.data(0, ASSET_ID_ROLE)
            if not isinstance(value, int) or value in seen:
                continue
            seen.add(value)
            ids.append(value)
        return ids

    def current_folder_path(self) -> Path | None:
        item = self.currentItem()
        if item is None:
            return self.root_path
        path_value = item.data(0, ASSET_PATH_ROLE)
        type_value = item.data(0, ASSET_TYPE_ROLE)
        if not isinstance(path_value, str):
            return self.root_path
        path = Path(path_value)
        if type_value == "folder":
            return path
        return path.parent

    def startDrag(self, supported_actions) -> None:
        asset_ids = self.selected_asset_ids()
        if not asset_ids:
            return
        _start_asset_drag(self, asset_ids, supported_actions)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(ASSET_IDS_MIME):
            event.acceptProposedAction()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(ASSET_IDS_MIME):
            event.acceptProposedAction()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls() and not event.mimeData().hasFormat(ASSET_IDS_MIME):
            paths = [
                Path(url.toLocalFile())
                for url in event.mimeData().urls()
                if url.isLocalFile()
            ]
            destination = self._drop_destination(event.position().toPoint())
            if paths and destination is not None:
                self.external_paths_dropped.emit(paths, destination)
                event.acceptProposedAction()
                return
            event.ignore()
            return

        if not event.mimeData().hasFormat(ASSET_IDS_MIME):
            super().dropEvent(event)
            return
        try:
            raw_value = bytes(event.mimeData().data(ASSET_IDS_MIME)).decode("utf-8")
            asset_ids = [
                int(value)
                for value in raw_value.split(",")
                if value.strip()
            ]
        except ValueError:
            event.ignore()
            return
        if not asset_ids:
            event.ignore()
            return

        destination = self._drop_destination(event.position().toPoint())
        if destination is None:
            event.ignore()
            return
        self.assets_moved.emit(asset_ids, destination)
        event.acceptProposedAction()

    def _drop_destination(self, position) -> Path | None:
        target_item = self.itemAt(position)
        destination = self.root_path
        if target_item is not None:
            path_value = target_item.data(0, ASSET_PATH_ROLE)
            type_value = target_item.data(0, ASSET_TYPE_ROLE)
            if isinstance(path_value, str):
                target_path = Path(path_value)
                destination = target_path if type_value == "folder" else target_path.parent
        return destination


class AssetListWidget(QListWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        if item is None:
            return
        asset_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(asset_id, int):
            return
        _start_asset_drag(self, [asset_id], supported_actions)


def _start_asset_drag(widget, asset_ids: list[int], supported_actions) -> None:
    if not asset_ids:
        return
    mime_data = QMimeData()
    encoded_ids = ",".join(str(asset_id) for asset_id in asset_ids)
    mime_data.setData(ASSET_IDS_MIME, QByteArray(encoded_ids.encode("utf-8")))
    if len(asset_ids) == 1:
        mime_data.setData(ASSET_MIME, QByteArray(str(asset_ids[0]).encode("utf-8")))
    drag = QDrag(widget)
    drag.setMimeData(mime_data)
    drag.exec(supported_actions)
