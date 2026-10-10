# Design decisions

Every non-obvious choice in this repository, with the reason it was made. References [R1]–[R8]
are listed at the end of [architecture.md](architecture.md).

## Reference model and comparison

| Decision | Reason |
|---|---|
| A Python reference model of its own, compared offline against the RTL's RVFI dump | Spike cannot model the SoC's peripherals, so it diverges on the first peripheral read. Values only the RTL can know flow one way, RTL → model, so the model can replay the dump after the run without being linked into the simulation. Ibex's Spike co-simulation injects at the same points [R1, R2]. |
| Spike kept as the validator of the model | A home-made reference is only trustworthy if a trusted one agrees with it. Every program without peripherals runs on both, re-checked after every model change. Spike found a model bug this way (`mcountinhibit` applied one instruction early) that would otherwise have been taken for RTL behaviour. |
| Spike as `--isa=rv32i_zicsr_zicntr_zifencei --priv=M --pmpregions=0 --triggers=0` | Measured, not assumed: without Zicntr, Spike traps on counter reads the core executes ([spike_isa_string.txt](reports/spike_isa_string.txt)). Machine mode only, no PMP and no triggers make Spike the same machine as the core. |
| Injection only for values the model cannot know | Counters, ID CSRs, `mip`, peripheral load data, and whether an interrupt was taken. Everything else is computed independently, so injection cannot hide a bug: a fake RTL with an SRA bug still diverges. |
| The model follows `soc_top`'s address decode, not Spike's | Unmapped addresses read 0 and drop writes, and RAM aliases across `0x8xxx_xxxx`: that is the design. A fetch outside RAM stays a hard error, because it means a broken program. |
| The next PC (`pc_wdata`) is compared, not only the current one | Without it, a trace with a wrong next-PC field passed, and one of rv32i-soc-asic's mutants survived. |

## Interface to rv32i-soc-asic

| Decision | Reason |
|---|---|
| RVFI as the RTL interface | Standard and small, and the same harness can serve any core that implements it [R6]. |
| The harness loads the image into the flop memory array; no RTL change | The same hierarchical load rv32i-soc-asic's own testbench uses. The co-simulation build uses the flop memory, which models the SRAM macro's read timing; the OpenRAM behavioural model is not a Verilator target. |
| Interrupts through an RVFI side band of three simulation-only signals | `rvfi_intr` (RVFI spec) plus `rvfi_ext_mcause` and `rvfi_ext_mip` (Ibex convention), inside `` `ifdef RVFI ``, so synthesis is unchanged. The pending lines are needed to check interrupt priority. |
| rv32i-soc-asic is the design under test, never patched here | Problems found are fixed in rv32i-soc-asic itself, with a test, and its flow re-run; this repository only reads it. |
| `sw/soc.h` is shared with rv32i-soc-asic; the startup code and linker script are kept identical to its own | `soc.h` is the memory map the RTL implements. rv32i-soc-asic's `main.c` is built unchanged against the same runtime. |
| RAM at `0x8000_0000`, exit through `tohost` | Spike's defaults, so one ELF runs unchanged on Spike, the model and the RTL. |

## Stimulus and checking

| Decision | Reason |
|---|---|
| Extend this repository's generator rather than adopt riscv-dv | riscv-dv's Python port is documented as slow with a non-uniform solution spread [R8]. The gaps that mattered (loops, CSR chains, traps, peripheral accesses, pointer chasing, interrupts) were closed as generator modes, each driven by a measured coverage hole. |
| Existing generator modes stay byte-identical when a mode is added | Results recorded for earlier programs must not move. Checked by regenerating and comparing before each change. |
| Every lockstep regression is also run with uninitialized RTL state randomized | It is the only check that finds a dependence on power-up state; Verilator's default zero-initialization hides it. |
| A must-fail control for every comparison | Three comparisons in this repository could not fail at first, because a pipe through `head` or `tail` hid the exit status. Each now runs with `pipefail` and has a control. |
| A baseline run before counting mutants | Every program must pass on the unmutated design first, so a program that fails for another reason cannot be counted as catching a mutant. |
| Coverage holes are confirmed by a second method before a test is written | Verilator 5.020 shifts and merges `case`-item line attribution; registers and CSRs are confirmed from the traces (`scripts/reg_census.py`). |
| riscv-formal left out of scope | It needs RVFI signals rv32i-soc-asic does not export (`rs1`/`rs2` data, `mode`, `ixl`, `halt`) and a separate formal tool flow [R7]. |

## Device models (stand-alone model runs only)

| Decision | Reason |
|---|---|
| Instant accelerator model | Firmware only observes `done`; in lockstep the RTL's real STATUS values are injected. |
| `mtime` = retired instructions | Monotonic is all firmware may rely on. Cycle figures printed on the model are not performance numbers; real cycles come from the RTL. |
