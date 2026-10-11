"""Behavioral tests for the in-window mouse-driven card reorder interaction."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_PLATFORMTHEME", "generic")

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QImage, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame

from src.image_stacker.main import ImageStacker, StackItem


class DragReorderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, count=4, preview=120, tall=False):
        window = ImageStacker()
        window.preview_height = preview
        size = (180, 1500) if tall else (180, 80)
        window.items = [StackItem(QImage(*size, QImage.Format.Format_RGB32)) for _ in range(count)]
        window._refresh()
        window.show()
        QTest.qWait(20)
        self.addCleanup(window.close)
        return window

    def move_held(self, handle, global_pos):
        local = handle.mapFromGlobal(global_pos)
        event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(local),
            QPointF(global_pos),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(handle, event)
        self.app.processEvents()

    def release(self, handle, global_pos):
        local = handle.mapFromGlobal(global_pos)
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            QPointF(local),
            QPointF(global_pos),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(handle, event)
        self.app.processEvents()

    def start_drag(self, window, index):
        item = window.items[index]
        handle = window._cards_by_uid[item.uid].drag_handle
        point = handle.rect().center()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        return item, handle, handle.mapToGlobal(point)

    def finish_at(self, window, handle, global_pos):
        self.move_held(handle, global_pos)
        self.release(handle, global_pos)

    def target_global_y(self, window, y):
        return window.cards_host.mapToGlobal(QPoint(40, y)).y()

    def test_first_to_last_last_to_first_and_middle(self):
        window = self.make_window()
        original = list(window.items)

        item, handle, press = self.start_drag(window, 0)
        last = window._cards_by_uid[original[-1].uid].geometry()
        target = QPoint(press.x(), self.target_global_y(window, last.bottom() + 24))
        self.finish_at(window, handle, target)
        self.assertEqual(window.items, original[1:] + original[:1])
        self.assertEqual(len(window._undo_stack), 1)
        QTest.qWait(160)

        moved, handle, press = self.start_drag(window, len(window.items) - 1)
        first = window._cards_by_uid[window.items[0].uid].geometry()
        target = QPoint(press.x(), self.target_global_y(window, first.top() - 20))
        self.finish_at(window, handle, target)
        self.assertEqual(window.items, original)
        QTest.qWait(160)

        middle, handle, press = self.start_drag(window, 1)
        end = window._cards_by_uid[window.items[-1].uid].geometry()
        target = QPoint(press.x(), self.target_global_y(window, end.bottom() + 24))
        self.finish_at(window, handle, target)
        self.assertEqual(window.items, [original[0], original[2], original[3], original[1]])

    def test_floating_card_tracks_vertical_pointer_and_stays_bounded(self):
        window = self.make_window()
        item, handle, press = self.start_drag(window, 0)
        card = window._cards_by_uid[item.uid]
        last = window._cards_by_uid[window.items[-1].uid].geometry()
        moved = QPoint(press.x() - 30, self.target_global_y(window, last.bottom() + 24))
        self.move_held(handle, moved)
        self.assertTrue(window._drag_order)
        self.assertEqual(card.x(), 10)
        self.assertEqual(card.width(), window.cards_host.width() - 20)
        expected_y = window.cards_host.mapFromGlobal(moved).y() - window._drag_hotspot_y
        viewport_top = window.cards_host.mapFromGlobal(window.scroll.viewport().mapToGlobal(QPoint(0, 0))).y()
        expected_y = min(max(expected_y, viewport_top), viewport_top + window.scroll.viewport().height() - card.height())
        self.assertEqual(card.y(), expected_y)
        self.assertTrue(window._drag_animations)
        self.release(handle, moved)

    def test_entire_handle_starts_drag_but_label_does_not(self):
        window = self.make_window()
        card = window._cards_by_uid[window.items[0].uid]
        handle = card.drag_handle
        point = QPoint(2, 2)
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        press = handle.mapToGlobal(point)
        self.move_held(handle, press + QPoint(0, 30))
        self.assertIsNotNone(window._drag_order)
        window._cancel_reorder()
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=point)

        label = card.label_edit
        label_point = label.rect().center()
        QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=label_point)
        label_global = label.mapToGlobal(label_point)
        local = label.mapFromGlobal(label_global + QPoint(0, 30))
        event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(local),
            QPointF(label_global + QPoint(0, 30)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(label, event)
        self.app.processEvents()
        self.assertIsNone(window._drag_order)
        QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=label_point)

    def test_drag_after_editing_label_keeps_source_widget_alive(self):
        window = self.make_window()
        item = window.items[0]
        card = window._cards_by_uid[item.uid]
        card.label_edit.setFocus()
        card.label_edit.selectAll()
        QTest.keyClicks(card.label_edit, "Edited title")
        refreshes = []
        original_refresh = window._refresh
        window._refresh = lambda: (refreshes.append(True), original_refresh())[1]

        handle = card.drag_handle
        point = handle.rect().center()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        self.assertIs(window._cards_by_uid[item.uid], card)
        self.assertEqual(refreshes, [])
        press = handle.mapToGlobal(point)
        target = QPoint(press.x(), self.target_global_y(window, card.y() + card.height() + 30))
        self.move_held(handle, target)
        self.assertIsNotNone(window._drag_order)
        self.release(handle, target)
        self.assertEqual(item.custom_label, "Edited title")

    def test_composition_and_clipboard_update_once_after_drop(self):
        window = self.make_window()
        original_rebuild = window._rebuild_combined
        rebuilds = []
        copies = []

        def count_rebuild():
            rebuilds.append(True)
            return original_rebuild()

        window._rebuild_combined = count_rebuild
        window._copy_to_clipboard = lambda: copies.append(True)
        revision = window._output_revision
        item, handle, press = self.start_drag(window, 0)
        last = window._cards_by_uid[window.items[-1].uid].geometry()
        target = QPoint(press.x(), self.target_global_y(window, last.bottom() + 24))
        self.move_held(handle, target)
        self.assertEqual(rebuilds, [])
        self.assertEqual(copies, [])
        self.assertEqual(window._output_revision, revision)
        self.release(handle, target)
        self.assertEqual(len(rebuilds), 1)
        self.assertEqual(window._output_revision, revision + 1)
        QTest.qWait(200)
        self.assertEqual(len(copies), 1)

    def test_outside_window_cancels_and_escape_cancels(self):
        window = self.make_window()
        original = list(window.items)
        copies = []
        window._copy_to_clipboard = lambda: copies.append(True)

        _, handle, press = self.start_drag(window, 0)
        far = QPoint(press.x(), window.frameGeometry().top() - 4)
        self.move_held(handle, far)
        self.assertIsNone(window._drag_order)
        self.assertEqual([item.uid for item in window.items], [item.uid for item in original])
        self.assertEqual(copies, [])

        _, handle, press = self.start_drag(window, 0)
        last = window._cards_by_uid[original[-1].uid].geometry()
        target = QPoint(press.x(), self.target_global_y(window, last.bottom() + 20))
        self.move_held(handle, target)
        QTest.keyClick(handle, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertIsNone(window._drag_order)
        self.assertEqual([item.uid for item in window.items], [item.uid for item in original])
        self.assertEqual(len(window._undo_stack), 0)
        self.assertEqual(copies, [])

    def test_vertical_boundaries_clamp_and_autoscroll(self):
        window = self.make_window(count=8)
        item, handle, press = self.start_drag(window, 0)
        viewport = window.scroll.viewport()
        top = viewport.mapToGlobal(QPoint(20, 1))
        self.move_held(handle, top)
        card = window._cards_by_uid[item.uid]
        viewport_top = window.cards_host.mapFromGlobal(viewport.mapToGlobal(QPoint(0, 0))).y()
        self.assertGreaterEqual(card.y(), viewport_top)

        bottom = viewport.mapToGlobal(QPoint(20, viewport.height() - 2))
        self.move_held(handle, bottom)
        QTest.qWait(180)
        self.assertGreater(window.scroll.verticalScrollBar().value(), 0)
        self.assertIsNotNone(window._drag_order)
        self.release(handle, bottom)

    def test_cancel_then_repeated_drags_and_drag_after_delete(self):
        window = self.make_window()
        window.remove_item(window.items[1])
        QTest.qWait(10)
        for _ in range(6):
            original = list(window.items)
            item, handle, press = self.start_drag(window, 0)
            last = window._cards_by_uid[original[-1].uid].geometry()
            target = QPoint(press.x(), self.target_global_y(window, last.bottom() + 18))
            self.finish_at(window, handle, target)
            self.assertEqual(window.items[0], original[1])
            window.undo()
            self.assertEqual([item.uid for item in window.items], [item.uid for item in original])

    def test_custom_labels_stay_attached_and_automatic_labels_reindex(self):
        window = self.make_window()
        window.items[0].custom_label = "Physics Notes"
        window.items[2].custom_label = "Homework"
        window._refresh()
        item, handle, press = self.start_drag(window, 2)
        first = window._cards_by_uid[window.items[0].uid].geometry()
        target = QPoint(press.x(), self.target_global_y(window, first.top() - 18))
        self.finish_at(window, handle, target)
        self.assertEqual(window.items[0].custom_label, "Homework")
        self.assertEqual(window.items[1].custom_label, "Physics Notes")
        self.assertEqual(window.items[2].custom_label, None)
        self.assertEqual(window._cards_by_uid[window.items[2].uid].label_edit.text(), "Image 3")

    def test_move_buttons_one_position_disabled_edges_undo_and_labels(self):
        window = self.make_window()
        initial = list(window.items)
        window.rename_item(initial[0], "Custom first")
        window._undo_stack.clear()
        window.undo_button.setEnabled(False)
        for index, item in enumerate(initial):
            card = window._cards_by_uid[item.uid]
            self.assertEqual(card.move_up_button.isEnabled(), index > 0)
            self.assertEqual(card.move_down_button.isEnabled(), index < len(initial) - 1)
            self.assertEqual(card.move_up_button.toolTip(), "Move up")
            self.assertEqual(card.move_down_button.toolTip(), "Move down")
            self.assertEqual(card.move_up_button.text(), "")  # Icons are painted as vectors.
            self.assertEqual(card.move_down_button.text(), "")
            self.assertIs(card.move_control.parentWidget(), card)
            self.assertIs(card.move_up_button.parentWidget(), card.move_control)
            self.assertIs(card.move_down_button.parentWidget(), card.move_control)
            self.assertIs(card.remove_button.parentWidget(), card)
            self.app.processEvents()
            divider = card.move_control.findChild(QFrame, "moveDivider")
            self.assertIsNotNone(divider)
            self.assertEqual(divider.width(), 1)
            self.assertEqual(card.move_control.height(), card.remove_button.height())
            self.assertLess(card.move_up_button.geometry().right(), divider.geometry().left())
            self.assertLess(divider.geometry().right(), card.move_down_button.geometry().left())
            self.assertLess(card.move_control.geometry().right(), card.remove_button.geometry().left())
            for control in (card.move_control, card.remove_button):
                self.assertLessEqual(abs(control.geometry().center().y() - card.label_edit.geometry().center().y()), 3)

        first = window._cards_by_uid[initial[0].uid]
        last = window._cards_by_uid[initial[-1].uid]
        QTest.mouseClick(first.move_up_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(last.move_down_button, Qt.MouseButton.LeftButton)
        self.assertEqual([item.uid for item in window.items], [item.uid for item in initial])
        self.assertEqual(len(window._undo_stack), 0)

        rebuilds, output_updates = [], []
        original_rebuild = window._rebuild_combined
        original_request = window._request_output_encoding
        window._rebuild_combined = lambda: (rebuilds.append(True), original_rebuild())[1]
        window._request_output_encoding = lambda: output_updates.append(True)
        QTest.mouseClick(first.move_down_button, Qt.MouseButton.LeftButton)
        self.assertEqual([item.uid for item in window.items], [initial[1].uid, initial[0].uid, initial[2].uid, initial[3].uid])
        self.assertEqual(len(window._undo_stack), 1)
        self.assertIsNone(window._drag_order)
        self.assertEqual(len(rebuilds), 1)
        self.assertEqual(len(output_updates), 1)
        self.assertEqual(initial[0].custom_label, "Custom first")
        self.assertEqual(window._cards_by_uid[initial[2].uid].label_edit.text(), "Image 3")

        middle = window._cards_by_uid[initial[2].uid]
        QTest.mouseClick(middle.move_down_button, Qt.MouseButton.LeftButton)
        self.assertEqual([item.uid for item in window.items], [initial[1].uid, initial[0].uid, initial[3].uid, initial[2].uid])
        self.assertEqual(len(window._undo_stack), 2)
        window.undo()
        self.assertEqual([item.uid for item in window.items], [initial[1].uid, initial[0].uid, initial[2].uid, initial[3].uid])

        last = window._cards_by_uid[initial[3].uid]
        QTest.mouseClick(last.move_up_button, Qt.MouseButton.LeftButton)
        self.assertEqual([item.uid for item in window.items], [initial[1].uid, initial[0].uid, initial[3].uid, initial[2].uid])
        self.assertEqual(len(window._undo_stack), 2)

    def test_arrows_work_with_compact_and_large_previews_and_keep_item_visible(self):
        for preview, tall in ((120, False), (700, True)):
            window = self.make_window(count=3, preview=preview, tall=tall)
            first = window._cards_by_uid[window.items[0].uid]
            self.assertEqual(first.move_control.height(), first.remove_button.height())
            self.assertLess(first.move_control.geometry().right(), first.remove_button.geometry().left())
            QTest.mouseClick(first.move_down_button, Qt.MouseButton.LeftButton)
            self.assertEqual(window.items[1].uid, first.item.uid)
            self.assertTrue(first.move_up_button.isEnabled())
            self.assertTrue(first.move_down_button.isEnabled())
            self.app.processEvents()
            target = window.cards_host.target_geometry(first.item.uid)
            top = window.cards_host.mapTo(window.scroll.viewport(), target.topLeft()).y()
            self.assertGreaterEqual(top, 0)
            if target.height() < window.scroll.viewport().height():
                self.assertLessEqual(top + target.height(), window.scroll.viewport().height())

    def test_compact_and_large_preview_cards(self):
        for preview, tall in ((120, False), (700, True)):
            window = self.make_window(count=3, preview=preview, tall=tall)
            item, handle, press = self.start_drag(window, 1)
            before_size = window.combined.size()
            pointer = QPoint(press.x() + 80, press.y() + 32)
            self.move_held(handle, pointer)
            card = window._cards_by_uid[item.uid]
            self.assertEqual(card.x(), 10)
            self.assertEqual(window.combined.size(), before_size)
            self.release(handle, pointer)
            self.assertEqual(window.combined.size(), before_size)


if __name__ == "__main__":
    unittest.main()
