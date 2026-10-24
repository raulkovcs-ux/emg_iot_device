"""Minimal S-expression reader/writer for KiCad files.

Atoms are kept as Python strings. Quoted strings are wrapped in `Q` so the
writer can re-emit them quoted; everything else (numbers, keywords) stays bare.
"""


class Q(str):
    """A quoted string atom."""


def parse(text):
    pos = 0
    n = len(text)

    def skip_ws():
        nonlocal pos
        while pos < n and text[pos] in " \t\r\n":
            pos += 1

    def read():
        nonlocal pos
        skip_ws()
        c = text[pos]
        if c == "(":
            pos += 1
            out = []
            while True:
                skip_ws()
                if text[pos] == ")":
                    pos += 1
                    return out
                out.append(read())
        if c == '"':
            pos += 1
            buf = []
            while text[pos] != '"':
                if text[pos] == "\\":
                    pos += 1
                    esc = text[pos]
                    buf.append({"n": "\n", "t": "\t"}.get(esc, esc))
                else:
                    buf.append(text[pos])
                pos += 1
            pos += 1
            return Q("".join(buf))
        start = pos
        while pos < n and text[pos] not in " \t\r\n()":
            pos += 1
        return text[start:pos]

    return read()


def _atom(a):
    if isinstance(a, Q):
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(a, float):
        s = f"{a:.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(a)


def dump(node, indent=0):
    if not isinstance(node, list):
        return _atom(node)
    # short lists of atoms stay on one line
    if all(not isinstance(x, list) for x in node) or (
        len(node) <= 4 and all(not isinstance(x, list) or all(not isinstance(y, list) for y in x) for x in node)
        and sum(len(dump(x)) for x in node) < 90
    ):
        return "(" + " ".join(dump(x) for x in node) + ")"
    pad = "  " * (indent + 1)
    head = []
    rest = []
    for x in node:
        if not rest and not isinstance(x, list):
            head.append(_atom(x))
        else:
            rest.append(x)
    return "(" + " ".join(head) + "".join("\n" + pad + dump(x, indent + 1) for x in rest) + "\n" + "  " * indent + ")"


def find(node, key):
    """First child list whose head is `key`."""
    for x in node:
        if isinstance(x, list) and x and x[0] == key:
            return x
    return None


def find_all(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]
