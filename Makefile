# rv32i-soc-validation — lockstep co-simulation of rv32i-soc-asic
# Without the RTL:
#   make verify         fw, iss, lockstep-test, mutate, harness-test (+ Spike checks)
#   make fw             build the firmware tests into build/
#   make iss            firmware tests and rv32i-soc-asic's sw/main.c on the ISS alone
#   make spike-check    ISS vs Spike on test_isa + random programs
#   make riscv-tests    ISS vs Spike on the 55 riscv-tests the core runs
#   make lockstep-test  guided lockstep vs a fake RTL (slow accel, CPI 2, other marchid)
#   make mutate         9 ISS mutants; lockstep must catch every one
#   make harness-test   Verilator harness against the RVFI stub
# Against rv32i-soc-asic's RTL (SOC=, default ../rv32i-soc-asic):
#   make cosim / cosim-all / directed / stress   lockstep regressions (SEED=n randomizes)
#   make xcheck         Icarus vs Verilator, same programs, cycle for cycle
#   make bench-xcheck   rv32i-soc-asic's benchmark image on both simulators + lockstep
#   make coverage       Verilator line/toggle coverage over every program
#   make mutate-rtl     rv32i-soc-asic's RTL mutants (28) under lockstep
#   make irq-stress     random software/timer interrupts (needs the RVFI side band)
# Expected results for each: docs/verification.md.
CC      = riscv64-unknown-elf-gcc
CFLAGS  = -march=rv32i_zicsr -mabi=ilp32 -O1 -nostdlib -nostartfiles -ffreestanding \
          -Wl,--no-warn-rwx-segments -T sw/link.ld
# Spike built from source into ~/opt/spike (README); falls back to a spike on PATH
SPIKE  ?= $(firstword $(wildcard $(HOME)/opt/spike/bin/spike) spike)
export SPIKE
# Spike configured to model the rv32i-soc-asic core exactly: M-mode only, no PMP, no triggers.
# Zicntr is required: without it Spike traps on counter reads (docs/reports/spike_isa_string.txt)
SPIKE_FLAGS = --isa=rv32i_zicsr_zicntr_zifencei --priv=M --pmpregions=0 --triggers=0
N_RANDOM ?= 20
SOC    ?= ../rv32i-soc-asic
B = build

.SECONDARY:
SHELL := /bin/bash
.PHONY: verify fw iss spike-check riscv-tests lockstep-test mutate harness-test cosim cosim-build cosim-all xcheck directed directed-elfs stress irq-stress coverage mutate-rtl bench-xcheck clean
verify: fw iss lockstep-test mutate harness-test
	@if command -v $(SPIKE) >/dev/null; then \
	   $(MAKE) --no-print-directory spike-check && $(MAKE) --no-print-directory riscv-tests; \
	 else echo "(spike not installed: skipping spike-check and riscv-tests)"; fi
	@echo "== verify: done =="

$(B):
	@mkdir -p $(B)

fw: $(B)/test_isa.elf $(B)/test_accel.elf $(B)/test_counters.elf $(B)/test_soc_map.elf
$(B)/%.elf: sw/%.c sw/crt0.S sw/link.ld sw/soc.h | $(B)
	@$(CC) $(CFLAGS) -Isw -o $@ sw/crt0.S $< -lgcc

iss: fw $(B)/p2_main.elf
	@python3 -m cosim.run iss $(B)/test_isa.elf
	@python3 -m cosim.run iss $(B)/test_accel.elf
	@python3 -m cosim.run iss $(B)/test_soc_map.elf
	@python3 -m cosim.run iss $(B)/p2_main.elf

# rv32i-soc-asic's benchmark firmware, built with rv32i-soc-asic's flags (its scripts/build_fw.sh)
$(B)/p2_main.elf: $(SOC)/sw/main.c sw/crt0.S sw/link.ld sw/soc.h | $(B)
	@$(CC) -march=rv32i_zicsr -mabi=ilp32 -O2 -Wall -Wextra -Werror -ffreestanding \
	  -fno-builtin -nostdlib -nostartfiles -Isw -T sw/link.ld -Wl,--no-warn-rwx-segments \
	  sw/crt0.S $< -o $@ -lgcc

$(B)/rand%.S: | $(B)
	@python3 -m cosim.randgen $* -o $@
