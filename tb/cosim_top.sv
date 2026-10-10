// cosim_top.sv — Verilator wrapper around rv32i-soc-asic's soc_top, built with -DRVFI.
//
// Wires the SoC the way rv32i-soc-asic's tb_soc does (UART rx <- tx, GPIO in <- out), so a
// program behaves identically under Icarus tb_soc and under this model, and loads the
// firmware image the way tb_soc does: zero the flop memory, then $readmemh into it
// through a hierarchical reference. No rv32i-soc-asic RTL is modified. Needs
// the flop build (USE_SRAM=0); the OpenRAM model is not a Verilator target.
//
// Plusargs: +hex=<file>   word image, one 32-bit word per line, '@<word index>' markers
module cosim_top #(
  parameter int MEM_WORDS = 16384,              // 64 KB, matching sw/link.ld and tb_soc
  parameter int UART_DIV  = 16,                 // tb_soc's value
  parameter int GPIO_W    = 8
)(
  input  logic        clk,
  input  logic        rst_n,
  output logic        rvfi_valid,
  output logic [63:0] rvfi_order,
  output logic [31:0] rvfi_insn,
  output logic        rvfi_trap,
  output logic [31:0] rvfi_pc_rdata,
  output logic [31:0] rvfi_pc_wdata,
  output logic [4:0]  rvfi_rd_addr,
  output logic [31:0] rvfi_rd_wdata,
  output logic [31:0] rvfi_mem_addr,
  output logic [3:0]  rvfi_mem_rmask,
  output logic [3:0]  rvfi_mem_wmask,
  output logic [31:0] rvfi_mem_rdata,
  output logic [31:0] rvfi_mem_wdata,
`ifdef RVFI_INTR
  // only when the SoC has the interrupt side band (rvfi_intr, rvfi_ext_*)
  output logic        rvfi_intr,
  output logic [31:0] rvfi_ext_mcause,
  output logic [31:0] rvfi_ext_mip,
`endif
  output logic        uart_tx
);
  logic [GPIO_W-1:0] gpio;

  soc_top #(.MEM_WORDS(MEM_WORDS), .USE_SRAM(0), .UART_DIV(UART_DIV), .GPIO_W(GPIO_W)) dut (
    .clk(clk), .rst_n(rst_n),
    .uart_tx(uart_tx), .uart_rx(uart_tx), .gpio_out(gpio), .gpio_in(gpio),
    .rvfi_valid(rvfi_valid), .rvfi_order(rvfi_order), .rvfi_insn(rvfi_insn),
    .rvfi_trap(rvfi_trap), .rvfi_pc_rdata(rvfi_pc_rdata), .rvfi_pc_wdata(rvfi_pc_wdata),
    .rvfi_rd_addr(rvfi_rd_addr), .rvfi_rd_wdata(rvfi_rd_wdata),
    .rvfi_mem_addr(rvfi_mem_addr), .rvfi_mem_rmask(rvfi_mem_rmask),
    .rvfi_mem_wmask(rvfi_mem_wmask), .rvfi_mem_rdata(rvfi_mem_rdata),
    .rvfi_mem_wdata(rvfi_mem_wdata)
`ifdef RVFI_INTR
    , .rvfi_intr(rvfi_intr), .rvfi_ext_mcause(rvfi_ext_mcause), .rvfi_ext_mip(rvfi_ext_mip)
`endif
  );

  string hexfile;
  initial begin
    if (!$value$plusargs("hex=%s", hexfile)) begin
      $display("cosim_top: no +hex=<file>");
      $finish;
    end
    for (int i = 0; i < MEM_WORDS; i++) dut.u_mem.g_flops.mem[i] = 32'h0;
    $readmemh(hexfile, dut.u_mem.g_flops.mem);
  end
endmodule
