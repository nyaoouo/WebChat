import argparse
import ctypes
import sys
import threading
import time
import os

from lib.memory import *
from lib.memory.res.structure import DEFAULT_CODING

game_execution = "ffxiv_dx11.exe"


def end(s: str):
    input(s + endl)
    exit()


endl = "\n<press enter to exit>"
if sys.version_info < (3, 10): end("please use python environment >=3.10")

application_path = os.path.dirname(__file__)
os.chdir(application_path)
init_modules = list(sys.modules.keys())
sys.path.insert(0, application_path)
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=35200)
parser.add_argument('--host', default='0.0.0.0')
args = parser.parse_args()

try:
    is_admin = ctypes.windll.shell32.IsUserAnAdmin()
except:
    is_admin = False
if not is_admin:
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
    exit()

ep = process.enable_privilege()
if ep: end(f"enable privileges failed with err code {ep}")

python_version = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
python_lib = process.module_from_name(python_version).filename
local_handle = kernel32.GetModuleHandleW(python_version)
funcs = {k: kernel32.GetProcAddress(local_handle, k) for k in
         [b'Py_InitializeEx', b'PyRun_SimpleString', b'Py_FinalizeEx']}


def is_process_injected(pid: int):
    handler = kernel32.OpenProcess(structure.PROCESS.PROCESS_ALL_ACCESS.value, False, pid)
    if handler:
        return process.module_from_name(python_version, handler) is not None
    else:
        return False


game_process = [
    p.th32ProcessID
    for p in process.list_processes()
    if game_execution in p.szExeFile.decode(DEFAULT_CODING).lower()
    and not is_process_injected(p.th32ProcessID)
]

if not game_process:
    end("game process not found")
if len(game_process) > 1:
    print(f"{len(game_process)} game process found:")
    for p in game_process:
        print(f"\t{p}")
    pid = int(input("please select one process<<"))
else:
    pid = game_process[0]

handler = kernel32.OpenProcess(structure.PROCESS.PROCESS_ALL_ACCESS.value, False, pid)
if not handler: end(f"could not open process {pid} with error code {ctypes.windll.kernel32.GetLastError()}")
python_lib_h = process.module_from_name(python_version, handler)
if python_lib_h is None:
    dll_base = process.inject_dll(bytes(python_lib, 'utf-8'), handler)
    if not dll_base: end(f"inject dll failed on process {pid}")
else:
    dll_base = python_lib_h.lpBaseOfDll

dif = dll_base - local_handle
param_addr = memory.allocate_memory(4, handler)
memory.write_memory(ctypes.c_int, param_addr, 1, handler)
process.start_thread(funcs[b'Py_InitializeEx'] + dif, param_addr, handler)
err_path = os.path.join(application_path, f'InjectErr_{int(time.time())}.log').replace("\\", "\\\\")
shellcode = f"""import ctypes
import sys
import os
from traceback import format_exc
try:
    sys.path={sys.path}
    os.chdir(sys.path[0])
    from lib import web_chat
    web_chat.run("{args.host}",{args.port})
except Exception as e:
    err_text=format_exc()
    with open("{err_path}", "w+") as f:
        f.write(err_text)
    import ctypes
    ctypes.windll.user32.MessageBoxW(0,"error occur:\\n"+err_text,"inject error",0x10)
""".encode('utf-8')
shellcode_addr = memory.allocate_memory(len(shellcode), handler)

memory.write_bytes(shellcode_addr, shellcode, handler=handler)
threading.Thread(target=process.start_thread, args=(funcs[b'PyRun_SimpleString'] + dif, shellcode_addr, handler)).start()
end(f"inject thread {pid} success")
