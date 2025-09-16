// Define router_sync module separately before the testbench
module router_sync(
    input clock,
    input resetn,
    input detect_add,
    input full_0,
    input full_1,
    input full_2,
    input empty_0,
    input empty_1,
    input empty_2,
    input write_enb_reg,
    input read_enb_0,
    input read_enb_1,
    input read_enb_2,
    input [1:0] data_in,
    output [2:0] write_enb,
    output fifo_full,
    output soft_reset_0,
    output soft_reset_1,
    output soft_reset_2,
    output vld_out_1,
    output vld_out_2,
    output vld_out_0
);
    // Empty implementation - just enough to compile
    assign write_enb = 0;
    assign fifo_full = 0;
    assign soft_reset_0 = 0;
    assign soft_reset_1 = 0;
    assign soft_reset_2 = 0;
    assign vld_out_1 = 0;
    assign vld_out_2 = 0;
    assign vld_out_0 = 0;
endmodule

// Testbench module
module router_sync_tb();

reg clock, resetn, detect_add, full_0, full_1, full_2, empty_0, empty_1, empty_2, write_enb_reg, read_enb_0, read_enb_1, read_enb_2;

reg [1:0]data_in;
wire [2:0]write_enb;
wire fifo_full, soft_reset_0, soft_reset_1, soft_reset_2; 
wire vld_out_1, vld_out_2, vld_out_0;
parameter DELAY=10;

// Now instantiate the module
router_sync DUT(
    .clock(clock),
    .resetn(resetn),
    .detect_add(detect_add),
    .full_0(full_0),
    .full_1(full_1),
    .full_2(full_2),
    .empty_0(empty_0),
    .empty_1(empty_1),
    .empty_2(empty_2),
    .write_enb_reg(write_enb_reg),
    .read_enb_0(read_enb_0),
    .read_enb_1(read_enb_1),
    .read_enb_2(read_enb_2),
    .data_in(data_in),
    .write_enb(write_enb),
    .fifo_full(fifo_full),
    .soft_reset_0(soft_reset_0),
    .soft_reset_1(soft_reset_1),
    .soft_reset_2(soft_reset_2),
    .vld_out_1(vld_out_1),
    .vld_out_2(vld_out_2),
    .vld_out_0(vld_out_0)
);

    always  //clock generation
        begin
        clock=1'b0;
        #(DELAY/2);
        clock=1'b1;
        #(DELAY/2);
        end
        
    task initialize;    //initialization
        begin
        data_in=0;
        detect_add=0;
        full_0=0;
        full_1=0;
        full_2=0;
        empty_0=0;
        empty_1=0;
        empty_2=0;
        write_enb_reg=0;
        read_enb_0=0;
        read_enb_1=0;
        read_enb_2=0;    
        end
    endtask

    task rst();    //reset
        begin
        resetn= 0;
        @(negedge clock);
        resetn=1'b1;
        end
    endtask
    
    task write_enb_s;    //write_enb_reg
    begin
    write_enb_reg=1'b1; 
    @(negedge clock);
    //write_enb_reg=1'b0;
    end
    endtask
    
    task drive(input[1:0]m);
        begin
        data_in=m;
        #DELAY;
        end
    endtask
        
    initial
    begin
    initialize;
    rst;
    drive(1);
    detect_add=1'b1;
    read_enb_0=1'b0;
    read_enb_1=1'b0;
    read_enb_2=1'b1;
    write_enb_s;
    full_0=1'b0;
    full_1=1'b1;
    full_2=1'b0;
    empty_0=1'b1;
    empty_1=1'b0;
    empty_2=1'b1;
    #DELAY;
    #DELAY;
    
    #1000 $finish;
    end
    
    initial
    $monitor("clock=%b, resetn=%b, detect_add=%b, full_0=%b, full_1=%b, full_2=%b, empty_0=%b, empty_1=%b, empty_2=%b, write_enb_reg=%b, read_enb_0=%b, read_enb_1=%b, read_enb_2=%b, data_in=%b", clock, resetn, detect_add, full_0, full_1, full_2, empty_0, empty_1, empty_2, write_enb_reg, read_enb_0, read_enb_1, read_enb_2, data_in);
endmodule