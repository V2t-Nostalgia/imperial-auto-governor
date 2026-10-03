#include <windows.h>
#include <tlhelp32.h>

#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <condition_variable>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace {

using namespace std::chrono_literals;

constexpr wchar_t kPipeName[] = L"\\\\.\\pipe\\iag-stellaris-native-research";
constexpr char kProtocolVersion[] = "IAGW1";
constexpr char kBuildIdentity[] =
    "stellaris-4.4.6-windows-steam-24109497-bc451c72";
constexpr std::uint32_t kPeTimestamp = 0x6a4e461dU;
constexpr std::uint32_t kPeImageSize = 0x03950000U;
constexpr auto kExecutionTimeout = 15s;
constexpr auto kResponseTimeout = 20s;

constexpr std::uintptr_t kGameIdlerIdle = 0x00337530;
constexpr std::uintptr_t kPostCommandToSession = 0x00648970;
constexpr std::uintptr_t kMoveCommandVtable = 0x02544180;
constexpr std::uintptr_t kMoveCommandClone = 0x0097c220;
constexpr std::uintptr_t kMoveCommandToken = 0x0097c2f0;
constexpr std::uintptr_t kMoveCommandIsValid = 0x00ac15b0;
constexpr std::uintptr_t kCalcFtlPointWithCoordinate = 0x008c3b10;
constexpr std::uintptr_t kCelestialCoordinateVtable = 0x024d6c10;
constexpr std::uintptr_t kFleetDatabase = 0x03285688;
constexpr std::uintptr_t kNullFleet = 0x03286190;
constexpr std::uintptr_t kGalacticObjectDatabase = 0x03287348;
constexpr std::uintptr_t kNullGalacticObject = 0x03283fc8;

constexpr std::size_t kHookLength = 14;
constexpr std::size_t kMoveCommandSize = 0x58;
constexpr std::size_t kCoordinateSize = 0x28;
constexpr std::size_t kFleetObjectIdOffset = 0x30;
constexpr std::size_t kGalacticObjectIdOffset = 0x08;
constexpr std::size_t kFleetCoordinateProviderOffset = 0x38;
constexpr std::size_t kFleetExecutingOrderOffset = 0x2c8;
constexpr std::size_t kOrderTypeOffset = 0x18;
constexpr std::uint32_t kMoveOrderType = 0x2cde;

constexpr std::array<unsigned char, kHookLength> kGameIdlerIdlePrefix = {
    0x48, 0x8b, 0xc4, 0x88, 0x50, 0x10, 0x55,
    0x53, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55,
};
constexpr std::array<unsigned char, 16> kPostCommandPrefix = {
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x6c,
    0x24, 0x10, 0x48, 0x89, 0x74, 0x24, 0x18, 0x57,
};
constexpr std::array<unsigned char, 12> kMoveIsValidPrefix = {
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48,
    0x89, 0x74, 0x24, 0x10, 0x57, 0x48,
};
constexpr std::array<unsigned char, 12> kCalcFtlPrefix = {
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48,
    0x89, 0x6c, 0x24, 0x10, 0x48, 0x89,
};
constexpr std::array<unsigned char, 6> kMoveTokenBody = {
    0xb8, 0x4f, 0x2c, 0x00, 0x00, 0xc3,
};

using GameIdlerIdleFn = void(__fastcall*)(void*, bool);
using CoordinateGetterFn = const void*(__fastcall*)(const void*);
using CalcFtlPointFn = void*(__fastcall*)(const void*, void*, const void*);
using MoveIsValidFn = bool(__fastcall*)(const void*, void*);
using MoveCloneFn = void*(__fastcall*)(const void*);
using PostCommandFn = void(__fastcall*)(void*, bool);

enum class RequestPhase { kPending, kPosted, kDone };

struct MoveRequest {
  std::uint64_t generation = 0;
  std::string request_id;
  std::uint32_t fleet_id = 0;
  std::uint32_t destination_system_id = 0;
  RequestPhase phase = RequestPhase::kPending;
  std::chrono::steady_clock::time_point deadline{};
  std::string outcome;
  std::string detail;
};

