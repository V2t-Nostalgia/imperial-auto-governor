#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cxxabi.h>
#include <dlfcn.h>
#include <elf.h>
#include <errno.h>
#include <execinfo.h>
#include <fcntl.h>
#include <link.h>
#include <limits>
#include <initializer_list>
#include <pthread.h>
#include <string>
#include <string_view>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <typeinfo>
#include <unistd.h>

namespace {

constexpr char kExpectedBuildId[] =
    "c6969e60fd81d738948222a94c0b5a0841abbffc";
constexpr char kDefaultLogPath[] = "/tmp/iag-stellaris-command-probe.jsonl";
constexpr char kSchema[] = "iag.stellaris.command_probe.v2";

// Stellaris 4.4.6 (fdde), ELF build-id c6969e60... . Offsets are relative
// to the main executable module base and are guarded by instruction prefixes.
constexpr std::uintptr_t kSessionPostOffset = 0x38250d0;
constexpr std::uintptr_t kDispatchOrderOffset = 0x38c8c80;
constexpr std::uintptr_t kExecuteSyncOffset = 0x3826178;
constexpr std::uintptr_t kTurnTickCommandVtableOffset = 0x42c7228;
constexpr std::size_t kSessionPostHookLength = 6;
constexpr std::size_t kDispatchOrderHookLength = 5;
constexpr std::size_t kExecuteSyncHookLength = 7;
constexpr std::size_t kAbsoluteJumpLength = 14;
constexpr std::size_t kRelativeJumpLength = 5;
constexpr std::size_t kCommonHeaderSize = 0x1c;
constexpr std::size_t kKnownPayloadCapacity = 0x60;
constexpr std::size_t kRelatedIdCapacity = 16;
constexpr std::size_t kStackCapacity = 14;
constexpr std::size_t kSeenCapacity = 4096;

// Object vptr offsets (the exported vtable symbol plus the Itanium ABI's two
// header entries) for the exact build guarded below. Only layouts whose full
// copied size has been recovered statically are listed here. Unknown commands
// continue to expose only the common CCommand header.
constexpr std::uintptr_t kFleetFlyCommandVptrOffset = 0x42f63a8;
constexpr std::uintptr_t kFleetCancelOrdersCommandVptrOffset = 0x42f6480;
constexpr std::uintptr_t kFleetCancelOrderCommandVptrOffset = 0x42f6558;
constexpr std::uintptr_t kFleetOrbitCommandVptrOffset = 0x42f6630;
constexpr std::uintptr_t kFleetUpgradeCommandVptrOffset = 0x42f67e0;
constexpr std::uintptr_t kSendFleetCommandVptrOffset = 0x42f6990;
constexpr std::uintptr_t kFleetEmergencyFtlCommandVptrOffset = 0x42f7128;
constexpr std::uintptr_t kMergeFleetsCommandVptrOffset = 0x42f73b0;
constexpr std::uintptr_t kQueueFleetCommandVptrOffset = 0x42f77e8;
constexpr std::uintptr_t kQueueFleetsCommandVptrOffset = 0x42f78c0;
constexpr std::uintptr_t kFollowFleetCommandVptrOffset = 0x42f7dd0;

constexpr std::uintptr_t kReturnOrderVptrOffset = 0x43b2510;
constexpr std::uintptr_t kMoveOrderVptrOffset = 0x43cec30;
constexpr std::uintptr_t kOrbitOrderVptrOffset = 0x43ceda0;
constexpr std::uintptr_t kFollowOrderVptrOffset = 0x43cfc00;
constexpr std::uintptr_t kMergeOrderVptrOffset = 0x43cfee0;
constexpr std::uintptr_t kRepairOrderVptrOffset = 0x43cf7b0;
constexpr std::uintptr_t kJumpDriveOrderVptrOffset = 0x43d1b38;

constexpr std::array<unsigned char, kSessionPostHookLength>
    kSessionPostPrefix = {
        0x41, 0x56, 0x53, 0x50, 0x31, 0xc0,
};
constexpr std::array<unsigned char, kDispatchOrderHookLength>
    kDispatchOrderPrefix = {
        0x41, 0x57, 0x41, 0x56, 0x53,
};
constexpr std::array<unsigned char, kExecuteSyncHookLength>
    kExecuteSyncPrefix = {
        0x49, 0x8b, 0x04, 0x24, 0x4c, 0x89, 0xe7,
};

using SessionPostFn = bool (*)(void*, void*);
using DispatchOrderFn = void (*)(void*, void*);

enum class Phase : std::uint8_t {
  kSessionPost = 1,
  kDispatchOrder = 2,
  kExecuteSync = 3,
};

struct ProbeEvent {
  std::uint64_t sequence = 0;
  std::uint64_t monotonic_ns = 0;
  std::uint64_t realtime_ns = 0;
  std::uint64_t thread_id = 0;
  std::uint64_t dropped_before = 0;
  std::uintptr_t command = 0;
  std::uintptr_t vtable = 0;
  std::uintptr_t type_info = 0;
  std::uintptr_t caller = 0;
  std::uint32_t serial = 0;
  std::uint32_t recipient_address = 0;
  std::uint16_t recipient_port = 0;
  std::uint16_t field_12_u16 = 0;
  std::uint32_t field_18_u32 = 0;
  std::uint8_t field_14_u8 = 0;
  std::uint8_t field_15_u8 = 0;
  std::uint8_t field_16_u8 = 0;
  std::uint8_t field_17_u8 = 0;
  Phase phase = Phase::kSessionPost;
  std::uint8_t stack_depth = 0;
  std::uint16_t known_command_size = 0;
  std::uint16_t known_nested_size = 0;
  std::uint8_t related_id_count = 0;
  std::uintptr_t nested_object = 0;
  std::uintptr_t nested_vtable = 0;
  std::uintptr_t nested_type_info = 0;
  std::array<unsigned char, kCommonHeaderSize> common_header{};
  std::array<unsigned char, kKnownPayloadCapacity> known_command_payload{};
  std::array<unsigned char, kKnownPayloadCapacity> known_nested_payload{};
  std::array<std::uint32_t, kRelatedIdCapacity> related_ids{};
  std::array<std::uintptr_t, kStackCapacity> stack{};
};

static_assert(sizeof(ProbeEvent) <= 1024);

struct KnownLayout {
  std::size_t size = 0;
  std::size_t nested_pointer_offset = 0;
  std::size_t fleet_array_offset = 0;
};

struct MainModuleIdentity {
  std::uintptr_t base = 0;
  std::uintptr_t end = 0;
  std::string build_id;
};

struct HookPatch {
  unsigned char* target = nullptr;
  void* trampoline = nullptr;
  void* relay = nullptr;
  std::size_t length = 0;
  std::array<unsigned char, 32> original{};
  bool applied = false;
};

std::uintptr_t g_module_base = 0;
std::uintptr_t g_module_end = 0;
SessionPostFn g_original_session_post = nullptr;
DispatchOrderFn g_original_dispatch_order = nullptr;
HookPatch g_session_post_patch;
HookPatch g_dispatch_order_patch;
HookPatch g_execute_sync_patch;
std::array<std::atomic<std::uintptr_t>, kSeenCapacity> g_seen_vtables{};
std::atomic<std::uint64_t> g_sequence{0};
std::atomic<std::uint64_t> g_dropped{0};
int g_event_read_fd = -1;
int g_event_write_fd = -1;
int g_log_fd = -1;
char g_probe_status[256] = "not_initialized";
thread_local bool g_inside_session_post = false;
thread_local bool g_inside_dispatch_order = false;

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
      const auto end = identity->base + header.p_vaddr + header.p_memsz;
      if (end > identity->end) {
        identity->end = end;
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
bool HasPrefix(std::uintptr_t address,
               const std::array<unsigned char, Size>& expected) {
  return std::memcmp(
             reinterpret_cast<const void*>(address), expected.data(), Size) == 0;
}

void WriteAbsoluteJump(unsigned char* destination, const void* target) {
  destination[0] = 0xff;
  destination[1] = 0x25;
  std::memset(destination + 2, 0, 4);
  const auto value = reinterpret_cast<std::uintptr_t>(target);
  std::memcpy(destination + 6, &value, sizeof(value));
}

bool PrepareHook(HookPatch* patch, std::uintptr_t address, std::size_t length) {
  if (length < kRelativeJumpLength || length > patch->original.size()) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "invalid_hook_length");
    return false;
  }
  patch->target = reinterpret_cast<unsigned char*>(address);
  patch->length = length;
  std::memcpy(patch->original.data(), patch->target, length);
  const std::size_t trampoline_size = length + kAbsoluteJumpLength;
  patch->trampoline = ::mmap(
      nullptr,
      trampoline_size,
      PROT_READ | PROT_WRITE | PROT_EXEC,
      MAP_PRIVATE | MAP_ANONYMOUS,
      -1,
      0);
  if (patch->trampoline == MAP_FAILED) {
    patch->trampoline = nullptr;
    std::snprintf(g_probe_status, sizeof(g_probe_status), "trampoline_mmap_failed");
    return false;
  }
  std::memcpy(patch->trampoline, patch->target, length);
  WriteAbsoluteJump(
      static_cast<unsigned char*>(patch->trampoline) + length,
      patch->target + length);
  __builtin___clear_cache(
      static_cast<char*>(patch->trampoline),
      static_cast<char*>(patch->trampoline) + trampoline_size);
  if (::mprotect(patch->trampoline, trampoline_size, PROT_READ | PROT_EXEC) != 0) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "trampoline_mprotect_failed");
    return false;
  }
  return true;
}

