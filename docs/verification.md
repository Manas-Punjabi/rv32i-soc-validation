# Verification

Every check has a command, a result and a report with its raw output. All results below are from
one run on 2026-10-10 against rv32i-soc-asic as published, with Verilator 5.020, Icarus Verilog
12.0, Spike (riscv-isa-sim `0bff1212`) and riscv-tests `d445110`.

| Check | Command | Result | Report |
|---|---|---|---|
| Reference model vs Spike | `make verify` | identical on 38,822 retirements: firmware and 20 random programs (21,712), 55 riscv-tests (17,110) | [reference_model.txt](reports/reference_model.txt) |
| The checker accepts legitimate differences and catches bugs | `make verify` | a fake RTL with a slow accelerator, CPI 2 and another `marchid` matches; the same with an SRA bug diverges at #162 | [reference_model.txt](reports/reference_model.txt) |
| Mutant reference models | `make verify` | 9 / 9 caught | [reference_model.txt](reports/reference_model.txt) |
| Lockstep regression | `make cosim-all` | 111 / 111 programs match | [lockstep_regression.txt](reports/lockstep_regression.txt) |
| Directed tests | `make directed` | 8 / 8 match | [directed_tests.txt](reports/directed_tests.txt) |
| Constrained-random stress | `make stress` | 150 / 150 match | [stress.txt](reports/stress.txt) |
| Random interrupts | `make irq-stress` | 50 / 50 match, 573 interrupts taken and checked | [interrupts.txt](reports/interrupts.txt) |
| Random initial state | `make <target> SEED=1..3` | all four regressions match under all three seeds | [random_initial_state.txt](reports/random_initial_state.txt) |
| Two simulators | `make xcheck` | Icarus and Verilator traces identical, cycle for cycle, 177,669 retirements | [simulator_crosscheck.txt](reports/simulator_crosscheck.txt) |
| Benchmark cross-check | `make bench-xcheck` | identical reports on both simulators; lockstep match on 103,043 retirements | [benchmark_crosscheck.txt](reports/benchmark_crosscheck.txt) |
| Coverage | `make coverage` | architectural 228 / 228 bins; RTL line 97.1 %, branch 96.2 % | [coverage.txt](reports/coverage.txt) |
| rv32i-soc-asic's RTL mutants | `make mutate-rtl` | 28 / 28 caught over 319 programs, after a baseline in which every program passes | [rtl_mutants.txt](reports/rtl_mutants.txt) |

`make verify` (about 25 s) needs only the RISC-V GCC toolchain and Spike; it skips the Spike
comparisons if Spike is not installed. The other targets need Verilator and a checkout of
rv32i-soc-asic next to this repository (`SOC=` to point elsewhere). `make mutate-rtl` takes about
15 minutes; everything else takes under a minute each.

## The reference model is correct

`make spike-check` runs firmware and 20 random programs on both Spike and the reference model and
compares every retirement: PC, instruction, destination register and value, memory address,
store data and width. `make riscv-tests` does the same for the 55 riscv-tests. All 38,822
retirements are identical. Spike keeps executing the exit loop after the pass signal until its
host polls `tohost`, so the Spike log reader stops at the `tohost` store, where the model and the
RTL stop. Spike runs under a timeout, so a program that never exits fails rather than hangs.

## The checker catches bugs

A comparison that never fails proves nothing, so each one has a deliberately broken input.

`scripts/test_lockstep.py` runs guided lockstep against a fake RTL with a slow accelerator, CPI 2
and a different `marchid`:

| Program | Guided lockstep | Plain offline compare |
|---|---|---|
| `test_accel` | match, 12,960 retirements, 21 values injected | diverges, as it should |
| `test_counters` | match, 343 retirements, 5 values injected | diverges, as it should |
| `test_isa` | match, 7,531 retirements, 0 injected | match |
| `test_isa` on a fake RTL with an SRA bug | **diverges at #162**: injection does not hide bugs | |

`make mutate` builds nine mutant reference models, each a classic pipeline bug, and runs them in
lockstep against the correct one:

