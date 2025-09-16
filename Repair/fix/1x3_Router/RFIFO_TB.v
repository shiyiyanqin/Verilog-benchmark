module router_fifo_tb();
parameter DELAY=10;
parameter DEPTH=16, WIDTH=9, ADD_SIZE=5;
reg clock, resetn, write_enb, read_enb, lfd_state, soft_reset;
reg [7:0]data_in;
wire full, empty;	
wire [7:0]data_out;
integer i;

// Instantiate router_reg which contains the FIFO interface
// Mapping testbench signals to router_reg ports
wire detect_add, ld_state, laf_state, full_state, rst_int_reg;
wire parity_done, low_packet_valid;

router_reg DUT(
    .clock(clock),
    .resetn(resetn),
    .pkt_valid(write_enb),  // Using pkt_valid as write enable
    .data_in(data_in),
    .fifo_full(full),
    .detect_add(1'b0),      // Not testing address detection
    .ld_state(1'b0),        // Not testing load state
    .laf_state(1'b0),       // Not testing look ahead state
    .full_state(1'b0),      // Not testing full state
    .lfd_state(lfd_state),
    .rst_int_reg(soft_reset), // Mapping soft_reset to internal reset
    .err(),                 // Not monitoring error
    .parity_done(parity_done),
    .low_packet_valid(low_packet_valid),
    .dout(data_out)
);

// Assign full/empty based on router_reg outputs
assign full = (data_out == 8'hFF);  // Simplified full condition
assign empty = (data_out == 8'h00); // Simplified empty condition

always	//clock generation
begin
    clock=1'b0;
    #(DELAY/2);
    clock=1'b1;
    #(DELAY/2);
end

task initialize;	//initialization
begin
    write_enb =1'b0;
    soft_reset=1'b0;
    read_enb =1'b0;
    data_in=0;
    lfd_state = 1'b0;
end
endtask

task rst;	//resetting
begin
    resetn=1'b0;
    @(negedge clock);
    resetn=1'b1;
end
endtask

task soft_rst;	//soft reset 
begin
    soft_reset=1'b1;
    @(negedge clock);
    soft_reset=1'b0;
end
endtask

task pkt_gen;	// packet generation
    reg [7:0]header, payload_data, parity;
    reg [5:0]payloadlen;
begin
    @(negedge clock);
    payloadlen=8;
    header={payloadlen,2'b10};
    data_in=header;
    lfd_state= 1'b1;
    for(i=0;i<payloadlen;i=i+1)
    begin
        @(negedge clock);
        payload_data={$random}%256;
        data_in=payload_data;
        lfd_state=1'b0;
    end
    @(negedge clock);
    parity={$random}%256;
    data_in=parity;
end
endtask

task read();	// read values
begin
    @(negedge clock);
    read_enb =1'b1;
end
endtask

task write(input a);	// write values into mem
begin
    @(negedge clock);
    write_enb=a;
end 
endtask

initial 
begin
    initialize;
    rst;	
    repeat(9)		
    write(1);
    pkt_gen;
    write(0);
    read;
    
    soft_rst;
    #1000 $finish;
end

initial
$monitor("Values of clock=%b, resetn=%b, write_enb=%b, read_enb=%b, lfd_state=%b, soft_reset=%b, data_in=%b, full=%b, empty=%b, data_out=%b", clock, resetn, write_enb, read_enb, lfd_state, soft_reset, data_in,full, empty, data_out);
endmodule