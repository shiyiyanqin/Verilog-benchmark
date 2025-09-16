// Define router_reg module separately (not nested)
module router_reg(
    input clock,
    input resetn,
    input pkt_valid,
    input [7:0] data_in,
    input fifo_full,
    input detect_add,
    input ld_state,
    input laf_state,
    input full_state,
    input lfd_state,
    input rst_int_reg,
    output reg err,
    output reg parity_done,
    output reg low_packet_valid,
    output reg [7:0] dout
);
    // Basic functionality for testing
    always @(posedge clock or negedge resetn) begin
        if (!resetn) begin
            err <= 0;
            parity_done <= 0;
            low_packet_valid <= 0;
            dout <= 0;
        end else begin
            dout <= data_in; // Simple pass-through for testing
        end
    end
endmodule

module router_reg_tb();
reg clock, resetn, pkt_valid;
reg [7:0] data_in;
reg fifo_full, detect_add, ld_state, laf_state, full_state, lfd_state, rst_int_reg;
wire err, parity_done, low_packet_valid;
wire [7:0]dout;
integer i;
parameter DELAY=10;

// Instantiation of router_reg after its definition
router_reg DUT (
    .clock(clock),
    .resetn(resetn),
    .pkt_valid(pkt_valid),
    .data_in(data_in),
    .fifo_full(fifo_full),
    .detect_add(detect_add),
    .ld_state(ld_state),
    .laf_state(laf_state),
    .full_state(full_state),
    .lfd_state(lfd_state),
    .rst_int_reg(rst_int_reg),
    .err(err),
    .parity_done(parity_done),
    .low_packet_valid(low_packet_valid),
    .dout(dout)
);

always 
    begin
    clock=0;
    #DELAY;
    clock=1'b1;
    #DELAY;
    end

task rst;
    begin
    resetn=0;
    @(negedge clock)
    resetn=1'b1;
    end
endtask


task initialize;
    begin
    {pkt_valid, data_in, fifo_full, detect_add, ld_state, laf_state, full_state, lfd_state, rst_int_reg}=0;
    end
endtask

task goodpkt_gen; // packet generation
            reg [7:0]header, payload_data, parity;
            reg [5:0]payloadlen;
            begin
                @(negedge clock);
                payloadlen=8;
                parity=0;
                detect_add=1'b1;
                pkt_valid=1'b1;
                header={payloadlen,2'b10};
                data_in=header;
                parity=parity^data_in;

                @(negedge clock);
                detect_add=1'b0;
                lfd_state=1'b1;
        
                for(i=0;i<payloadlen;i=i+1)    
                    begin
                    @(negedge clock);    
                    lfd_state=0;
                    ld_state=1;
    
                    payload_data={$random}%256;
                    data_in=payload_data;
                    parity=parity^data_in;                
                    end

                    @(negedge clock);    
                    pkt_valid=0;
                    data_in=parity;
                
                    @(negedge clock);
                    ld_state=0;
                    end
endtask

task badpkt_gen;     // packet generation
        reg [7:0]header, payload_data, parity;
        reg [5:0]payloadlen;
            begin
                @(negedge clock);
                payloadlen=8;
                parity=0;
                detect_add=1'b1;
                pkt_valid=1'b1;
                header={payloadlen,2'b10};
                data_in=header;
                parity=parity^data_in;

                @(negedge clock);
                detect_add=1'b0;
                lfd_state=1'b1;
        
                //@(negedge clock);
                //lfd_state=0;
                //ld_state=1;
    
                for(i=0;i<payloadlen;i=i+1)    
                    begin
                    @(negedge clock);    
                    lfd_state=0;
                    ld_state=1;
    
                    payload_data={$random}%256;
                    data_in=payload_data;
                    parity=parity^data_in;
                    end
                    @(negedge clock);
                    pkt_valid=0;
                    data_in=~parity;
                
                    @(negedge clock);
                    ld_state=0;
            end
endtask

initial 
    begin
    initialize;
    rst;
    goodpkt_gen;
    #105;
    badpkt_gen;
    #1000 $finish;
    end
    
initial //=%b

$monitor("clock=%b, resetn=%b, pkt_valid=%b, data_in=%b, fifo_full=%b, detect_add=%b, ld_state=%b, laf_state=%b, full_state=%b, lfd_state=%b, rst_int_reg=%b, err=%b, parity_done=%b, low_packet_valid=%b, dout=%b", clock, resetn, pkt_valid, data_in, fifo_full, detect_add, ld_state, laf_state, full_state, lfd_state, rst_int_reg, err, parity_done, low_packet_valid, dout);

endmodule