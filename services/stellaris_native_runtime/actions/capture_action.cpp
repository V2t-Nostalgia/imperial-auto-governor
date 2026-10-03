#include "capture_action.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <optional>
#include <sstream>
#include <type_traits>
#include <utility>
#include <vector>

#include "iag_native_runtime/game_api.h"
#include "iag_native_runtime/version_profile.h"

namespace iag::native_runtime {
namespace {

bool IsPrintableAscii(std::string_view value) {
  return !value.empty() &&
         std::all_of(value.begin(), value.end(), [](unsigned char character) {
           return character >= 0x20U && character <= 0x7eU &&
                  character != '\t';
         });
}

std::optional<std::int64_t> ParseInt64(std::string_view value) {
  if (value.empty() || value.size() > 20U) {
    return std::nullopt;
  }
  std::int64_t parsed = 0;
  const auto result = std::from_chars(
      value.data(), value.data() + value.size(), parsed, 10);
  if (result.ec != std::errc{} || result.ptr != value.data() + value.size()) {
    return std::nullopt;
  }
  return parsed;
}

std::optional<unsigned char> HexNibble(char value) {
  if (value >= '0' && value <= '9') {
    return static_cast<unsigned char>(value - '0');
  }
  if (value >= 'a' && value <= 'f') {
    return static_cast<unsigned char>(value - 'a' + 10);
  }
  if (value >= 'A' && value <= 'F') {
    return static_cast<unsigned char>(value - 'A' + 10);
  }
  return std::nullopt;
}

std::optional<std::vector<unsigned char>> DecodeHex(std::string_view value) {
  if (value.empty() || (value.size() & 1U) != 0U) {
    return std::nullopt;
  }
  std::vector<unsigned char> decoded;
  decoded.reserve(value.size() / 2U);
  for (std::size_t index = 0; index < value.size(); index += 2U) {
    const auto high = HexNibble(value[index]);
    const auto low = HexNibble(value[index + 1U]);
    if (!high.has_value() || !low.has_value()) {
      return std::nullopt;
    }
    decoded.push_back(static_cast<unsigned char>((*high << 4U) | *low));
  }
  return decoded;
}

std::string TargetDigest(
    std::string_view action_type,
    std::span<const std::string> values) {
  std::uint64_t digest = 1469598103934665603ULL;
  const auto mix = [&digest](unsigned char value) {
    digest ^= value;
    digest *= 1099511628211ULL;
  };
  for (const unsigned char value : action_type) {
    mix(value);
  }
  mix(0U);
  for (const auto& value : values) {
    for (const unsigned char character : value) {
      mix(character);
    }
    mix(0U);
  }
  std::ostringstream output;
  output << action_type << ':' << std::hex << std::setw(16)
         << std::setfill('0') << digest;
  return output.str();
}

template <typename Integer>
void WriteLittleEndian(
    std::vector<unsigned char>& output,
    std::size_t offset,
    Integer value) {
  using Unsigned = std::make_unsigned_t<Integer>;
  const Unsigned encoded = static_cast<Unsigned>(value);
  for (std::size_t index = 0; index < sizeof(Integer); ++index) {
    output[offset + index] = static_cast<unsigned char>(
        (encoded >> (index * 8U)) & static_cast<Unsigned>(0xffU));
  }
}

class CaptureActionInvocation final : public ActionInvocation {
 public:
  CaptureActionInvocation(
      const CaptureActionDefinition& definition,
      std::vector<std::string> fields)
      : definition_(definition),
        fields_(std::move(fields)),
        target_echo_(TargetDigest(definition.action_type, fields_)) {}

  [[nodiscard]] std::string_view action_type() const noexcept override {
    return definition_.action_type;
  }

  [[nodiscard]] std::string TargetEcho() const override {
    return target_echo_;
  }

  [[nodiscard]] PrepareResult Prepare(
      const GameApi& game,
      std::uint32_t country_id,
      const void*) override {
    auto body = Materialize();
    if (!body.has_value()) {
      return PrepareResult::Failed("capture_materialization_failed");
    }
    if (body->size() < definition_.expected_family.size() ||
        !std::equal(
            definition_.expected_family.begin(),
            definition_.expected_family.end(),
            body->begin())) {
      return PrepareResult::Rejected("capture_command_family_mismatch");
    }
    void* command = game.CreateSerializedCommand(*body);
    if (command == nullptr) {
      return PrepareResult::Failed("native_command_deserialization_failed");
    }
    game.SetCommandActor(command, country_id);
    if (!game.IsCommandValid(command)) {
      game.DestroyCommand(command);
      return PrepareResult::Rejected("native_command_invalid");
    }
    return PrepareResult::Ready(command);
  }

  [[nodiscard]] std::optional<RuntimeResult> Verify(
      const GameApi&,
      std::uint32_t,
      const void*) override {
    return RuntimeResult{
        "pending", "native_posted_awaiting_save_verification"};
  }

  [[nodiscard]] std::string_view TimeoutDetail() const noexcept override {
    return "native_posted_awaiting_save_verification";
  }

