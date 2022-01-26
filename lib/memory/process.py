from ctypes import *
from ctypes import wintypes
from win32con import *

from .res import kernel32, psapi, structure, advapi32
from . import exception
import os

from .res.structure import DEFAULT_CODING, MODULEENTRY32

CURRENT_PROCESS_HANDLER = kernel32.GetCurrentProcess()


def virtual_query(address, handle=CURRENT_PROCESS_HANDLER):
    mbi = structure.MEMORY_BASIC_INFORMATION()
    kernel32.VirtualQueryEx(handle, address, byref(mbi), sizeof(mbi))
    return mbi


def get_current_process_filename(length: int = wintypes.MAX_PATH, coding: str = structure.DEFAULT_CODING):
    buff = create_string_buffer(length)
    windll.kernel32.SetLastError(0)
    rl = kernel32.GetModuleFileName(None, byref(buff), length)
    error_code = windll.kernel32.GetLastError()
    if error_code:
        windll.kernel32.SetLastError(0)
        raise exception.WinAPIError(error_code)
    raw = buff.raw
    return raw[:rl].decode(coding)


def base_module(handle=CURRENT_PROCESS_HANDLER):
    hModules = (c_void_p * 1024)()
    windll.kernel32.SetLastError(0)
    process_module_success = psapi.EnumProcessModulesEx(
        handle,
        byref(hModules),
        sizeof(hModules),
        byref(c_ulong()),
        structure.EnumProcessModuleEX.LIST_MODULES_64BIT
    )
    error_code = windll.kernel32.GetLastError()
    if error_code:
        windll.kernel32.SetLastError(0)
        raise exception.WinAPIError(error_code)
    if not process_module_success:
        return  # xxx
    module_info = structure.MODULEINFO(handle)
    psapi.GetModuleInformation(
        handle,
        c_void_p(hModules[0]),
        byref(module_info),
        sizeof(module_info)
    )
    return module_info


def enum_process_module(handle=CURRENT_PROCESS_HANDLER):
    hModules = (c_void_p * 1024)()
    windll.kernel32.SetLastError(0)
    process_module_success = psapi.EnumProcessModulesEx(
        handle,
        byref(hModules),
        sizeof(hModules),
        byref(c_ulong()),
        structure.EnumProcessModuleEX.LIST_MODULES_64BIT
    )
    error_code = windll.kernel32.GetLastError()
    if error_code:
        windll.kernel32.SetLastError(0)
        raise exception.WinAPIError(error_code)
    if process_module_success:
        hModules = iter(m for m in hModules if m)
        for hModule in hModules:
            module_info = structure.MODULEINFO(handle)
            psapi.GetModuleInformation(
                handle,
                c_void_p(hModule),
                byref(module_info),
                sizeof(module_info)
            )
            yield module_info


def module_from_name(module_name: str, handle=CURRENT_PROCESS_HANDLER):
    module_name = module_name.lower()
    modules = enum_process_module(handle)
    for module in modules:
        if module.name.lower() == module_name:
            return module


def inject_dll(filepath, handle=CURRENT_PROCESS_HANDLER):
    windll.kernel32.SetLastError(0)
    filepath_address = kernel32.VirtualAllocEx(
        handle,
        0,
        len(filepath),
        structure.MEMORY_STATE.MEM_COMMIT.value | structure.MEMORY_STATE.MEM_RESERVE.value,
        structure.MEMORY_PROTECTION.PAGE_EXECUTE_READWRITE.value
    )
    kernel32.WriteProcessMemory(handle, filepath_address, filepath, len(filepath), None)
    kernel32_handle = kernel32.GetModuleHandleW("kernel32.dll")
    load_library_a_address = kernel32.GetProcAddress(kernel32_handle, b"LoadLibraryA")
    thread_h = kernel32.CreateRemoteThread(
        handle, None, 0, load_library_a_address, filepath_address, 0, None
    )
    kernel32.WaitForSingleObject(thread_h, -1)
    kernel32.VirtualFreeEx(
        handle, filepath_address, len(filepath), structure.MEMORY_STATE.MEM_RELEASE.value
    )
    dll_name = os.path.basename(filepath)
    dll_name = dll_name.decode('ascii')
    module_address = kernel32.GetModuleHandleW(dll_name)
    return module_address


def start_thread(address, params=None, handler=CURRENT_PROCESS_HANDLER):
    params = params or 0
    windll.kernel32.SetLastError(0)
    NULL_SECURITY_ATTRIBUTES = cast(0, structure.LPSECURITY_ATTRIBUTES)
    thread_h = kernel32.CreateRemoteThread(
        handler,
        NULL_SECURITY_ATTRIBUTES,
        0,
        address,
        params,
        0,
        byref(c_ulong(0))
    )
    last_error = windll.kernel32.GetLastError()
    if last_error:
        raise Exception('Got an error in start thread, code: %s' % last_error)
    kernel32.WaitForSingleObject(thread_h, -1)
    return thread_h


def list_processes():
    SNAPPROCESS = 0x00000002
    windll.kernel32.SetLastError(0)
    hSnap = kernel32.CreateToolhelp32Snapshot(SNAPPROCESS, 0)
    process_entry = structure.ProcessEntry32()
    process_entry.dwSize = sizeof(process_entry)
    p32 = kernel32.Process32First(hSnap, byref(process_entry))
    if p32:
        yield process_entry
    while p32:
        yield process_entry
        p32 = kernel32.Process32Next(hSnap, byref(process_entry))
    kernel32.CloseHandle(hSnap)


def enable_privilege():
    hProcess = c_void_p(CURRENT_PROCESS_HANDLER)
    if advapi32.OpenProcessToken(hProcess, TOKEN_ADJUST_PRIVILEGES, byref(hProcess)):
        tkp = structure.TOKEN_PRIVILEGES()
        advapi32.LookupPrivilegeValue(None, SE_DEBUG_NAME, byref(tkp.Privileges[0].Luid))
        tkp.count = 1
        tkp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        advapi32.AdjustTokenPrivileges(hProcess, 0, byref(tkp), 0, None, None)
        return kernel32.GetLastError()
    return 0


def get_module_info(process_id: int, module_name: str):
    h_snap = windll.kernel32.CreateToolhelp32Snapshot(8, process_id)
    if h_snap == -1: raise Exception("CreateToolhelp32Snapshot")
    module_entry = MODULEENTRY32()
    module_entry.dwSize = sizeof(module_entry)
    while windll.kernel32.Module32Next(h_snap, byref(module_entry)):
        if module_name == module_entry.szModule.decode(DEFAULT_CODING):
            windll.kernel32.CloseHandle(h_snap)
            return module_entry
    windll.kernel32.CloseHandle(h_snap)
    raise Exception(f"Module {module_name} not found")