| ID | Bug | Caught by | After N matching |
|---|---|---|---|
| C1 | SRA implemented as SRL | `test_isa` | 162 |
| C2 | LB zero-extends | random program | 689 |
| C3 | missing forwarding (stale operand) | random program | 388 |
| C4 | BLTU compares signed | `test_isa` | 157 |
| C5 | SH writes the low byte only | `test_isa` | 256 |
| C6 | JALR keeps target bit 0 | `test_isa` | 7,513 |
| C7 | writes to x0 not suppressed | `csr_ops.S` | 114 |
| C8 | SLT compares unsigned | random program | 401 |
| C9 | `fence.i` leaves read-ahead instructions stale | `fence_i.S` only | 55 |

Two of these shaped the stimulus: C7 survived until the random generator was made to target x0,
and C6 until `test_isa` gained a deliberately odd `jalr` target, because compilers never emit one.

## Lockstep regressions

| Target | Programs |
|---|---|
| `make cosim-all` | 55 riscv-tests; rv32i-soc-asic's SoC smoke test (UART transcript checked); `test_isa`, `test_accel`, `test_counters`, `test_soc_map` and rv32i-soc-asic's benchmark; 50 random programs |
| `make directed` | `fence_i.S` (stores over the next 0–3 and 6 instructions, then falls through), `csr_ops.S`, `hazards.S` (computed addresses 1–3 instructions after their producer), `far_code.S` (code, handler and data above 48 KB), `mret_mpie.S`, `irq_soc.S` (software, timer and UART interrupts, then all three pending at once, taken in priority order), `mmio_regs_soc.S` (every timer, UART and GPIO register), `wfi_soc.S` |
| `make stress` | 50 stress programs (loops, CSR chains on `mscratch`, illegal instructions, `ecall`, `ebreak` and misaligned accesses inside hazard chains, with a skip-and-`mret` handler), 50 with timer and GPIO accesses (bus stalls), 50 pointer-chasing; the generated programs also match on Spike |
| `make irq-stress` | 50 programs raising software and timer interrupts at random points: 573 interrupts, each checked for legality and priority |

The directed programs that need no SoC device also run on Spike and the reference model first.
Every regression is repeated with all uninitialized RTL state randomized (`SEED=1..3`), which is
the only check that finds a dependence on power-up state; Verilator's default zero-initialization
hides it.

**Two simulators.** `make xcheck` runs the 111 regression programs on Icarus and Verilator
through the same wrapper and compares the two RVFI dumps record by record, including the cycle
of each retirement: identical, 177,669 retirements.

**The benchmark.** `make bench-xcheck` builds rv32i-soc-asic's exact benchmark image (its startup
code, linker script, `main.c` and build flags) and runs it on rv32i-soc-asic's own Icarus
testbench and on this repository's Verilator model. The UART reports are identical line by line
(software 5,075 cycles, accelerator 473, 10.7×, 97.2 % bus movement), and the Verilator trace
matches in lockstep over 103,043 retirements with 9,501 RTL values injected. The two run lengths
differ by the fixed measuring-point offset of the two testbenches (+2 retirements, −1 cycle).

## Coverage

