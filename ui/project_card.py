from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QMimeData,
    QPoint,
    Property,
    QRect,
    QRectF,
    Qt,
    QUrl,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QDrag, QPainter, QPen, QPixmap, QPolygon, QRegion
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from models.project import (
    PUBLISHED_STATUS,
    Project,
    project_ready_for_status_transition,
)


PROJECT_MIME = "application/x-editflow-project-id"
ASSET_MIME = "application/x-editflow-asset-id"
ASSET_IDS_MIME = "application/x-editflow-asset-ids"
EDITED_VIDEO_DROP_EXTENSIONS = {".mp4"}


CAPCUT_MOTION_BLUR_CACHE = Path(
    r"C:\Users\TAO\AppData\Local\CapCut\User Data\Cache\MotionBlurCache"
)


class CornerTabButton(QWidget):
    SIZE = 26

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hovered = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open CapCut MotionBlurCache")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._apply_triangle_mask()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_triangle_mask()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_cache_folder()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#8A94A3" if self._hovered else "#5E6877"))
        painter.drawPolygon(self._triangle())

    def _triangle(self) -> QPolygon:
        return QPolygon(
            [
                QPoint(0, 0),
                QPoint(self.width(), 0),
                QPoint(0, self.height()),
            ]
        )

    def _apply_triangle_mask(self) -> None:
        self.setMask(QRegion(self._triangle()))

    def _open_cache_folder(self) -> None:
        if not CAPCUT_MOTION_BLUR_CACHE.exists():
            QMessageBox.warning(
                self,
                "CapCut Cache Not Found",
                f"This folder does not exist:\n{CAPCUT_MOTION_BLUR_CACHE}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CAPCUT_MOTION_BLUR_CACHE)))


class CardVisualEffect(QGraphicsEffect):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._opacity = 1.0
        self._scale = 1.0
        self._lift = 0.0
        self._shadow_opacity = 0.0
        self._selected = False
        self._flow_active = False
        self._flow_pulse = 0.0

    def opacity(self) -> float:
        return self._opacity

    def setOpacity(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def scale(self) -> float:
        return self._scale

    def setScale(self, value: float) -> None:
        self._scale = max(0.96, min(1.05, float(value)))
        self.updateBoundingRect()
        self.update()

    def lift(self) -> float:
        return self._lift

    def setLift(self, value: float) -> None:
        self._lift = max(0.0, min(4.0, float(value)))
        self.updateBoundingRect()
        self.update()

    def shadowOpacity(self) -> float:
        return self._shadow_opacity

    def setShadowOpacity(self, value: float) -> None:
        self._shadow_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def setSelected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.updateBoundingRect()
        self.update()

    def setFlowActive(self, active: bool) -> None:
        self._flow_active = bool(active)
        self.updateBoundingRect()
        self.update()

    def flowPulse(self) -> float:
        return self._flow_pulse

    def setFlowPulse(self, value: float) -> None:
        self._flow_pulse = max(0.0, min(1.0, float(value)))
        self.updateBoundingRect()
        self.update()

    opacity = Property(float, opacity, setOpacity)
    scale = Property(float, scale, setScale)
    lift = Property(float, lift, setLift)
    shadowOpacity = Property(float, shadowOpacity, setShadowOpacity)
    flowPulse = Property(float, flowPulse, setFlowPulse)

    def boundingRectFor(self, rect: QRectF) -> QRectF:
        scale_extra = (self._scale - 1.0) * max(rect.width(), rect.height()) / 2
        shadow_extra = 8.0 * self._shadow_opacity
        selection_extra = 4.0 if self._selected else 0.0
        flow_extra = 7.0 if self._flow_active else 0.0
        extra = max(2.0, scale_extra + shadow_extra + selection_extra + flow_extra + 2)
        return rect.adjusted(-extra, -extra - self._lift, extra, extra + shadow_extra)

    def draw(self, painter: QPainter) -> None:
        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.LogicalCoordinates,
            offset,
            QGraphicsEffect.PixmapPadMode.PadToEffectiveBoundingRect,
        )
        if pixmap.isNull():
            return

        painter.save()
        painter.setOpacity(self._opacity)
        target = QRectF(
            offset.x(),
            offset.y() - self._lift,
            pixmap.width(),
            pixmap.height(),
        )
        if self._shadow_opacity > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            base_alpha = int(70 * self._shadow_opacity * self._opacity)
            for spread, alpha_scale in ((7, 0.25), (4, 0.45), (2, 0.7)):
                shadow_rect = target.adjusted(
                    7 - spread,
                    9 - spread + self._lift,
                    -7 + spread,
                    -1 + spread + self._lift,
                )
                painter.setBrush(
                    QColor(0, 0, 0, max(0, min(255, int(base_alpha * alpha_scale))))
                )
                painter.drawRoundedRect(shadow_rect, 10 + spread, 10 + spread)

        if self._scale != 1.0:
            painter.translate(target.center())
            painter.scale(self._scale, self._scale)
            painter.translate(-target.center())
        painter.drawPixmap(target.topLeft(), pixmap)

        if self._flow_active:
            pulse = self._flow_pulse
            glow_alpha = int(22 + 34 * pulse)
            border_alpha = int(120 + 105 * pulse)
            border_width = 1.2 + 1.0 * pulse
            flow_rect = target.adjusted(-3.5, -3.5, 3.5, 3.5)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            glow = QColor("#168BFF")
            glow.setAlpha(glow_alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(flow_rect.adjusted(-2.0, -2.0, 2.0, 2.0), 12, 12)

            accent = QColor("#25D7F2")
            accent.setAlpha(border_alpha)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, border_width))
            painter.drawRoundedRect(flow_rect, 11, 11)

        if self._selected:
            selection_rect = target.adjusted(-2.0, -2.0, 2.0, 2.0)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            highlight = QColor("#25D7F2")
            highlight.setAlpha(22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(highlight)
            painter.drawRoundedRect(selection_rect, 10, 10)

            outline = QColor("#25D7F2")
            outline.setAlpha(235)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(outline, 1.6))
            painter.drawRoundedRect(selection_rect.adjusted(0.8, 0.8, -0.8, -0.8), 10, 10)

        painter.restore()


