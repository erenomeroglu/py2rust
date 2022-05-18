using win32com_.client: GetObject
include("win32com_/test/util.jl")

abstract type AbstractSimple <: Abstractwin32com_.test.util.TestCase end
mutable struct Simple <: AbstractSimple

end
function testit(self::Simple)
cses = InstancesOf(GetObject("WinMgMts:"), "Win32_Process")
vals = []
for cs in cses
val = Properties_(cs, "Caption").Value
push!(vals, val)
end
assertFalse(self, length(vals) < 5, "We only found %d processes!" % length(vals))
end

function main()

end

main()