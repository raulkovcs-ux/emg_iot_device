"""FAMA-Band electrode pod: two dry electrodes + dual unity-gain buffer (active electrode).

One pod = one bipolar sEMG channel. Eight identical pods connect to the hub.
"""
from circuit import FP, Circuit


def build():
    c = Circuit("pod", "FAMA-Band electrode pod", [
        ("pod", "Active dry-electrode pod: 2 x 10 mm ENIG electrodes, 20 mm IED, OPA2376 buffers"),
    ])
    c.note("Electrodes E1/E2 are bare ENIG pads on the PCB bottom (skin side), 20 mm centre-to-centre (SENIAM).\n"
           "R1/R2 (100k) limit current into the body to < 33 uA under any single fault (3.3 V across 100k).\n"
           "Buffer outputs also drive the solder-masked guard rings around each electrode (bootstrapped guard).\n"
           "R3/R4 isolate the op-amp outputs from cable capacitance. Body is biased to ~1.5 V by the hub RLD.")
    j = c.part("J", "Connector_Generic:Conn_01x04", "HUB", "Connector_JST:JST_GH_SM04B-GHS-TB_1x04-1MP_P1.25mm_Horizontal",
               mpn="SM04B-GHS-TB(LF)(SN)", manufacturer="JST", desc="Cable to hub (+3V0, P, N, GND/shield)")
    c.connect("+3V0", j["1"]); c.connect("OUT_P", j["2"]); c.connect("OUT_N", j["3"]); c.connect("GND", j["4"])
    c.flag("+3V0"); c.flag("GND")
    for side, e_ref in (("P", "E1"), ("N", "E2")):
        e = c.part("E", "famaband:DryElectrode", f"ELEC_{side}", "famaband:DryElectrode_D10mm", ref=e_ref,
                   desc="10 mm ENIG skin electrode (PCB pad)")
        c.connect(f"ELEC_{side}", e["1"])
        c.R("100k", f"ELEC_{side}", f"IN_{side}", "0603", desc="Patient fault-current limit")
    u = c.part("U", "Amplifier_Operational:OPA2376xxDGK", "OPA2376", "Package_SO:VSSOP-8_3.0x3.0mm_P0.65mm",
               mpn="OPA2376AIDGKR", manufacturer="Texas Instruments",
               desc="Dual RRIO CMOS op-amp, 7.5 nV/rtHz, 0.2 pA bias")
    c.connect("IN_P", u["3"]); c.connect("BUF_P", u["2"], u["1"])
    c.connect("IN_N", u["5"]); c.connect("BUF_N", u["6"], u["7"])
    c.connect("+3V0", u["8"]); c.connect("GND", u["4"])
    c.R("47", "BUF_P", "OUT_P", "0603"); c.R("47", "BUF_N", "OUT_N", "0603")
    c.C("100nF", "+3V0", "GND", "0603"); c.C("1uF", "+3V0", "GND", "0603")
    return c


if __name__ == "__main__":
    circ = build()
    errs = circ.erc()
    print(len(circ.parts), "parts,", len(circ.nets), "nets,", len(errs), "ERC errors")
    for e in errs:
        print("  ", e)