struct CachedResponse {
  std::string request_id;
  std::string fingerprint;
  std::string response;
};

std::uintptr_t g_module_base = 0;
std::uintptr_t g_module_end = 0;
std::uintptr_t g_text_start = 0;
std::uintptr_t g_text_end = 0;
GameIdlerIdleFn g_original_idle = nullptr;
std::mutex g_request_mutex;
std::condition_variable g_request_changed;
std::optional<MoveRequest> g_request;
std::optional<CachedResponse> g_last_response;
std::atomic<std::uint64_t> g_generation{0};
std::atomic<bool> g_ready{false};
std::atomic<bool> g_shutdown{false};
thread_local bool g_inside_idle_hook = false;
char g_status[192] = "not_initialized";

template <typename Function>
Function FunctionAt(std::uintptr_t rva) {
  return reinterpret_cast<Function>(g_module_base + rva);
}

void Log(const char* format, ...) {
  wchar_t temporary_path[MAX_PATH]{};
  if (GetTempPathW(MAX_PATH, temporary_path) == 0) {
    return;
  }
  std::wstring path(temporary_path);
  path += L"iag-stellaris-native-windows.log";
  FILE* stream = nullptr;
  if (_wfopen_s(&stream, path.c_str(), L"a, ccs=UTF-8") != 0 || stream == nullptr) {
    return;
  }
  char message[1024]{};
  va_list arguments;
  va_start(arguments, format);
  vsnprintf_s(message, sizeof(message), _TRUNCATE, format, arguments);
  va_end(arguments);
  std::fwprintf(stream, L"%hs", message);
  std::fclose(stream);
}

void SetStatus(const char* value) {
  strncpy_s(g_status, value, _TRUNCATE);
  Log("status=%s\n", value);
}

bool InModule(const void* pointer) {
  const auto value = reinterpret_cast<std::uintptr_t>(pointer);
  return value >= g_module_base && value < g_module_end;
}

bool InText(const void* pointer) {
  const auto value = reinterpret_cast<std::uintptr_t>(pointer);
  return value >= g_text_start && value < g_text_end;
}

template <std::size_t Size>
bool Matches(std::uintptr_t rva, const std::array<unsigned char, Size>& expected) {
  return std::memcmp(
             reinterpret_cast<const void*>(g_module_base + rva),
             expected.data(),
             expected.size()) == 0;
}

bool InspectMainModule() {
  const auto module = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
  if (module == 0) {
    SetStatus("main_module_missing");
    return false;
  }
  const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(module);
  if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
    SetStatus("invalid_dos_header");
    return false;
  }
  const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(module + dos->e_lfanew);
  if (nt->Signature != IMAGE_NT_SIGNATURE ||
      nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
      nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) {
    SetStatus("invalid_pe_header");
    return false;
  }
  if (nt->FileHeader.TimeDateStamp != kPeTimestamp ||
      nt->OptionalHeader.SizeOfImage != kPeImageSize) {
    SetStatus("unsupported_windows_stellaris_build");
    return false;
  }
  g_module_base = module;
  g_module_end = module + nt->OptionalHeader.SizeOfImage;
  const auto* section = IMAGE_FIRST_SECTION(nt);
  for (unsigned index = 0; index < nt->FileHeader.NumberOfSections; ++index) {
    if (std::memcmp(section[index].Name, ".text", 5) == 0) {
      g_text_start = module + section[index].VirtualAddress;
      g_text_end = g_text_start + section[index].Misc.VirtualSize;
      break;
    }
  }
  if (g_text_start == 0 || !Matches(kGameIdlerIdle, kGameIdlerIdlePrefix) ||
      !Matches(kPostCommandToSession, kPostCommandPrefix) ||
      !Matches(kMoveCommandIsValid, kMoveIsValidPrefix) ||
      !Matches(kCalcFtlPointWithCoordinate, kCalcFtlPrefix) ||
      !Matches(kMoveCommandToken, kMoveTokenBody)) {
    SetStatus("windows_function_signature_mismatch");
    return false;
  }
  const auto* vtable = reinterpret_cast<const std::uintptr_t*>(
      g_module_base + kMoveCommandVtable);
  if (vtable[8] != g_module_base + kMoveCommandIsValid ||
      vtable[10] != g_module_base + kMoveCommandToken ||
      vtable[12] != g_module_base + kMoveCommandClone) {
    SetStatus("move_command_vtable_mismatch");
    return false;
  }
  return true;
}

