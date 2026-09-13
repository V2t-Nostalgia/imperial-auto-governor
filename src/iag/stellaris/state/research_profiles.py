"""Extract save-backed Stellaris research state without conflating queues.

The 4.4 save places completed technologies, active research queues, rolled
alternatives, and always-available technologies in one ``tech_status`` block.
This module keeps those concepts separate so an agent can only select options
that the synchronized save actually exposes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from iag.stellaris.state.planet_profiles import (
    find_braced_section,
    parse_numeric_map,
)

if TYPE_CHECKING:
    from iag.stellaris.state.state_index import WorldStateIndex

RESEARCH_AREAS = ("physics", "society", "engineering")
TECHNOLOGY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def optional_section(text: str, name: str) -> str:
    try:
        return find_braced_section(text, name, allow_indent=True)
    except ValueError:
        return ""


def _brace_delta(line: str) -> int:
    """Count structural braces while ignoring comments and quoted strings."""
    depth = 0
    quoted = False
    escaped = False
    for character in line:
        if escaped:
            escaped = False
            continue
        if quoted and character == "\\":
            escaped = True
            continue
        if character == '"':
            quoted = not quoted
            continue
        if character == "#" and not quoted:
            break
        if quoted:
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
    return depth


def top_level_assignments(block: str) -> list[tuple[str, str]]:
    """Return scalar assignments that are direct children of ``block``."""
    output: list[tuple[str, str]] = []
    depth = 0
    assignment = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*(.*?)\s*$")
    for line in block.splitlines():
        if depth == 0:
            match = assignment.match(line)
            if match and "{" not in match.group(2):
                output.append((match.group(1), match.group(2)))
        depth += _brace_delta(line)
    return output


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _quoted_values(block: str) -> list[str]:
    return re.findall(r'"([A-Za-z0-9_.-]+)"', block)


def _queue_profile(status: str, area: str) -> dict[str, Any] | None:
    queue = optional_section(status, f"{area}_queue")
    if not queue:
        return None
    technology = re.search(
        r'(?m)^\s*technology="([A-Za-z0-9_.-]+)"\s*$',
        queue,
    )
    if technology is None:
        return None
    progress = re.search(
        r"(?m)^\s*progress=(-?\d+(?:\.\d+)?)\s*$",
        queue,
    )
    completion_date = re.search(
        r'(?m)^\s*date=\s*"([^"]+)"\s*$',
        queue,
    )
    return {
        "technology_id": technology.group(1),
        "progress": float(progress.group(1)) if progress else None,
        "estimated_completion_date": (
            completion_date.group(1) if completion_date else None
        ),
    }

def _stored_points(status: str) -> dict[str, float | None]:
    block = optional_section(status, "stored_techpoints")
    values = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", block)]
    return {
        area: values[index] if index < len(values) else None
        for index, area in enumerate(RESEARCH_AREAS)
    }


def _stored_points_by_technology(status: str) -> dict[str, float]:
    block = optional_section(status, "stored_techpoints_for_tech")
    return {
        technology_id: float(value)
        for technology_id, value in re.findall(
            r"(?m)^\s*([A-Za-z0-9_.-]+)=(-?\d+(?:\.\d+)?)\s*$",
            block,
        )
    }


def _player_countries(text: str) -> list[int]:
    players = optional_section(text, "player")
    return sorted({int(value) for value in re.findall(r"\bcountry=(\d+)", players)})


def extract_research_profile(
    text: str,
    owner: int | None = None,
    *,
    technology_area: Callable[[str], str | None] | None = None,
    state_index: WorldStateIndex | None = None,
) -> dict[str, Any]:
    """Return authoritative research queues and legal save candidates."""
    players = (
        state_index.player_countries()
        if state_index is not None
        else _player_countries(text)
    )
    if owner is None:
        if len(players) != 1:
            raise ValueError(
                "Could not infer one player country; pass owner explicitly."
            )
        owner = players[0]

    countries = (
        state_index.numeric_map("country")
        if state_index is not None
        else parse_numeric_map(find_braced_section(text, "country").strip())
    )
    country = countries.get(owner)
    if country is None:
        raise ValueError(f"Country {owner} does not exist in this save.")
    status = optional_section(country, "tech_status")
    if not status:
        raise ValueError(f"Country {owner} has no tech_status block.")

    direct = top_level_assignments(status)
    known_levels: dict[str, int] = {}
    pending_known: str | None = None
    always_available: list[str] = []
    automatic: dict[str, bool] = {}
    for key, raw_value in direct:
        value = _unquote(raw_value)
        if key == "technology" and TECHNOLOGY_ID_RE.fullmatch(value):
            pending_known = value
        elif key == "level" and pending_known is not None:
            try:
                known_levels[pending_known] = int(value)
            except ValueError:
                known_levels[pending_known] = 1
            pending_known = None
        elif key == "always_available_tech" and TECHNOLOGY_ID_RE.fullmatch(value):
            always_available.append(value)
        elif key.startswith("auto_researching_"):
            area = key.removeprefix("auto_researching_")
            if area in RESEARCH_AREAS:
                automatic[area] = value == "yes"

    alternatives = optional_section(status, "alternatives")
    alternative_by_area: dict[str, list[str]] = {}
    for area in RESEARCH_AREAS:
        area_block = optional_section(alternatives, area)
        alternative_by_area[area] = list(dict.fromkeys(_quoted_values(area_block)))

    always_by_area: dict[str, list[str]] = {area: [] for area in RESEARCH_AREAS}
    unclassified: list[str] = []
    for technology_id in dict.fromkeys(always_available):
        matching_area = next(
            (
                area
                for area in RESEARCH_AREAS
                if technology_id in alternative_by_area[area]
            ),
            None,
        )
        if matching_area is None and technology_area is not None:
            matching_area = technology_area(technology_id)
        if matching_area in RESEARCH_AREAS:
            always_by_area[str(matching_area)].append(technology_id)
        else:
            unclassified.append(technology_id)

    stored_points = _stored_points(status)
    fields: dict[str, dict[str, Any]] = {}
    for area in RESEARCH_AREAS:
        alternatives_for_area = alternative_by_area[area]
        always_for_area = always_by_area[area]
        fields[area] = {
            "current": _queue_profile(status, area),
            "alternatives": alternatives_for_area,
            "always_available": always_for_area,
            "legal_candidate_ids": list(
                dict.fromkeys([*alternatives_for_area, *always_for_area])
            ),
            "stored_research_points": stored_points[area],
            "auto_researching": automatic.get(area, False),
        }

    date_match = re.search(r'(?m)^date="([^"]+)"\s*$', text)
    return {
        "schema": "iag.stellaris_research_state.v1",
        "schema_version": 1,
        "game_date": date_match.group(1) if date_match else None,
        "owner_country_id": owner,
        "player_country_ids": players,
        "known_technologies": sorted(known_levels),
        "known_technology_levels": dict(sorted(known_levels.items())),
        "fields": fields,
        "always_available_unclassified": unclassified,
        "stored_points_by_technology": _stored_points_by_technology(status),
        "candidate_authority": "synchronized_save_tech_status",
    }
