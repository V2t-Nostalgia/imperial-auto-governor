#include "stop_research.h"

#include <array>
#include <cstddef>
#include <new>
#include <utility>
#include <vector>

#include "iag_native_runtime/game_api.h"

namespace iag::native_runtime {
namespace {

using CancelResearchCtorFn = void (*)(void*, std::uint32_t, const void*);
using CancelResearchIsValidFn = bool (*)(const void*, void*);
using IsResearchingTechnologyFn = bool (*)(const void*, const void*);
using CStringCtorFn = void (*)(void*, const char*);
using CStringDtorFn = void (*)(void*);
using AccessTechnologyFn = const void* (*)(void*, const void*);

class StopResearchInvocation final : public ActionInvocation {
 public:
  explicit StopResearchInvocation(std::string technology_id)
      : technology_id_(std::move(technology_id)) {}

  [[nodiscard]] std::string_view action_type() const noexcept override {
    return "stop_research";
  }

  [[nodiscard]] std::string TargetEcho() const override {
    return technology_id_;
  }

  [[nodiscard]] PrepareResult Prepare(
      const GameApi& game,
      std::uint32_t country_id,
      const void* local_country) override {
    const auto& profile = game.profile();
    std::vector<std::byte> key(profile.c_string.object_size);
    game.FunctionAt<CStringCtorFn>(profile.c_string.construct)(
        key.data(), technology_id_.c_str());
    void* database = *reinterpret_cast<void* const*>(
        game.Address(profile.stop_research.technology_database_instance));
    const void* technology = nullptr;
    if (database != nullptr) {
      technology = game.FunctionAt<AccessTechnologyFn>(
          profile.stop_research.access_technology)(database, key.data());
    }
    game.FunctionAt<CStringDtorFn>(profile.c_string.destroy)(key.data());
    const void* null_technology = *reinterpret_cast<void* const*>(
        game.Address(profile.stop_research.null_technology_instance));
    if (technology == nullptr || technology == null_technology) {
      return PrepareResult::Rejected("unknown_technology_id");
    }
    if (!IsResearching(game, local_country, technology)) {
      return PrepareResult::Rejected(
          "technology_is_not_currently_researching");
    }

    std::vector<std::byte> validation_command(
        profile.stop_research.command_size);
    auto construct = game.FunctionAt<CancelResearchCtorFn>(
        profile.stop_research.construct_command);
    construct(validation_command.data(), country_id, technology);
    if (!game.FunctionAt<CancelResearchIsValidFn>(
            profile.stop_research.command_is_valid)(
            validation_command.data(), nullptr)) {
      return PrepareResult::Rejected("native_is_valid_false_before_submit");
    }

    void* command = ::operator new(
        profile.stop_research.command_size, std::nothrow);
    if (command == nullptr) {
      return PrepareResult::Failed("command_allocation_failed");
    }
    construct(command, country_id, technology);
    technology_ = technology;
    return PrepareResult::Ready(command);
  }

  [[nodiscard]] std::optional<RuntimeResult> Verify(
      const GameApi& game,
      std::uint32_t,
      const void* local_country) override {
    if (local_country != nullptr && technology_ != nullptr &&
        !IsResearching(game, local_country, technology_)) {
      return RuntimeResult{
          "confirmed", "native_postcondition_not_researching"};
    }
    return std::nullopt;
  }

  [[nodiscard]] std::string_view TimeoutDetail() const noexcept override {
    return "native_submit_timeout_waiting_for_postcondition";
  }

 private:
  [[nodiscard]] static bool IsResearching(
      const GameApi& game,
      const void* country,
      const void* technology) {
    const auto& bindings = game.profile().stop_research;
    const void* technology_status =
        static_cast<const unsigned char*>(country) +
        bindings.technology_status_offset;
    return game.FunctionAt<IsResearchingTechnologyFn>(
        bindings.is_researching_technology)(technology_status, technology);
  }

  std::string technology_id_;
  const void* technology_ = nullptr;
};

std::unique_ptr<ActionInvocation> ParseStopResearch(
    std::span<const std::string> fields,
    std::string& error_detail) {
  if (fields.size() != 1U || !IsSemanticToken(fields[0], 160U)) {
    error_detail = "invalid_semantic_target";
    return nullptr;
  }
  return std::make_unique<StopResearchInvocation>(fields[0]);
}

bool StopResearchSupported(const BuildProfile& profile) noexcept {
  return profile.stop_research.command_size != 0U &&
         profile.stop_research.command_is_valid != 0U;
}

constexpr std::array<ActionParameterDescriptor, 1> kParameters = {{
    {
        .name = "technology_id",
        .type = ActionParameterType::kString,
        .required = true,
        .description = "Current researched technology identifier.",
        .maximum_length = 160,
    },
}};

const ActionDescriptor kDescriptor = {
    .action_type = "stop_research",
    .action_version = 1,
    .application_id = "research_strategy",
    .description = "Cancel the currently active matching research project.",
    .risk_class = "destructive_state_change",
    .verification_state = "live_verified",
    .parameters = kParameters,
    .parse = &ParseStopResearch,
    .supported = &StopResearchSupported,
};
IAG_NATIVE_ACTION_REGISTRATION const ActionRegistration kRegistration{
    kDescriptor};

}  // namespace

const ActionDescriptor& StopResearchDescriptor() noexcept {
  return kDescriptor;
}

}  // namespace iag::native_runtime