class NotePreviewPopup(QWidget):
    MAX_WIDTH = 320
    MIN_WIDTH = 220
    MAX_BODY_HEIGHT = 150
    TAIL_HEIGHT = 9
    EDGE_MARGIN = 8

    def __init__(self, card: "ProjectCard", parent=None) -> None:
        super().__init__(parent)
        self.card = card
        self._tail_x = 28
        self._tail_y = 28
        self._tail_side = "bottom"
        self._hiding = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self.opacity_effect)
        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self.fade_animation.setDuration(130)
        self.fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade_animation.finished.connect(self._finish_fade)

        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setTextFormat(Qt.TextFormat.PlainText)
        self.note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.note_label.setObjectName("NotePreviewText")

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setWidget(self.note_label)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(0)
        self.layout.addWidget(self.scroll_area)
        self.hide()

    def show_for(self, text: str, anchor: QWidget) -> None:
        self.card._note_preview_hide_timer.stop()
        self._hiding = False
        self.note_label.setText(text.strip())
        self._fit_content(self.MAX_WIDTH)
        self._position_for(anchor)
        self.show()
        self.raise_()
        self.fade_animation.stop()
        self.fade_animation.setStartValue(self.opacity_effect.opacity())
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.start()

    def _fit_content(self, width: int) -> None:
        width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(width)))
        self.note_label.setFixedWidth(width - 24)
        self.note_label.adjustSize()
        body_height = min(
            self.MAX_BODY_HEIGHT,
            max(44, self.note_label.sizeHint().height() + 8),
        )
        self.scroll_area.setFixedSize(width - 20, body_height)
        total_height = body_height + 20 + self.TAIL_HEIGHT
        self.resize(width, total_height)

    def hide_smooth(self) -> None:
        if not self.isVisible():
            return
        self._hiding = True
        self.fade_animation.stop()
        self.fade_animation.setStartValue(self.opacity_effect.opacity())
        self.fade_animation.setEndValue(0.0)
        self.fade_animation.start()

    def _finish_fade(self) -> None:
        if self._hiding:
            self.hide()
            self._hiding = False

    def _position_for(self, anchor: QWidget) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        anchor_center = anchor.mapTo(parent, QPoint(anchor.width() // 2, anchor.height() // 2))
        card_top_left = self.card.mapTo(parent, QPoint(0, 0))
        card_rect = QRect(card_top_left, self.card.size())
        safe = parent.rect().adjusted(
            self.EDGE_MARGIN,
            self.EDGE_MARGIN,
            -self.EDGE_MARGIN,
            -self.EDGE_MARGIN,
        )

        gap = 5
        self._fit_content(self.MAX_WIDTH)
        above_y = card_rect.top() - self.height() - gap
        below_y = card_rect.bottom() + gap
        if above_y >= safe.top():
            self._place_above(anchor_center, safe, above_y)
            return
        if below_y + self.height() <= safe.bottom():
            self._place_below(anchor_center, safe, below_y)
            return

        right_width = safe.right() - card_rect.right() - gap + 1
        if right_width >= self.MIN_WIDTH:
            self._fit_content(min(self.MAX_WIDTH, right_width))
            x = card_rect.right() + gap
            y = max(safe.top(), min(anchor_center.y() - self.height() // 2, safe.bottom() - self.height() + 1))
            self._tail_side = "left"
            self.layout.setContentsMargins(10 + self.TAIL_HEIGHT, 10, 10, 10)
            self._tail_y = max(18, min(self.height() - 18, anchor_center.y() - y))
            self.move(x, y)
            self.update()
            return

        left_width = card_rect.left() - safe.left() - gap
        if left_width >= self.MIN_WIDTH:
            self._fit_content(min(self.MAX_WIDTH, left_width))
            x = card_rect.left() - gap - self.width()
            y = max(safe.top(), min(anchor_center.y() - self.height() // 2, safe.bottom() - self.height() + 1))
            self._tail_side = "right"
            self.layout.setContentsMargins(10, 10, 10 + self.TAIL_HEIGHT, 10)
            self._tail_y = max(18, min(self.height() - 18, anchor_center.y() - y))
            self.move(x, y)
            self.update()
            return

        y = max(safe.top(), min(above_y, safe.bottom() - self.height() + 1))
        if y + self.height() <= card_rect.top() or y > anchor_center.y():
            self._place_above(anchor_center, safe, y)
        else:
            self._place_below(anchor_center, safe, max(safe.top(), min(below_y, safe.bottom() - self.height() + 1)))

    def _place_above(self, anchor_center: QPoint, safe: QRect, y: int) -> None:
        x = anchor_center.x() - self.width() // 2
        x = max(safe.left(), min(x, safe.right() - self.width() + 1))
        self._tail_side = "bottom"
        self.layout.setContentsMargins(10, 10, 10, 10 + self.TAIL_HEIGHT)
        self._tail_x = max(18, min(self.width() - 18, anchor_center.x() - x))
        self.move(x, y)
        self.update()

    def _place_below(self, anchor_center: QPoint, safe: QRect, y: int) -> None:
        x = anchor_center.x() - self.width() // 2
        x = max(safe.left(), min(x, safe.right() - self.width() + 1))
        self._tail_side = "top"
        self.layout.setContentsMargins(10, 10 + self.TAIL_HEIGHT, 10, 10)
        self._tail_x = max(18, min(self.width() - 18, anchor_center.x() - x))
        self.move(x, y)
        self.update()

    def enterEvent(self, event) -> None:
        self.card._note_preview_hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.card._schedule_note_preview_hide()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#223653"))
        painter.setBrush(QColor("#101E32"))
        if self._tail_side == "bottom":
            bubble = self.rect().adjusted(0, 0, -1, -self.TAIL_HEIGHT - 1)
            tail = [
                QPoint(self._tail_x - 7, bubble.bottom()),
                QPoint(self._tail_x + 7, bubble.bottom()),
                QPoint(self._tail_x, self.height() - 1),
            ]
        elif self._tail_side == "top":
            bubble = self.rect().adjusted(0, self.TAIL_HEIGHT, -1, -1)
            tail = [
                QPoint(self._tail_x, 0),
                QPoint(self._tail_x - 7, bubble.top()),
                QPoint(self._tail_x + 7, bubble.top()),
            ]
        elif self._tail_side == "left":
            bubble = self.rect().adjusted(self.TAIL_HEIGHT, 0, -1, -1)
            tail = [
                QPoint(0, self._tail_y),
                QPoint(bubble.left(), self._tail_y - 7),
                QPoint(bubble.left(), self._tail_y + 7),
            ]
        else:
            bubble = self.rect().adjusted(0, 0, -self.TAIL_HEIGHT - 1, -1)
            tail = [
                QPoint(self.width() - 1, self._tail_y),
                QPoint(bubble.right(), self._tail_y - 7),
                QPoint(bubble.right(), self._tail_y + 7),
            ]
        painter.drawRoundedRect(bubble, 8, 8)
        painter.drawPolygon(tail)
        super().paintEvent(event)


class ProjectCard(QFrame):
    clicked = Signal(object)
    files_dropped = Signal(object, list)
    asset_dropped = Signal(object, int)
    assets_dropped = Signal(object, list)
    project_status_dropped = Signal(int, str)
    project_drag_positioned = Signal(object, bool)
    project_order_dropped = Signal(int, object, bool)
    project_drag_left = Signal()
    video_preview_requested = Signal(object, str)
    edited_video_requested = Signal(object)
    edited_video_dropped = Signal(object, object)
    open_folder_requested = Signal(object)
    copy_folder_path_requested = Signal(object)
    priority_toggle_requested = Signal(object)
    video_type_edit_requested = Signal(object)
    payment_edit_requested = Signal(object)
    publish_requested = Signal(object)
    revision_requested = Signal(object)
    note_edit_requested = Signal(object)
    link_assets_requested = Signal(object)
    open_linked_assets_requested = Signal(object)
    remove_project_requested = Signal(object)
    flow_toggle_requested = Signal(object)

    def __init__(
        self,
        project: Project,
        thumbnail_provider=None,
        sound_effects=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.thumbnail_provider = thumbnail_provider
        self.sound_effects = sound_effects
        self._drag_start_position = None
        self._dragging = False
        self._hovered = False
        self._suppress_release_click = False
        self._drop_animation: QPropertyAnimation | None = None
        self._note_preview_popup: NotePreviewPopup | None = None
        self._note_preview_hide_timer = QTimer(self)
        self._note_preview_hide_timer.setSingleShot(True)
        self._note_preview_hide_timer.setInterval(170)
        self._note_preview_hide_timer.timeout.connect(self._hide_note_preview)
        self._thumbnail_labels: dict[str, QLabel] = {}
        self.setObjectName("ProjectCard")
        self.setProperty("selected", False)
        self.setProperty("readiness", self._readiness_state())
        self.setProperty("dragging", False)
        self.setProperty("hovered", False)
        self.setProperty("flow", project.in_flow)
        self._visual_effect = CardVisualEffect(self)
        self._visual_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._visual_effect)
        self._hover_lift_animation = QPropertyAnimation(
            self._visual_effect,
            b"lift",
            self,
        )
        self._hover_lift_animation.setDuration(140)
        self._hover_lift_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_shadow_animation = QPropertyAnimation(
            self._visual_effect,
            b"shadowOpacity",
            self,
        )
        self._hover_shadow_animation.setDuration(140)
        self._hover_shadow_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._flow_pulse_animation = QSequentialAnimationGroup(self)
        flow_brighten = QPropertyAnimation(self._visual_effect, b"flowPulse", self)
        flow_brighten.setDuration(800)
        flow_brighten.setStartValue(0.0)
        flow_brighten.setEndValue(1.0)
        flow_brighten.setEasingCurve(QEasingCurve.Type.InOutSine)
        flow_soften = QPropertyAnimation(self._visual_effect, b"flowPulse", self)
        flow_soften.setDuration(800)
        flow_soften.setStartValue(1.0)
        flow_soften.setEndValue(0.0)
        flow_soften.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._flow_pulse_animation.addAnimation(flow_brighten)
        self._flow_pulse_animation.addAnimation(flow_soften)
        self._flow_pulse_animation.setLoopCount(-1)
        self._set_flow_visual(project.in_flow)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setToolTip(self._readiness_message())

        self.corner_tab_button = CornerTabButton(self)

        self.raw_thumbnail_label = self._thumbnail_label("Raw")
        self.edited_thumbnail_label = self._thumbnail_label("Edited")
        self.edited_thumbnail_label.setAcceptDrops(True)
        self.edited_thumbnail_label.setProperty("drop_active", "false")
        self.edited_thumbnail_label.setVisible(project.has_edited_video)
        self._add_edited_button_text = "+ Add\nEdited Video"
        self.add_edited_button = QPushButton(self._add_edited_button_text, self)
        self.add_edited_button.setObjectName("AddEditedVideoButton")
        self.add_edited_button.setProperty("drop_active", "false")
        self.add_edited_button.setAcceptDrops(True)
        self.add_edited_button.installEventFilter(self)
        self.add_edited_button.setFixedSize(86, 40)
        self.add_edited_button.setToolTip("Add Edited Video")
        self.add_edited_button.clicked.connect(
            lambda _checked=False: self.edited_video_requested.emit(self.project)
        )
        self.add_edited_button.setVisible(not project.has_edited_video)
        self.priority_button = QPushButton("📌", self)
        self.priority_button.setObjectName("PriorityButton")
        self.priority_button.setProperty("priority", project.priority)
        self.priority_button.setFixedSize(22, 22)
        self.priority_button.setToolTip(
            "Important project" if project.priority else "Mark as important"
        )
        self.priority_button.clicked.connect(self._emit_priority_toggle)
        self.priority_button.setVisible(project.priority)
        self.note_indicator = QLabel("📝", self)
        self.note_indicator.setObjectName("NoteIndicator")
        self.note_indicator.setProperty("has_notes", self._has_note_preview())
        self.note_indicator.setFixedSize(22, 22)
        self.note_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note_indicator.setCursor(Qt.CursorShape.PointingHandCursor)
        self.note_indicator.setToolTip("")
        self.note_indicator.installEventFilter(self)
        self.note_indicator.setVisible(self._has_note_preview())

        thumbnail_row = QHBoxLayout()
        thumbnail_row.setContentsMargins(0, 0, 0, 0)
        thumbnail_row.setSpacing(8)
        thumbnail_row.addWidget(self.raw_thumbnail_label)
        thumbnail_row.addWidget(self.edited_thumbnail_label)
        thumbnail_row.addWidget(self.add_edited_button)
        thumbnail_row.addStretch(1)

        title = QLabel(project.name)
        title.setObjectName("CardTitle")
        title.setWordWrap(True)

        client = QLabel(project.client or "No client")
        client.setObjectName("MutedLabel")

        video_type_text = self._video_type_text()
        self.video_type_label = QLabel(video_type_text)
        self.video_type_label.setObjectName("VideoTypePill")
        self.video_type_label.setProperty(
            "compact",
            "true" if len(video_type_text) > 8 else "false",
        )
        self.video_type_label.setProperty(
            "kind",
            self._video_type_kind(video_type_text),
        )
        self.video_type_label.setMinimumWidth(0)
        self.video_type_label.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.video_type_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.video_type_label.setToolTip(f"Video type: {video_type_text}. Click to change.")
        self.video_type_label.installEventFilter(self)

        self.edited_label = QLabel(
            "Edited: Yes" if project.has_edited_video else "Edited: No"
        )
        self.edited_label.setObjectName(
            "GoodPill" if project.has_edited_video else "NeutralPill"
        )

        raw_file = QLabel(f"Raw: {project.raw_video_name}")
        raw_file.setObjectName("PathLabel")
        raw_file.setWordWrap(True)

        self.link_assets_button = QPushButton("🔗", self)
        self.link_assets_button.setObjectName("CardIconButton")
        self.link_assets_button.setFixedSize(24, 24)
        self.link_assets_button.setToolTip("Link assets")
        self.link_assets_button.clicked.connect(
            lambda _checked=False: self.link_assets_requested.emit(self.project)
        )

        self.asset_count_label = QLabel(f"📦 Assets: {project.asset_count}")
        self.asset_count_label.setObjectName("AssetCountLink")
        self.asset_count_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.asset_count_label.setToolTip("Open Linked Assets folder")
        self.asset_count_label.installEventFilter(self)

        earnings_text = self._earnings_text()
        self.earnings_label = QLabel(earnings_text)
        self.earnings_label.setObjectName("EarningsBadge")
        self.earnings_label.setProperty(
            "earned",
            "true" if self.project.status == PUBLISHED_STATUS else "false",
        )
        self.earnings_label.setVisible(bool(earnings_text))
        self.earnings_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.earnings_label.setToolTip("Double-click to edit this project earnings amount")
        self.earnings_label.installEventFilter(self)

        asset_row = QHBoxLayout()
        asset_row.setContentsMargins(0, 0, 0, 0)
        asset_row.setSpacing(6)
        asset_row.addWidget(self.link_assets_button)
        asset_row.addWidget(self.asset_count_label)
        asset_row.addStretch(1)
        asset_row.addWidget(self.earnings_label)

        pill_row = QHBoxLayout()
        pill_row.setContentsMargins(0, 0, 0, 0)
        pill_row.setSpacing(6)
        pill_row.addWidget(self.video_type_label)
        pill_row.addWidget(self.edited_label)
        pill_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(thumbnail_row)
        layout.addWidget(title)
        layout.addWidget(client)
        layout.addLayout(pill_row)
        layout.addWidget(raw_file)
        layout.addLayout(asset_row)

        self.setMinimumHeight(210)
        self._position_overlay_icons()
        self._load_thumbnails()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self._visual_effect.setSelected(selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def _set_flow_visual(self, active: bool) -> None:
        self._visual_effect.setFlowActive(active)
        if active:
            if self._flow_pulse_animation.state() != QSequentialAnimationGroup.State.Running:
                self._flow_pulse_animation.start()
            return
        self._flow_pulse_animation.stop()
        self._visual_effect.setFlowPulse(0.0)

    def _set_drag_visual(self, dragging: bool) -> None:
        if dragging:
            self._set_hover_visual(False, animated=False)
        self.setProperty("dragging", dragging)
        self._visual_effect.setOpacity(0.48 if dragging else 1.0)
        self.style().unpolish(self)
        self.style().polish(self)

    def _set_hover_visual(self, hovered: bool, *, animated: bool = True) -> None:
        if hovered and self._dragging:
            return
        self._hovered = hovered
        self.setProperty("hovered", hovered)
        self.style().unpolish(self)
        self.style().polish(self)

        target_lift = 2.5 if hovered else 0.0
        target_shadow = 1.0 if hovered else 0.0
        self._hover_lift_animation.stop()
        self._hover_shadow_animation.stop()
        if not animated:
            self._visual_effect.setLift(target_lift)
            self._visual_effect.setShadowOpacity(target_shadow)
            return

        self._hover_lift_animation.setStartValue(self._visual_effect.lift)
        self._hover_lift_animation.setEndValue(target_lift)
        self._hover_shadow_animation.setStartValue(
            self._visual_effect.shadowOpacity
        )
        self._hover_shadow_animation.setEndValue(target_shadow)
        self._hover_lift_animation.start()
        self._hover_shadow_animation.start()

    def play_drop_confirmation(self) -> None:
        if self._drop_animation is not None:
            self._drop_animation.stop()
        self._set_drag_visual(False)
        self._visual_effect.setScale(1.0)
        animation = QPropertyAnimation(self._visual_effect, b"scale", self)
        animation.setDuration(190)
        animation.setStartValue(1.0)
        animation.setKeyValueAt(0.45, 1.035)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(self._finish_drop_confirmation)
        self._drop_animation = animation
        animation.start()

    def _finish_drop_confirmation(self) -> None:
        self._visual_effect.setScale(1.0)
        self._drop_animation = None

    def _emit_priority_toggle(self) -> None:
        self.priority_toggle_requested.emit(self.project)

    def enterEvent(self, event) -> None:
        self._set_hover_visual(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_hover_visual(False)
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_overlay_icons()

    def _position_overlay_icons(self) -> None:
        margin = 7
        spacing = 4
        self.corner_tab_button.move(0, 0)
        self.corner_tab_button.raise_()

        right_x = max(margin, self.width() - self.priority_button.width() - margin)

        self.priority_button.move(right_x, margin)
        self.priority_button.raise_()

        note_y = margin
        if not self.priority_button.isHidden():
            note_y = margin + self.priority_button.height() + spacing
        self.note_indicator.move(right_x, note_y)
        self.note_indicator.raise_()

    def _has_notes(self) -> bool:
        return bool(self.project.notes.strip())

    def _has_revision_notes(self) -> bool:
        return self.project.open_revision_count > 0

    def _has_note_preview(self) -> bool:
        return self._has_notes() or self._has_revision_notes()

    def _note_preview_text(self) -> str:
        parts: list[str] = []
        if self.project.open_revision_count > 0:
            noun = "revision note" if self.project.open_revision_count == 1 else "revision notes"
            parts.append(
                f"Revision Notes\n{self.project.open_revision_count} incomplete {noun}. Complete all revisions before moving this card forward."
            )
        note = self.project.notes.strip()
        if note:
            parts.append(("Notes\n" if parts else "") + note)
        return "\n\n".join(parts)

    def _video_type_text(self) -> str:
        return self.project.video_type.strip() or "Type needed"

    def _video_type_kind(self, video_type: str) -> str:
        normalized = video_type.strip().casefold()
        if normalized == "ugc":
            return "ugc"
        if normalized == "personal brand":
            return "personal_brand"
        return "other"

    def _earnings_text(self) -> str:
        if self.project.status == PUBLISHED_STATUS:
            if self.project.earned_amount > 0 or self.project.estimated_payment_custom:
                return f"${self.project.earned_amount:.2f}"
            return ""
        if self.project.estimated_payment > 0 or self.project.estimated_payment_custom:
            return f"${self.project.estimated_payment:.2f}"
        return ""

    def _note_tooltip(self) -> str:
        note = self.project.notes.strip()
        if not note:
            return (
                "<div><b>Notes</b><br>"
                "<span>No notes yet.</span><br>"
                "<span style='color:#8EA4C2;'>Click to add a note.</span></div>"
            )
        preview = escape(note[:900]).replace("\n", "<br>")
        if len(note) > 900:
            preview = f"{preview}<br>..."
        return (
            "<div style='max-width:280px;'>"
            "<b>Notes</b><br>"
            f"<span>{preview}</span><br>"
            "<span style='color:#8EA4C2;'>Click to edit.</span>"
            "</div>"
        )

    def eventFilter(self, watched, event) -> bool:
        if self._is_edited_drop_target(watched):
            if event.type() in {QEvent.Type.DragEnter, QEvent.Type.DragMove}:
                video_path = self._edited_video_path_from_mime(event.mimeData())
                if video_path is not None:
                    self._set_edited_drop_active(True)
                    event.acceptProposedAction()
                    return True
                self._set_edited_drop_active(False)
                event.ignore()
                return True
            if event.type() == QEvent.Type.DragLeave:
                self._set_edited_drop_active(False)
                event.accept()
                return True
            if event.type() == QEvent.Type.Drop:
                video_path = self._edited_video_path_from_mime(event.mimeData())
                self._set_edited_drop_active(False)
                if video_path is not None:
                    self.edited_video_dropped.emit(self.project, video_path)
                    event.acceptProposedAction()
                    return True
                event.ignore()
                return True

        if hasattr(self, "note_indicator") and watched == self.note_indicator:
            if event.type() == QEvent.Type.Enter:
                self._show_note_preview()
                return super().eventFilter(watched, event)
            if event.type() == QEvent.Type.Leave:
                self._schedule_note_preview_hide()
                return super().eventFilter(watched, event)
            if event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._hide_note_preview()
                    self.note_edit_requested.emit(self.project)
                    event.accept()
                    return True
            return super().eventFilter(watched, event)

        if hasattr(self, "asset_count_label") and watched == self.asset_count_label:
            if event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.open_linked_assets_requested.emit(self.project)
                    event.accept()
                    return True
            return super().eventFilter(watched, event)

        if hasattr(self, "video_type_label") and watched == self.video_type_label:
            if event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.video_type_edit_requested.emit(self.project)
                    event.accept()
                    return True
            return super().eventFilter(watched, event)

        if hasattr(self, "earnings_label") and watched == self.earnings_label:
            if event.type() == QEvent.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.payment_edit_requested.emit(self.project)
                    event.accept()
                    return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                event.accept()
                return True
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.MouseButtonDblClick:
            if watched == self.raw_thumbnail_label:
                self.video_preview_requested.emit(self.project, "raw")
                event.accept()
                return True
            if watched == self.edited_thumbnail_label and self.project.edited_video_path is not None:
                self.video_preview_requested.emit(self.project, "edited")
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _is_edited_drop_target(self, watched) -> bool:
        return watched in {
            getattr(self, "add_edited_button", None),
            getattr(self, "edited_thumbnail_label", None),
        }

    def _edited_video_path_from_mime(self, mime_data) -> Path | None:
        if not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.casefold() in EDITED_VIDEO_DROP_EXTENSIONS:
                return path
        return None

    def _set_edited_drop_active(self, active: bool) -> None:
        value = "true" if active else "false"
        for widget in (self.add_edited_button, self.edited_thumbnail_label):
            widget.setProperty("drop_active", value)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.add_edited_button.setText("Drop Edited Video" if active else self._add_edited_button_text)

    def _show_note_preview(self) -> None:
        if not self._has_note_preview() or self._dragging:
            return
        parent = self.window()
        if self._note_preview_popup is None or self._note_preview_popup.parentWidget() is not parent:
            if self._note_preview_popup is not None:
                self._note_preview_popup.deleteLater()
            self._note_preview_popup = NotePreviewPopup(self, parent)
        self._note_preview_popup.show_for(self._note_preview_text(), self.note_indicator)

    def _schedule_note_preview_hide(self) -> None:
        self._note_preview_hide_timer.start()

    def _hide_note_preview(self) -> None:
        if self._note_preview_popup is not None:
            self._note_preview_popup.hide_smooth()
    def _readiness_state(self) -> str:
        if self.project.status == "Done":
            return "normal"
        if self.project.open_revision_count > 0:
            return "revision"

        ready, _message = project_ready_for_status_transition(
            self.project,
            self.project.status,
        )
        if ready:
            return "ready"
        return "normal"

    def _readiness_message(self) -> str:
        if self.project.status == "Done":
            return "Done"
        if self.project.open_revision_count > 0:
            return f"{self.project.open_revision_count} revision note open"
        _ready, message = project_ready_for_status_transition(
            self.project,
            self.project.status,
        )
        if message:
            return message
        return ""

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_position = event.position().toPoint()
            self._dragging = False
            self._suppress_release_click = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        if self._drag_start_position is None:
            super().mouseMoveEvent(event)
            return

        distance = (event.position().toPoint() - self._drag_start_position).manhattanLength()
        if distance < 10 or self.project.id is None:
            super().mouseMoveEvent(event)
            return

        self._dragging = True
        self._suppress_release_click = True
        self._set_drag_visual(True)
        if self.sound_effects is not None:
            self.sound_effects.play("pickup_card")
        mime_data = QMimeData()
        mime_data.setData(PROJECT_MIME, QByteArray(str(self.project.id).encode("utf-8")))

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.position().toPoint())
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._set_drag_visual(False)
            self._dragging = False
            self._drag_start_position = None

    def mouseReleaseEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._dragging
            and not self._suppress_release_click
        ):
            self.clicked.emit(self.project)
        if event.button() == Qt.MouseButton.LeftButton:
            self._suppress_release_click = False
        self._dragging = False
        self._drag_start_position = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_folder_requested.emit(self.project)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        copy_folder_path_action = menu.addAction("📋 Copy Folder Path")
        note_action = menu.addAction(
            "📝 Edit Note" if self._has_notes() else "📝 Add Note"
        )
        priority_action = menu.addAction(
            "📌 Remove Priority" if self.project.priority else "📌 Mark Priority"
        )
        flow_action = menu.addAction(
            "🌊 Exit Flow" if self.project.in_flow else "🌊 Enter Flow"
        )
        link_assets_action = menu.addAction("🔗 Link Assets")
        menu.addSeparator()
        revision_action = menu.addAction("📝 Revision")
        publish_action = None
        if self.project.status == "Done":
            menu.addSeparator()
            publish_action = menu.addAction("🌐 Mark as Published")
        menu.addSeparator()
        remove_project_action = menu.addAction("🗑 Remove Project")
        selected_action = menu.exec(event.globalPos())
        if selected_action == copy_folder_path_action:
            self.copy_folder_path_requested.emit(self.project)
        elif selected_action == note_action:
            self.note_edit_requested.emit(self.project)
        elif selected_action == priority_action:
            self.priority_toggle_requested.emit(self.project)
        elif selected_action == flow_action:
            self.flow_toggle_requested.emit(self.project)
        elif selected_action == link_assets_action:
            self.link_assets_requested.emit(self.project)
        elif selected_action == revision_action:
            self.revision_requested.emit(self.project)
        elif publish_action is not None and selected_action == publish_action:
            self.publish_requested.emit(self.project)
        elif selected_action == remove_project_action:
            self.remove_project_requested.emit(self.project)

    def _project_id_from_mime(self, mime_data) -> int | None:
        try:
            return int(bytes(mime_data.data(PROJECT_MIME)).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    def _drop_before_midpoint(self, event) -> bool:
        return event.position().y() < self.height() / 2

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(PROJECT_MIME):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        if (
            event.mimeData().hasUrls()
            or event.mimeData().hasFormat(ASSET_IDS_MIME)
            or event.mimeData().hasFormat(ASSET_MIME)
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(PROJECT_MIME):
            self.project_drag_positioned.emit(
                self.project,
                self._drop_before_midpoint(event),
            )
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        if (
            event.mimeData().hasUrls()
            or event.mimeData().hasFormat(ASSET_IDS_MIME)
            or event.mimeData().hasFormat(ASSET_MIME)
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.project_drag_left.emit()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        mime_data = event.mimeData()
        if mime_data.hasFormat(PROJECT_MIME):
            project_id = self._project_id_from_mime(mime_data)
            if project_id is None:
                event.ignore()
                return
            self.project_order_dropped.emit(
                project_id,
                self.project,
                self._drop_before_midpoint(event),
            )
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return

        if mime_data.hasFormat(ASSET_IDS_MIME):
            try:
                raw_value = bytes(mime_data.data(ASSET_IDS_MIME)).decode("utf-8")
                asset_ids = [
                    int(value)
                    for value in raw_value.split(",")
                    if value.strip()
                ]
            except ValueError:
                event.ignore()
                return
            if asset_ids:
                self.assets_dropped.emit(self.project, asset_ids)
                event.acceptProposedAction()
                return

        if mime_data.hasFormat(ASSET_MIME):
            try:
                asset_id = int(bytes(mime_data.data(ASSET_MIME)).decode("utf-8"))
            except ValueError:
                event.ignore()
                return
            self.asset_dropped.emit(self.project, asset_id)
            event.acceptProposedAction()
            return

        if mime_data.hasUrls():
            paths = [
                Path(url.toLocalFile())
                for url in mime_data.urls()
                if url.isLocalFile()
            ]
            if paths:
                self.files_dropped.emit(self.project, paths)
                event.acceptProposedAction()
                return

        event.ignore()

    def _thumbnail_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Thumbnail")
        label.setFixedSize(72, 40)
        label.setScaledContents(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setToolTip(f"Double-click to preview {text.lower()} video")
        label.installEventFilter(self)
        return label

    def _load_thumbnails(self) -> None:
        if self.thumbnail_provider is None:
            return
        self.thumbnail_provider.thumbnail_ready.connect(self._handle_thumbnail_ready)
        self._request_thumbnail(self.project.raw_video_path, self.raw_thumbnail_label)
        if self.project.edited_video_path is not None:
            self._request_thumbnail(
                self.project.edited_video_path,
                self.edited_thumbnail_label,
            )

    def _request_thumbnail(self, path: Path, label: QLabel) -> None:
        if not str(path):
            return
        try:
            normalized_path = self.thumbnail_provider.normalized(path)
        except OSError:
            return
        self._thumbnail_labels[normalized_path] = label
        cached = self.thumbnail_provider.cached_thumbnail(path)
        if cached is not None:
            self._set_thumbnail(label, cached)
            return
        self.thumbnail_provider.request(path)

    def _handle_thumbnail_ready(self, normalized_path: str, cache_path: str) -> None:
        label = self._thumbnail_labels.get(normalized_path)
        if label is not None:
            self._set_thumbnail(label, Path(cache_path))

    def _set_thumbnail(self, label: QLabel, thumbnail_path: Path) -> None:
        pixmap = QPixmap(str(thumbnail_path))
        if not pixmap.isNull():
            label.setPixmap(
                pixmap.scaled(
                    label.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
