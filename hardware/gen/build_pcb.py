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
from pcb_tools import Board, export_fab, finalize, reroute_saved, unconnected_nets  # noqa: E402

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


if __name__ == "__main__":
    which = sys.argv[1:] or ["pod", "hub"]
    for w in which:
        {"pod": build_pod, "hub": build_hub}[w]()
