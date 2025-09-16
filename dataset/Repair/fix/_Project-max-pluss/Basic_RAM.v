module Basic_RAM (
    data,
    wraddress,
    rdaddress,
    wren,
    q);

    input    [15:0]  data;
    input    [11:0]  wraddress;
    input    [11:0]  rdaddress;
    input            wren;
    output   [15:0]  q;

    // Memory array declaration
    reg [15:0] mem [0:4095];  // 2^12 = 4096 locations of 16 bits each

    // Continuous assignment for output
    assign q = mem[rdaddress];

    // Write operation
    always @(*) begin
        if (wren) begin
            mem[wraddress] = data;
        end
    end

    // Initialize memory to zeros (optional)
    integer i;
    initial begin
        for (i = 0; i < 4096; i = i + 1) begin
            mem[i] = 16'b0;
        end
    end

endmodule