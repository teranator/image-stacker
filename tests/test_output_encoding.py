"""Tests for actual PNG sizing, ChatGPT fitting, and clipboard output mode."""

import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_PLATFORMTHEME", "generic")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.image_stacker.main import ImageStacker, OutputEncoder, StackItem


class OutputEncodingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_large_png_is_fitted_without_changing_source(self):
        width, height = 3000, 2600
        raw = os.urandom(width * height * 3)
        source = QImage(raw, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        results = []
        worker = OutputEncoder(7, source, True)
        worker.encoded.connect(lambda *result: results.append(result))
        started = time.monotonic()
        worker.run()
        self.assertLess(time.monotonic() - started, 10)
        revision, output, png, error = results[0]
        self.assertEqual(revision, 7)
        self.assertEqual(error, "")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLessEqual(len(png), OutputEncoder.TARGET_BYTES)
        self.assertLess(output.width(), source.width())
        self.assertLess(output.height(), source.height())
        self.assertAlmostEqual(output.width() / output.height(), width / height, places=2)
        self.assertEqual(source.size().width(), width)
        self.assertEqual(source.size().height(), height)

    def test_fit_mode_controls_warning_clipboard_and_saved_png(self):
        width, height = 3000, 2600
        raw = os.urandom(width * height * 3)
        source = QImage(raw, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        unfit_results = []
        unfit = OutputEncoder(0, source, False)
        unfit.encoded.connect(lambda *result: unfit_results.append(result))
        unfit.run()
        self.assertGreater(len(unfit_results[0][2]), ImageStacker.CHATGPT_LIMIT_BYTES)

        window = ImageStacker()
        window.items = [StackItem(source)]
        window.combined = source
        window._refresh()
        window.show()
        self.addCleanup(window.close)
        window._output_encoded(0, source, unfit_results[0][2], "")
        self.assertIn("exceeds 20 MB", window.output_size_label.text())
        self.assertFalse(window.fit_checkbox.isChecked())
        window.fit_checkbox.setChecked(True)
        deadline = time.monotonic() + 3
        while window._output_pending and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(window._output_pending)
        self.assertLess(window.output_image.width(), source.width())
        self.assertLessEqual(len(window._output_png), OutputEncoder.TARGET_BYTES)
        mime = QApplication.clipboard().mimeData()
        self.assertEqual(bytes(mime.data("image/png")), window._output_png)
        with tempfile.TemporaryDirectory() as directory:
            saved = os.path.join(directory, "fitted.png")
            self.assertTrue(window._write_output_png(saved))
            with open(saved, "rb") as image_file:
                self.assertEqual(image_file.read(), window._output_png)
        self.assertEqual(source.width(), width)
        self.assertEqual(source.height(), height)

    def test_async_output_size_and_clipboard_match_selected_png(self):
        window = ImageStacker()
        window.items = [StackItem(QImage(240, 120, QImage.Format.Format_RGB32))]
        self.assertTrue(window._rebuild_combined())
        window._refresh()
        window.show()
        self.addCleanup(window.close)
        window._request_output_encoding()
        deadline = time.monotonic() + 3
        while window._output_pending and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(window._output_pending)
        self.assertEqual(window.output_image.size(), window.combined.size())
        self.assertEqual(len(window._output_png), len(bytes(window._output_png)))
        self.assertIn("MB", window.output_size_label.text())
        mime = QApplication.clipboard().mimeData()
        self.assertTrue(mime.hasFormat("image/png"))
        self.assertEqual(bytes(mime.data("image/png")), window._output_png)
        self.assertEqual(QApplication.clipboard().image().size(), window.output_image.size())
        revision = window._output_revision
        output_dimensions = window.output_image.size()
        window.preview_slider.setValue(180)
        self.assertEqual(window._output_revision, revision)
        self.assertEqual(window.output_image.size(), output_dimensions)
        window.fit_checkbox.setChecked(True)
        self.assertTrue(window.fit_for_chatgpt)
        deadline = time.monotonic() + 3
        while window._output_pending and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(window._output_pending)
        self.assertEqual(window.output_image.size(), window.combined.size())
        with tempfile.TemporaryDirectory() as directory:
            saved = os.path.join(directory, "stack.png")
            self.assertTrue(window._write_output_png(saved))
            with open(saved, "rb") as image_file:
                self.assertEqual(image_file.read(), window._output_png)


if __name__ == "__main__":
    unittest.main()
