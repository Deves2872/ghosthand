"""Build "Mac Buddy.app": a double-clickable menu bar app for people who never
open a terminal.

    uv run python scripts/build_app.py            # → dist/Mac Buddy.app
    uv run python scripts/build_app.py --install  # …and copy it to ~/Applications

The app is a thin bundle around this project's environment (it runs
`.venv/bin/python -m mac_buddy.app.main`), so build it from the project folder
after `uv sync`. Its own name, icon and bundle id mean macOS lists it as
"Mac Buddy" in Privacy & Security, not as "Python" or "Terminal".
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "Mac Buddy"
BUNDLE_ID = "io.github.deves2872.macbuddy"


def make_icon(dest: Path):
    """A purple rounded square with a white waving hand, at every size macOS wants."""
    import AppKit
    tmp = Path(tempfile.mkdtemp()) / "AppIcon.iconset"
    tmp.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            img = AppKit.NSImage.alloc().initWithSize_((px, px))
            img.lockFocus()
            inset = px * 0.08
            rect = ((inset, inset), (px - 2 * inset, px - 2 * inset))
            path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, px * 0.2, px * 0.2)
            grad = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
                AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.55, 0.40, 1.0, 1),
                AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.36, 0.20, 0.85, 1))
            grad.drawInBezierPath_angle_(path, -90)
            cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(px * 0.46, 6)
            sym = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("hand.wave.fill", None)
            sym = sym.imageWithSymbolConfiguration_(cfg)
            tinted = AppKit.NSImage.alloc().initWithSize_(sym.size())
            tinted.lockFocus()
            sym.drawAtPoint_fromRect_operation_fraction_((0, 0), AppKit.NSZeroRect, AppKit.NSCompositingOperationSourceOver, 1)
            AppKit.NSColor.whiteColor().set()
            AppKit.NSRectFillUsingOperation(((0, 0), sym.size()), AppKit.NSCompositingOperationSourceAtop)
            tinted.unlockFocus()
            w, h = sym.size()
            tinted.drawAtPoint_fromRect_operation_fraction_(((px - w) / 2, (px - h) / 2), AppKit.NSZeroRect,
                                                            AppKit.NSCompositingOperationSourceOver, 1)
            img.unlockFocus()
            rep = AppKit.NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
            png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
            suffix = "" if scale == 1 else "@2x"
            png.writeToFile_atomically_(str(tmp / f"icon_{size}x{size}{suffix}.png"), True)
    subprocess.run(["iconutil", "-c", "icns", str(tmp), "-o", str(dest)], check=True)


def build() -> Path:
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        sys.exit("Run `uv sync` in the project folder first.")
    app = ROOT / "dist" / f"{NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    plist = {
        "CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": NAME, "CFBundleIconFile": "AppIcon", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.2.0", "CFBundleVersion": "2",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,  # menu bar only: no Dock icon
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription":
            "So you can talk to Mac Buddy instead of typing. Your voice is turned into text on this Mac.",
        "NSAppleEventsUsageDescription": "Mac Buddy can open apps you ask it to use.",
    }
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump(plist, f)
    launcher = app / "Contents" / "MacOS" / NAME
    # Not `exec`: the bundle stays the "responsible" process, so macOS asks for (and lists)
    # permissions under "Mac Buddy".
    launcher.write_text(f"""#!/bin/bash
mkdir -p "$HOME/Library/Logs/mac-buddy"
cd "{ROOT}"
"{py}" -m mac_buddy.app.main >> "$HOME/Library/Logs/mac-buddy/app.log" 2>&1
""")
    launcher.chmod(0o755)
    make_icon(app / "Contents" / "Resources" / "AppIcon.icns")
    # an ad-hoc signature gives macOS a stable identity to remember permissions by
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True, capture_output=True)
    return app


def main():
    app = build()
    print(f"Built {app}")
    if "--install" in sys.argv:
        dest = Path.home() / "Applications" / app.name
        dest.parent.mkdir(exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(app, dest, symlinks=True)
        print(f"Installed {dest}. Open it from Finder ▸ Applications, or: open \"{dest}\"")


if __name__ == "__main__":
    main()
