#include "attack_fleet.h"

#include <array>
#include <cstddef>
#include <cstring>
#include <new>
#include <utility>
#include <vector>

#include "iag_native_runtime/game_api.h"

namespace iag::native_runtime {
namespace {

using AttackFleetCtorFn = void (*)(
    void*, std::uint32_t, std::uint32_t, bool, bool, bool, bool);
using AttackFleetIsValidFn = bool (*)(const void*, void*);
using FleetGetControllerRefFn = std::uint32_t (*)(const void*);
using FleetGetExecutingOrderFn = const void* (*)(const void*);

template <typename Value>
Value ReadField(const void* object, std::size_t offset) {
  Value value{};
  std::memcpy(
      &value,
      static_cast<const std::byte*>(object) + offset,
      sizeof(value));
  return value;
}

class AttackFleetInvocation final : public ActionInvocation {
 public:
  AttackFleetInvocation(std::uint32_t fleet_id, std::uint32_t target_fleet_id)
      : fleet_id_(fleet_id), target_fleet_id_(target_fleet_id) {}

  [[nodiscard]] std::string_view action_type() const noexcept override {
    return "attack_fleet";
  }

  [[nodiscard]] std::string TargetEcho() const override {
    return std::to_string(fleet_id_) + ":" +
           std::to_string(target_fleet_id_);
  }

  [[nodiscard]] PrepareResult Prepare(
      const GameApi& game,
      std::uint32_t country_id,
      const void*) override {
    const auto& bindings = game.profile().attack_fleet;
    const void* fleet = game.ResolvePdxObject(bindings.fleets, fleet_id_);
    if (fleet == nullptr) {
      return PrepareResult::Rejected("unknown_fleet_id");
    }
    const void* target =
        game.ResolvePdxObject(bindings.fleets, target_fleet_id_);
    if (target == nullptr) {
      return PrepareResult::Rejected("unknown_target_fleet_id");
    }
    if (fleet == target) {
      return PrepareResult::Rejected("fleet_cannot_attack_itself");
    }
    if (game.FunctionAt<FleetGetControllerRefFn>(
            bindings.fleet_get_controller_ref)(fleet) != country_id) {
      return PrepareResult::Rejected("fleet_authority_mismatch");
    }
    if (!CommandIsValid(game)) {
      return PrepareResult::Rejected("native_is_valid_false_before_submit");
    }

    void* command = ::operator new(bindings.command_size, std::nothrow);
    if (command == nullptr) {
      return PrepareResult::Failed("command_allocation_failed");
    }
    ConstructCommand(game, command);
    return PrepareResult::Ready(command);
  }

  [[nodiscard]] std::optional<RuntimeResult> Verify(
      const GameApi& game,
      std::uint32_t country_id,
      const void*) override {
    const auto& bindings = game.profile().attack_fleet;
    const void* fleet = game.ResolvePdxObject(bindings.fleets, fleet_id_);
    if (fleet == nullptr ||
        game.FunctionAt<FleetGetControllerRefFn>(
            bindings.fleet_get_controller_ref)(fleet) != country_id) {
      return std::nullopt;
    }
    const void* order = game.FunctionAt<FleetGetExecutingOrderFn>(
        bindings.fleet_get_executing_order)(fleet);
    if (order == nullptr ||
        ReadField<std::uintptr_t>(order, 0) !=
            game.Address(bindings.follow_fleet_order_vptr)) {
      return std::nullopt;
    }
    if (ReadField<std::uint32_t>(order, bindings.order_target_fleet_offset) !=
            target_fleet_id_ ||
        !ReadField<bool>(order, bindings.order_attack_offset) ||
        ReadField<bool>(order, bindings.order_cancelled_offset)) {
      return std::nullopt;
    }
    return RuntimeResult{
        "confirmed", "native_postcondition_attack_order_matches_target"};
  }

  [[nodiscard]] std::string_view TimeoutDetail() const noexcept override {
    return "native_submit_timeout_waiting_for_attack_order";
  }

 private:
  void ConstructCommand(const GameApi& game, void* command) const {
    game.FunctionAt<AttackFleetCtorFn>(
        game.profile().attack_fleet.construct_command)(
        command,
        fleet_id_,
        target_fleet_id_,
        true,
        false,
        false,
        false);
  }

  [[nodiscard]] bool CommandIsValid(const GameApi& game) const {
    const auto& bindings = game.profile().attack_fleet;
    std::vector<std::byte> command(bindings.command_size);
    ConstructCommand(game, command.data());
    return game.FunctionAt<AttackFleetIsValidFn>(bindings.command_is_valid)(
        command.data(), nullptr);
  }

  std::uint32_t fleet_id_;
  std::uint32_t target_fleet_id_;
};

std::unique_ptr<ActionInvocation> ParseAttackFleet(
    std::span<const std::string> fields,
    std::string& error_detail) {
  if (fields.size() != 2U) {
    error_detail = "invalid_semantic_target";
    return nullptr;
  }
  const auto fleet_id = ParseUint32(fields[0]);
  const auto target_fleet_id = ParseUint32(fields[1]);
  if (!fleet_id.has_value() || !target_fleet_id.has_value()) {
    error_detail = "invalid_semantic_target";
    return nullptr;
  }
  return std::make_unique<AttackFleetInvocation>(*fleet_id, *target_fleet_id);
}

bool AttackFleetSupported(const BuildProfile& profile) noexcept {
  return profile.attack_fleet.command_size != 0U &&
         profile.attack_fleet.command_is_valid != 0U;
}

constexpr std::array<ActionParameterDescriptor, 2> kParameters = {{
    {
        .name = "fleet_id",
        .type = ActionParameterType::kUint32,
        .required = true,
        .description = "Generation-bearing owned fleet identifier.",
        .minimum = 0,
        .maximum = UINT32_MAX,
    },
    {
        .name = "target_fleet_id",
        .type = ActionParameterType::kUint32,
        .required = true,
        .description = "Generation-bearing hostile target fleet identifier.",
        .minimum = 0,
        .maximum = UINT32_MAX,
    },
}};

const ActionDescriptor kDescriptor = {
    .action_type = "attack_fleet",
    .action_version = 1,
    .application_id = "fleet_operations",
    .description = "Order one owned fleet to attack a hostile fleet.",
    .risk_class = "destructive_state_change",
    .verification_state = "live_verified",
    .parameters = kParameters,
    .parse = &ParseAttackFleet,
    .supported = &AttackFleetSupported,
};
IAG_NATIVE_ACTION_REGISTRATION const ActionRegistration kRegistration{
    kDescriptor};

}  // namespace

const ActionDescriptor& AttackFleetDescriptor() noexcept { return kDescriptor; }

}  // namespace iag::native_runtime
