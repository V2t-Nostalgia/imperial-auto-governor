#include "move_fleet.h"

#include <array>
#include <cstddef>
#include <cstring>
#include <new>
#include <utility>
#include <vector>

#include "iag_native_runtime/game_api.h"

namespace iag::native_runtime {
namespace {

using MoveFleetCtorFn = void (*)(
    void*, std::uint32_t, const void*, bool, bool);
using MoveFleetIsValidFn = bool (*)(const void*, void*);
using CalcFtlPointWithFn = void (*)(void*, const void*, const void*);
using FleetGetControllerRefFn = std::uint32_t (*)(const void*);
using FleetGetCoordinateOriginFn = const void* (*)(const void*);
using FleetCountOrdersFn = int (*)(const void*);
using FleetHasPlayerIssuedMovementOrderFn = bool (*)(const void*);
using CoordinateGetterFn = const void* (*)(const void*);
using WindowsCalcFtlPointWithFn = void* (*)(const void*, void*, const void*);
using MoveFleetCloneFn = void* (*)(const void*);

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

class MoveFleetInvocation final : public ActionInvocation {
 public:
  MoveFleetInvocation(
      std::uint32_t fleet_id,
      std::uint32_t destination_system_id)
      : fleet_id_(fleet_id), destination_system_id_(destination_system_id) {}

  [[nodiscard]] std::string_view action_type() const noexcept override {
    return "move_fleet";
  }

  [[nodiscard]] std::string TargetEcho() const override {
    return std::to_string(fleet_id_) + ":" +
           std::to_string(destination_system_id_);
  }

  [[nodiscard]] PrepareResult Prepare(
      const GameApi& game,
      std::uint32_t country_id,
      const void*) override {
    const auto& bindings = game.profile().move_fleet;
    const void* fleet = game.ResolvePdxObject(bindings.fleets, fleet_id_);
    if (fleet == nullptr) {
      return PrepareResult::Rejected("unknown_fleet_id");
    }
    if (!FleetOwnedByCountry(game, fleet, country_id)) {
      return PrepareResult::Rejected("fleet_authority_mismatch");
    }
    const void* destination = game.ResolvePdxObject(
        bindings.galactic_objects, destination_system_id_);
    if (destination == nullptr) {
      return PrepareResult::Rejected("unknown_destination_system_id");
    }

    std::vector<std::byte> coordinate(bindings.celestial_coordinate_size);
    if (!BuildMoveCoordinate(game, fleet, destination, coordinate.data())) {
      return PrepareResult::Rejected("invalid_or_current_destination");
    }
    if (!CommandIsValid(game, coordinate.data())) {
      return PrepareResult::Rejected("native_is_valid_false_before_submit");
    }

    initial_order_count_ = FleetOrderCount(game, fleet);
    initially_had_player_movement_order_ =
        FleetHasPlayerMovementOrder(game, fleet);
    initial_order_type_ = FleetExecutingOrderType(game, fleet);
    if (bindings.clone_command != 0U) {
      std::vector<std::byte> command(bindings.command_size);
      PopulateCommandValue(game, coordinate.data(), command.data());
      void* owned = game.FunctionAt<MoveFleetCloneFn>(bindings.clone_command)(
          command.data());
      if (owned == nullptr) {
        return PrepareResult::Failed("native_command_clone_failed");
      }
      return PrepareResult::Ready(owned);
    }
    void* command = ::operator new(bindings.command_size, std::nothrow);
    if (command == nullptr) {
      return PrepareResult::Failed("command_allocation_failed");
    }
    game.FunctionAt<MoveFleetCtorFn>(bindings.construct_command)(
        command, fleet_id_, coordinate.data(), false, false);
    return PrepareResult::Ready(command);
  }

