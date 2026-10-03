#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

#include "iag_native_runtime/action.h"
#include "iag_native_runtime/game_api.h"
#include "iag_native_runtime/version_profile.h"

namespace iag::native_runtime {
namespace {

using namespace std::chrono_literals;

constexpr char kProtocol[] = "IAGTAKE1";
constexpr char kDefaultSocketPath[] =
    "/tmp/iag-stellaris-observer-takeover.sock";
constexpr char kDefaultLogPath[] =
    "/tmp/iag-stellaris-observer-takeover.jsonl";
constexpr char kExpectedBuildId[] =
    "c6969e60fd81d738948222a94c0b5a0841abbffc";
constexpr std::uintptr_t kPostAiCommandsOffset = 0x238eb90;
constexpr std::uintptr_t kAddAiCommandOffset = 0x237d480;
constexpr std::size_t kPostAiHookLength = 15;
constexpr std::size_t kAbsoluteJumpLength = 14;
constexpr std::size_t kRelativeJumpLength = 5;
constexpr std::size_t kCountryRefOffset = 0x3a0;
constexpr std::size_t kPendingStorageOffset = 0x520;
constexpr std::size_t kPendingCountOffset = 0x52c;
constexpr int kMaximumPendingCommands = 4096;
constexpr std::size_t kObservedVtableCapacity = 8;

constexpr std::array<unsigned char, kPostAiHookLength> kPostAiPrefix = {
    0x41, 0x57, 0x41, 0x56, 0x53, 0x49, 0x89, 0xff,
    0x44, 0x8b, 0xb7, 0x2c, 0x05, 0x00, 0x00,
};
constexpr std::array<unsigned char, 12> kAddAiPrefix = {
    0x41, 0x57, 0x41, 0x56, 0x53, 0x48,
    0x83, 0xec, 0x10, 0x48, 0x89, 0xf3,
};

using PostAiCommandsFn = void (*)(void*);
using AddAiCommandFn = void (*)(void*, void*);
using DeletingDestructorFn = void (*)(void*);

enum class Mode {
  kObserving,
  kArmed,
  kExecuting,
  kAwaitingVerification,
  kCompleted,
  kFailed,
  kDisarmed,
};

struct AddressRange {
  std::uintptr_t begin = 0;
  std::uintptr_t end = 0;
};

struct MainModuleIdentity {
  std::uintptr_t base = 0;
  std::uintptr_t end = 0;
  std::string build_id;
  std::array<AddressRange, 8> executable_ranges{};
  std::size_t executable_range_count = 0;
};

struct HookPatch {
  unsigned char* target = nullptr;
  void* trampoline = nullptr;
  void* relay = nullptr;
  std::size_t length = 0;
  std::array<unsigned char, 32> original{};
  bool applied = false;
};

struct ControlState {
  Mode mode = Mode::kObserving;
  std::uint64_t generation = 0;
  std::uint32_t country_id = UINT32_MAX;
  std::uint32_t fleet_id = UINT32_MAX;
  std::uint32_t destination_system_id = UINT32_MAX;
  std::unique_ptr<ActionInvocation> invocation;
  std::chrono::steady_clock::time_point deadline{};
  std::string detail = "read_only_observation";
};

struct ArmedRequest {
  std::uint64_t generation = 0;
  std::uint32_t country_id = UINT32_MAX;
  std::uint32_t fleet_id = UINT32_MAX;
  std::uint32_t destination_system_id = UINT32_MAX;
};

std::uintptr_t g_module_base = 0;
std::uintptr_t g_module_end = 0;
std::array<AddressRange, 8> g_executable_ranges{};
std::size_t g_executable_range_count = 0;
std::unique_ptr<GameApi> g_game;
PostAiCommandsFn g_original_post_ai_commands = nullptr;
AddAiCommandFn g_add_ai_command = nullptr;
HookPatch g_post_ai_patch;
std::mutex g_control_mutex;
ControlState g_control;
std::atomic<std::uint64_t> g_observed_batches{0};
std::atomic<std::uint64_t> g_replacement_batches{0};
std::atomic<std::uint64_t> g_suppressed_commands{0};
int g_log_fd = -1;
char g_probe_status[256] = "not_initialized";
thread_local bool g_inside_post_ai_hook = false;

const char* ModeName(Mode mode) {
  switch (mode) {
    case Mode::kObserving:
      return "observing";
    case Mode::kArmed:
      return "armed";
    case Mode::kExecuting:
      return "executing";
    case Mode::kAwaitingVerification:
      return "awaiting_verification";
    case Mode::kCompleted:
      return "completed";
    case Mode::kFailed:
      return "failed";
    case Mode::kDisarmed:
      return "disarmed";
  }
  return "unknown";
}

std::size_t Align4(std::size_t value) {
  return (value + 3U) & ~std::size_t{3U};
}

int ReadMainModuleIdentity(dl_phdr_info* info, std::size_t, void* opaque) {
  if (info->dlpi_name != nullptr && info->dlpi_name[0] != '\0') {
    return 0;
  }
  auto* identity = static_cast<MainModuleIdentity*>(opaque);
  identity->base = static_cast<std::uintptr_t>(info->dlpi_addr);
  for (ElfW(Half) index = 0; index < info->dlpi_phnum; ++index) {
    const auto& header = info->dlpi_phdr[index];
    if (header.p_type == PT_LOAD) {
      const AddressRange range = {
          identity->base + header.p_vaddr,
          identity->base + header.p_vaddr + header.p_memsz,
      };
      identity->end = std::max(identity->end, range.end);
      if ((header.p_flags & PF_X) != 0 &&
          identity->executable_range_count <
              identity->executable_ranges.size()) {
        identity->executable_ranges[identity->executable_range_count++] =
            range;
      }
    }
    if (header.p_type != PT_NOTE) {
      continue;
    }
    const auto* cursor = reinterpret_cast<const unsigned char*>(
        identity->base + header.p_vaddr);
    const auto* end = cursor + header.p_memsz;
    while (cursor + sizeof(ElfW(Nhdr)) <= end) {
      const auto* note = reinterpret_cast<const ElfW(Nhdr)*>(cursor);
      cursor += sizeof(ElfW(Nhdr));
      const std::size_t name_size = Align4(note->n_namesz);
      const std::size_t description_size = Align4(note->n_descsz);
      if (cursor + name_size + description_size > end) {
        break;
      }
      const auto* name = cursor;
      const auto* description = cursor + name_size;
      if (note->n_type == NT_GNU_BUILD_ID && note->n_namesz == 4 &&
          std::memcmp(name, "GNU", 4) == 0) {
        static constexpr char kHex[] = "0123456789abcdef";
        identity->build_id.reserve(note->n_descsz * 2U);
        for (std::size_t byte = 0; byte < note->n_descsz; ++byte) {
          identity->build_id.push_back(kHex[description[byte] >> 4U]);
          identity->build_id.push_back(kHex[description[byte] & 0x0fU]);
        }
      }
      cursor += name_size + description_size;
    }
  }
  return 1;
}

template <std::size_t Size>
bool HasPrefix(
    std::uintptr_t address,
    const std::array<unsigned char, Size>& expected) {
  return std::memcmp(
             reinterpret_cast<const void*>(address), expected.data(), Size) ==
         0;
}

bool IsModuleAddress(std::uintptr_t address) {
  return address >= g_module_base && address < g_module_end;
}

bool IsTextAddress(std::uintptr_t address) {
  for (std::size_t index = 0; index < g_executable_range_count; ++index) {
    if (address >= g_executable_ranges[index].begin &&
        address < g_executable_ranges[index].end) {
      return true;
    }
  }
  return false;
}

void Log(const char* format, ...) {
  if (g_log_fd < 0) {
    return;
  }
  std::array<char, 4096> line{};
  va_list arguments;
  va_start(arguments, format);
  const int length = std::vsnprintf(
      line.data(), line.size() - 2U, format, arguments);
  va_end(arguments);
  if (length < 0) {
    return;
  }
  std::size_t used = std::min(
      static_cast<std::size_t>(length), line.size() - 2U);
  line[used++] = '\n';
  [[maybe_unused]] const ssize_t written =
      ::write(g_log_fd, line.data(), used);
}

void WriteAbsoluteJump(unsigned char* destination, const void* target) {
  destination[0] = 0xff;
  destination[1] = 0x25;
  std::memset(destination + 2, 0, 4);
  const auto value = reinterpret_cast<std::uintptr_t>(target);
  std::memcpy(destination + 6, &value, sizeof(value));
}

bool SetTargetProtection(const HookPatch& patch, int protection) {
  const long page_size = ::sysconf(_SC_PAGESIZE);
  if (page_size <= 0) {
    return false;
  }
  const auto mask = static_cast<std::uintptr_t>(page_size - 1);
  const auto first = reinterpret_cast<std::uintptr_t>(patch.target) & ~mask;
  const auto last =
      (reinterpret_cast<std::uintptr_t>(patch.target) + patch.length - 1U) &
      ~mask;
  const auto size = static_cast<std::size_t>(last - first) +
                    static_cast<std::size_t>(page_size);
  return ::mprotect(reinterpret_cast<void*>(first), size, protection) == 0;
}

bool PrepareHook(
    HookPatch* patch,
    std::uintptr_t address,
    std::size_t length) {
  if (length < kRelativeJumpLength || length > patch->original.size()) {
    return false;
  }
  patch->target = reinterpret_cast<unsigned char*>(address);
  patch->length = length;
  std::memcpy(patch->original.data(), patch->target, length);
  const auto trampoline_size = length + kAbsoluteJumpLength;
  patch->trampoline = ::mmap(
      nullptr,
      trampoline_size,
      PROT_READ | PROT_WRITE | PROT_EXEC,
      MAP_PRIVATE | MAP_ANONYMOUS,
      -1,
      0);
  if (patch->trampoline == MAP_FAILED) {
    patch->trampoline = nullptr;
    return false;
  }
  std::memcpy(patch->trampoline, patch->target, length);
  WriteAbsoluteJump(
      static_cast<unsigned char*>(patch->trampoline) + length,
      patch->target + length);
  __builtin___clear_cache(
      static_cast<char*>(patch->trampoline),
      static_cast<char*>(patch->trampoline) + trampoline_size);
  return ::mprotect(
             patch->trampoline,
             trampoline_size,
             PROT_READ | PROT_EXEC) == 0;
}

bool ApplyHook(HookPatch* patch, void* hook) {
  patch->relay = ::mmap(
      nullptr,
      kAbsoluteJumpLength,
      PROT_READ | PROT_WRITE | PROT_EXEC,
      MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT,
      -1,
      0);
  if (patch->relay == MAP_FAILED) {
    patch->relay = nullptr;
    return false;
  }
  const auto distance =
      reinterpret_cast<std::intptr_t>(patch->relay) -
      (reinterpret_cast<std::intptr_t>(patch->target) +
       static_cast<std::intptr_t>(kRelativeJumpLength));
  if (distance < std::numeric_limits<std::int32_t>::min() ||
      distance > std::numeric_limits<std::int32_t>::max()) {
    return false;
  }
  WriteAbsoluteJump(static_cast<unsigned char*>(patch->relay), hook);
  __builtin___clear_cache(
      static_cast<char*>(patch->relay),
      static_cast<char*>(patch->relay) + kAbsoluteJumpLength);
  if (::mprotect(
          patch->relay,
          kAbsoluteJumpLength,
          PROT_READ | PROT_EXEC) != 0 ||
      !SetTargetProtection(*patch, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    return false;
  }
  std::array<unsigned char, 32> replacement{};
  replacement.fill(0x90);
  replacement[0] = 0xe9;
  const auto encoded = static_cast<std::int32_t>(distance);
  std::memcpy(replacement.data() + 1, &encoded, sizeof(encoded));
  std::memcpy(patch->target, replacement.data(), patch->length);
  __builtin___clear_cache(
      reinterpret_cast<char*>(patch->target),
      reinterpret_cast<char*>(patch->target) + patch->length);
  patch->applied = true;
  return SetTargetProtection(*patch, PROT_READ | PROT_EXEC);
}

template <typename Value>
Value ReadField(const void* object, std::size_t offset) {
  Value value{};
  std::memcpy(
      &value,
      static_cast<const std::byte*>(object) + offset,
      sizeof(value));
  return value;
}

template <typename Value>
void WriteField(void* object, std::size_t offset, Value value) {
  std::memcpy(
      static_cast<std::byte*>(object) + offset,
      &value,
      sizeof(value));
}

bool NativeCommandHasSafeDeleter(void* command) {
  if (command == nullptr) {
    return false;
  }
  const auto vtable = ReadField<std::uintptr_t>(command, 0);
  if (!IsModuleAddress(vtable)) {
    return false;
  }
  const auto deleter = ReadField<std::uintptr_t>(
      reinterpret_cast<const void*>(vtable), sizeof(void*));
  return IsTextAddress(deleter);
}

bool DestroyNativeCommand(void* command) {
  if (!NativeCommandHasSafeDeleter(command)) {
    return false;
  }
  const auto vtable = ReadField<std::uintptr_t>(command, 0);
  const auto deleter = ReadField<std::uintptr_t>(
      reinterpret_cast<const void*>(vtable), sizeof(void*));
  reinterpret_cast<DeletingDestructorFn>(deleter)(command);
  return true;
}

void CaptureObservedBatch(
    void* country_ai,
    std::uint32_t country_id,
    int pending_count) {
  if (pending_count <= 0 || pending_count > kMaximumPendingCommands) {
    return;
  }
  void* const* storage = ReadField<void* const*>(
      country_ai, kPendingStorageOffset);
  if (storage == nullptr) {
    return;
  }
  std::array<std::uintptr_t, kObservedVtableCapacity> vtables{};
  const auto copied = std::min(
      static_cast<std::size_t>(pending_count), vtables.size());
  for (std::size_t index = 0; index < copied; ++index) {
    if (storage[index] != nullptr) {
      const auto vtable = ReadField<std::uintptr_t>(storage[index], 0);
      vtables[index] = IsModuleAddress(vtable) ? vtable - g_module_base : 0;
    }
  }
  g_observed_batches.fetch_add(1, std::memory_order_relaxed);
  Log(
      "{\"schema\":\"iag.stellaris.observer_takeover.v1\","
      "\"kind\":\"observed_batch\",\"country_id\":%u,"
      "\"pending_count\":%d,\"vtable_offsets\":["
      "\"0x%lx\",\"0x%lx\",\"0x%lx\",\"0x%lx\","
      "\"0x%lx\",\"0x%lx\",\"0x%lx\",\"0x%lx\"]}",
      country_id,
      pending_count,
      static_cast<unsigned long>(vtables[0]),
      static_cast<unsigned long>(vtables[1]),
      static_cast<unsigned long>(vtables[2]),
      static_cast<unsigned long>(vtables[3]),
      static_cast<unsigned long>(vtables[4]),
      static_cast<unsigned long>(vtables[5]),
      static_cast<unsigned long>(vtables[6]),
      static_cast<unsigned long>(vtables[7]));
}

void SetFailure(std::uint64_t generation, std::string detail) {
  {
    std::lock_guard lock(g_control_mutex);
    if (g_control.generation != generation) {
      return;
    }
    g_control.mode = Mode::kFailed;
    g_control.invocation.reset();
    g_control.detail = std::move(detail);
  }
  Log(
      "{\"schema\":\"iag.stellaris.observer_takeover.v1\","
      "\"kind\":\"experiment_failed\",\"generation\":%lu}",
      static_cast<unsigned long>(generation));
}

void TryVerifyPending() {
  std::string detail;
  std::uint64_t generation = 0;
  bool completed = false;
  bool timed_out = false;
  {
    std::lock_guard lock(g_control_mutex);
    if (g_control.mode != Mode::kAwaitingVerification ||
        g_control.invocation == nullptr) {
      return;
    }
    generation = g_control.generation;
    const auto result = g_control.invocation->Verify(
        *g_game, g_control.country_id, nullptr);
    if (result.has_value()) {
      g_control.mode = Mode::kCompleted;
      g_control.detail = result->outcome + ":" + result->detail;
      g_control.invocation.reset();
      detail = g_control.detail;
      completed = true;
    } else if (std::chrono::steady_clock::now() >= g_control.deadline) {
      g_control.mode = Mode::kFailed;
      g_control.detail = std::string(
          g_control.invocation->TimeoutDetail());
      g_control.invocation.reset();
      detail = g_control.detail;
      timed_out = true;
    }
  }
  if (completed || timed_out) {
    Log(
        "{\"schema\":\"iag.stellaris.observer_takeover.v1\","
        "\"kind\":\"%s\",\"generation\":%lu,"
        "\"detail\":\"%s\"}",
        completed ? "replacement_confirmed" : "verification_timeout",
        static_cast<unsigned long>(generation),
        detail.c_str());
  }
}

std::optional<ArmedRequest> ClaimArmedRequest(
    std::uint32_t country_id,
    int pending_count) {
  if (pending_count <= 0) {
    return std::nullopt;
  }
  std::lock_guard lock(g_control_mutex);
  if (g_control.mode != Mode::kArmed ||
      g_control.country_id != country_id) {
    return std::nullopt;
  }
  g_control.mode = Mode::kExecuting;
  g_control.detail = "building_validated_replacement";
  return ArmedRequest{
      g_control.generation,
      g_control.country_id,
      g_control.fleet_id,
      g_control.destination_system_id,
  };
}

bool ReplacePendingBatch(
    void* country_ai,
    int pending_count,
    const ArmedRequest& request) {
  const auto* descriptor = FindActionDescriptor("move_fleet");
  if (descriptor == nullptr || !descriptor->supported(g_game->profile())) {
    SetFailure(request.generation, "move_fleet_descriptor_unavailable");
    return false;
  }
  std::string parse_error;
  const std::vector<std::string> fields = {
      std::to_string(request.fleet_id),
      std::to_string(request.destination_system_id),
  };
  auto invocation = descriptor->parse(
      std::span<const std::string>(fields), parse_error);
  if (invocation == nullptr) {
    SetFailure(
        request.generation,
        parse_error.empty() ? "move_fleet_parse_failed" : parse_error);
    return false;
  }
  PrepareResult prepared = invocation->Prepare(
      *g_game, request.country_id, nullptr);
  if (prepared.status != PreparationStatus::kReady ||
      prepared.command == nullptr) {
    const std::string detail = prepared.detail.empty()
                                   ? "move_fleet_prepare_failed"
                                   : prepared.detail;
    SetFailure(request.generation, detail);
    return false;
  }

  const auto abandon_prepared = [&prepared]() {
    if (prepared.command != nullptr) {
      if (!DestroyNativeCommand(prepared.command)) {
        Log(
            "{\"schema\":\"iag.stellaris.observer_takeover.v1\","
            "\"kind\":\"prepared_command_leaked_fail_closed\"}");
      }
      prepared.command = nullptr;
    }
  };

  if (pending_count <= 0 || pending_count > kMaximumPendingCommands) {
    abandon_prepared();
    SetFailure(request.generation, "invalid_native_pending_count");
    return false;
  }
  void* const* storage = ReadField<void* const*>(
      country_ai, kPendingStorageOffset);
  if (storage == nullptr) {
    abandon_prepared();
    SetFailure(request.generation, "native_pending_storage_missing");
    return false;
  }
  std::vector<void*> original_commands(
      storage, storage + static_cast<std::size_t>(pending_count));
  for (void* command : original_commands) {
    if (!NativeCommandHasSafeDeleter(command)) {
      abandon_prepared();
      SetFailure(request.generation, "unclassified_native_command_deleter");
      return false;
    }
  }
  auto sorted = original_commands;
  std::sort(sorted.begin(), sorted.end());
  if (std::adjacent_find(sorted.begin(), sorted.end()) != sorted.end()) {
    abandon_prepared();
    SetFailure(request.generation, "duplicate_native_command_pointer");
    return false;
  }

  WriteField(country_ai, kPendingCountOffset, 0);
  for (void* command : original_commands) {
    if (!DestroyNativeCommand(command)) {
      SetFailure(request.generation, "native_command_destroy_failed");
      abandon_prepared();
      return false;
    }
  }

  void* replacement = prepared.command;
  prepared.command = nullptr;
  g_add_ai_command(country_ai, replacement);
  const int replacement_count = ReadField<int>(
      country_ai, kPendingCountOffset);
  void* const* replacement_storage = ReadField<void* const*>(
      country_ai, kPendingStorageOffset);
  if (replacement_count != 1 || replacement_storage == nullptr ||
      replacement_storage[0] != replacement) {
    SetFailure(request.generation, "native_ai_append_rejected_replacement");
    return false;
  }

  {
    std::lock_guard lock(g_control_mutex);
    if (g_control.generation != request.generation ||
        g_control.mode != Mode::kExecuting) {
      // The socket refuses cancellation while executing, so this is an
      // internal state violation. The native queue already owns replacement.
      g_control.mode = Mode::kFailed;
      g_control.detail = "control_generation_changed_during_execution";
      return false;
    }
    g_control.invocation = std::move(invocation);
    g_control.deadline = std::chrono::steady_clock::now() + 15s;
    g_control.mode = Mode::kAwaitingVerification;
    g_control.detail = "replacement_posted_waiting_for_native_postcondition";
  }
  g_replacement_batches.fetch_add(1, std::memory_order_relaxed);
  g_suppressed_commands.fetch_add(
      static_cast<std::uint64_t>(pending_count),
      std::memory_order_relaxed);
  Log(
      "{\"schema\":\"iag.stellaris.observer_takeover.v1\","
      "\"kind\":\"batch_replaced\",\"generation\":%lu,"
      "\"country_id\":%u,\"suppressed_count\":%d,"
      "\"action_type\":\"move_fleet\",\"fleet_id\":%u,"
      "\"destination_system_id\":%u}",
      static_cast<unsigned long>(request.generation),
      request.country_id,
      pending_count,
      request.fleet_id,
      request.destination_system_id);
  return true;
}

void PostAiCommandsHook(void* country_ai) {
  if (g_inside_post_ai_hook || country_ai == nullptr) {
    g_original_post_ai_commands(country_ai);
    return;
  }
  g_inside_post_ai_hook = true;
  TryVerifyPending();
  const auto country_id = ReadField<std::uint32_t>(
      country_ai, kCountryRefOffset);
  const int pending_count = ReadField<int>(
      country_ai, kPendingCountOffset);
  CaptureObservedBatch(country_ai, country_id, pending_count);
  const auto request = ClaimArmedRequest(country_id, pending_count);
  if (request.has_value()) {
    (void)ReplacePendingBatch(country_ai, pending_count, *request);
  }
  g_original_post_ai_commands(country_ai);
  TryVerifyPending();
  g_inside_post_ai_hook = false;
}

std::vector<std::string> SplitTabs(std::string_view line) {
  std::vector<std::string> fields;
  while (true) {
    const auto separator = line.find('\t');
    fields.emplace_back(line.substr(0, separator));
    if (separator == std::string_view::npos) {
      return fields;
    }
    line.remove_prefix(separator + 1U);
  }
}

std::string Sanitize(std::string value) {
  for (char& character : value) {
    if (character == '\t' || character == '\r' || character == '\n') {
      character = ' ';
    }
  }
  return value;
}

std::string StatusResponse(std::string_view outcome) {
  std::lock_guard lock(g_control_mutex);
  return std::string(kProtocol) + '\t' + std::string(outcome) + '\t' +
         ModeName(g_control.mode) + '\t' +
         std::to_string(g_control.generation) + '\t' +
         std::to_string(g_control.country_id) + '\t' +
         std::to_string(g_control.fleet_id) + '\t' +
         std::to_string(g_control.destination_system_id) + '\t' +
         std::to_string(g_observed_batches.load(std::memory_order_relaxed)) +
         '\t' +
         std::to_string(g_replacement_batches.load(std::memory_order_relaxed)) +
         '\t' +
         std::to_string(g_suppressed_commands.load(std::memory_order_relaxed)) +
         '\t' + Sanitize(g_control.detail) + '\n';
}

std::string HandleControlRequest(std::string_view line) {
  while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) {
    line.remove_suffix(1U);
  }
  const auto fields = SplitTabs(line);
  if (fields.size() < 2U || fields[0] != kProtocol) {
    return StatusResponse("invalid_protocol");
  }
  if (fields[1] == "status") {
    return StatusResponse("ok");
  }
  if (fields[1] == "observe" || fields[1] == "disarm") {
    bool busy = false;
    {
      std::lock_guard lock(g_control_mutex);
      if (g_control.mode == Mode::kExecuting ||
          g_control.mode == Mode::kAwaitingVerification) {
        busy = true;
      } else {
        ++g_control.generation;
        g_control.mode = fields[1] == "observe" ? Mode::kObserving
                                                 : Mode::kDisarmed;
        g_control.invocation.reset();
        g_control.detail = fields[1] == "observe" ? "read_only_observation"
                                                   : "request_disarmed";
      }
    }
    return StatusResponse(busy ? "busy" : "ok");
  }
  if (fields[1] != "arm_move" || fields.size() != 5U) {
    return StatusResponse("invalid_request");
  }
  const auto country_id = ParseUint32(fields[2]);
  const auto fleet_id = ParseUint32(fields[3]);
  const auto destination_system_id = ParseUint32(fields[4]);
  if (!country_id.has_value() || !fleet_id.has_value() ||
      !destination_system_id.has_value()) {
    return StatusResponse("invalid_target");
  }
  bool busy = false;
  {
    std::lock_guard lock(g_control_mutex);
    if (g_control.mode == Mode::kArmed ||
        g_control.mode == Mode::kExecuting ||
        g_control.mode == Mode::kAwaitingVerification) {
      busy = true;
    } else {
      ++g_control.generation;
      g_control.country_id = *country_id;
      g_control.fleet_id = *fleet_id;
      g_control.destination_system_id = *destination_system_id;
      g_control.invocation.reset();
      g_control.mode = Mode::kArmed;
      g_control.detail = "waiting_for_nonempty_target_country_ai_batch";
    }
  }
  if (busy) {
    return StatusResponse("busy");
  }
  Log(
      "{\"schema\":\"iag.stellaris.observer_takeover.v1\","
      "\"kind\":\"armed_move\",\"country_id\":%u,"
      "\"fleet_id\":%u,\"destination_system_id\":%u}",
      *country_id,
      *fleet_id,
      *destination_system_id);
  return StatusResponse("ok");
}

void WriteAll(int descriptor, std::string_view value) {
  while (!value.empty()) {
    const ssize_t written = ::write(descriptor, value.data(), value.size());
    if (written <= 0) {
      return;
    }
    value.remove_prefix(static_cast<std::size_t>(written));
  }
}

void* ControlServer(void*) {
  const char* configured = std::getenv("IAG_TAKEOVER_SOCKET");
  const std::string path =
      configured != nullptr && configured[0] != '\0' ? configured
                                                       : kDefaultSocketPath;
  if (path.size() >= sizeof(sockaddr_un::sun_path)) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "socket_path_too_long");
    return nullptr;
  }
  const int server = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (server < 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "socket_failed");
    return nullptr;
  }
  (void)::unlink(path.c_str());
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  std::memcpy(address.sun_path, path.c_str(), path.size() + 1U);
  if (::bind(
          server,
          reinterpret_cast<const sockaddr*>(&address),
          sizeof(address)) != 0 ||
      ::chmod(path.c_str(), 0600) != 0 || ::listen(server, 4) != 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "socket_bind_failed");
    ::close(server);
    return nullptr;
  }
  while (true) {
    const int client = ::accept4(server, nullptr, nullptr, SOCK_CLOEXEC);
    if (client < 0) {
      continue;
    }
    std::array<char, 512> request{};
    const ssize_t received = ::read(client, request.data(), request.size());
    if (received > 0) {
      const std::string response = HandleControlRequest(std::string_view(
          request.data(), static_cast<std::size_t>(received)));
      WriteAll(client, response);
    }
    ::close(client);
  }
}

