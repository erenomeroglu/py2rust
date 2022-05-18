using Printf
using PyCall
pythoncom = pyimport("pythoncom")
win32api = pyimport("win32api")
import win32con
import win32com_.client
using util: CheckClean
using win32com_.client: gencache, constants


ammodule = nothing
function GetDefaultProfileName()
try
key = RegOpenKey(win32api, win32con.HKEY_CURRENT_USER, "Software\\Microsoft\\Windows NT\\CurrentVersion\\Windows Messaging Subsystem\\Profiles")
try
return RegQueryValueEx(win32api, key, "DefaultProfile")[1]
finally
Close(key)
end
catch exn
if exn isa win32api.error
return nothing
end
end
end

function DumpFolder(folder, indent = 0)
println("$(folder.Name)")
folders = folder.Folders
folder = GetFirst(folders)
while folder
DumpFolder(folder, indent + 1)
folder = GetNext(folders)
end
end

function DumpFolders(session)
try
infostores = session.InfoStores
catch exn
if exn isa AttributeError
store = session.DefaultStore
folder = GetRootFolder(store)
DumpFolder(folder)
return
end
end
println(infostores)
@printf("There are %d infostores", infostores.Count)
for i in 0:infostores.Count - 1
infostore = infostores[i + 2]
println("Infostore = $(infostore.Name)")
try
folder = infostore.RootFolder
catch exn
 let details = exn
if details isa pythoncom.com_error
hr, msg, exc, arg = details
if exc && exc[end] == -2147221219
println("This info store is currently not available")
continue;
end
end
end
end
DumpFolder(folder)
end
end

PropTagsById = Dict()
if ammodule
for (name, val) in items(ammodule.constants.__dict__)
PropTagsById[val] = name
end
end
function TestAddress(session)
#= pass =#
end

function TestUser(session)
ae = session.CurrentUser
fields = hasfield(ae, "Fields"): getfield(ae, "Fields" ? []
@printf("User has %d fields", length(fields))
for f in 0:length(fields) - 1
field = fields[f + 2]
try
id = PropTagsById[field.ID]
catch exn
if exn isa KeyError
id = field.ID
end
end
@printf("%s/%s=%s", (field.Name, id, field.Value))
end
end

function test()
oldcwd = getcwd(os)
try
session = EnsureDispatch(gencache, "MAPI.Session")
try
Logon(session, GetDefaultProfileName())
catch exn
 let details = exn
if details isa pythoncom.com_error
println("Could not log on to MAPI:$(details)")
return
end
end
end
catch exn
if exn isa pythoncom.error
app = EnsureDispatch(gencache, "Outlook.Application")
session = app.Session
end
end
try
TestUser(session)
TestAddress(session)
DumpFolders(session)
finally
Logoff(session)
chdir(os, oldcwd)
end
end

function main()
test()
CheckClean()
end

main()