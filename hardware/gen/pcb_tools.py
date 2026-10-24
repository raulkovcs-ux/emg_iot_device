"""Board-building helpers on top of the KiCad 7 pcbnew Python API.

Workflow used by build_pcb.py:
  Board(circuit) -> place parts -> outline/zones -> route() via Freerouting
  -> fill zones -> DRC -> save + Gerber/drill/position/STEP export.
"""
import json
import math
import os
import re
import subprocess

import pcbnew

from sexpr import find, find_all, parse

HERE = os.path.dirname(os.path.abspath(__file__))
FP_DIR = "/usr/share/kicad/footprints"
PROJECT_FP = os.path.normpath(os.path.join(HERE, "..", "lib", "famaband.pretty"))
FREEROUTING = os.environ.get("FREEROUTING_JAR", os.path.join(HERE, "..", "tools", "freerouting-1.9.0.jar"))
ORIGIN = (100.0, 100.0)  # board (0,0) sits at this KiCad page coordinate

mm = pcbnew.FromMM


def V(x, y):
    return pcbnew.VECTOR2I(mm(ORIGIN[0] + x), mm(ORIGIN[1] + y))


class Board:
    def __init__(self, circuit, layers, width, height, corner=2.0):
        self.c = circuit
        self.w, self.h = width, height
        self.b = pcbnew.CreateEmptyBoard()
        self.b.SetCopperLayerCount(layers)
        self.layers = layers
        self.fps = {}
        self.nets = {}
        self.netclasses = {}
        ds = self.b.GetDesignSettings()
        ds.SetBoardThickness(mm(1.6))
        ds.m_TrackMinWidth = mm(0.127)
        ds.m_MinClearance = mm(0.127)
        ds.m_ViasMinSize = mm(0.45)
        ds.m_MinThroughDrill = mm(0.2)
        ds.m_CopperEdgeClearance = mm(0.3)
        ds.m_HoleClearance = mm(0.25)
        ds.m_HoleToHoleMin = mm(0.25)
        ds.m_SolderMaskMinWidth = mm(0.0)
        dflt = ds.m_NetSettings.m_DefaultNetClass
        # route with 0.2 mm, sign off at 0.15 mm (written to .kicad_pro): margin for router rounding
        dflt.SetClearance(mm(0.2)); dflt.SetTrackWidth(mm(0.15))
        dflt.SetViaDiameter(mm(0.45)); dflt.SetViaDrill(mm(0.2))
        self._outline(corner)
        for net in circuit.nets:
            n = pcbnew.NETINFO_ITEM(self.b, net)
            self.b.Add(n)
            self.nets[net] = n
        pin_net = circuit.pin_net()
        for ref, part in circuit.parts.items():
            if ref.startswith("#") or not part.footprint:
                continue
            lib, name = part.footprint.split(":")
            path = PROJECT_FP if lib == "famaband" else os.path.join(FP_DIR, lib + ".pretty")
            fp = pcbnew.FootprintLoad(path, name)
            if fp is None:
                raise RuntimeError(f"footprint {part.footprint} not found")
            fp.SetReference(ref)
            fp.SetValue(part.value)
            fp.SetFPID(pcbnew.LIB_ID(lib, name))
            if part.dnp:
                fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM)
            for pad in fp.Pads():
                net = pin_net.get((ref, pad.GetNumber()))
                if net:
                    pad.SetNet(self.nets[net])
            self.b.Add(fp)
            self.fps[ref] = fp

    # ------------------------------------------------------------ geometry
    def _seg(self, a, b, layer=pcbnew.Edge_Cuts, width=0.1):
        s = pcbnew.PCB_SHAPE(self.b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(*a)); s.SetEnd(V(*b))
        s.SetLayer(layer); s.SetWidth(mm(width))
        self.b.Add(s)

    def _arc(self, center, start, angle_deg, layer=pcbnew.Edge_Cuts, width=0.1):
        s = pcbnew.PCB_SHAPE(self.b)
        s.SetShape(pcbnew.SHAPE_T_ARC)
        s.SetCenter(V(*center)); s.SetStart(V(*start))
        s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(angle_deg, pcbnew.DEGREES_T), True)
        s.SetLayer(layer); s.SetWidth(mm(width))
        self.b.Add(s)

    def _outline(self, r):
        w, h = self.w, self.h
        self._seg((r, 0), (w - r, 0)); self._seg((w, r), (w, h - r))
        self._seg((w - r, h), (r, h)); self._seg((0, h - r), (0, r))
        self._arc((w - r, r), (w - r, 0), 90); self._arc((w - r, h - r), (w, h - r), 90)
        self._arc((r, h - r), (r, h), 90); self._arc((r, r), (0, r), 90)

    def outline_pts(self, inset=0.0):
        i = inset
        return [(i, i), (self.w - i, i), (self.w - i, self.h - i), (i, self.h - i)]

    # ------------------------------------------------------------ parts
    def part_by_nets(self, *nets, prefix=None):
        want = set(nets)
        pin_net = self.c.pin_net()
        hits = []
        for ref, p in self.c.parts.items():
            if ref.startswith("#") or (prefix and not ref.startswith(prefix)):
                continue
            got = {pin_net.get((ref, n)) for n in p.sym.pins}
            if got == want:
                hits.append(ref)
        return hits

    def place(self, ref, x, y, rot=0, side="F"):
        fp = self.fps[ref]
        fp.SetPosition(V(x, y))
        fp.SetOrientationDegrees(rot)
        if side == "B" and not fp.IsFlipped():
            fp.Flip(V(x, y), True)
        return fp

    def _signal_centroid(self, fp):
        pads = [p for p in fp.Pads() if p.GetNumber() not in ("", "MP", "S1", "11", "10") and p.GetNumber()[:1].isalnum()]
        xs = [p.GetPosition().x for p in pads]; ys = [p.GetPosition().y for p in pads]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def pad_bbox(self, fp):
        box = None
        for p in fp.Pads():
            bb = p.GetBoundingBox()
            box = bb if box is None else box.Merge(bb) or box
        return box

    def place_edge(self, ref, edge, along, side="F", overhang=0.0, inset=None):
        """Place a connector so its opening faces out of `edge` ('L','R','T','B').

        The opening is taken to be on the side of the body away from the
        signal pads (true for KiCad's JST, USB-C, microSD and slide-switch
        footprints). The courtyard/fab box is aligned to the board edge.
        """
        want = {"L": (-1, 0), "R": (1, 0), "T": (0, -1), "B": (0, 1)}[edge]
        for rot in (0, 90, 180, 270):
            fp = self.place(ref, 0, 0, rot, "F")
            if fp.IsFlipped():
                fp.Flip(V(0, 0), True)
                fp.SetOrientationDegrees(rot)
            if side == "B":
                fp.Flip(V(0, 0), True)
            cx, cy = self._signal_centroid(fp)
            bb = fp.GetBoundingBox(False, False)
            mx, my = bb.GetCenter().x - cx, bb.GetCenter().y - cy
            if (abs(mx) > abs(my) and want[1] == 0 and math.copysign(1, mx) == want[0]) or \
               (abs(my) >= abs(mx) and want[0] == 0 and math.copysign(1, my) == want[1]):
                break
        else:
            raise RuntimeError(f"cannot orient {ref}")
        bb = fp.GetBoundingBox(False, False)
        pos = fp.GetPosition()
        to_mm = lambda v: v / 1e6
        x0, y0 = to_mm(bb.GetLeft() - pos.x), to_mm(bb.GetTop() - pos.y)
        x1, y1 = to_mm(bb.GetRight() - pos.x), to_mm(bb.GetBottom() - pos.y)
        if edge == "L":
            x, y = -x0 - overhang, along
        elif edge == "R":
            x, y = self.w - x1 + overhang, along
        elif edge == "T":
            x, y = along, -y0 - overhang
        else:
            x, y = along, self.h - y1 + overhang
        fp.SetPosition(V(x, y))
        return fp

    def add_hole(self, ref, x, y, name="MountingHole_2.2mm_M2"):
        fp = pcbnew.FootprintLoad(os.path.join(FP_DIR, "MountingHole.pretty"), name)
        fp.SetFPID(pcbnew.LIB_ID("MountingHole", name))
        fp.SetReference(ref)
        fp.SetPosition(V(x, y))
        self.b.Add(fp)
        self.fps[ref] = fp

    def text(self, s, x, y, size=1.0, layer=pcbnew.F_SilkS, rot=0, mirror=False):
        t = pcbnew.PCB_TEXT(self.b)
        t.SetText(s); t.SetPosition(V(x, y)); t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); t.SetTextThickness(mm(size * 0.15))
        t.SetTextAngle(pcbnew.EDA_ANGLE(rot, pcbnew.DEGREES_T))
        if mirror:
            t.SetMirrored(True)
        self.b.Add(t)

    # ------------------------------------------------------------ nets/zones
    def netclass(self, name, track, clearance, nets, via=(0.45, 0.2)):
        nc = pcbnew.NETCLASS(name)
        nc.SetTrackWidth(mm(track)); nc.SetClearance(mm(clearance + 0.05))
        nc.SetViaDiameter(mm(via[0])); nc.SetViaDrill(mm(via[1]))
        self.b.GetDesignSettings().m_NetSettings.m_NetClasses[name] = nc
        for n in nets:
            self.nets[n].SetNetClass(nc)
        self.netclasses[name] = dict(track=track, clearance=clearance, via=via, nets=list(nets))

    def zone(self, net, layer, pts, priority=0, clearance=0.25, min_w=0.2, thermal=True, name=None, fill=True):
        z = pcbnew.ZONE(self.b)
        z.SetLayer(layer)
        if net:
            z.SetNet(self.nets[net])
        o = z.Outline()
        o.NewOutline()
        for x, y in pts:
            o.Append(mm(ORIGIN[0] + x), mm(ORIGIN[1] + y))
        z.SetAssignedPriority(priority)
        z.SetLocalClearance(mm(clearance))
        z.SetMinThickness(mm(min_w))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL if thermal else pcbnew.ZONE_CONNECTION_FULL)
        z.SetThermalReliefGap(mm(0.25))
        z.SetThermalReliefSpokeWidth(mm(0.3))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        if name:
            z.SetZoneName(name)
        self.b.Add(z)
        return z

    def track(self, net, layer, pts, width=0.25):
        for a, b in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(V(*a)); t.SetEnd(V(*b)); t.SetWidth(mm(width)); t.SetLayer(layer)
            t.SetNet(self.nets[net])
            self.b.Add(t)

    def pad_xy(self, ref, num):
        p = [p for p in self.fps[ref].Pads() if p.GetNumber() == str(num)][0].GetPosition()
        return p.x / 1e6 - ORIGIN[0], p.y / 1e6 - ORIGIN[1]

    def via(self, net, x, y, dia=0.6, drill=0.3):
        """Pre-placed via (e.g. to tie a zone to its net); the router connects to it."""
        v = pcbnew.PCB_VIA(self.b)
        v.SetPosition(V(x, y)); v.SetWidth(mm(dia)); v.SetDrill(mm(drill))
        v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetNet(self.nets[net])
        self.b.Add(v)
        return v

    def keepout(self, layers, pts, tracks=True, vias=True, pour=True):
        z = pcbnew.ZONE(self.b)
        z.SetIsRuleArea(True)
        ls = pcbnew.LSET()
        for l in layers:
            ls.AddLayer(l)
        z.SetLayerSet(ls)
        z.SetDoNotAllowTracks(tracks); z.SetDoNotAllowVias(vias); z.SetDoNotAllowCopperPour(pour)
        z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False)
        o = z.Outline(); o.NewOutline()
        for x, y in pts:
            o.Append(mm(ORIGIN[0] + x), mm(ORIGIN[1] + y))
        self.b.Add(z)

    def fill(self):
        filler = pcbnew.ZONE_FILLER(self.b)
        filler.Fill(self.b.Zones())

    # ------------------------------------------------------------ fan-out
    def fanout(self, net="GND", via=(0.45, 0.2), track=0.25, clearance=0.17):
        """Drop a via next to every SMD pad of `net` so it reaches the inner plane.
        Freerouting does not create plane connections itself. Returns (added, failed refs)."""
        ni = self.nets[net]
        r = via[0] / 2
        blockers = []  # (bbox inflated, net code)
        for fp in self.b.GetFootprints():
            for p in fp.Pads():
                blockers.append((p.GetBoundingBox(), p.GetNetCode(), p))
        rule_areas = [z for fp in self.b.GetFootprints() for z in fp.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
        rule_areas += [z for z in self.b.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
        vias = []
        added, failed = 0, []
        edge = self.b.GetBoardEdgesBoundingBox()

        def free(x, y, own_pad, seg_from):
            pt = pcbnew.VECTOR2I(int(x), int(y))
            if not edge.Contains(pt):
                return False
            if (x - edge.GetLeft() < mm(0.6) or edge.GetRight() - x < mm(0.6) or
                    y - edge.GetTop() < mm(0.6) or edge.GetBottom() - y < mm(0.6)):
                return False
            for z in rule_areas:
                m = int(r + mm(0.15))
                for ox, oy in ((0, 0), (m, 0), (-m, 0), (0, m), (0, -m)):
                    if z.Outline().Contains(pcbnew.VECTOR2I(int(x) + ox, int(y) + oy)):
                        return False
                mx, my = (x + seg_from[0]) / 2, (y + seg_from[1]) / 2
                if z.Outline().Contains(pcbnew.VECTOR2I(int(mx), int(my))):
                    return False
            for vx, vy in vias:
                if math.hypot(vx - x, vy - y) < mm(via[0] + clearance):
                    return False
            for bb, code, p in blockers:
                if p is own_pad:
                    continue
                infl = mm(clearance + r) if code != ni.GetNetCode() else mm(0.05 + r)
                if (bb.GetLeft() - infl < x < bb.GetRight() + infl) and (bb.GetTop() - infl < y < bb.GetBottom() + infl):
                    return False
                # track from pad centre to via must clear other-net pads
                if code != ni.GetNetCode():
                    fx, fy = seg_from
                    for k in range(1, 8):
                        sx, sy = fx + (x - fx) * k / 8, fy + (y - fy) * k / 8
                        t = mm(clearance + track / 2)
                        if (bb.GetLeft() - t < sx < bb.GetRight() + t) and (bb.GetTop() - t < sy < bb.GetBottom() + t):
                            return False
            return True

        for fp in self.b.GetFootprints():
            c = fp.GetPosition()
            pth = {p.GetNumber() for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH}
            for p in fp.Pads():
                if p.GetNetCode() != ni.GetNetCode() or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                    continue
                if p.GetNumber() in pth:  # e.g. module EP with built-in thermal vias
                    continue
                pc = p.GetPosition()
                dx, dy = pc.x - c.x, pc.y - c.y
                if abs(dx) < 1 and abs(dy) < 1:
                    dx, dy = 1, 0
                base = math.atan2(dy, dx)
                half = max(p.GetSize().x, p.GetSize().y) / 2
                done = False
                for dist in (0.55, 0.75, 1.0, 1.3, 1.7):
                    for da in (0, 30, -30, 60, -60, 90, -90, 135, -135, 180):
                        a = base + math.radians(da)
                        x = pc.x + math.cos(a) * (half + mm(dist))
                        y = pc.y + math.sin(a) * (half + mm(dist))
                        if free(x, y, p, (pc.x, pc.y)):
                            v = pcbnew.PCB_VIA(self.b)
                            v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
                            v.SetWidth(mm(via[0])); v.SetDrill(mm(via[1]))
                            v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                            v.SetNet(ni)
                            self.b.Add(v)
                            t = pcbnew.PCB_TRACK(self.b)
                            t.SetStart(pc); t.SetEnd(pcbnew.VECTOR2I(int(x), int(y)))
                            t.SetWidth(mm(track) if min(p.GetSize().x, p.GetSize().y) >= mm(0.4) else mm(0.2))
                            t.SetLayer(pcbnew.F_Cu if p.IsOnLayer(pcbnew.F_Cu) else pcbnew.B_Cu)
                            t.SetNet(ni)
                            self.b.Add(t)
                            vias.append((x, y))
                            blockers.append((v.GetBoundingBox(), ni.GetNetCode(), v))
                            added += 1
                            done = True
                            break
                    if done:
                        break
                if not done:
                    failed.append(f"{fp.GetReference()}.{p.GetNumber()}")
        return added, failed

    def silk_refs_to_fab(self):
        """Dense board: reference designators live on the Fab layers (assembly drawing)."""
        for fp in self.b.GetFootprints():
            fp.Reference().SetVisible(False)

    def placement_report(self, outdir):
        """Fast pre-route check: save, run DRC, return courtyard / keep-out problems."""
        path = self.save(outdir)
        b = pcbnew.LoadBoard(path)
        rpt = os.path.join(outdir, "placement_drc.rpt")
        pcbnew.WriteDRCReport(b, rpt, pcbnew.EDA_UNITS_MILLIMETRES, False)
        txt = open(rpt).read()
        out = []
        for blk in re.split(r"\n(?=\[)", txt):
            if blk.startswith(("[courtyards_overlap]", "[items_not_allowed]", "[copper_edge_clearance]")):
                refs = re.findall(r"(?:Footprint|of) (\S+)", blk)
                out.append(blk.split("]")[0][1:] + ": " + " / ".join(refs))
        return out

    # ------------------------------------------------------------ routing
    def route(self, workdir, passes=40, threads=1, timeout=1800):
        os.makedirs(workdir, exist_ok=True)
        dsn = os.path.join(workdir, f"{self.c.name}.dsn")
        ses = os.path.join(workdir, f"{self.c.name}.ses")
        if not pcbnew.ExportSpecctraDSN(self.b, dsn):
            raise RuntimeError("DSN export failed")
        if os.path.exists(ses):
            os.remove(ses)
        cmd = ["xvfb-run", "-a", "java", "-Djava.awt.headless=false", "-jar", FREEROUTING,
               "-de", dsn, "-do", ses, "-mp", str(passes), "-mt", str(threads), "-dct", "0"]
        log = open(os.path.join(workdir, "freerouting.log"), "w")
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        if not os.path.exists(ses):
            raise RuntimeError("Freerouting produced no session file; see freerouting.log")
        return self.import_ses(ses, dsn)

    def import_ses(self, ses_path, dsn_path):
        """Import tracks/vias from a Specctra session (replacement for ImportSpecctraSES,
        which only works inside the KiCad GUI)."""
        dsn = parse(open(dsn_path).read())
        ses = parse(open(ses_path).read())
        routes = find(ses, "routes")
        r = find(routes, "resolution")
        unit, scale = r[1], float(r[2])
        to_mm = {"um": 1e-3, "mm": 1.0, "mil": 0.0254, "inch": 25.4}[unit] / scale
        layer_ids = {self.b.GetLayerName(l): l for l in range(pcbnew.PCB_LAYER_ID_COUNT)}
        # via padstacks in the session/DSN: "Via[0-3]_450:200_um"
        n_tracks = n_vias = 0
        existing = [(v.GetPosition().x, v.GetPosition().y) for v in self.b.GetTracks() if v.Type() == pcbnew.PCB_VIA_T]
        for net in find_all(find(routes, "network_out"), "net"):
            name = net[1]
            ni = self.nets.get(name)
            for wire in find_all(net, "wire"):
                path = find(wire, "path")
                if path is None:
                    continue
                layer = layer_ids[path[1]]
                width = float(path[2]) * to_mm
                coords = [float(v) * to_mm for v in path[3:]]
                pts = [(coords[i], -coords[i + 1]) for i in range(0, len(coords), 2)]
                for a, b in zip(pts, pts[1:]):
                    t = pcbnew.PCB_TRACK(self.b)
                    t.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
                    t.SetEnd(pcbnew.VECTOR2I(mm(b[0]), mm(b[1])))
                    t.SetWidth(mm(width)); t.SetLayer(layer)
                    if ni:
                        t.SetNet(ni)
                    self.b.Add(t)
                    n_tracks += 1
            for via in find_all(net, "via"):
                m = re.search(r"(\d+):(\d+)_um", via[1])
                dia, drill = (int(m.group(1)) / 1000, int(m.group(2)) / 1000) if m else (0.45, 0.2)
                pos = pcbnew.VECTOR2I(mm(float(via[2]) * to_mm), mm(-float(via[3]) * to_mm))
                if any(abs(pos.x - ex) < 5000 and abs(pos.y - ey) < 5000 for ex, ey in existing):
                    continue  # our own pre-placed via echoed back by the router
                v = pcbnew.PCB_VIA(self.b)
                v.SetPosition(pos)
                v.SetWidth(mm(dia)); v.SetDrill(mm(drill))
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                if ni:
                    v.SetNet(ni)
                self.b.Add(v)
                n_vias += 1
        return n_tracks, n_vias

    # ------------------------------------------------------------ checks/output
    def unrouted(self):
        self.b.BuildConnectivity()
        return self.b.GetConnectivity().GetUnconnectedCount(False)

    def drc(self, path):
        pcbnew.WriteDRCReport(self.b, path, pcbnew.EDA_UNITS_MILLIMETRES, True)
        txt = open(path).read()
        viol = re.search(r"\*\* Found (\d+) DRC violations", txt)
        unc = re.search(r"\*\* Found (\d+) unconnected pads", txt)
        return int(viol.group(1)) if viol else -1, int(unc.group(1)) if unc else -1, txt

    def save(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        # sign-off clearances (routing used +0.05 mm margin)
        ns = self.b.GetDesignSettings().m_NetSettings
        ns.m_DefaultNetClass.SetClearance(mm(0.15))
        for name, nc in self.netclasses.items():
            ns.m_NetClasses[name].SetClearance(mm(nc["clearance"]))
        path = os.path.join(outdir, f"{self.c.name}.kicad_pcb")
        self.b.Save(path)
        # persist net classes in the project (KiCad 7 keeps them in .kicad_pro)
        pro = os.path.join(outdir, f"{self.c.name}.kicad_pro")
        data = json.load(open(pro)) if os.path.exists(pro) else {"meta": {"filename": os.path.basename(pro), "version": 1}}
        classes = [{"name": "Default", "clearance": 0.15, "track_width": 0.15, "via_diameter": 0.45, "via_drill": 0.2,
                    "diff_pair_gap": 0.15, "diff_pair_width": 0.2, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                    "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
                    "bus_width": 12, "line_style": 0, "wire_width": 6}]
        patterns = []
        for name, nc in self.netclasses.items():
            classes.append(dict(classes[0], name=name, clearance=nc["clearance"], track_width=nc["track"],
                                via_diameter=nc["via"][0], via_drill=nc["via"][1]))
            patterns += [{"netclass": name, "pattern": n} for n in nc["nets"]]
        data["net_settings"] = {"classes": classes, "meta": {"version": 3}, "net_colors": None,
                                "netclass_assignments": None, "netclass_patterns": patterns}
        json.dump(data, open(pro, "w"), indent=2)
        with open(os.path.join(outdir, f"{self.c.name}.kicad_dru"), "w") as f:
            f.write('(version 1)\n'
                    '(rule "thermal spokes"\n  (constraint min_resolved_spokes 1))\n')
        return path


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def stitch(board, net="GND", pitch=2.5, via=(0.45, 0.2), clearance=0.3, edge_margin=1.0, corner=4.0):
    """Ground-stitching vias on a grid wherever nothing else is nearby (post-route).
    Ties the outer-layer pours to the inner plane and removes isolated pour islands."""
    ni = board.FindNet(net)
    r = mm(via[0] / 2)
    edge = board.GetBoardEdgesBoundingBox()
    items = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            bb = p.GetBoundingBox()
            items.append(("box", bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()))
        cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
        for z in fp.Zones():
            if z.GetIsRuleArea():
                items.append(("area", z))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            items.append(("circle", t.GetPosition().x, t.GetPosition().y, t.GetWidth() / 2))
        else:
            items.append(("seg", t.GetStart().x, t.GetStart().y, t.GetEnd().x, t.GetEnd().y, t.GetWidth() / 2))
    for z in board.Zones():
        if z.GetIsRuleArea():
            items.append(("area", z))
    cl = mm(clearance)
    added = 0
    step = mm(pitch)
    y = edge.GetTop() + mm(edge_margin)
    while y < edge.GetBottom() - mm(edge_margin):
        x = edge.GetLeft() + mm(edge_margin)
        while x < edge.GetRight() - mm(edge_margin):
            near_x = min(x - edge.GetLeft(), edge.GetRight() - x) < mm(corner)
            near_y = min(y - edge.GetTop(), edge.GetBottom() - y) < mm(corner)
            ok = not (near_x and near_y)  # rounded board corners
            for it in items:
                k = it[0]
                if k == "box":
                    if it[1] - cl - r < x < it[3] + cl + r and it[2] - cl - r < y < it[4] + cl + r:
                        ok = False
                elif k == "circle":
                    if math.hypot(x - it[1], y - it[2]) < it[3] + r + cl:
                        ok = False
                elif k == "seg":
                    if _seg_dist(x, y, it[1], it[2], it[3], it[4]) < it[5] + r + cl:
                        ok = False
                elif k == "area":
                    if it[1].Outline().Contains(pcbnew.VECTOR2I(int(x), int(y))):
                        ok = False
                if not ok:
                    break
            if ok:
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
                v.SetWidth(mm(via[0])); v.SetDrill(mm(via[1]))
                v.SetViaType(pcbnew.VIATYPE_THROUGH); v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                v.SetNet(ni)
                board.Add(v)
                items.append(("circle", x, y, r))
                added += 1
            x += step
        y += step
    return added


def unconnected_nets(rpt_path):
    txt = open(rpt_path).read()
    nets = set()
    for blk in re.split(r"\n(?=\[)", txt):
        if blk.startswith("[unconnected_items]"):
            for n in re.findall(r"(?:Track|Pad \S+|Via) \[([^\]]+)\]", blk):
                nets.add(n)
    return nets


def reroute_saved(pcb_path, workdir, passes=60):
    """Second Freerouting pass on a saved, already routed board: Freerouting keeps the
    existing wiring and completes the missing connections; all wiring is re-imported."""
    b = pcbnew.LoadBoard(pcb_path)
    dsn = os.path.join(workdir, "repair.dsn"); ses = os.path.join(workdir, "repair.ses")
    for path in (dsn, ses):
        if os.path.exists(path):
            os.remove(path)
    pcbnew.ExportSpecctraDSN(b, dsn)
    subprocess.run(["xvfb-run", "-a", "java", "-jar", FREEROUTING, "-de", dsn, "-do", ses, "-mp", str(passes),
                    "-mt", "1", "-dct", "0"], stdout=open(os.path.join(workdir, "repair.log"), "w"),
                   stderr=subprocess.STDOUT, timeout=3600, check=False)
    if not os.path.exists(ses):
        raise RuntimeError("repair pass produced no session")
    for t in list(b.GetTracks()):
        b.Remove(t)
    shim = Board.__new__(Board)
    shim.b = b
    shim.nets = {ni.GetNetname(): ni for ni in b.GetNetsByNetcode().values()}
    n = shim.import_ses(ses, dsn)
    b.Save(pcb_path)
    return n


def finalize(pcb_path, rpt_path, stitch_net=None):
    """Reload the saved board (sets up the project context the zone filler needs),
    fill zones, run DRC and save again. Returns (violations by type, unconnected)."""
    b = pcbnew.LoadBoard(pcb_path)
    if stitch_net:
        stitch(b, stitch_net)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.WriteDRCReport(b, rpt_path, pcbnew.EDA_UNITS_MILLIMETRES, True)
    b.Save(pcb_path)
    txt = open(rpt_path).read()
    counts = {}
    section = None
    for line in txt.splitlines():
        if line.startswith("** Found") and "unconnected" in line:
            section = "unconnected"
        elif line.startswith("** Found") and "DRC" in line:
            section = "drc"
        m = re.match(r"\[(\w+)\]", line)
        if m:
            key = m.group(1) if section == "drc" else "unconnected:" + m.group(1)
            counts[key] = counts.get(key, 0) + 1
    unc = re.search(r"\*\* Found (\d+) unconnected pads", txt)
    return counts, int(unc.group(1)) if unc else -1


def export_fab(pcb_path, outdir, layers):
    os.makedirs(outdir, exist_ok=True)
    cu = ["F.Cu", "B.Cu"] + [f"In{i}.Cu" for i in range(1, layers - 1)]
    lay = ",".join(cu + ["F.Paste", "B.Paste", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "Edge.Cuts"])
    run = lambda *a: subprocess.run(["kicad-cli", "pcb", "export", *a], check=True, capture_output=True, text=True)
    gdir = os.path.join(outdir, "gerbers")
    os.makedirs(gdir, exist_ok=True)
    run("gerbers", "--layers", lay, "--subtract-soldermask", "--use-drill-file-origin", "-o", gdir + "/", pcb_path)
    run("drill", "--format", "excellon", "--excellon-separate-th", "--generate-map", "--map-format", "pdf", "-o", gdir + "/", pcb_path)
    stem = os.path.splitext(os.path.basename(pcb_path))[0]
    run("pos", "--side", "both", "--format", "csv", "--units", "mm", "-o", os.path.join(outdir, f"{stem}_cpl.csv"), pcb_path)
    run("step", "--subst-models", "--force", "-o", os.path.join(outdir, f"{stem}.step"), pcb_path)
    import shutil
    shutil.make_archive(os.path.join(outdir, f"{stem}_gerbers"), "zip", gdir)