void WriteAbsoluteJump(unsigned char* destination, const void* target) {
  destination[0] = 0xff;
  destination[1] = 0x25;
  std::memset(destination + 2, 0, 4);
  const auto address = reinterpret_cast<std::uintptr_t>(target);
  std::memcpy(destination + 6, &address, sizeof(address));
}

class SuspendedProcessThreads final {
 public:
  SuspendedProcessThreads() = default;
  SuspendedProcessThreads(const SuspendedProcessThreads&) = delete;
  SuspendedProcessThreads& operator=(const SuspendedProcessThreads&) = delete;

  ~SuspendedProcessThreads() { Resume(); }

  bool Collect() {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
      return false;
    }
    THREADENTRY32 entry{};
    entry.dwSize = sizeof(entry);
    const DWORD process_id = GetCurrentProcessId();
    const DWORD current_thread = GetCurrentThreadId();
    if (Thread32First(snapshot, &entry) != 0) {
      do {
        if (entry.th32OwnerProcessID != process_id ||
            entry.th32ThreadID == current_thread) {
          continue;
        }
        HANDLE thread = OpenThread(
            THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION,
            FALSE,
            entry.th32ThreadID);
        if (thread != nullptr) {
          threads_.push_back(thread);
        }
      } while (Thread32Next(snapshot, &entry) != 0);
    }
    CloseHandle(snapshot);
    suspended_.reserve(threads_.size());
    return !threads_.empty();
  }

  bool Suspend() {
    for (HANDLE thread : threads_) {
      if (SuspendThread(thread) == static_cast<DWORD>(-1)) {
        if (WaitForSingleObject(thread, 0) == WAIT_OBJECT_0) {
          continue;
        }
        Resume();
        return false;
      }
      suspended_.push_back(thread);
    }
    return true;
  }

  void Resume() {
    while (!suspended_.empty()) {
      ResumeThread(suspended_.back());
      suspended_.pop_back();
    }
    if (!closed_) {
      for (HANDLE thread : threads_) {
        CloseHandle(thread);
      }
      closed_ = true;
    }
  }

 private:
  std::vector<HANDLE> threads_;
  std::vector<HANDLE> suspended_;
  bool closed_ = false;
};

bool InstallIdleHook(void* hook) {
  auto* target = reinterpret_cast<unsigned char*>(g_module_base + kGameIdlerIdle);
  auto* trampoline = static_cast<unsigned char*>(VirtualAlloc(
      nullptr,
      kHookLength + 14,
      MEM_COMMIT | MEM_RESERVE,
      PAGE_EXECUTE_READWRITE));
  if (trampoline == nullptr) {
    SetStatus("trampoline_allocation_failed");
    return false;
  }
  std::memcpy(trampoline, target, kHookLength);
  WriteAbsoluteJump(trampoline + kHookLength, target + kHookLength);
  SuspendedProcessThreads suspended_threads;
  if (!suspended_threads.Collect()) {
    SetStatus("thread_snapshot_failed");
    return false;
  }
  if (!suspended_threads.Suspend()) {
    SetStatus("thread_suspend_failed");
    return false;
  }
  DWORD old_protection = 0;
  if (VirtualProtect(target, kHookLength, PAGE_EXECUTE_READWRITE, &old_protection) == 0) {
    suspended_threads.Resume();
    SetStatus("idle_hook_virtual_protect_failed");
    return false;
  }
  std::array<unsigned char, kHookLength> patch{};
  WriteAbsoluteJump(patch.data(), hook);
  std::memcpy(target, patch.data(), patch.size());
  FlushInstructionCache(GetCurrentProcess(), target, patch.size());
  DWORD ignored = 0;
  const bool protection_restored =
      VirtualProtect(target, kHookLength, old_protection, &ignored) != 0;
  suspended_threads.Resume();
  if (!protection_restored) {
    SetStatus("idle_hook_restore_protection_failed");
    return false;
  }
  g_original_idle = reinterpret_cast<GameIdlerIdleFn>(trampoline);
  return true;
}