$(B)/rand%.elf: $(B)/rand%.S sw/crt0.S sw/link.ld
	@$(CC) $(CFLAGS) -o $@ sw/crt0.S $<

RANDOM_ELFS = $(foreach s,$(shell seq 1 $(N_RANDOM)),$(B)/rand$(s).elf)

spike-check: fw $(RANDOM_ELFS)
	@for e in $(B)/test_isa.elf $(RANDOM_ELFS); do \
	  printf "%-24s " $$(basename $$e); \
	  timeout 20 $(SPIKE) $(SPIKE_FLAGS) --log-commits $$e 2> $$e.spike.log; rc=$$?; \
	  if [ $$rc -eq 124 ]; then echo "spike never exited (no tohost/fromhost?)"; \
	    head -3 $$e.spike.log; rm -f $$e.spike.log; exit 1; fi; \
	  [ $$rc -eq 0 ] || { echo "program failed on spike, exit $$rc"; exit 1; }; \
	  out=$$(python3 -m cosim.run cmp-spike $$e $$e.spike.log); rc=$$?; \
	  echo "$$out" | head -1; [ $$rc -eq 0 ] || { echo "$$out"; exit 1; }; \
	done

mutate: fw directed-elfs
	@python3 scripts/mutate_iss.py

lockstep-test: fw
	@python3 scripts/test_lockstep.py

# riscv-tests: clone riscv-software-src/riscv-tests (+ env submodule) to RT_SRC
RT_SRC ?= $(HOME)/src/riscv-tests
RT_OUT  = $(B)/riscv-tests
RT_CC   = $(CC) -march=rv32i_zicsr_zifencei -mabi=ilp32 -static -mcmodel=medany \
          -fvisibility=hidden -nostdlib -nostartfiles -Wl,--no-warn-rwx-segments \
          -I$(RT_SRC)/env/p -I$(RT_SRC)/isa/macros/scalar -T$(RT_SRC)/env/p/link.ld
