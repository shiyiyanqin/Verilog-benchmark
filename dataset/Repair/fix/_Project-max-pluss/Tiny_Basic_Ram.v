// Replaced lpm_ram_dp megafunction with standard Verilog implementation
// since Icarus Verilog doesn't support Altera-specific primitives

module Tiny_Basic_Ram (
    data,
    wraddress,
    rdaddress,
    wren,
    q);

    input   [3:0]  data;
    input   [3:0]  wraddress;
    input   [3:0]  rdaddress;
    input     wren;
    output  [3:0]  q;

    // Memory array (16 locations x 4 bits)
    reg [3:0] mem [0:15];
    
    // Output register
    reg [3:0] q_reg;
    
    // Continuous assignment for output
    assign q = q_reg;
    
    // Write operation (synchronous)
    always @(*) begin
        if (wren) begin
            mem[wraddress] <= data;
        end
    end
    
    // Read operation (asynchronous)
    always @(*) begin
        q_reg = mem[rdaddress];
    end

endmodule