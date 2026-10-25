"""Generate schematics for hub + pod, verify them, export PDF and BOM.

Run from anywhere:  python3 hardware/gen/build_sch.py
"""
import csv
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hub  # noqa: E402
import pod  # noqa: E402
from gen_sch import check_netlist, write_project  # noqa: E402
from kicad_lib import build_project_lib  # noqa: E402

HW = os.path.normpath(os.path.join(HERE, ".."))


def write_bom(circuit, path):
    rows = circuit.bom()
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["refs", "qty", "value", "footprint", "manufacturer", "mpn", "desc", "dnp"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return rows


def main():
    build_project_lib()
    ok = True
    for build in (hub.build, lambda: hub.build("socket"), pod.build):
        c = build()
        errs = c.erc()
        print(f"[{c.name}] {len(c.parts)} parts, {len(c.nets)} nets, ERC errors: {len(errs)}")
        for e in errs:
            print("   ERC:", e)
        outdir = os.path.join(HW, "kicad", c.name)
        write_project(c, outdir)
        problems = check_netlist(c, outdir)
        print(f"[{c.name}] KiCad netlist vs source: {'MATCH' if not problems else str(len(problems)) + ' mismatches'}")
        for p in problems[:20]:
            print("   NET:", p)
        os.makedirs(os.path.join(HW, "docs"), exist_ok=True)
        pdf = os.path.join(HW, "docs", f"{c.name}_schematic.pdf")
        subprocess.run(["kicad-cli", "sch", "export", "pdf", "-o", pdf, os.path.join(outdir, f"{c.name}.kicad_sch")],
                       check=True, capture_output=True)
        rows = write_bom(c, os.path.join(HW, "fab", c.name, f"{c.name}_bom.csv") if os.makedirs(os.path.join(HW, "fab", c.name), exist_ok=True) is None else None)
        print(f"[{c.name}] BOM lines: {len(rows)}  -> {pdf}")
        ok &= not errs and not problems
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