riscv-tests:
	@test -d $(RT_SRC)/isa || { echo "clone riscv-tests to $(RT_SRC) (with the env submodule)"; exit 1; }
	@mkdir -p $(RT_OUT)
	@for s in $(RT_SRC)/isa/rv32ui/*.S $(RT_SRC)/isa/rv32mi/*.S; do \
	  t=$$(basename $$s .S); suite=$$(basename $$(dirname $$s)); \
	  $(RT_CC) $$s -o $(RT_OUT)/$$suite-p-$$t || exit 1; done
	@set -o pipefail; python3 scripts/riscv_tests.py $(RT_OUT) | tail -1

harness-test: | $(B)
	@rm -rf $(B)/stub
	@verilator --cc --exe --build -j 1 -Wall -Wno-UNUSEDSIGNAL --top-module rvfi_stub \
	  -CFLAGS '-DVTOP=Vrvfi_stub -DVTOP_HEADER=\"Vrvfi_stub.h\"' -Mdir $(B)/stub \
	  $(abspath tb/rvfi_stub.sv) $(abspath tb/sim_main.cpp) > $(B)/stub.log 2>&1
	@$(B)/stub/Vrvfi_stub +tohost=80000300 +rvfi=$(B)/stub_rvfi.txt
	@python3 -c "from cosim.trace import read_rvfi; \
	  r=list(read_rvfi(open('$(B)/stub_rvfi.txt'))); \
	  assert len(r)==3 and r[1].store_bytes==1 and r[1].store_data==0x2a, r; \
	  assert r[1].mem_addr==0x80001001, hex(r[1].mem_addr); \
	  print('harness: RVFI dump parses, 3 records, word-aligned byte store normalized')"

# ---- lockstep against rv32i-soc-asic's RTL ----------------------------------------
# Verilator model of soc_top (flop memory, -DRVFI) inside tb/cosim_top.sv.
# --x-assign/--x-initial unique: X and uninitialized state become values chosen at run
# time; cosim.rtl --seed N randomizes them, so an X dependence shows up as a seed-
# dependent result.
SOC_RTL   = $(wildcard $(SOC)/rtl/core/*.sv $(SOC)/rtl/accel/*.sv $(SOC)/rtl/bus/*.sv \
                       $(SOC)/rtl/periph/*.sv $(SOC)/rtl/mem/*.sv) $(SOC)/rtl/soc_top.sv
COSIM_SIM = $(B)/cosim/Vcosim_top
COSIM_ELFS ?= $(B)/test_isa.elf
SEED      ?=
# Interrupt side band: rvfi_intr / rvfi_ext_mcause / rvfi_ext_mip, part of rv32i-soc-asic's
# RVFI port.
# Detected from soc_top.sv, so a SoC without it still builds; programs that take
# interrupts are then compared up to their first interrupt (--interrupts).
INTR_DEF := $(if $(shell grep -l rvfi_intr $(SOC)/rtl/soc_top.sv 2>/dev/null),-DRVFI_INTR)

$(COSIM_SIM): $(SOC_RTL) tb/cosim_top.sv tb/sim_main.cpp | $(B)
	@test -f $(SOC)/rtl/soc_top.sv || { echo "no $(SOC)/rtl/soc_top.sv (set SOC=)"; exit 1; }
	@rm -rf $(B)/cosim
	@verilator --cc --exe --build -j 8 -Wall -DRVFI $(INTR_DEF) --top-module cosim_top \
	  --x-assign unique --x-initial unique \
	  -CFLAGS '-O1 -DVTOP=Vcosim_top -DVTOP_HEADER=\"Vcosim_top.h\" -DHAS_UART $(INTR_DEF)' \
	  -Mdir $(B)/cosim $(abspath $(SOC_RTL) tb/cosim_top.sv tb/sim_main.cpp) \
	  > $(B)/cosim.log 2>&1 || { tail -30 $(B)/cosim.log; exit 1; }
	@echo "verilated soc_top (-DRVFI $(INTR_DEF), USE_SRAM=0, MEM_WORDS=16384): $(COSIM_SIM)"

cosim-build: $(COSIM_SIM)

cosim: fw $(COSIM_SIM)
	@python3 -m cosim.rtl --sim $(COSIM_SIM) --keep $(B)/cosim/run \
	  $(if $(SEED),--seed $(SEED)) $(COSIM_ELFS)

# lockstep regression: every program, in lockstep on the RTL
#   55 riscv-tests (same exclusions as rv32i-soc-asic) + rv32i-soc-asic's SoC directed test
#   + firmware (test_isa, test_accel, test_counters, rv32i-soc-asic's main.c) + random programs
N_COSIM ?= 50
RT_EXCL   = %-ma_data %-breakpoint %-pmpaddr
SOC_ASM   = $(B)/soc_smoke.elf
$(SOC_ASM): $(SOC)/tb/asm_soc/soc_smoke.S | $(B)
	@$(CC) -march=rv32i_zicsr_zifencei -mabi=ilp32 -static -mcmodel=medany -nostdlib \
	  -nostartfiles -Wl,--no-warn-rwx-segments -I$(SOC)/tb/asm -T$(SOC)/tb/asm/link.ld $< -o $@
	@cp $(SOC)/tb/asm_soc/soc_smoke.expect $@.expect
COSIM_RAND = $(foreach s,$(shell seq 1 $(N_COSIM)),$(B)/rand$(s).elf)
cosim-all: fw riscv-tests $(B)/p2_main.elf $(SOC_ASM) $(COSIM_RAND) $(COSIM_SIM)
	@python3 -m cosim.rtl --sim $(COSIM_SIM) --keep $(B)/cosim/all $(if $(SEED),--seed $(SEED)) \
	  --interrupts $(SOC_ASM) \
	  $(filter-out $(RT_EXCL),$(sort $(wildcard $(RT_OUT)/rv32u*-p-* $(RT_OUT)/rv32m*-p-*))) \
	  $(SOC_ASM) $(B)/test_isa.elf $(B)/test_accel.elf $(B)/test_counters.elf $(B)/test_soc_map.elf \
	  $(B)/p2_main.elf $(COSIM_RAND) | tee $(B)/cosim_all.out; exit $${PIPESTATUS[0]}

# directed tests (tb/asm/*.S, main() under this repo's crt0). Each one
# runs on Spike vs the ISS first (the spec's answer), then in lockstep on the RTL.
# *_soc.S tests touch MMIO, which Spike does not model: ISS and RTL only.
DIRECTED = $(patsubst tb/asm/%.S,$(B)/d_%.elf,$(wildcard tb/asm/*.S))
$(B)/d_%.elf: tb/asm/%.S sw/crt0.S sw/link.ld | $(B)
	@$(CC) $(CFLAGS) -march=rv32i_zicsr_zifencei -o $@ sw/crt0.S $<
# Programs expected to fail because of a known, unfixed RTL bug (ELF:name). If one
# passes, cosim.rtl reports XPASS and fails, so the list cannot go stale.
XFAIL =        # none
# programs that take interrupts: full lockstep on a SoC with the side band,
# lockstep up to the first interrupt on rv32i-soc-asic as it is
IRQ_PROGS = $(B)/d_irq_soc.elf
directed-elfs: $(DIRECTED)
directed: $(DIRECTED) $(COSIM_SIM)
	@for e in $(filter-out %_soc.elf,$(DIRECTED)); do \
	  printf "spike  %-24s " $$(basename $$e); \
	  timeout 20 $(SPIKE) $(SPIKE_FLAGS) --log-commits $$e 2> $$e.spike.log; rc=$$?; \
	  [ $$rc -eq 0 ] || { echo "program FAILED on spike (exit $$rc)"; exit 1; }; \
	  out=$$(python3 -m cosim.run cmp-spike $$e $$e.spike.log); rc=$$?; \
	  echo "$$out" | head -1; [ $$rc -eq 0 ] || { echo "$$out"; exit 1; }; \
	done
	@python3 -m cosim.rtl --sim $(COSIM_SIM) --keep $(B)/cosim/directed \
	  $(if $(SEED),--seed $(SEED)) $(addprefix --xfail ,$(XFAIL)) \
	  $(addprefix --interrupts ,$(IRQ_PROGS)) $(DIRECTED)

# stress programs (cosim/randgen.py gen_stress): loops, CSR chains, traps inside
# hazard chains; mmio* adds GPIO/timer accesses (APB stalls) and so skips Spike.
N_STRESS ?= 50
STRESS_ELFS = $(foreach s,$(shell seq 1 $(N_STRESS)),$(B)/stress$(s).elf)
MMIO_ELFS   = $(foreach s,$(shell seq 1 $(N_STRESS)),$(B)/mmio$(s).elf)
# ptr* adds address-generation hazards (load/link/CSR result used as a base)
PTR_ELFS    = $(foreach s,$(shell seq 1 $(N_STRESS)),$(B)/ptr$(s).elf)
$(B)/stress%.S: cosim/randgen.py | $(B)
	@python3 -m cosim.randgen $* --stress -o $@
$(B)/mmio%.S: cosim/randgen.py | $(B)
	@python3 -m cosim.randgen $* --soc -o $@
$(B)/ptr%.S: cosim/randgen.py | $(B)
	@python3 -m cosim.randgen $* --ptr -o $@
$(B)/stress%.elf: $(B)/stress%.S sw/crt0.S sw/link.ld
	@$(CC) $(CFLAGS) -o $@ sw/crt0.S $<
$(B)/mmio%.elf: $(B)/mmio%.S sw/crt0.S sw/link.ld
	@$(CC) $(CFLAGS) -o $@ sw/crt0.S $<
$(B)/ptr%.elf: $(B)/ptr%.S sw/crt0.S sw/link.ld
	@$(CC) $(CFLAGS) -o $@ sw/crt0.S $<
stress: $(STRESS_ELFS) $(MMIO_ELFS) $(PTR_ELFS) $(COSIM_SIM)
	@n=0; for e in $(STRESS_ELFS) $(PTR_ELFS); do \
	  timeout 20 $(SPIKE) $(SPIKE_FLAGS) --log-commits $$e 2> $$e.spike.log; rc=$$?; \
	  [ $$rc -eq 0 ] || { echo "$$e FAILED on spike (exit $$rc)"; exit 1; }; \
	  out=$$(python3 -m cosim.run cmp-spike $$e $$e.spike.log) \
	    || { echo "$$(basename $$e): $$out"; exit 1; }; n=$$((n+1)); \
	done; echo "spike: $$n stress + ptr programs, ISS matches Spike"
	@set -o pipefail; python3 -m cosim.rtl --sim $(COSIM_SIM) --keep $(B)/cosim/stress \
	  $(if $(SEED),--seed $(SEED)) $(STRESS_ELFS) $(MMIO_ELFS) $(PTR_ELFS) \
	  | tail -$(if $(V),1000,1)

# random software/timer interrupts landing anywhere in stress programs. Needs a SoC
# with the RVFI interrupt side band, which rv32i-soc-asic has
IRQ_ELFS = $(foreach s,$(shell seq 1 $(N_STRESS)),$(B)/irq$(s).elf)
$(B)/irq%.S: cosim/randgen.py | $(B)
	@python3 -m cosim.randgen $* --irq -o $@
$(B)/irq%.elf: $(B)/irq%.S sw/crt0.S sw/link.ld
	@$(CC) $(CFLAGS) -o $@ sw/crt0.S $<
irq-stress: $(IRQ_ELFS) $(COSIM_SIM)
	@test -n "$(INTR_DEF)" || { echo "irq-stress needs a SOC whose soc_top has the RVFI interrupt side band"; exit 1; }
	@set -o pipefail; python3 -m cosim.rtl --sim $(COSIM_SIM) --keep $(B)/cosim/irq \
	  $(if $(SEED),--seed $(SEED)) $(IRQ_ELFS) | tail -$(if $(V),1000,1)

# RTL coverage: a separate Verilator build with line + toggle coverage (the
# memory array excluded by tb/coverage.vlt), every program of cosim-all, stress
# and directed run on it, one .dat each, merged and annotated by verilator_coverage.
COV_SIM = $(B)/cov/Vcosim_top
$(COV_SIM): $(SOC_RTL) tb/cosim_top.sv tb/sim_main.cpp tb/coverage.vlt | $(B)
	@rm -rf $(B)/cov
	@verilator --cc --exe --build -j 8 -Wall -DRVFI $(INTR_DEF) --top-module cosim_top \
	  --coverage-line --coverage-toggle \
	  -CFLAGS '-O1 -DVTOP=Vcosim_top -DVTOP_HEADER=\"Vcosim_top.h\" -DHAS_UART $(INTR_DEF)' \
	  -Mdir $(B)/cov $(abspath tb/coverage.vlt $(SOC_RTL) tb/cosim_top.sv \
	  tb/sim_main.cpp) > $(B)/cov.log 2>&1 || { tail -30 $(B)/cov.log; exit 1; }
coverage: fw riscv-tests $(B)/p2_main.elf $(SOC_ASM) $(COSIM_RAND) $(DIRECTED) \
          $(STRESS_ELFS) $(MMIO_ELFS) $(PTR_ELFS) $(COV_SIM)
	@rm -rf $(B)/cov/dat $(B)/cov/annotate && mkdir -p $(B)/cov/dat
	@set -o pipefail; python3 -m cosim.rtl --sim $(COV_SIM) --cov $(B)/cov/dat \
	  --keep $(B)/cov/run --interrupts $(SOC_ASM) $(addprefix --interrupts ,$(IRQ_PROGS)) $(addprefix --xfail ,$(XFAIL)) \
	  $(filter-out $(RT_EXCL),$(sort $(wildcard $(RT_OUT)/rv32u*-p-* $(RT_OUT)/rv32m*-p-*))) \
	  $(SOC_ASM) $(B)/test_isa.elf $(B)/test_accel.elf $(B)/test_counters.elf \
	  $(B)/test_soc_map.elf $(B)/p2_main.elf $(COSIM_RAND) $(DIRECTED) \
	  $(STRESS_ELFS) $(MMIO_ELFS) $(PTR_ELFS) | tail -1
	@verilator_coverage --write $(B)/cov/merged.dat $(B)/cov/dat/*.dat
	@verilator_coverage --annotate $(B)/cov/annotate --annotate-min 1 $(B)/cov/merged.dat \
	  | tail -3

# rv32i-soc-asic's 28 RTL mutants (its scripts/mutate_core.sh, read as data) under
# lockstep, over every program (mutate_rtl.py checks they all pass unmutated first).
# Slow: one Verilator build plus 319 programs per mutant. MUT="M1 F3" for a subset.
MUT ?=
mutate-rtl: fw riscv-tests $(B)/p2_main.elf $(SOC_ASM) $(COSIM_RAND) $(DIRECTED) \
            $(STRESS_ELFS) $(MMIO_ELFS) $(PTR_ELFS) $(if $(INTR_DEF),$(IRQ_ELFS))
	@mkdir -p $(B)/mut
	@printf "%s\n" $(filter-out $(RT_EXCL),$(sort $(wildcard $(RT_OUT)/rv32u*-p-* $(RT_OUT)/rv32m*-p-*))) \
	  $(DIRECTED) $(B)/test_isa.elf $(B)/test_accel.elf \
	  $(B)/test_counters.elf $(B)/test_soc_map.elf $(B)/p2_main.elf $(SOC_ASM) \
	  $(COSIM_RAND) $(STRESS_ELFS) $(MMIO_ELFS) $(PTR_ELFS) $(if $(INTR_DEF),$(IRQ_ELFS)) > $(B)/mut/programs.txt
	@set -o pipefail; python3 scripts/mutate_rtl.py $(SOC) $(B)/mut/programs.txt $(MUT) \
	  | tee $(B)/mut/lockstep.txt

# rv32i-soc-asic's benchmark, its exact image (its crt0.S, link.ld, main.c and the
# flags of its scripts/build_fw.sh), run on its own Icarus tb_soc and on this repo's
# Verilator model; the UART reports must be identical, and the Verilator run is also
# checked instruction by instruction in lockstep. rv32i-soc-asic is only read.
BENCH = $(B)/bench
$(BENCH)/p2_bench.elf: $(SOC)/sw/main.c $(SOC)/sw/crt0.S $(SOC)/sw/link.ld $(SOC)/sw/soc.h | $(B)
	@mkdir -p $(BENCH)
	@$(CC) -march=rv32i_zicsr -mabi=ilp32 -O2 -Wall -Wextra -Werror -ffreestanding \
	  -fno-builtin -nostdlib -nostartfiles -I$(SOC)/sw -T $(SOC)/sw/link.ld \
	  -Wl,--no-warn-rwx-segments $(SOC)/sw/crt0.S $(SOC)/sw/main.c -o $@ -lgcc
$(BENCH)/tb_soc: $(SOC_RTL) $(SOC)/tb/tb_soc.sv | $(B)
	@mkdir -p $(BENCH)
	@iverilog -g2012 -I $(SOC)/tb -s tb_soc -o $@ $(SOC_RTL) $(SOC)/tb/tb_soc.sv \
	  2> $(BENCH)/iv.log || { grep -v "constant selects" $(BENCH)/iv.log; exit 1; }
bench-xcheck: $(BENCH)/p2_bench.elf $(BENCH)/tb_soc $(COSIM_SIM)
	@python3 scripts/bench_xcheck.py $(SOC) $(BENCH)/p2_bench.elf $(BENCH)/tb_soc $(COSIM_SIM)

# Icarus vs Verilator: every program of cosim-all again, under Icarus, through the same
# cosim_top wrapper (tb/tb_icarus.sv mirrors sim_main.cpp). The RVFI dumps must be
# identical, cycle column included (scripts/icarus_xcheck.py).
ICARUS_TB = $(B)/icarus/tb_icarus
$(ICARUS_TB): $(SOC_RTL) tb/cosim_top.sv tb/tb_icarus.sv | $(B)
	@mkdir -p $(B)/icarus
	@iverilog -g2012 -DRVFI $(INTR_DEF) -s tb_icarus -o $@ tb/tb_icarus.sv $(SOC_RTL) \
	  tb/cosim_top.sv 2> $(B)/icarus/iv.log \
	  || { grep -v "constant selects" $(B)/icarus/iv.log; exit 1; }
xcheck: $(ICARUS_TB)
	@test -d $(B)/cosim/all || { echo "run make cosim-all first"; exit 1; }
	@python3 scripts/icarus_xcheck.py $(B)/cosim/all $(ICARUS_TB) \
	  $(filter-out $(RT_EXCL),$(sort $(wildcard $(RT_OUT)/rv32u*-p-* $(RT_OUT)/rv32m*-p-*))) \
	  $(SOC_ASM) $(B)/test_isa.elf $(B)/test_accel.elf $(B)/test_counters.elf $(B)/test_soc_map.elf \
	  $(B)/p2_main.elf $(COSIM_RAND)

clean:
	rm -rf $(B)
