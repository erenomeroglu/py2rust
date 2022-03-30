using Test

abstract type Abstractaug_test end
abstract type Abstractaug_test2 <: Abstractaug_test end
abstract type Abstractaug_test3 <: Abstractaug_test end
abstract type Abstractaug_test4 <: Abstractaug_test3 end
abstract type Abstracttestall end
abstract type AbstractAugAssignTest end
mutable struct AugAssignTest <: AbstractAugAssignTest

end
function testBasic(self::AbstractAugAssignTest)
    x = 2
    x += 1
    x *= 2
    x *= 2
    x -= 8
    x /= 5
    x %= 3
    x &= 2
    x |= 5
    x = x ⊻ 1
    x /= 2
    @test (x == 3.0)
end

function test_with_unpacking(self::AbstractAugAssignTest)
    @test_throws SyntaxError compile("x, b += 3", "<test>", "exec")
end

function testInList(self::AbstractAugAssignTest)
    x = [2]
    x[1] += 1
    x[1] *= 2
    x[1] *= 2
    x[1] -= 8
    x[1] /= 5
    x[1] %= 3
    x[1] &= 2
    x[1] |= 5
    x[1] = x[1] ⊻ 1
    x[1] /= 2
    @test (x[1] == 3.0)
end

function testInDict(self::AbstractAugAssignTest)
    x = Dict(0 => 2)
    x[1] += 1
    x[1] *= 2
    x[1] *= 2
    x[1] -= 8
    x[1] /= 5
    x[1] %= 3
    x[1] &= 2
    x[1] |= 5
    x[1] = x[1] ⊻ 1
    x[1] /= 2
    @test (x[1] == 3.0)
end

function testSequences(self::AbstractAugAssignTest)
    x = [1, 2]
    x += [3, 4]
    x *= 2
    @test (x == [1, 2, 3, 4, 1, 2, 3, 4])
    x = [1, 2, 3]
    y = x
    x[2:2] *= 2
    y[2:2] += [1]
    @test (x == [1, 2, 1, 2, 3])
    @test x == y
end

function testCustomMethods1(self::AbstractAugAssignTest)
    x = aug_test(1)
    y = x
    x += 10
    assertIsInstance(self, x, aug_test)
    @test y != x
    @test (x.val == 11)
    x = aug_test2(2)
    y = x
    x += 10
    @test y == x
    @test (x.val == 12)
    x = aug_test3(3)
    y = x
    x += 10
    assertIsInstance(self, x, aug_test3)
    @test y != x
    @test (x.val == 13)
    x = aug_test4(4)
    assertRaises(self, TypeError) do
        x += 10
    end
end

function testCustomMethods2(test_self)
    output = []
    x = testall()
    x + 1
    1 + x
    x += 1
    x - 1
    1 - x
    x -= 1
    x * 1
    1 * x
    x *= 1
    x * 1
    1 * x
    x *= 1
    x / 1
    1 / x
    x /= 1
    x / 1
    1 / x
    x /= 1
    x % 1
    1 % x
    x %= 1
    x^1
    1^x
    x *= 1
    x | 1
    1 | x
    x |= 1
    x & 1
    1 & x
    x &= 1
    x ⊻ 1
    1 ⊻ x
    x = x ⊻ 1
    x >> 1
    1 >> x
    x >>= 1
    x << 1
    1 << x
    x <<= 1
    assertEqual(
        test_self,
        output,
        splitlines(
            "__add__ called\n__radd__ called\n__iadd__ called\n__sub__ called\n__rsub__ called\n__isub__ called\n__mul__ called\n__rmul__ called\n__imul__ called\n__matmul__ called\n__rmatmul__ called\n__imatmul__ called\n__truediv__ called\n__rtruediv__ called\n__itruediv__ called\n__floordiv__ called\n__rfloordiv__ called\n__ifloordiv__ called\n__mod__ called\n__rmod__ called\n__imod__ called\n__pow__ called\n__rpow__ called\n__ipow__ called\n__or__ called\n__ror__ called\n__ior__ called\n__and__ called\n__rand__ called\n__iand__ called\n__xor__ called\n__rxor__ called\n__ixor__ called\n__rshift__ called\n__rrshift__ called\n__irshift__ called\n__lshift__ called\n__rlshift__ called\n__ilshift__ called\n",
        ),
    )