bool SetTargetProtection(const HookPatch& patch, int protection) {
  const long page_size = ::sysconf(_SC_PAGESIZE);
  if (page_size <= 0) {
    return false;
  }
  const auto page_mask = static_cast<std::uintptr_t>(page_size - 1);
  const auto first_page =
      reinterpret_cast<std::uintptr_t>(patch.target) & ~page_mask;
  const auto last_byte = reinterpret_cast<std::uintptr_t>(patch.target) +
                         patch.length - 1U;
  const auto last_page = last_byte & ~page_mask;
  const auto size = static_cast<std::size_t>(last_page - first_page) +
                    static_cast<std::size_t>(page_size);
  return ::mprotect(reinterpret_cast<void*>(first_page), size, protection) == 0;
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
    std::snprintf(g_probe_status, sizeof(g_probe_status), "relay_mmap_failed");
    return false;
  }
  const auto relative_distance =
      reinterpret_cast<std::intptr_t>(patch->relay) -
      (reinterpret_cast<std::intptr_t>(patch->target) +
       static_cast<std::intptr_t>(kRelativeJumpLength));
  if (relative_distance < std::numeric_limits<std::int32_t>::min() ||
      relative_distance > std::numeric_limits<std::int32_t>::max()) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "relay_out_of_range");
    return false;
  }
  WriteAbsoluteJump(static_cast<unsigned char*>(patch->relay), hook);
  __builtin___clear_cache(
      static_cast<char*>(patch->relay),
      static_cast<char*>(patch->relay) + kAbsoluteJumpLength);
  if (::mprotect(patch->relay, kAbsoluteJumpLength, PROT_READ | PROT_EXEC) != 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "relay_mprotect_failed");
    return false;
  }
  if (!SetTargetProtection(*patch, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "hook_mprotect_failed");
    return false;
  }
  std::array<unsigned char, 32> bytes{};
  bytes.fill(0x90);
  bytes[0] = 0xe9;
  const auto encoded_distance = static_cast<std::int32_t>(relative_distance);
  std::memcpy(bytes.data() + 1, &encoded_distance, sizeof(encoded_distance));
  std::memcpy(patch->target, bytes.data(), patch->length);
  __builtin___clear_cache(
      reinterpret_cast<char*>(patch->target),
      reinterpret_cast<char*>(patch->target) + patch->length);
  patch->applied = true;
  if (!SetTargetProtection(*patch, PROT_READ | PROT_EXEC)) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "hook_restore_protection_failed");
    return false;
  }
  return true;
}

