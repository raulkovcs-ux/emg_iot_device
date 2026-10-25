"""Place, autoroute, fill, DRC and export the hub and pod PCBs.

    python3 hardware/gen/build_pcb.py [pod|hub]

Requires KiCad 7 (pcbnew Python module, kicad-cli), Java 17+, xvfb-run and the
Freerouting 1.9 jar in hardware/tools/ (see hardware/tools/get_freerouting.sh).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ.setdefault("KICAD7_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
import pcbnew  # noqa: E402

import hub  # noqa: E402
import pod  # noqa: E402
from pcb_tools import Board, export_fab, finalize, remove_dangling_stubs, reroute_saved, trim_lanes, unconnected_nets  # noqa: E402

HW = os.path.normpath(os.path.join(HERE, ".."))
F, B = pcbnew.F_Cu, pcbnew.B_Cu


def finish(bd, layers, gnd_layers, stitch_net=None, repair=False):
    for layer in gnd_layers:
        bd.zone("GND", layer, bd.outline_pts(0.0), priority=0, name=f"GND_{bd.b.GetLayerName(layer)}")
    outdir = os.path.join(HW, "kicad", bd.c.name)
    path = bd.save(outdir)
    rpt = os.path.join(outdir, f"{bd.c.name}_drc.rpt")
    counts, unc = finalize(path, rpt)
    missing = unconnected_nets(rpt) - {"GND"}
    if repair and missing:
        print(f"[{bd.c.name}] unrouted after pass 1: {sorted(missing)} -> repair pass")
        nt, nv = reroute_saved(path, os.path.join(HW, "build", bd.c.name))
        print(f"[{bd.c.name}] repair pass: {nt} track segments, {nv} vias")
    counts, unc = finalize(path, rpt, stitch_net=stitch_net)
    print(f"[{bd.c.name}] DRC: {sum(v for k, v in counts.items() if not k.startswith('unconnected'))} violations {counts}, unconnected pads: {unc}")
    export_fab(path, os.path.join(HW, "fab", bd.c.name), layers)
    return counts, unc


# =============================================================== POD (2 layers)
def build_pod():
    c = pod.build()
    bd = Board(c, 2, 16.0, 34.0, corner=2.0)
    # skin side (bottom): electrodes 20 mm apart along the forearm axis
    bd.place("E1", 8, 7, 0, "B")
    bd.place("E2", 8, 27, 0, "B")
    # bootstrapped guards on the skin side, under solder mask, driven by the buffer outputs
    bd.zone("BUF_P", B, [(0.4, 0.4), (15.6, 0.4), (15.6, 13.6), (0.4, 13.6)], priority=2, clearance=0.3, name="GUARD_P")
    bd.zone("BUF_N", B, [(0.4, 20.4), (15.6, 20.4), (15.6, 33.6), (0.4, 33.6)], priority=2, clearance=0.3, name="GUARD_N")
    bd.zone("GND", B, [(0.4, 14.0), (15.6, 14.0), (15.6, 20.0), (0.4, 20.0)], priority=1, name="GND_B_mid")
    bd.via("GND", 15.0, 18.6)
    # component side
    bd.place_edge("J1", "L", 17.0)
    bd.fps["J1"].Reference().SetVisible(False)
    bd.place("U1", 10.6, 17.0, 90)
    bd.place("R1", 13.6, 11.0, 90)   # ELEC_P -> IN_P
    bd.place("R2", 13.6, 23.0, 90)   # ELEC_N -> IN_N
    bd.place("R3", 4.0, 10.0, 0)     # BUF_P -> OUT_P
    bd.place("R4", 4.0, 24.0, 0)     # BUF_N -> OUT_N
    bd.place("C1", 8.6, 11.0, 90)
    bd.place("C2", 8.6, 23.0, 90)
    # guard rings: hard-wire each guard via to the buffer side of its series resistor
    for ref, net in (("R3", "BUF_P"), ("R4", "BUF_N")):
        x, y = bd.pad_xy(ref, 1)
        bd.via(net, x - 1.2, y)
        bd.track(net, F, [(x, y), (x - 1.2, y)])
    bd.text("FAMA-Band pod A", 8, 3.0, 0.8)
    bd.text("+", 2.0, 7, 1.2, pcbnew.B_SilkS, mirror=True)
    bd.text("-", 2.0, 27, 1.2, pcbnew.B_SilkS, mirror=True)
    bd.text("SKIN SIDE  ENIG", 8, 17, 0.8, pcbnew.B_SilkS, mirror=True)
    bd.netclass("Power", 0.3, 0.15, ["+3V0", "GND"])
    bd.silk_refs_to_fab()
    nt, nv = bd.route(os.path.join(HW, "build", "pod"), passes=30)
    print(f"[pod] routed: {nt} track segments, {nv} vias")
    return finish(bd, 2, [F])


# =============================================================== HUB (4 layers)
def build_hub():
    c = hub.build()
    W, H = 66.0, 46.0
    bd = Board(c, 4, W, H, corner=3.0)
    bd.b.SetLayerName(pcbnew.In1_Cu, "In1.Cu")
    pn = c.pin_net()

    def one(*nets, prefix=None):
        hits = bd.part_by_nets(*nets, prefix=prefix)
        assert hits, nets
        return hits

    # ---- electrode connectors: all on the left (band) edge, both board sides,
    # interleaved so the eight channels reach the ADS1298 in pin order (ch8 on top)
    slots = [6.0, 15.6, 25.2, 34.8]
    jpod = {int(p.value[3:]): r for r, p in c.parts.items() if p.value.startswith("POD")}
    for i, ch in enumerate((8, 6, 4, 2)):
        bd.place_edge(jpod[ch], "L", slots[i], "F")
    for i, ch in enumerate((7, 5, 3, 1)):
        bd.place_edge(jpod[ch], "L", slots[i] + 3.8, "B")

    # ---- per-channel EMI filter rows, top to bottom ch8 ... ch1
    for i, ch in enumerate(range(8, 0, -1)):
        y = 4.6 + i * 4.15
        rp = one(f"POD{ch}_P", f"CH{ch}_P")[0]; rn = one(f"POD{ch}_N", f"CH{ch}_N")[0]
        cp, cn = [r for r in one(f"CH{ch}_P", "GND") if r.startswith("C")][0], one(f"CH{ch}_N", "GND")[0]
        cd = one(f"CH{ch}_P", f"CH{ch}_N")[0]
        bd.place(rp, 8.4, y, 0); bd.place(rn, 8.4, y + 1.3, 0)
        bd.place(cp, 10.5, y, 0); bd.place(cn, 10.5, y + 1.3, 0)
        bd.place(cd, 12.4, y + 0.65, 90)

    # ---- ADS1298 and its local parts
    ads = [r for r, p in c.parts.items() if p.value == "ADS1298IPAG"][0]
    bd.place(ads, 21.0, 19.0, 0)
    top = [r for r in one("+3V0", "GND")[:3]] + [r for r in one("+3V3", "GND") if c.parts[r].sheet == "afe"]
    vcap = {n: one(n, "GND")[0] for n in ("VCAP1", "VCAP2", "VCAP3", "VCAP4")}
    vref = one("VREFP", "GND")
    afe_avdd = [r for r in one("+3V0", "GND") if c.parts[r].sheet == "afe"]
    upper = afe_avdd[:3] + [r for r in one("+3V3", "GND") if c.parts[r].sheet == "afe" and c.parts[r].value in ("100nF", "1uF")][:3] + [vcap["VCAP3"]]
    for i, r in enumerate(upper):
        bd.place(r, 15.6 + i * 1.6, 10.6, 90)
    lower = vref + [vcap["VCAP1"], vcap["VCAP2"], vcap["VCAP4"]] + afe_avdd[3:]
    for i, r in enumerate(lower):
        bd.place(r, 15.4 + i * 1.75, 27.6, 90)
    osc_cap = [r for r in one("+3V3", "GND") if c.parts[r].sheet == "afe" and r not in upper][0]
    rld_r = one("RLD_INV", "RLD_OUT", prefix="R")[0]; rld_c = one("RLD_INV", "RLD_OUT", prefix="C")[0]
    bd.place(rld_r, 22.0, 7.4, 0); bd.place(rld_c, 25.0, 7.4, 0)
    for i, net in enumerate(("AFE_CS", "AFE_PWDN", "AFE_RESET")):
        bd.place(one("+3V3", net)[0], 29.0, 14.0 + i * 1.3, 0)
    xo = [r for r, p in c.parts.items() if p.ref.startswith("X")][0]
    bd.place(xo, 29.4, 21.4, 0); bd.place(one("OSC_OUT", "AFE_CLK")[0], 29.0, 18.4, 0); bd.place(osc_cap, 31.8, 21.4, 90)
    # bias electrode
    jbias = [r for r, p in c.parts.items() if p.value == "BIAS"][0]
    bd.place_edge(jbias, "B", 9.0)
    bd.place(one("RLD_OUT", "BIAS_ELEC")[0], 9.6, 38.4, 0)
    # AFE supply (LDO + ferrite + interlock)
    ldo = [r for r, p in c.parts.items() if p.value == "LP5907-3.0"][0]
    bd.place(ldo, 21.0, 36.0, 0)
    fb = [r for r in c.parts if r.startswith("FB")][0]
    bd.place(fb, 16.5, 36.0, 90)
    bd.place(one("AFE_LDO_IN", "GND")[0], 18.4, 36.0, 90)
    pwr_caps = [r for r in one("+3V0", "GND") if c.parts[r].sheet == "power"]
    bd.place(pwr_caps[0], 24.2, 35.4, 90); bd.place(pwr_caps[1], 25.8, 35.4, 90)
    bd.place(one("AFE_EN", "GND", prefix="R")[0], 21.0, 39.2, 0)
    bd.place(one("AFE_EN_MCU", "AFE_EN")[0], 24.0, 39.2, 0)
    q1 = [r for r, p in c.parts.items() if r.startswith("Q")][0]
    bd.place(q1, 27.0, 39.6, 0)
    bd.place(one("VBUS", "VBUS_DET")[0], 27.5, 42.6, 0)
    bd.place(one("VBUS_DET", "GND")[0], 24.5, 42.6, 0)

    # ---- ESP32-S3 module, antenna hanging over the right board edge
    mod = [r for r, p in c.parts.items() if p.value.startswith("ESP32")][0]
    bd.place(mod, W - 6.75, 13.0, 270)
    # +3V3 decoupling on the MCU sheet, in creation order: module (2), IMU (2), microSD (2)
    mcu_sheet_caps = [r for r in one("+3V3", "GND") if c.parts[r].sheet == "mcu"]
    mcu_caps, imu_caps, sd_caps = mcu_sheet_caps[0:2], mcu_sheet_caps[2:4], mcu_sheet_caps[4:6]
    bd.place(mcu_caps[0], W - 4.2, 2.0, 0); bd.place(mcu_caps[1], W - 7.2, 2.0, 0)
    bd.place(one("+3V3", "ESP_EN")[0], 41.0, 6.0, 90); bd.place(one("ESP_EN", "GND", prefix="C")[0], 42.6, 6.0, 90)
    sws = [r for r, p in c.parts.items() if p.value in ("RESET", "BOOT/USER")]
    bd.place(sws[0], 36.4, 3.2, 0); bd.place(sws[1], 36.4, 8.2, 0)
    bd.place(one("+3V3", "BOOT")[0], 41.0, 9.5, 90)
    leds = [r for r, p in c.parts.items() if p.value in ("green", "blue")]
    bd.place(leds[0], 33.0, 12.8, 0); bd.place(one("LED_G", "LEDG_A")[0], 36.0, 12.8, 0)
    bd.place(leds[1], 33.0, 14.6, 0); bd.place(one("LED_B", "LEDB_A")[0], 36.0, 14.6, 0)
    bd.place(one("+3V3", "I2C_SDA")[0], 39.5, 14.0, 90); bd.place(one("+3V3", "I2C_SCL")[0], 41.0, 14.0, 90)
    # IMU near the centre of the board
    imu = [r for r, p in c.parts.items() if p.value == "LSM6DSL"][0]
    bd.place(imu, 36.0, 20.0, 0)
    bd.place(imu_caps[0], 38.4, 18.3, 90); bd.place(imu_caps[1], 38.4, 20.3, 90)
    bd.fps[imu].SetLocalClearance(pcbnew.FromMM(0.1))  # 0.5 mm pitch LGA

    # ---- microSD at the bottom edge (card inserted from the wrist side)
    sd = [r for r, p in c.parts.items() if p.value == "microSD"][0]
    bd.place_edge(sd, "B", 37.0, overhang=0.6)
    sd_pu = [one("+3V3", n)[0] for n in ("SD_CMD", "SD_D0", "SD_D1", "SD_D2", "SD_D3", "SD_DET")]
    for i, r in enumerate(sd_pu):
        bd.place(r, 33.0 + i * 1.4, 22.8, 90)
    bd.place(sd_caps[0], 41.6, 22.4, 90); bd.place(sd_caps[1], 43.0, 22.4, 90)

    # ---- USB-C, ESD, fuse, CC resistors
    usb = [r for r, p in c.parts.items() if p.value == "USB-C"][0]
    bd.place_edge(usb, "B", 50.6, overhang=0.8)
    esd = [r for r, p in c.parts.items() if p.value == "USBLC6-2SC6"][0]
    bd.place(esd, 52.4, 35.0, 0)
    bd.place(one("CC1", "GND")[0], 46.6, 36.6, 0); bd.place(one("CC2", "GND")[0], 55.5, 36.6, 0)
    fuse = [r for r in c.parts if r.startswith("F") and not r.startswith("FB")][0]
    bd.place(fuse, 48.2, 34.4, 0)

    # ---- power switch at the bottom edge, battery connector on the skin side
    sw = [r for r, p in c.parts.items() if p.value == "POWER"][0]
    bd.place_edge(sw, "B", 60.4, overhang=0.6)
    jb = [r for r, p in c.parts.items() if p.value.startswith("BATT")][0]
    bd.place(jb, 59.0, 38.0, 180, "B")

    # ---- buck-boost (3V3)
    bb = [r for r, p in c.parts.items() if p.value == "TPS63001"][0]
    bd.place(bb, 49.2, 26.4, 0)
    l1 = [r for r in c.parts if r.startswith("L")][0]
    bd.place(l1, 53.5, 26.4, 0)
    vsys_caps = one("VSYS", "GND")
    bd.place(vsys_caps[0], 45.9, 24.6, 90); bd.place(vsys_caps[1], 45.9, 28.2, 90)
    p3_caps = [r for r in one("+3V3", "GND") if c.parts[r].sheet == "power"]
    for i, r in enumerate(p3_caps):
        bd.place(r, 48.4 + i * 2.0, 30.8, 90)
    bd.place(one("VSYS", "BB_VINA")[0], 45.9, 30.9, 90); bd.place(one("BB_VINA", "GND")[0], 45.9, 32.9, 90)

    # ---- charger + LED, fuel gauge
    chg = [r for r, p in c.parts.items() if p.value == "MCP73831-2"][0]
    bd.place(chg, 60.0, 25.0, 0)
    bd.place(one("CHG_PROG", "GND")[0], 60.0, 27.8, 0)
    bd.place(one("VBUS", "GND")[0], 57.2, 24.8, 90); bd.place(one("+BATT", "GND")[0], 62.8, 24.8, 90)
    bd.place(one("VBUS", "CHG_LED_A")[0], 64.4, 28.4, 90)
    bd.place([r for r, p in c.parts.items() if p.value == "CHG red"][0], 64.4, 31.4, 90)
    fg = [r for r, p in c.parts.items() if p.value == "LC709203F"][0]
    bd.place(fg, 59.0, 31.4, 0)
    fgc = [r for r in one("+BATT", "GND") if c.parts[r].value == "100nF"][0]
    bd.place(fgc, 55.6, 30.6, 90)
    bd.place(one("FG_TSW", "FG_TSENSE")[0], 58.6, 34.6, 0)
    bd.place([r for r in c.parts if r.startswith("TH")][0], 61.6, 34.6, 0)
    bd.place(one("+3V3", "FG_ALRT")[0], 55.6, 33.2, 90)

    # ---- mechanics, keep-outs, labels
    bd.add_hole("H1", 15.0, 43.3); bd.add_hole("H2", 31.0, 2.6); bd.add_hole("H3", 42.0, 2.6)
    # antenna: no copper under the part of the module that is still over the board
    bd.keepout([F, pcbnew.In1_Cu, pcbnew.In2_Cu, B], [(W - 0.8, 3.0), (W, 3.0), (W, 23.0), (W - 0.8, 23.0)])
    bd.text("FAMA-Band hub rev A", 28.0, 30.0, 0.8, pcbnew.B_SilkS, mirror=True)
    bd.text("DO NOT WEAR WHILE USB CONNECTED", 40.0, 20.0, 0.8, pcbnew.B_SilkS, mirror=True)
    for ch, y in zip((8, 6, 4, 2), slots):
        bd.text(f"{ch}", 7.0, y - 2.6, 0.8)
    for ch, y in zip((7, 5, 3, 1), slots):
        bd.text(f"{ch}", 7.0, y + 1.2, 0.8, pcbnew.B_SilkS, mirror=True)

    bd.silk_refs_to_fab()
    problems = bd.placement_report(os.path.join(HW, "build", "hub_place"))
    if problems:
        print("[hub] placement problems:\n  " + "\n  ".join(problems))
        if os.environ.get("PLACE_ONLY"):
            return problems
    if os.environ.get("PLACE_ONLY"):
        return []
    # USB-C VBUS sits on two pad stacks (A4/B9 and A9/B4): bridge them on B.Cu
    (ax, ay), (bx, by) = bd.pad_xy(usb, "A4"), bd.pad_xy(usb, "A9")
    vy = ay - 1.25
    for px in (ax, bx):
        bd.via("VBUS_IN", px, vy, 0.6, 0.3)
        bd.track("VBUS_IN", F, [(px, ay), (px, vy)], 0.4)
    bd.track("VBUS_IN", B, [(ax, vy), (bx, vy)], 0.4)
    n_fan, fan_fail = bd.fanout("GND")
    print(f"[hub] GND fan-out vias: {n_fan}, no room: {fan_fail}")
    bd.b.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)
    bd.zone("GND", pcbnew.In1_Cu, bd.outline_pts(0.0), priority=0, name="GND_plane")
    bd.netclass("Power", 0.4, 0.15, ["+BATT", "VSYS", "VBUS", "VBUS_IN", "+3V3", "BB_L1", "BB_L2"], via=(0.6, 0.3))
    bd.netclass("Analog", 0.2, 0.15, ["+3V0", "AFE_LDO_IN", "VREFP", "VCAP1", "VCAP2", "VCAP3", "VCAP4", "RLD_OUT", "RLD_INV", "BIAS_ELEC"])
    bd.netclass("GND", 0.3, 0.15, ["GND"])
    nt, nv = bd.route(os.path.join(HW, "build", "hub"), passes=100)
    print(f"[hub] routed: {nt} track segments, {nv} vias")
    bd.b.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_SIGNAL)
    return finish(bd, 4, [F, B, pcbnew.In2_Cu], stitch_net="GND", repair=True)


# ===================================================== SOCKET HUB (rigid-flex, 4 layers)
# Three rigid islands joined by two flex hinges; the board wraps around the outside
# of the inner socket (bend axis = forearm axis = board y).
H_FLEX = 46.0
ISL_A = (0.0, 33.0)     # analog front end
FLEX1 = (33.0, 43.0)
ISL_B = (43.0, 67.0)    # ESP32-S3
FLEX2 = (67.0, 77.0)
ISL_C = (77.0, 109.0)   # power, USB, microSD, hand interface
FLEX_Y = (6.0, 40.0)    # flex hinge height (narrower than the islands)
VIA_TRANSITION_KEEPOUT = 1.3   # no vias within 1.3 mm (~0.050") of a rigid/flex transition
FLEX_LAYERS_NO_COPPER = (pcbnew.F_Cu, pcbnew.B_Cu)


def flex_outline():
    y0, y1 = FLEX_Y
    o, i = 2.0, 1.0   # outer corner / inner (flex junction) fillet radii
    return [(0, 0, o), (FLEX1[0], 0, 0), (FLEX1[0], y0, i), (FLEX1[1], y0, i), (FLEX1[1], 0, 0),
            (FLEX2[0], 0, 0), (FLEX2[0], y0, i), (FLEX2[1], y0, i), (FLEX2[1], 0, 0), (ISL_C[1], 0, o),
            (ISL_C[1], H_FLEX, o), (FLEX2[1], H_FLEX, 0), (FLEX2[1], y1, i), (FLEX2[0], y1, i),
            (FLEX2[0], H_FLEX, 0), (FLEX1[1], H_FLEX, 0), (FLEX1[1], y1, i), (FLEX1[0], y1, i),
            (FLEX1[0], H_FLEX, 0), (0, H_FLEX, o)]


def rect(x0, x1, y0, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def island_of(x):
    for name, (a, b) in (("A", ISL_A), ("B", ISL_B), ("C", ISL_C)):
        if a <= x <= b:
            return name
    return "flex"


def add_flex_lanes(bd, flex, nets, width=0.2, power_width=0.6, pitch=None):
    """Pre-route each crossing net as one straight In2 track perpendicular to the bend line,
    evenly spaced. The lanes end as plain In2 stubs just outside the via keep-out; the
    router connects to the stub ends and places its own vias where the island has room
    (a fixed column of lane vias would wall off the island edge)."""
    x0 = flex[0] - VIA_TRANSITION_KEEPOUT - 0.2
    x1 = flex[1] + VIA_TRANSITION_KEEPOUT + 0.2
    y0, y1 = FLEX_Y[0] + 2.5, FLEX_Y[1] - 2.5
    pitch = pitch or (y1 - y0) / max(1, len(nets) - 1)
    for k, net in enumerate(nets):
        y = y0 + k * pitch
        w = power_width if net.startswith("+") else width
        bd.track(net, pcbnew.In2_Cu, [(x0, y), (x1, y)], w)
    return pitch


def crossing_nets(bd, c):
    """Nets with pads on both sides of each hinge (GND uses the hatched plane instead)."""
    sides = {}
    for ref, fp in bd.fps.items():
        for p in fp.Pads():
            n = p.GetNetname()
            if not n or n == "GND":
                continue
            x = p.GetPosition().x / 1e6 - pcb_origin()[0]
            sides.setdefault(n, set()).add(island_of(x))
    f1 = sorted(n for n, s in sides.items() if "A" in s and ("B" in s or "C" in s))
    f2 = sorted(n for n, s in sides.items() if "C" in s and ("A" in s or "B" in s))
    return f1, f2


def pcb_origin():
    from pcb_tools import ORIGIN
    return ORIGIN


def build_hub_flex():
    c = hub.build("socket")
    bd = Board(c, 4, ISL_C[1], H_FLEX, outline=flex_outline())

    def one(*nets, prefix=None):
        hits = bd.part_by_nets(*nets, prefix=prefix)
        assert hits, nets
        return hits

    def by_value(v):
        return [r for r, p in c.parts.items() if p.value == v][0]

    # ------------------------------------------------ island A: analog front end
    slots = [6.0, 15.6, 25.2, 34.8]
    jpod = {int(p.value[3:]): r for r, p in c.parts.items() if p.value.startswith("POD")}
    for i, ch in enumerate((8, 6, 4, 2)):
        bd.place_edge(jpod[ch], "L", slots[i], "F")
    for i, ch in enumerate((7, 5, 3, 1)):
        bd.place_edge(jpod[ch], "L", slots[i] + 3.8, "B")
    for i, ch in enumerate(range(8, 0, -1)):
        y = 4.6 + i * 4.15
        rp = one(f"POD{ch}_P", f"CH{ch}_P")[0]; rn = one(f"POD{ch}_N", f"CH{ch}_N")[0]
        cp = [r for r in one(f"CH{ch}_P", "GND") if r.startswith("C")][0]; cn = one(f"CH{ch}_N", "GND")[0]
        cd = one(f"CH{ch}_P", f"CH{ch}_N")[0]
        bd.place(rp, 8.4, y, 0); bd.place(rn, 8.4, y + 1.3, 0)
        bd.place(cp, 10.5, y, 0); bd.place(cn, 10.5, y + 1.3, 0)
        bd.place(cd, 12.4, y + 0.65, 90)
    ads = by_value("ADS1298IPAG")
    bd.place(ads, 21.0, 19.0, 0)
    afe_avdd = [r for r in one("+3V0", "GND") if c.parts[r].sheet == "afe"]
    vcap = {n: one(n, "GND")[0] for n in ("VCAP1", "VCAP2", "VCAP3", "VCAP4")}
    upper = afe_avdd[:3] + [r for r in one("+3V3", "GND") if c.parts[r].sheet == "afe" and c.parts[r].value in ("100nF", "1uF")][:3] + [vcap["VCAP3"]]
    for i, r in enumerate(upper):
        bd.place(r, 15.6 + i * 1.6, 10.6, 90)
    lower = one("VREFP", "GND") + [vcap["VCAP1"], vcap["VCAP2"], vcap["VCAP4"]] + afe_avdd[3:]
    for i, r in enumerate(lower):
        bd.place(r, 15.4 + i * 1.75, 27.6, 90)
    osc_cap = [r for r in one("+3V3", "GND") if c.parts[r].sheet == "afe" and r not in upper][0]
    bd.place(one("RLD_INV", "RLD_OUT", prefix="R")[0], 22.0, 7.4, 0); bd.place(one("RLD_INV", "RLD_OUT", prefix="C")[0], 25.0, 7.4, 0)
    for i, net in enumerate(("AFE_CS", "AFE_PWDN", "AFE_RESET")):
        bd.place(one("+3V3", net)[0], 29.4, 13.0 + i * 1.3, 0)
    xo = [r for r in c.parts if r.startswith("X")][0]
    bd.place(xo, 29.6, 21.4, 0); bd.place(one("OSC_OUT", "AFE_CLK")[0], 29.4, 18.2, 0); bd.place(osc_cap, 29.6, 24.4, 0)
    bd.place_edge(by_value("BIAS"), "B", 9.0)
    bd.place(one("RLD_OUT", "BIAS_ELEC")[0], 9.6, 38.4, 0)
    bd.place(by_value("LP5907-3.0"), 21.0, 36.0, 0)
    bd.place([r for r in c.parts if r.startswith("FB")][0], 16.5, 36.0, 90)
    bd.place(one("AFE_LDO_IN", "GND")[0], 18.4, 36.0, 90)
    pwr_caps = [r for r in one("+3V0", "GND") if c.parts[r].sheet == "power"]
    bd.place(pwr_caps[0], 24.2, 35.4, 90); bd.place(pwr_caps[1], 25.8, 35.4, 90)
    bd.place(one("AFE_EN", "GND", prefix="R")[0], 21.0, 39.4, 0)
    bd.place(one("AFE_EN_MCU", "AFE_EN")[0], 24.0, 39.4, 0)
    bd.place([r for r in c.parts if r.startswith("Q")][0], 27.4, 39.8, 0)
    bd.add_hole("H1", 15.0, 43.3); bd.add_hole("H2", 29.8, 3.2)

    # ------------------------------------------------ island B: MCU
    mod = [r for r, p in c.parts.items() if p.value.startswith("ESP32")][0]
    bd.place(mod, 55.0, 6.75, 0)   # antenna overhangs the island's top edge
    mcu_caps = [r for r in one("+3V3", "GND") if c.parts[r].sheet == "mcu"]
    esp_caps, imu_caps, sd_caps = mcu_caps[0:2], mcu_caps[2:4], mcu_caps[4:6]
    # decoupling + EN RC on the bottom side under the module's left pad row: the strip between
    # the hinge's via keep-out and the module pads is too narrow to escape from on top
    bd.place(esp_caps[0], 48.4, 3.0, 0, "B"); bd.place(esp_caps[1], 48.4, 5.2, 0, "B")
    bd.place(one("+3V3", "ESP_EN")[0], 48.4, 7.4, 0, "B"); bd.place(one("ESP_EN", "GND", prefix="C")[0], 48.4, 9.4, 0, "B")
    sws = [r for r, p in c.parts.items() if p.value in ("RESET", "BOOT/USER")]
    bd.place(sws[0], 48.6, 24.6, 0); bd.place(sws[1], 55.0, 24.6, 0)
    bd.place(one("+3V3", "BOOT")[0], 59.0, 24.6, 90)
    leds = [r for r, p in c.parts.items() if p.value in ("green", "blue")]
    bd.place(leds[0], 62.6, 23.2, 0); bd.place(one("LED_G", "LEDG_A")[0], 62.6, 25.0, 0)
    bd.place(leds[1], 62.6, 27.2, 0); bd.place(one("LED_B", "LEDB_A")[0], 62.6, 29.0, 0)
    imu = by_value("LSM6DSL")
    bd.place(imu, 54.0, 32.0, 0)
    bd.fps[imu].SetLocalClearance(pcbnew.FromMM(0.1))
    bd.place(imu_caps[0], 56.6, 31.0, 90); bd.place(imu_caps[1], 56.6, 33.2, 90)
    bd.place(one("+3V3", "I2C_SDA")[0], 49.0, 31.0, 90); bd.place(one("+3V3", "I2C_SCL")[0], 50.5, 31.0, 90)
    bd.add_hole("H3", 46.0, 42.8); bd.add_hole("H4", 64.0, 42.8)

    # ------------------------------------------------ island C: power, USB, storage, hand interface
    bd.place_edge(by_value("USB-C"), "B", 84.0, overhang=0.8)
    bd.place_edge(by_value("HAND"), "B", 98.5)
    sd = by_value("microSD")
    bd.place_edge(sd, "R", 28.0, overhang=0.6)
    bd.place_edge(by_value("POWER"), "R", 10.0, overhang=0.6)
    bd.place(by_value("USBLC6-2SC6"), 84.0, 33.8, 0)
    bd.place(one("CC1", "GND")[0], 79.6, 36.5, 0); bd.place(one("CC2", "GND")[0], 88.6, 36.5, 0)
    bd.place([r for r in c.parts if r.startswith("F") and not r.startswith("FB")][0], 80.4, 30.2, 0)
    bd.place(one("VBUS", "VBUS_DET")[0], 79.4, 25.4, 0); bd.place(one("VBUS_DET", "GND")[0], 79.4, 27.4, 0)
    bd.place([r for r, p in c.parts.items() if p.value.startswith("BATT")][0], 86.6, 18.0, 0, "B")
    bb = by_value("TPS63001")
    bd.place(bb, 81.9, 5.0, 0)
    bd.place([r for r in c.parts if r.startswith("L")][0], 86.6, 5.0, 0)
    vsys = one("VSYS", "GND")
    bd.place(vsys[0], 78.7, 3.6, 90); bd.place(vsys[1], 78.7, 7.6, 90)
    for i, r in enumerate([r for r in one("+3V3", "GND") if c.parts[r].sheet == "power"]):
        bd.place(r, 81.0 + i * 2.0, 11.0, 90)
    bd.place(one("VSYS", "BB_VINA")[0], 78.7, 11.4, 90); bd.place(one("BB_VINA", "GND")[0], 78.7, 13.6, 90)
    bd.place(by_value("MCP73831-2"), 93.0, 4.4, 0)
    bd.place(one("CHG_PROG", "GND")[0], 93.0, 7.4, 0)
    bd.place(one("VBUS", "GND")[0], 90.0, 4.4, 90); bd.place(one("+BATT", "GND")[0], 96.0, 4.4, 90)
    bd.place(one("VBUS", "CHG_LED_A")[0], 98.4, 3.0, 90)
    bd.place(by_value("CHG red"), 98.4, 6.6, 90)
    bd.place(by_value("LC709203F"), 92.6, 12.6, 0)
    bd.place([r for r in one("+BATT", "GND") if c.parts[r].value == "100nF"][0], 88.8, 12.4, 90)
    bd.place(one("FG_TSW", "FG_TSENSE")[0], 92.6, 16.2, 0)
    bd.place([r for r in c.parts if r.startswith("TH")][0], 95.8, 16.2, 0)
    bd.place(one("+3V3", "FG_ALRT")[0], 88.8, 15.4, 90)
    for i, n in enumerate(("SD_CMD", "SD_D0", "SD_D1", "SD_D2", "SD_D3", "SD_DET")):
        bd.place(one("+3V3", n)[0], 85.6, 20.6 + i * 1.3, 0)
    bd.place(sd_caps[0], 85.4, 28.6, 0); bd.place(sd_caps[1], 85.6, 30.4, 0)
    td = [one("TD_TX_PIN", "TD_TX")[0], one("TD_RX", "TD_RX_PIN")[0], one("TD_PWM1", "TD_AN1")[0],
          one("TD_PWM2", "TD_AN2")[0], one("TD_AN1", "GND")[0], one("TD_AN2", "GND")[0], one("TD_RX", "+3V3")[0]]
    for i, r in enumerate(td):
        bd.place(r, 93.0 + i * 2.0, 18.3, 90)
    bd.add_hole("H5", 106.4, 2.6); bd.add_hole("H6", 80.6, 21.0)

    # ------------------------------------------------ flex hinges
    for name, (fx0, fx1) in (("FLEX1", FLEX1), ("FLEX2", FLEX2)):
        y0, y1 = FLEX_Y
        bd.keepout([pcbnew.F_Cu, pcbnew.B_Cu], rect(fx0, fx1, y0 - 0.5, y1 + 0.5), tracks=True, vias=True, pour=True,
                   footprints=True, name=name)
        bd.keepout([pcbnew.In1_Cu, pcbnew.In2_Cu], rect(fx0 - VIA_TRANSITION_KEEPOUT, fx1 + VIA_TRANSITION_KEEPOUT, 0, H_FLEX),
                   tracks=False, vias=True, pour=False, name=name + "_via_keepout")
        bd.zone("GND", pcbnew.In1_Cu, rect(fx0 - 0.5, fx1 + 0.5, y0, y1), priority=5, hatch=(0.25, 0.65),
                thermal=False, name=name + "_GND_hatch")
        for layer in (pcbnew.Cmts_User,):
            for a, b in zip(rect(fx0, fx1, y0, y1), rect(fx0, fx1, y0, y1)[1:] + rect(fx0, fx1, y0, y1)[:1]):
                bd._seg(a, b, layer, 0.15)
        bd.text(name, (fx0 + fx1) / 2, (y0 + y1) / 2, 1.0, pcbnew.Cmts_User, rot=90)
    bd.text("RIGID-FLEX: islands 4L FR-4 1.6 mm; flex hinges 2L polyimide (In1 GND hatch, In2 signals), "
            "coverlay both sides, no stiffener; static bend R >= 5 mm", 54.0, -4.0, 1.0, pcbnew.Cmts_User)
    bd.text("FAMA-Band socket hub rev A", 18.0, 44.6, 0.8, pcbnew.B_SilkS, mirror=True)
    bd.text("DO NOT WEAR WHILE USB CONNECTED", 93.0, 26.0, 0.8, pcbnew.B_SilkS, mirror=True)

    bd.silk_refs_to_fab()
    problems = bd.placement_report(os.path.join(HW, "build", "hub_flex_place"))
    stray = [f"{r}@{island_of(fp.GetPosition().x / 1e6 - pcb_origin()[0])}" for r, fp in bd.fps.items()
             if island_of(fp.GetPosition().x / 1e6 - pcb_origin()[0]) == "flex"]
    if problems or stray:
        print("[hub_flex] placement problems:\n  " + "\n  ".join(problems + stray))
    if os.environ.get("PLACE_ONLY"):
        return problems

    usb = by_value("USB-C")
    (ax, ay), (bx, by_) = bd.pad_xy(usb, "A4"), bd.pad_xy(usb, "A9")
    vy = ay - 1.25
    for px in (ax, bx):
        bd.via("VBUS_IN", px, vy, 0.6, 0.3)
        bd.track("VBUS_IN", F, [(px, ay), (px, vy)], 0.4)
    bd.track("VBUS_IN", B, [(ax, vy), (bx, vy)], 0.4)
    f1, f2 = crossing_nets(bd, c)
    print(f"[hub_flex] FLEX1 lanes ({len(f1)}): {f1}\n[hub_flex] FLEX2 lanes ({len(f2)}): {f2}")
    add_flex_lanes(bd, FLEX1, f1)
    add_flex_lanes(bd, FLEX2, f2)
    n_fan, fan_fail = bd.fanout("GND")
    print(f"[hub_flex] GND fan-out vias: {n_fan}, no room: {fan_fail}")
    bd.b.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)
    for name, (a, b) in (("A", ISL_A), ("B", ISL_B), ("C", ISL_C)):
        bd.zone("GND", pcbnew.In1_Cu, rect(a, b, 0, H_FLEX), priority=0, name=f"GND_plane_{name}")
    # 0.3 mm power tracks (>1 A on outer 1 oz copper; max load 0.46 A) ease routing on island C
    bd.netclass("Power", 0.3, 0.15, ["+BATT", "VSYS", "VBUS", "VBUS_IN", "+3V3", "BB_L1", "BB_L2"], via=(0.5, 0.25))
    bd.netclass("Analog", 0.2, 0.15, ["+3V0", "AFE_LDO_IN", "VREFP", "VCAP1", "VCAP2", "VCAP3", "VCAP4", "RLD_OUT", "RLD_INV", "BIAS_ELEC"])
    bd.netclass("GND", 0.3, 0.15, ["GND"])
    # router may not add any copper inside the hinges (the locked lanes are the only flex copper);
    # these keep-outs exist only during autorouting
    router_ko = [bd.keepout([pcbnew.In1_Cu, pcbnew.In2_Cu], rect(fx0, fx1, FLEX_Y[0] - 0.5, FLEX_Y[1] + 0.5),
                            tracks=True, vias=True, pour=False, name="ROUTER_ONLY_" + str(i))
                 for i, (fx0, fx1) in enumerate((FLEX1, FLEX2))]
    nt, nv = bd.route(os.path.join(HW, "build", "hub_flex"), passes=100)
    for z in router_ko:
        bd.b.Remove(z)
    dup = bd.remove_lane_duplicates()
    n_trim, n_stub = trim_lanes(bd.b), remove_dangling_stubs(bd.b)
    print(f"[hub_flex] trimmed {n_trim} lane stubs, removed {n_stub} dangling GND stubs")
    stray = [t for t in bd.b.GetTracks() if not t.IsLocked() and t.Type() != pcbnew.PCB_VIA_T and
             any(island_of((p.x / 1e6) - pcb_origin()[0]) == "flex" and FLEX_Y[0] < (p.y / 1e6 - pcb_origin()[1]) < FLEX_Y[1]
                 for p in (t.GetStart(), t.GetEnd()))]
    print(f"[hub_flex] routed: {nt} track segments, {nv} vias; removed {dup} echoed lane duplicates; "
          f"router copper inside hinges: {len(stray)}")
    bd.b.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_SIGNAL)
    # outer and In2 pours only on the rigid islands
    for name, (a, b) in (("A", ISL_A), ("B", ISL_B), ("C", ISL_C)):
        for layer in (F, B, pcbnew.In2_Cu):
            bd.zone("GND", layer, rect(a, b, 0, H_FLEX), priority=0, name=f"GND_{name}_{bd.b.GetLayerName(layer)}")
    # no repair pass here: re-routing the routed board makes Freerouting rip up connections
    # to the locked hinge lanes; fix residual opens in placement instead
    return finish(bd, 4, [], stitch_net="GND", repair=False)


if __name__ == "__main__":
    which = sys.argv[1:] or ["pod", "hub", "hub_flex"]
    for w in which:
        {"pod": build_pod, "hub": build_hub, "hub_flex": build_hub_flex}[w]()
