#include <windows.h>
#include <tlhelp32.h>

#include <cstdint>
#include <filesystem>
#include <iostream>
#include <optional>
#include <string_view>

namespace {

std::optional<DWORD> FindProcessId(std::wstring_view executable_name) {
  HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snapshot == INVALID_HANDLE_VALUE) {
    return std::nullopt;
  }
  PROCESSENTRY32W entry{};
  entry.dwSize = sizeof(entry);
  std::optional<DWORD> result;
  if (Process32FirstW(snapshot, &entry) != 0) {
    do {
      if (_wcsicmp(entry.szExeFile, executable_name.data()) == 0) {
        result = entry.th32ProcessID;
        break;
      }
    } while (Process32NextW(snapshot, &entry) != 0);
  }
  CloseHandle(snapshot);
  return result;
}

std::uintptr_t RemoteModuleBase(DWORD process_id, std::wstring_view name) {
  HANDLE snapshot = CreateToolhelp32Snapshot(
      TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, process_id);
  if (snapshot == INVALID_HANDLE_VALUE) {
    return 0;
  }
  MODULEENTRY32W entry{};
  entry.dwSize = sizeof(entry);
  std::uintptr_t result = 0;
  if (Module32FirstW(snapshot, &entry) != 0) {
    do {
      if (_wcsicmp(entry.szModule, name.data()) == 0) {
        result = reinterpret_cast<std::uintptr_t>(entry.modBaseAddr);
        break;
      }
    } while (Module32NextW(snapshot, &entry) != 0);
  }
  CloseHandle(snapshot);
  return result;
}

int Inject(const std::filesystem::path& library_path) {
  const auto process_id = FindProcessId(L"stellaris.exe");
  if (!process_id.has_value()) {
    std::cerr << "stellaris.exe is not running\n";
    return 2;
  }
  const auto absolute = std::filesystem::absolute(library_path);
  if (!std::filesystem::is_regular_file(absolute)) {
    std::cerr << "runtime library does not exist\n";
    return 2;
  }
  HANDLE process = OpenProcess(
      PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION |
          PROCESS_VM_WRITE | PROCESS_VM_READ,
      FALSE,
      *process_id);
  if (process == nullptr) {
    std::cerr << "OpenProcess failed: " << GetLastError() << '\n';
    return 3;
  }
  const std::wstring path = absolute.wstring();
  const std::size_t bytes = (path.size() + 1U) * sizeof(wchar_t);
  void* remote_path = VirtualAllocEx(
      process, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
  if (remote_path == nullptr ||
      WriteProcessMemory(
          process, remote_path, path.c_str(), bytes, nullptr) == 0) {
    if (remote_path != nullptr) {
      VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
    }
    CloseHandle(process);
    std::cerr << "writing runtime path failed\n";
    return 3;
  }
  const auto local_kernel = reinterpret_cast<std::uintptr_t>(
      GetModuleHandleW(L"kernel32.dll"));
  const auto local_load_library = reinterpret_cast<std::uintptr_t>(
      GetProcAddress(reinterpret_cast<HMODULE>(local_kernel), "LoadLibraryW"));
  const auto remote_kernel = RemoteModuleBase(*process_id, L"kernel32.dll");
  if (local_kernel == 0U || local_load_library == 0U || remote_kernel == 0U) {
    VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
    CloseHandle(process);
    std::cerr << "LoadLibraryW resolution failed\n";
    return 3;
  }
  const auto remote_load_library = reinterpret_cast<LPTHREAD_START_ROUTINE>(
      remote_kernel + (local_load_library - local_kernel));
  HANDLE thread = CreateRemoteThread(
      process, nullptr, 0, remote_load_library, remote_path, 0, nullptr);
  if (thread == nullptr) {
    VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
    CloseHandle(process);
    std::cerr << "CreateRemoteThread failed: " << GetLastError() << '\n';
    return 3;
  }
  const DWORD wait = WaitForSingleObject(thread, 15'000);
  DWORD result = 0;
  GetExitCodeThread(thread, &result);
  CloseHandle(thread);
  VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
  CloseHandle(process);
  if (wait != WAIT_OBJECT_0 || result == 0U) {
    std::cerr << "remote LoadLibraryW failed or timed out\n";
    return 4;
  }
  std::cout << "runtime loaded into stellaris.exe pid=" << *process_id << '\n';
  return 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc != 2) {
    std::cerr << "usage: iag_stellaris_native_runtime_loader <runtime.dll>\n";
    return 2;
  }
  return Inject(argv[1]);
}
