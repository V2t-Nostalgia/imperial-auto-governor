#include <windows.h>
#include <tlhelp32.h>

#include <charconv>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr wchar_t kPipeName[] = L"\\\\.\\pipe\\iag-stellaris-native-research";

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

std::uintptr_t RemoteModuleBase(DWORD process_id, std::wstring_view module_name) {
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
      if (_wcsicmp(entry.szModule, module_name.data()) == 0) {
        result = reinterpret_cast<std::uintptr_t>(entry.modBaseAddr);
        break;
      }
    } while (Module32NextW(snapshot, &entry) != 0);
  }
  CloseHandle(snapshot);
  return result;
}

int Inject(const std::filesystem::path& dll_path) {
  const auto process_id = FindProcessId(L"stellaris.exe");
  if (!process_id.has_value()) {
    std::cerr << "stellaris.exe is not running\n";
    return 2;
  }
  const std::filesystem::path absolute = std::filesystem::absolute(dll_path);
  if (!std::filesystem::is_regular_file(absolute)) {
    std::cerr << "probe DLL does not exist: " << absolute.string() << '\n';
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
  const std::size_t bytes = (path.size() + 1) * sizeof(wchar_t);
  void* remote_path = VirtualAllocEx(
      process, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
  if (remote_path == nullptr ||
      WriteProcessMemory(process, remote_path, path.c_str(), bytes, nullptr) == 0) {
    std::cerr << "writing DLL path failed: " << GetLastError() << '\n';
    if (remote_path != nullptr) {
      VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
    }
    CloseHandle(process);
    return 3;
  }
  const auto local_kernel = reinterpret_cast<std::uintptr_t>(
      GetModuleHandleW(L"kernel32.dll"));
  const auto local_load_library = reinterpret_cast<std::uintptr_t>(
      GetProcAddress(reinterpret_cast<HMODULE>(local_kernel), "LoadLibraryW"));
  const auto remote_kernel = RemoteModuleBase(*process_id, L"kernel32.dll");
  if (local_kernel == 0 || local_load_library == 0 || remote_kernel == 0) {
    std::cerr << "LoadLibraryW resolution failed\n";
    VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
    CloseHandle(process);
    return 3;
  }
  const auto remote_load_library = reinterpret_cast<LPTHREAD_START_ROUTINE>(
      remote_kernel + (local_load_library - local_kernel));
  HANDLE thread = CreateRemoteThread(
      process, nullptr, 0, remote_load_library, remote_path, 0, nullptr);
  if (thread == nullptr) {
    std::cerr << "CreateRemoteThread failed: " << GetLastError() << '\n';
    VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
    CloseHandle(process);
    return 3;
  }
  const DWORD wait = WaitForSingleObject(thread, 15000);
  DWORD result = 0;
  GetExitCodeThread(thread, &result);
  CloseHandle(thread);
  VirtualFreeEx(process, remote_path, 0, MEM_RELEASE);
  CloseHandle(process);
  if (wait != WAIT_OBJECT_0 || result == 0) {
    std::cerr << "remote LoadLibraryW failed or timed out\n";
    return 4;
  }
  std::cout << "probe DLL loaded into stellaris.exe pid=" << *process_id << '\n';
  return 0;
}

std::optional<std::uint32_t> ParseUint32(std::string_view value) {
  std::uint32_t parsed = 0;
  const auto result = std::from_chars(value.data(), value.data() + value.size(), parsed);
  if (result.ec != std::errc{} || result.ptr != value.data() + value.size()) {
    return std::nullopt;
  }
  return parsed;
}

int Exchange(std::string_view request) {
  if (WaitNamedPipeW(kPipeName, 5000) == 0) {
    std::cerr << "native probe pipe is unavailable: " << GetLastError() << '\n';
    return 5;
  }
  HANDLE pipe = CreateFileW(
      kPipeName,
      GENERIC_READ | GENERIC_WRITE,
      0,
      nullptr,
      OPEN_EXISTING,
      0,
      nullptr);
  if (pipe == INVALID_HANDLE_VALUE) {
    std::cerr << "opening native probe pipe failed: " << GetLastError() << '\n';
    return 5;
  }
  DWORD written = 0;
  if (WriteFile(
          pipe,
          request.data(),
          static_cast<DWORD>(request.size()),
          &written,
          nullptr) == 0 ||
      written != request.size()) {
    std::cerr << "writing native probe request failed: " << GetLastError() << '\n';
    CloseHandle(pipe);
    return 5;
  }
  std::string response;
  char buffer[512]{};
  while (response.size() <= 2048) {
    DWORD read = 0;
    if (ReadFile(pipe, buffer, sizeof(buffer), &read, nullptr) == 0 || read == 0) {
      break;
    }
    response.append(buffer, buffer + read);
    if (response.find('\n') != std::string::npos) {
      break;
    }
  }
  CloseHandle(pipe);
  if (response.empty()) {
    std::cerr << "native probe returned no response\n";
    return 5;
  }
  std::cout << response;
  return 0;
}

void Usage() {
  std::cerr
      << "usage:\n"
      << "  iag_stellaris_windows_probe_tool inject <probe.dll>\n"
      << "  iag_stellaris_windows_probe_tool status\n"
      << "  iag_stellaris_windows_probe_tool move <request-id> <fleet-id> <system-id>\n";
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc == 3 && std::wstring_view(argv[1]) == L"inject") {
    return Inject(argv[2]);
  }
  if (argc == 2 && std::wstring_view(argv[1]) == L"status") {
    return Exchange("IAGW1\tstatus\n");
  }
  if (argc == 5 && std::wstring_view(argv[1]) == L"move") {
    const std::filesystem::path request_id_path(argv[2]);
    const std::string request_id = request_id_path.string();
    const std::filesystem::path fleet_path(argv[3]);
    const std::filesystem::path system_path(argv[4]);
    const auto fleet = ParseUint32(fleet_path.string());
    const auto system = ParseUint32(system_path.string());
    if (!fleet.has_value() || !system.has_value()) {
      Usage();
      return 2;
    }
    const std::string request = "IAGW1\t" + request_id +
                                "\tmove_fleet\t1\t" +
                                std::to_string(*fleet) + '\t' +
                                std::to_string(*system) + '\n';
    return Exchange(request);
  }
  Usage();
  return 2;
}