void RestoreHook(HookPatch* patch) {
  if (!patch->applied ||
      !SetTargetProtection(*patch, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    return;
  }
  std::memcpy(patch->target, patch->original.data(), patch->length);
  __builtin___clear_cache(
      reinterpret_cast<char*>(patch->target),
      reinterpret_cast<char*>(patch->target) + patch->length);
  (void)SetTargetProtection(*patch, PROT_READ | PROT_EXEC);
  patch->applied = false;
}

std::uint64_t ClockNanoseconds(clockid_t clock) {
  timespec value{};
  if (::clock_gettime(clock, &value) != 0) {
    return 0;
  }
  return static_cast<std::uint64_t>(value.tv_sec) * 1000000000ULL +
         static_cast<std::uint64_t>(value.tv_nsec);
}

bool ClaimFirstVtable(std::uintptr_t vtable) {
  std::size_t slot = (vtable >> 4U) % kSeenCapacity;
  for (std::size_t probe = 0; probe < 32; ++probe) {
    auto& entry = g_seen_vtables[(slot + probe) % kSeenCapacity];
    std::uintptr_t observed = entry.load(std::memory_order_relaxed);
    if (observed == vtable) {
      return false;
    }
    if (observed == 0 && entry.compare_exchange_strong(
                             observed, vtable, std::memory_order_relaxed)) {
      return true;
    }
  }
  return false;
}

KnownLayout CommandLayout(std::uintptr_t vptr_offset) {
  switch (vptr_offset) {
    case kFleetFlyCommandVptrOffset:
    case kSendFleetCommandVptrOffset:
      return {.size = 0x50};
    case kFleetCancelOrdersCommandVptrOffset:
      return {.size = 0x38, .fleet_array_offset = 0x20};
    case kFleetCancelOrderCommandVptrOffset:
    case kFollowFleetCommandVptrOffset:
      return {.size = 0x28};
    case kFleetEmergencyFtlCommandVptrOffset:
      return {.size = 0x20};
    case kFleetOrbitCommandVptrOffset:
      return {.size = 0x40};
    case kFleetUpgradeCommandVptrOffset:
      return {.size = 0x30};
    case kMergeFleetsCommandVptrOffset:
      return {.size = 0x40, .fleet_array_offset = 0x20};
    case kQueueFleetCommandVptrOffset:
      return {.size = 0x30, .nested_pointer_offset = 0x28};
    case kQueueFleetsCommandVptrOffset:
      return {
          .size = 0x48,
          .nested_pointer_offset = 0x40,
          .fleet_array_offset = 0x20,
      };
    default:
      return {};
  }
}

std::size_t NestedOrderSize(std::uintptr_t vptr_offset) {
  switch (vptr_offset) {
    case kReturnOrderVptrOffset:
    case kRepairOrderVptrOffset:
    case kJumpDriveOrderVptrOffset:
      return 0x28;
    case kMoveOrderVptrOffset:
      return 0x50;
    case kOrbitOrderVptrOffset:
      return 0x60;
    case kFollowOrderVptrOffset:
      return 0x58;
    case kMergeOrderVptrOffset:
      return 0x30;
    default:
      return 0;
  }
}

void CaptureKnownFields(ProbeEvent* event, const void* command) {
  const auto* bytes = static_cast<const unsigned char*>(command);
  const auto vptr_offset = event->vtable - g_module_base;
  const KnownLayout layout = CommandLayout(vptr_offset);
  if (layout.size == 0 || layout.size > event->known_command_payload.size()) {
    return;
  }
  event->known_command_size = static_cast<std::uint16_t>(layout.size);
  std::memcpy(
      event->known_command_payload.data(), command, layout.size);

  if (layout.fleet_array_offset != 0) {
    const auto* array = bytes + layout.fleet_array_offset;
    const auto* ids = *reinterpret_cast<const std::uint32_t* const*>(array + 0x08);
    const int count = *reinterpret_cast<const int*>(array + 0x14);
    if (ids != nullptr && count > 0) {
      const auto copied = static_cast<std::size_t>(count) < kRelatedIdCapacity
                              ? static_cast<std::size_t>(count)
                              : kRelatedIdCapacity;
      std::memcpy(
          event->related_ids.data(), ids, copied * sizeof(std::uint32_t));
      event->related_id_count = static_cast<std::uint8_t>(copied);
    }
  }

  if (layout.nested_pointer_offset == 0) {
    return;
  }
  const void* nested = *reinterpret_cast<void* const*>(
      bytes + layout.nested_pointer_offset);
  if (nested == nullptr) {
    return;
  }
  event->nested_object = reinterpret_cast<std::uintptr_t>(nested);
  event->nested_vtable = *reinterpret_cast<const std::uintptr_t*>(nested);
  if (event->nested_vtable == 0) {
    return;
  }
  event->nested_type_info = *(reinterpret_cast<const std::uintptr_t*>(
                                event->nested_vtable) - 1);
  const std::size_t nested_size =
      NestedOrderSize(event->nested_vtable - g_module_base);
  if (nested_size == 0 || nested_size > event->known_nested_payload.size()) {
    return;
  }
  event->known_nested_size = static_cast<std::uint16_t>(nested_size);
  std::memcpy(event->known_nested_payload.data(), nested, nested_size);
}

void CaptureEvent(Phase phase, void* command, std::uintptr_t caller) {
  if (command == nullptr || g_event_write_fd < 0) {
    return;
  }
  const auto vtable = *reinterpret_cast<const std::uintptr_t*>(command);
  if (vtable == g_module_base + kTurnTickCommandVtableOffset) {
    return;
  }
  ProbeEvent event;
  event.sequence = g_sequence.fetch_add(1, std::memory_order_relaxed) + 1U;
  event.monotonic_ns = ClockNanoseconds(CLOCK_MONOTONIC);
  event.realtime_ns = ClockNanoseconds(CLOCK_REALTIME);
  event.thread_id = static_cast<std::uint64_t>(::syscall(SYS_gettid));
  event.dropped_before = g_dropped.exchange(0, std::memory_order_relaxed);
  event.command = reinterpret_cast<std::uintptr_t>(command);
  event.vtable = vtable;
  event.type_info = event.vtable == 0
                        ? 0
                        : *(reinterpret_cast<const std::uintptr_t*>(event.vtable) - 1);
  event.caller = caller;
  event.serial = *reinterpret_cast<const std::uint32_t*>(
      static_cast<const unsigned char*>(command) + 0x08);
  event.recipient_address = *reinterpret_cast<const std::uint32_t*>(
      static_cast<const unsigned char*>(command) + 0x0c);
  event.recipient_port = *reinterpret_cast<const std::uint16_t*>(
      static_cast<const unsigned char*>(command) + 0x10);
  event.field_12_u16 = *reinterpret_cast<const std::uint16_t*>(
      static_cast<const unsigned char*>(command) + 0x12);
  event.field_14_u8 = *reinterpret_cast<const std::uint8_t*>(
      static_cast<const unsigned char*>(command) + 0x14);
  event.field_15_u8 = *reinterpret_cast<const std::uint8_t*>(
      static_cast<const unsigned char*>(command) + 0x15);
  event.field_16_u8 = *reinterpret_cast<const std::uint8_t*>(
      static_cast<const unsigned char*>(command) + 0x16);
  event.field_17_u8 = *reinterpret_cast<const std::uint8_t*>(
      static_cast<const unsigned char*>(command) + 0x17);
  event.field_18_u32 = *reinterpret_cast<const std::uint32_t*>(
      static_cast<const unsigned char*>(command) + 0x18);
  event.phase = phase;
  std::memcpy(event.common_header.data(), command, event.common_header.size());
  CaptureKnownFields(&event, command);
  if (ClaimFirstVtable(event.vtable)) {
    void* frames[kStackCapacity]{};
    const int depth = ::backtrace(frames, static_cast<int>(kStackCapacity));
    event.stack_depth = static_cast<std::uint8_t>(depth > 0 ? depth : 0);
    for (int index = 0; index < depth; ++index) {
      event.stack[static_cast<std::size_t>(index)] =
          reinterpret_cast<std::uintptr_t>(frames[index]);
    }
  }
  const ssize_t written = ::write(g_event_write_fd, &event, sizeof(event));
  if (written != static_cast<ssize_t>(sizeof(event))) {
    g_dropped.fetch_add(event.dropped_before + 1U, std::memory_order_relaxed);
  }
}

void CaptureExecuteEventBridge(void* command, std::uintptr_t caller) {
  CaptureEvent(Phase::kExecuteSync, command, caller);
}

bool PrepareExecuteHook(HookPatch* patch, std::uintptr_t address) {
  patch->target = reinterpret_cast<unsigned char*>(address);
  patch->length = kExecuteSyncHookLength;
  std::memcpy(patch->original.data(), patch->target, patch->length);
  return true;
}

bool ApplyExecuteHook(HookPatch* patch) {
  constexpr std::size_t kRelayCapacity = 192;
  patch->relay = ::mmap(
      nullptr,
      kRelayCapacity,
      PROT_READ | PROT_WRITE | PROT_EXEC,
      MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT,
      -1,
      0);
  if (patch->relay == MAP_FAILED) {
    patch->relay = nullptr;
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "execute_relay_mmap_failed");
    return false;
  }
  auto* code = static_cast<unsigned char*>(patch->relay);
  std::size_t cursor = 0;
  const auto emit_bytes = [&](std::initializer_list<unsigned char> values) {
    for (const auto value : values) {
      code[cursor++] = value;
    }
  };
  const auto emit_address = [&](std::uintptr_t value) {
    std::memcpy(code + cursor, &value, sizeof(value));
    cursor += sizeof(value);
  };

  // Preserve the full caller-saved integer state and flags. The original
  // virtual Execute call remains in the Stellaris text segment, so exception
  // unwinding and its return address are unchanged.
  emit_bytes({0x9c, 0x50, 0x51, 0x52, 0x56, 0x57});
  emit_bytes({0x41, 0x50, 0x41, 0x51, 0x41, 0x52, 0x41, 0x53});
  emit_bytes({0x4c, 0x89, 0xe7});  // mov r12, rdi
  emit_bytes({0x48, 0xbe});        // movabs caller, rsi
  emit_address(g_module_base + kExecuteSyncOffset);
  emit_bytes({0x48, 0xb8});  // movabs CaptureExecuteEventBridge, rax
  emit_address(reinterpret_cast<std::uintptr_t>(&CaptureExecuteEventBridge));
  emit_bytes({0xff, 0xd0});  // call rax
  emit_bytes({0x41, 0x5b, 0x41, 0x5a, 0x41, 0x59, 0x41, 0x58});
  emit_bytes({0x5f, 0x5e, 0x5a, 0x59, 0x58, 0x9d});
  std::memcpy(code + cursor, patch->original.data(), patch->length);
  cursor += patch->length;
  WriteAbsoluteJump(code + cursor, patch->target + patch->length);
  cursor += kAbsoluteJumpLength;
  if (cursor > kRelayCapacity) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "execute_relay_overflow");
    return false;
  }
  __builtin___clear_cache(
      reinterpret_cast<char*>(code), reinterpret_cast<char*>(code + cursor));
  if (::mprotect(patch->relay, kRelayCapacity, PROT_READ | PROT_EXEC) != 0) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "execute_relay_mprotect_failed");
    return false;
  }
  const auto relative_distance =
      reinterpret_cast<std::intptr_t>(patch->relay) -
      (reinterpret_cast<std::intptr_t>(patch->target) +
       static_cast<std::intptr_t>(kRelativeJumpLength));
  if (relative_distance < std::numeric_limits<std::int32_t>::min() ||
      relative_distance > std::numeric_limits<std::int32_t>::max()) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "execute_relay_out_of_range");
    return false;
  }
  if (!SetTargetProtection(*patch, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "execute_hook_mprotect_failed");
    return false;
  }
  std::array<unsigned char, kExecuteSyncHookLength> bytes{};
  bytes.fill(0x90);
  bytes[0] = 0xe9;
  const auto encoded_distance = static_cast<std::int32_t>(relative_distance);
  std::memcpy(bytes.data() + 1, &encoded_distance, sizeof(encoded_distance));
  std::memcpy(patch->target, bytes.data(), bytes.size());
  __builtin___clear_cache(
      reinterpret_cast<char*>(patch->target),
      reinterpret_cast<char*>(patch->target) + patch->length);
  patch->applied = true;
  if (!SetTargetProtection(*patch, PROT_READ | PROT_EXEC)) {
    std::snprintf(
        g_probe_status,
        sizeof(g_probe_status),
        "execute_hook_restore_protection_failed");
    return false;
  }
  return true;
}

