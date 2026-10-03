#pragma once

#include <cstddef>
#include <memory>
#include <span>
#include <string>
#include <string_view>

#include "iag_native_runtime/action.h"

namespace iag::native_runtime {

// Capture-backed actions retain the exact CBin object layout established by
// paired packet evidence, but reconstruct a native CCommand through the
// game's own persistence factory. They are deliberately advertised as
// paired_capture until a live postcondition is implemented for that action.
enum class CapturePatchKind {
  kUint32,
  kInt64,
  kAsciiString,
};

struct CapturePatch {
  std::size_t field_index;
  CapturePatchKind kind;
  std::size_t offset;
  std::size_t original_size;
};

struct CaptureActionDefinition {
  std::string_view action_type;
  std::span<const ActionParameterDescriptor> parameters;
  std::span<const unsigned char> template_body;
  std::span<const unsigned char> expected_family;
  std::span<const CapturePatch> patches;
  // A non-negative index selects the explicitly isolated legacy adapter used
  // only by complex records that cannot yet be represented by scalar patches.
  int serialized_body_hex_field = -1;
};

[[nodiscard]] std::unique_ptr<ActionInvocation> ParseCaptureAction(
    const CaptureActionDefinition& definition,
    std::span<const std::string> target_fields,
    std::string& error_detail);
[[nodiscard]] bool CaptureActionSupported(
    const BuildProfile& profile) noexcept;

}  // namespace iag::native_runtime
