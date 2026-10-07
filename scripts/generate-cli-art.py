#!/usr/bin/env python3
"""Rebuild the checked-in terminal portraits with Chafa; runtime uses ANSI only."""
import argparse
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ART = ROOT / "assets" / "cli"
POSES = {
    "rest": None,
    "low": ((162, 505), (930, 505)),
    "left": ((168, 383), (922, 430)),
    "right": ((168, 430), (922, 383)),
}


def arms(pose):
    if pose is None:
        return ""
    (lx, ly), (rx, ry) = pose
    return f'''<defs>
      <linearGradient id="arm" x1="0" x2="1">
        <stop stop-color="#b5dce6"/><stop offset=".5" stop-color="#e4daed"/>
        <stop offset="1" stop-color="#b2b9dc"/>
      </linearGradient>
    </defs><g fill="none" stroke="url(#arm)" stroke-width="22" stroke-linecap="round">
      <path d="M 234 640 Q 154 592 162 522 L {lx} {ly}"/>
      <path d="M 867 640 Q 934 592 930 522 L {rx} {ry}"/>
      <path d="M {lx} {ly} l -4 -24 M {lx} {ly} l 20 -19"/>
      <path d="M {rx} {ry} l 4 -24 M {rx} {ry} l -20 -19"/>
    </g>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chafa", default="chafa")
    args = parser.parse_args()
    source = (ART / "pearl.svg").read_text()
    with tempfile.TemporaryDirectory(prefix="bopbop-art-") as scratch:
        for pose, coordinates in POSES.items():
            svg = pathlib.Path(scratch) / f"{pose}.svg"
            # Place arms behind the unmodified portrait.
            svg.write_text(source.replace("<image", arms(coordinates) + "<image", 1))
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