  [[nodiscard]] std::optional<RuntimeResult> Verify(
      const GameApi& game,
      std::uint32_t country_id,
      const void*) override {
    const void* fleet = game.ResolvePdxObject(
        game.profile().move_fleet.fleets, fleet_id_);
    if (fleet == nullptr || !FleetOwnedByCountry(game, fleet, country_id)) {
      return std::nullopt;
    }
    const int order_count = FleetOrderCount(game, fleet);
    const bool has_player_movement_order =
        FleetHasPlayerMovementOrder(game, fleet);
    const std::uint32_t order_type = FleetExecutingOrderType(game, fleet);
    if (game.profile().move_fleet.clone_command != 0U &&
        initial_order_type_ != game.profile().move_fleet.move_order_type &&
        order_type == game.profile().move_fleet.move_order_type) {
      return RuntimeResult{
          "confirmed", "native_postcondition_move_order_type_present"};
    }
    if (order_count > initial_order_count_ ||
        (has_player_movement_order &&
         !initially_had_player_movement_order_)) {
      return RuntimeResult{
          "confirmed", "native_postcondition_player_movement_order_present"};
    }
    return std::nullopt;
  }

  [[nodiscard]] std::string_view TimeoutDetail() const noexcept override {
    return "native_submit_timeout_waiting_for_movement_order";
  }

 private:
  [[nodiscard]] static bool FleetOwnedByCountry(
      const GameApi& game,
      const void* fleet,
      std::uint32_t country_id) {
    if (game.profile().move_fleet.fleet_get_controller_ref == 0U) {
      // The verified Windows build performs the ownership check in native
      // IsValid. Its direct controller getter is not promoted yet.
      return true;
    }
    return game.FunctionAt<FleetGetControllerRefFn>(
               game.profile().move_fleet.fleet_get_controller_ref)(fleet) ==
           country_id;
  }

  [[nodiscard]] static int FleetOrderCount(
      const GameApi& game,
      const void* fleet) {
    if (game.profile().move_fleet.fleet_count_orders == 0U) {
      return 0;
    }
    return game.FunctionAt<FleetCountOrdersFn>(
        game.profile().move_fleet.fleet_count_orders)(fleet);
  }

  [[nodiscard]] static bool FleetHasPlayerMovementOrder(
      const GameApi& game,
      const void* fleet) {
    if (game.profile().move_fleet.fleet_has_player_movement_order == 0U) {
      return false;
    }
    return game.FunctionAt<FleetHasPlayerIssuedMovementOrderFn>(
        game.profile().move_fleet.fleet_has_player_movement_order)(fleet);
  }

  [[nodiscard]] bool BuildMoveCoordinate(
      const GameApi& game,
      const void* fleet,
      const void* destination,
      void* coordinate) const {
    const auto& bindings = game.profile().move_fleet;
    if (bindings.clone_command != 0U) {
      const auto* provider = static_cast<const std::byte*>(fleet) +
                             bindings.fleet_coordinate_provider_offset;
      const auto* vtable = *reinterpret_cast<void* const* const*>(provider);
      if (!game.IsModulePointer(vtable) || !game.IsTextPointer(vtable[1])) {
        return false;
      }
      const void* source_coordinate = reinterpret_cast<CoordinateGetterFn>(
          const_cast<void*>(vtable[1]))(provider);
      if (source_coordinate == nullptr ||
          ReadField<std::uintptr_t>(source_coordinate, 0) !=
              game.Address(bindings.celestial_coordinate_vtable)) {
        return false;
      }
      game.FunctionAt<WindowsCalcFtlPointWithFn>(
          bindings.calculate_ftl_point_with)(
          destination, coordinate, source_coordinate);
      return ReadField<std::uintptr_t>(coordinate, 0) ==
                 game.Address(bindings.celestial_coordinate_vtable) &&
             ReadField<std::uint32_t>(coordinate, 0x20) ==
                 destination_system_id_;
    }
    const void* source_system = game.FunctionAt<FleetGetCoordinateOriginFn>(
        bindings.fleet_get_coordinate_origin)(fleet);
    const void* null_system = *reinterpret_cast<void* const*>(
        game.Address(bindings.galactic_objects.null_object_instance));
    if (source_system == nullptr || source_system == null_system ||
        source_system == destination) {
      return false;
    }
    game.FunctionAt<CalcFtlPointWithFn>(bindings.calculate_ftl_point_with)(
        coordinate, destination, source_system);
    return true;
  }

  [[nodiscard]] bool CommandIsValid(
      const GameApi& game,
      const void* coordinate) const {
    const auto& bindings = game.profile().move_fleet;
    std::vector<std::byte> command(bindings.command_size);
    PopulateCommandValue(game, coordinate, command.data());
    return game.FunctionAt<MoveFleetIsValidFn>(bindings.command_is_valid)(
        command.data(), nullptr);
  }

