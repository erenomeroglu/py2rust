

function mult_int_and_int()::Int64
a = 2
return a*2
end

function mult_float_and_int()::Float64
a = 2.0
return a*2
end

function mult_string_and_int()::String
a = "test"
return repeat(a,2)
end

function mult_int_and_string()::Int64
a::Int64 = 2
return repeat("test",a)
end

function mult_list_and_int()::Array
a::Array = []
for i in (0:10 - 1)
push!(a, i);
end
return repeat(a,2)
end

function add_two_lists()::Array
a::Array = []
b::Array = []
for i in (0:10 - 1)
push!(a, i);
push!(b, i);
end
return [a;b]
end

function mult_int_and_bool()::Int64
a::Bool = false
return a*1
end

function mult_bool_and_string()::Int64
a::Int64 = 1
return a*false
end

function and_op_int_and_int()::Int64
a::Int64 = 2
return (a & 2)
end

function or_op_int_and_int()::Int64
a::Int64 = 2
return (a | 1)
end

function arithmetic_shift_right_int_and_int()::Int64
a::Int64 = 2
return (a >> 1)
end

function arithmetic_shift_left_int_and_int()::Int64
a::Int64 = 2
return (a << 1)
end

function main()
@assert(mult_int_and_int() == 4)
@assert(mult_float_and_int() == 4.0)
@assert(mult_string_and_int() == "testtest")
@assert(mult_list_and_int() == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
@assert(add_two_lists() == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
@assert(mult_int_and_bool() == 0)
@assert(mult_bool_and_string() == 0)
@assert(and_op_int_and_int() == 2)
@assert(or_op_int_and_int() == 3)
@assert(arithmetic_shift_right_int_and_int() == 1)
@assert(arithmetic_shift_left_int_and_int() == 4)
println("Ok");
end

main()
