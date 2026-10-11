# Image Stacker

Image Stacker is a lightweight Linux desktop utility that combines images copied to the clipboard into one vertically stacked image. It is designed for screenshot workflows such as pasting several captures into ChatGPT as a single image.

## Features

- Paste clipboard images with **Ctrl+V** or the **Paste Image** button.
- Append images vertically in paste order, centered on a canvas as wide as the widest source.
- Preserve source pixels in the normal combined output; the preview size only affects the GUI.
- Automatically publish the combined result to the clipboard after edits.
- Reorder cards by dragging their right-side six-dot handle or using the header **▲ / ▼** buttons.
- Rename images, include readable labels in the output, delete individual images, undo, and clear the stack.
- Save the current combined output as PNG.
- Show the encoded PNG size and warn near the app's configured 20 MB threshold.
- Optionally enable **Fit for ChatGPT** to scale an oversized final composition toward 19 MB. This is off by default and never changes source images.
- Use Qt's clipboard integration in Linux desktop sessions, including Wayland.

## Screenshots

Screenshots will be added here when release captures are available.

| Main window | Stacked images |
| --- | --- |
| _Screenshot placeholder_ | _Screenshot placeholder_ |

## Installation on Arch Linux / Omarchy

Install Python and PyQt6 from the Arch repositories:

```sh
sudo pacman -S python python-pyqt6
```

Then, from the project directory, run the per-user installer:

```sh
./install.sh
```

The installer copies the application to `~/.local/share/image-stacker/`, installs the `~/.local/bin/image-stacker` command, and creates `~/.local/share/applications/image-stacker.desktop`. The app installation does not require root. The package dependency can also be installed in another Python environment with `python3 -m pip install -r requirements.txt`, although the system PyQt6 package is preferred on Arch/Omarchy.

If `~/.local/bin` is not on `PATH`, launch with its full path or add this to your shell startup file:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

To remove the per-user installation, run:

```sh
./uninstall.sh
```

## Usage

1. Launch **Image Stacker** from your application launcher or run `~/.local/bin/image-stacker`.
2. Copy a screenshot from any application.
3. Click **Paste Image** or press **Ctrl+V**.
4. Copy another screenshot and paste it the same way. Repeat for more images.
5. Rename, remove, move, or drag cards to arrange the stack. The **Include labels in combined image** option controls whether label headers appear in the result.
6. Switch to ChatGPT and press **Ctrl+V** to paste the combined image. **Save As…** can save the same selected output as a PNG file.

The visible preview can be resized independently with the **Preview size** slider. Preview scaling never changes the source or normal full-resolution output.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+V` | Add the current clipboard image |
| `Ctrl+Z` | Undo the last change |
| `Delete` | Remove the focused image card |
| `Ctrl+Shift+C` | Copy the combined image |
| `Ctrl+Shift+S` | Save as PNG |
| `Ctrl+Shift+Backspace` | Clear the stack |
| `Escape` | Cancel an active card reorder |

The header's **▲** and **▼** controls move one card position and disable at the corresponding end of the list. To drag, press anywhere on the narrow right-side handle; its six dots indicate the grab area. Cards move vertically within the list, and **Ctrl+Z** undoes a completed reorder.

## Clipboard behavior

Image Stacker reads the clipboard only when you explicitly paste with the button or **Ctrl+V**. After a successful stack edit, it publishes the newly combined image back to the clipboard. Copying the next external screenshot replaces that clipboard content; pasting it into Image Stacker appends it to the stack. The app does not automatically ingest its own clipboard update. If the clipboard does not contain an image, the stack is left unchanged and a status message is shown.

Undo, reordering, renaming, label visibility changes, and deleting an image refresh the clipboard when a non-empty output remains. Clearing the last image leaves the clipboard untouched.

## ChatGPT image-size awareness

The header reports the **encoded PNG size** of the selected output alongside its dimensions and image count. The UI warns at 18,000,000 bytes and indicates when the output exceeds the app's configured 20,000,000-byte threshold. These are decimal byte values (18 MB and 20 MB).

**Fit for ChatGPT** is disabled by default. When enabled, an output above 19,000,000 bytes is scaled down with its aspect ratio intact toward an approximately 19 MB target. This optimization applies only to a copy of the final combined output: source screenshots remain unchanged. Both clipboard copy and Save As use the currently selected output mode. PNG encoding runs off the GUI thread after content changes, not while preview size changes or a card is being dragged.

The size display reflects the PNG bytes generated by the app. Upload limits and acceptance can depend on the receiving service and may change; check the service's current guidance if an upload is rejected.

## Wayland compatibility

The app uses PyQt6's `QClipboard` and Qt's active platform plugin; it does not use temporary files for normal clipboard operations. Launch it from the logged-in graphical session so it inherits that session's Wayland environment and clipboard access. Clipboard behavior depends on the Qt platform backend and the desktop session. The project is intended for Wayland desktops such as Omarchy, but no separate compositor-specific clipboard service is bundled.

## Repository structure

```text
image-stacker/
├── .gitignore
├── CHANGELOG.md
├── LICENSE
├── README.md
├── docs/
│   └── ARCHITECTURE.md
├── image-stacker.desktop
├── image_stacker.py
├── install.sh
├── requirements.txt
├── src/
│   └── image_stacker/
│       ├── __init__.py
│       ├── __main__.py
│       └── main.py
├── tests/
│   ├── test_drag_reorder.py
│   └── test_output_encoding.py
└── uninstall.sh
```

## Development

Requirements are Python 3 and PyQt6. On Arch/Omarchy, install PyQt6 with `sudo pacman -S python-pyqt6`. From the repository root, start the app with:

```sh
python3 image_stacker.py
```

The package entry point can also be run with:

```sh
PYTHONPATH=src python3 -m image_stacker
```

Run the automated Qt tests with an offscreen platform (useful in headless environments):

```sh
QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=generic python3 -m unittest discover -s tests -v
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for implementation details.

## Troubleshooting

- **PyQt6 is missing:** On Arch/Omarchy, run `sudo pacman -S python-pyqt6`, then retry the launcher.
- **`image-stacker` is not found:** Run `~/.local/bin/image-stacker` directly or add `~/.local/bin` to `PATH`.
- **The application launcher does not show Image Stacker:** Confirm `~/.local/share/applications/image-stacker.desktop` exists, then refresh or reopen the launcher.
- **Paste reports no image:** Copy the image again as an image (not a file name or text) and use **Paste Image** or **Ctrl+V**.
- **Clipboard access is unavailable:** Launch from a terminal inside the active desktop session and check that the Qt Wayland platform plugin is available in the installed PyQt6 environment.
- **An upload is rejected despite the size warning:** Enable **Fit for ChatGPT** and check the receiving service's current upload guidance.

## License

Image Stacker is distributed under the [MIT License](LICENSE).
