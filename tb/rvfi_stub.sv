// rvfi_stub.sv — harness self-test only. Same ports as soc_top under `ifdef RVFI`,
// replays three fixed retirements (addi, sb, tohost store) so the C++ harness and the
// dump parser can be checked before the real core exists. Not part of any design.
module rvfi_stub (
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
  output logic [31:0] rvfi_mem_wdata
);
  logic [2:0] step;
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) step <= '0; else if (step != 3'd3) step <= step + 3'd1;

  always_comb begin
    rvfi_valid = 1'b0; rvfi_order = 64'(step); rvfi_insn = '0; rvfi_trap = 1'b0;
    rvfi_pc_rdata = 32'h8000_0000 + 32'(step) * 4; rvfi_pc_wdata = rvfi_pc_rdata + 4;
    rvfi_rd_addr = '0; rvfi_rd_wdata = '0; rvfi_mem_addr = '0;
    rvfi_mem_rmask = '0; rvfi_mem_wmask = '0; rvfi_mem_rdata = '0; rvfi_mem_wdata = '0;
    case (step)
      3'd0: begin rvfi_valid = 1; rvfi_insn = 32'h02a00513;          // addi a0,x0,42
                  rvfi_rd_addr = 5'd10; rvfi_rd_wdata = 32'd42; end
      3'd1: begin rvfi_valid = 1; rvfi_insn = 32'h00a100a3;          // sb a0,1(sp)
                  rvfi_mem_addr = 32'h8000_1000; rvfi_mem_wmask = 4'b0010;  // word-aligned
                  rvfi_mem_wdata = 32'h0000_2a00; end                // lane-aligned
      3'd2: begin rvfi_valid = 1; rvfi_insn = 32'h00a2a023;          // sw a0,0(t0)
                  rvfi_mem_addr = 32'h8000_0300; rvfi_mem_wmask = 4'b1111;
                  rvfi_mem_wdata = 32'h1; end                        // tohost: pass
      default: ;
    endcase
  end
endmodule
