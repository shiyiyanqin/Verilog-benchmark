module router_fsm_tb();
reg clock,resetn, pkt_valid;
reg [1:0]data_in;
reg fifo_full, fifo_empty_0, fifo_empty_1, fifo_empty_2, soft_reset_0, soft_reset_1, soft_reset_2, parity_done, low_packet_valid;
wire write_enb_reg, detect_add, ld_state, laf_state, lfd_state, full_state, rst_int_reg, busy;
parameter DELAY=10;

// Define a placeholder for router_fsm since it's not provided in the project
router_fsm_placeholder DUT(
    .clock(clock),
    .resetn(resetn),
    .pkt_valid(pkt_valid),
    .data_in(data_in),
    .fifo_full(fifo_full),
    .fifo_empty_0(fifo_empty_0),
    .fifo_empty_1(fifo_empty_1),
    .fifo_empty_2(fifo_empty_2),
    .soft_reset_0(soft_reset_0),
    .soft_reset_1(soft_reset_1),
    .soft_reset_2(soft_reset_2),
    .parity_done(parity_done),
    .low_packet_valid(low_packet_valid),
    .write_enb_reg(write_enb_reg),
    .detect_add(detect_add),
    .ld_state(ld_state),
    .laf_state(laf_state),
    .lfd_state(lfd_state),
    .full_state(full_state),
    .rst_int_reg(rst_int_reg),
    .busy(busy)
);

    always
    begin
        clock=1'b0;
        #DELAY;
        clock=1'b1;
        #DELAY;
    end

task rst;
    begin
        resetn=1'b0;
        @(negedge clock);
        resetn=1'b1;
    end
endtask

task initialize;
begin
    {pkt_valid, data_in, fifo_full, fifo_empty_0, fifo_empty_1, fifo_empty_2, soft_reset_0, soft_reset_1, soft_reset_2, parity_done, low_packet_valid}=0;
end
endtask

task p0;
begin
        @(negedge clock);
        {pkt_valid, data_in[1:0], fifo_empty_2}=4'b1101;
        repeat(2)
        @(negedge clock);
        {fifo_full, pkt_valid}=2'b00;
        repeat(2)
        @(negedge clock);
        fifo_full=0;
        @(negedge clock);
end
endtask

task p2;
begin
@(negedge clock);    
        {pkt_valid, data_in[1:0], fifo_empty_0}=4'b1001;
        repeat(2)
        @(negedge clock);
        fifo_full=1'b1;
        @(negedge clock);
        fifo_full=0;
        @(negedge clock);
        parity_done=1'b1;
        @(negedge clock);
end
endtask

task p3;
begin
initialize;
        {pkt_valid, data_in[1:0], fifo_empty_2}=4'b1101;
        repeat(2)
        @(negedge clock);
        fifo_full=1'b1;
        @(negedge clock);
        fifo_full=0;
        @(negedge clock);
        {parity_done, low_packet_valid}=2'b01;
        repeat(2)
        @(negedge clock);
        fifo_full=0;
        @(negedge clock);
end
endtask

task p1;
begin
        initialize;
@(negedge clock);
        {pkt_valid, data_in[1:0], fifo_empty_2}=4'b1100;
        @(negedge clock);
        fifo_empty_2=0;
        @(negedge clock);
        {fifo_empty_0, fifo_empty_1, fifo_empty_2}=3'b111;
        repeat(2)
        @(negedge clock);
        fifo_full=1'b1;
        @(negedge clock);
        fifo_full=1'b0;
        @(negedge clock);
        {parity_done, low_packet_valid}=2'b01;
        repeat(2)
        @(negedge clock);
        fifo_full=0;
        @(negedge clock);
end
endtask
        
        initial
        begin
        initialize;
        rst;
        p0;
        p1;
        p2;
        p3;
        #1000 $finish;
        end
        
        initial
        $monitor("Values of pkt_valid=%b, data_in=%b, fifo_full=%b, fifo_empty_0=%b, fifo_empty_1=%b, fifo_empty_2=%b, soft_reset_0=%b, soft_reset_1=%b, soft_reset_2=%b, parity_done=%b, low_packet_valid=%b", pkt_valid,data_in, fifo_full, fifo_empty_0, fifo_empty_1, fifo_empty_2, soft_reset_0, soft_reset_1, soft_reset_2, parity_done, low_packet_valid);

endmodule

// Placeholder module that matches the expected interface
module router_fsm_placeholder(
    input clock,
    input resetn,
    input pkt_valid,
    input [1:0] data_in,
    input fifo_full,
    input fifo_empty_0,
    input fifo_empty_1,
    input fifo_empty_2,
    input soft_reset_0,
    input soft_reset_1,
    input soft_reset_2,
    input parity_done,
    input low_packet_valid,
    output write_enb_reg,
    output detect_add,
    output ld_state,
    output laf_state,
    output lfd_state,
    output full_state,
    output rst_int_reg,
    output busy
);
    // Simple assignments for testing purposes
    assign write_enb_reg = pkt_valid & ~fifo_full;
    assign detect_add = pkt_valid & (data_in == 2'b11);
    assign busy = pkt_valid;
    assign rst_int_reg = ~resetn;
endmodule