`timescale 1ns/1ps
// tb_icarus.sv — Icarus Verilog twin of tb/sim_main.cpp, for cross-simulator checks.
//
// Same wrapper (cosim_top: soc_top + memory load), same stimulus (5 clocks in reset,
// reset released while clk is low), same sampling point (clk low, before the rising
// edge that retires the record) and the same dump format, cycle column included. A
// correct design gives byte-identical dumps under Icarus and Verilator; the first
// differing line is the first instruction whose retirement, value or timing differs.
//
// Plusargs: +hex=<file> (read by cosim_top) +tohost=<hex> +rvfi=<file> +max_cycles=<dec>
module tb_icarus;
  logic        clk = 1'b0, rst_n = 1'b0;
  logic        rvfi_valid, rvfi_trap, uart_tx;
  logic [63:0] rvfi_order;
  logic [31:0] rvfi_insn, rvfi_pc_rdata, rvfi_pc_wdata, rvfi_rd_wdata;
  logic [31:0] rvfi_mem_addr, rvfi_mem_rdata, rvfi_mem_wdata;
  logic [4:0]  rvfi_rd_addr;
  logic [3:0]  rvfi_mem_rmask, rvfi_mem_wmask;
`ifdef RVFI_INTR
  logic        rvfi_intr;
  logic [31:0] rvfi_ext_mcause, rvfi_ext_mip;
`endif

  cosim_top dut (.*);

  string       path;
  int          fd, max_cycles;
  logic [31:0] tohost;
  longint      cycle = 0, retired = 0;

  initial begin
    if (!$value$plusargs("rvfi=%s", path)) path = "rvfi_icarus.txt";
    if (!$value$plusargs("tohost=%h", tohost)) tohost = '0;
    if (!$value$plusargs("max_cycles=%d", max_cycles)) max_cycles = 4194304;
    fd = $fopen(path, "w");
    $fdisplay(fd, "# order pc insn trap rd_addr rd_wdata mem_addr mem_rmask mem_wmask mem_wdata cycle pc_wdata");
    repeat (5) begin #5 clk = 1'b1; #5 clk = 1'b0; end     // 5 cycles in reset
    rst_n = 1'b1;
    forever begin
      #1;                                                  // clk low, settled
      if (rvfi_valid) begin
`ifdef RVFI_INTR
        $fdisplay(fd, "%0d %08h %08h %0d %02h %08h %08h %0h %0h %08h %0d %08h %0d %08h %08h",
                  rvfi_order, rvfi_pc_rdata, rvfi_insn, rvfi_trap, rvfi_rd_addr,
                  rvfi_rd_wdata, rvfi_mem_addr, rvfi_mem_rmask, rvfi_mem_wmask,
                  rvfi_mem_wdata, cycle, rvfi_pc_wdata,
                  rvfi_intr, rvfi_ext_mcause, rvfi_ext_mip);
`else
        $fdisplay(fd, "%0d %08h %08h %0d %02h %08h %08h %0h %0h %08h %0d %08h",
                  rvfi_order, rvfi_pc_rdata, rvfi_insn, rvfi_trap, rvfi_rd_addr,
                  rvfi_rd_wdata, rvfi_mem_addr, rvfi_mem_rmask, rvfi_mem_wmask,
                  rvfi_mem_wdata, cycle, rvfi_pc_wdata);
`endif
        retired++;
        if (tohost != 0 && rvfi_mem_wmask != 0 && rvfi_mem_addr == tohost && rvfi_mem_wdata[0]) begin
          $display("ICARUS: exit code %0d, %0d retired in %0d cycles",
                   rvfi_mem_wdata >> 1, retired, cycle + 1);
          $fclose(fd);
          $finish;
        end
      end
      #4 clk = 1'b1;
      cycle++;
      if (cycle >= max_cycles) begin
        $display("ICARUS: TIMEOUT after %0d cycles, %0d retired", cycle, retired);
        $fclose(fd);
        $finish;
      end
      #5 clk = 1'b0;
    end
  end
endmodule
