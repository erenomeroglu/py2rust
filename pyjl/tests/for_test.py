@use_continuables
def generator_func():
    num = 1
    yield num
    num = 5
    yield num
    num = 10
    yield num

def generator_func_loop():
    num = 0
    for n in range(1, 10):
        yield num + n

def generator_func_loop_using_var():
    num = 0
    end = 12
    end = 16
    for n in range(1, end):
        yield num + n

class TestClass:
    def generator_func(self):
        num = 123
        yield num
        num = 5
        yield num
        num = 10
        yield num

if __name__ == "__main__":
    testClass: TestClass = TestClass()
    funcs = [generator_func, generator_func_loop, generator_func_loop_using_var, testClass.generator_func]
    for func in funcs:
        for i in func():
            print(i)