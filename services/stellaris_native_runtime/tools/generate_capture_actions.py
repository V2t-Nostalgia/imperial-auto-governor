#!/usr/bin/env python3
"""Generate exact-layout native adapters from the 4.4.6 packet fixtures.

The generated C++ never posts bytes to the network. It patches the reviewed
CBin command-object body and gives that body to Stellaris' own CreateCommand
factory. Keeping generation tied to the existing parsers/builders prevents a
second hand-maintained copy of the captured field layout.
"""

from __future__ import annotations

import argparse
import copy
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from iag.stellaris.execution.protocol_compatibility import (
    COMMAND_SPEC_BY_ACTION,
    OFFLINE_FIXTURE_TARGETS,
)
from iag.stellaris.execution.session_proxy import (
    _build_request_record,
    parse_arm_document,
)


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    description: str


@dataclass(frozen=True)
class Action:
    name: str
    application: str
    fields: tuple[Field, ...]


U32 = "u32"
I64 = "i64"
STRING = "string"
RAW = "raw"


def field(name: str, kind: str = U32) -> Field:
    return Field(name, kind, name.replace("_", " "))


ACTIONS: tuple[Action, ...] = (
    Action(
        "build_building",
        "economy_governance",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("colony_id"),
            field("zone_id"),
            field("building_id", STRING),
        ),
    ),
    Action(
        "upgrade_building",
        "economy_governance",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("colony_id"),
            field("zone_id"),
            field("building_object_id"),
            field("building_id", STRING),
        ),
    ),
    Action(
        "replace_building",
        "economy_governance",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("colony_id"),
            field("zone_id"),
            field("source_building_object_id"),
            field("building_id", STRING),
        ),
    ),
    Action(
        "build_district",
        "economy_governance",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("colony_id"),
            field("district_type", STRING),
        ),
    ),
    Action(
        "build_zone",
        "economy_governance",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("colony_id"),
            field("district_id"),
            field("slot_selector"),
            field("zone_type", STRING),
        ),
    ),
    Action(
        "move_fleet_to_coordinate",
        "fleet_operations",
        (
            field("source_fleet_object"),
            field("x_fixed", I64),
            field("y_fixed", I64),
            field("system_origin"),
        ),
    ),
    Action(
        "set_orbital_bombardment_stance",
        "fleet_operations",
        (field("source_fleet_object"), field("stance", STRING)),
    ),
    Action(
        "land_armies",
        "fleet_operations",
        (field("source_fleet_object"), field("target_colony_object")),
    ),
    Action(
        "recruit_army",
        "fleet_operations",
        (
            field("context_822c"),
            field("army_build_queue_id"),
            field("army_type", STRING),
            field("species_id"),
            field("source_colony_object"),
            field("recruitment_starbase_object"),
        ),
    ),
    Action(
        "repair_fleet",
        "fleet_operations",
        (field("context_822c"), field("source_fleet_object")),
    ),
    Action(
        "upgrade_fleet",
        "fleet_operations",
        (
            field("context_822c"),
            field("source_fleet_object"),
            field("shipyard_build_queue_id"),
        ),
    ),
    Action(
        "configure_ship_automation",
        "fleet_operations",
        (field("verified_record_hex", RAW),),
    ),
    Action(
        "build_starbase",
        "fleet_operations",
        (field("source_fleet_object"), field("target_system_object")),
    ),
    Action(
        "order_colony_ship_and_colonize",
        "fleet_operations",
        (
            field("context_822c"),
            field("species_id"),
            field("colony_designation", STRING),
            field("design_id"),
            field("upgrade_id"),
            field("growth_stage"),
            field("target_planet_id"),
            field("source_shipyard_build_queue_id"),
            field("system_name_key", STRING),
        ),
    ),
    Action(
        "colonize_with_existing_ship",
        "fleet_operations",
        (
            field("source_fleet_object"),
            field("target_planet_id"),
            field("system_name_key", STRING),
        ),
    ),
    Action(
        "upgrade_starbase",
        "fleet_operations",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("target_level", STRING),
            field("starbase_object"),
        ),
    ),
    Action(
        "set_starbase_module",
        "fleet_operations",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("component_id", STRING),
            field("slot_index"),
            field("starbase_object"),
        ),
    ),
    Action(
        "set_starbase_building",
        "fleet_operations",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("component_id", STRING),
            field("slot_index"),
            field("starbase_object"),
        ),
    ),
    Action(
        "start_research",
        "research_strategy",
        (field("context_822c"), field("technology_id", STRING)),
    ),
    Action(
        "build_ship",
        "fleet_operations",
        (
            field("context_822c"),
            field("build_queue_id"),
            field("design_id"),
            field("upgrade_id"),
            field("growth_stage"),
            field("destination_object"),
        ),
    ),
    Action(
        "create_ship_design",
        "fleet_operations",
        (field("verified_record_hex", RAW),),
    ),
    Action(
        "create_fleet_template",
        "fleet_operations",
        (field("context_822c"),),
    ),
    Action(
        "add_fleet_template_ship",
        "fleet_operations",
        (
            field("context_822c"),
            field("fleet_template_id"),
            field("design_id"),
            field("upgrade_id"),
            field("growth_stage"),
        ),
    ),
    Action(
        "remove_fleet_template_ship",
        "fleet_operations",
        (
            field("fleet_template_id"),
            field("design_id"),
            field("upgrade_id"),
            field("growth_stage"),
        ),
    ),
    Action(
        "reinforce_selected_fleet",
        "fleet_operations",
        (field("context_822c"), field("fleet_template_id")),
    ),
)


