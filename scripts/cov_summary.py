"""cov_summary.py — break a merged verilator_coverage .dat down by kind and by file.

verilator_coverage prints one total; this shows what it is made of (line vs toggle
points, per source file) and lists every point with zero hits, so a percentage can be
read for what it is.

Usage: python3 scripts/cov_summary.py <merged.dat> [--zeros] [--file <substring>]
"""
import re
import sys
from collections import Counter, defaultdict

FIELD = re.compile(r"\x01(\w+)\x02([^\x01']*)")


def points(path):
    for line in open(path, encoding="latin-1"):
        if not line.startswith("C '"):
            continue
        body, count = line.rsplit("' ", 1)
        f = dict(FIELD.findall(body))
        kind = f.get("page", "?").split("/")[0]            # v_line, v_toggle, v_branch...
        yield kind, f.get("f", "?").rsplit("/", 1)[-1], int(f.get("l", 0)), \
            f.get("o", ""), f.get("h", ""), int(count)


def main(argv):
    path = argv[0]
    zeros = "--zeros" in argv
    only = argv[argv.index("--file") + 1] if "--file" in argv else None
    tot, hit = Counter(), Counter()
    zero_list = defaultdict(list)
    for kind, fname, line, comment, hier, count in points(path):
        key = (fname, kind)
        tot[key] += 1
        if count:
            hit[key] += 1
        else:
            zero_list[fname].append((line, kind, comment, hier))
    kinds = sorted({k for _, k in tot})
    print(f"{'file':22s}" + "".join(f"{k:>18s}" for k in kinds))
    for fname in sorted({f for f, _ in tot}):
        row = ""
        for k in kinds:
            t = tot[(fname, k)]
            row += f"{(f'{hit[(fname, k)]}/{t}' if t else '-'):>18s}"
        print(f"{fname:22s}{row}")
    for k in kinds:
        t = sum(v for (f, kk), v in tot.items() if kk == k)
        h = sum(v for (f, kk), v in hit.items() if kk == k)
        print(f"total {k:16s} {h}/{t} = {100 * h / t:.1f} %")
    if zeros:
        for fname in sorted(zero_list):
            if only and only not in fname:
                continue
            print(f"\n== {fname}: {len(zero_list[fname])} points never hit")
            for line, kind, comment, hier in sorted(zero_list[fname]):
                print(f"  {line:5d} {kind:9s} {comment:28s} {hier}")


if __name__ == "__main__":
    main(sys.argv[1:])
