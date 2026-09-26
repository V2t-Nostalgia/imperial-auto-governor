#include "iag_native_runtime/game_api.h"

#include <cstring>

namespace iag::native_runtime {
namespace {

using PostCommandFn = void (*)(void*, bool);
using GetLocalObservedFn = const void* (*)(const void*);

}  // namespace

GameApi::GameApi(std::uintptr_t module_base, const BuildProfile& profile)
    : module_base_(module_base), profile_(profile) {}

const BuildProfile& GameApi::profile() const noexcept { return profile_; }

std::uintptr_t GameApi::Address(std::uintptr_t offset) const noexcept {
  return module_base_ + offset;
}

bool GameApi::IsModulePointer(const void* value) const noexcept {
  if (profile_.core.image_size == 0U) {
    return value != nullptr;
  }
  const auto address = reinterpret_cast<std::uintptr_t>(value);
  return address >= module_base_ &&
         address < module_base_ + profile_.core.image_size;
}

bool GameApi::IsTextPointer(const void* value) const noexcept {
  if (profile_.core.text_size == 0U) {
    return value != nullptr;
  }
  const auto address = reinterpret_cast<std::uintptr_t>(value);
  return address >= module_base_ + profile_.core.text_rva &&
         address < module_base_ + profile_.core.text_rva +
                       profile_.core.text_size;
}

bool GameApi::VerifyRequiredAnchors() const noexcept {
  for (std::size_t index = 0; index < profile_.required_anchor_count; ++index) {
    const auto& anchor = profile_.required_anchors[index];
    if (std::memcmp(
            reinterpret_cast<const void*>(Address(anchor.offset)),
            anchor.prefix,
            anchor.prefix_size) != 0) {
      return false;
    }
  }
  return true;
}

const void* GameApi::LocalObservedCountry() const noexcept {
  if (profile_.core.current_game_state == 0U ||
      profile_.core.get_local_observed == 0U) {
    return nullptr;
  }
  const void* game_state = *reinterpret_cast<void* const*>(
      Address(profile_.core.current_game_state));
  if (game_state == nullptr) {
    return nullptr;
  }
  return FunctionAt<GetLocalObservedFn>(profile_.core.get_local_observed)(
      game_state);
}

std::uint32_t GameApi::CountryId(const void* country) const noexcept {
  if (country == nullptr) {
    return UINT32_MAX;
  }
  return *reinterpret_cast<const std::uint32_t*>(
      static_cast<const unsigned char*>(country) +
      profile_.core.country_id_offset);
}

const void* GameApi::ResolvePdxObject(
    const ObjectDatabaseProfile& database_profile,
    std::uint32_t object_id) const noexcept {
  const void* null_object = *reinterpret_cast<void* const*>(
      Address(database_profile.null_object_instance));
  const auto* database = reinterpret_cast<const unsigned char*>(
      *reinterpret_cast<void* const*>(Address(database_profile.database_instance)));
  if (database == nullptr) {
    return nullptr;
  }
  const std::uint32_t index = object_id & 0x00ffffffU;
  const std::uint32_t count =
      *reinterpret_cast<const std::uint32_t*>(database + 0x20);
  if (index >= count) {
    return nullptr;
  }
  const auto* entries = *reinterpret_cast<unsigned char* const*>(database + 0x18);
  if (entries == nullptr) {
    return nullptr;
  }
  const void* object =
      *reinterpret_cast<void* const*>(entries + index * 0x10U + 8U);
  if (object == nullptr || object == null_object) {
    return nullptr;
  }
  const std::uint32_t resolved_id = *reinterpret_cast<const std::uint32_t*>(
      static_cast<const unsigned char*>(object) +
      database_profile.object_id_offset);
  return resolved_id == object_id ? object : nullptr;
}

void GameApi::PostCommand(void* command) const noexcept {
  FunctionAt<PostCommandFn>(profile_.core.post_command_to_session)(command, false);
}

}  // namespace iag::native_runtime