def cpp_name(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))


def build_record(action: str, target: dict[str, object]) -> bytes:
    request = parse_arm_document(
        {
            "request_id": "native-capture-generator",
            "session_id": "native-capture-generator",
            "action": action,
            "source_actor": 0,
            "host_actor": 1,
            "request_origin": 0,
            "target": target,
        },
        "native-capture-generator",
    )
    record = _build_request_record(request, command_serial=1)
    if len(record) < 12 or record[2:6] != bytes.fromhex("04000000"):
        raise RuntimeError(f"{action}: invalid captured command envelope")
    return record


def scalar_patches(
    action: str,
    target: dict[str, object],
    name: str,
    width: int,
) -> list[tuple[int, int]]:
    original = build_record(action, target)[6:]
    changed = copy.deepcopy(target)
    old = int(changed[name])
    encoded = old.to_bytes(width, "little", signed=width == 8)
    replacement = bytes(value ^ 0xFF for value in encoded)
    changed[name] = int.from_bytes(replacement, "little", signed=width == 8)
    mutated = build_record(action, changed)[6:]
    if len(original) != len(mutated):
        raise RuntimeError(f"{action}.{name}: scalar mutation resized record")
    indexes = [index for index, pair in enumerate(zip(original, mutated)) if pair[0] != pair[1]]
    if not indexes:
        raise RuntimeError(f"{action}.{name}: scalar mutation was not serialized")
    groups: list[list[int]] = []
    for index in indexes:
        if not groups or index != groups[-1][-1] + 1:
            groups.append([index])
        else:
            groups[-1].append(index)
    patches: list[tuple[int, int]] = []
    for group in groups:
        if len(group) != width:
            raise RuntimeError(
                f"{action}.{name}: unexpected scalar diff width {len(group)}"
            )
        patches.append((group[0], width))
    return patches


def string_patches(
    action: str,
    target: dict[str, object],
    name: str,
) -> list[tuple[int, int]]:
    original = build_record(action, target)[6:]
    value = str(target[name])
    if not value:
        raise RuntimeError(f"{action}.{name}: empty fixture string")
    encoded = value.encode("ascii")
    marker = len(encoded).to_bytes(2, "little") + encoded
    candidates: list[int] = []
    cursor = original.find(marker)
    while cursor >= 0:
        candidates.append(cursor)
        cursor = original.find(marker, cursor + 1)
    if not candidates:
        raise RuntimeError(f"{action}.{name}: string marker was not found")
    if name == "stance":
        if len(candidates) != 1:
            raise RuntimeError(f"{action}.{name}: ambiguous enum string marker")
        return [(candidates[0], len(encoded))]

    replacement = value[:-1] + ("z" if value[-1] != "z" else "y")
    changed = copy.deepcopy(target)
    changed[name] = replacement
    mutated = build_record(action, changed)[6:]
    if len(original) != len(mutated):
        raise RuntimeError(f"{action}.{name}: same-size mutation resized record")
    indexes = [
        index
        for index, pair in enumerate(zip(original, mutated))
        if pair[0] != pair[1]
    ]
    if not indexes:
        raise RuntimeError(f"{action}.{name}: string mutation was not serialized")
    selected = [
        offset
        for offset in candidates
        if any(offset + 2 <= index < offset + 2 + len(encoded) for index in indexes)
    ]
    covered = {
        index
        for offset in selected
        for index in indexes
        if offset + 2 <= index < offset + 2 + len(encoded)
    }
    if not selected or covered != set(indexes):
        raise RuntimeError(f"{action}.{name}: string mutation was ambiguous")
    return [(offset, len(encoded)) for offset in selected]


