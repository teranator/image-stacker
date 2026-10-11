# Architecture

## Entry point and GUI

`src/image_stacker/main.py` defines the window and starts the Qt event loop. `src/image_stacker/__main__.py` enables `python -m image_stacker`; the root `image_stacker.py` remains a convenience compatibility launcher. The main window contains a counter, label-output checkbox, an action row with Paste Image and stack actions, and a scroll area with one card per item. The Paste Image button and Ctrl+V connect to the same `paste_image` slot.

## Item model and labels

Each `StackItem` owns a full-resolution `QImage` and `custom_label`. A `None` label means automatic; its visible value is derived from its current position (`Image 1`, `Image 2`, ...). A non-empty string is custom and stays attached to that image when other items are deleted. Clearing the inline field returns it to automatic naming. Undo snapshots retain both images, custom-label state, and the label-output option.

## Clipboard and composition

Ctrl+V or the Paste Image button reads `QApplication.clipboard().image()`, copies the source into owned memory, appends it in paste order, and composes the output. The app writes that output to the system clipboard after adding, deleting, reordering, renaming, toggling labels, and undoing to a non-empty stack. It only reads clipboard images in response to an explicit paste action; its own clipboard update cannot add an item on its own. Clear or an operation leaving no images leaves the current clipboard untouched.

The full-resolution combined canvas width is the widest source width. Images are drawn at their original dimensions and centered horizontally. When labels are enabled, each item gets a compact header rendered at source output resolution before its source pixels. Disabling labels restores direct, zero-gap stacking. Source images are never changed to create the composition.

## PNG size and ChatGPT fit

`combined` remains the full-resolution composition. After a content or output-mode change, an `OutputEncoder` QThread encodes a PNG from that canvas and reports its actual byte length. It uses PNG compression and, only when **Fit for ChatGPT** is enabled and the encoded output exceeds 19,000,000 bytes, scales a copy of the final canvas down with smooth aspect-preserving interpolation until it reaches the target. Each original `StackItem.image` and the full-resolution `combined` image remain untouched. Preview resizing and in-progress drag motion do not start encoders. New revisions request interruption of obsolete workers and stale results are ignored.

The selected output image and its encoded PNG bytes are retained together. Both clipboard MIME data (`image/png` plus Qt image data) and Save As consume this same result. The header displays the selected output dimensions and encoded PNG size, with a warning from 18,000,000 bytes and an exceeded-limit warning above 20,000,000 bytes. Clipboard update occurs on the GUI thread after the worker completes. Closing waits for active encoders to finish so no QThread is destroyed while running.

## Preview and deletion

Cards contain a right-side six-dot handle, compact vector-triangle up/down buttons in the header inside a shared segmented border, an inline label field, and an `ImagePreview`. The two arrow buttons are independent accessible controls separated by a narrow divider; their boundary disabled states follow card position. The entire handle rectangle starts a reorder after Qt's standard pointer threshold; other card controls remain independent. There is no `QDrag` or platform drag loop. The existing card widget stays parented to `CardListHost` and floats above it at a fixed horizontal position; its top follows the pointer's Y coordinate using the original press offset. Its Y position is clamped to the scroll viewport and its widget parent clips it horizontally. A same-height placeholder remains in the card sequence. `CardListHost` positions the existing widgets directly, allowing the placeholder and neighboring cards to animate into new positions over 135 ms. A short timer scrolls near the viewport edges and updates the floating card and gap from the same pointer location.

During the drag, only card geometry, the temporary visible order, and automatic preview labels change. Source images, the combined canvas, and the clipboard remain untouched. Mouse release adopts the pending order, adds one Undo snapshot, recomposes once, updates automatic labels, and copies once. Arrow buttons move one item and call the same `_commit_reorder` method used by a completed drag; that shared method records one snapshot, rebuilds once, refreshes automatic labels and arrow availability, animates the cards, scrolls the moved card into view, and queues one clipboard update. Escape restores the original widget order without an Undo entry or clipboard change. If pointer movement leaves the application window, the interaction cancels and restores the original order. QPropertyAnimation instances are stopped and scheduled for deletion whenever a newer layout supersedes them.

Preview painting scales the source image to the available card width and caps its height at the global Preview size setting (120–700 logical pixels, default 520). A scaled `QImage` is cached per preview size and discarded when the control or card width changes. Source `QImage`s remain full resolution for composition; any ChatGPT fit scaling applies later to a final-canvas copy only. The chosen preview height is stored using Qt `QSettings`. The scroll area's `CardListHost` sizes cards from each existing card layout's height-for-width hint, then assigns card geometry directly; no image preview or card is recreated during a drag. Hover, focus, and drag styles highlight card state. The remove button deletes only its associated model item; Delete also removes a focused card.

## Undo

Before each content change, the app keeps an in-memory snapshot of the item list and label setting. Ctrl+Z restores that snapshot and refreshes the clipboard when the restored state contains images. Restoring an empty state leaves the clipboard unchanged.

## Installation layout

`install.sh` copies the Python package into `~/.local/share/image-stacker/image_stacker/`, writes `~/.local/bin/image-stacker`, and installs a desktop entry in `~/.local/share/applications/`. The generated desktop entry invokes the per-user launcher by absolute path. `uninstall.sh` removes those installed paths. Both scripts accept `IMAGE_STACKER_INSTALL_ROOT` for isolated smoke tests; when unset, paths remain under `~/.local`.
