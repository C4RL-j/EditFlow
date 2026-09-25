from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    Property,
    QRectF,
    QSize,
    Qt,
    QPropertyAnimation,
    Signal,
)
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.project import PUBLISHED_STATUS, workflow_stage_label


class SidebarScaleEffect(QGraphicsEffect):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scale = 1.0

    def scale(self) -> float:
        return self._scale

    def setScale(self, value: float) -> None:
        self._scale = max(1.0, min(1.1, float(value)))
        self.updateBoundingRect()
        self.update()

    scale = Property(float, scale, setScale)

    def boundingRectFor(self, rect: QRectF) -> QRectF:
        extra = (self._scale - 1.0) * max(rect.width(), rect.height()) / 2 + 2
        return rect.adjusted(-extra, -extra, extra, extra)

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
        rect = QRectF(offset.x(), offset.y(), pixmap.width(), pixmap.height())
        painter.translate(rect.center())
        painter.scale(self._scale, self._scale)
        painter.translate(-rect.center())
        painter.drawPixmap(offset, pixmap)
        painter.restore()


class SidebarButton(QPushButton):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__("", parent)
        self._collapsed = False
        self._scale_effect = SidebarScaleEffect(self)
        self._scale_animation = QPropertyAnimation(self._scale_effect, b"scale", self)
        self._scale_animation.setDuration(130)
        self._scale_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setGraphicsEffect(self._scale_effect)
        self.setProperty("collapsed", False)
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.icon_label = QLabel()
        self.icon_label.setObjectName("SidebarButtonIcon")
        self.icon_label.setFixedSize(40, 40)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.text_label = QLabel()
        self.text_label.setObjectName("SidebarButtonLabel")
        self.text_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        button_layout = QHBoxLayout(self)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(0)
        button_layout.addWidget(self.icon_label)
        button_layout.addWidget(self.text_label, 1)
        self.setText(text)

    def setText(self, text: str) -> None:
        if not hasattr(self, "icon_label"):
            super().setText(text)
            return

        icon, _separator, label = text.partition(" ")
        self.icon_label.setText(icon)
        self.text_label.setText(label)
        super().setText("")

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.setProperty("collapsed", collapsed)
        self.text_label.setVisible(not collapsed)
        self.setFixedHeight(40)
        if collapsed:
            self.setFixedWidth(40)
        else:
            self.setMinimumWidth(40)
            self.setMaximumWidth(16777215)
        self._animate_scale(1.0)
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event) -> None:
        if self._collapsed:
            self._animate_scale(1.07)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._collapsed:
            self._animate_scale(1.0)
        super().leaveEvent(event)

    def _animate_scale(self, value: float) -> None:
        self._scale_animation.stop()
        self._scale_animation.setStartValue(self._scale_effect.scale)
        self._scale_animation.setEndValue(value)
        self._scale_animation.start()


class Sidebar(QFrame):
    view_requested = Signal(str)
    add_raw_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.expanded_width = 204
        self.collapsed_width = 64
        self.collapsed = False
        self.setFixedWidth(self.expanded_width)

        self.logo_label = QPushButton("EF")
        self.logo_label.setObjectName("LogoButton")
        self.logo_label.setFixedSize(40, 40)
        self.logo_label.setIconSize(QSize(28, 28))
        self.logo_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logo_label.setToolTip("EditFlow")
        self.logo_label.clicked.connect(self._handle_logo_clicked)
        self._load_logo()

        self.title = QLabel("EditFlow")
        self.title.setObjectName("AppTitle")

        self.subtitle = QLabel("Visual file organizer")
        self.subtitle.setObjectName("MutedLabel")

        self.title_container = QWidget()
        title_stack = QVBoxLayout(self.title_container)
        title_stack.setContentsMargins(8, 0, 0, 0)
        title_stack.setSpacing(0)
        title_stack.addWidget(self.title)
        title_stack.addWidget(self.subtitle)

        self.collapse_button = QPushButton("<")
        self.collapse_button.setObjectName("IconButton")
        self.collapse_button.setFixedSize(24, 24)
        self.collapse_button.setToolTip("Collapse sidebar")
        self.collapse_button.clicked.connect(self.toggle_collapsed)

        self.header = QHBoxLayout()
        self.header.setContentsMargins(0, 0, 0, 0)
        self.header.setSpacing(0)
        self.header.addWidget(self.logo_label)
        self.header.addWidget(self.title_container, 1)
        self.header.addWidget(self.collapse_button)

        self.nav_buttons: list[tuple[SidebarButton, str, str]] = []
        dashboard = self._nav_button("📌 Dashboard", "📌", "dashboard")
        projects = self._nav_button("📁 Projects", "📁", "projects")
        assets = self._nav_button("📦 Assets", "📦", "assets")
        published_label = workflow_stage_label(PUBLISHED_STATUS)
        publish = self._nav_button(published_label, "🌐", "publish")
        clients = self._nav_button("👤 Clients", "👤", "clients")
        settings = self._nav_button("⚙ Settings", "⚙", "settings")

        self.add_raw = SidebarButton("➕ Raw")
        self.add_raw.setObjectName("PrimaryButton")
        self.add_raw.setToolTip("Add Raw Video")
        self.add_raw.clicked.connect(self.add_raw_requested.emit)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 18, 12, 18)
        self.main_layout.setSpacing(12)
        self.main_layout.addLayout(self.header)
        self.main_layout.addSpacing(24)
        self.main_layout.addWidget(dashboard)
        self.main_layout.addWidget(projects)
        self.main_layout.addWidget(assets)
        self.main_layout.addWidget(publish)
        self.main_layout.addWidget(clients)
        self.main_layout.addWidget(settings)
        self.main_layout.addSpacing(20)
        self.main_layout.addWidget(self.add_raw)
        self.main_layout.addStretch(1)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self.collapsed)

    def _handle_logo_clicked(self) -> None:
        if self.collapsed:
            self.set_collapsed(False)

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        self.setFixedWidth(self.collapsed_width if collapsed else self.expanded_width)
        self.title.setVisible(not collapsed)
        self.subtitle.setVisible(not collapsed)
        self.title_container.setVisible(not collapsed)
        self.collapse_button.setVisible(not collapsed)
        self.collapse_button.setText("<")
        self.collapse_button.setToolTip("Collapse sidebar")
        self.logo_label.setToolTip("Expand sidebar" if collapsed else "EditFlow")
        self.header.setSpacing(0)
        self.main_layout.setContentsMargins(12, 18, 12, 18)
        for button, _expanded_text, _collapsed_text in self.nav_buttons:
            button.set_collapsed(collapsed)
        self.add_raw.set_collapsed(collapsed)

    def _nav_button(
        self,
        expanded_text: str,
        collapsed_text: str,
        view_name: str,
    ) -> SidebarButton:
        button = SidebarButton(expanded_text)
        button.setObjectName("NavButton")
        button.setToolTip(expanded_text)
        button.clicked.connect(lambda: self.view_requested.emit(view_name))
        button.set_collapsed(False)
        self.nav_buttons.append((button, expanded_text, collapsed_text))
        return button

    def _load_logo(self) -> None:
        assets_path = Path(__file__).resolve().parent.parent / "assets"
        logo_candidates = [
            assets_path / "logos" / "Logo.png",
            assets_path / "logos" / "editflow_logo.png",
            assets_path / "editflow_logo.png",
        ]
        logo_path = next((path for path in logo_candidates if path.exists()), None)
        if logo_path is None:
            return
        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return
        self.logo_label.setText("")
        self.logo_label.setIcon(QIcon(pixmap))
