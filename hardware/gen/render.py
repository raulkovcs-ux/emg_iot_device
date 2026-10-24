"""Render PCB layer views and schematic pages to PNG for the docs (needs kicad-cli, rsvg-convert, pdftoppm)."""
import os
import subprocess
import tempfile

from PIL import Image

HW = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(HW, "..", "docs", "images")


def svg2png(svg, png, width):
    subprocess.run(["rsvg-convert", "-w", str(width), "-b", "white", svg, "-o", png], check=True)


def board(name, views, width=1100):
    pcb = os.path.join(HW, "kicad", name, f"{name}.kicad_pcb")
    with tempfile.TemporaryDirectory() as td:
        pngs = []
        for tag, layers, mirror in views:
            svg = os.path.join(td, f"{tag}.svg")
            cmd = ["kicad-cli", "pcb", "export", "svg", "--layers", layers, "--page-size-mode", "2",
                   "--exclude-drawing-sheet", "-o", svg, pcb]
            if mirror:
                cmd.insert(4, "--mirror")
            subprocess.run(cmd, check=True, capture_output=True)
            png = os.path.join(OUT, f"{name}_{tag}.png")
            svg2png(svg, png, width)
            pngs.append(png)
    return pngs


def side_by_side(paths, out, gap=20):
    ims = [Image.open(p) for p in paths]
    h = max(i.height for i in ims)
    canvas = Image.new("RGB", (sum(i.width for i in ims) + gap * (len(ims) - 1), h), "white")
    x = 0
    for i in ims:
        canvas.paste(i, (x, 0))
        x += i.width + gap
    canvas.save(out)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    board("hub", [("top", "F.Cu,F.SilkS,F.Mask,Edge.Cuts", False), ("in2", "In2.Cu,Edge.Cuts", False),
                  ("bottom", "B.Cu,B.SilkS,Edge.Cuts", True), ("assembly_top", "F.Fab,F.CrtYd,Edge.Cuts", False)])
    p = board("pod", [("top", "F.Cu,F.SilkS,Edge.Cuts", False), ("skin_side", "B.Cu,B.Mask,B.SilkS,Edge.Cuts", True)], 500)
    side_by_side(p, os.path.join(OUT, "pod_both_sides.png"))
    for n in ("hub", "pod"):
        pdf = os.path.join(HW, "docs", f"{n}_schematic.pdf")
        subprocess.run(["pdftoppm", "-r", "90", "-png", pdf, os.path.join(OUT, f"{n}_schematic")], check=True)
    print("rendered to", os.path.normpath(OUT))