**Architectural coverage** (`scripts/arch_cov.py`, from the RTL's own RVFI traces): every opcode;
read-after-write distance 1, 2 and 3 for each producer and consumer class, including computed
addresses; load-use; taken and not-taken per branch type; every trap cause; every CSR operation;
store-to-load on the same address; MMIO reads and writes; stalls. **228 / 228 bins hit.** One bin
is excluded with its reason: a trap on the instruction right after a trap, which cannot happen
because the handler's first instruction always comes in between.

The hardest bins were address-generation hazards: a load, `jal` link or CSR result used 1–3
instructions later as the base of a load, store or `jalr`, which ordinary generated code never
does. The pointer-chasing generator mode and the directed `hazards.S` and `wfi_soc.S` close them.

**RTL coverage** (Verilator line and toggle coverage over all 269 lockstep programs; the memory
storage array excluded):

| | |
|---|---|
| line | **97.1 %** (232 / 239) |
| branch | **96.2 %** (175 / 182) |
| toggle | 77.8 % (5,966 / 7,666) |

The core and the fetch stage reach 100 % branch coverage. The one core line never hit is in the
RVFI side band (not synthesized): a handler's first instruction that is also a replayed
`fence.i`, which cannot occur. The remaining line and branch gaps are peripheral `default` arms,
UART receive error paths (covered by rv32i-soc-asic's 299-check UART testbench) and the APB
"slave not ready" branch (every slave is zero-wait). Untoggled bits are mostly constant by
design: hard-wired CSR fields, high cause-code bits, word-aligned address bits, and the RVFI side
band's 32-bit cause and pending registers.

Two method notes: Verilator 5.020 shifts and merges line attribution for `case` items, so a line
hole is confirmed from the traces (`scripts/reg_census.py`) before a test is written; and
`verilator_coverage`'s single "Total coverage" figure does not match its own per-kind totals, so
`scripts/cov_summary.py` reports line, branch and toggle separately.

## rv32i-soc-asic's RTL mutants under lockstep

`make mutate-rtl` reads the 28 mutants from rv32i-soc-asic's `scripts/mutate_core.sh`, applies
each to a scratch copy of the RTL, and runs all 319 programs in lockstep. rv32i-soc-asic itself is
never modified. A baseline run on the unmutated design must pass first, so a program that fails
for any other reason cannot be counted as catching a mutant.

**28 / 28 caught.** 26 by a lockstep divergence, which names the instruction and field. The other
two, M7 (`mret` does not restore MIE) and M8 (interrupt priority swapped), are caught by a
program's own self-check while lockstep matches: both change *whether* an interrupt is taken, and
the comparison cannot see an interrupt that never happens (see Limitations).

Lockstep catches most mutants with far more programs than rv32i-soc-asic's self-checking suite,
because any wrong value anywhere is a failure, not only values a test checks: for example, M15
(write-back advancing during a stall) by 279 of 319 programs, which rv32i-soc-asic's suite catches
only in its artificial random-stall mode, and M16 (`csrrc` as `csrrs`) by 191. Its weak spots are
M5 (`jalr` keeping bit 0, 2 programs) and the `fence.i` mutants F7 and F8 (1 program each, the
directed test). The two suites run different program sets on different harnesses, so the counts
are not a like-for-like score.

## What lockstep found

Running rv32i-soc-asic in lockstep found four problems in it, all fixed there, each with a test:

- **`fence.i` executed stale instructions (an RTL bug, a Zifencei violation).** The fetch
  front-end reads ahead (two reads in flight and a three-entry queue), and `fence.i` was
  decoded as a no-op, so code
  stored just ahead of the fetch point and then entered by falling through ran the old
  instructions. riscv-tests' own `fence_i` test jumps to its new code, and a jump already
  flushes the front-end, so it could not see the bug. The directed `fence_i.S` passed on Spike and
  the reference model and diverged on the RTL at retirement #55. rv32i-soc-asic now redirects on
  `fence.i`, or replays it when a store is still in the memory stage, and keeps `fence_i.S` and
  two mutants as regression guards.
- **The startup code left x1–x31 uninitialized.** The register file has no reset (legal and
  cheaper), and `main`'s prologue saved registers nothing had written. With randomized initial
  state, every seed diverged at the same store. Invisible to rv32i-soc-asic's own tests: Verilator
  zero-initializes and riscv-tests zero the registers themselves. The startup code now zeroes
  them.
- **Firmware could never exit on Spike.** The linker script defined `fromhost` with `PROVIDE`,
  which defines nothing when nothing references it, and Spike's host interface needs both
  `tohost` and `fromhost`. RTL simulation was unaffected. Fixed with plain assignments.
- **One of rv32i-soc-asic's mutants had stopped applying** after an RTL rewrite, so its "all
  mutants caught" no longer held. It was replaced.

One timing observation, not a bug: a store to the timer's MSIP register appears in `mip` one
instruction late, which no specification bounds. The SoC-register test polls instead of assuming
the next instruction sees it.

## Limitations

- A **missing** interrupt is not detected by the comparison, only a wrong one: the reference model
  does not time devices, so it cannot know when an interrupt line rose. Interrupt tests use bounded
  waits and self-checks for that.
- The SoC is simulated with its flop memory, which models the SRAM macro's timing, not with the
  SRAM's own behavioural model.
- Mutation results are claims about those mutants only, not about all possible bugs.
