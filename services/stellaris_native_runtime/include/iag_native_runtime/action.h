#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace iag::native_runtime {

class GameApi;
struct BuildProfile;

struct RuntimeResult {
  std::string outcome;
  std::string detail;
};

enum class PreparationStatus { kReady, kRejected, kFailed };

struct PrepareResult {
  PreparationStatus status;
  void* command;
  std::string detail;

  [[nodiscard]] static PrepareResult Ready(void* command);
  [[nodiscard]] static PrepareResult Rejected(std::string detail);
  [[nodiscard]] static PrepareResult Failed(std::string detail);
};

// One parsed semantic request. Implementations own only action-specific state;
// the runtime core owns authority checks, serialization and native submission.
class ActionInvocation {
 public:
  virtual ~ActionInvocation() = default;

  [[nodiscard]] virtual std::string_view action_type() const noexcept = 0;
  [[nodiscard]] virtual std::string TargetEcho() const = 0;
  [[nodiscard]] virtual PrepareResult Prepare(
      const GameApi& game,
      std::uint32_t country_id,
      const void* local_country) = 0;
  [[nodiscard]] virtual std::optional<RuntimeResult> Verify(
      const GameApi& game,
      std::uint32_t country_id,
      const void* local_country) = 0;
  [[nodiscard]] virtual std::string_view TimeoutDetail() const noexcept = 0;
};

using ActionFactory = std::unique_ptr<ActionInvocation> (*)(
    std::span<const std::string> target_fields,
    std::string& error_detail);

enum class ActionParameterType {
  kUint32,
  kString,
  kBoolean,
};

struct ActionParameterDescriptor {
  std::string_view name;
  ActionParameterType type;
  bool required;
  std::string_view description;
  std::uint64_t minimum = 0;
  std::uint64_t maximum = 0;
  std::size_t maximum_length = 0;
};

using ActionSupportProbe = bool (*)(const BuildProfile& profile) noexcept;

struct ActionDescriptor {
  std::string_view action_type;
  std::uint32_t action_version;
  // New contributions default to the catch-all Application until the semantic
  // owner is reviewed. This is metadata, not an authority bypass.
  std::string_view application_id;
  std::string_view description;
  std::string_view risk_class;
  std::string_view verification_state;
  std::span<const ActionParameterDescriptor> parameters;
  ActionFactory parse;
  ActionSupportProbe supported;
};

// ELF constructors can run before a library-level constructor in another
// translation unit. Register actions at an earlier initialization priority so
// the runtime can validate the complete catalog before installing its hook.
#if defined(__GNUC__) && !defined(_WIN32)
#define IAG_NATIVE_ACTION_REGISTRATION __attribute__((init_priority(200)))
#else
#define IAG_NATIVE_ACTION_REGISTRATION
#endif

// Each action translation unit registers its own descriptor. CMake already
// discovers actions/*.cpp, so adding one reviewed source file no longer
// requires editing a central action-name list.
class ActionRegistration final {
 public:
  explicit ActionRegistration(const ActionDescriptor& descriptor) noexcept;
};

[[nodiscard]] const ActionDescriptor* FindActionDescriptor(
    std::string_view action_type) noexcept;
[[nodiscard]] std::vector<const ActionDescriptor*> RegisteredActionDescriptors();
[[nodiscard]] bool ValidateActionDescriptors(std::string& error_detail);
[[nodiscard]] std::string BuildToolManifestJson(const BuildProfile& profile);
[[nodiscard]] bool IsSemanticToken(
    std::string_view value,
    std::size_t maximum) noexcept;
[[nodiscard]] std::optional<std::uint32_t> ParseUint32(
    std::string_view value) noexcept;

}  // namespace iag::native_runtime
