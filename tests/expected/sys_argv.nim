import os


proc main() =
  let a: seq[string] = (@[getAppFilename()] & commandLineParams())
  let cmd: string = a[0]
  assert(cmd != "")
  if len(a) > 1:
    echo a[1]
  else:
    echo "OK"

main()
