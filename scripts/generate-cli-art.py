#!/usr/bin/env python3
"""Rebuild the checked-in terminal portraits with Chafa; runtime uses ANSI only."""
import argparse
import base64
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ART = ROOT / "assets" / "cli"
POSES = ("rest", "blink", "grin", "beam", "wink")


def patch(name, region, cx, cy, rx, ry, scale=2.9, tx=79, ty=35):
    """Align and feather an existing expression over the original face only."""
    image = base64.b64encode((ART / "expressions" / f"{name}.png").read_bytes()).decode()
    return f'''<defs>
      <radialGradient id="fade-{region}">
        <stop offset=".8" stop-color="white"/><stop offset="1" stop-color="black"/>
      </radialGradient>
      <mask id="mask-{region}" maskUnits="userSpaceOnUse" x="150" y="230" width="790" height="730">
        <ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="url(#fade-{region})"/>
      </mask>
    </defs><g mask="url(#mask-{region})">
      <image width="320" height="320" transform="translate({tx} {ty}) scale({scale})"
        xlink:href="data:image/png;base64,{image}"/>
    </g>'''


def expression(pose):
    if pose == "rest":
        return ""
    if pose == "wink":
        return patch("winking", "right-eye", 653, 501, 54, 46)
    layers = patch("laughing", "left-eye", 433, 501, 54, 46)
    layers += patch("laughing", "right-eye", 653, 501, 54, 46)
    if pose in {"grin", "beam"}:
        # The mouth grows from the same laughing key pose; the original body,
        # highlights, and cheeks stay in place throughout the greeting.
        scale = 2.2 if pose == "grin" else 2.9
        layers += patch("laughing", "mouth", 548, 576, 95, 75,
                        scale=scale, tx=548 - 161 * scale, ty=576 - 188 * scale)
    return layers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chafa", default="chafa")
    args = parser.parse_args()
    source = (ART / "pearl.svg").read_text()
    with tempfile.TemporaryDirectory(prefix="bopbop-art-") as scratch:
        for pose in POSES:
            svg = pathlib.Path(scratch) / f"{pose}.svg"
            svg.write_text(source.replace("</svg>", expression(pose) + "</svg>"))
            for size, dimensions in (("wide", "34x16"), ("compact", "22x10")):
                result = subprocess.check_output([
                    args.chafa, "--format", "symbols", "--colors", "full",
                    "--size", dimensions, "--stretch", "--animate", "off",
                    "--optimize", "0", "--symbols", "half+quad", "--bg",
                    "#10141b", "--work", "9", str(svg),
                ], text=True)
                # Assets contain SGR colors and printable glyphs only; drop
                # Chafa's cursor visibility toggles and other terminal controls.
                result = re.sub(r"\x1b\[(?![0-9;]*m)[0-9;?]*[A-Za-z]", "", result)
                result = result.strip("\n") + "\n"
                (ART / f"pearl-{size}-{pose}.ansi").write_text(result)
    print("Rebuilt Pearl terminal portraits")


if __name__ == "__main__":
    main()
