"""Write a KiCad 7 hierarchical schematic from a Circuit.

Connections are drawn label-style: every connected pin gets a global label or a
power symbol right at its end point, unconnected pins get a no-connect flag.
That keeps the generated sheets readable without a router and lets KiCad's own
netlister prove the schematic matches the Circuit (see check_netlist()).
"""
import datetime
import json
import os
import subprocess
import uuid

from circuit import POWER_SYMBOLS
from kicad_lib import SymInfo, embedded
from sexpr import Q, dump, find, find_all, parse

GRID = 2.54
PAPERS = [("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594)]
NS = uuid.UUID("6f1b1a52-6a8e-4f6e-9a0e-fa3a0e7b0d11")
DIRV = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}  # schematic coords, y down


def uid(*parts):
    return Q(str(uuid.uuid5(NS, "/".join(map(str, parts)))))


def snap(v):
    return round(v / GRID) * GRID


def eff(size=1.27, justify=None, hide=False):
    e = ["effects", ["font", ["size", size, size]]]
    if justify:
        e.append(["justify"] + justify.split())
    if hide:
        e.append("hide")
    return e


def label_len(text):
    return 1.05 * len(text) + 3.0


def xform(px, py, rot):
    """Library pin coordinate -> schematic offset for a symbol rotated by rot (CCW)."""
    r = rot % 360
    x, y = {0: (px, py), 90: (-py, px), 180: (-px, -py), 270: (py, -px)}[r]
    return x, -y


class SheetWriter:
    def __init__(self, circuit, project, root_uuid, sheet_key, sheet_uuid, page):
        self.c, self.project, self.root_uuid = circuit, project, root_uuid
        self.key, self.sheet_uuid, self.page = sheet_key, sheet_uuid, page
        self.items = []
        self.lib_ids = set()
        self.pin_net = circuit.pin_net()
        self.pwr_count = 0
        self.placed_pins = []  # (x, y, net) for self-check

    @property
    def inst_path(self):
        return f"/{self.root_uuid}" + (f"/{self.sheet_uuid}" if self.sheet_uuid else "")

    def _symbol(self, lib_id, ref, value, fp, x, y, rot, unit, pins, extra_props=(), in_bom=True, dnp=False, ref_at=None, val_at=None, hide_val=False, val_angle=0, justify=None):
        self.lib_ids.add(lib_id)
        u = uid(self.project, self.key, ref, unit)
        rx, ry = ref_at or (x, y - 3)
        vx, vy = val_at or (x, y + 3)
        node = ["symbol", ["lib_id", Q(lib_id)], ["at", x, y, rot], ["unit", unit],
                ["in_bom", "yes" if in_bom else "no"], ["on_board", "yes" if in_bom or not ref.startswith("#") else "no"],
                ["dnp", "yes" if dnp else "no"], ["uuid", u],
                ["property", Q("Reference"), Q(ref), ["at", rx, ry, 0], eff(justify=justify, hide=ref.startswith("#"))],
                ["property", Q("Value"), Q(value), ["at", vx, vy, val_angle], eff(justify=justify or ("left" if val_angle else None), hide=hide_val)],
                ["property", Q("Footprint"), Q(fp), ["at", x, y, 0], eff(hide=True)],
                ["property", Q("Datasheet"), Q("~"), ["at", x, y, 0], eff(hide=True)]]
        for k, v in extra_props:
            node.append(["property", Q(k), Q(v), ["at", x, y, 0], eff(hide=True)])
        for num in pins:
            node.append(["pin", Q(num), ["uuid", uid(self.project, self.key, ref, unit, "pin", num)]])
        node.append(["instances", ["project", Q(self.project), ["path", Q(self.inst_path), ["reference", Q(ref)], ["unit", unit]]]])
        self.items.append(node)

    def _power(self, net, x, y, outward):
        lib_id = POWER_SYMBOLS[net]
        info = SymInfo(lib_id)
        pin_angle = info.pins["1"]["angle"]
        rot = (outward - pin_angle) % 360
        self.pwr_count += 1
        ref = f"#PWR_{self.key}_{self.pwr_count:03d}"
        dx, dy = DIRV[outward]
        vertical = outward in (90, 270)
        dist = 4.0 if vertical else 5.0
        self._symbol(lib_id, ref, net, "", x, y, rot, 1, ["1"], in_bom=False,
                     val_at=(x + dx * dist, y + dy * dist), val_angle=90 if vertical else 0,
                     justify=("left" if outward == 90 else "right") if vertical else None)

    def _label(self, net, x, y, outward):
        just = {0: "left", 90: "left", 180: "right", 270: "right"}[outward]
        self.items.append(["global_label", Q(net), ["shape", "passive"], ["at", x, y, outward], ["fields_autoplaced"],
                           eff(justify=just), ["uuid", uid(self.project, self.key, "gl", net, x, y)],
                           ["property", Q("Intersheetrefs"), Q("${INTERSHEET_REFS}"), ["at", x, y, 0], eff(hide=True)]])

    def _nc(self, x, y):
        self.items.append(["no_connect", ["at", x, y], ["uuid", uid(self.project, self.key, "nc", x, y)]])

    def _text(self, text, x, y, size=1.27):
        self.items.append(["text", Q(text), ["at", x, y, 0], eff(size, "left bottom"), ["uuid", uid(self.project, self.key, "txt", text[:40], x, y)]])

    # ------------------------------------------------------------------ layout
    def layout(self):
        parts = [p for p in self.c.parts.values() if p.sheet == self.key]
        entries = []
        for p in parts:
            for unit in sorted(p.sym.units):
                rot = 0
                pins = p.sym.pins_of_unit(unit)
                if p.lib_id in ("Device:R", "Device:C", "Device:L", "Device:FerriteBead_Small", "Device:Polyfuse",
                                "Device:Thermistor_NTC", "Device:LED"):
                    rot = 90 if p.lib_id != "Device:LED" else 0
                if p.lib_id.startswith("famaband:DryElectrode"):
                    rot = 0
                entries.append((p, unit, rot, pins))

        def extent(entry):
            p, unit, rot, pins = entry
            b = p.sym.unit_bbox(unit)
            corners = [xform(x, y, rot) for x in (b[0], b[2]) for y in (b[1], b[3])]
            x0, x1 = min(c[0] for c in corners), max(c[0] for c in corners)
            y0, y1 = min(c[1] for c in corners), max(c[1] for c in corners)
            m = {0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0}
            for num, pin in pins.items():
                o = (pin["angle"] + rot + 180) % 360
                net = self.pin_net.get((p.ref, num))
                need = 2.0 if net is None else (7.0 if net in POWER_SYMBOLS else label_len(net))
                m[o] = max(m[o], need)
            return x0 - m[180] - 2, y0 - m[90] - 4, x1 + m[0] + 2, y1 + m[270] + 4

        # big ICs first (one row), then functional groups in creation order, one row per group
        big = [e for e in entries if len(e[0].sym.pins) > 8]
        small = [e for e in entries if len(e[0].sym.pins) <= 8]
        for paper, W, H in PAPERS:
            x, y, row_h = 25.0, 40.0, 0.0
            placed = []
            ok = True
            prev_row = None
            for e in big + small:
                ex = extent(e)
                w, h = ex[2] - ex[0], ex[3] - ex[1]
                grp = "big" if e in big else e[0].row
                if (prev_row is not None and grp != prev_row) or x + w > W - 15:
                    x, y, row_h = 25.0, y + row_h + 6, 0.0
                prev_row = grp
                if y + h > H - 45:
                    ok = False
                    break
                placed.append((e, snap(x - ex[0]), snap(y - ex[1])))
                x += w + 4
                row_h = max(row_h, h)
            if ok:
                return paper, W, H, placed
        raise RuntimeError(f"sheet {self.key} does not fit on A1")

    def build(self):
        paper, W, H, placed = self.layout()
        for (p, unit, rot, pins), sx, sy in placed:
            b = p.sym.unit_bbox(unit)
            corners = [xform(x, y, rot) for x in (b[0], b[2]) for y in (b[1], b[3])]
            top = min(c[1] for c in corners); bot = max(c[1] for c in corners)
            left = min(c[0] for c in corners)
            small = len(p.sym.pins) <= 3
            ref_at = (sx + (left - 1.0 if small and rot else 0), sy + top - (1.5 if not small else 2.0))
            val_at = (sx + (left - 1.0 if small and rot else 0), sy + bot + 2.0)
            big = len(p.sym.pins) > 8
            if big:
                ref_at = (sx + left, sy + bot + 12)
                val_at = (sx + left, sy + bot + 14.5)
            props = [("MPN", p.mpn), ("Manufacturer", p.manufacturer), ("Description", p.desc)]
            self._symbol(p.lib_id, p.ref, p.value, p.footprint, sx, sy, rot, unit, sorted(pins),
                         extra_props=[x for x in props if x[1]], in_bom=not p.ref.startswith("#"), dnp=p.dnp,
                         ref_at=ref_at, val_at=val_at, justify="left" if big else None)
            seen = {}
            for num, pin in pins.items():
                ox, oy = xform(pin["x"], pin["y"], rot)
                px, py = round(sx + ox, 3), round(sy + oy, 3)
                net = self.pin_net.get((p.ref, num))
                if (px, py) in seen:
                    if seen[(px, py)] != net:
                        raise ValueError(f"{p.ref}: stacked pins at same point on different nets")
                    continue
                seen[(px, py)] = net
                outward = (pin["angle"] + rot + 180) % 360
                if net is None:
                    if pin["type"] != "no_connect" or True:
                        self._nc(px, py)
                    continue
                self.placed_pins.append((px, py, net))
                if net in POWER_SYMBOLS:
                    self._power(net, px, py, outward)
                else:
                    self._label(net, px, py, outward)
        title = self.c.sheets[self.key]
        self._text(title, 20, 25, 2.5)
        for i, note in enumerate(self.c.notes.get(self.key, [])):
            self._text(note, 20, H - 40 + 0 * i, 1.5)
        return paper

    def write(self, path, root=False, sheets=None):
        paper = self.build()
        rev = "A"
        node = ["kicad_sch", ["version", "20230121"], ["generator", "eeschema"],
                ["uuid", Q(self.root_uuid if root else self.sheet_uuid)], ["paper", Q(paper)],
                ["title_block", ["title", Q(f"{self.c.title} - {self.c.sheets[self.key].split(':')[0]}")],
                 ["date", Q(datetime.date.today().isoformat())], ["rev", Q(rev)],
                 ["company", Q("FAMA-Net thesis hardware")],
                 ["comment", 1, Q("Generated by hardware/gen/gen_sch.py from the Python netlist - edit the netlist, not this file")]],
                ["lib_symbols"] + [embedded(l) for l in sorted(self.lib_ids)]]
        node += self.items
        if sheets:
            node += sheets
        if root:
            node.append(["sheet_instances", ["path", Q("/"), ["page", Q("1")]]])
        with open(path, "w") as f:
            f.write(dump(node) + "\n")


def write_project(circuit, outdir):
    os.makedirs(outdir, exist_ok=True)
    name = circuit.name
    root_uuid = str(uid(name, "root"))
    keys = list(circuit.sheets)
    if len(keys) == 1:
        sw = SheetWriter(circuit, name, root_uuid, keys[0], None, 1)
        sw.write(os.path.join(outdir, f"{name}.kicad_sch"), root=True)
    else:
        sheet_nodes = []
        x = 30
        for i, key in enumerate(keys):
            su = str(uid(name, "sheet", key))
            sw = SheetWriter(circuit, name, root_uuid, key, su, i + 2)
            sw.write(os.path.join(outdir, f"{key}.kicad_sch"))
            title = circuit.sheets[key].split(":")[0]
            sheet_nodes.append(["sheet", ["at", x, 60], ["size", 70, 40], ["fields_autoplaced"],
                                ["stroke", ["width", 0.1524], ["type", "solid"]], ["fill", ["color", 0, 0, 0, 0.0]],
                                ["uuid", Q(su)],
                                ["property", Q("Sheetname"), Q(title), ["at", x, 59.3, 0], eff(justify="left bottom")],
                                ["property", Q("Sheetfile"), Q(f"{key}.kicad_sch"), ["at", x, 100.6, 0], eff(justify="left top")],
                                ["instances", ["project", Q(name), ["path", Q(f"/{root_uuid}"), ["page", Q(str(i + 2))]]]]])
            x += 90
        root = SheetWriter(circuit, name, root_uuid, keys[0], None, 1)
        root.items = []
        root.build = lambda: "A4" if False else "A3"
        for i, key in enumerate(keys):
            root.items.append(["text", Q(circuit.sheets[key]), ["at", 30 + 90 * i, 110, 0], eff(1.27, "left bottom"),
                               ["uuid", uid(name, "roottxt", key)]])
        root.items.append(["text", Q(circuit.title + " - hierarchical root. All inter-sheet nets are global labels."),
                           ["at", 30, 35, 0], eff(3, "left bottom"), ["uuid", uid(name, "roottitle")]])
        root.write(os.path.join(outdir, f"{name}.kicad_sch"), root=True, sheets=sheet_nodes)
    # project + library tables
    with open(os.path.join(outdir, f"{name}.kicad_pro"), "w") as f:
        json.dump({"meta": {"filename": f"{name}.kicad_pro", "version": 1},
                   "board": {"design_settings": {}}, "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
                   "sheets": [], "text_variables": {}}, f, indent=2)
    with open(os.path.join(outdir, "sym-lib-table"), "w") as f:
        f.write('(sym_lib_table\n  (lib (name "famaband")(type "KiCad")(uri "${KIPRJMOD}/../../lib/famaband.kicad_sym")(options "")(descr "FAMA-Band project symbols"))\n)\n')
    with open(os.path.join(outdir, "fp-lib-table"), "w") as f:
        f.write('(fp_lib_table\n  (lib (name "famaband")(type "KiCad")(uri "${KIPRJMOD}/../../lib/famaband.pretty")(options "")(descr "FAMA-Band project footprints"))\n)\n')


def check_netlist(circuit, outdir):
    """Export the netlist with kicad-cli and compare against the Circuit."""
    sch = os.path.join(outdir, f"{circuit.name}.kicad_sch")
    net_path = os.path.join(outdir, f"{circuit.name}.net")
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr", "-o", net_path, sch],
                   check=True, capture_output=True)
    tree = parse(open(net_path).read())
    got = {}
    for net in find_all(find(tree, "nets"), "net"):
        nodes = frozenset((find(n, "ref")[1], find(n, "pin")[1]) for n in find_all(net, "node")
                          if not find(n, "ref")[1].startswith("#"))
        if nodes:
            got[find(net, "name")[1]] = nodes
    want = {}
    for net, members in circuit.nets.items():
        nodes = frozenset((r, n) for r, n in members if not r.startswith("#"))
        if nodes:
            want[net] = nodes
    got_sets = {v: k for k, v in got.items()}
    problems = []
    for net, nodes in want.items():
        if nodes not in got_sets:
            close = [k for k, v in got.items() if v & nodes]
            problems.append(f"{net}: expected {sorted(nodes)} ; schematic nets touching it: {close}")
    extra = [k for k, v in got.items() if v not in set(want.values()) and len(v) > 1]
    for k in extra:
        problems.append(f"unexpected schematic net {k}: {sorted(got[k])}")
    return problems