bool SessionPostHook(void* session, void* command) {
  const auto caller = reinterpret_cast<std::uintptr_t>(
      __builtin_extract_return_addr(__builtin_return_address(0)));
  if (!g_inside_session_post) {
    g_inside_session_post = true;
    CaptureEvent(Phase::kSessionPost, command, caller);
    g_inside_session_post = false;
  }
  return g_original_session_post(session, command);
}

void DispatchOrderHook(void* server, void* command) {
  const auto caller = reinterpret_cast<std::uintptr_t>(
      __builtin_extract_return_addr(__builtin_return_address(0)));
  if (!g_inside_dispatch_order) {
    g_inside_dispatch_order = true;
    CaptureEvent(Phase::kDispatchOrder, command, caller);
    g_inside_dispatch_order = false;
  }
  g_original_dispatch_order(server, command);
}

std::string JsonEscape(std::string_view input) {
  std::string output;
  output.reserve(input.size() + 16U);
  for (const unsigned char character : input) {
    switch (character) {
      case '\\':
        output += "\\\\";
        break;
      case '"':
        output += "\\\"";
        break;
      case '\n':
        output += "\\n";
        break;
      case '\r':
        output += "\\r";
        break;
      case '\t':
        output += "\\t";
        break;
      default:
        if (character < 0x20) {
          char encoded[7]{};
          std::snprintf(encoded, sizeof(encoded), "\\u%04x", character);
          output += encoded;
        } else {
          output.push_back(static_cast<char>(character));
        }
    }
  }
  return output;
}

