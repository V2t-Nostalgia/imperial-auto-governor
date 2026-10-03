#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <limits>
#include <mutex>
#include <new>
#include <string>
#include <string_view>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {

constexpr char kExpectedBuildId[] =
    "c6969e60fd81d738948222a94c0b5a0841abbffc";
constexpr char kDefaultAgentSocket[] = "/tmp/iag-stellaris-ui-agent.sock";
constexpr char kProtocol[] = "IAGUI1";
constexpr char kLogPath[] = "/tmp/iag-stellaris-ui-bridge.log";

// Stellaris 4.4.6 Linux, ELF build ID c6969e60... . These offsets are
// research-only and are protected by the build ID and hook prefixes below.
constexpr std::uintptr_t kGuiPerFrameUpdateOffset = 0x3fa33a0;
constexpr std::uintptr_t kButtonOnClickOffset = 0x3f50730;
constexpr std::uintptr_t kCStringConstructorOffset = 0x3e4a740;
constexpr std::uintptr_t kCStringDestructorOffset = 0x16584b0;
constexpr std::uintptr_t kGuiCreateContainerWindowOffset = 0x3fa2190;
constexpr std::uintptr_t kGuiGetGuiTypeOffset = 0x3fa22d0;
constexpr std::uintptr_t kGuiIsValidOffset = 0x3fa43c0;
constexpr std::uintptr_t kWindowGetButtonOffset = 0x3f61f80;
constexpr std::uintptr_t kWindowGetEditBoxOffset = 0x3f61fb0;
constexpr std::uintptr_t kWindowGetInstantTextBoxOffset = 0x3f61fa0;
constexpr std::uintptr_t kWindowGetSmoothListboxOffset = 0x3f62050;
constexpr std::uintptr_t kWindowShowOffset = 0x3f604a0;
constexpr std::uintptr_t kWindowHideOffset = 0x3f60c00;
constexpr std::uintptr_t kWindowMoveToGuiFrontOffset = 0x3f65140;
constexpr std::uintptr_t kInstantTextBoxChangeStringOffset = 0x3fb1e50;
constexpr std::uintptr_t kEditBoxUpdateSpriteOffset = 0x3f80340;
constexpr std::uintptr_t kTextBufferSetStringOffset = 0x3ff0f00;
constexpr std::uintptr_t kTextBufferGetStringOffset = 0x3ff1060;
constexpr std::uintptr_t kSmoothListboxItemConstructorOffset = 0x3fd4b30;
constexpr std::uintptr_t kSmoothListboxItemDestructorOffset = 0x3fd4d00;
constexpr std::uintptr_t kSmoothListboxAddOffset = 0x3fd70b0;
constexpr std::uintptr_t kSmoothListboxDeleteAndRemoveAllOffset = 0x3fbd160;
constexpr std::uintptr_t kSmoothListboxSetScrollbarToMaxOffset = 0x3fd7e90;
constexpr std::uintptr_t kSmoothListboxUpdateGraphicsOffset = 0x3fd8550;
constexpr std::uintptr_t kCurrentGameStateOffset = 0x5492098;
constexpr std::uintptr_t kCurrentIdlerOffset = 0x5491900;
constexpr std::uintptr_t kCurrentFrontEndIdlerOffset = 0x5491908;
constexpr std::uintptr_t kCurrentInGameIdlerOffset = 0x5491910;

constexpr std::size_t kGuiPerFrameHookLength = 18;
constexpr std::size_t kButtonOnClickHookLength = 5;
constexpr std::size_t kRelativeJumpLength = 5;
constexpr std::size_t kAbsoluteJumpLength = 14;
constexpr std::size_t kEditBoxTextBufferOffset = 0xd8;
constexpr std::size_t kCStringDataPointerOffset = 0x10;
constexpr std::size_t kMaxProtocolLine = 65536;
constexpr std::uint32_t kContainerWindowGuiType = 0x264;
constexpr std::size_t kGuiTypeIdOffset = 0x148;
constexpr std::size_t kGameStartedFlagOffset = 0xa0;
constexpr std::size_t kContainerGuiObjectOffset = 0x38;
constexpr std::size_t kSmoothListboxItemWindowOffset = 0x68;
constexpr std::size_t kSmoothListboxItemSize = 0x78;
constexpr std::size_t kMaxConversationMessages = 80;
constexpr std::size_t kMaxRenderedMessageBytes = 6000;
constexpr std::size_t kMaxNotificationQueue = 16;
constexpr std::size_t kNotificationLineWidth = 28;
constexpr std::size_t kNotificationMaxLines = 3;
constexpr std::size_t kNotificationMaxBytes = 360;
constexpr std::chrono::seconds kNotificationLifetime{8};

struct ApplicationDescriptor {
  const char* id;
  const char* display_name;
  const char* contact_button;
  const char* active_marker;
  const char* unread_marker;
  const char* welcome;
};

constexpr std::array<ApplicationDescriptor, 4> kApplications = {{
    {
        "fleet_operations",
        "Fleet & Expansion",
        "contact_fleet",
        "active_fleet",
        "unread_fleet",
        "Fleet operations channel is online.",
    },
    {
        "economy_governance",
        "Economy Governance",
        "contact_economy",
        "active_economy",
        "unread_economy",
        "Economic governance channel is online.",
    },
    {
        "research_strategy",
        "Research Strategy",
        "contact_research",
        "active_research",
        "unread_research",
        "Research strategy channel is online.",
    },
    {
        "etc",
        "Other Capabilities",
        "contact_etc",
        "active_etc",
        "unread_etc",
        "Native communications are online.",
    },
}};
constexpr std::size_t kDefaultApplicationIndex = kApplications.size() - 1U;

constexpr std::array<unsigned char, kGuiPerFrameHookLength>
    kGuiPerFramePrefix = {
        0x41, 0x57, 0x41, 0x56, 0x41, 0x54, 0x53, 0x50, 0x49,
        0x89, 0xfc, 0x48, 0x8b, 0xbf, 0xf8, 0x01, 0x00, 0x00,
};
constexpr std::array<unsigned char, kButtonOnClickHookLength>
    kButtonOnClickPrefix = {0x41, 0x57, 0x41, 0x56, 0x53};