def alternate_target(action: Action, target: dict[str, object]) -> dict[str, object]:
    changed = copy.deepcopy(target)
    for item in action.fields:
        if item.kind == RAW:
            continue
        if item.kind == U32:
            old = int(changed[item.name])
            changed[item.name] = (old ^ 0xA5A55A5A) & 0xFFFFFFFF
        elif item.kind == I64:
            old = int(changed[item.name])
            changed[item.name] = old + 1_234_567
        elif item.name == "stance":
            changed[item.name] = (
                "indiscriminate"
                if changed[item.name] != "indiscriminate"
                else "selective"
            )
        else:
            changed[item.name] = f"{changed[item.name]}_iag"
    return changed


def materialize(
    body: bytes,
    action: Action,
    target: dict[str, object],
    patches: list[tuple[int, str, int, int]],
) -> bytes:
    output = bytearray(body)
    for field_index, kind, offset, _size in patches:
        if kind == "kAsciiString":
            continue
        item = action.fields[field_index]
        width = 4 if kind == "kUint32" else 8
        output[offset : offset + width] = int(target[item.name]).to_bytes(
            width,
            "little",
            signed=kind == "kInt64",
        )
    string_patches = sorted(
        (patch for patch in patches if patch[1] == "kAsciiString"),
        key=lambda patch: patch[2],
        reverse=True,
    )
    for field_index, _kind, offset, original_size in string_patches:
        encoded = str(target[action.fields[field_index].name]).encode("ascii")
        output[offset : offset + 2 + original_size] = (
            len(encoded).to_bytes(2, "little") + encoded
        )
    return bytes(output)


def validate_materialization(
    action: Action,
    fixture_target: dict[str, object],
    body: bytes,
    patches: list[tuple[int, str, int, int]],
) -> None:
    if any(item.kind == RAW for item in action.fields):
        return
    changed = alternate_target(action, fixture_target)
    expected = build_record(action.name, changed)[6:]
    actual = materialize(body, action, changed, patches)
    if actual == expected:
        return
    mismatch = next(
        (
            index
            for index, pair in enumerate(zip(actual, expected))
            if pair[0] != pair[1]
        ),
        min(len(actual), len(expected)),
    )
    raise RuntimeError(
        f"{action.name}: generated materializer diverged at byte {mismatch} "
        f"(actual={len(actual)}, expected={len(expected)})"
    )


def bytes_array(name: str, value: bytes) -> list[str]:
    lines = [f"constexpr std::array<unsigned char, {len(value)}> {name} = {{{{"]
    for offset in range(0, len(value), 12):
        chunk = value[offset : offset + 12]
        lines.append("    " + ", ".join(f"0x{item:02x}" for item in chunk) + ",")
    lines.append("}};")
    return lines


def parameter_line(item: Field) -> str:
    if item.kind == U32:
        return (
            f'    {{"{item.name}", ActionParameterType::kUint32, true, '
            f'"{item.description}", 0U, UINT32_MAX, 0U}},'
        )
    maximum = 60000 if item.kind == RAW else (20 if item.kind == I64 else 160)
    return (
        f'    {{"{item.name}", ActionParameterType::kString, true, '
        f'"{item.description}", 0U, 0U, {maximum}U}},'
    )


