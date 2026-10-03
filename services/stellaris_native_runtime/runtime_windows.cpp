#include <windows.h>
#include <tlhelp32.h>

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "iag_native_runtime/action.h"
#include "iag_native_runtime/game_api.h"
#include "iag_native_runtime/version_profile.h"

namespace iag::native_runtime {
namespace {

using namespace std::chrono_literals;

constexpr wchar_t kDefaultPipeName[] = L"\\\\.\\pipe\\iag-stellaris-native";
constexpr char kProtocolVersion[] = "IAG1";
constexpr char kDescribeToolsAction[] = "describe_tools";
constexpr auto kExecutionTimeout = 15s;
constexpr auto kResponseTimeout = 20s;

using GameIdlerIdleFn = void(__fastcall*)(void*, bool);

enum class RequestPhase { kPending, kPosted, kDone };

struct RuntimeRequest {
  std::uint64_t generation = 0;
  std::string request_id;
  std::string action_type;
  std::string target_echo;
  std::uint32_t country_id = 0;
  std::shared_ptr<ActionInvocation> invocation;
  RequestPhase phase = RequestPhase::kPending;
  std::chrono::steady_clock::time_point deadline;
  RuntimeResult result;
};

struct CachedResult {
  std::string request_id;
  std::string payload_fingerprint;
  std::string response;
};

GameIdlerIdleFn g_original_game_idler_idle = nullptr;
std::unique_ptr<GameApi> g_game;
std::mutex g_request_mutex;
std::condition_variable g_request_changed;
std::optional<RuntimeRequest> g_active_request;
std::optional<CachedResult> g_last_result;
std::atomic<std::uint64_t> g_generation{0};
std::atomic<bool> g_shutdown{false};
std::mutex g_log_mutex;
char g_runtime_status[256] = "not_initialized";
thread_local bool g_inside_idle_hook = false;

const BuildProfile& Profile() { return Stellaris446WindowsProfile(); }

void Log(const char* format, ...) {
  std::lock_guard lock(g_log_mutex);
  wchar_t temporary_path[MAX_PATH]{};
  if (GetTempPathW(MAX_PATH, temporary_path) == 0) {
    return;
  }
  std::wstring path(temporary_path);
  path += L"iag-stellaris-native.log";
  FILE* stream = nullptr;
  if (_wfopen_s(&stream, path.c_str(), L"a, ccs=UTF-8") != 0 ||
      stream == nullptr) {
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

bool VerifyExecutable() {
  const auto module = reinterpret_cast<std::uintptr_t>(GetModuleHandleW(nullptr));
  if (module == 0U) {
    std::snprintf(g_runtime_status, sizeof(g_runtime_status), "main_module_missing");
    return false;
  }
  const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(module);
  if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
    std::snprintf(g_runtime_status, sizeof(g_runtime_status), "invalid_dos_header");
    return false;
  }
  const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(
      module + static_cast<std::uintptr_t>(dos->e_lfanew));
  if (nt->Signature != IMAGE_NT_SIGNATURE ||
      nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
      nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
      nt->FileHeader.TimeDateStamp != Profile().core.pe_timestamp ||
      nt->OptionalHeader.SizeOfImage != Profile().core.image_size) {
    std::snprintf(
        g_runtime_status,
        sizeof(g_runtime_status),
        "unsupported_windows_stellaris_build");
    return false;
  }
  g_game = std::make_unique<GameApi>(module, Profile());
  if (!g_game->VerifyRequiredAnchors()) {
    std::snprintf(
        g_runtime_status, sizeof(g_runtime_status), "function_signature_mismatch");
    g_game.reset();
    return false;
  }
  const auto& move = Profile().move_fleet;
  const auto* vtable = reinterpret_cast<const std::uintptr_t*>(
      g_game->Address(move.command_vtable));
  if (vtable[8] != g_game->Address(move.command_is_valid) ||
      vtable[12] != g_game->Address(move.clone_command)) {
    std::snprintf(
        g_runtime_status, sizeof(g_runtime_status), "move_command_vtable_mismatch");
    g_game.reset();
    return false;
  }
  return true;
}

void WriteAbsoluteJump(unsigned char* destination, const void* target) {
  destination[0] = 0xff;
  destination[1] = 0x25;
  std::memset(destination + 2, 0, 4);
  const auto value = reinterpret_cast<std::uintptr_t>(target);
  std::memcpy(destination + 6, &value, sizeof(value));
}

class SuspendedProcessThreads final {
 public:
  SuspendedProcessThreads() = default;
  SuspendedProcessThreads(const SuspendedProcessThreads&) = delete;
  SuspendedProcessThreads& operator=(const SuspendedProcessThreads&) = delete;
  ~SuspendedProcessThreads() { Resume(); }

  bool CollectAndSuspend() {
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
    for (HANDLE thread : threads_) {
      if (SuspendThread(thread) == static_cast<DWORD>(-1)) {
        Resume();
        return false;
      }
      suspended_.push_back(thread);
    }
    return !threads_.empty();
  }

  void Resume() {
    while (!suspended_.empty()) {
      ResumeThread(suspended_.back());
      suspended_.pop_back();
    }
    for (HANDLE thread : threads_) {
      CloseHandle(thread);
    }
    threads_.clear();
  }

 private:
  std::vector<HANDLE> threads_;
  std::vector<HANDLE> suspended_;
};

bool InstallGameIdlerIdleHook(void* hook) {
  const std::size_t hook_length = Profile().core.hook_length;
  auto* target = reinterpret_cast<unsigned char*>(
      g_game->Address(Profile().core.game_idler_idle));
  auto* trampoline = static_cast<unsigned char*>(VirtualAlloc(
      nullptr,
      hook_length + 14U,
      MEM_COMMIT | MEM_RESERVE,
      PAGE_EXECUTE_READWRITE));
  if (trampoline == nullptr) {
    std::snprintf(
        g_runtime_status, sizeof(g_runtime_status), "trampoline_allocation_failed");
    return false;
  }
  std::memcpy(trampoline, target, hook_length);
  WriteAbsoluteJump(trampoline + hook_length, target + hook_length);
  SuspendedProcessThreads suspended;
  if (!suspended.CollectAndSuspend()) {
    std::snprintf(g_runtime_status, sizeof(g_runtime_status), "thread_suspend_failed");
    return false;
  }
  DWORD old_protection = 0;
  if (VirtualProtect(
          target, hook_length, PAGE_EXECUTE_READWRITE, &old_protection) == 0) {
    return false;
  }
  std::vector<unsigned char> patch(hook_length);
  WriteAbsoluteJump(patch.data(), hook);
  std::memcpy(target, patch.data(), patch.size());
  FlushInstructionCache(GetCurrentProcess(), target, patch.size());
  DWORD ignored = 0;
  if (VirtualProtect(target, hook_length, old_protection, &ignored) == 0) {
    std::snprintf(
        g_runtime_status, sizeof(g_runtime_status), "hook_restore_protection_failed");
    return false;
  }
  suspended.Resume();
  g_original_game_idler_idle = reinterpret_cast<GameIdlerIdleFn>(trampoline);
  return true;
}

std::vector<std::string> SplitTabs(std::string_view line) {
  std::vector<std::string> fields;
  while (true) {
    const std::size_t separator = line.find('\t');
    fields.emplace_back(line.substr(0, separator));
    if (separator == std::string_view::npos) {
      return fields;
    }
    line.remove_prefix(separator + 1U);
  }
}

std::string SanitizeDetail(std::string detail) {
  for (char& character : detail) {
    if (character == '\t' || character == '\r' || character == '\n') {
      character = ' ';
    }
  }
  if (detail.size() > 240U) {
    detail.resize(240U);
  }
  return detail;
}

std::string RequestFingerprint(const RuntimeRequest& request) {
  return request.action_type + '\t' + std::to_string(request.country_id) + '\t' +
         request.target_echo;
}

std::string BuildResponse(const RuntimeRequest& request) {
  return std::string(kProtocolVersion) + '\t' + request.request_id + '\t' +
         request.action_type + '\t' + request.result.outcome + '\t' +
         Profile().build_id + '\t' + std::to_string(request.country_id) + '\t' +
         request.target_echo + '\t' + SanitizeDetail(request.result.detail) + '\n';
}

std::string ImmediateResponse(
    std::string_view request_id,
    std::string_view action,
    std::string_view outcome,
    std::string_view detail) {
  return std::string(kProtocolVersion) + '\t' + std::string(request_id) + '\t' +
         std::string(action) + '\t' + std::string(outcome) + '\t' +
         Profile().build_id + "\t0\t-\t" +
         SanitizeDetail(std::string(detail)) + '\n';
}

std::string ToolManifestResponse(std::string_view request_id) {
  return std::string(kProtocolVersion) + '\t' + std::string(request_id) + '\t' +
         kDescribeToolsAction + "\tconfirmed\t" + Profile().build_id +
         "\t0\t" + BuildToolManifestJson(Profile()) +
         "\truntime_tool_manifest\n";
}

void FinishRequest(
    std::uint64_t generation,
    std::string outcome,
    std::string detail) {
  std::lock_guard lock(g_request_mutex);
  if (!g_active_request.has_value() ||
      g_active_request->generation != generation) {
    return;
  }
  g_active_request->result = {std::move(outcome), std::move(detail)};
  g_active_request->phase = RequestPhase::kDone;
  g_request_changed.notify_all();
}

void ProcessPendingBeforeUpdate() {
  RuntimeRequest request;
  {
    std::lock_guard lock(g_request_mutex);
    if (!g_active_request.has_value() ||
        g_active_request->phase != RequestPhase::kPending) {
      return;
    }
    request = *g_active_request;
  }
  PrepareResult prepared =
      request.invocation->Prepare(*g_game, request.country_id, nullptr);
  if (prepared.status != PreparationStatus::kReady) {
    FinishRequest(
        request.generation,
        prepared.status == PreparationStatus::kRejected ? "rejected" : "failed",
        std::move(prepared.detail));
    return;
  }
  if (prepared.command == nullptr) {
    FinishRequest(request.generation, "failed", "action_returned_null_command");
    return;
  }
  {
    std::lock_guard lock(g_request_mutex);
    if (!g_active_request.has_value() ||
        g_active_request->generation != request.generation) {
      // The active request cannot normally change while the game thread is in
      // Prepare. Do not cross heaps by deleting a game-owned clone.
      return;
    }
    g_active_request->deadline =
        std::chrono::steady_clock::now() + kExecutionTimeout;
    g_active_request->phase = RequestPhase::kPosted;
  }
  g_game->PostCommand(prepared.command);
  Log(
      "request=%s action=%s country=%u target=%s posted\n",
      request.request_id.c_str(),
      request.action_type.c_str(),
      request.country_id,
      request.target_echo.c_str());
}

void VerifyPostedAfterUpdate() {
  RuntimeRequest request;
  {
    std::lock_guard lock(g_request_mutex);
    if (!g_active_request.has_value() ||
        g_active_request->phase != RequestPhase::kPosted) {
      return;
    }
    request = *g_active_request;
  }
  const auto result =
      request.invocation->Verify(*g_game, request.country_id, nullptr);
  if (result.has_value()) {
    FinishRequest(request.generation, result->outcome, result->detail);
    return;
  }
  if (std::chrono::steady_clock::now() >= request.deadline) {
    FinishRequest(
        request.generation,
        "pending",
        std::string(request.invocation->TimeoutDetail()));
  }
}

void __fastcall GameIdlerIdleHook(void* idler, bool run_game_update) {
  if (g_original_game_idler_idle == nullptr) {
    return;
  }
  if (g_inside_idle_hook) {
    g_original_game_idler_idle(idler, run_game_update);
    return;
  }
  g_inside_idle_hook = true;
  if (run_game_update) {
    ProcessPendingBeforeUpdate();
  }
  g_original_game_idler_idle(idler, run_game_update);
  if (run_game_update) {
    VerifyPostedAfterUpdate();
  }
  g_inside_idle_hook = false;
}

std::string HandleRequest(std::string_view line) {
  const auto fields = SplitTabs(line);
  if (fields.size() == 4U && fields[0] == kProtocolVersion &&
      fields[2] == kDescribeToolsAction && fields[3] == "0") {
    return IsSemanticToken(fields[1], 96U)
               ? ToolManifestResponse(fields[1])
               : ImmediateResponse(
                     fields[1], fields[2], "rejected", "invalid_request_id");
  }
  if (fields.size() < 5U || fields[0] != kProtocolVersion) {
    return ImmediateResponse("invalid", "unknown", "rejected", "bad_request");
  }
  const std::string& request_id = fields[1];
  const std::string& action_type = fields[2];
  const auto country_id = ParseUint32(fields[3]);
  if (!IsSemanticToken(request_id, 96U) || !country_id.has_value()) {
    return ImmediateResponse(
        request_id, action_type, "rejected", "invalid_semantic_target");
  }
  const ActionDescriptor* descriptor = FindActionDescriptor(action_type);
  if (descriptor == nullptr || !descriptor->supported(Profile())) {
    return ImmediateResponse(
        request_id, action_type, "rejected", "unsupported_action");
  }
  std::string parse_error;
  const auto target_fields = std::span<const std::string>(fields).subspan(4U);
  std::unique_ptr<ActionInvocation> parsed =
      descriptor->parse(target_fields, parse_error);
  if (parsed == nullptr) {
    return ImmediateResponse(
        request_id,
        action_type,
        "rejected",
        parse_error.empty() ? "invalid_semantic_target" : parse_error);
  }
  const std::string target_echo = parsed->TargetEcho();
  if (parsed->action_type() != descriptor->action_type ||
      !IsSemanticToken(target_echo, 160U)) {
    return ImmediateResponse(
        request_id, action_type, "failed", "action_handler_contract_violation");
  }
  RuntimeRequest parsed_request;
  parsed_request.request_id = request_id;
  parsed_request.action_type = action_type;
  parsed_request.target_echo = target_echo;
  parsed_request.country_id = *country_id;
  parsed_request.invocation = std::shared_ptr<ActionInvocation>(std::move(parsed));
  const std::string fingerprint = RequestFingerprint(parsed_request);

  std::unique_lock lock(g_request_mutex);
  if (g_last_result.has_value() && g_last_result->request_id == request_id) {
    return g_last_result->payload_fingerprint == fingerprint
               ? g_last_result->response
               : ImmediateResponse(
                     request_id,
                     action_type,
                     "conflict",
                     "request_id_payload_conflict");
  }
  if (g_active_request.has_value()) {
    if (g_active_request->request_id != request_id) {
      return ImmediateResponse(
          request_id, action_type, "busy", "runtime_request_active");
    }
    if (RequestFingerprint(*g_active_request) != fingerprint) {
      return ImmediateResponse(
          request_id,
          action_type,
          "conflict",
          "request_id_payload_conflict");
    }
  } else {
    parsed_request.generation = ++g_generation;
    g_active_request = std::move(parsed_request);
  }
  const std::uint64_t generation = g_active_request->generation;
  const bool completed = g_request_changed.wait_for(
      lock, kResponseTimeout, [generation] {
        return g_active_request.has_value() &&
               g_active_request->generation == generation &&
               g_active_request->phase == RequestPhase::kDone;
      });
  if (!completed) {
    return ImmediateResponse(
        request_id, action_type, "pending", "runtime_response_timeout");
  }
  const std::string response = BuildResponse(*g_active_request);
  g_last_result = CachedResult{request_id, fingerprint, response};
  g_active_request.reset();
  return response;
}

bool ReadLine(HANDLE pipe, std::string& output) {
  output.clear();
  char character = 0;
  while (output.size() <= 65'536U) {
    DWORD read = 0;
    if (ReadFile(pipe, &character, 1, &read, nullptr) == 0 || read != 1U) {
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
        written == 0U) {
      return false;
    }
    data.remove_prefix(written);
  }
  return true;
}

DWORD WINAPI PipeServer(void*) {
  while (!g_shutdown.load()) {
    HANDLE pipe = CreateNamedPipeW(
        kDefaultPipeName,
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1,
        65'536,
        8192,
        1000,
        nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
      return 1;
    }
    const bool connected = ConnectNamedPipe(pipe, nullptr) != 0 ||
                           GetLastError() == ERROR_PIPE_CONNECTED;
    if (connected) {
      std::string line;
      const std::string response =
          ReadLine(pipe, line)
              ? HandleRequest(line)
              : ImmediateResponse(
                    "invalid", "unknown", "rejected", "request_read_failed");
      WriteAll(pipe, response);
      FlushFileBuffers(pipe);
      DisconnectNamedPipe(pipe);
    }
    CloseHandle(pipe);
  }
  return 0;
}

DWORD WINAPI Bootstrap(void*) {
  std::string descriptor_error;
  if (!ValidateActionDescriptors(descriptor_error)) {
    std::snprintf(
        g_runtime_status,
        sizeof(g_runtime_status),
        "action_registry_invalid:%s",
        descriptor_error.c_str());
    return 1;
  }
  if (!VerifyExecutable()) {
    Log("runtime rejected: %s\n", g_runtime_status);
    return 1;
  }
  Sleep(250);
  if (!InstallGameIdlerIdleHook(reinterpret_cast<void*>(&GameIdlerIdleHook))) {
    Log("runtime hook failed: %s\n", g_runtime_status);
    return 1;
  }
  std::snprintf(g_runtime_status, sizeof(g_runtime_status), "ready");
  Log(
      "runtime ready pipe=iag-stellaris-native build_id=%s "
      "hook=CGameIdler::Idle\n",
      Profile().build_id);
  HANDLE server = CreateThread(nullptr, 0, &PipeServer, nullptr, 0, nullptr);
  if (server == nullptr) {
    std::snprintf(
        g_runtime_status, sizeof(g_runtime_status), "pipe_server_thread_failed");
    return 1;
  }
  CloseHandle(server);
  return 0;
}

}  // namespace

const char* RuntimeStatus() noexcept { return g_runtime_status; }
DWORD WINAPI InitializeRuntimeThread(void* context) { return Bootstrap(context); }
void SignalRuntimeShutdown() noexcept { g_shutdown = true; }

}  // namespace iag::native_runtime

extern "C" __declspec(dllexport) const char* iag_native_runtime_status() {
  return iag::native_runtime::RuntimeStatus();
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    DisableThreadLibraryCalls(module);
    HANDLE bootstrap = CreateThread(
        nullptr,
        0,
        &iag::native_runtime::InitializeRuntimeThread,
        nullptr,
        0,
        nullptr);
    if (bootstrap != nullptr) {
      CloseHandle(bootstrap);
    }
  } else if (reason == DLL_PROCESS_DETACH) {
    iag::native_runtime::SignalRuntimeShutdown();
  }
  return TRUE;
}
