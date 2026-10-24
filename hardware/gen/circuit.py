"""Tiny netlist DSL: the single source of truth for schematics, PCB and BOM.

A Circuit holds parts (one KiCad symbol each, optionally multi-unit) grouped by
schematic sheet, plus named nets connecting (ref, pin-number) pairs. The
schematic writer, the PCB builder and the BOM exporter all read from it, so the
three cannot drift apart.
"""
from collections import OrderedDict, defaultdict

from kicad_lib import SymInfo

# nets drawn as KiCad power symbols instead of global labels
POWER_SYMBOLS = {"GND": "power:GND", "+3V3": "power:+3V3", "+3V0": "power:+3V0",
                 "VBUS": "power:VBUS", "+BATT": "power:+BATT"}

FP = {
    "R0402": "Resistor_SMD:R_0402_1005Metric", "C0402": "Capacitor_SMD:C_0402_1005Metric",
    "R0603": "Resistor_SMD:R_0603_1608Metric", "C0603": "Capacitor_SMD:C_0603_1608Metric",
    "C0805": "Capacitor_SMD:C_0805_2012Metric", "LED0603": "LED_SMD:LED_0603_1608Metric",
    "FB0603": "Inductor_SMD:L_0603_1608Metric", "F0805": "Fuse:Fuse_0805_2012Metric",
}


class Part:
    def __init__(self, circuit, ref, lib_id, value, footprint, sheet, mpn="", manufacturer="", desc="", dnp=False):
        self.circuit, self.ref, self.lib_id, self.value = circuit, ref, lib_id, value
        self.footprint, self.sheet, self.mpn, self.manufacturer, self.desc, self.dnp = (
            footprint, sheet, mpn, manufacturer, desc, dnp)
        self.sym = SymInfo(lib_id)
        self.nc = set()

    def pin(self, key):
        """Pin by number, or by unique name."""
        key = str(key)
        if key in self.sym.pins:
            return key
        hits = [n for n, p in self.sym.pins.items() if p["name"] == key]
        if len(hits) != 1:
            raise KeyError(f"{self.ref}: pin '{key}' -> {hits}")
        return hits[0]

    def pins_named(self, name):
        return [n for n, p in self.sym.pins.items() if p["name"] == name]

    def __getitem__(self, key):
        return (self, self.pin(key))


class Circuit:
    def __init__(self, name, title, sheets):
        self.name, self.title = name, title
        self.sheets = OrderedDict(sheets)  # file-stem -> human title
        self.parts = OrderedDict()
        self.nets = OrderedDict()  # net -> list[(ref, pin)]
        self.counters = defaultdict(int)
        self.notes = defaultdict(list)  # sheet -> [text]
        self.sheet = next(iter(self.sheets))
        self.row_id = 0

    # ---------------------------------------------------------------- building
    def use(self, sheet):
        assert sheet in self.sheets
        self.sheet = sheet
        self.row_id += 1
        return self

    def newrow(self):
        """Start a new functional group (drawn as a new row on the schematic)."""
        self.row_id += 1

    def part(self, prefix, lib_id, value, footprint, ref=None, **kw):
        if ref is None:
            self.counters[prefix] += 1
            ref = f"{prefix}{self.counters[prefix]}"
        assert ref not in self.parts, ref
        p = Part(self, ref, lib_id, value, footprint, self.sheet, **kw)
        p.row = self.row_id
        self.parts[ref] = p
        return p

    def connect(self, net, *pins):
        lst = self.nets.setdefault(net, [])
        for part, num in pins:
            for other_net, members in self.nets.items():
                if other_net != net and (part.ref, num) in members:
                    raise ValueError(f"{part.ref}.{num} already on {other_net}, not {net}")
            if (part.ref, num) not in lst:
                lst.append((part.ref, num))

    def nc(self, part, *keys):
        for k in keys:
            part.nc.add(part.pin(k))

    def note(self, text):
        self.notes[self.sheet].append(text)

    # two-terminal helpers ---------------------------------------------------
    def R(self, value, a, b, size="0402", **kw):
        p = self.part("R", "Device:R", value, FP["R" + size], **kw)
        self.connect(a, p["1"]); self.connect(b, p["2"])
        return p

    def C(self, value, a, b, size="0402", **kw):
        p = self.part("C", "Device:C", value, FP.get("C" + size, size), **kw)
        self.connect(a, p["1"]); self.connect(b, p["2"])
        return p

    def LED(self, value, anode, cathode, **kw):
        p = self.part("D", "Device:LED", value, FP["LED0603"], **kw)
        self.connect(anode, p["A"]); self.connect(cathode, p["K"])
        return p

    def flag(self, net):
        p = self.part("#FLG", "power:PWR_FLAG", "PWR_FLAG", "")
        self.connect(net, p["1"])

    # ---------------------------------------------------------------- queries
    def pin_net(self):
        out = {}
        for net, members in self.nets.items():
            for m in members:
                out[m] = net
        return out

    def erc(self):
        """Structural electrical-rules check. Returns a list of error strings."""
        errors = []
        pn = self.pin_net()
        for ref, p in self.parts.items():
            for num, pin in p.sym.pins.items():
                connected = (ref, num) in pn
                if pin["type"] == "no_connect":
                    if connected:
                        errors.append(f"{ref}.{num} ({pin['name']}) is a no-connect pin but on net {pn[(ref, num)]}")
                    continue
                if connected and num in p.nc:
                    errors.append(f"{ref}.{num} marked NC but connected")
                if not connected and num not in p.nc:
                    errors.append(f"{ref}.{num} ({pin['name']}) unconnected")
        for net, members in self.nets.items():
            if len(members) < 2:
                errors.append(f"net {net} has a single pin {members}")
            types = [self.parts[r].sym.pins[n]["type"] for r, n in members]
            drivers = sum(t in ("output", "power_out") for t in types)
            if types.count("output") > 1:
                errors.append(f"net {net}: {types.count('output')} outputs drive the same net")
            if "power_in" in types and not any(t == "power_out" for t in types):
                errors.append(f"net {net}: power input(s) without a power source or PWR_FLAG")
            if "input" in types and drivers == 0 and not any(
                    t in ("passive", "bidirectional", "tri_state", "open_collector", "power_in") for t in types):
                errors.append(f"net {net}: inputs only, nothing drives it")
        return errors

    def bom(self):
        groups = OrderedDict()
        for p in self.parts.values():
            if p.ref.startswith("#"):
                continue
            key = (p.value, p.footprint, p.mpn, p.dnp)
            groups.setdefault(key, []).append(p)
        rows = []
        for (value, fp, mpn, dnp), ps in groups.items():
            refs = sorted((x.ref for x in ps), key=lambda r: (r.rstrip("0123456789"), int("0" + "".join(c for c in r if c.isdigit()))))
            rows.append(dict(refs=",".join(refs), qty=len(ps), value=value, footprint=fp.split(":")[-1],
                             mpn=mpn, manufacturer=ps[0].manufacturer, desc=ps[0].desc, dnp=dnp))
        return rows