const void* ResolveObject(
    std::uintptr_t database_rva,
    std::uintptr_t null_object_rva,
    std::size_t id_offset,
    std::uint32_t object_id) {
  const auto* database = reinterpret_cast<const unsigned char*>(
      *reinterpret_cast<void* const*>(g_module_base + database_rva));
  const void* null_object = *reinterpret_cast<void* const*>(
      g_module_base + null_object_rva);
  if (database == nullptr || null_object == nullptr) {
    return nullptr;
  }
  const std::uint32_t index = object_id & 0x00ffffffU;
  const std::uint32_t count =
      *reinterpret_cast<const std::uint32_t*>(database + 0x20);
  if (index >= count) {
    return nullptr;
  }
  const auto* entries = *reinterpret_cast<unsigned char* const*>(database + 0x18);
  if (entries == nullptr) {
    return nullptr;
  }
  const void* object =
      *reinterpret_cast<void* const*>(entries + index * 0x10U + 8U);
  if (object == nullptr || object == null_object) {
    return nullptr;
  }
  const auto resolved_id = *reinterpret_cast<const std::uint32_t*>(
      static_cast<const unsigned char*>(object) + id_offset);
  return resolved_id == object_id ? object : nullptr;
}

const void* FleetCoordinate(const void* fleet) {
  const auto* provider = static_cast<const unsigned char*>(fleet) +
                         kFleetCoordinateProviderOffset;
  const auto* vtable = *reinterpret_cast<void* const* const*>(provider);
  if (!InModule(vtable)) {
    return nullptr;
  }
  const void* function = vtable[1];
  if (!InText(function)) {
    return nullptr;
  }
  const void* coordinate =
      reinterpret_cast<CoordinateGetterFn>(const_cast<void*>(function))(provider);
  if (coordinate == nullptr ||
      *reinterpret_cast<const std::uintptr_t*>(coordinate) !=
          g_module_base + kCelestialCoordinateVtable) {
    return nullptr;
  }
  return coordinate;
}

std::uint32_t ExecutingOrderType(const void* fleet) {
  const void* order = *reinterpret_cast<void* const*>(
      static_cast<const unsigned char*>(fleet) + kFleetExecutingOrderOffset);
  if (order == nullptr || !InModule(*reinterpret_cast<void* const*>(order))) {
    return 0;
  }
  return *reinterpret_cast<const std::uint32_t*>(
      static_cast<const unsigned char*>(order) + kOrderTypeOffset);
}

std::string Fingerprint(std::uint32_t fleet, std::uint32_t destination) {
  return std::to_string(fleet) + ":" + std::to_string(destination);
}

std::string Sanitize(std::string value) {
  for (char& character : value) {
    if (character == '\t' || character == '\r' || character == '\n') {
      character = ' ';
    }
  }
  if (value.size() > 240) {
    value.resize(240);
  }
  return value;
}

std::string BuildResponse(const MoveRequest& request) {
  return std::string(kProtocolVersion) + '\t' + request.request_id +
         "\tmove_fleet\t" + request.outcome + '\t' + kBuildIdentity + '\t' +
         Fingerprint(request.fleet_id, request.destination_system_id) + '\t' +
         Sanitize(request.detail) + '\n';
}