using GuiPerFrameFn = void (*)(void*, float);
using ButtonOnClickFn = void (*)(void*);
using CStringConstructorFn = void* (*)(void*, const char*);
using CStringDestructorFn = void (*)(void*);
using GuiCreateContainerWindowFn =
    void* (*)(void*, const void*, void*, void*);
using GuiGetGuiTypeFn = void* (*)(void*, const void*);
using GuiIsValidFn = bool (*)(const void*, void*);
using WindowGetChildFn = void* (*)(const void*, const void*);
using ObjectVoidFn = void (*)(void*);
using ChangeStringFn = void (*)(void*, const char*);
using TextBufferSetStringFn = void (*)(void*, const char*);
using TextBufferGetStringFn = void* (*)(void*, const void*);
using SmoothListboxItemConstructorFn =
    void (*)(void*, void*, const void*, void*);
using SmoothListboxItemDestructorFn = void (*)(void*);
using SmoothListboxAddFn = void (*)(void*, void*, bool);

struct MainModuleIdentity {
  std::uintptr_t base = 0;
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

struct GameFunctions {
  std::uintptr_t module_base = 0;
  CStringConstructorFn string_constructor = nullptr;
  CStringDestructorFn string_destructor = nullptr;
  GuiCreateContainerWindowFn create_container_window = nullptr;
  GuiGetGuiTypeFn get_gui_type = nullptr;
  GuiIsValidFn is_valid = nullptr;
  WindowGetChildFn get_button = nullptr;
  WindowGetChildFn get_edit_box = nullptr;
  WindowGetChildFn get_instant_text_box = nullptr;
  WindowGetChildFn get_smooth_listbox = nullptr;
  ObjectVoidFn show_window = nullptr;
  ObjectVoidFn hide_window = nullptr;
  ObjectVoidFn move_window_to_front = nullptr;
  ChangeStringFn change_string = nullptr;
  ObjectVoidFn update_edit_sprite = nullptr;
  TextBufferSetStringFn set_buffer_string = nullptr;
  TextBufferGetStringFn get_buffer_string = nullptr;
  SmoothListboxItemConstructorFn construct_smooth_listbox_item = nullptr;
  SmoothListboxItemDestructorFn destroy_smooth_listbox_item = nullptr;
  SmoothListboxAddFn add_smooth_listbox_item = nullptr;
  ObjectVoidFn delete_and_remove_all_smooth_listbox_items = nullptr;
  ObjectVoidFn set_smooth_listbox_to_max = nullptr;
  ObjectVoidFn update_smooth_listbox_graphics = nullptr;
};

GameFunctions g_game;
HookPatch g_per_frame_patch;
HookPatch g_button_patch;
GuiPerFrameFn g_original_per_frame = nullptr;
ButtonOnClickFn g_original_button_on_click = nullptr;

void* g_window = nullptr;
void* g_launcher_window = nullptr;
void* g_notification_window = nullptr;
void* g_gui = nullptr;
void* g_input = nullptr;
void* g_send_button = nullptr;
void* g_open_button = nullptr;
void* g_close_button = nullptr;
void* g_notification_open_button = nullptr;
void* g_notification_close_button = nullptr;
void* g_notification_application = nullptr;
void* g_notification_text = nullptr;
void* g_message_list = nullptr;
void* g_active_title = nullptr;
void* g_status_box = nullptr;
void* g_launcher_badge = nullptr;
std::array<void*, kApplications.size()> g_contact_buttons{};
std::array<void*, kApplications.size()> g_contact_active_markers{};
std::array<void*, kApplications.size()> g_contact_unread_markers{};
bool g_panel_visible = false;
bool g_notification_visible = false;
bool g_suspended_outside_campaign = false;
std::size_t g_notification_application_index = kDefaultApplicationIndex;
std::chrono::steady_clock::time_point g_notification_deadline{};
std::uint64_t g_frame_count = 0;
std::uint64_t g_next_message_id = 1;
char g_status[256] = "not_initialized";
thread_local bool g_inside_per_frame = false;
thread_local bool g_inside_button = false;

enum class ConversationSpeaker {
  player,
  application,
};

struct ConversationMessage {
  std::uint64_t id = 0;
  ConversationSpeaker speaker = ConversationSpeaker::application;
  std::string text;
};

struct ConversationState {
  std::deque<ConversationMessage> messages;
  std::size_t unread = 0;
};

struct BridgeRequest {
  std::size_t application_index = kDefaultApplicationIndex;
  std::string message;
};

struct BridgeResponse {
  std::size_t application_index = kDefaultApplicationIndex;
  std::string message;
};

struct NotificationToast {
  std::size_t application_index = kDefaultApplicationIndex;
  std::string preview;
};

struct ProtocolResponse {
  bool valid = false;
  std::string operation;
  std::string application_id;
  std::string message;
  std::string error;
};

struct DynamicState {
  std::string agent_socket = kDefaultAgentSocket;
  std::size_t active_application_index = kDefaultApplicationIndex;
  std::array<ConversationState, kApplications.size()> conversations;
  std::mutex log_mutex;
  std::mutex request_mutex;
  std::condition_variable request_changed;
  std::deque<BridgeRequest> requests;
  std::mutex response_mutex;
  std::deque<BridgeResponse> responses;
  std::deque<NotificationToast> notifications;
};

DynamicState& State() {
  static DynamicState state;
  return state;
}

void Log(const char* format, ...) {
  std::lock_guard lock(State().log_mutex);
  const int fd =
      ::open(kLogPath, O_CREAT | O_WRONLY | O_APPEND | O_CLOEXEC, 0600);
  if (fd < 0) {
    return;
  }
  va_list arguments;
  va_start(arguments, format);
  ::vdprintf(fd, format, arguments);
  va_end(arguments);
  ::close(fd);
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
        return 1;
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

bool PrepareHook(HookPatch* patch, std::uintptr_t address, std::size_t length) {
  if (length < kRelativeJumpLength || length > patch->original.size()) {
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
    return false;
  }
  const auto distance = reinterpret_cast<std::intptr_t>(patch->relay) -
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
  if (::mprotect(patch->relay, kAbsoluteJumpLength, PROT_READ | PROT_EXEC) != 0) {
    return false;
  }
  if (!SetTargetProtection(*patch, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    return false;
  }
  std::array<unsigned char, 32> bytes{};
  bytes.fill(0x90);
  bytes[0] = 0xe9;
  const auto encoded_distance = static_cast<std::int32_t>(distance);
  std::memcpy(bytes.data() + 1, &encoded_distance, sizeof(encoded_distance));
  std::memcpy(patch->target, bytes.data(), patch->length);
  __builtin___clear_cache(
      reinterpret_cast<char*>(patch->target),
      reinterpret_cast<char*>(patch->target) + patch->length);
  patch->applied = true;
  return SetTargetProtection(*patch, PROT_READ | PROT_EXEC);
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

template <typename Function>
Function GameFunction(std::uintptr_t offset) {
  return reinterpret_cast<Function>(g_game.module_base + offset);
}

bool IsInCampaign() {
  const auto state_location = reinterpret_cast<void* const*>(
      g_game.module_base + kCurrentGameStateOffset);
  const auto* state = static_cast<const unsigned char*>(*state_location);
  const void* current_idler = *reinterpret_cast<void* const*>(
      g_game.module_base + kCurrentIdlerOffset);
  const void* front_end_idler = *reinterpret_cast<void* const*>(
      g_game.module_base + kCurrentFrontEndIdlerOffset);
  const void* in_game_idler = *reinterpret_cast<void* const*>(
      g_game.module_base + kCurrentInGameIdlerOffset);
  return state != nullptr && state[kGameStartedFlagOffset] != 0 &&
         current_idler != nullptr && current_idler == in_game_idler &&
         front_end_idler == nullptr;
}

class GameString {
 public:
  explicit GameString(const char* value) {
    g_game.string_constructor(storage_.data(), value);
  }

  ~GameString() { g_game.string_destructor(storage_.data()); }

  GameString(const GameString&) = delete;
  GameString& operator=(const GameString&) = delete;

  const void* get() const { return storage_.data(); }

 private:
  alignas(16) std::array<std::byte, 64> storage_{};
};

std::string EscapeField(std::string_view value) {
  std::string escaped;
  escaped.reserve(value.size());
  for (const char character : value) {
    switch (character) {
      case '\\':
        escaped += "\\\\";
        break;
      case '\t':
        escaped += "\\t";
        break;
      case '\r':
        escaped += "\\r";
        break;
      case '\n':
        escaped += "\\n";
        break;
      default:
        escaped.push_back(character);
        break;
    }
  }
  return escaped;
}

std::string UnescapeField(std::string_view value) {
  std::string unescaped;
  unescaped.reserve(value.size());
  for (std::size_t index = 0; index < value.size(); ++index) {
    if (value[index] != '\\' || index + 1U >= value.size()) {
      unescaped.push_back(value[index]);
      continue;
    }
    const char escaped = value[++index];
    switch (escaped) {
      case 'n':
        unescaped.push_back('\n');
        break;
      case 'r':
        unescaped.push_back('\r');
        break;
      case 't':
        unescaped.push_back('\t');
        break;
      default:
        unescaped.push_back(escaped);
        break;
    }
  }
  return unescaped;
}

ProtocolResponse ExchangeWithAgent(
    std::string_view operation,
    std::string_view application_id,
    std::string_view message) {
  const int socket_fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (socket_fd < 0) {
    return ProtocolResponse{
        false,
        {},
        {},
        {},
        "Local IAG bridge could not create its IPC socket.",
    };
  }

  timeval timeout{};
  timeout.tv_sec = 15;
  (void)::setsockopt(
      socket_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  (void)::setsockopt(
      socket_fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  if (State().agent_socket.size() >= sizeof(address.sun_path)) {
    ::close(socket_fd);
    return ProtocolResponse{
        false,
        {},
        {},
        {},
        "Configured IAG bridge socket path is too long.",
    };
  }
  std::memcpy(
      address.sun_path,
      State().agent_socket.c_str(),
      State().agent_socket.size() + 1U);
  if (::connect(
          socket_fd,
          reinterpret_cast<const sockaddr*>(&address),
          sizeof(address)) != 0) {
    ::close(socket_fd);
    return ProtocolResponse{
        false,
        {},
        {},
        {},
        "Local IAG service is offline; start echo_agent.py for this probe.",
    };
  }

  const std::string request = std::string(kProtocol) +
                              "\t" + std::string(operation) + "\t" +
                              EscapeField(application_id) + "\t" +
                              EscapeField(message) + "\n";
  std::size_t sent = 0;
  while (sent < request.size()) {
    const ssize_t count = ::send(
        socket_fd,
        request.data() + sent,
        request.size() - sent,
        MSG_NOSIGNAL);
    if (count < 0 && errno == EINTR) {
      continue;
    }
    if (count <= 0) {
      ::close(socket_fd);
      return ProtocolResponse{
          false,
          {},
          {},
          {},
          "Local IAG service disconnected while receiving the request.",
      };
    }
    sent += static_cast<std::size_t>(count);
  }
  (void)::shutdown(socket_fd, SHUT_WR);

  std::string response;
  std::array<char, 4096> buffer{};
  while (response.size() < kMaxProtocolLine) {
    const ssize_t count = ::recv(socket_fd, buffer.data(), buffer.size(), 0);
    if (count < 0 && errno == EINTR) {
      continue;
    }
    if (count <= 0) {
      break;
    }
    response.append(buffer.data(), static_cast<std::size_t>(count));
    if (response.find('\n') != std::string::npos) {
      break;
    }
  }
  ::close(socket_fd);

  const std::size_t line_end = response.find('\n');
  if (line_end != std::string::npos) {
    response.resize(line_end);
  }
  const std::size_t first = response.find('\t');
  const std::size_t second =
      first == std::string::npos ? first : response.find('\t', first + 1U);
  const std::size_t third =
      second == std::string::npos ? second : response.find('\t', second + 1U);
  if (first == std::string::npos || second == std::string::npos ||
      third == std::string::npos || response.substr(0, first) != kProtocol) {
    return ProtocolResponse{
        false,
        {},
        {},
        {},
        "Local IAG service returned an invalid IAGUI1 response.",
    };
  }
  return ProtocolResponse{
      true,
      response.substr(first + 1U, second - first - 1U),
      UnescapeField(response.substr(second + 1U, third - second - 1U)),
      UnescapeField(response.substr(third + 1U)),
      {},
  };
}

std::size_t FindApplicationIndex(std::string_view application_id);

std::string SubmitToAgent(
    std::string_view application_id,
    std::string_view message) {
  ProtocolResponse response = ExchangeWithAgent(
      "conversation.submit", application_id, message);
  if (!response.valid) {
    return std::move(response.error);
  }
  if (response.operation != "conversation.response") {
    return "Local IAG service returned an unexpected response type.";
  }
  if (response.application_id != application_id) {
    return "Local IAG service returned a response for a different "
           "Application.";
  }
  return std::move(response.message);
}

bool PollAgentMessage(BridgeResponse* output) {
  ProtocolResponse response =
      ExchangeWithAgent("conversation.poll", "*", "");
  if (!response.valid || response.operation == "conversation.none") {
    return false;
  }
  if (response.operation != "conversation.push") {
    return false;
  }
  output->application_index = FindApplicationIndex(response.application_id);
  output->message = std::move(response.message);
  return true;
}

void BridgeWorker() {
  for (;;) {
    BridgeRequest request;
    bool has_request = false;
    {
      std::unique_lock lock(State().request_mutex);
      (void)State().request_changed.wait_for(
          lock,
          std::chrono::milliseconds(400),
          [] { return !State().requests.empty(); });
      if (!State().requests.empty()) {
        request = std::move(State().requests.front());
        State().requests.pop_front();
        has_request = true;
      }
    }
    if (has_request) {
      const std::string application_id =
          kApplications[request.application_index].id;
      std::string response = SubmitToAgent(application_id, request.message);
      {
        std::lock_guard lock(State().response_mutex);
        State().responses.push_back(
            BridgeResponse{request.application_index, std::move(response)});
      }
    }

    BridgeResponse pushed;
    if (PollAgentMessage(&pushed)) {
      std::lock_guard lock(State().response_mutex);
      State().responses.push_back(std::move(pushed));
    }
  }
}

void ChangeText(void* text_box, const std::string& value) {
  if (text_box != nullptr) {
    g_game.change_string(text_box, value.c_str());
  }
}

void ResetPanelPointers() {
  g_window = nullptr;
  g_launcher_window = nullptr;
  g_notification_window = nullptr;
  g_gui = nullptr;
  g_input = nullptr;
  g_send_button = nullptr;
  g_open_button = nullptr;
  g_close_button = nullptr;
  g_notification_open_button = nullptr;
  g_notification_close_button = nullptr;
  g_notification_application = nullptr;
  g_notification_text = nullptr;
  g_message_list = nullptr;
  g_active_title = nullptr;
  g_status_box = nullptr;
  g_launcher_badge = nullptr;
  g_contact_buttons.fill(nullptr);
  g_contact_active_markers.fill(nullptr);
  g_contact_unread_markers.fill(nullptr);
  g_panel_visible = false;
  g_notification_visible = false;
}

void* GetNamedChild(WindowGetChildFn getter, void* window, const char* name) {
  GameString game_name(name);
  return getter(window, game_name.get());
}

void* CreateContainerWindow(void* gui, const char* name) {
  GameString type_name(name);
  const void* gui_type = g_game.get_gui_type(gui, type_name.get());
  if (gui_type == nullptr ||
      *reinterpret_cast<const std::uint32_t*>(
          static_cast<const unsigned char*>(gui_type) + kGuiTypeIdOffset) !=
          kContainerWindowGuiType) {
    Log("container_create_failed name=%s: missing or incompatible gui type\n",
        name);
    return nullptr;
  }
  return g_game.create_container_window(gui, type_name.get(), nullptr, nullptr);
}

std::size_t FindApplicationIndex(std::string_view application_id) {
  for (std::size_t index = 0; index < kApplications.size(); ++index) {
    if (application_id == kApplications[index].id) {
      return index;
    }
  }
  return kDefaultApplicationIndex;
}

void UpdateLauncherBadge() {
  bool has_unread = false;
  for (const ConversationState& conversation : State().conversations) {
    has_unread = has_unread || conversation.unread != 0U;
  }
  ChangeText(g_launcher_badge, has_unread ? "!" : "");
}

void UpdateApplicationChrome() {
  const std::size_t active = State().active_application_index;
  for (std::size_t index = 0; index < kApplications.size(); ++index) {
    ChangeText(g_contact_active_markers[index], index == active ? ">" : "");
    const std::size_t unread = State().conversations[index].unread;
    ChangeText(
        g_contact_unread_markers[index],
        unread == 0U ? std::string{} : std::to_string(unread));
  }
  ChangeText(g_active_title, kApplications[active].display_name);
  ChangeText(
      g_status_box,
      std::string("CONNECTED // APPLICATION: ") + kApplications[active].id);
  UpdateLauncherBadge();
}

std::vector<std::string> WrapMessage(
    std::string_view message,
    std::size_t max_line_width = 66U) {
  std::string visible(message);
  if (visible.size() > kMaxRenderedMessageBytes) {
    std::size_t end = kMaxRenderedMessageBytes;
    while (end > 0U &&
           (static_cast<unsigned char>(visible[end]) & 0xc0U) == 0x80U) {
      --end;
    }
    visible.resize(end);
    visible += "\n[message truncated in native panel]";
  }

  std::vector<std::string> lines;
  std::string line;
  std::size_t current_line_width = 0;
  for (std::size_t index = 0; index < visible.size();) {
    const unsigned char first = static_cast<unsigned char>(visible[index]);
    if (first == '\r') {
      ++index;
      continue;
    }
    if (first == '\n') {
      lines.push_back(line.empty() ? " " : std::move(line));
      line.clear();
      current_line_width = 0;
      ++index;
      continue;
    }

    std::size_t length = 1;
    std::size_t display_width = 1;
    if ((first & 0x80U) != 0U) {
      display_width = 2;
      if ((first & 0xe0U) == 0xc0U) {
        length = 2;
      } else if ((first & 0xf0U) == 0xe0U) {
        length = 3;
      } else if ((first & 0xf8U) == 0xf0U) {
        length = 4;
      }
      length = std::min(length, visible.size() - index);
    }
    if (!line.empty() &&
        current_line_width + display_width > max_line_width) {
      lines.push_back(std::move(line));
      line.clear();
      current_line_width = 0;
    }
    line.append(visible, index, length);
    current_line_width += display_width;
    index += length;
  }
  if (!line.empty() || lines.empty()) {
    lines.push_back(line.empty() ? " " : std::move(line));
  }
  return lines;
}

std::string BuildNotificationPreview(std::string_view message) {
  std::string visible(message);
  for (char& character : visible) {
    if (character == '\r' || character == '\n' || character == '\t') {
      character = ' ';
    }
  }
  if (visible.size() > kNotificationMaxBytes) {
    std::size_t end = kNotificationMaxBytes;
    while (end > 0U &&
           (static_cast<unsigned char>(visible[end]) & 0xc0U) == 0x80U) {
      --end;
    }
    visible.resize(end);
    visible += "...";
  }

  std::vector<std::string> lines =
      WrapMessage(visible, kNotificationLineWidth);
  const bool omitted_lines = lines.size() > kNotificationMaxLines;
  if (omitted_lines) {
    lines.resize(kNotificationMaxLines);
    lines.back() += "...";
  }
  std::string preview;
  for (std::size_t index = 0; index < lines.size(); ++index) {
    if (index != 0U) {
      preview.push_back('\n');
    }
    preview += lines[index];
  }
  return preview;
}

void HideCurrentNotification(bool retire) {
  if (g_notification_window != nullptr && g_notification_visible) {
    g_game.hide_window(g_notification_window);
  }
  g_notification_visible = false;
  if (retire && !State().notifications.empty()) {
    State().notifications.pop_front();
  }
}

void ClearNotifications() {
  HideCurrentNotification(false);
  State().notifications.clear();
}

void ShowNextNotification() {
  if (g_notification_window == nullptr || g_notification_visible ||
      State().notifications.empty()) {
    return;
  }
  const NotificationToast& notification = State().notifications.front();
  g_notification_application_index = notification.application_index;
  ChangeText(
      g_notification_application,
      kApplications[notification.application_index].display_name);
  ChangeText(g_notification_text, notification.preview);
  g_game.show_window(g_notification_window);
  g_game.move_window_to_front(g_notification_window);
  g_notification_visible = true;
  g_notification_deadline =
      std::chrono::steady_clock::now() + kNotificationLifetime;
  Log("notification_shown application=%s bytes=%zu\n",
      kApplications[notification.application_index].id,
      notification.preview.size());
}

void UpdateNotification() {
  if (g_notification_visible &&
      std::chrono::steady_clock::now() >= g_notification_deadline) {
    HideCurrentNotification(true);
  }
  ShowNextNotification();
}

void QueueNotification(
    std::size_t application_index,
    std::string preview) {
  if (application_index >= kApplications.size() || preview.empty()) {
    return;
  }
  if (State().notifications.size() >= kMaxNotificationQueue) {
    State().notifications.pop_back();
  }
  State().notifications.push_back(
      NotificationToast{application_index, std::move(preview)});
}

bool IsContainerGuiType(void* gui, const char* name) {
  GameString type_name(name);
  const void* gui_type = g_game.get_gui_type(gui, type_name.get());
  return gui_type != nullptr &&
         *reinterpret_cast<const std::uint32_t*>(
             static_cast<const unsigned char*>(gui_type) + kGuiTypeIdOffset) ==
             kContainerWindowGuiType;
}

void* CreateMessageListItem(
    const char* type_name,
    const std::string& text) {
  if (g_gui == nullptr || g_message_list == nullptr ||
      !IsContainerGuiType(g_gui, type_name)) {
    Log("message_item_create_failed type=%s: unavailable gui type\n",
        type_name);
    return nullptr;
  }

  void* item = ::operator new(kSmoothListboxItemSize, std::nothrow);
  if (item == nullptr) {
    Log("message_item_create_failed type=%s: allocation failed\n", type_name);
    return nullptr;
  }
  GameString game_type_name(type_name);
  g_game.construct_smooth_listbox_item(
      item, g_gui, game_type_name.get(), nullptr);
  void* item_window = *reinterpret_cast<void**>(
      static_cast<unsigned char*>(item) + kSmoothListboxItemWindowOffset);
  void* text_box = item_window == nullptr ?
      nullptr :
      GetNamedChild(g_game.get_instant_text_box, item_window, "text");
  if (item_window == nullptr || text_box == nullptr) {
    Log("message_item_create_failed type=%s: missing text control\n",
        type_name);
    g_game.destroy_smooth_listbox_item(item);
    ::operator delete(item);
    return nullptr;
  }

  ChangeText(text_box, text);
  g_game.add_smooth_listbox_item(g_message_list, item, true);
  return item;
}

void RenderMessage(
    std::size_t application_index,
    const ConversationMessage& message) {
  const bool player = message.speaker == ConversationSpeaker::player;
  const char* header_type =
      player ? "iag_message_player_header" : "iag_message_agent_header";
  const char* line_type =
      player ? "iag_message_player_line" : "iag_message_agent_line";
  const std::string header =
      player ? "YOU" : kApplications[application_index].display_name;
  if (CreateMessageListItem(header_type, header) == nullptr) {
    return;
  }
  for (const std::string& line : WrapMessage(message.text)) {
    if (CreateMessageListItem(line_type, line) == nullptr) {
      return;
    }
  }
  (void)CreateMessageListItem("iag_message_spacer", "");
}

void FinishMessageListUpdate() {
  if (g_message_list == nullptr) {
    return;
  }
  g_game.update_smooth_listbox_graphics(g_message_list);
  g_game.set_smooth_listbox_to_max(g_message_list);
}

void RenderActiveConversation() {
  if (g_message_list == nullptr) {
    return;
  }
  g_game.delete_and_remove_all_smooth_listbox_items(g_message_list);
  const std::size_t active = State().active_application_index;
  for (const ConversationMessage& message :
       State().conversations[active].messages) {
    RenderMessage(active, message);
  }
  FinishMessageListUpdate();
}

void AddConversationMessage(
    std::size_t application_index,
    ConversationSpeaker speaker,
    std::string message,
    bool mark_unread) {
  ConversationState& conversation =
      State().conversations[application_index];
  ConversationMessage entry{
      g_next_message_id++, speaker, std::move(message)};
  conversation.messages.push_back(entry);
  bool retired_message = false;
  if (conversation.messages.size() > kMaxConversationMessages) {
    conversation.messages.pop_front();
    retired_message = true;
  }
  if (g_message_list != nullptr &&
      application_index == State().active_application_index) {
    if (retired_message) {
      RenderActiveConversation();
    } else {
      RenderMessage(application_index, conversation.messages.back());
      FinishMessageListUpdate();
    }
  }
  if (mark_unread &&
      (!g_panel_visible ||
       application_index != State().active_application_index)) {
    ++conversation.unread;
  }
  UpdateApplicationChrome();
}

void EnsureWelcomeMessages() {
  for (std::size_t index = 0; index < kApplications.size(); ++index) {
    ConversationState& conversation = State().conversations[index];
    if (conversation.messages.empty()) {
      conversation.messages.push_back(ConversationMessage{
          g_next_message_id++,
          ConversationSpeaker::application,
          kApplications[index].welcome,
      });
    }
  }
}

void SelectApplication(std::size_t application_index) {
  if (application_index >= kApplications.size()) {
    return;
  }
  const bool changed =
      application_index != State().active_application_index;
  State().active_application_index = application_index;
  State().conversations[application_index].unread = 0;
  UpdateApplicationChrome();
  if (changed) {
    RenderActiveConversation();
  } else {
    FinishMessageListUpdate();
  }
  Log("application_selected id=%s\n", kApplications[application_index].id);
}

void OpenPanel() {
  if (g_window == nullptr) {
    return;
  }
  g_panel_visible = true;
  SelectApplication(State().active_application_index);
  g_game.show_window(g_window);
  g_game.move_window_to_front(g_window);
  Log("panel_opened\n");
}

void ClosePanel() {
  if (g_window == nullptr) {
    return;
  }
  g_game.hide_window(g_window);
  g_panel_visible = false;
  Log("panel_closed\n");
}

bool CreatePanel(void* gui) {
  void* launcher = CreateContainerWindow(gui, "iag_llm_chat_launcher");
  void* notification =
      CreateContainerWindow(gui, "iag_llm_notification");
  void* window = CreateContainerWindow(gui, "iag_llm_chat_window");
  if (launcher == nullptr || notification == nullptr || window == nullptr) {
    return false;
  }

  void* open = GetNamedChild(g_game.get_button, launcher, "open_chat");
  void* badge =
      GetNamedChild(g_game.get_instant_text_box, launcher, "unread_badge");
  void* notification_open =
      GetNamedChild(g_game.get_button, notification, "notification_open");
  void* notification_close = GetNamedChild(
      g_game.get_button, notification, "notification_dismiss");
  void* notification_application = GetNamedChild(
      g_game.get_instant_text_box,
      notification,
      "notification_application");
  void* notification_text = GetNamedChild(
      g_game.get_instant_text_box, notification, "notification_text");
  void* title =
      GetNamedChild(g_game.get_instant_text_box, window, "title");
  void* active_title =
      GetNamedChild(g_game.get_instant_text_box, window, "active_title");
  void* message_list =
      GetNamedChild(g_game.get_smooth_listbox, window, "message_list");
  void* status =
      GetNamedChild(g_game.get_instant_text_box, window, "status");
  void* input = GetNamedChild(g_game.get_edit_box, window, "chat_input");
  void* send = GetNamedChild(g_game.get_button, window, "send");
  void* close = GetNamedChild(g_game.get_button, window, "close");
  std::array<void*, kApplications.size()> contact_buttons{};
  std::array<void*, kApplications.size()> active_markers{};
  std::array<void*, kApplications.size()> unread_markers{};
  for (std::size_t index = 0; index < kApplications.size(); ++index) {
    contact_buttons[index] = GetNamedChild(
        g_game.get_button, window, kApplications[index].contact_button);
    active_markers[index] = GetNamedChild(
        g_game.get_instant_text_box,
        window,
        kApplications[index].active_marker);
    unread_markers[index] = GetNamedChild(
        g_game.get_instant_text_box,
        window,
        kApplications[index].unread_marker);
  }
  if (open == nullptr || badge == nullptr || notification_open == nullptr ||
      notification_close == nullptr || notification_application == nullptr ||
      notification_text == nullptr || title == nullptr ||
      active_title == nullptr || message_list == nullptr || status == nullptr ||
      input == nullptr || send == nullptr || close == nullptr ||
      std::find(contact_buttons.begin(), contact_buttons.end(), nullptr) !=
          contact_buttons.end() ||
      std::find(active_markers.begin(), active_markers.end(), nullptr) !=
          active_markers.end() ||
      std::find(unread_markers.begin(), unread_markers.end(), nullptr) !=
          unread_markers.end()) {
    Log("panel_create_failed: missing child control\n");
    return false;
  }

  g_gui = gui;
  g_launcher_window = launcher;
  g_notification_window = notification;
  g_window = window;
  g_input = input;
  g_send_button = send;
  g_open_button = open;
  g_close_button = close;
  g_notification_open_button = notification_open;
  g_notification_close_button = notification_close;
  g_notification_application = notification_application;
  g_notification_text = notification_text;
  g_message_list = message_list;
  g_active_title = active_title;
  g_status_box = status;
  g_launcher_badge = badge;
  g_contact_buttons = contact_buttons;
  g_contact_active_markers = active_markers;
  g_contact_unread_markers = unread_markers;
  g_panel_visible = false;
  g_notification_visible = false;
  ChangeText(title, "IAG // Strategic Communications");
  EnsureWelcomeMessages();
  RenderActiveConversation();
  UpdateApplicationChrome();
  g_game.hide_window(g_window);
  g_game.hide_window(g_notification_window);
  g_game.show_window(g_launcher_window);
  g_game.move_window_to_front(g_launcher_window);
  Log("panel_created launcher=%p notification=%p window=%p list=%p "
      "input=%p send=%p open=%p close=%p\n",
      launcher,
      notification,
      window,
      message_list,
      input,
      send,
      open,
      close);
  return true;
}

std::string ReadInputText() {
  if (g_input == nullptr) {
    return {};
  }
  alignas(16) std::array<std::byte, 64> storage{};
  const auto buffer = reinterpret_cast<const unsigned char*>(g_input) +
                      kEditBoxTextBufferOffset;
  g_game.get_buffer_string(storage.data(), buffer);
  const char* text = *reinterpret_cast<const char* const*>(
      reinterpret_cast<const unsigned char*>(storage.data()) +
      kCStringDataPointerOffset);
  std::string result = text == nullptr ? std::string{} : std::string{text};
  g_game.string_destructor(storage.data());
  return result;
}

void ClearInputText() {
  if (g_input == nullptr) {
    return;
  }
  auto* buffer = reinterpret_cast<unsigned char*>(g_input) +
                 kEditBoxTextBufferOffset;
  g_game.set_buffer_string(buffer, "");
  g_game.update_edit_sprite(g_input);
}

void SubmitInput() {
  std::string message = ReadInputText();
  const auto first = message.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) {
    return;
  }
  const auto last = message.find_last_not_of(" \t\r\n");
  message = message.substr(first, last - first + 1U);
  ClearInputText();
  const std::size_t application_index = State().active_application_index;
  AddConversationMessage(
      application_index,
      ConversationSpeaker::player,
      message,
      false);
  ChangeText(
      g_status_box,
      std::string("WAITING // APPLICATION: ") +
          kApplications[application_index].id);
  {
    std::lock_guard lock(State().request_mutex);
    State().requests.push_back(BridgeRequest{application_index, message});
  }
  State().request_changed.notify_one();
  Log("conversation_submit bytes=%zu application=%s\n",
      message.size(),
      kApplications[application_index].id);
}

void DrainResponses() {
  std::deque<BridgeResponse> responses;
  {
    std::lock_guard lock(State().response_mutex);
    responses.swap(State().responses);
  }
  for (BridgeResponse& response : responses) {
    const std::size_t response_size = response.message.size();
    std::string notification_preview =
        BuildNotificationPreview(response.message);
    AddConversationMessage(
        response.application_index,
        ConversationSpeaker::application,
        std::move(response.message),
        true);
    QueueNotification(
        response.application_index, std::move(notification_preview));
    Log("conversation_response bytes=%zu application=%s\n",
        response_size,
        kApplications[response.application_index].id);
  }
}

void GuiPerFrameHook(void* gui, float delta) {
  g_original_per_frame(gui, delta);
  if (g_inside_per_frame) {
    return;
  }
  g_inside_per_frame = true;
  ++g_frame_count;
  const bool in_campaign = IsInCampaign();

  if (g_window != nullptr && g_launcher_window != nullptr &&
      g_notification_window != nullptr) {
    auto* window_gui_object = static_cast<unsigned char*>(g_window) +
                              kContainerGuiObjectOffset;
    auto* launcher_gui_object =
        static_cast<unsigned char*>(g_launcher_window) +
        kContainerGuiObjectOffset;
    auto* notification_gui_object =
        static_cast<unsigned char*>(g_notification_window) +
        kContainerGuiObjectOffset;
    if (!g_game.is_valid(gui, window_gui_object) ||
        !g_game.is_valid(gui, launcher_gui_object) ||
        !g_game.is_valid(gui, notification_gui_object)) {
      Log("panel_invalidated; scheduling recreation\n");
      ResetPanelPointers();
    }
  }
  if (!in_campaign) {
    if (!g_suspended_outside_campaign) {
      if (g_window != nullptr) {
        g_game.hide_window(g_window);
      }
      if (g_launcher_window != nullptr) {
        g_game.hide_window(g_launcher_window);
      }
      if (g_notification_window != nullptr) {
        g_game.hide_window(g_notification_window);
      }
      g_panel_visible = false;
      ClearNotifications();
      g_suspended_outside_campaign = true;
      Log("panel_suspended: no active in-game idler\n");
    }
    g_inside_per_frame = false;
    return;
  }
  if (g_suspended_outside_campaign) {
    g_suspended_outside_campaign = false;
    if (g_launcher_window != nullptr) {
      g_game.show_window(g_launcher_window);
      g_game.move_window_to_front(g_launcher_window);
    }
    Log("panel_resumed: active in-game idler\n");
  }
  if (g_window == nullptr &&
      (g_frame_count % 120U) == 0U) {
    (void)CreatePanel(gui);
  }
  if (g_window != nullptr) {
    DrainResponses();
    UpdateNotification();
  }
  g_inside_per_frame = false;
}

void ButtonOnClickHook(void* button) {
  g_original_button_on_click(button);
  if (g_inside_button) {
    return;
  }
  g_inside_button = true;
  if (button == g_send_button) {
    SubmitInput();
  } else if (button == g_open_button) {
    OpenPanel();
  } else if (button == g_close_button) {
    ClosePanel();
  } else if (button == g_notification_open_button) {
    const std::size_t application_index =
        g_notification_application_index;
    HideCurrentNotification(true);
    SelectApplication(application_index);
    OpenPanel();
  } else if (button == g_notification_close_button) {
    HideCurrentNotification(true);
  } else {
    for (std::size_t index = 0; index < g_contact_buttons.size(); ++index) {
      if (button == g_contact_buttons[index]) {
        SelectApplication(index);
        break;
      }
    }
  }
  g_inside_button = false;
}

bool InitializeFunctions(const MainModuleIdentity& identity) {
  g_game.module_base = identity.base;
  g_game.string_constructor =
      GameFunction<CStringConstructorFn>(kCStringConstructorOffset);
  g_game.string_destructor =
      GameFunction<CStringDestructorFn>(kCStringDestructorOffset);
  g_game.create_container_window = GameFunction<GuiCreateContainerWindowFn>(
      kGuiCreateContainerWindowOffset);
  g_game.get_gui_type =
      GameFunction<GuiGetGuiTypeFn>(kGuiGetGuiTypeOffset);
  g_game.is_valid = GameFunction<GuiIsValidFn>(kGuiIsValidOffset);
  g_game.get_button = GameFunction<WindowGetChildFn>(kWindowGetButtonOffset);
  g_game.get_edit_box = GameFunction<WindowGetChildFn>(kWindowGetEditBoxOffset);
  g_game.get_instant_text_box =
      GameFunction<WindowGetChildFn>(kWindowGetInstantTextBoxOffset);
  g_game.get_smooth_listbox =
      GameFunction<WindowGetChildFn>(kWindowGetSmoothListboxOffset);
  g_game.show_window = GameFunction<ObjectVoidFn>(kWindowShowOffset);
  g_game.hide_window = GameFunction<ObjectVoidFn>(kWindowHideOffset);
  g_game.move_window_to_front =
      GameFunction<ObjectVoidFn>(kWindowMoveToGuiFrontOffset);
  g_game.change_string =
      GameFunction<ChangeStringFn>(kInstantTextBoxChangeStringOffset);
  g_game.update_edit_sprite =
      GameFunction<ObjectVoidFn>(kEditBoxUpdateSpriteOffset);
  g_game.set_buffer_string =
      GameFunction<TextBufferSetStringFn>(kTextBufferSetStringOffset);
  g_game.get_buffer_string =
      GameFunction<TextBufferGetStringFn>(kTextBufferGetStringOffset);
  g_game.construct_smooth_listbox_item =
      GameFunction<SmoothListboxItemConstructorFn>(
          kSmoothListboxItemConstructorOffset);
  g_game.destroy_smooth_listbox_item =
      GameFunction<SmoothListboxItemDestructorFn>(
          kSmoothListboxItemDestructorOffset);
  g_game.add_smooth_listbox_item =
      GameFunction<SmoothListboxAddFn>(kSmoothListboxAddOffset);
  g_game.delete_and_remove_all_smooth_listbox_items =
      GameFunction<ObjectVoidFn>(kSmoothListboxDeleteAndRemoveAllOffset);
  g_game.set_smooth_listbox_to_max =
      GameFunction<ObjectVoidFn>(kSmoothListboxSetScrollbarToMaxOffset);
  g_game.update_smooth_listbox_graphics =
      GameFunction<ObjectVoidFn>(kSmoothListboxUpdateGraphicsOffset);
  return true;
}

bool InstallHooks() {
  const std::uintptr_t per_frame =
      g_game.module_base + kGuiPerFrameUpdateOffset;
  const std::uintptr_t button = g_game.module_base + kButtonOnClickOffset;
  if (!HasPrefix(per_frame, kGuiPerFramePrefix) ||
      !HasPrefix(button, kButtonOnClickPrefix)) {
    std::snprintf(g_status, sizeof(g_status), "function_signature_mismatch");
    return false;
  }
  if (!PrepareHook(&g_per_frame_patch, per_frame, kGuiPerFrameHookLength) ||
      !ApplyHook(&g_per_frame_patch, reinterpret_cast<void*>(&GuiPerFrameHook))) {
    std::snprintf(g_status, sizeof(g_status), "per_frame_hook_failed");
    return false;
  }
  g_original_per_frame =
      reinterpret_cast<GuiPerFrameFn>(g_per_frame_patch.trampoline);

  if (!PrepareHook(&g_button_patch, button, kButtonOnClickHookLength) ||
      !ApplyHook(
          &g_button_patch, reinterpret_cast<void*>(&ButtonOnClickHook))) {
    RestoreHook(&g_per_frame_patch);
    std::snprintf(g_status, sizeof(g_status), "button_hook_failed");
    return false;
  }
  g_original_button_on_click =
      reinterpret_cast<ButtonOnClickFn>(g_button_patch.trampoline);
  return true;
}

bool Initialize() {
  MainModuleIdentity identity;
  ::dl_iterate_phdr(ReadMainModuleIdentity, &identity);
  if (identity.build_id != kExpectedBuildId) {
    std::snprintf(
        g_status,
        sizeof(g_status),
        "build_id_mismatch:%s",
        identity.build_id.c_str());
    return false;
  }
  const char* socket_path = std::getenv("IAG_NATIVE_UI_AGENT_SOCKET");
  if (socket_path != nullptr && socket_path[0] != '\0') {
    State().agent_socket = socket_path;
  }
  const char* application = std::getenv("IAG_NATIVE_UI_APPLICATION");
  if (application != nullptr && application[0] != '\0') {
    State().active_application_index = FindApplicationIndex(application);
  }
  if (!InitializeFunctions(identity) || !InstallHooks()) {
    return false;
  }
  std::thread(BridgeWorker).detach();
  std::snprintf(g_status, sizeof(g_status), "ready");
  Log("native_ui_bridge_ready build_id=%s socket=%s application=%s\n",
      identity.build_id.c_str(),
      State().agent_socket.c_str(),
      kApplications[State().active_application_index].id);
  return true;
}

}  // namespace

extern "C" __attribute__((visibility("default"))) const char*
iag_native_ui_bridge_status() {
  return g_status;
}

__attribute__((constructor)) static void IagNativeUiBridgeInitialize() {
  if (!Initialize()) {
    Log("native_ui_bridge_init_failed status=%s\n", g_status);
  }
}