bool VerifyExecutable() {
  MainModuleIdentity identity;
  ::dl_iterate_phdr(ReadMainModuleIdentity, &identity);
  if (identity.build_id != kExpectedBuildId) {
    std::snprintf(
        g_probe_status,
        sizeof(g_probe_status),
        "build_id_mismatch:%s",
        identity.build_id.c_str());
    return false;
  }
  g_module_base = identity.base;
  g_module_end = identity.end;
  g_executable_ranges = identity.executable_ranges;
  g_executable_range_count = identity.executable_range_count;
  if (!HasPrefix(g_module_base + kPostAiCommandsOffset, kPostAiPrefix) ||
      !HasPrefix(g_module_base + kAddAiCommandOffset, kAddAiPrefix)) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "ai_function_signature_mismatch");
    return false;
  }
  g_game = std::make_unique<GameApi>(
      g_module_base, Stellaris446Profile());
  if (!g_game->VerifyRequiredAnchors()) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "action_anchor_mismatch");
    return false;
  }
  const auto* move = FindActionDescriptor("move_fleet");
  if (move == nullptr || !move->supported(g_game->profile())) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "move_fleet_not_registered");
    return false;
  }
  return true;
}

bool InstallHook() {
  if (!PrepareHook(
          &g_post_ai_patch,
          g_module_base + kPostAiCommandsOffset,
          kPostAiHookLength)) {
    return false;
  }
  g_original_post_ai_commands = reinterpret_cast<PostAiCommandsFn>(
      g_post_ai_patch.trampoline);
  g_add_ai_command = reinterpret_cast<AddAiCommandFn>(
      g_module_base + kAddAiCommandOffset);
  return ApplyHook(
      &g_post_ai_patch,
      reinterpret_cast<void*>(&PostAiCommandsHook));
}

