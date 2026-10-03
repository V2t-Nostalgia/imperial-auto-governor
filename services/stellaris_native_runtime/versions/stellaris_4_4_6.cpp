#include "iag_native_runtime/version_profile.h"

#include <array>

namespace iag::native_runtime {
namespace {

constexpr std::array<unsigned char, 17> kGameIdlerIdlePrefix = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54,
    0x53, 0x48, 0x81, 0xec, 0x88, 0x01, 0x00, 0x00,
};
constexpr std::array<unsigned char, 12> kPostCommandPrefix = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x53,
    0x50, 0x89, 0xf5, 0x49, 0x89, 0xff,
};
constexpr std::array<unsigned char, 12> kCancelCtorPrefix = {
    0xb8, 0xff, 0xff, 0xff, 0xff, 0x48,
    0x89, 0x47, 0x08, 0xc7, 0x47, 0x10,
};
constexpr std::array<unsigned char, 12> kIsResearchingPrefix = {
    0x48, 0x89, 0xf8, 0x8b, 0x4f, 0x5c,
    0x85, 0xc9, 0x7e, 0x16, 0x48, 0x8b,
};
constexpr std::array<unsigned char, 16> kMoveFleetCtorPrefix = {
    0xb8, 0xff, 0xff, 0xff, 0xff, 0x48, 0x89, 0x47,
    0x08, 0xc7, 0x47, 0x10, 0x00, 0x00, 0xff, 0xff,
};
constexpr std::array<unsigned char, 12> kMoveFleetIsValidPrefix = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x41,
    0x55, 0x41, 0x54, 0x53, 0x50, 0x49,
};
constexpr std::array<unsigned char, 12> kCalcFtlPointWithPrefix = {
    0x41, 0x57, 0x41, 0x56, 0x53, 0x48,
    0x89, 0xf3, 0x49, 0x89, 0xfe, 0x48,
};
constexpr std::array<unsigned char, 12> kAttackFleetCtorPrefix = {
    0x44, 0x8a, 0x54, 0x24, 0x08, 0xb8,
    0xff, 0xff, 0xff, 0xff, 0x48, 0x89,
};
constexpr std::array<unsigned char, 12> kAttackFleetIsValidPrefix = {
    0x41, 0x56, 0x53, 0x50, 0x48, 0x89,
    0xfb, 0xbf, 0x58, 0x00, 0x00, 0x00,
};
constexpr std::array<unsigned char, 12> kFleetGetExecutingOrderPrefix = {
    0x48, 0x8b, 0xbf, 0xc8, 0x02, 0x00,
    0x00, 0xe9, 0xf4, 0xcf, 0x06, 0x00,
};
constexpr std::array<unsigned char, 12> kCreateCommandFromBytesPrefix = {
    0x55, 0x41, 0x57, 0x41, 0x56, 0x53,
    0x48, 0x81, 0xec, 0xa8, 0x05, 0x00,
};

constexpr std::array<FunctionAnchor, 11> kRequiredAnchors = {{
    {"CGameIdler::Idle", 0x17bd4a0, kGameIdlerIdlePrefix.data(),
     kGameIdlerIdlePrefix.size()},
    {"PostCommandToSession", 0x17c2290, kPostCommandPrefix.data(),
     kPostCommandPrefix.size()},
    {"CCancelResearchCommand::CCancelResearchCommand", 0x1f34680,
     kCancelCtorPrefix.data(), kCancelCtorPrefix.size()},
    {"CTechnologyStatus::IsResearching", 0x1d3adb0,
     kIsResearchingPrefix.data(), kIsResearchingPrefix.size()},
    {"CFleetFlyToCoordinatesCommand::CFleetFlyToCoordinatesCommand", 0x1f64c30,
     kMoveFleetCtorPrefix.data(), kMoveFleetCtorPrefix.size()},
    {"CFleetFlyToCoordinatesCommand::IsValid", 0x1f64f50,
     kMoveFleetIsValidPrefix.data(), kMoveFleetIsValidPrefix.size()},
    {"CGalacticObject::CalcFTLPointWith", 0x229b7a0,
     kCalcFtlPointWithPrefix.data(), kCalcFtlPointWithPrefix.size()},
    {"CFollowFleetCommand::CFollowFleetCommand", 0x1f73000,
     kAttackFleetCtorPrefix.data(), kAttackFleetCtorPrefix.size()},
    {"CFollowFleetCommand::IsValid", 0x1f73140,
     kAttackFleetIsValidPrefix.data(), kAttackFleetIsValidPrefix.size()},
    {"CFleet::GetExecutingOrder", 0x25ae690,
     kFleetGetExecutingOrderPrefix.data(),
     kFleetGetExecutingOrderPrefix.size()},
    {"CreateCommand(bytes)", 0x381c8d0,
     kCreateCommandFromBytesPrefix.data(),
     kCreateCommandFromBytesPrefix.size()},
}};

