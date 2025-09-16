module stopwatch(CLOCK_50, HEX0, HEX1, HEX2, HEX3, HEX4, HEX5, KEY);
//100 HZ clock would represent 1/100th of a second
//1HZ would represent 1 second of a second


	reg [18:0] counter; //can count up to 2^19
	input CLOCK_50;
	reg CLOCK_100HZ; //start CLOCK_100HZ with 0
	
	//registers for hundreth of a sec
	reg [6:0] hundreths; // we need to store up to 99 values, 2^7 gives 128 values
	reg [3:0] tens_hundreths;
	reg [3:0] units_hundreths;
	
	//registers for second
	reg [6:0] seconds; 
	reg [3:0] tens_seconds;
	reg [3:0] units_seconds;
	
	//registers for minute
	reg [6:0] minutes; 
	reg [3:0] tens_minutes;
	reg [3:0] units_minutes;
	
	//BCD for hundreths of a second
   output [6:0]HEX0; //To display units of the hundreth of a second
	output [6:0]HEX1; //To display tens of the hundreth of a second
	wire a, b, c, d, e, f, g; //units of the hundreth of a second
	wire a1, b1, c1, d1, e1, f1, g1; //tens of the hundreth of a second
	
	//BCD for seconds 
   output [6:0]HEX2; 
	output [6:0]HEX3; 
	wire a2, b2, c2, d2, e2, f2, g2; 
	wire a3, b3, c3, d3, e3, f3, g3; 
	
	//BCD for minutes 
   output [6:0]HEX4; 
	output [6:0]HEX5; 
	wire a4, b4, c4, d4, e4, f4, g4; 
	wire a5, b5, c5, d5, e5, f5, g5; 
	
	//Reset and Stop
	input [1:0] KEY;
	wire reset;
   assign reset = ~KEY[0];   
	wire pause;
   assign pause = ~KEY[1];  
	reg pause_state;
	
	initial begin
	 CLOCK_100HZ = 0;
	 counter = 0;
	 hundreths = 0;
	 seconds = 0;
	 minutes = 0;
	end

	// implement a counter 
	always @(posedge CLOCK_50) begin
	counter = counter + 1;
		if (counter == 499999) begin // 2^19 for max bits - 1 to get 499999 as counter starts from 0
			counter = 0;
			CLOCK_100HZ = ~ CLOCK_100HZ; //when counter reaches the max counter value we flip CLOCK_100HZ to synthesis a switching between states
		end 
	end

	
	//Implementing hundreths of a second and seconds
	always @(posedge CLOCK_100HZ) begin
		if (reset) begin
			hundreths = 0;
			seconds = 0;
			minutes = 0;
		end
		
		if (pause) begin
			pause_state = 1;
		end
		if (!pause) begin
			pause_state = 0;
		end
	
		if (pause_state == 0) begin
			hundreths = hundreths + 1;
			if (hundreths == 99) begin
				hundreths = 0; // Hundreths of second 
				seconds = seconds + 1; //When Hundeths of a second reaches 99 increment seconds by 1
			end
			
			if(seconds == 60) begin //when seconds hit 60 increment by 1 minute
			seconds = 0;
			minutes = minutes + 1;
			end
			
			
			tens_hundreths = hundreths / 10;
			units_hundreths = hundreths % 10;
			tens_seconds = seconds / 10;
			units_seconds = seconds % 10;
			tens_minutes = minutes / 10;
			units_minutes = minutes % 10;
		end
	end
	
	//Hundreth of a second
	BCD bcd_unit_hundreths(.A(units_hundreths[3]), .B(units_hundreths[2]), .C(units_hundreths[1]), .D(units_hundreths[0]), .a(a), .b(b), .c(c), .d(d), .e(e)
	, .f(f), .g(g));
	BCD bcd_tens_hundreths(.A(tens_hundreths[3]), .B(tens_hundreths[2]), .C(tens_hundreths[1]), .D(tens_hundreths[0]), .a(a1), .b(b1), .c(c1), .d(d1), .e(e1)
	, .f(f1), .g(g1));
	
	assign HEX0[0] = a;
	assign HEX0[1] = b;
	assign HEX0[2] = c;
	assign HEX0[3] = d;
	assign HEX0[4] = e;
	assign HEX0[5] = f;
	assign HEX0[6] = g;
	
	assign HEX1[0] = a1;
	assign HEX1[1] = b1;
	assign HEX1[2] = c1;
	assign HEX1[3] = d1;
	assign HEX1[4] = e1;
	assign HEX1[5] = f1;
	assign HEX1[6] = g1;
	
	//Seconds
	BCD bcd_unit_second(.A(units_seconds[3]), .B(units_seconds[2]), .C(units_seconds[1]), .D(units_seconds[0]), .a(a2), .b(b2), .c(c2), .d(d2), .e(e2)
	, .f(f2), .g(g2));
   BCD bcd_tens_second(.A(tens_seconds[3]), .B(tens_seconds[2]), .C(tens_seconds[1]), .D(tens_seconds[0]), .a(a3), .b(b3), .c(c3), .d(d3), .e(e3)
	, .f(f3), .g(g3));
	
	assign HEX2[0] = a2;
	assign HEX2[1] = b2;
	assign HEX2[2] = c2;
	assign HEX2[3] = d2;
	assign HEX2[4] = e2;
	assign HEX2[5] = f2;
	assign HEX2[6] = g2;
	
	assign HEX3[0] = a3;
	assign HEX3[1] = b3;
	assign HEX3[2] = c3;
	assign HEX3[3] = d3;
	assign HEX3[4] = e3;
	assign HEX3[5] = f3;
	assign HEX3[6] = g3;
	
	//minutes
	BCD bcd_unit_min(.A(units_minutes[3]), .B(units_minutes[2]), .C(units_minutes[1]), .D(units_minutes[0]), .a(a4), .b(b4), .c(c4), .d(d4), .e(e4)
	, .f(f4), .g(g4));
   BCD bcd_tens_min(.A(tens_minutes[3]), .B(tens_minutes[2]), .C(tens_minutes[1]), .D(tens_minutes[0]), .a(a5), .b(b5), .c(c5), .d(d5), .e(e5)
	, .f(f5), .g(g5));
	
	assign HEX4[0] = a4;
	assign HEX4[1] = b4;
	assign HEX4[2] = c4;
	assign HEX4[3] = d4;
	assign HEX4[4] = e4;
	assign HEX4[5] = f4;
	assign HEX4[6] = g4;
	
	assign HEX5[0] = a5;
	assign HEX5[1] = b5;
	assign HEX5[2] = c5;
	assign HEX5[3] = d5;
	assign HEX5[4] = e5;
	assign HEX5[5] = f5;
	assign HEX5[6] = g5;
	
endmodule