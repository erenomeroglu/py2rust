using ResumableFunctions


reverse_translation = maketrans(
    bytes,
    b"ABCDGHKMNRSTUVWYabcdghkmnrstuvwy",
    b"TVGHCDMKNYSAABWRTVGHCDMKNYSAABWR",
)
function reverse_complement(header, sequence)::Tuple
    t = replace!(reverse_translation, b"\n\r ")
    output = Vector{UInt8}()
    trailing_length = length(t) % 60
    if trailing_length
        output += b"\n" + t[begin:trailing_length]
    end
    for i in (trailing_length:60:length(t)-1)
        output += b"\n" + t[(i+1):i+60]
    end
    return (header, output[begin:end])
end

@resumable function read_sequences(file)
    for line in file
        if line[1] == ord(">")
            header = line
            sequence = Vector{UInt8}()
            for line in file
                if line[1] == ord(">")
                    @yield (header, sequence)
                    header = line
                    sequence = Vector{UInt8}()
                else
                    sequence += line
                end
            end
            @yield (header, sequence)
            break
        end
    end
end

@resumable function main()
    write_ = x -> write(IOBuffer(), x)
    flush = flush
    s = read_sequences(stdin.buffer)
    data = next(s)
    @resumable function merge(v, g)
        @yield v
        @yield from g
    end

    for (h, r) in starmap(reverse_complement, merge(data, s))
        write_(h)
        write_(r)
    end
end

main()