def render_action(action: Action) -> list[str]:
    name = cpp_name(action.name)
    fixture_target = copy.deepcopy(OFFLINE_FIXTURE_TARGETS[action.name])
    record = build_record(action.name, fixture_target)
    body = record[6:]
    lines: list[str] = []
    lines.append(f"// {action.name}")
    lines.append(
        f"constexpr std::array<ActionParameterDescriptor, {len(action.fields)}> "
        f"k{name}Parameters = {{{{"
    )
    lines.extend(parameter_line(item) for item in action.fields)
    lines.append("}};")
    lines.extend(bytes_array(f"k{name}Family", body[:6]))

    raw_index = next(
        (index for index, item in enumerate(action.fields) if item.kind == RAW),
        -1,
    )
    patches: list[tuple[int, str, int, int]] = []
    if raw_index < 0:
        lines.extend(bytes_array(f"k{name}Body", body))
        for field_index, item in enumerate(action.fields):
            if item.kind == U32:
                found = scalar_patches(
                    action.name, fixture_target, item.name, 4
                )
                patches.extend(
                    (field_index, "kUint32", offset, size)
                    for offset, size in found
                )
            elif item.kind == I64:
                found = scalar_patches(
                    action.name, fixture_target, item.name, 8
                )
                patches.extend(
                    (field_index, "kInt64", offset, size)
                    for offset, size in found
                )
            elif item.kind == STRING:
                found = string_patches(action.name, fixture_target, item.name)
                patches.extend(
                    (field_index, "kAsciiString", offset, size)
                    for offset, size in found
                )
    else:
        lines.append(f"constexpr std::array<unsigned char, 0> k{name}Body = {{}};")

    validate_materialization(action, fixture_target, body, patches)

    lines.append(
        f"constexpr std::array<CapturePatch, {len(patches)}> k{name}Patches = {{{{"
    )
    for field_index, kind, offset, size in patches:
        lines.append(
            f"    {{{field_index}U, CapturePatchKind::{kind}, {offset}U, {size}U}},"
        )
    lines.append("}};")
    lines.extend(
        (
            f"const CaptureActionDefinition k{name}Definition = {{",
            f'    .action_type = "{action.name}",',
            f"    .parameters = k{name}Parameters,",
            f"    .template_body = k{name}Body,",
            f"    .expected_family = k{name}Family,",
            f"    .patches = k{name}Patches,",
            f"    .serialized_body_hex_field = {raw_index},",
            "};",
            f"std::unique_ptr<ActionInvocation> Parse{name}(",
            "    std::span<const std::string> fields,",
            "    std::string& error_detail) {",
            f"  return ParseCaptureAction(k{name}Definition, fields, error_detail);",
            "}",
        )
    )
    risk = COMMAND_SPEC_BY_ACTION[action.name].risk
    lines.extend(
        (
            f"const ActionDescriptor k{name}Descriptor = {{",
            f'    .action_type = "{action.name}",',
            "    .action_version = 1U,",
            f'    .application_id = "{action.application}",',
            f'    .description = "Capture-backed native {action.name} command.",',
            f'    .risk_class = "{risk}",',
            '    .verification_state = "paired_capture",',
            f"    .parameters = k{name}Parameters,",
            f"    .parse = &Parse{name},",
            "    .supported = &CaptureActionSupported,",
            "};",
            f"IAG_NATIVE_ACTION_REGISTRATION const ActionRegistration k{name}Registration{{",
            f"    k{name}Descriptor}};",
            "",
        )
    )
    return lines


def render() -> str:
    lines = [
        "// Generated by tools/generate_capture_actions.py. Do not edit by hand.",
        "// Source of truth: the paired 4.4.6 packet builders and offline fixtures.",
        "",
    ]
    for action in ACTIONS:
        lines.extend(render_action(action))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "actions" / "generated_capture_actions.inc"
    rendered = render()
    if args.check:
        if not output.exists() or output.read_text(encoding="ascii") != rendered:
            print(f"generated capture actions are stale: {output}", file=sys.stderr)
            return 1
        print(f"verified {output} ({len(ACTIONS)} actions)")
        return 0
    output.write_text(rendered, encoding="ascii")
    print(f"wrote {output} ({len(ACTIONS)} actions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