std::string ImmediateResponse(
    std::string_view request_id,
    std::string_view action,
    std::string_view outcome,
    std::string_view detail) {
  return std::string(kProtocolVersion) + '\t' + std::string(request_id) + '\t' +
         std::string(action) + '\t' + std::string(outcome) + '\t' +
         kBuildIdentity + "\t-\t" + Sanitize(std::string(detail)) + '\n';
}

void Finish(std::uint64_t generation, std::string outcome, std::string detail) {
  std::lock_guard lock(g_request_mutex);
  if (!g_request.has_value() || g_request->generation != generation ||
      g_request->phase == RequestPhase::kDone) {
    return;
  }
  g_request->outcome = std::move(outcome);
  g_request->detail = std::move(detail);
  g_request->phase = RequestPhase::kDone;
  g_request_changed.notify_all();
}

void ProcessMoveBeforeUpdate() {
  MoveRequest request;
  {
    std::lock_guard lock(g_request_mutex);
    if (!g_request.has_value() || g_request->phase != RequestPhase::kPending) {
      return;
    }
    request = *g_request;
  }

  const void* fleet = ResolveObject(
      kFleetDatabase, kNullFleet, kFleetObjectIdOffset, request.fleet_id);
  if (fleet == nullptr) {
    Finish(request.generation, "rejected", "unknown_fleet_id");
    return;
  }
  const void* destination = ResolveObject(
      kGalacticObjectDatabase,
      kNullGalacticObject,
      kGalacticObjectIdOffset,
      request.destination_system_id);
  if (destination == nullptr) {
    Finish(request.generation, "rejected", "unknown_destination_system_id");
    return;
  }
  const void* source_coordinate = FleetCoordinate(fleet);
  if (source_coordinate == nullptr) {
    Finish(request.generation, "failed", "fleet_coordinate_unavailable");
    return;
  }

  alignas(16) std::array<std::byte, kCoordinateSize> destination_coordinate{};
  FunctionAt<CalcFtlPointFn>(kCalcFtlPointWithCoordinate)(
      destination, destination_coordinate.data(), source_coordinate);
  if (*reinterpret_cast<const std::uintptr_t*>(destination_coordinate.data()) !=
          g_module_base + kCelestialCoordinateVtable ||
      *reinterpret_cast<const std::uint32_t*>(
          destination_coordinate.data() + 0x20) !=
          request.destination_system_id) {
    Finish(request.generation, "failed", "native_ftl_coordinate_invalid");
    return;
  }

  alignas(16) std::array<std::byte, kMoveCommandSize> command{};
  *reinterpret_cast<std::uintptr_t*>(command.data()) =
      g_module_base + kMoveCommandVtable;
  *reinterpret_cast<std::uint32_t*>(command.data() + 0x08) = 0xffffffffU;
  *reinterpret_cast<std::uint32_t*>(command.data() + 0x0c) = 0;
  *reinterpret_cast<std::uint32_t*>(command.data() + 0x10) = 0xffff0000U;
  *reinterpret_cast<std::uint16_t*>(command.data() + 0x14) = 0;
  *reinterpret_cast<std::uint8_t*>(command.data() + 0x16) = 0;
  *reinterpret_cast<std::uint32_t*>(command.data() + 0x18) = 0;
  *reinterpret_cast<std::uint32_t*>(command.data() + 0x20) = request.fleet_id;
  std::memcpy(command.data() + 0x28, destination_coordinate.data(), kCoordinateSize);
  *reinterpret_cast<std::uint8_t*>(command.data() + 0x50) = 0;
  *reinterpret_cast<std::uint8_t*>(command.data() + 0x51) = 0;

  if (!FunctionAt<MoveIsValidFn>(kMoveCommandIsValid)(command.data(), nullptr)) {
    Finish(request.generation, "rejected", "native_is_valid_false_before_submit");
    return;
  }
  {
    std::lock_guard lock(g_request_mutex);
    if (!g_request.has_value() || g_request->generation != request.generation ||
        g_request->phase != RequestPhase::kPending) {
      Log("request generation changed before native submit\n");
      return;
    }
    g_request->phase = RequestPhase::kPosted;
    g_request->deadline = std::chrono::steady_clock::now() + kExecutionTimeout;
  }
  void* owned_command = FunctionAt<MoveCloneFn>(kMoveCommandClone)(command.data());
  if (owned_command == nullptr) {
    Finish(request.generation, "failed", "native_command_clone_failed");
    return;
  }
  FunctionAt<PostCommandFn>(kPostCommandToSession)(owned_command, false);
  Log(
      "request=%s action=move_fleet fleet=%u destination=%u posted\n",
      request.request_id.c_str(),
      request.fleet_id,
      request.destination_system_id);
}

