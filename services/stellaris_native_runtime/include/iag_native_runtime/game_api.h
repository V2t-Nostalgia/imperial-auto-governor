#pragma once

#include <cstdint>

#include "iag_native_runtime/version_profile.h"

namespace iag::native_runtime {

class GameApi {
 public:
  GameApi(std::uintptr_t module_base, const BuildProfile& profile);

  [[nodiscard]] const BuildProfile& profile() const noexcept;
  [[nodiscard]] std::uintptr_t Address(std::uintptr_t offset) const noexcept;
  [[nodiscard]] bool IsModulePointer(const void* value) const noexcept;
  [[nodiscard]] bool IsTextPointer(const void* value) const noexcept;

  template <typename Function>
  [[nodiscard]] Function FunctionAt(std::uintptr_t offset) const noexcept {
    return reinterpret_cast<Function>(Address(offset));
  }

  [[nodiscard]] bool VerifyRequiredAnchors() const noexcept;
  [[nodiscard]] const void* LocalObservedCountry() const noexcept;
  [[nodiscard]] std::uint32_t CountryId(const void* country) const noexcept;
  [[nodiscard]] const void* ResolvePdxObject(
      const ObjectDatabaseProfile& database,
      std::uint32_t object_id) const noexcept;
  void PostCommand(void* command) const noexcept;

 private:
  std::uintptr_t module_base_;
  const BuildProfile& profile_;
};

}  // namespace iag::native_runtime