std::string Hex(std::uintptr_t value) {
  char buffer[2U + sizeof(value) * 2U + 1U]{};
  std::snprintf(buffer, sizeof(buffer), "0x%lx", static_cast<unsigned long>(value));
  return buffer;
}

template <std::size_t Size>
std::string BytesHex(
    const std::array<unsigned char, Size>& bytes,
    std::size_t count = Size) {
  static constexpr char kHexDigits[] = "0123456789abcdef";
  if (count > Size) {
    count = Size;
  }
  std::string output;
  output.reserve(count * 2U);
  for (std::size_t index = 0; index < count; ++index) {
    const auto byte = bytes[index];
    output.push_back(kHexDigits[byte >> 4U]);
    output.push_back(kHexDigits[byte & 0x0fU]);
  }
  return output;
}

std::string TypeName(std::uintptr_t type_info_address) {
  if (type_info_address == 0) {
    return "";
  }
  const auto* information =
      reinterpret_cast<const std::type_info*>(type_info_address);
  const char* mangled = information->name();
  if (mangled == nullptr) {
    return "";
  }
  int status = -1;
  char* demangled = abi::__cxa_demangle(mangled, nullptr, nullptr, &status);
  if (status == 0 && demangled != nullptr) {
    std::string output(demangled);
    std::free(demangled);
    return output;
  }
  std::free(demangled);
  return mangled;
}

