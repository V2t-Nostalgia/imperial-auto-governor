#include <cstdint>
#include <iostream>
#include <span>
#include <string>
#include <vector>

#include "iag_native_runtime/action.h"
#include "iag_native_runtime/version_profile.h"

namespace {

bool Expect(bool condition, const char* message) {
  if (!condition) {
    std::cerr << message << '\n';
  }
  return condition;
}

}  // namespace

int main() {
  using iag::native_runtime::FindActionDescriptor;
  using iag::native_runtime::ParseUint32;
  using iag::native_runtime::Stellaris446Profile;
  using iag::native_runtime::ValidateActionDescriptors;
  using iag::native_runtime::BuildToolManifestJson;

  bool ok = true;
  std::string registry_error;
  ok &= Expect(
      ValidateActionDescriptors(registry_error),
      "self-registered action catalog is invalid");

  const auto* move = FindActionDescriptor("move_fleet");
  const auto* attack = FindActionDescriptor("attack_fleet");
  const auto* stop = FindActionDescriptor("stop_research");
  ok &= Expect(move != nullptr, "move_fleet descriptor missing");
  ok &= Expect(attack != nullptr, "attack_fleet descriptor missing");
  ok &= Expect(stop != nullptr, "stop_research descriptor missing");

  ok &= Expect(ParseUint32("0") == std::uint32_t{0}, "zero id rejected");
  ok &= Expect(
      ParseUint32("4294967295") == UINT32_MAX, "maximum id rejected");
  ok &= Expect(!ParseUint32("4294967296").has_value(), "overflow id accepted");
  ok &= Expect(!ParseUint32("-1").has_value(), "negative id accepted");

  if (move != nullptr) {
    std::string error;
    const std::vector<std::string> fields = {"888", "47"};
    auto invocation = move->parse(std::span<const std::string>(fields), error);
    ok &= Expect(invocation != nullptr, "valid move_fleet target rejected");
    if (invocation != nullptr) {
      ok &= Expect(
          invocation->TargetEcho() == "888:47", "move target echo changed");
    }
    const std::vector<std::string> invalid = {"888", "not-an-id"};
    invocation = move->parse(std::span<const std::string>(invalid), error);
    ok &= Expect(invocation == nullptr, "invalid move_fleet target accepted");
  }

  if (attack != nullptr) {
    std::string error;
    const std::vector<std::string> fields = {"888", "220"};
    auto invocation = attack->parse(std::span<const std::string>(fields), error);
    ok &= Expect(invocation != nullptr, "valid attack_fleet target rejected");
    if (invocation != nullptr) {
      ok &= Expect(
          invocation->TargetEcho() == "888:220",
          "attack target echo changed");
    }
    const std::vector<std::string> invalid = {"888", "not-an-id"};
    invocation = attack->parse(std::span<const std::string>(invalid), error);
    ok &= Expect(invocation == nullptr, "invalid attack_fleet target accepted");
  }

  if (stop != nullptr) {
    std::string error;
    const std::vector<std::string> fields = {"tech_shields_2"};
    auto invocation = stop->parse(std::span<const std::string>(fields), error);
    ok &= Expect(invocation != nullptr, "valid stop_research target rejected");
    if (invocation != nullptr) {
      ok &= Expect(
          invocation->TargetEcho() == "tech_shields_2",
          "research target echo changed");
    }
  }

  ok &= Expect(
      FindActionDescriptor("unknown_action") == nullptr,
      "unknown action descriptor accepted");
  const std::string manifest = BuildToolManifestJson(Stellaris446Profile());
  ok &= Expect(
      manifest.find("\"application_id\":\"fleet_operations\"") !=
          std::string::npos,
      "fleet Application assignment missing from manifest");
  ok &= Expect(
      manifest.find("\"name\":\"destination_system_id\"") !=
          std::string::npos,
      "move parameter schema missing from manifest");
#if !defined(_WIN32)
  ok &= Expect(
      manifest.find("\"application_id\":\"research_strategy\"") !=
          std::string::npos,
      "research Application assignment missing from manifest");
#else
  ok &= Expect(
      manifest.find("\"action_type\":\"stop_research\"") ==
          std::string::npos,
      "unsupported Windows research action leaked into manifest");
#endif
  return ok ? 0 : 1;
}