  void PopulateCommandValue(
      const GameApi& game,
      const void* coordinate,
      void* command) const {
    const auto& bindings = game.profile().move_fleet;
    if (bindings.clone_command == 0U) {
      game.FunctionAt<MoveFleetCtorFn>(bindings.construct_command)(
          command, fleet_id_, coordinate, false, false);
      return;
    }
    std::memset(command, 0, bindings.command_size);
    WriteField(
        command, 0x00, game.Address(bindings.command_vtable));
    WriteField(command, 0x08, UINT32_MAX);
    WriteField(command, 0x0c, std::uint32_t{0});
    WriteField(command, 0x10, std::uint32_t{0xffff0000U});
    WriteField(command, 0x14, std::uint16_t{0});
    WriteField(command, 0x16, std::uint8_t{0});
    WriteField(command, 0x18, std::uint32_t{0});
    WriteField(command, 0x20, fleet_id_);
    std::memcpy(
        static_cast<std::byte*>(command) + 0x28,
        coordinate,
        bindings.celestial_coordinate_size);
    WriteField(command, 0x50, std::uint8_t{0});
    WriteField(command, 0x51, std::uint8_t{0});
  }

  [[nodiscard]] static std::uint32_t FleetExecutingOrderType(
      const GameApi& game,
      const void* fleet) {
    const auto& bindings = game.profile().move_fleet;
    if (bindings.fleet_executing_order_offset == 0U) {
      return 0;
    }
    const void* order = ReadField<const void*>(
        fleet, bindings.fleet_executing_order_offset);
    if (order == nullptr ||
        !game.IsModulePointer(ReadField<const void*>(order, 0))) {
      return 0;
    }
    return ReadField<std::uint32_t>(order, bindings.order_type_offset);
  }

  std::uint32_t fleet_id_;
  std::uint32_t destination_system_id_;
  int initial_order_count_ = 0;
  bool initially_had_player_movement_order_ = false;
  std::uint32_t initial_order_type_ = 0;
};

std::unique_ptr<ActionInvocation> ParseMoveFleet(
    std::span<const std::string> fields,
    std::string& error_detail) {
  if (fields.size() != 2U) {
    error_detail = "invalid_semantic_target";
    return nullptr;
  }
  const auto fleet_id = ParseUint32(fields[0]);
  const auto destination_system_id = ParseUint32(fields[1]);
  if (!fleet_id.has_value() || !destination_system_id.has_value()) {
    error_detail = "invalid_semantic_target";
    return nullptr;
  }
  return std::make_unique<MoveFleetInvocation>(
      *fleet_id, *destination_system_id);
}

bool MoveFleetSupported(const BuildProfile& profile) noexcept {
  return profile.move_fleet.command_size != 0U &&
         profile.move_fleet.command_is_valid != 0U &&
         (profile.move_fleet.construct_command != 0U ||
          profile.move_fleet.clone_command != 0U);
}

constexpr std::array<ActionParameterDescriptor, 2> kParameters = {{
    {
        .name = "fleet_id",
        .type = ActionParameterType::kUint32,
        .required = true,
        .description = "Generation-bearing fleet identifier from the current snapshot.",
        .minimum = 0,
        .maximum = UINT32_MAX,
    },
    {
        .name = "destination_system_id",
        .type = ActionParameterType::kUint32,
        .required = true,
        .description = "Destination star-system identifier from the current snapshot.",
        .minimum = 0,
        .maximum = UINT32_MAX,
    },
}};

const ActionDescriptor kDescriptor = {
    .action_type = "move_fleet",
    .action_version = 1,
    .application_id = "fleet_operations",
    .description = "Order one owned fleet to move to a reachable star system.",
    .risk_class = "state_change",
    .verification_state = "live_verified",
    .parameters = kParameters,
    .parse = &ParseMoveFleet,
    .supported = &MoveFleetSupported,
};
IAG_NATIVE_ACTION_REGISTRATION const ActionRegistration kRegistration{
    kDescriptor};

}  // namespace

const ActionDescriptor& MoveFleetDescriptor() noexcept { return kDescriptor; }

}  // namespace iag::native_runtime