std::string AddressLabel(std::uintptr_t address) {
  if (address >= g_module_base && address < g_module_end) {
    return "stellaris+" + Hex(address - g_module_base);
  }
  Dl_info information{};
  if (::dladdr(reinterpret_cast<void*>(address), &information) != 0 &&
      information.dli_fname != nullptr && information.dli_fbase != nullptr) {
    std::string module(information.dli_fname);
    const std::size_t slash = module.find_last_of('/');
    if (slash != std::string::npos) {
      module.erase(0, slash + 1U);
    }
    return module + "+" +
           Hex(address - reinterpret_cast<std::uintptr_t>(information.dli_fbase));
  }
  return Hex(address);
}

void AppendJsonString(std::string* output, std::string_view value) {
  output->push_back('"');
  *output += JsonEscape(value);
  output->push_back('"');
}

std::string SerializeEvent(const ProbeEvent& event) {
  std::string output;
  output.reserve(1536);
  output += "{\"schema\":\"";
  output += kSchema;
  output += "\",\"kind\":\"command\",\"sequence\":";
  output += std::to_string(event.sequence);
  output += ",\"phase\":\"";
  if (event.phase == Phase::kSessionPost) {
    output += "session_post";
  } else if (event.phase == Phase::kDispatchOrder) {
    output += "dispatch_order";
  } else {
    output += "execute_sync";
  }
  output += "\",\"monotonic_ns\":" + std::to_string(event.monotonic_ns);
  output += ",\"realtime_ns\":" + std::to_string(event.realtime_ns);
  output += ",\"thread_id\":" + std::to_string(event.thread_id);
  output += ",\"dropped_before\":" + std::to_string(event.dropped_before);
  output += ",\"command\":\"" + Hex(event.command) + "\"";
  output += ",\"vtable\":\"" + AddressLabel(event.vtable) + "\"";
  output += ",\"type_info\":\"" + AddressLabel(event.type_info) + "\"";
  output += ",\"command_type\":";
  AppendJsonString(&output, TypeName(event.type_info));
  output += ",\"serial\":" + std::to_string(event.serial);
  output +=
      ",\"recipient_address\":" + std::to_string(event.recipient_address);
  output += ",\"recipient_port\":" + std::to_string(event.recipient_port);
  output += ",\"field_12_u16\":" + std::to_string(event.field_12_u16);
  output += ",\"field_14_u8\":" + std::to_string(event.field_14_u8);
  output += ",\"field_15_u8\":" + std::to_string(event.field_15_u8);
  output += ",\"field_16_u8\":" + std::to_string(event.field_16_u8);
  output += ",\"field_17_u8\":" + std::to_string(event.field_17_u8);
  output += ",\"field_18_u32\":" + std::to_string(event.field_18_u32);
  output += ",\"caller\":";
  AppendJsonString(&output, AddressLabel(event.caller));
  output += ",\"common_header_hex\":\"" + BytesHex(event.common_header) + "\"";
  output += ",\"known_command_size\":" +
            std::to_string(event.known_command_size);
  output += ",\"known_command_hex\":\"" +
            BytesHex(event.known_command_payload, event.known_command_size) +
            "\"";
  output += ",\"nested_object\":\"" + Hex(event.nested_object) + "\"";
  output += ",\"nested_vtable\":\"" + AddressLabel(event.nested_vtable) +
            "\"";
  output += ",\"nested_type_info\":\"" +
            AddressLabel(event.nested_type_info) + "\"";
  output += ",\"nested_type\":";
  AppendJsonString(&output, TypeName(event.nested_type_info));
  output += ",\"known_nested_size\":" +
            std::to_string(event.known_nested_size);
  output += ",\"known_nested_hex\":\"" +
            BytesHex(event.known_nested_payload, event.known_nested_size) +
            "\"";
  output += ",\"related_ids\":[";
  for (std::size_t index = 0; index < event.related_id_count; ++index) {
    if (index != 0) {
      output.push_back(',');
    }
    output += std::to_string(event.related_ids[index]);
  }
  output += "]";
  output += ",\"first_seen_stack\":[";
  for (std::size_t index = 0; index < event.stack_depth; ++index) {
    if (index != 0) {
      output.push_back(',');
    }
    AppendJsonString(&output, AddressLabel(event.stack[index]));
  }
  output += "]}\n";
  return output;
}

