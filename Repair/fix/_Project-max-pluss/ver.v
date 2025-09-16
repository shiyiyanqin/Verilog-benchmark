module ver (
    data,
    wrreq,
    rdreq,
    clock,
    q,
    full,
    empty,
    usedw);

    input    [7:0]  data;
    input      wrreq;
    input      rdreq;
    input      clock;
    output    [7:0]  q;
    output      full;
    output      empty;
    output    [7:0]  usedw;

    // Internal signals
    reg [7:0] write_ptr = 0;
    reg [7:0] read_ptr = 0;
    reg [7:0] count = 0;
    wire [7:0] ram_q;
    
    // FIFO status signals
    assign full = (count == 255);
    assign empty = (count == 0);
    assign usedw = count;
    
    // RAM instance
    Basic_RAM ram (
        .data(data),
        .wraddress(write_ptr),
        .rdaddress(read_ptr),
        .wren(wrreq & ~full),
        .q(ram_q)
    );
    
    // Output register for synchronous read
    reg [7:0] q_reg;
    assign q = q_reg;
    
    // FIFO control logic
    always @(posedge clock) begin
        // Write operation
        if (wrreq && !full) begin
            write_ptr <= write_ptr + 1;
            count <= count + 1;
        end
        
        // Read operation
        if (rdreq && !empty) begin
            read_ptr <= read_ptr + 1;
            q_reg <= ram_q;
            count <= count - 1;
        end
        
        // Simultaneous read and write
        if (wrreq && rdreq && !empty && !full) begin
            write_ptr <= write_ptr + 1;
            read_ptr <= read_ptr + 1;
            q_reg <= ram_q;
            // Count remains the same
        end
    end

endmodule