void VerifyMoveAfterUpdate() {
  MoveRequest request;
  {
    std::lock_guard lock(g_request_mutex);
    if (!g_request.has_value() || g_request->phase != RequestPhase::kPosted) {
      return;
    }
    request = *g_request;
  }
  const void* fleet = ResolveObject(
      kFleetDatabase, kNullFleet, kFleetObjectIdOffset, request.fleet_id);
  if (fleet != nullptr && ExecutingOrderType(fleet) == kMoveOrderType) {
    Finish(
        request.generation,
        "confirmed",
        "native_postcondition_move_order_2cde_present");
    Log(
        "request=%s action=move_fleet fleet=%u destination=%u confirmed\n",
        request.request_id.c_str(),
        request.fleet_id,
        request.destination_system_id);
    return;
  }
  if (std::chrono::steady_clock::now() >= request.deadline) {
    Finish(
        request.generation,
        "pending",
        "native_submit_timeout_waiting_for_move_order");
  }
}

void __fastcall GameIdlerIdleHook(void* idler, bool run_game_update) {
  if (g_original_idle == nullptr) {
    return;
  }
  if (g_inside_idle_hook) {
    g_original_idle(idler, run_game_update);
    return;
  }
  g_inside_idle_hook = true;
  if (run_game_update) {
    ProcessMoveBeforeUpdate();
  }
  g_original_idle(idler, run_game_update);
  if (run_game_update) {
    VerifyMoveAfterUpdate();
  }
  g_inside_idle_hook = false;
}

std::vector<std::string> SplitTabs(std::string_view line) {
  std::vector<std::string> fields;
  while (true) {
    const std::size_t separator = line.find('\t');
    fields.emplace_back(line.substr(0, separator));
    if (separator == std::string_view::npos) {
      return fields;
    }
    line.remove_prefix(separator + 1);
  }
}

std::optional<std::uint32_t> ParseUint32(std::string_view value) {
  std::uint32_t result = 0;
  const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
  if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size()) {
    return std::nullopt;
  }
  return result;
}

bool ReadLine(HANDLE pipe, std::string& output) {
  output.clear();
  char character = 0;
  while (output.size() <= 1024) {
    DWORD read = 0;
    if (ReadFile(pipe, &character, 1, &read, nullptr) == 0 || read != 1) {
      return false;
    }
    if (character == '\n') {
      return true;
    }
    if (character != '\r') {
      output.push_back(character);
    }
  }
  return false;
}

bool WriteAll(HANDLE pipe, std::string_view data) {
  while (!data.empty()) {
    DWORD written = 0;
    if (WriteFile(
            pipe,
            data.data(),
            static_cast<DWORD>(data.size()),
            &written,
            nullptr) == 0 ||
        written == 0) {
      return false;
    }
    data.remove_prefix(written);
  }
  return true;
}

