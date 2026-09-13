"""Build save-backed orbital bombardment, landing, and army candidates."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from statistics import fmean, median
from typing import TYPE_CHECKING, Any

from iag.stellaris.game_knowledge import find_definition_source
from iag.stellaris.state.extract_game_state import construction_queues, resource_values
from iag.stellaris.state.fleet_profiles import (
    INVALID_OBJECT_ID,
    MILITARY_SHIP_CLASS,
    TRANSPORT_SHIP_CLASS,
    active_war_opponents,
    attack_target_routes,
    country_relation_profiles,
    extract_fleet_profiles,
    float_scalar,
    integer_scalar,
    integer_values,
    known_system_ids,
    name_hint,
    name_key,
    optional_section,
    owned_fleet_ids,
    planet_map,
    player_countries,
    quoted_value,
    repeated_integer,
    starbase_map,
)
from iag.stellaris.state.planet_profiles import (
    find_braced_section,
    object_reference_list,
    parse_numeric_map,
)
from iag.stellaris.state.research_profiles import extract_research_profile

if TYPE_CHECKING:
    from iag.stellaris.state.state_index import WorldStateIndex

VERIFIED_BOMBARDMENT_STANCES = ("selective", "indiscriminate", "raiding")
VERIFIED_ARMY_TYPE = "robotic_army"
VERIFIED_ARMY_MINERAL_COST = 150.0


def _optional_numeric_map(text: str, name: str) -> dict[int, str | None]:
    try:
        return parse_numeric_map(find_braced_section(text, name).strip())
    except ValueError:
        return {}


def _ratio(current: float | None, maximum: float | None) -> float | None:
    if current is None or maximum is None or maximum <= 0:
        return None
    return max(0.0, min(float(current) / float(maximum), 1.0))


def _army_summary(
    army_ids: list[int],
    armies: dict[int, str | None],
    *,
    owner_country_ids: set[int] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for army_id in army_ids:
        block = armies.get(army_id)
        if not block:
            continue
        army_owner = integer_scalar(block, "owner")
        if owner_country_ids is not None and army_owner not in owner_country_ids:
            continue
        health = float_scalar(block, "health")
        maximum_health = float_scalar(block, "max_health")
        rows.append(
            {
                "army_id": army_id,
                "army_type": quoted_value(block, "type"),
                "owner_country_id": army_owner,
                "species_id": integer_scalar(block, "species"),
                "health": health,
                "maximum_health": maximum_health,
                "health_ratio": _ratio(health, maximum_health),
                "morale": float_scalar(block, "morale"),
            }
        )
    health_ratios = [
        float(row["health_ratio"])
        for row in rows
        if row.get("health_ratio") is not None
    ]
    current_health = sum(float(row.get("health") or 0.0) for row in rows)
    maximum_health = sum(float(row.get("maximum_health") or 0.0) for row in rows)
    return {
        "army_count": len(rows),
        "army_types": dict(
            sorted(Counter(str(row.get("army_type") or "unknown") for row in rows).items())
        ),
        "current_health_total": current_health,
        "maximum_health_total": maximum_health,
        "aggregate_health_ratio": _ratio(current_health, maximum_health),
        "mean_health_ratio": fmean(health_ratios) if health_ratios else None,
        "median_health_ratio": median(health_ratios) if health_ratios else None,
        "armies": rows,
        "power_authority": (
            "save_health_and_type_summary_not_a_combat_power_estimate"
        ),
    }


def _planet_building_types(
    *,
    planet_id: int,
    planet_block: str,
    colonies: dict[int, str | None],
    districts: dict[int, str | None],
    zones: dict[int, str | None],
    buildings: dict[int, str | None],
) -> list[dict[str, Any]]:
    colony_id = integer_scalar(planet_block, "colony")
    colony_block = colonies.get(int(colony_id)) if colony_id is not None else None
    district_ids = integer_values(colony_block or "", "districts")
    if not district_ids:
        district_ids = integer_values(planet_block, "districts")
    output: list[dict[str, Any]] = []
    for district_id in district_ids:
        district_block = districts.get(district_id) or ""
        for zone_id in object_reference_list(district_block, "zones"):
            if zone_id is None:
                continue
            zone_block = zones.get(zone_id) or ""
            for building_id in integer_values(zone_block, "buildings"):
                building_block = buildings.get(building_id) or ""
                building_type = quoted_value(building_block, "type")
                if building_type:
                    output.append(
                        {
                            "planet_id": planet_id,
                            "district_id": district_id,
                            "zone_id": zone_id,
                            "building_object_id": building_id,
                            "building_type": building_type,
                        }
                    )
    return output


def _planetary_inhibitor_sources(
    *,
    buildings: list[dict[str, Any]],
    game_root: Path | None,
) -> list[dict[str, Any]]:
    if game_root is None:
        return []
    output: list[dict[str, Any]] = []
    for building in buildings:
        building_type = str(building["building_type"])
        source = find_definition_source(game_root, "building", building_type)
        if source is None:
            continue
        path, block, line = source
        if not re.search(r"\bplanetary_ftl_inhibitor\s*=\s*yes\b", block):
            continue
        output.append(
            {
                **building,
                "source_path": str(path),
                "source_line": line,
                "authority": "installed_building_definition",
            }
        )
    return output


def _country_stockpile(country_block: str) -> dict[str, float]:
    modules = optional_section(country_block, "modules")
    economy = optional_section(modules, "standard_economy_module")
    return resource_values(optional_section(economy, "resources"))


def _planet_system_index(systems: dict[int, str | None]) -> dict[int, int]:
    return {
        planet_id: system_id
        for system_id, block in systems.items()
        if block
        for planet_id in repeated_integer(block, "planet")
    }


def _queue_type(block: str) -> str | None:
    values = re.findall(r"(?m)^\s*type=([A-Za-z0-9_.-]+)\s*$", block)
    return values[-1] if values else None


def _owned_recruitment_starbases(
    *,
    owner: int,
    country_block: str,
    starbases: dict[int, str | None],
    systems: dict[int, str | None],
    queues: dict[int, str | None],
) -> list[dict[str, Any]]:
    owned_fleet_id_set = set(owned_fleet_ids(country_block))
    starbase_to_system = {
        starbase_index: system_id
        for system_id, system_block in systems.items()
        if system_block
        for starbase_index in integer_values(system_block, "starbases")
        if starbase_index != INVALID_OBJECT_ID
    }

    def owned_queue(starbase_block: str, key: str) -> bool:
        queue_id = integer_scalar(starbase_block, key)
        queue = queues.get(int(queue_id)) if queue_id is not None else None
        return bool(queue) and integer_scalar(queue or "", "owner") == owner

    output: list[dict[str, Any]] = []
    for starbase_index, block in sorted(starbases.items()):
        if not block:
            continue
        owned = (
            integer_scalar(block, "station") in owned_fleet_id_set
            or owned_queue(block, "build_queue")
            or owned_queue(block, "shipyard_build_queue")
        )
        level = quoted_value(block, "level")
        if not owned or not level or level == "starbase_level_outpost":
            continue
        system_id = starbase_to_system.get(starbase_index)
        system_block = systems.get(system_id) or ""
        output.append(
            {
                "starbase_index": starbase_index,
                "station_object": integer_scalar(block, "station"),
                "starbase_level": level,
                "system_id": system_id,
                "system_name_key": name_key(system_block),
                "system_display_name_hint": name_hint(system_block),
            }
        )
    return output


def _robotic_army_rule(
    *,
    game_root: Path | None,
    country_block: str,
    species_block: str,
    known_technologies: set[str],
) -> dict[str, Any]:
    traits = set(
        re.findall(
            r'(?m)^\s*trait="([A-Za-z0-9_.-]+)"\s*$',
            optional_section(species_block, "traits"),
        )
    )
    reasons: list[str] = []
    source: tuple[Path, str, int] | None = None
    if game_root is None:
        reasons.append("stellaris_game_root_unavailable")
    else:
        source = find_definition_source(game_root, "army", VERIFIED_ARMY_TYPE)
        if source is None:
            reasons.append("robotic_army_definition_missing")
    if "trait_mechanical" not in traits:
        reasons.append("founder_species_is_not_mechanical")
    if "tech_droid_workers" not in known_technologies:
        reasons.append("tech_droid_workers_missing")
    if "auth_machine_intelligence" in country_block:
        reasons.append("machine_empire_uses_a_different_army_rule")
    if "robots_outlawed" in country_block:
        reasons.append("robot_armies_may_be_outlawed")
    return {
        "army_type": VERIFIED_ARMY_TYPE,
        "eligible": not reasons,
        "reasons": reasons,
        "required_technology": "tech_droid_workers",
        "required_species_trait": "trait_mechanical",
        "verified_unit_cost": {"minerals": VERIFIED_ARMY_MINERAL_COST},
        "definition_source": (
            {"path": str(source[0]), "line": source[2]} if source else None
        ),
        "evidence_scope": "stellaris_4_4_6_non_nomadic_non_bio_robotic_army",
    }


def extract_invasion_profiles(
    text: str,
    owner: int | None = None,
    *,
    game_root: Path | None = None,
    maximum_recruitment_count: int = 5,
    fleet_profile: dict[str, Any] | None = None,
    research_profile: dict[str, Any] | None = None,
    state_index: WorldStateIndex | None = None,
) -> dict[str, Any]:
    """Return only candidates whose protocol fields resolve from the current save."""
    players = player_countries(text, state_index=state_index)
    if owner is None:
        if len(players) != 1:
            raise ValueError(
                "Could not infer one player country; pass owner explicitly."
            )
        owner = players[0]
    maximum_recruitment_count = max(1, min(int(maximum_recruitment_count), 5))

    countries = (
        state_index.numeric_map("country")
        if state_index is not None
        else parse_numeric_map(find_braced_section(text, "country").strip())
    )
    country = countries.get(owner)
    if not country:
        raise ValueError(f"Country {owner} does not exist in this save.")
    if state_index is None:
        fleets_raw = parse_numeric_map(find_braced_section(text, "fleet").strip())
        ships = parse_numeric_map(find_braced_section(text, "ships").strip())
        systems = parse_numeric_map(
            find_braced_section(text, "galactic_object").strip()
        )
        colonies = _optional_numeric_map(text, "colony")
        armies = _optional_numeric_map(text, "army")
        districts = _optional_numeric_map(text, "districts")
        zones = _optional_numeric_map(text, "zones")
        buildings = _optional_numeric_map(text, "buildings")
    else:
        fleets_raw = state_index.numeric_map("fleet")
        ships = state_index.numeric_map("ships")
        systems = state_index.numeric_map("galactic_object")
        colonies = state_index.optional_numeric_map("colony")
        armies = state_index.optional_numeric_map("army")
        districts = state_index.optional_numeric_map("districts")
        zones = state_index.optional_numeric_map("zones")
        buildings = state_index.optional_numeric_map("buildings")
    planets = planet_map(text, state_index=state_index)
    starbases = starbase_map(text, state_index=state_index)
    queues = construction_queues(text, state_index=state_index)
    planet_to_system = _planet_system_index(systems)
    relations = country_relation_profiles(country)
    opponents, active_war_ids = active_war_opponents(
        text,
        owner,
        state_index=state_index,
    )
    known_systems = known_system_ids(country, owner=owner, systems=systems)
    fleet_profile = fleet_profile or extract_fleet_profiles(
        text,
        owner=owner,
        state_index=state_index,
    )

    raw_hostile_colonies: list[dict[str, Any]] = []
    for planet_id, block in sorted(planets.items()):
        if not block:
            continue
        colony_id = integer_scalar(block, "colony")
        planet_owner = integer_scalar(block, "owner")
        controller = integer_scalar(block, "controller")
        system_id = planet_to_system.get(planet_id)
        if controller == INVALID_OBJECT_ID:
            controller = None
        effective_controller = controller if controller is not None else planet_owner
        if (
            colony_id in (None, INVALID_OBJECT_ID)
            or system_id is None
            or system_id not in known_systems
            or effective_controller not in opponents
        ):
            continue
        system_block = systems.get(system_id) or ""
        colony_block = colonies.get(int(colony_id)) or ""
        defending_army_ids = integer_values(colony_block, "army")
        planet_buildings = _planet_building_types(
            planet_id=planet_id,
            planet_block=block,
            colonies=colonies,
            districts=districts,
            zones=zones,
            buildings=buildings,
        )
        inhibitor_sources = _planetary_inhibitor_sources(
            buildings=planet_buildings,
            game_root=game_root,
        )
        inhibitor_owners = set(integer_values(system_block, "inhibitor_owners"))
        raw_hostile_colonies.append(
            {
                "planet_id": planet_id,
                "colony_id": int(colony_id),
                "planet_name_key": name_key(block),
                "planet_display_name_hint": name_hint(block),
                "planet_class": quoted_value(block, "planet_class"),
                "owner_country_id": planet_owner,
                "controller_country_id": controller,
                "effective_controller_country_id": effective_controller,
                "occupation_state": (
                    "occupied_by_third_party"
                    if controller is not None and controller != planet_owner
                    else "owner_controlled"
                ),
                "bombardment_damage": float_scalar(block, "bombardment_damage"),
                "defending_armies": _army_summary(
                    defending_army_ids,
                    armies,
                    owner_country_ids={int(effective_controller)},
                ),
                "planetary_ftl_inhibitor_sources": inhibitor_sources,
                "has_planetary_ftl_inhibitor_source": bool(inhibitor_sources),
                "system_has_hostile_ftl_inhibitor": bool(
                    opponents.intersection(inhibitor_owners)
                ),
                "system_id": system_id,
                "system_name_key": name_key(system_block),
                "system_display_name_hint": name_hint(system_block),
                "active_war_target": True,
                "target_authority": "save_planet_colony_and_active_war_mapping",
            }
        )

    hostile_colonies, blocked_hostile_colonies = attack_target_routes(
        owner=owner,
        country_block=country,
        countries=countries,
        fleets=fleets_raw,
        systems=systems,
        known_systems=known_systems,
        relations=relations,
        active_war_opponent_ids=opponents,
        owned_fleets=fleet_profile["fleets"],
        targets=raw_hostile_colonies,
        source_capabilities=("bombardment_verified_family", "landing_verified_family"),
    )

    fleets_with_armies: list[dict[str, Any]] = []
    for fleet in fleet_profile["fleets"]:
        fleet_view = dict(fleet)
        if fleet.get("ship_class") == TRANSPORT_SHIP_CLASS:
            army_ids = [
                army_id
                for ship_id in fleet.get("ship_ids", [])
                if (army_id := integer_scalar(ships.get(int(ship_id)) or "", "army"))
                not in (None, INVALID_OBJECT_ID)
            ]
            fleet_view["transport_armies"] = _army_summary(
                [int(army_id) for army_id in army_ids],
                armies,
                owner_country_ids={owner},
            )
            fleet_view["transport_power"] = fleet.get("military_power")
            fleet_view["transport_power_authority"] = "save_fleet_military_power"
        fleets_with_armies.append(fleet_view)

    research = research_profile or extract_research_profile(
        text,
        owner=owner,
        state_index=state_index,
    )
    known_technologies = set(research["known_technologies"])
    species_id = integer_scalar(country, "founder_species_ref")
    species_blocks = (
        state_index.numeric_map("species_db")
        if state_index is not None
        else parse_numeric_map(find_braced_section(text, "species_db").strip())
    )
    species_block = species_blocks.get(species_id) if species_id is not None else None
    army_rule = _robotic_army_rule(
        game_root=game_root,
        country_block=country,
        species_block=species_block or "",
        known_technologies=known_technologies,
    )
    stockpile = _country_stockpile(country)
    recruitment_starbases = _owned_recruitment_starbases(
        owner=owner,
        country_block=country,
        starbases=starbases,
        systems=systems,
        queues=queues,
    )
    recruitment_sources: list[dict[str, Any]] = []
    if species_id is not None and species_block and army_rule["eligible"]:
        for queue_id, queue_block in sorted(queues.items()):
            if (
                not queue_block
                or integer_scalar(queue_block, "owner") != owner
                or _queue_type(queue_block) != "army"
            ):
                continue
            location = optional_section(queue_block, "location")
            planet_id = integer_scalar(location, "id")
            planet_block = planets.get(planet_id) if planet_id is not None else None
            colony_id = integer_scalar(planet_block or "", "colony")
            if (
                integer_scalar(location, "type") != 2
                or not planet_block
                or integer_scalar(planet_block, "owner") != owner
                or colony_id in (None, INVALID_OBJECT_ID)
            ):
                continue
            recruitment_sources.append(
                {
                    "army_build_queue_id": queue_id,
                    "source_planet_id": int(planet_id),
                    "source_colony_id": int(colony_id),
                    "planet_name_key": name_key(planet_block),
                    "planet_display_name_hint": name_hint(planet_block),
                    "queue_item_handles": integer_values(queue_block, "items"),
                }
            )

    affordable = int(
        float(stockpile.get("minerals", 0.0)) // VERIFIED_ARMY_MINERAL_COST
    )
    maximum_affordable = max(0, min(maximum_recruitment_count, affordable))
    recruitment_candidates: list[dict[str, Any]] = []
    if maximum_affordable and recruitment_sources:
        # Every verified candidate recruits the same founder species. One stable
        # source colony per starbase avoids multiplying equivalent model choices.
        source = recruitment_sources[0]
        for starbase in recruitment_starbases:
            candidate_id = (
                f"recruit:{VERIFIED_ARMY_TYPE}:queue:"
                f"{source['army_build_queue_id']}:starbase:"
                f"{starbase['starbase_index']}"
            )
            recruitment_candidates.append(
                {
                    "candidate_id": candidate_id,
                    "army_type": VERIFIED_ARMY_TYPE,
                    "species_id": int(species_id),
                    "source_colony": source,
                    "recruitment_starbase": starbase,
                    "maximum_count": maximum_affordable,
                    "unit_cost": {"minerals": VERIFIED_ARMY_MINERAL_COST},
                    "target_template": {
                        "context_822c": owner,
                        "army_build_queue_id": int(source["army_build_queue_id"]),
                        "army_subtype": 0,
                        "army_type": VERIFIED_ARMY_TYPE,
                        "species_id": int(species_id),
                        "source_colony_object": int(source["source_colony_id"]),
                        "recruitment_starbase_object": int(starbase["starbase_index"]),
                    },
                }
            )

    return {
        "schema": "iag.stellaris_invasion_state.v1",
        "schema_version": 1,
        "game_date": quoted_value(text, "date"),
        "owner_country_id": owner,
        "active_war_ids": active_war_ids,
        "active_war_opponent_country_ids": sorted(opponents),
        "fleets": fleets_with_armies,
        "hostile_colonies": hostile_colonies,
        "blocked_hostile_colonies": blocked_hostile_colonies,
        "verified_bombardment_stances": list(VERIFIED_BOMBARDMENT_STANCES),
        "army_rule": army_rule,
        "country_stockpile": stockpile,
        "recruitment_sources": recruitment_sources,
        "recruitment_starbases": recruitment_starbases,
        "army_recruitment_candidates": recruitment_candidates,
        "maximum_army_recruitment_count": maximum_recruitment_count,
    }


def _source_fleet(profile: dict[str, Any], fleet_id: int) -> dict[str, Any]:
    fleet = next(
        (
            item
            for item in profile.get("fleets", [])
            if int(item["fleet_id"]) == fleet_id
        ),
        None,
    )
    if fleet is None:
        raise ValueError(f"Fleet {fleet_id} is not owned by the selected country.")
    return fleet


def _reachable_colony(
    profile: dict[str, Any], fleet_id: int, target_planet_id: int
) -> dict[str, Any]:
    target = next(
        (
            item
            for item in profile.get("hostile_colonies", [])
            if int(item["planet_id"]) == target_planet_id
        ),
        None,
    )
    if target is None:
        raise ValueError(
            f"Planet {target_planet_id} is not a reachable active-war colony target."
        )
    if fleet_id not in target.get("reachable_from_fleet_ids", []):
        route = next(
            (
                item
                for item in target.get("route_evidence", [])
                if fleet_id in item.get("source_fleet_ids", [])
            ),
            None,
        )
        status = route.get("status") if route else "unreachable_in_current_save"
        raise ValueError(
            f"Planet {target_planet_id} is not reachable from fleet {fleet_id}: {status}."
        )
    return target


def selected_orbital_bombardment(
    profile: dict[str, Any],
    *,
    fleet_id: int,
    target_planet_id: int,
    stance: str,
) -> dict[str, Any]:
    source = _source_fleet(profile, fleet_id)
    if source.get("ship_class") != MILITARY_SHIP_CLASS or not source.get(
        "bombardment_verified_family", False
    ):
        raise ValueError(f"Fleet {fleet_id} is not in the verified bombardment family.")
    if not source.get("attack_callable_now", False):
        raise ValueError(f"Fleet {fleet_id} cannot accept a bombardment order now.")
    if stance not in VERIFIED_BOMBARDMENT_STANCES:
        raise ValueError(f"Unsupported bombardment stance: {stance}.")
    target = _reachable_colony(profile, fleet_id, target_planet_id)
    return {
        "action": "orbital_bombardment",
        "source_fleet": source,
        "hostile_colony": target,
        "stance": stance,
        "protocol_sequence": [
            {
                "action": "set_orbital_bombardment_stance",
                "target": {"source_fleet_object": fleet_id, "stance": stance},
            },
            {
                "action": "move_fleet",
                "target": {
                    "source_fleet_object": fleet_id,
                    "destination_tag_hex": "132a01001400",
                    "destination_object": target_planet_id,
                },
            },
        ],
    }


def selected_army_landing(
    profile: dict[str, Any],
    *,
    transport_fleet_id: int,
    target_planet_id: int,
) -> dict[str, Any]:
    source = _source_fleet(profile, transport_fleet_id)
    if source.get("ship_class") != TRANSPORT_SHIP_CLASS or not source.get(
        "landing_verified_family", False
    ):
        raise ValueError(
            f"Fleet {transport_fleet_id} is not in the verified landing family."
        )
    if not source.get("landing_callable_now", False):
        raise ValueError(
            f"Fleet {transport_fleet_id} cannot accept a landing order now."
        )
    target = _reachable_colony(profile, transport_fleet_id, target_planet_id)
    return {
        "action": "land_armies",
        "source_fleet": source,
        "hostile_colony": target,
        "protocol_sequence": [
            {
                "action": "land_armies",
                "target": {
                    "source_fleet_object": transport_fleet_id,
                    "target_colony_object": int(target["colony_id"]),
                    "flag_6340": 0,
                    "flag_de35": 0,
                },
            }
        ],
    }


def selected_army_recruitment(
    profile: dict[str, Any],
    *,
    candidate_id: str,
    count: int,
) -> dict[str, Any]:
    candidate = next(
        (
            item
            for item in profile.get("army_recruitment_candidates", [])
            if item.get("candidate_id") == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError(
            "The army recruitment candidate is absent from the fresh save."
        )
    if not 1 <= count <= int(candidate["maximum_count"]):
        raise ValueError(
            f"count must be between 1 and {int(candidate['maximum_count'])}."
        )
    target = dict(candidate["target_template"])
    return {
        "action": "recruit_armies",
        "candidate_id": candidate_id,
        "count": count,
        "army_type": candidate["army_type"],
        "species_id": candidate["species_id"],
        "source_colony": candidate["source_colony"],
        "recruitment_starbase": candidate["recruitment_starbase"],
        "total_cost": {"minerals": VERIFIED_ARMY_MINERAL_COST * count},
        "protocol_sequence": [
            {"action": "recruit_army", "target": dict(target)} for _ in range(count)
        ],
    }
