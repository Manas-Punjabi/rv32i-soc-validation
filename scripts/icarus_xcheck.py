"""icarus_xcheck.py — do Icarus and Verilator agree on rv32i-soc-asic's SoC, cycle for cycle?

For each program of the lockstep regression, runs tb/tb_icarus.sv (Icarus) on the
same image that `make cosim-all` ran under Verilator, with the same wrapper (cosim_top),
the same reset and sampling point, and the same dump format. The two RVFI dumps must be
identical line for line: every retired instruction, its values, its memory access, and
the cycle in which it retires.

Usage: python3 scripts/icarus_xcheck.py <dir with cosim-all's .hex/.rvfi> <tb_icarus vvp> <elf> ...
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cosim.iss import load_elf      # noqa: E402


def records(path):
    return [l.split() for l in open(path) if l.strip() and l[0] != "#"]


def main(rundir, vvp, elfs):
    rundir, out = pathlib.Path(rundir), pathlib.Path(vvp).parent
    same = 0
    total_recs = 0
    for elf in elfs:
        name = pathlib.Path(elf).name
        tohost = load_elf(elf)[2].get("tohost", 0)
        vl_dump, ic_dump = rundir / f"{name}.rvfi", out / f"{name}.rvfi"
        r = subprocess.run(["vvp", "-n", vvp, f"+hex={rundir / (name + '.hex')}",
                            f"+tohost={tohost:x}", f"+rvfi={ic_dump}"],
                           capture_output=True, text=True, timeout=1800)
        a, b = records(vl_dump), records(ic_dump)
        diff = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
        if diff is None and len(a) == len(b):
            same += 1
            total_recs += len(a)
            continue
        status = next((l for l in r.stdout.splitlines() if l.startswith("ICARUS:")), "")
        print(f"  DIFFER {name}: verilator {len(a)} records, icarus {len(b)}"
              + (f" ({status})" if status else ""))
        if diff is not None:
            print(f"    first difference at record {diff}:\n"
                  f"      verilator: {' '.join(a[diff])}\n      icarus:    {' '.join(b[diff])}")
    print(f"{same} / {len(elfs)} programs: Icarus and Verilator RVFI dumps identical, "
          f"cycle for cycle ({total_recs} retirements)")
    return 0 if same == len(elfs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
