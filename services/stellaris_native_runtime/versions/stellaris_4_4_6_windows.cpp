#include "iag_native_runtime/version_profile.h"

#if defined(_WIN32)

#include <array>

namespace iag::native_runtime {
namespace {

constexpr std::array<unsigned char, 14> kGameIdlerIdlePrefix = {
    0x48, 0x8b, 0xc4, 0x88, 0x50, 0x10, 0x55,
    0x53, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55,
};
constexpr std::array<unsigned char, 16> kPostCommandPrefix = {
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x6c,
    0x24, 0x10, 0x48, 0x89, 0x74, 0x24, 0x18, 0x57,
};
constexpr std::array<unsigned char, 12> kMoveIsValidPrefix = {
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48,
    0x89, 0x74, 0x24, 0x10, 0x57, 0x48,
};
constexpr std::array<unsigned char, 12> kCalcFtlPrefix = {
    0x48, 0x89, 0x5c, 0x24, 0x08, 0x48,
    0x89, 0x6c, 0x24, 0x10, 0x48, 0x89,
};
constexpr std::array<unsigned char, 6> kMoveTokenPrefix = {
    0xb8, 0x4f, 0x2c, 0x00, 0x00, 0xc3,
};

constexpr std::array<FunctionAnchor, 5> kRequiredAnchors = {{
    {"CGameIdler::Idle", 0x00337530, kGameIdlerIdlePrefix.data(),
     kGameIdlerIdlePrefix.size()},
    {"PostCommandToSession", 0x00648970, kPostCommandPrefix.data(),
     kPostCommandPrefix.size()},
    {"CFleetFlyToCoordinatesCommand::IsValid", 0x00ac15b0,
     kMoveIsValidPrefix.data(), kMoveIsValidPrefix.size()},
    {"CGalacticObject::CalcFTLPointWith", 0x008c3b10,
     kCalcFtlPrefix.data(), kCalcFtlPrefix.size()},
    {"CFleetFlyToCoordinatesCommand::GetToken", 0x0097c2f0,
     kMoveTokenPrefix.data(), kMoveTokenPrefix.size()},
}};

const BuildProfile kProfile = {
    .platform_id = "windows-x64",
    .game_version = "Stellaris 4.4.6 (fdde), Steam build 24109497",
    .build_id = "stellaris-4.4.6-windows-steam-24109497-bc451c72",
    .core = {
        .game_idler_idle = 0x00337530,
        .post_command_to_session = 0x00648970,
        .current_game_state = 0,
        .get_local_observed = 0,
        .country_id_offset = 0,
        .hook_length = 14,
        .pe_timestamp = 0x6a4e461dU,
        .image_size = 0x03950000U,
        .text_rva = 0x1000,
        .text_size = 0x03200000,
    },
    .c_string = {},
    .stop_research = {},
    .move_fleet = {
        .command_size = 0x58,
        .celestial_coordinate_size = 0x28,
        .construct_command = 0,
        .command_is_valid = 0x00ac15b0,
        .calculate_ftl_point_with = 0x008c3b10,
        .fleet_get_controller_ref = 0,
        .fleet_get_coordinate_origin = 0,
        .fleet_count_orders = 0,
        .fleet_has_player_movement_order = 0,
        .fleets = {
            .database_instance = 0x03285688,
            .null_object_instance = 0x03286190,
            .object_id_offset = 0x30,
        },
        .galactic_objects = {
            .database_instance = 0x03287348,
            .null_object_instance = 0x03283fc8,
            .object_id_offset = 0x08,
        },
        .command_vtable = 0x02544180,
        .clone_command = 0x0097c220,
        .celestial_coordinate_vtable = 0x024d6c10,
        .fleet_coordinate_provider_offset = 0x38,
        .fleet_executing_order_offset = 0x2c8,
        .order_type_offset = 0x18,
        .move_order_type = 0x2cde,
    },
    .attack_fleet = {},
    .serialized_command = {},
    .required_anchors = kRequiredAnchors.data(),
    .required_anchor_count = kRequiredAnchors.size(),
};

}  // namespace

const BuildProfile& Stellaris446WindowsProfile() { return kProfile; }
const BuildProfile& Stellaris446Profile() { return kProfile; }

}  // namespace iag::native_runtime

#endif