void WriteAll(int fd, std::string_view value) {
  while (!value.empty()) {
    const ssize_t written = ::write(fd, value.data(), value.size());
    if (written > 0) {
      value.remove_prefix(static_cast<std::size_t>(written));
      continue;
    }
    if (written < 0 && errno == EINTR) {
      continue;
    }
    break;
  }
}

void* WriterThread(void*) {
  ProbeEvent event;
  while (true) {
    const ssize_t received = ::read(g_event_read_fd, &event, sizeof(event));
    if (received == static_cast<ssize_t>(sizeof(event))) {
      WriteAll(g_log_fd, SerializeEvent(event));
      continue;
    }
    if (received < 0 && errno == EINTR) {
      continue;
    }
    if (received == 0) {
      break;
    }
  }
  return nullptr;
}

bool OpenEventStream() {
  const char* configured_path = std::getenv("IAG_COMMAND_PROBE_LOG");
  const char* log_path = configured_path != nullptr && configured_path[0] != '\0'
                             ? configured_path
                             : kDefaultLogPath;
  g_log_fd = ::open(log_path, O_CREAT | O_WRONLY | O_APPEND | O_CLOEXEC, 0600);
  if (g_log_fd < 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "log_open_failed");
    return false;
  }
  int descriptors[2]{};
  if (::pipe2(descriptors, O_CLOEXEC) != 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "pipe_create_failed");
    return false;
  }
  g_event_read_fd = descriptors[0];
  g_event_write_fd = descriptors[1];
  const int write_flags = ::fcntl(g_event_write_fd, F_GETFL, 0);
  if (write_flags < 0 ||
      ::fcntl(g_event_write_fd, F_SETFL, write_flags | O_NONBLOCK) != 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "pipe_nonblock_failed");
    return false;
  }

  std::string header = "{\"schema\":\"";
  header += kSchema;
  header += "\",\"kind\":\"probe_started\",\"pid\":";
  header += std::to_string(static_cast<unsigned long>(::getpid()));
  header += ",\"build_id\":\"";
  header += kExpectedBuildId;
  header += "\",\"session_post_offset\":\"" + Hex(kSessionPostOffset);
  header += "\",\"dispatch_order_offset\":\"" + Hex(kDispatchOrderOffset);
  header += "\",\"execute_sync_offset\":\"" + Hex(kExecuteSyncOffset);
  header += "\"}\n";
  WriteAll(g_log_fd, header);

  pthread_t thread{};
  if (::pthread_create(&thread, nullptr, &WriterThread, nullptr) != 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "writer_thread_failed");
    return false;
  }
  (void)::pthread_detach(thread);
  return true;
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
  if (!HasPrefix(g_module_base + kSessionPostOffset, kSessionPostPrefix) ||
      !HasPrefix(g_module_base + kDispatchOrderOffset, kDispatchOrderPrefix) ||
      !HasPrefix(g_module_base + kExecuteSyncOffset, kExecuteSyncPrefix)) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "function_signature_mismatch");
    return false;
  }
  return true;
}