__attribute__((constructor)) void InitializeProbe() {
  if (!VerifyExecutable()) {
    return;
  }
  const char* configured_log = std::getenv("IAG_TAKEOVER_LOG");
  const char* log_path =
      configured_log != nullptr && configured_log[0] != '\0'
          ? configured_log
          : kDefaultLogPath;
  g_log_fd = ::open(log_path, O_CREAT | O_WRONLY | O_APPEND | O_CLOEXEC, 0600);
  if (g_log_fd < 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "log_open_failed");
    return;
  }
  if (!InstallHook()) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "hook_install_failed");
    return;
  }
  pthread_t server_thread{};
  if (::pthread_create(&server_thread, nullptr, &ControlServer, nullptr) != 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "server_thread_failed");
    return;
  }
  (void)::pthread_detach(server_thread);
  std::snprintf(
      g_probe_status,
      sizeof(g_probe_status),
      "ready:observe_only:pid=%lu:post_ai=0x%lx",
      static_cast<unsigned long>(::getpid()),
      static_cast<unsigned long>(kPostAiCommandsOffset));
  Log(
      "{\"schema\":\"iag.stellaris.observer_takeover.v1\","
      "\"kind\":\"probe_started\",\"pid\":%lu,"
      "\"build_id\":\"%s\",\"mode\":\"observing\"}",
      static_cast<unsigned long>(::getpid()),
      kExpectedBuildId);
}

}  // namespace
}  // namespace iag::native_runtime

extern "C" const char* iag_stellaris_observer_takeover_probe_status() {
  return iag::native_runtime::g_probe_status;
}
