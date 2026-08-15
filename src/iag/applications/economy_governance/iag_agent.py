#!/usr/bin/env python3
"""Create one auditable IAG plan and execution manifest."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from iag.core.paths import project_root
from iag.infrastructure.llm.model_client import call_model
from iag.infrastructure.llm.model_pool_runtime import ModelPoolRuntime
from iag.infrastructure.llm.runtime_config import RuntimeConfig, RuntimeSnapshot
from iag.infrastructure.research.web_research import build_web_research_context
from iag.stellaris.game_knowledge import (
    build_local_rules_context,
    enrich_snapshot_layout,
)
from iag.stellaris.state.extract_game_state import (
    extract_game_state,
    load_gamestate,
)
from iag.stellaris.state.save_ingest import resolve_current_save

from .planner import (
    CAPABILITIES_PATH,
    build_candidates,
    decision_request,
    execution_manifest,
    read_json,
    validate_plan,
    write_json,
)


ROOT = project_root()


def optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def configured_path(config: dict, key: str, default: Path) -> Path:
    value = Path(config.get(key, default)).expanduser()
    if value.is_absolute():
        return value
    return Path(config["runtime_root"]).expanduser() / value


def run_cycle(
    runtime: RuntimeSnapshot,
    *,
    save_path: Path | None,
    run_root: Path,
    plan_path: Path | None,
    model_pool_runtime: ModelPoolRuntime | None = None,
) -> Path:
    config = runtime.settings
    capabilities = read_json(CAPABILITIES_PATH)
    if save_path is None:
        save_path = resolve_current_save(config)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    runtime_root = Path(config["runtime_root"]).expanduser()
    prompt_path = configured_path(
        config,
        "operator_prompt_path",
        runtime_root / "operator" / "strategic_prompt.md",
    )
    instruction_path = configured_path(
        config,
        "operator_instruction_path",
        runtime_root / "operator" / "current_instruction.md",
    )
    snapshot = extract_game_state(
        load_gamestate(save_path),
        save_path=save_path,
    )
    enrich_snapshot_layout(config, capabilities, snapshot)
    candidates = build_candidates(snapshot, capabilities, config)
    operator_context = {
        "strategic_prompt": optional_text(prompt_path),
        "current_instruction": optional_text(instruction_path),
        "prompt_path": str(prompt_path),
        "instruction_path": str(instruction_path),
    }
    local_rules = build_local_rules_context(config, capabilities, snapshot)
    web_research = build_web_research_context(
        config,
        snapshot,
        local_rules,
        runtime_root,
    )
    knowledge = {
        "schema": "iag.knowledge_context.v1",
        "priority_order": [
            "save_state",
            "installed_game_rules",
            "official_web_sources",
            "community_web_sources",
        ],
        "local_rules": local_rules,
        "web_research": web_research,
    }
    request = decision_request(
        snapshot,
        candidates,
        operator_context=operator_context,
        knowledge_context=knowledge,
    )
    write_json(run_dir / "snapshot.json", snapshot)
    write_json(run_dir / "candidates.json", candidates)
    write_json(run_dir / "local_rules.json", local_rules)
    write_json(run_dir / "web_research.json", web_research)
    write_json(run_dir / "knowledge.json", knowledge)
    write_json(run_dir / "decision_request.json", request)

    pool_runtime = model_pool_runtime or ModelPoolRuntime(runtime.model_pool)
    pool_runtime.replace_pool(runtime.model_pool)
    plan = (
        read_json(plan_path)
        if plan_path
        else pool_runtime.execute(
            lambda endpoint: call_model(
                endpoint,
                request,
                request_options=runtime.request_options,
            )
        )
    )
    write_json(run_dir / "plan.json", plan)
    selected = validate_plan(plan, snapshot, candidates, config)
    manifest = execution_manifest(plan, selected, snapshot, capabilities)
    write_json(run_dir / "execution_manifest.json", manifest)
    return run_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--save", type=Path)
    parser.add_argument(
        "--run-root",
        type=Path,
        default=ROOT / "runtime" / "runs",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        help="Use a supplied plan instead of calling the configured API.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime = RuntimeConfig.load(args.config).snapshot()
    run_dir = run_cycle(
        runtime,
        save_path=args.save,
        run_root=args.run_root,
        plan_path=args.plan,
    )
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
