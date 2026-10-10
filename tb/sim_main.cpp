// sim_main.cpp — Verilator driver for the RVFI-equipped SoC model (tb/cosim_top.sv)
// and for the harness self-test stub (tb/rvfi_stub.sv).
//
// Drives clock and reset, writes one line per retirement from the rvfi_* ports, and
// stops on a tohost store or a cycle limit. Traps do NOT stop the run: they are
// ordinary control flow (riscv-tests' rv32mi suite traps on purpose) and are compared
// record by record like everything else. The dump format is what
// cosim/trace.py:read_rvfi() parses:
//   order pc insn trap rd_addr rd_wdata mem_addr mem_rmask mem_wmask mem_wdata cycle pc_wdata
// cycle = rising edges since reset release, before the one that retires the record;
// tb/tb_icarus.sv writes the identical format, so the two dumps can be diffed.
//
// Usage: ./Vcosim_top +hex=prog.hex +tohost=80001000 [+rvfi=rvfi.txt]
//                     [+max_cycles=<hex>] [+uart_div=<decimal>] [+cov=<file.dat>]
// Loading the image is cosim_top's job (+hex); this driver never touches memory.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include "verilated.h"
#include VTOP_HEADER            // defined by the Makefile, e.g. "Vcosim_top.h"

static const char* plusarg(VerilatedContext* ctx, const char* name) {
  std::string key = std::string(name) + "=";
  const char* v = ctx->commandArgsPlusMatch(key.c_str());
  return (v && *v) ? v + key.size() + 1 : nullptr;     // skip "+name="
}

#ifdef HAS_UART
// 8N1 receiver on uart_tx, sampled once per clock: start bit on a falling edge,
// then each bit in the middle of its DIV-clock window.
struct UartRx {
  int div = 16, phase = -1, bit = 0, prev = 1;
  uint8_t shift = 0;
  std::string text;
  void clock(int tx) {
    if (phase < 0) {
      if (prev == 1 && tx == 0) { phase = div + div / 2; bit = 0; shift = 0; }
    } else if (--phase == 0) {
      if (bit < 8) { shift |= (uint8_t)(tx << bit); bit++; phase = div; }
      else { if (tx) text.push_back((char)shift); phase = -1; }   // stop bit must be 1
    }
    prev = tx;
  }
};
#endif

int main(int argc, char** argv) {
  auto ctx = std::make_unique<VerilatedContext>();
  ctx->commandArgs(argc, argv);
  auto top = std::make_unique<VTOP>(ctx.get());

  const char* rvfi_arg = plusarg(ctx.get(), "rvfi");
  std::string rvfi_path = rvfi_arg ? rvfi_arg : "rvfi.txt";
  FILE* out = std::fopen(rvfi_path.c_str(), "w");
  if (!out) { std::perror(rvfi_path.c_str()); return 2; }
  std::fprintf(out, "# order pc insn trap rd_addr rd_wdata mem_addr mem_rmask mem_wmask mem_wdata cycle pc_wdata\n");

  const char* a;
  const uint64_t tohost = (a = plusarg(ctx.get(), "tohost")) ? std::strtoull(a, nullptr, 16) : 0;
  const uint64_t max_cycles =
      (a = plusarg(ctx.get(), "max_cycles")) ? std::strtoull(a, nullptr, 16) : 0x400000;
#ifdef HAS_UART
  UartRx uart;
  if ((a = plusarg(ctx.get(), "uart_div"))) uart.div = std::atoi(a);
#endif
  int exit_code = -1;
  uint64_t retired = 0, traps = 0, cycle = 0;

  top->clk = 0;
  top->rst_n = 0;
  for (int i = 0; i < 10; i++) { top->clk ^= 1; top->eval(); }   // 5 cycles in reset
  top->rst_n = 1;

  // Sample RVFI while clk is low, i.e. before the rising edge that retires the
  // instruction and advances the pipeline. Sampling after the edge loses the first
  // retirement and misaligns every record after it. `cycle` counts rising edges after
  // reset release, including the one that retires the tohost store.
  while (cycle < max_cycles && exit_code < 0) {
    top->clk = 0; top->eval();
    if (top->rvfi_valid) {
      std::fprintf(out, "%llu %08x %08x %d %02x %08x %08x %x %x %08x %llu %08x",
                   (unsigned long long)top->rvfi_order, top->rvfi_pc_rdata,
                   top->rvfi_insn, top->rvfi_trap, top->rvfi_rd_addr,
                   top->rvfi_rd_wdata, top->rvfi_mem_addr, top->rvfi_mem_rmask,
                   top->rvfi_mem_wmask, top->rvfi_mem_wdata,
                   (unsigned long long)cycle, top->rvfi_pc_wdata);
#ifdef RVFI_INTR
      // interrupt side band: columns 13-15 (intr mcause mip)
      std::fprintf(out, " %d %08x %08x", top->rvfi_intr, top->rvfi_ext_mcause,
                   top->rvfi_ext_mip);
#endif
      std::fputc('\n', out);
      retired++;
      traps += top->rvfi_trap;
      if (tohost && top->rvfi_mem_wmask && top->rvfi_mem_addr == tohost
          && (top->rvfi_mem_wdata & 1))
        exit_code = (int)(top->rvfi_mem_wdata >> 1);
    }
#ifdef HAS_UART
    uart.clock(top->uart_tx);
#endif
    top->clk = 1; top->eval();
    cycle++;
  }
  std::fclose(out);
  top->final();
#if VM_COVERAGE
  // coverage build (make coverage): one .dat per run, merged by verilator_coverage
  if (const char* cov = plusarg(ctx.get(), "cov")) ctx->coveragep()->write(cov);
#endif

#ifdef HAS_UART
  if (!uart.text.empty()) {
    std::printf("uart: ");
    for (char c : uart.text)
      if (c == '\n') std::printf("\\n"); else if (c != '\r') std::putchar(c);
    std::printf("\n");
  }
#endif
  if (exit_code < 0) {
    std::printf("RTL: TIMEOUT after %llu cycles, %llu retired\n",
                (unsigned long long)cycle, (unsigned long long)retired);
    return 2;
  }
  std::printf("RTL: %s, %llu instructions retired (%llu traps) in %llu cycles (CPI %.3f)\n",
              exit_code == 0 ? "PASS" : "FAIL", (unsigned long long)retired,
              (unsigned long long)traps, (unsigned long long)cycle,
              retired ? (double)cycle / retired : 0.0);
  if (exit_code != 0) std::printf("RTL: exit code %d (0x%x)\n", exit_code, exit_code);
  return exit_code == 0 ? 0 : 1;
}