std::string HandleRequest(const std::string& line) {
  const auto fields = SplitTabs(line);
  if (fields.size() == 2 && fields[0] == kProtocolVersion &&
      fields[1] == "status") {
    return ImmediateResponse("status", "status", g_ready ? "ready" : "failed", g_status);
  }
  if (fields.size() != 6 || fields[0] != kProtocolVersion ||
      fields[2] != "move_fleet") {
    return ImmediateResponse("-", "-", "rejected", "invalid_request_shape");
  }
  const auto fleet_id = ParseUint32(fields[4]);
  const auto destination_id = ParseUint32(fields[5]);
  if (fields[1].empty() || !fleet_id.has_value() || !destination_id.has_value()) {
    return ImmediateResponse(fields[1], fields[2], "rejected", "invalid_semantic_target");
  }
  const std::string fingerprint = Fingerprint(*fleet_id, *destination_id);
  std::unique_lock lock(g_request_mutex);
  if (g_last_response.has_value() && g_last_response->request_id == fields[1]) {
    if (g_last_response->fingerprint == fingerprint) {
      return g_last_response->response;
    }
    return ImmediateResponse(fields[1], fields[2], "rejected", "request_id_payload_conflict");
  }
  if (g_request.has_value()) {
    if (g_request->request_id != fields[1]) {
      return ImmediateResponse(fields[1], fields[2], "rejected", "another_request_active");
    }
    if (Fingerprint(g_request->fleet_id, g_request->destination_system_id) !=
        fingerprint) {
      return ImmediateResponse(fields[1], fields[2], "rejected", "request_id_payload_conflict");
    }
  } else {
    g_request = MoveRequest{
        .generation = ++g_generation,
        .request_id = fields[1],
        .fleet_id = *fleet_id,
        .destination_system_id = *destination_id,
    };
  }
  const bool completed = g_request_changed.wait_for(lock, kResponseTimeout, [] {
    return g_request.has_value() && g_request->phase == RequestPhase::kDone;
  });
  if (!completed) {
    return ImmediateResponse(fields[1], fields[2], "pending", "runtime_response_timeout");
  }
  const std::string response = BuildResponse(*g_request);
  g_last_response = CachedResponse{fields[1], fingerprint, response};
  g_request.reset();
  return response;
}

DWORD WINAPI PipeServer(void*) {
  while (!g_shutdown.load()) {
    HANDLE pipe = CreateNamedPipeW(
        kPipeName,
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1,
        2048,
        2048,
        1000,
        nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
      SetStatus("named_pipe_creation_failed");
      return 1;
    }
    const bool connected = ConnectNamedPipe(pipe, nullptr) != 0 ||
                           GetLastError() == ERROR_PIPE_CONNECTED;
    if (connected) {
      std::string line;
      if (ReadLine(pipe, line)) {
        const std::string response = HandleRequest(line);
        WriteAll(pipe, response);
      }
      FlushFileBuffers(pipe);
      DisconnectNamedPipe(pipe);
    }
    CloseHandle(pipe);
  }
  return 0;
}

DWORD WINAPI Bootstrap(void*) {
  if (!InspectMainModule()) {
    return 1;
  }
  // Let the remote LoadLibrary thread leave the loader lock before pausing
  // the simulation threads for the short, instruction-patch critical section.
  Sleep(250);
  if (!InstallIdleHook(reinterpret_cast<void*>(&GameIdlerIdleHook))) {
    return 1;
  }
  SetStatus("ready");
  g_ready = true;
  Log(
      "runtime ready build=%s base=0x%llx hook_rva=0x%llx\n",
      kBuildIdentity,
      static_cast<unsigned long long>(g_module_base),
      static_cast<unsigned long long>(kGameIdlerIdle));
  HANDLE server = CreateThread(nullptr, 0, &PipeServer, nullptr, 0, nullptr);
  if (server != nullptr) {
    CloseHandle(server);
  } else {
    SetStatus("pipe_server_thread_failed");
    g_ready = false;
  }
  return 0;
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    DisableThreadLibraryCalls(module);
    HANDLE bootstrap = CreateThread(nullptr, 0, &Bootstrap, nullptr, 0, nullptr);
    if (bootstrap != nullptr) {
      CloseHandle(bootstrap);
    }
  } else if (reason == DLL_PROCESS_DETACH) {
    g_shutdown = true;
  }
  return TRUE;
}
