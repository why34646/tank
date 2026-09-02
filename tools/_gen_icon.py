"""Convert app/assets/icon/app_icon.png -> app/assets/icon/app_icon.ico.

Generates a multi-size ICO (16/32/48/64/128/256) using embedded PNG data,
which is the most compatible format for both PyInstaller and Inno Setup.

Run from project root: python tools/_gen_icon.py
"""

import struct
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("[WARN] Pillow not installed. Run: pip install pillow")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "app" / "assets" / "icon" / "app_icon.png"
DST = ROOT / "app" / "assets" / "icon" / "app_icon.ico"
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    if not SRC.exists():
        print(f"[WARN] {SRC} not found. Nothing to do.")
        return 0

    img = Image.open(SRC).convert("RGBA")
    imgs = [img.resize(s, Image.LANCZOS) for s in SIZES]

    ico = bytearray()
    ico += struct.pack("<HHH", 0, 1, len(imgs))  # ICONDIR
    dir_off = len(ico)
    for _ in imgs:
        ico += b"\x00" * 16  # reserve ICONDIRENTRY slots
    offset = 6 + len(imgs) * 16

    import io
    for i, (im, (w, h)) in enumerate(zip(imgs, SIZES)):
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        wi = 0 if w >= 256 else w
        hi = 0 if h >= 256 else h
        ico[dir_off + i * 16 : dir_off + (i + 1) * 16] = struct.pack(
            "<BBBBHHII", wi, hi, 0, 0, 1, 32, len(png_bytes), offset
        )
        ico += png_bytes
        offset += len(png_bytes)

    DST.write_bytes(bytes(ico))
    print(f"[OK] {DST.name} generated ({len(imgs)} sizes, {len(ico)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