const BuildProfile kProfile = {
    .platform_id = "linux-x86_64",
    .game_version = "Stellaris 4.4.6 (fdde)",
    .build_id = "c6969e60fd81d738948222a94c0b5a0841abbffc",
    .core = {
        .game_idler_idle = 0x17bd4a0,
        .post_command_to_session = 0x17c2290,
        .current_game_state = 0x5492098,
        .get_local_observed = 0x180be30,
        .country_id_offset = 0x20,
        .hook_length = 17,
        .pe_timestamp = 0,
        .image_size = 0,
        .text_rva = 0,
        .text_size = 0,
    },
    .c_string = {
        .object_size = 0x30,
        .construct = 0x3e4a740,
        .destroy = 0x16584b0,
    },
    .stop_research = {
        .command_size = 0x28,
        .construct_command = 0x1f34680,
        .command_is_valid = 0x1f34790,
        .is_researching_technology = 0x1d3adb0,
        .access_technology = 0x1d284b0,
        .technology_database_instance = 0x548d890,
        .null_technology_instance = 0x548b828,
        .technology_status_offset = 0x1830,
    },
    .move_fleet = {
        .command_size = 0x50,
        .celestial_coordinate_size = 0x28,
        .construct_command = 0x1f64c30,
        .command_is_valid = 0x1f64f50,
        .calculate_ftl_point_with = 0x229b7a0,
        .fleet_get_controller_ref = 0x2595480,
        .fleet_get_coordinate_origin = 0x25bd960,
        .fleet_count_orders = 0x25ae480,
        .fleet_has_player_movement_order = 0x25ae8f0,
        .fleets = {
            .database_instance = 0x548bc10,
            .null_object_instance = 0x548bc18,
            .object_id_offset = 0x30,
        },
        .galactic_objects = {
            .database_instance = 0x548aec0,
            .null_object_instance = 0x548aec8,
            .object_id_offset = 0x08,
        },
        .command_vtable = 0,
        .clone_command = 0,
        .celestial_coordinate_vtable = 0,
        .fleet_coordinate_provider_offset = 0,
        .fleet_executing_order_offset = 0,
        .order_type_offset = 0,
        .move_order_type = 0,
    },
    .attack_fleet = {
        .command_size = 0x28,
        .construct_command = 0x1f73000,
        .command_is_valid = 0x1f73140,
        .fleet_get_controller_ref = 0x2595480,
        .fleet_get_executing_order = 0x25ae690,
        .follow_fleet_order_vptr = 0x43cfc00,
        .order_target_fleet_offset = 0x24,
        .order_attack_offset = 0x50,
        .order_cancelled_offset = 0x51,
        .fleets = {
            .database_instance = 0x548bc10,
            .null_object_instance = 0x548bc18,
            .object_id_offset = 0x30,
        },
    },
    .serialized_command = {
        .create_command_from_bytes = 0x381c8d0,
        .command_actor_offset = 0x08,
        .command_is_valid_vtable_offset = 0x40,
        .command_deleting_destructor_vtable_offset = 0x08,
    },
    .required_anchors = kRequiredAnchors.data(),
    .required_anchor_count = kRequiredAnchors.size(),
};

}  // namespace

const BuildProfile& Stellaris446Profile() { return kProfile; }

}  // namespace iag::native_runtime
