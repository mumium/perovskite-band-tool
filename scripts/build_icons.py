"""Encode the taskbar artwork at native Windows icon sizes (requires Pillow)."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
# Include intermediate DPI sizes so Windows can use a native bitmap.
SIZES = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 96, 128, 256)


def main():
    with Image.open(ROOT / 'assets' / 'logo.png') as source:
        source.save(ROOT / 'assets' / 'logo.ico', format='ICO',
                    sizes=[(size, size) for size in SIZES])
        source.resize((256, 256), Image.Resampling.LANCZOS).save(
            ROOT / 'assets' / 'logo-window.png')


if __name__ == '__main__':
    main()
