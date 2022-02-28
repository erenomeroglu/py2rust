

abstract type AbstractIntListNonEmpty end
abstract type AbstractIntList end
mutable struct IntListNonEmpty <: AbstractIntListNonEmpty
    first::Int64
    rest::AbstractIntList
end

function __init__(self::AbstractIntListNonEmpty, first::Int64, rest::AbstractIntList)
    setfield!(self::AbstractIntListNonEmpty, :first, first::Int64),
    setfield!(self::AbstractIntListNonEmpty, :rest, rest::IntList)
end

function __repr__(self::AbstractIntListNonEmpty)::String
    return AbstractIntListNonEmpty(self.first, self.rest)
end
function __eq__(self::AbstractIntListNonEmpty, other::AbstractIntListNonEmpty)::Bool
    return __key(self) == __key(other)
end

function __key(self::AbstractIntListNonEmpty)
    (__key(self.first), self.rest)
end


struct IntList <: AbstractIntList
    NONE::Any
    REST::AbstractIntListNonEmpty
end