 private:
  [[nodiscard]] std::optional<std::vector<unsigned char>> Materialize() const {
    if (definition_.serialized_body_hex_field >= 0) {
      const auto index = static_cast<std::size_t>(
          definition_.serialized_body_hex_field);
      if (index >= fields_.size()) {
        return std::nullopt;
      }
      return DecodeHex(fields_[index]);
    }

    std::vector<unsigned char> body(
        definition_.template_body.begin(), definition_.template_body.end());
    std::vector<const CapturePatch*> string_patches;
    for (const auto& patch : definition_.patches) {
      if (patch.field_index >= fields_.size()) {
        return std::nullopt;
      }
      if (patch.kind == CapturePatchKind::kAsciiString) {
        string_patches.push_back(&patch);
        continue;
      }
      const std::size_t width = patch.kind == CapturePatchKind::kUint32
                                    ? sizeof(std::uint32_t)
                                    : sizeof(std::int64_t);
      if (patch.offset > body.size() || width > body.size() - patch.offset) {
        return std::nullopt;
      }
      if (patch.kind == CapturePatchKind::kUint32) {
        const auto parsed = ParseUint32(fields_[patch.field_index]);
        if (!parsed.has_value()) {
          return std::nullopt;
        }
        WriteLittleEndian(body, patch.offset, *parsed);
      } else {
        const auto parsed = ParseInt64(fields_[patch.field_index]);
        if (!parsed.has_value()) {
          return std::nullopt;
        }
        WriteLittleEndian(body, patch.offset, *parsed);
      }
    }

    std::sort(
        string_patches.begin(),
        string_patches.end(),
        [](const CapturePatch* left, const CapturePatch* right) {
          return left->offset > right->offset;
        });
    for (const auto* patch : string_patches) {
      const std::string& value = fields_[patch->field_index];
      if (value.size() > std::numeric_limits<std::uint16_t>::max() ||
          patch->offset > body.size() ||
          body.size() - patch->offset < 2U ||
          patch->original_size > body.size() - patch->offset - 2U) {
        return std::nullopt;
      }
      const auto begin = body.begin() + static_cast<std::ptrdiff_t>(patch->offset);
      const auto end = begin + static_cast<std::ptrdiff_t>(2U + patch->original_size);
      std::vector<unsigned char> replacement;
      replacement.reserve(2U + value.size());
      replacement.push_back(static_cast<unsigned char>(value.size() & 0xffU));
      replacement.push_back(static_cast<unsigned char>(value.size() >> 8U));
      replacement.insert(replacement.end(), value.begin(), value.end());
      body.erase(begin, end);
      body.insert(
          body.begin() + static_cast<std::ptrdiff_t>(patch->offset),
          replacement.begin(),
          replacement.end());
    }
    return body;
  }

  const CaptureActionDefinition& definition_;
  std::vector<std::string> fields_;
  std::string target_echo_;
};

bool FieldNeedsInt64(
    const CaptureActionDefinition& definition,
    std::size_t field_index) {
  return std::any_of(
      definition.patches.begin(),
      definition.patches.end(),
      [field_index](const CapturePatch& patch) {
        return patch.field_index == field_index &&
               patch.kind == CapturePatchKind::kInt64;
      });
}

}  // namespace

std::unique_ptr<ActionInvocation> ParseCaptureAction(
    const CaptureActionDefinition& definition,
    std::span<const std::string> target_fields,
    std::string& error_detail) {
  if (target_fields.size() != definition.parameters.size()) {
    error_detail = "invalid_capture_target_field_count";
    return nullptr;
  }
  for (std::size_t index = 0; index < target_fields.size(); ++index) {
    const auto& parameter = definition.parameters[index];
    const auto& value = target_fields[index];
    if (parameter.required && value.empty()) {
      error_detail = "missing_capture_target_field";
      return nullptr;
    }
    if (value.empty()) {
      continue;
    }
    if (parameter.type == ActionParameterType::kUint32) {
      const auto parsed = ParseUint32(value);
      if (!parsed.has_value() || *parsed < parameter.minimum ||
          *parsed > parameter.maximum) {
        error_detail = "capture_uint32_out_of_range";
        return nullptr;
      }
    } else if (parameter.type == ActionParameterType::kBoolean) {
      if (value != "0" && value != "1") {
        error_detail = "capture_boolean_invalid";
        return nullptr;
      }
    } else if (value.size() > parameter.maximum_length ||
               !IsPrintableAscii(value)) {
      error_detail = "capture_string_invalid";
      return nullptr;
    }
    if (FieldNeedsInt64(definition, index) && !ParseInt64(value).has_value()) {
      error_detail = "capture_int64_invalid";
      return nullptr;
    }
  }
  if (definition.serialized_body_hex_field >= 0) {
    const auto index = static_cast<std::size_t>(
        definition.serialized_body_hex_field);
    if (index >= target_fields.size()) {
      error_detail = "capture_body_field_missing";
      return nullptr;
    }
    const auto decoded = DecodeHex(target_fields[index]);
    if (!decoded.has_value() ||
        decoded->size() < definition.expected_family.size() ||
        !std::equal(
            definition.expected_family.begin(),
            definition.expected_family.end(),
            decoded->begin())) {
      error_detail = "capture_command_family_mismatch";
      return nullptr;
    }
  }
  error_detail.clear();
  return std::make_unique<CaptureActionInvocation>(
      definition,
      std::vector<std::string>(target_fields.begin(), target_fields.end()));
}

bool CaptureActionSupported(const BuildProfile& profile) noexcept {
  return profile.serialized_command.create_command_from_bytes != 0U;
}

}  // namespace iag::native_runtime
