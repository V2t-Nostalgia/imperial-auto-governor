#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cxxabi.h>
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <link.h>
#include <limits>
#include <pthread.h>
#include <string>
#include <string_view>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <typeinfo>
#include <unistd.h>

namespace {

constexpr char kExpectedBuildId[] =
    "c6969e60fd81d738948222a94c0b5a0841abbffc";
constexpr char kDefaultLogPath[] = "/tmp/iag-stellaris-payload-probe.jsonl";
constexpr char kSchema[] = "iag.stellaris.payload_probe.v1";

constexpr std::uintptr_t kWritePersistentConstOffset = 0x3eef460;
constexpr std::uintptr_t kWritePersistentOffset = 0x3eef490;
constexpr std::uintptr_t kWriteBuildableOffset = 0x1dfff50;
constexpr std::size_t kPersistentHookLength = 7;
constexpr std::size_t kBuildableHookLength = 5;
constexpr std::size_t kAbsoluteJumpLength = 14;
constexpr std::size_t kRelativeJumpLength = 5;
constexpr std::size_t kObjectHeadSize = 32;

constexpr std::array<unsigned char, kPersistentHookLength>
    kWritePersistentConstPrefix = {
        0x41, 0x56, 0x53, 0x50, 0x49, 0x89, 0xd6,
};
constexpr std::array<unsigned char, kPersistentHookLength>
    kWritePersistentPrefix = {
        0x41, 0x56, 0x53, 0x50, 0x49, 0x89, 0xd6,
};
constexpr std::array<unsigned char, kBuildableHookLength>
    kWriteBuildablePrefix = {
        0x41, 0x57, 0x41, 0x56, 0x53,
};

using PersistentWriteFn = void (*)(void*, int, const void*);
using BuildableWriteFn = void (*)(const void*, void*);

enum class PayloadKind : std::uint8_t {
  kPersistentConst = 1,
  kPersistentMutable = 2,
  kBuildable = 3,
};

struct PayloadEvent {
  std::uint64_t sequence = 0;
  std::uint64_t monotonic_ns = 0;
  std::uint64_t realtime_ns = 0;
  std::uint64_t thread_id = 0;
  std::uint64_t dropped_before = 0;
  std::uintptr_t object = 0;
  std::uintptr_t vtable = 0;
  std::uintptr_t type_info = 0;
  std::int32_t label_or_type = 0;
  PayloadKind kind = PayloadKind::kPersistentConst;
  std::array<unsigned char, kObjectHeadSize> object_head{};
};

static_assert(sizeof(PayloadEvent) <= 256);

struct MainModuleIdentity {
  std::uintptr_t base = 0;
  std::string build_id;
};

struct HookPatch {
  unsigned char* target = nullptr;
  void* trampoline = nullptr;
  void* relay = nullptr;
  std::size_t length = 0;
  std::array<unsigned char, 16> original{};
  bool applied = false;
};

std::uintptr_t g_module_base = 0;
PersistentWriteFn g_original_write_persistent_const = nullptr;
PersistentWriteFn g_original_write_persistent = nullptr;
BuildableWriteFn g_original_write_buildable = nullptr;
HookPatch g_write_persistent_const_patch;
HookPatch g_write_persistent_patch;
HookPatch g_write_buildable_patch;
std::atomic<std::uint64_t> g_sequence{0};
std::atomic<std::uint64_t> g_dropped{0};
int g_event_read_fd = -1;
int g_event_write_fd = -1;
int g_log_fd = -1;
char g_probe_status[256] = "not_initialized";
thread_local bool g_inside_payload_hook = false;

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
  const auto distance =
      reinterpret_cast<std::intptr_t>(patch->relay) -
      (reinterpret_cast<std::intptr_t>(patch->target) +
       static_cast<std::intptr_t>(kRelativeJumpLength));
  if (distance < std::numeric_limits<std::int32_t>::min() ||
      distance > std::numeric_limits<std::int32_t>::max()) {
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
  std::array<unsigned char, 16> bytes{};
  bytes.fill(0x90);
  bytes[0] = 0xe9;
  const auto encoded = static_cast<std::int32_t>(distance);
  std::memcpy(bytes.data() + 1, &encoded, sizeof(encoded));
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

bool IsInterestingPersistent(std::uintptr_t type_info_address) {
  if (type_info_address == 0) {
    return false;
  }
  const auto* information =
      reinterpret_cast<const std::type_info*>(type_info_address);
  const char* name = information->name();
  return name != nullptr &&
         (std::strstr(name, "FleetOrder") != nullptr ||
          std::strstr(name, "FleetAction") != nullptr);
}

void CapturePayload(PayloadKind kind, std::int32_t label_or_type,
                    const void* object) {
  if (g_event_write_fd < 0 || object == nullptr) {
    return;
  }
  PayloadEvent event;
  event.sequence = g_sequence.fetch_add(1, std::memory_order_relaxed) + 1U;
  event.monotonic_ns = ClockNanoseconds(CLOCK_MONOTONIC);
  event.realtime_ns = ClockNanoseconds(CLOCK_REALTIME);
  event.thread_id = static_cast<std::uint64_t>(::syscall(SYS_gettid));
  event.object = reinterpret_cast<std::uintptr_t>(object);
  event.vtable = *reinterpret_cast<const std::uintptr_t*>(object);
  event.type_info = event.vtable == 0
                        ? 0
                        : *(reinterpret_cast<const std::uintptr_t*>(event.vtable) - 1);
  if (kind != PayloadKind::kBuildable &&
      !IsInterestingPersistent(event.type_info)) {
    return;
  }
  event.dropped_before = g_dropped.exchange(0, std::memory_order_relaxed);
  event.label_or_type = label_or_type;
  event.kind = kind;
  std::memcpy(event.object_head.data(), object, event.object_head.size());
  const ssize_t written = ::write(g_event_write_fd, &event, sizeof(event));
  if (written != static_cast<ssize_t>(sizeof(event))) {
    g_dropped.fetch_add(event.dropped_before + 1U, std::memory_order_relaxed);
  }
}

void WritePersistentConstHook(void* writer, int label_or_type,
                              const void* object) {
  if (!g_inside_payload_hook) {
    g_inside_payload_hook = true;
    CapturePayload(PayloadKind::kPersistentConst, label_or_type, object);
    g_inside_payload_hook = false;
  }
  g_original_write_persistent_const(writer, label_or_type, object);
}

void WritePersistentHook(void* writer, int label_or_type, const void* object) {
  if (!g_inside_payload_hook) {
    g_inside_payload_hook = true;
    CapturePayload(PayloadKind::kPersistentMutable, label_or_type, object);
    g_inside_payload_hook = false;
  }
  g_original_write_persistent(writer, label_or_type, object);
}

void WriteBuildableHook(const void* object, void* writer) {
  if (!g_inside_payload_hook) {
    g_inside_payload_hook = true;
    CapturePayload(PayloadKind::kBuildable, 0, object);
    g_inside_payload_hook = false;
  }
  g_original_write_buildable(object, writer);
}

std::string JsonEscape(std::string_view input) {
  std::string output;
  output.reserve(input.size() + 16U);
  for (const unsigned char character : input) {
    if (character == '\\') {
      output += "\\\\";
    } else if (character == '"') {
      output += "\\\"";
    } else if (character == '\n') {
      output += "\\n";
    } else if (character == '\r') {
      output += "\\r";
    } else if (character == '\t') {
      output += "\\t";
    } else if (character < 0x20) {
      char encoded[7]{};
      std::snprintf(encoded, sizeof(encoded), "\\u%04x", character);
      output += encoded;
    } else {
      output.push_back(static_cast<char>(character));
    }
  }
  return output;
}

std::string Hex(std::uintptr_t value) {
  char buffer[2U + sizeof(value) * 2U + 1U]{};
  std::snprintf(buffer, sizeof(buffer), "0x%lx", static_cast<unsigned long>(value));
  return buffer;
}

std::string BytesHex(const std::array<unsigned char, kObjectHeadSize>& bytes) {
  static constexpr char kHexDigits[] = "0123456789abcdef";
  std::string output;
  output.reserve(bytes.size() * 2U);
  for (const auto byte : bytes) {
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

std::string SerializeEvent(const PayloadEvent& event) {
  const char* kind = "persistent_const";
  if (event.kind == PayloadKind::kPersistentMutable) {
    kind = "persistent_mutable";
  } else if (event.kind == PayloadKind::kBuildable) {
    kind = "buildable";
  }
  std::string output;
  output.reserve(768);
  output += "{\"schema\":\"";
  output += kSchema;
  output += "\",\"kind\":\"";
  output += kind;
  output += "\",\"sequence\":" + std::to_string(event.sequence);
  output += ",\"monotonic_ns\":" + std::to_string(event.monotonic_ns);
  output += ",\"realtime_ns\":" + std::to_string(event.realtime_ns);
  output += ",\"thread_id\":" + std::to_string(event.thread_id);
  output += ",\"dropped_before\":" + std::to_string(event.dropped_before);
  output += ",\"label_or_type\":" + std::to_string(event.label_or_type);
  output += ",\"object\":\"" + Hex(event.object) + "\"";
  output += ",\"vtable_offset\":\"" + Hex(event.vtable - g_module_base) + "\"";
  output += ",\"type_info_offset\":\"" + Hex(event.type_info - g_module_base) + "\"";
  output += ",\"object_type\":\"" + JsonEscape(TypeName(event.type_info)) + "\"";
  output += ",\"object_head_hex\":\"" + BytesHex(event.object_head) + "\"}\n";
  return output;
}

void WriteAll(int fd, std::string_view value) {
  while (!value.empty()) {
    const ssize_t written = ::write(fd, value.data(), value.size());
    if (written > 0) {
      value.remove_prefix(static_cast<std::size_t>(written));
    } else if (written < 0 && errno == EINTR) {
      continue;
    } else {
      break;
    }
  }
}

void* WriterThread(void*) {
  PayloadEvent event;
  while (true) {
    const ssize_t received = ::read(g_event_read_fd, &event, sizeof(event));
    if (received == static_cast<ssize_t>(sizeof(event))) {
      WriteAll(g_log_fd, SerializeEvent(event));
    } else if (received < 0 && errno == EINTR) {
      continue;
    } else if (received == 0) {
      break;
    }
  }
  return nullptr;
}

bool OpenEventStream() {
  const char* configured = std::getenv("IAG_PAYLOAD_PROBE_LOG");
  const char* path = configured != nullptr && configured[0] != '\0'
                         ? configured
                         : kDefaultLogPath;
  g_log_fd = ::open(path, O_CREAT | O_WRONLY | O_APPEND | O_CLOEXEC, 0600);
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
  const int flags = ::fcntl(g_event_write_fd, F_GETFL, 0);
  if (flags < 0 || ::fcntl(g_event_write_fd, F_SETFL, flags | O_NONBLOCK) != 0) {
    std::snprintf(g_probe_status, sizeof(g_probe_status), "pipe_nonblock_failed");
    return false;
  }
  std::string header = "{\"schema\":\"";
  header += kSchema;
  header += "\",\"kind\":\"probe_started\",\"pid\":";
  header += std::to_string(static_cast<unsigned long>(::getpid()));
  header += ",\"build_id\":\"";
  header += kExpectedBuildId;
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
        g_probe_status, sizeof(g_probe_status), "build_id_mismatch:%s",
        identity.build_id.c_str());
    return false;
  }
  g_module_base = identity.base;
  if (!HasPrefix(
          g_module_base + kWritePersistentConstOffset,
          kWritePersistentConstPrefix) ||
      !HasPrefix(
          g_module_base + kWritePersistentOffset, kWritePersistentPrefix) ||
      !HasPrefix(
          g_module_base + kWriteBuildableOffset, kWriteBuildablePrefix)) {
    std::snprintf(
        g_probe_status, sizeof(g_probe_status), "function_signature_mismatch");
    return false;
  }
  return true;
}

bool InstallHooks() {
  if (!PrepareHook(
          &g_write_persistent_const_patch,
          g_module_base + kWritePersistentConstOffset,
          kPersistentHookLength) ||
      !PrepareHook(
          &g_write_persistent_patch,
          g_module_base + kWritePersistentOffset,
          kPersistentHookLength) ||
      !PrepareHook(
          &g_write_buildable_patch,
          g_module_base + kWriteBuildableOffset,
          kBuildableHookLength)) {
    return false;
  }
  g_original_write_persistent_const = reinterpret_cast<PersistentWriteFn>(
      g_write_persistent_const_patch.trampoline);
  g_original_write_persistent =
      reinterpret_cast<PersistentWriteFn>(g_write_persistent_patch.trampoline);
  g_original_write_buildable =
      reinterpret_cast<BuildableWriteFn>(g_write_buildable_patch.trampoline);
  if (!ApplyHook(
          &g_write_persistent_const_patch,
          reinterpret_cast<void*>(&WritePersistentConstHook))) {
    return false;
  }
  if (!ApplyHook(
          &g_write_persistent_patch,
          reinterpret_cast<void*>(&WritePersistentHook))) {
    RestoreHook(&g_write_persistent_const_patch);
    return false;
  }
  if (!ApplyHook(
          &g_write_buildable_patch,
          reinterpret_cast<void*>(&WriteBuildableHook))) {
    RestoreHook(&g_write_persistent_patch);
    RestoreHook(&g_write_persistent_const_patch);
    return false;
  }
  return true;
}

__attribute__((constructor)) void InitializeProbe() {
  if (!VerifyExecutable() || !OpenEventStream() || !InstallHooks()) {
    return;
  }
  std::snprintf(
      g_probe_status,
      sizeof(g_probe_status),
      "ready:pid=%lu:persistent=0x%lx:buildable=0x%lx",
      static_cast<unsigned long>(::getpid()),
      static_cast<unsigned long>(kWritePersistentConstOffset),
      static_cast<unsigned long>(kWriteBuildableOffset));
}

}  // namespace

extern "C" const char* iag_stellaris_payload_probe_status() {
  return g_probe_status;
}
