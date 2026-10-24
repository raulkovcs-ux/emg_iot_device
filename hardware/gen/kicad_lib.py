"""Load, flatten and patch KiCad symbols so generated schematics can embed them."""
import copy
import os
import re

from sexpr import Q, dump, find, find_all, parse

KICAD_SYM_DIR = "/usr/share/kicad/symbols"
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_LIB = os.path.join(HERE, "..", "lib", "famaband.kicad_sym")

_cache = {}


def _load_lib(lib):
    if lib not in _cache:
        path = PROJECT_LIB if lib == "famaband" else os.path.join(KICAD_SYM_DIR, lib + ".kicad_sym")
        with open(path) as f:
            _cache[lib] = parse(f.read())
    return _cache[lib]


def _raw(lib, name):
    for s in find_all(_load_lib(lib), "symbol"):
        if s[1] == name:
            return s
    raise KeyError(f"{lib}:{name}")


def _rename_units(node, old, new):
    for sub in find_all(node, "symbol"):
        sub[1] = Q(re.sub("^" + re.escape(old) + "_", new + "_", sub[1]))


def flatten(lib, name):
    """Return a self-contained copy of lib:name with `extends` resolved."""
    sym = copy.deepcopy(_raw(lib, name))
    ext = find(sym, "extends")
    if ext is None:
        return sym
    parent = flatten(lib, ext[1])
    out = copy.deepcopy(parent)
    out[1] = Q(name)
    _rename_units(out, ext[1], name)
    child_props = {p[1]: p for p in find_all(sym, "property")}
    for i, x in enumerate(out):
        if isinstance(x, list) and x and x[0] == "property" and x[1] in child_props:
            out[i] = child_props.pop(x[1])
    # any property only the child has goes before the first unit
    idx = next(i for i, x in enumerate(out) if isinstance(x, list) and x and x[0] == "symbol")
    for p in child_props.values():
        out.insert(idx, p)
        idx += 1
    return out


def embedded(lib_id):
    lib, name = lib_id.split(":")
    sym = flatten(lib, name)
    sym[1] = Q(lib_id)
    return sym


class SymInfo:
    """Pin geometry of a symbol, per unit."""

    def __init__(self, lib_id):
        lib, name = lib_id.split(":")
        self.lib_id = lib_id
        self.node = flatten(lib, name)
        self.pins = {}  # number -> dict(name, type, x, y, angle, unit)
        self.units = set()
        self.power = find(self.node, "power") is not None
        self.bbox = {}  # unit -> [x0, y0, x1, y1]
        for sub in find_all(self.node, "symbol"):
            m = re.search(r"_(\d+)_(\d+)$", sub[1])
            unit = int(m.group(1))
            if unit:
                self.units.add(unit)
            for pin in find_all(sub, "pin"):
                at = find(pin, "at")
                num = find(pin, "number")[1]
                self.pins[num] = dict(
                    name=find(pin, "name")[1], type=pin[1], x=float(at[1]), y=float(at[2]),
                    angle=int(float(at[3])), unit=unit, hidden="hide" in pin,
                    length=float(find(pin, "length")[1]),
                )
                self._grow(unit, float(at[1]), float(at[2]))
            for shape in ("rectangle",):
                for r in find_all(sub, shape):
                    s, e = find(r, "start"), find(r, "end")
                    self._grow(unit, float(s[1]), float(s[2]))
                    self._grow(unit, float(e[1]), float(e[2]))
        if not self.units:
            self.units = {1}

    def _grow(self, unit, x, y):
        b = self.bbox.setdefault(unit, [x, y, x, y])
        b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)

    def unit_bbox(self, unit):
        boxes = [self.bbox[u] for u in (0, unit) if u in self.bbox]
        return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]

    def pins_of_unit(self, unit):
        return {n: p for n, p in self.pins.items() if p["unit"] in (0, unit)}


# ---------------------------------------------------------------- project lib

def _pin(num, name, ptype, x, y, angle, length=2.54, hide=False):
    p = ["pin", ptype, "line", ["at", x, y, angle], ["length", length]]
    if hide:
        p.append("hide")
    p += [["name", Q(name), ["effects", ["font", ["size", 1.27, 1.27]]]],
          ["number", Q(str(num)), ["effects", ["font", ["size", 1.27, 1.27]]]]]
    return p


def _prop(key, val, y, hide=False):
    eff = ["effects", ["font", ["size", 1.27, 1.27]]]
    if hide:
        eff.append("hide")
    return ["property", Q(key), Q(val), ["at", 0, y, 0], eff]


def build_project_lib():
    """Write lib/famaband.kicad_sym: a corrected ADS1298 and a dry-electrode symbol."""
    ads = flatten("Analog_ADC", "ADS1298xPAG")
    ads[1] = Q("ADS1298IPAG")
    _rename_units(ads, "ADS1298xPAG", "ADS1298IPAG")
    # KiCad 7.0 library swaps the names of pins 3 and 4. Datasheet (SBAS459, PAG
    # pin table): pin 3 = IN7N, pin 4 = IN7P. Pin 18 is TESTN_PACE_OUT2.
    fix = {"3": "IN7N", "4": "IN7P", "18": "TESTN_PACE_OUT2"}
    for sub in find_all(ads, "symbol"):
        for pin in find_all(sub, "pin"):
            num = find(pin, "number")[1]
            if num in fix:
                find(pin, "name")[1] = Q(fix[num])
    for p in find_all(ads, "property"):
        if p[1] == "Value":
            p[2] = Q("ADS1298IPAG")
        if p[1] == "Description" or p[1] == "ki_description":
            p[2] = Q("8-ch 24-bit biopotential AFE, TQFP-64 (pin 3/4 names corrected vs KiCad 7.0 lib)")

    elec = ["symbol", Q("DryElectrode"), ["pin_names", ["offset", 0.254]], ["in_bom", "no"], ["on_board", "yes"],
            _prop("Reference", "E", 3.81), _prop("Value", "DryElectrode", -3.81),
            _prop("Footprint", "famaband:DryElectrode_D10mm", -6.35, True), _prop("Datasheet", "~", -8.89, True),
            _prop("ki_description", "Skin-contact dry electrode (ENIG pad, PCB bottom side)", -11.43, True),
            ["symbol", Q("DryElectrode_0_1"),
             ["circle", ["center", 0, 0], ["radius", 1.905], ["stroke", ["width", 0.254], ["type", "default"]], ["fill", ["type", "outline"]]],
             ["polyline", ["pts", ["xy", -1.27, -2.54], ["xy", 1.27, -2.54]], ["stroke", ["width", 0.254], ["type", "default"]], ["fill", ["type", "none"]]]],
            ["symbol", Q("DryElectrode_1_1"), _pin(1, "E", "passive", 0, 5.08, 270, 3.175)]]

    lib = ["kicad_symbol_lib", ["version", "20220914"], ["generator", "famaband_gen"], ads, elec]
    os.makedirs(os.path.dirname(PROJECT_LIB), exist_ok=True)
    with open(PROJECT_LIB, "w") as f:
        f.write(dump(lib) + "\n")
    _cache.pop("famaband", None)


if __name__ == "__main__":
    build_project_lib()
    info = SymInfo("famaband:ADS1298IPAG")
    print({n: info.pins[n]["name"] for n in ("3", "4", "18")})
