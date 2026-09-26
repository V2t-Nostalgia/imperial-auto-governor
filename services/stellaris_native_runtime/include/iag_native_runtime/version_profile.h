#pragma once

#include <cstddef>
#include <cstdint>

namespace iag::native_runtime {

struct FunctionAnchor {
  const char* name;
  std::uintptr_t offset;
  const unsigned char* prefix;
  std::size_t prefix_size;
};

struct ObjectDatabaseProfile {
  std::uintptr_t database_instance;
  std::uintptr_t null_object_instance;
  std::size_t object_id_offset;
};

struct CoreBindings {
  std::uintptr_t game_idler_idle;
  std::uintptr_t post_command_to_session;
  std::uintptr_t current_game_state;
  std::uintptr_t get_local_observed;
  std::size_t country_id_offset;
  std::size_t hook_length;
  std::uint32_t pe_timestamp;
  std::size_t image_size;
  std::size_t text_rva;
  std::size_t text_size;
};

struct CStringBindings {
  std::size_t object_size;
  std::uintptr_t construct;
  std::uintptr_t destroy;
};

struct StopResearchBindings {
  std::size_t command_size;
  std::uintptr_t construct_command;
  std::uintptr_t command_is_valid;
  std::uintptr_t is_researching_technology;
  std::uintptr_t access_technology;
  std::uintptr_t technology_database_instance;
  std::uintptr_t null_technology_instance;
  std::size_t technology_status_offset;
};

struct MoveFleetBindings {
  std::size_t command_size;
  std::size_t celestial_coordinate_size;
  std::uintptr_t construct_command;
  std::uintptr_t command_is_valid;
  std::uintptr_t calculate_ftl_point_with;
  std::uintptr_t fleet_get_controller_ref;
  std::uintptr_t fleet_get_coordinate_origin;
  std::uintptr_t fleet_count_orders;
  std::uintptr_t fleet_has_player_movement_order;
  ObjectDatabaseProfile fleets;
  ObjectDatabaseProfile galactic_objects;
  // A zero clone address selects the constructor ABI used by the Linux
  // profile. The verified Windows profile builds the native value layout and
  // transfers a game-allocated clone instead.
  std::uintptr_t command_vtable;
  std::uintptr_t clone_command;
  std::uintptr_t celestial_coordinate_vtable;
  std::size_t fleet_coordinate_provider_offset;
  std::size_t fleet_executing_order_offset;
  std::size_t order_type_offset;
  std::uint32_t move_order_type;
};

struct AttackFleetBindings {
  std::size_t command_size;
  std::uintptr_t construct_command;
  std::uintptr_t command_is_valid;
  std::uintptr_t fleet_get_controller_ref;
  std::uintptr_t fleet_get_executing_order;
  std::uintptr_t follow_fleet_order_vptr;
  std::size_t order_target_fleet_offset;
  std::size_t order_attack_offset;
  std::size_t order_cancelled_offset;
  ObjectDatabaseProfile fleets;
};

struct BuildProfile {
  const char* platform_id;
  const char* game_version;
  const char* build_id;
  CoreBindings core;
  CStringBindings c_string;
  StopResearchBindings stop_research;
  MoveFleetBindings move_fleet;
  AttackFleetBindings attack_fleet;
  const FunctionAnchor* required_anchors;
  std::size_t required_anchor_count;
};

// The runtime currently supports exactly one verified executable. New versions
// add a profile and evidence; action handlers remain version-agnostic.
const BuildProfile& Stellaris446Profile();
#if defined(_WIN32)
const BuildProfile& Stellaris446WindowsProfile();
#endif

}  // namespace iag::native_runtime
