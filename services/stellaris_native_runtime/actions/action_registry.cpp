#include "iag_native_runtime/action.h"

#include <algorithm>
#include <charconv>
#include <mutex>
#include <sstream>
#include <utility>

#include "iag_native_runtime/version_profile.h"

namespace iag::native_runtime {
namespace {

std::vector<const ActionDescriptor*>& MutableDescriptors() {
  static std::vector<const ActionDescriptor*> descriptors;
  return descriptors;
}

std::mutex& DescriptorMutex() {
  static std::mutex mutex;
  return mutex;
}

std::string JsonEscape(std::string_view value) {
  std::string escaped;
  escaped.reserve(value.size() + 8U);
  for (const unsigned char character : value) {
    switch (character) {
      case '\"':
        escaped += "\\\"";
        break;
      case '\\':
        escaped += "\\\\";
        break;
      case '\b':
        escaped += "\\b";
        break;
      case '\f':
        escaped += "\\f";
        break;
      case '\n':
        escaped += "\\n";
        break;
      case '\r':
        escaped += "\\r";
        break;
      case '\t':
        escaped += "\\t";
        break;
      default:
        if (character < 0x20U) {
          static constexpr char kHex[] = "0123456789abcdef";
          escaped += "\\u00";
          escaped.push_back(kHex[character >> 4U]);
          escaped.push_back(kHex[character & 0x0fU]);
        } else {
          escaped.push_back(static_cast<char>(character));
        }
    }
  }
  return escaped;
}

std::string_view ParameterTypeName(ActionParameterType type) {
  switch (type) {
    case ActionParameterType::kUint32:
      return "uint32";
    case ActionParameterType::kString:
      return "string";
    case ActionParameterType::kBoolean:
      return "boolean";
  }
  return "unknown";
}

bool DescriptorLess(
    const ActionDescriptor* left,
    const ActionDescriptor* right) {
  if (left->action_type != right->action_type) {
    return left->action_type < right->action_type;
  }
  return left->action_version < right->action_version;
}

}  // namespace

ActionRegistration::ActionRegistration(
    const ActionDescriptor& descriptor) noexcept {
  std::lock_guard lock(DescriptorMutex());
  MutableDescriptors().push_back(&descriptor);
}

PrepareResult PrepareResult::Ready(void* command) {
  return {PreparationStatus::kReady, command, {}};
}

PrepareResult PrepareResult::Rejected(std::string detail) {
  return {PreparationStatus::kRejected, nullptr, std::move(detail)};
}

PrepareResult PrepareResult::Failed(std::string detail) {
  return {PreparationStatus::kFailed, nullptr, std::move(detail)};
}

bool IsSemanticToken(std::string_view value, std::size_t maximum) noexcept {
  if (value.empty() || value.size() > maximum) {
    return false;
  }
  return std::all_of(value.begin(), value.end(), [](unsigned char character) {
    return (character >= 'a' && character <= 'z') ||
           (character >= 'A' && character <= 'Z') ||
           (character >= '0' && character <= '9') || character == '_' ||
           character == '-' || character == '.' || character == ':';
  });
}

std::optional<std::uint32_t> ParseUint32(std::string_view value) noexcept {
  if (value.empty() || value.size() > 10U ||
      !std::all_of(value.begin(), value.end(), [](unsigned char character) {
        return character >= '0' && character <= '9';
      })) {
    return std::nullopt;
  }
  std::uint32_t parsed = 0;
  const auto result = std::from_chars(
      value.data(), value.data() + value.size(), parsed, 10);
  if (result.ec != std::errc{} || result.ptr != value.data() + value.size()) {
    return std::nullopt;
  }
  return parsed;
}

const ActionDescriptor* FindActionDescriptor(
    std::string_view action_type) noexcept {
  std::lock_guard lock(DescriptorMutex());
  const auto& descriptors = MutableDescriptors();
  const auto found = std::find_if(
      descriptors.begin(), descriptors.end(), [action_type](const auto* item) {
        return item != nullptr && item->action_type == action_type;
      });
  return found == descriptors.end() ? nullptr : *found;
}

std::vector<const ActionDescriptor*> RegisteredActionDescriptors() {
  std::lock_guard lock(DescriptorMutex());
  auto descriptors = MutableDescriptors();
  std::sort(descriptors.begin(), descriptors.end(), DescriptorLess);
  return descriptors;
}

bool ValidateActionDescriptors(std::string& error_detail) {
  const auto descriptors = RegisteredActionDescriptors();
  if (descriptors.empty()) {
    error_detail = "no_registered_actions";
    return false;
  }
  for (std::size_t index = 0; index < descriptors.size(); ++index) {
    const auto* descriptor = descriptors[index];
    if (descriptor == nullptr || descriptor->action_version == 0U ||
        descriptor->parse == nullptr || descriptor->supported == nullptr ||
        !IsSemanticToken(descriptor->action_type, 64U) ||
        !IsSemanticToken(descriptor->application_id, 64U) ||
        !IsSemanticToken(descriptor->risk_class, 64U) ||
        !IsSemanticToken(descriptor->verification_state, 64U)) {
      error_detail = "invalid_action_descriptor";
      return false;
    }
    if (index != 0U &&
        descriptors[index - 1U]->action_type == descriptor->action_type &&
        descriptors[index - 1U]->action_version == descriptor->action_version) {
      error_detail = "duplicate_action_descriptor";
      return false;
    }
    for (const auto& parameter : descriptor->parameters) {
      if (!IsSemanticToken(parameter.name, 64U) ||
          parameter.description.empty()) {
        error_detail = "invalid_action_parameter_descriptor";
        return false;
      }
    }
  }
  error_detail.clear();
  return true;
}

std::string BuildToolManifestJson(const BuildProfile& profile) {
  std::ostringstream output;
  output << "{\"schema_version\":\"iag.native_tool_manifest.v1\","
         << "\"platform\":\"" << JsonEscape(profile.platform_id) << "\","
         << "\"game_version\":\"" << JsonEscape(profile.game_version) << "\","
         << "\"build_id\":\"" << JsonEscape(profile.build_id) << "\","
         << "\"tools\":[";
  bool first_tool = true;
  for (const auto* descriptor : RegisteredActionDescriptors()) {
    if (descriptor == nullptr || !descriptor->supported(profile)) {
      continue;
    }
    if (!first_tool) {
      output << ',';
    }
    first_tool = false;
    output << "{\"action_type\":\"" << JsonEscape(descriptor->action_type)
           << "\",\"action_version\":" << descriptor->action_version
           << ",\"application_id\":\""
           << JsonEscape(
                  descriptor->application_id.empty()
                      ? std::string_view{"etc"}
                      : descriptor->application_id)
           << "\",\"description\":\""
           << JsonEscape(descriptor->description)
           << "\",\"risk_class\":\""
           << JsonEscape(descriptor->risk_class)
           << "\",\"verification_state\":\""
           << JsonEscape(descriptor->verification_state)
           << "\",\"parameters\":[";
    bool first_parameter = true;
    for (const auto& parameter : descriptor->parameters) {
      if (!first_parameter) {
        output << ',';
      }
      first_parameter = false;
      output << "{\"name\":\"" << JsonEscape(parameter.name)
             << "\",\"type\":\"" << ParameterTypeName(parameter.type)
             << "\",\"required\":"
             << (parameter.required ? "true" : "false")
             << ",\"description\":\""
             << JsonEscape(parameter.description) << '\"';
      if (parameter.type == ActionParameterType::kUint32) {
        output << ",\"minimum\":" << parameter.minimum
               << ",\"maximum\":" << parameter.maximum;
      } else if (parameter.type == ActionParameterType::kString) {
        output << ",\"maximum_length\":" << parameter.maximum_length;
      }
      output << '}';
    }
    output << "]}";
  }
  output << "]}";
  return output.str();
}

}  // namespace iag::native_runtime
