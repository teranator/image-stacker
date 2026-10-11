#!/usr/bin/env python3
"""Clipboard-first vertical image stacker for Linux desktops."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import (
    QBuffer, QByteArray, QIODevice, QMimeData, QPoint, QRect, QRectF, QSize, Qt, QSettings, QThread, QTimer,
    QPropertyAnimation, QEasingCurve, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import (
    QAction, QColor, QCursor, QFont, QFontMetrics, QImage, QImageWriter, QKeySequence, QPainter, QPainterPath,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(eq=False)
class StackItem:
    """An image and optional user-provided label; None means auto-numbered."""

    image: QImage
    custom_label: str | None = None
    uid: str = dataclass_field(default_factory=lambda: uuid4().hex)

    def label(self, index: int) -> str:
        return self.custom_label if self.custom_label is not None else f"Image {index + 1}"


@dataclass
class StackSnapshot:
    items: list[StackItem]
    include_labels: bool


class OutputEncoder(QThread):
    """Encodes one composed output off the GUI thread, optionally fitting it below the upload target."""

    encoded = pyqtSignal(int, QImage, bytes, str)
    TARGET_BYTES = 19_000_000

    def __init__(self, revision: int, source: QImage, fit_for_chatgpt: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.revision = revision
        self.source = QImage(source)
        self.fit_for_chatgpt = fit_for_chatgpt

    @staticmethod
    def _png_bytes(image: QImage) -> tuple[bytes, str]:
        data = QBuffer()
        data.open(QIODevice.OpenModeFlag.WriteOnly)
        writer = QImageWriter(data, b"png")
        writer.setCompression(9)
        writer.setQuality(100)
        if not writer.write(image):
            return b"", writer.errorString()
        return bytes(data.data()), ""

    def run(self) -> None:
        output = self.source
        png, error = self._png_bytes(output)
        if error:
            self.encoded.emit(self.revision, QImage(), b"", error)
            return
        if self.isInterruptionRequested():
            return
        if self.fit_for_chatgpt and len(png) > self.TARGET_BYTES:
            for _ in range(16):
                if self.isInterruptionRequested():
                    return
                ratio = min(0.99, max(0.05, (self.TARGET_BYTES / len(png)) ** 0.5 * 0.99))
                width = max(1, round(self.source.width() * ratio))
                height = max(1, round(self.source.height() * ratio))
                if QSize(width, height) == output.size():
                    break
                output = self.source.scaled(
                    QSize(width, height), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                png, error = self._png_bytes(output)
                if error:
                    self.encoded.emit(self.revision, QImage(), b"", error)
                    return
                if len(png) <= self.TARGET_BYTES:
                    break
        self.encoded.emit(self.revision, output, png, "")


class ImagePreview(QWidget):
    """Scaled card preview; its QImage remains untouched at source resolution."""

    def __init__(self, image: QImage, max_height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image = image
        self.max_height = max_height
        self._scaled_cache = QImage()
        self._cache_size = QSize()
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(1)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API name
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API name
        if self.image.width() <= 0:
            return 1
        scale = min(1.0, max(1, width) / self.image.width(), self.max_height / self.image.height())
        return max(1, round(self.image.height() * scale))

    def set_max_height(self, height: int) -> None:
        if self.max_height == height:
            return
        self.max_height = height
        self._scaled_cache = QImage()
        self._cache_size = QSize()
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(400, self.heightForWidth(400))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API name
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#181a1f"))
        if self.image.isNull():
            return
        scale = min(1.0, self.width() / self.image.width(), self.max_height / self.image.height())
        width = max(1, round(self.image.width() * scale))
        height = max(1, round(self.image.height() * scale))
        size = QSize(width, height)
        if self._cache_size != size:
            if width == self.image.width() and height == self.image.height():
                self._scaled_cache = self.image
            else:
                self._scaled_cache = self.image.scaled(
                    size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            self._cache_size = size
        target = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
        painter.drawImage(target, self._scaled_cache)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        card = self.parentWidget()
        if card is not None:
            card.setFocus()
        super().mousePressEvent(event)


class DragHandle(QWidget):
    def __init__(self, item: StackItem, begin_callback, motion_callback, finish_callback,
                 cancel_callback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.begin_callback = begin_callback
        self.motion_callback = motion_callback
        self.finish_callback = finish_callback
        self.cancel_callback = cancel_callback
        self._press_global = QPoint()
        self._press_offset = QPoint()
        self._dragging = False
        self._hovered = False
        self.setObjectName("dragHandle")
        self.setFixedWidth(27)
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setToolTip("Drag to reorder this image")
        self.setAccessibleName(f"Reorder {item.custom_label or 'image'}")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API name
        painter = QPainter(self)
        if self._hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(120, 132, 166, 32))
            painter.drawRoundedRect(self.rect().adjusted(1, 2, -1, -2), 6, 6)
        color = QColor("#aeb4c2" if self._hovered else "#727987")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        cx, cy = self.width() / 2, self.height() / 2
        radius, gap_x, gap_y = 2, 5.2, 6.2
        for row in (-1, 0, 1):
            for col in (-0.5, 0.5):
                painter.drawEllipse(QPoint(round(cx + col * gap_x), round(cy + row * gap_y)), round(radius), round(radius))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            card = self.parentWidget()
            self._press_offset = card.mapFromGlobal(self._press_global)
            self._dragging = False
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            event.ignore()
            return
        global_position = event.globalPosition().toPoint()
        if not self._dragging and (global_position - self._press_global).manhattanLength() < QApplication.startDragDistance():
            event.accept()
            return
        card = self.parentWidget()
        if not self._dragging:
            if not self.begin_callback(self.item, self._press_offset, global_position):
                event.accept()
                return
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            card.setProperty("dragging", True)
            card.style().unpolish(card)
            card.style().polish(card)
        self.motion_callback(global_position)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self.motion_callback(event.globalPosition().toPoint())
                self._dragging = False
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                self.finish_callback(self.item.uid)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def cancel_drag(self) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.cancel_callback()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.key() == Qt.Key.Key_Escape and self._dragging:
            self.cancel_drag()
            event.accept()
            return
        super().keyPressEvent(event)


class DragPlaceholder(QFrame):
    """Card-sized destination gap shown while an image card is lifted."""

    def __init__(self, height: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("dragPlaceholder")
        self.setFixedHeight(max(1, height))
        self.setStyleSheet(
            "QFrame#dragPlaceholder { background: rgba(120,132,166,24); "
            "border: 1px dashed #626b82; border-radius: 8px; }"
        )

class CardListHost(QWidget):
    """Manages card geometry directly so reorder animations are not fought by a layout."""

    def __init__(self, viewport: QWidget) -> None:
        super().__init__()
        self._viewport = viewport
        self._visual_order: list[StackItem] = []
        self._cards: dict[str, ImageCard] = {}
        self._placeholder: DragPlaceholder | None = None
        self._source_uid: str | None = None
        self._animations: dict[str, QPropertyAnimation] = {}
        self._target_geometries: dict[str, QRect] = {}
        self._empty_hint: QLabel | None = None
        self._floating_card: ImageCard | None = None
        self._floating_y = 0
        self._updating_geometry = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(1)

    def set_empty_hint(self, hint: QLabel) -> None:
        for animation in self._animations.values():
            animation.stop()
            animation.deleteLater()
        self._animations.clear()
        self._empty_hint = hint
        hint.setParent(self)
        hint.show()
        self._visual_order = []
        self._cards = {}
        self._placeholder = None
        self._source_uid = None
        self._animations = {}
        self._floating_card = None
        self._place_widgets(False, {})

    def set_cards(
        self,
        order: list[StackItem],
        cards: dict[str, ImageCard],
        placeholder: DragPlaceholder | None,
        source_uid: str | None,
        animate: bool,
        animations: dict[str, QPropertyAnimation],
    ) -> None:
        self._visual_order = list(order)
        self._cards = cards
        self._placeholder = placeholder
        self._source_uid = source_uid
        self._floating_card = cards.get(source_uid) if source_uid is not None and placeholder is not None else None
        self._animations = animations
        if self._empty_hint is not None:
            self._empty_hint.hide()
        self._place_widgets(animate, animations)

    def relayout(self, animations: dict[str, QPropertyAnimation] | None = None) -> None:
        if animations is not None:
            self._animations = animations
        self._place_widgets(False, self._animations)

    def _place_widgets(self, animate: bool, animations: dict[str, QPropertyAnimation]) -> None:
        for animation in list(animations.values()):
            animation.stop()
            animation.deleteLater()
        animations.clear()
        old_geometry: dict[QWidget, QRect] = {}
        for card in self._cards.values():
            if card.isVisible():
                old_geometry[card] = card.geometry()
        if self._placeholder is not None and self._placeholder.isVisible():
            old_geometry[self._placeholder] = self._placeholder.geometry()

        margin, spacing = 10, 9
        card_width = max(1, self.width() - margin * 2)
        target_geometries: list[tuple[QWidget, QRect, str]] = []
        y = margin
        for item in self._visual_order:
            if item.uid == self._source_uid and self._placeholder is not None:
                widget: QWidget = self._placeholder
                height = self._placeholder.height()
                name = "placeholder"
            else:
                card = self._cards.get(item.uid)
                if card is None:
                    continue
                widget = card
                card.setParent(self)
                card_layout = card.layout()
                inner_width = max(1, card_width - 2 * card.frameWidth())
                height = max(card.drag_handle.minimumHeight(), card_layout.heightForWidth(inner_width))
                height += 2 * card.frameWidth()
                name = item.uid
            target_geometries.append((widget, QRect(margin, y, card_width, height), name))
            y += height + spacing
        if self._visual_order:
            y -= spacing
        elif self._empty_hint is not None:
            self._empty_hint.setGeometry(0, 0, max(1, self.width()), max(180, self.height()))
            y = 180
        content_height = max(1, y + (margin if self._visual_order else 0))
        self._target_geometries = {name: rect for _, rect, name in target_geometries}
        self._updating_geometry = True
        if self.minimumHeight() != content_height:
            self.setMinimumHeight(content_height)
        self._updating_geometry = False

        for widget, target_rect, name in target_geometries:
            target = target_rect
            widget.show()
            previous = old_geometry.get(widget)
            if not animate or previous is None or previous == target:
                widget.setGeometry(target)
                continue
            widget.setGeometry(previous)
            animation = QPropertyAnimation(widget, b"geometry", self)
            animation.setDuration(135)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.setStartValue(previous)
            animation.setEndValue(target)
            def discard_animation(key=name, current=animation) -> None:
                if animations.get(key) is current:
                    animations.pop(key, None)
                current.deleteLater()

            animation.finished.connect(discard_animation)
            animations[name] = animation
            animation.start()

        if self._floating_card is not None:
            card = self._floating_card
            card_width = max(1, self.width() - 2 * margin)
            self._floating_y = self._clamp_floating_y(self._floating_y, card.height())
            card.setGeometry(margin, self._floating_y, card_width, card.height())
            card.show()
            card.raise_()

    def _clamp_floating_y(self, y: int, height: int) -> int:
        viewport = self._viewport
        top = self.mapFromGlobal(viewport.mapToGlobal(QPoint(0, 0))).y()
        bottom = top + viewport.height()
        return min(max(y, top), max(top, bottom - height))

    def set_floating_y(self, y: int) -> None:
        if self._floating_card is None:
            return
        self._floating_y = self._clamp_floating_y(y, self._floating_card.height())
        margin = 10
        self._floating_card.setGeometry(
            margin, self._floating_y, max(1, self.width() - 2 * margin), self._floating_card.height()
        )
        self._floating_card.raise_()

    def target_geometry(self, uid: str) -> QRect | None:
        return self._target_geometries.get(uid)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        if not self._updating_geometry:
            self._place_widgets(False, self._animations)


class ImageCard(QFrame):
    """One removable, renameable source image in the scrollable list."""

    def __init__(
        self, item: StackItem, index: int, preview_height: int, remove_callback, rename_callback,
        move_callback, begin_drag_callback, cancel_drag_callback, finish_reorder_callback, drag_motion_callback,
    ) -> None:
        super().__init__()
        self.item = item
        self.remove_callback = remove_callback
        self.finish_reorder_callback = finish_reorder_callback
        self.drag_motion_callback = drag_motion_callback
        self.setObjectName("imageCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Click the card, then press Delete to remove it")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 5, 5)
        layout.setSpacing(5)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(4)
        header = QHBoxLayout()
        self.label_edit = QLineEdit(item.label(index))
        self.label_edit.setPlaceholderText(f"Image {index + 1}")
        self.label_edit.setClearButtonEnabled(False)
        self.label_edit.setToolTip(item.label(index))
        self.label_edit.editingFinished.connect(lambda: rename_callback(item, self.label_edit.text()))
        header.addWidget(self.label_edit, 1)
        self.move_control = SegmentedMoveControl(
            lambda: move_callback(item, -1), lambda: move_callback(item, 1)
        )
        self.move_up_button = self.move_control.up_button
        self.move_down_button = self.move_control.down_button
        header.addWidget(self.move_control)
        remove = QPushButton("×")
        self.remove_button = remove
        remove.setObjectName("removeButton")
        remove.setFixedSize(30, 30)
        remove.setToolTip("Remove this image")
        remove.setAccessibleName(f"Remove {item.label(index)}")
        remove.clicked.connect(lambda: remove_callback(item))
        header.addWidget(remove)
        content.addLayout(header)

        self.preview = ImagePreview(item.image, preview_height)
        content.addWidget(self.preview)
        layout.addLayout(content, 1)

        self.drag_handle = DragHandle(
            item, begin_drag_callback, drag_motion_callback, finish_reorder_callback, cancel_drag_callback, self
        )
        layout.addWidget(self.drag_handle)

    def update_position(self, index: int, count: int) -> None:
        self.move_up_button.setEnabled(index > 0)
        self.move_down_button.setEnabled(index < count - 1)
        if self.item.custom_label is None:
            self.label_edit.setPlaceholderText(f"Image {index + 1}")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.key() == Qt.Key.Key_Delete:
            self.remove_callback(self.item)
            event.accept()
            return
        super().keyPressEvent(event)


class TriangleButton(QPushButton):
    """Compact icon button with a crisp, theme-friendly vector triangle."""

    def __init__(self, direction: str, tooltip: str, accessible_name: str) -> None:
        super().__init__("")
        self.direction = direction
        self.setObjectName("moveButton")
        self.setFixedSize(24, 24)
        self.setToolTip(tooltip)
        self.setAccessibleName(accessible_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#666b76" if not self.isEnabled() else "#c1c6d0")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        cx, cy = self.width() / 2, self.height() / 2
        path = QPainterPath()
        if self.direction == "up":
            path.moveTo(cx, cy - 4)
            path.lineTo(cx - 4, cy + 3)
            path.lineTo(cx + 4, cy + 3)
        else:
            path.moveTo(cx - 4, cy - 3)
            path.lineTo(cx + 4, cy - 3)
            path.lineTo(cx, cy + 4)
        path.closeSubpath()
        painter.drawPath(path)


class SegmentedMoveControl(QFrame):
    """Two independently clickable triangle buttons inside one bordered control."""

    def __init__(self, move_up, move_down) -> None:
        super().__init__()
        self.setObjectName("moveControl")
        self.setFixedSize(54, 30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.up_button = TriangleButton("up", "Move up", "Move image up")
        self.up_button.setObjectName("moveUpButton")
        self.up_button.setFixedSize(24, 26)
        self.up_button.clicked.connect(move_up)
        layout.addWidget(self.up_button)
        divider = QFrame(self)
        divider.setObjectName("moveDivider")
        divider.setFixedSize(1, 14)
        layout.addWidget(divider)
        self.down_button = TriangleButton("down", "Move down", "Move image down")
        self.down_button.setObjectName("moveDownButton")
        self.down_button.setFixedSize(25, 26)
        self.down_button.clicked.connect(move_down)
        layout.addWidget(self.down_button)


class ImageStacker(QMainWindow):
    DEFAULT_PREVIEW_HEIGHT = 520
    MIN_PREVIEW_HEIGHT = 120
    MAX_PREVIEW_HEIGHT = 700
    CHATGPT_LIMIT_BYTES = 20_000_000

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Image Stacker")
        self.resize(540, 720)
        self.setMinimumSize(380, 420)
        self.items: list[StackItem] = []
        self.combined = QImage()
        self.output_image = QImage()
        self._output_png = b""
        self._output_revision = 0
        self._output_workers: dict[int, OutputEncoder] = {}
        self._output_pending = False
        self.fit_for_chatgpt = False
        self.include_labels = True
        self._undo_stack: list[StackSnapshot] = []
        self._clipboard = QApplication.clipboard()
        self._settings = QSettings("Image Stacker", "Image Stacker")
        self.preview_height = max(
            self.MIN_PREVIEW_HEIGHT,
            min(self.MAX_PREVIEW_HEIGHT, self._settings.value("previewHeight", self.DEFAULT_PREVIEW_HEIGHT, type=int)),
        )
        self._cards_by_uid: dict[str, ImageCard] = {}
        self._drag_origin: list[StackItem] | None = None
        self._drag_order: list[StackItem] | None = None
        self._drag_source_uid: str | None = None
        self._drag_placeholder: DragPlaceholder | None = None
        self._drag_hotspot_y = 0
        self._drag_animations: dict[str, QPropertyAnimation] = {}
        self._drag_pointer_global = QPoint()
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(30)
        self._autoscroll_timer.timeout.connect(self._autoscroll_step)

        self._build_ui()
        self._build_actions()
        self._refresh()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.counter = QLabel("0 images")
        self.counter.setObjectName("counter")
        self.counter.setStyleSheet("font-size: 15px; font-weight: 600;")
        header.addWidget(self.counter)
        self.output_size_label = QLabel("PNG: —")
        self.output_size_label.setObjectName("outputSize")
        self.output_size_label.setToolTip("Encoded size of the PNG used for clipboard and Save As")
        header.addWidget(self.output_size_label)
        header.addStretch(1)
        self.labels_checkbox = QCheckBox("Include labels in combined image")
        self.labels_checkbox.setChecked(True)
        self.labels_checkbox.toggled.connect(self.set_include_labels)
        header.addWidget(self.labels_checkbox)
        layout.addLayout(header)

        preview_controls = QHBoxLayout()
        preview_controls.setSpacing(7)
        preview_controls.addWidget(QLabel("Preview size"))
        self.preview_slider = QSlider(Qt.Orientation.Horizontal)
        self.preview_slider.setRange(self.MIN_PREVIEW_HEIGHT, self.MAX_PREVIEW_HEIGHT)
        self.preview_slider.setSingleStep(20)
        self.preview_slider.setPageStep(80)
        self.preview_slider.setFixedWidth(150)
        self.preview_slider.setValue(self.preview_height)
        self.preview_slider.setToolTip("Change preview height; source and output resolution stay unchanged")
        self.preview_size_label = QLabel(f"{self.preview_height} px")
        self.preview_size_label.setMinimumWidth(52)
        self.preview_slider.valueChanged.connect(self._set_preview_height)
        preview_controls.addWidget(self.preview_slider)
        preview_controls.addWidget(self.preview_size_label)
        preview_controls.addStretch(1)
        self.fit_checkbox = QCheckBox("Fit for ChatGPT")
        self.fit_checkbox.setToolTip(
            "When needed, scale only the final combined PNG to about 19 MB while preserving its aspect ratio"
        )
        self.fit_checkbox.toggled.connect(self._set_fit_for_chatgpt)
        preview_controls.addWidget(self.fit_checkbox)
        layout.addLayout(preview_controls)

        actions = QHBoxLayout()
        self.paste_button = QPushButton("Paste Image")
        self.paste_button.setObjectName("pasteButton")
        self.paste_button.setToolTip("Paste image from clipboard (Ctrl+V)")
        self.undo_button = QPushButton("Undo")
        self.clear_button = QPushButton("Clear")
        self.copy_button = QPushButton("Copy Combined")
        self.save_button = QPushButton("Save As…")
        for button in (self.paste_button, self.undo_button, self.clear_button, self.copy_button, self.save_button):
            actions.addWidget(button)
        layout.addLayout(actions)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: #181a1f; border: 1px solid #343740; border-radius: 8px; }")
        self.cards_host = CardListHost(self.scroll.viewport())
        self.empty_hint = QLabel('Copy an image, then click “Paste Image”\nor press Ctrl+V')
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setMinimumHeight(180)
        self.empty_hint.setStyleSheet("color: #a7abb5; font-size: 14px;")
        self.cards_host.set_empty_hint(self.empty_hint)
        self.scroll.setWidget(self.cards_host)
        layout.addWidget(self.scroll, 1)
        self.setCentralWidget(root)

        self._cancel_drag_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._cancel_drag_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._cancel_drag_shortcut.activated.connect(self._cancel_reorder)

        self.paste_button.clicked.connect(self.paste_image)
        self.undo_button.clicked.connect(self.undo)
        self.clear_button.clicked.connect(self.clear_all)
        self.copy_button.clicked.connect(self.copy_combined)
        self.save_button.clicked.connect(self.save_as)
        self.statusBar().showMessage("Ready")
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #202228; color: #e8eaf0; }
            QPushButton { background: #30333b; border: 1px solid #444852; border-radius: 6px; padding: 7px 10px; }
            QPushButton:hover { background: #3b3f49; }
            QPushButton#pasteButton { background: #505f8c; border-color: #7183b5; font-weight: 600; }
            QPushButton#pasteButton:hover { background: #5b6b9c; }
            QPushButton:disabled { color: #777b85; background: #272930; }
            QFrame#imageCard { background: #24272e; border: 1px solid #3b3f49; border-radius: 8px; }
            QFrame#imageCard:hover, QFrame#imageCard:focus { border: 1px solid #7889bd; }
            QFrame#imageCard[dragging="true"] { border: 2px solid #9eafff; background: #2c3040; }
            QLineEdit { background: #30333b; border: 1px solid #444852; border-radius: 5px; padding: 5px 7px; }
            QPushButton#removeButton { font-size: 18px; padding: 0; }
            QFrame#moveControl { background: #30333b; border: 1px solid #444852; border-radius: 6px; }
            QPushButton#moveUpButton, QPushButton#moveDownButton { background: transparent; border: none; padding: 0; }
            QPushButton#moveUpButton:hover:enabled { background: #3b3f49; border-top-left-radius: 5px; border-bottom-left-radius: 5px; }
            QPushButton#moveDownButton:hover:enabled { background: #3b3f49; border-top-right-radius: 5px; border-bottom-right-radius: 5px; }
            QPushButton#moveButton:disabled { background: transparent; }
            QFrame#moveDivider { background: #555a64; border: none; }
            QWidget#dragHandle { border: 1px solid transparent; border-radius: 6px; }
            QWidget#dragHandle:hover { border-color: #484e5b; }
        """)

    def _build_actions(self) -> None:
        shortcuts = (
            ("Paste image", "Ctrl+V", self.paste_image),
            ("Undo", "Ctrl+Z", self.undo),
            ("Copy combined image", "Ctrl+Shift+C", self.copy_combined),
            ("Save as", "Ctrl+Shift+S", self.save_as),
            ("Clear all", "Ctrl+Shift+Backspace", self.clear_all),
        )
        for name, shortcut, callback in shortcuts:
            action = QAction(name, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            self.addAction(action)

    def _snapshot(self) -> StackSnapshot:
        return StackSnapshot(
            [StackItem(item.image, item.custom_label, item.uid) for item in self.items], self.include_labels
        )

    def _record_undo(self) -> None:
        self._undo_stack.append(self._snapshot())

    def _set_preview_height(self, height: int) -> None:
        self.preview_height = height
        self.preview_size_label.setText(f"{height} px")
        self._settings.setValue("previewHeight", height)
        for card in self._cards_by_uid.values():
            card.preview.set_max_height(height)
            card.layout().activate()
        self._update_cards_minimum_height()

    def _begin_drag(
        self, item: StackItem, press_offset: QPoint | None = None, global_position: QPoint | None = None
    ) -> bool:
        if self._drag_order is not None or not any(candidate.uid == item.uid for candidate in self.items):
            return False
        card = self._cards_by_uid.get(item.uid)
        if card is None:
            return False
        self._drag_origin = list(self.items)
        self._drag_order = list(self.items)
        self._drag_source_uid = item.uid
        self._drag_hotspot_y = press_offset.y() if press_offset is not None else card.mapFromGlobal(QCursor.pos()).y()
        self._drag_pointer_global = global_position or QCursor.pos()
        self._drag_placeholder = DragPlaceholder(card.height(), self.cards_host)
        card.setProperty("dragging", True)
        card.style().unpolish(card)
        card.style().polish(card)
        self._apply_drag_layout(self._drag_order, animate=True)
        self._position_floating_card()
        return True

    def _apply_drag_layout(self, order: list[StackItem], animate: bool) -> None:
        source_uid = self._drag_source_uid
        if source_uid is None or self._drag_placeholder is None:
            return
        self.cards_host.set_cards(order, self._cards_by_uid, self._drag_placeholder, source_uid, animate, self._drag_animations)

    def _apply_card_order(self, order: list[StackItem]) -> None:
        if self._drag_placeholder is not None:
            self._drag_placeholder.hide()
            self._drag_placeholder.deleteLater()
            self._drag_placeholder = None
        self.cards_host.set_cards(order, self._cards_by_uid, None, None, False, self._drag_animations)

    def _update_cards_minimum_height(self) -> None:
        if not hasattr(self, "scroll"):
            return
        self.cards_host.relayout(self._drag_animations)

    def _set_fit_for_chatgpt(self, enabled: bool) -> None:
        if self.fit_for_chatgpt == enabled:
            return
        self.fit_for_chatgpt = enabled
        self._request_output_encoding()

    def _request_output_encoding(self) -> None:
        self._output_revision += 1
        revision = self._output_revision
        for old_revision, worker in self._output_workers.items():
            if old_revision < revision:
                worker.requestInterruption()
        if not self.items or self.combined.isNull():
            self.output_image = QImage()
            self._output_png = b""
            self._output_pending = False
            self.counter.setText("0 images")
            self.output_size_label.setText("PNG: —")
            self.output_size_label.setStyleSheet("color: #a7abb5;")
            self.copy_button.setEnabled(False)
            self.save_button.setEnabled(False)
            return
        self._output_pending = True
        self.output_size_label.setText("PNG: preparing…")
        self.output_size_label.setStyleSheet("color: #a7abb5;")
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.statusBar().showMessage("Preparing clipboard PNG…")
        worker = OutputEncoder(revision, self.combined, self.fit_for_chatgpt, self)
        self._output_workers[revision] = worker
        worker.encoded.connect(self._output_encoded)
        worker.finished.connect(self._output_worker_finished)
        worker.start()

    @pyqtSlot()
    def _output_worker_finished(self) -> None:
        worker = self.sender()
        if not isinstance(worker, OutputEncoder):
            return
        if self._output_workers.get(worker.revision) is worker:
            self._output_workers.pop(worker.revision, None)
        worker.deleteLater()

    def _output_encoded(self, revision: int, image: QImage, png: bytes, error: str) -> None:
        if revision != self._output_revision:
            return
        self._output_pending = False
        if error or image.isNull() or not png:
            self.output_image = QImage()
            self._output_png = b""
            self.output_size_label.setText("PNG: encoding failed")
            self.output_size_label.setStyleSheet("color: #f08a8a;")
            self.copy_button.setEnabled(False)
            self.save_button.setEnabled(False)
            self.statusBar().showMessage(f"Could not encode PNG: {error or 'unknown error'}", 5000)
            return
        self.output_image = image
        self._output_png = png
        size_mb = len(png) / 1_000_000
        if len(png) > self.CHATGPT_LIMIT_BYTES:
            suffix = " • exceeds 20 MB limit"
            color = "#f08a8a"
        elif len(png) >= 18_000_000:
            suffix = " • near 20 MB limit"
            color = "#e5b96b"
        else:
            suffix = ""
            color = "#a7abb5"
        self.output_size_label.setText(f"PNG: {size_mb:.1f} MB{suffix}")
        self.output_size_label.setStyleSheet(f"color: {color};")
        self.counter.setText(
            f"{len(self.items)} images • {image.width()} × {image.height()}"
        )
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self._copy_to_clipboard()
        self.statusBar().showMessage("Combined image copied to clipboard", 3000)

    def _update_card_labels(self, order: list[StackItem]) -> None:
        for index, item in enumerate(order):
            card = self._cards_by_uid.get(item.uid)
            if card is not None and item.custom_label is None:
                label = item.label(index)
                card.label_edit.setText(label)
                card.label_edit.setToolTip(label)

    def _drag_motion(self, global_position: QPoint) -> None:
        if self._drag_order is None or self._drag_source_uid is None:
            return
        if not self.frameGeometry().contains(global_position):
            self._cancel_reorder()
            return
        self._drag_pointer_global = global_position
        y = self.scroll.viewport().mapFromGlobal(global_position).y()
        edge = 44
        if y < edge or y > self.scroll.viewport().height() - edge:
            if not self._autoscroll_timer.isActive():
                self._autoscroll_timer.start()
        else:
            self._autoscroll_timer.stop()
        self._position_floating_card()
        self._update_drag_slot()

    def _position_floating_card(self) -> None:
        if self._drag_order is None or self._drag_source_uid is None:
            return
        source = self._cards_by_uid.get(self._drag_source_uid)
        if source is None:
            return
        pointer_y = self.cards_host.mapFromGlobal(self._drag_pointer_global).y()
        self.cards_host.set_floating_y(pointer_y - self._drag_hotspot_y)

    def _update_drag_slot(self) -> None:
        if self._drag_order is None or self._drag_source_uid is None:
            return
        source_card = self._cards_by_uid.get(self._drag_source_uid)
        if source_card is None:
            return
        others = [item for item in self._drag_order if item.uid != self._drag_source_uid]
        center_y = self.cards_host.mapFromGlobal(self._drag_pointer_global).y() - self._drag_hotspot_y
        center_y += source_card.height() / 2
        geometries = [
            self.cards_host.target_geometry(item.uid) or self._cards_by_uid[item.uid].geometry()
            for item in others
        ]
        candidate = next(
            (index for index, geometry in enumerate(geometries) if center_y < geometry.center().y()), len(others)
        )
        current = next(
            (index for index, item in enumerate(self._drag_order) if item.uid == self._drag_source_uid), len(others)
        )
        if candidate != current:
            if candidate < current and candidate < len(geometries):
                if center_y >= geometries[candidate].center().y() - 8:
                    return
            elif candidate > current and current < len(geometries):
                if center_y <= geometries[current].center().y() + 8:
                    return
        if candidate == current:
            return
        reordered = list(others)
        source = next(item for item in self._drag_order if item.uid == self._drag_source_uid)
        reordered.insert(candidate, source)
        self._drag_order = reordered
        self._apply_drag_layout(reordered, animate=True)
        self._update_card_labels(reordered)

    def _autoscroll_step(self) -> None:
        viewport = self.scroll.viewport()
        y = viewport.mapFromGlobal(self._drag_pointer_global).y()
        edge = 44
        if y < edge:
            delta = -max(2, min(12, round((edge - y) * 0.28)))
        elif y > viewport.height() - edge:
            delta = max(2, min(12, round((y - (viewport.height() - edge)) * 0.28)))
        else:
            self._autoscroll_timer.stop()
            return
        bar = self.scroll.verticalScrollBar()
        previous_value = bar.value()
        bar.setValue(previous_value + delta)
        if bar.value() == previous_value:
            self._autoscroll_timer.stop()
            return
        self._position_floating_card()
        self._update_drag_slot()

    def _clear_drag_state(self) -> None:
        self._autoscroll_timer.stop()
        for animation in self._drag_animations.values():
            animation.stop()
            animation.deleteLater()
        self._drag_animations.clear()
        if self._drag_placeholder is not None:
            self._drag_placeholder.hide()
            self._drag_placeholder.deleteLater()
            self._drag_placeholder = None
        self._drag_origin = None
        self._drag_order = None
        self._drag_source_uid = None

    def _finish_reorder(self, source_uid: str) -> None:
        if self._drag_order is None or source_uid != self._drag_source_uid:
            return
        reordered = list(self._drag_order)
        changed = any(old.uid != new.uid for old, new in zip(self.items, reordered))
        source_card = self._cards_by_uid.get(source_uid)
        if source_card is not None:
            source_card.setProperty("dragging", False)
            source_card.style().unpolish(source_card)
            source_card.style().polish(source_card)
        self._clear_drag_state()
        if not changed:
            self._apply_card_order(self.items)
            self._update_card_labels(self.items)
            self._update_card_positions()
            return
        self._commit_reorder(reordered, source_uid)

    def _cancel_reorder(self) -> None:
        if self._drag_origin is None:
            return
        original = list(self._drag_origin)
        source = self._cards_by_uid.get(self._drag_source_uid or "")
        if source is not None:
            source.drag_handle._dragging = False
            source.drag_handle.setCursor(Qt.CursorShape.OpenHandCursor)
            source.setProperty("dragging", False)
            source.style().unpolish(source)
            source.style().polish(source)
        self._clear_drag_state()
        self._apply_card_order(original)
        self._update_card_labels(original)

    def paste_image(self) -> None:
        self._cancel_reorder()
        image = self._clipboard.image()
        if image.isNull():
            self.statusBar().showMessage("Clipboard does not contain an image", 3000)
            return
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied).copy()
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            self.statusBar().showMessage("Could not read the clipboard image", 3000)
            return
        self._record_undo()
        self.items.append(StackItem(image))
        if not self._rebuild_combined():
            self.items.pop()
            self._undo_stack.pop()
            return
        self._refresh()
        self._request_output_encoding()
        self.statusBar().showMessage(f"Added image {len(self.items)} • preparing clipboard PNG", 3500)

    def remove_item(self, item: StackItem) -> None:
        self._cancel_reorder()
        index = next((i for i, candidate in enumerate(self.items) if candidate is item), None)
        if index is None:
            return
        self._record_undo()
        del self.items[index]
        if not self._rebuild_combined():
            self._undo_stack.pop()
            return
        self._refresh()
        if self.items:
            self._request_output_encoding()
            self.statusBar().showMessage("Image removed • preparing clipboard PNG", 3000)
        else:
            self._request_output_encoding()
            self.statusBar().showMessage("Image removed • clipboard left unchanged", 3000)

    def reorder_item(self, source_uid: str, target_item: StackItem, after: bool) -> None:
        self._cancel_reorder()
        source_index = next((i for i, item in enumerate(self.items) if item.uid == source_uid), None)
        target_index = next((i for i, item in enumerate(self.items) if item is target_item), None)
        if source_index is None or target_index is None or source_index == target_index:
            return
        reordered = list(self.items)
        moved_item = reordered.pop(source_index)
        target_index = next(i for i, item in enumerate(reordered) if item is target_item)
        insert_at = target_index + (1 if after else 0)
        reordered.insert(insert_at, moved_item)
        if all(old is new for old, new in zip(self.items, reordered)):
            return
        self._commit_reorder(reordered, source_uid)

    def move_item(self, item: StackItem, delta: int) -> None:
        self._cancel_reorder()
        index = next((i for i, candidate in enumerate(self.items) if candidate.uid == item.uid), None)
        if index is None:
            return
        destination = index + delta
        if destination < 0 or destination >= len(self.items):
            return
        reordered = list(self.items)
        moved = reordered.pop(index)
        reordered.insert(destination, moved)
        self._commit_reorder(reordered, item.uid)

    def _commit_reorder(self, order: list[StackItem], moved_uid: str) -> bool:
        if len(order) != len(self.items) or all(old.uid == new.uid for old, new in zip(self.items, order)):
            return False
        previous = self.items
        self._record_undo()
        self.items = list(order)
        if not self._rebuild_combined():
            self.items = previous
            self._undo_stack.pop()
            self._apply_card_order(previous)
            self._update_card_labels(previous)
            self._update_card_positions()
            return False
        self.undo_button.setEnabled(True)
        self.cards_host.set_cards(self.items, self._cards_by_uid, None, None, True, self._drag_animations)
        self._update_card_labels(self.items)
        self._update_card_positions()
        self.counter.setText(f"{len(self.items)} images • {self.combined.width()} × {self.combined.height()}")
        self._ensure_item_visible(moved_uid)
        self._request_output_encoding()
        self.statusBar().showMessage("Image order updated • preparing clipboard PNG", 3000)
        return True

    def _update_card_positions(self) -> None:
        count = len(self.items)
        for index, item in enumerate(self.items):
            card = self._cards_by_uid.get(item.uid)
            if card is not None:
                card.update_position(index, count)

    def _ensure_item_visible(self, uid: str) -> None:
        target = self.cards_host.target_geometry(uid)
        if target is None:
            return
        viewport = self.scroll.viewport()
        top = self.cards_host.mapTo(viewport, target.topLeft()).y()
        bottom = top + target.height()
        if target.height() >= viewport.height() or top < 0:
            delta = top
        elif bottom > viewport.height():
            delta = bottom - viewport.height()
        else:
            delta = 0
        if delta:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.value() + delta)

    def rename_item(self, item: StackItem, label: str) -> None:
        self._cancel_reorder()
        if not any(candidate is item for candidate in self.items):
            return
        label = label.strip()
        custom_label = label or None
        if item.custom_label == custom_label or (
            custom_label is None and item.custom_label is None
        ) or (
            custom_label is not None and item.custom_label is None and custom_label == self._auto_label_for(item)
        ):
            return
        previous_label = item.custom_label
        self._record_undo()
        item.custom_label = custom_label
        if not self._rebuild_combined():
            item.custom_label = previous_label
            self._undo_stack.pop()
            return
        self._update_card_labels(self.items)
        index = next(i for i, candidate in enumerate(self.items) if candidate is item)
        card = self._cards_by_uid.get(item.uid)
        if card is not None:
            visible_label = item.label(index)
            card.label_edit.setText(visible_label)
            card.label_edit.setToolTip(visible_label)
        self._request_output_encoding()
        self.statusBar().showMessage("Label updated • preparing clipboard PNG", 2500)

    def _auto_label_for(self, item: StackItem) -> str:
        index = next(i for i, candidate in enumerate(self.items) if candidate is item)
        return f"Image {index + 1}"

    def set_include_labels(self, enabled: bool) -> None:
        self._cancel_reorder()
        if self.include_labels == enabled:
            return
        self._record_undo()
        self.include_labels = enabled
        if self._rebuild_combined():
            self._refresh()
            self._request_output_encoding()
            self.statusBar().showMessage(
                "Labels included • clipboard refreshed" if enabled else "Labels hidden • clipboard refreshed", 2500
            )

    def _rebuild_combined(self) -> bool:
        if not self.items:
            self.combined = QImage()
            return True
        width = max(item.image.width() for item in self.items)
        font_px = max(18, min(40, round(width * 0.018)))
        header_height = round(font_px * 1.65) if self.include_labels else 0
        height = sum(item.image.height() + header_height for item in self.items)
        canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        if canvas.isNull():
            self.statusBar().showMessage("Could not allocate memory for the combined image", 5000)
            return False
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        y = 0
        for index, item in enumerate(self.items):
            if self.include_labels:
                painter.fillRect(0, y, width, header_height, QColor("#343943"))
                font = QFont("Sans Serif")
                font.setPixelSize(font_px)
                font.setWeight(QFont.Weight.DemiBold)
                painter.setFont(font)
                painter.setPen(QColor("#f3f4f6"))
                padding = max(12, round(font_px * 0.65))
                rect = QRectF(padding, y, max(1, width - padding * 2), header_height)
                metrics = QFontMetrics(font)
                text = metrics.elidedText(item.label(index), Qt.TextElideMode.ElideRight, round(rect.width()))
                painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
                y += header_height
            image = item.image
            painter.drawImage((width - image.width()) // 2, y, image)
            y += image.height()
        painter.end()
        self.combined = canvas
        return True

    def _copy_to_clipboard(self) -> None:
        if not self.output_image.isNull() and self._output_png:
            mime = QMimeData()
            mime.setImageData(self.output_image)
            mime.setData("image/png", QByteArray(self._output_png))
            self._clipboard.setMimeData(mime)

    def copy_combined(self) -> None:
        if self._output_pending:
            self.statusBar().showMessage("PNG is still being prepared", 2500)
            return
        if self.output_image.isNull():
            self.statusBar().showMessage("There is no combined image to copy", 2500)
            return
        self._copy_to_clipboard()
        self.statusBar().showMessage("Combined image copied to clipboard", 3000)

    def undo(self) -> None:
        self._cancel_reorder()
        if not self._undo_stack:
            return
        snapshot = self._undo_stack.pop()
        self.items = snapshot.items
        self.include_labels = snapshot.include_labels
        self.labels_checkbox.blockSignals(True)
        self.labels_checkbox.setChecked(self.include_labels)
        self.labels_checkbox.blockSignals(False)
        if self._rebuild_combined():
            self._refresh()
            if self.items:
                self._request_output_encoding()
                self.statusBar().showMessage("Undo complete • preparing clipboard PNG", 3000)
            else:
                self._request_output_encoding()
                self.statusBar().showMessage("Undo complete • clipboard left unchanged", 3000)

    def clear_all(self) -> None:
        self._cancel_reorder()
        if not self.items:
            self.statusBar().showMessage("Stack is already empty", 2000)
            return
        self._record_undo()
        self.items.clear()
        self._rebuild_combined()
        self._refresh()
        self._request_output_encoding()
        self.statusBar().showMessage("Stack cleared • clipboard left unchanged", 3000)

    def save_as(self) -> None:
        if self._output_pending:
            self.statusBar().showMessage("PNG is still being prepared", 2500)
            return
        if self.output_image.isNull() or not self._output_png:
            self.statusBar().showMessage("There is no combined image to save", 2500)
            return
        filename = f"chatgpt-stack-{datetime.now():%Y%m%d-%H%M%S}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Combined Image", str(Path.home() / filename), "PNG image (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not self._write_output_png(path):
            self.statusBar().showMessage("Could not save the PNG file", 4000)
            return
        self.statusBar().showMessage(f"Saved {Path(path).name}", 3500)

    def _write_output_png(self, path: str | Path) -> bool:
        if self._output_pending or not self._output_png:
            return False
        try:
            Path(path).write_bytes(self._output_png)
        except OSError:
            return False
        return True

    def _refresh(self) -> None:
        self.counter.setText(
            f"{len(self.items)} images • {self.combined.width()} × {self.combined.height()}"
            if self.items else "0 images"
        )
        self.undo_button.setEnabled(bool(self._undo_stack))
        has_items = bool(self.items)
        self.clear_button.setEnabled(has_items)
        self.copy_button.setEnabled(has_items)
        self.save_button.setEnabled(has_items)
        for card in self._cards_by_uid.values():
            card.hide()
            card.deleteLater()
        self._cards_by_uid.clear()
        if not self.items:
            self.cards_host.set_empty_hint(self.empty_hint)
        else:
            for index, item in enumerate(self.items):
                card = ImageCard(
                    item,
                    index,
                    self.preview_height,
                    self.remove_item,
                    self.rename_item,
                    self.move_item,
                    self._begin_drag,
                    self._cancel_reorder,
                    self._finish_reorder,
                    self._drag_motion,
                )
                self._cards_by_uid[item.uid] = card
            self.cards_host.set_cards(self.items, self._cards_by_uid, None, None, False, self._drag_animations)
            self._update_card_positions()
        self.labels_checkbox.setEnabled(has_items)
        self._update_cards_minimum_height()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        self._update_cards_minimum_height()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._autoscroll_timer.stop()
        self._cancel_reorder()
        for worker in tuple(self._output_workers.values()):
            worker.requestInterruption()
        for worker in tuple(self._output_workers.values()):
            worker.wait()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Image Stacker")
    app.setOrganizationName("Image Stacker")
    window = ImageStacker()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