end

mutable struct aug_test <: Abstractaug_test
    val::Any
end
function __init__(self, value)
    self.val = value
end

function __radd__(self, val)
    return self.val + val
end

function __add__(self, val)::aug_test
    return aug_test(self.val + val)
end

mutable struct aug_test2 <: Abstractaug_test2
    val::Any
end
function __iadd__(self, val)
    self.val = self.val + val
    return self
end

mutable struct aug_test3 <: Abstractaug_test3

end
function __iadd__(self, val)::aug_test3
    return aug_test3(self.val + val)
end

mutable struct aug_test4 <: Abstractaug_test4
    __iadd__::Any

    aug_test4(__iadd__::Any = nothing) = new(__iadd__)
end

mutable struct testall <: Abstracttestall

end
function __add__(self, val)
    push!(output, "__add__ called")
end

function __radd__(self, val)
    push!(output, "__radd__ called")
end

function __iadd__(self, val)
    push!(output, "__iadd__ called")
    return self
end

function __sub__(self, val)
    push!(output, "__sub__ called")
end

function __rsub__(self, val)
    push!(output, "__rsub__ called")
end

function __isub__(self, val)
    push!(output, "__isub__ called")
    return self
end

function __mul__(self, val)
    push!(output, "__mul__ called")
end

function __rmul__(self, val)
    push!(output, "__rmul__ called")
end

function __imul__(self, val)
    push!(output, "__imul__ called")
    return self
end

function __matmul__(self, val)
    push!(output, "__matmul__ called")
end

function __rmatmul__(self, val)
    push!(output, "__rmatmul__ called")
end

function __imatmul__(self, val)
    push!(output, "__imatmul__ called")
    return self
end

function __floordiv__(self, val)
    push!(output, "__floordiv__ called")
    return self
end

function __ifloordiv__(self, val)
    push!(output, "__ifloordiv__ called")
    return self
end

function __rfloordiv__(self, val)
    push!(output, "__rfloordiv__ called")
    return self
end

function __truediv__(self, val)
    push!(output, "__truediv__ called")
    return self
end

function __rtruediv__(self, val)
    push!(output, "__rtruediv__ called")
    return self
end

function __itruediv__(self, val)
    push!(output, "__itruediv__ called")
    return self
end

function __mod__(self, val)
    push!(output, "__mod__ called")
end

function __rmod__(self, val)
    push!(output, "__rmod__ called")
end

function __imod__(self, val)
    push!(output, "__imod__ called")
    return self
end

function __pow__(self, val)
    push!(output, "__pow__ called")
end

function __rpow__(self, val)
    push!(output, "__rpow__ called")
end

function __ipow__(self, val)
    push!(output, "__ipow__ called")
    return self
end

function __or__(self, val)
    push!(output, "__or__ called")
end

function __ror__(self, val)
    push!(output, "__ror__ called")
end

function __ior__(self, val)
    push!(output, "__ior__ called")
    return self
end

function __and__(self, val)
    push!(output, "__and__ called")
end

function __rand__(self, val)
    push!(output, "__rand__ called")
end

function __iand__(self, val)
    push!(output, "__iand__ called")
    return self
end

function __xor__(self, val)
    push!(output, "__xor__ called")
end

function __rxor__(self, val)
    push!(output, "__rxor__ called")
end

function __ixor__(self, val)
    push!(output, "__ixor__ called")
    return self
end

function __rshift__(self, val)
    push!(output, "__rshift__ called")
end

function __rrshift__(self, val)
    push!(output, "__rrshift__ called")
end

function __irshift__(self, val)
    push!(output, "__irshift__ called")
    return self
end

function __lshift__(self, val)
    push!(output, "__lshift__ called")
end

function __rlshift__(self, val)
    push!(output, "__rlshift__ called")
end

function __ilshift__(self, val)
    push!(output, "__ilshift__ called")
    return self
end

function main()
    aug_assign_test = AugAssignTest()
    testBasic(aug_assign_test)
    test_with_unpacking(aug_assign_test)
    testInList(aug_assign_test)
    testInDict(aug_assign_test)
    testSequences(aug_assign_test)
    testCustomMethods1(aug_assign_test)
    testCustomMethods2(aug_assign_test)
end

main()
