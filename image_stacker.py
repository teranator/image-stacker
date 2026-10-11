#!/usr/bin/env python3
"""Compatibility entry point for running Image Stacker from the project root."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from image_stacker.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