bool InstallHooks() {
  if (!PrepareHook(
          &g_session_post_patch,
          g_module_base + kSessionPostOffset,
          kSessionPostHookLength) ||
      !PrepareHook(
          &g_dispatch_order_patch,
          g_module_base + kDispatchOrderOffset,
          kDispatchOrderHookLength) ||
      !PrepareExecuteHook(
          &g_execute_sync_patch, g_module_base + kExecuteSyncOffset)) {
    return false;
  }
  g_original_session_post =
      reinterpret_cast<SessionPostFn>(g_session_post_patch.trampoline);
  g_original_dispatch_order =
      reinterpret_cast<DispatchOrderFn>(g_dispatch_order_patch.trampoline);
  if (!ApplyHook(
          &g_session_post_patch,
          reinterpret_cast<void*>(&SessionPostHook))) {
    return false;
  }
  if (!ApplyHook(
          &g_dispatch_order_patch,
          reinterpret_cast<void*>(&DispatchOrderHook))) {
    RestoreHook(&g_session_post_patch);
    return false;
  }
  if (!ApplyExecuteHook(&g_execute_sync_patch)) {
    RestoreHook(&g_dispatch_order_patch);
    RestoreHook(&g_session_post_patch);
    return false;
  }
  return true;
}

__attribute__((constructor)) void InitializeProbe() {
  if (!VerifyExecutable()) {
    return;
  }
  if (!OpenEventStream()) {
    return;
  }
  if (!InstallHooks()) {
    return;
  }
  std::snprintf(
      g_probe_status,
      sizeof(g_probe_status),
      "ready:pid=%lu:post=0x%lx:dispatch=0x%lx:execute=0x%lx",
      static_cast<unsigned long>(::getpid()),
      static_cast<unsigned long>(kSessionPostOffset),
      static_cast<unsigned long>(kDispatchOrderOffset),
      static_cast<unsigned long>(kExecuteSyncOffset));
}

}  // namespace

extern "C" const char* iag_stellaris_command_probe_status() {
  return g_probe_status;
}
