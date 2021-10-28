function generator_func()
channel_generator_func = Channel(6)
num = 1
put!(channel_generator_func, num);
num = 5
put!(channel_generator_func, num);
num = 10
put!(channel_generator_func, num);
close(channel_generator_func)
channel_generator_func
end

function generator_func_loop()
channel_generator_func_loop = Channel(9)
num = 0
for n in (1:10 - 1)
put!(channel_generator_func_loop, (num + n));
end
close(channel_generator_func_loop)
channel_generator_func_loop
end

function generator_func_loop_using_var()
channel_generator_func_loop_using_var = Channel(15)
num = 0
end_ = 12
end_ = 16
for n in (1:end_ - 1)
put!(channel_generator_func_loop_using_var, (num + n));
end
close(channel_generator_func_loop_using_var)
channel_generator_func_loop_using_var
end

struct TestClass
end
function generator_func(self::TestClass)
channel_generator_func = Channel(3)
num = 123
put!(channel_generator_func, num);
num = 5
put!(channel_generator_func, num);
num = 10
put!(channel_generator_func, num);
close(channel_generator_func)
channel_generator_func
end


function main()
for i in generator_func()
println(i);
end
println("-----------------------");
for i in generator_func_loop()
println(i);
end
println("-----------------------");
for i in generator_func_loop_using_var()
println(i);
end
println("-----------------------");
testClass::TestClass = TestClass()
for i in generator_func()
println(i);
end
end

main()
