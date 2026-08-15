#!/usr/bin/env python3
"""从冻结的 IAG v0.5.8 仓库复制已验证生产文件并生成来源清单。

该脚本只读取源仓库，不删除目标文件，也不会复制测试、运行状态、抓包、
存档、缓存、发行压缩包或已被正式链路替代的实验探针。每项映射都显式
记录在下方，方便开发者审查新平台中的职责归属。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


# 单文件映射按新平台职责分组。保留原文件名可以降低首次迁移时的行为变化。
FILE_MAP: dict[str, str] = {
    # Core: 战役记忆、战略状态、上下文和周期调度。
    "tools/agent_runtime/autonomy.py": "src/iag/core/autonomy.py",
    "tools/agent_runtime/campaign_strategy.py": "src/iag/core/campaign_strategy.py",
    "tools/agent_runtime/context_window.py": "src/iag/core/context_window.py",
    "tools/agent_runtime/conversation_store.py": "src/iag/core/conversation_store.py",
    # Stellaris state and knowledge: 存档、游戏定义和状态归一化。
    "tools/agent_runtime/extract_game_state.py": "src/iag/stellaris/state/extract_game_state.py",
    "tools/agent_runtime/save_ingest.py": "src/iag/stellaris/state/save_ingest.py",
    "tools/save_state/extract_planet_profiles.py": "src/iag/stellaris/state/planet_profiles.py",
    "tools/save_state/Export-IAGPlanetProfiles.ps1": "scripts/diagnostics/Export-IAGPlanetProfiles.ps1",
    "tools/agent_runtime/game_knowledge.py": "src/iag/stellaris/game_knowledge.py",
    # Execution: 固定点击、会话发现、房主协议和 Linux 回退路径。
    "tools/agent_runtime/fixed_click.py": "src/iag/stellaris/execution/fixed_click.py",
    "tools/agent_runtime/windows_fixed_click.py": "src/iag/stellaris/execution/windows_fixed_click.py",
    "tools/agent_runtime/x11_fixed_click.py": "src/iag/stellaris/execution/x11_fixed_click.py",
    "tools/agent_runtime/host_bridge_pairing.py": "src/iag/stellaris/execution/host_bridge_pairing.py",
    "tools/agent_runtime/host_executor_protocol.py": "src/iag/stellaris/execution/host_executor_protocol.py",
    "tools/agent_runtime/port_discovery.py": "src/iag/stellaris/execution/port_discovery.py",
    "tools/agent_runtime/iag_supervisor.py": "src/iag/stellaris/execution/iag_supervisor.py",
    "tools/agent_runtime/iag_linux_interceptor.py": "src/iag/stellaris/execution/iag_linux_interceptor.py",
    "tools/agent_runtime/linux_packet.py": "src/iag/stellaris/execution/linux_packet.py",
    "tools/agent_runtime/passive_network_observer.py": "src/iag/stellaris/execution/passive_network_observer.py",
    # Packet core: 当前 Host Bridge 或 Linux 执行链实际依赖的解析器与改写器。
    "tools/packet_interceptor/iag_stream_command_injector.py": "src/iag/stellaris/execution/packet/iag_stream_command_injector.py",
    "tools/packet_interceptor/iag_packet_interceptor.py": "src/iag/stellaris/execution/packet/iag_packet_interceptor.py",
    "tools/packet_interceptor/iag_same_family_construction_rewriter.py": "src/iag/stellaris/execution/packet/iag_same_family_construction_rewriter.py",
    "tools/packet_interceptor/iag_building_upgrade_rewriter.py": "src/iag/stellaris/execution/packet/iag_building_upgrade_rewriter.py",
    "tools/packet_interceptor/iag_building_replacement_rewriter.py": "src/iag/stellaris/execution/packet/iag_building_replacement_rewriter.py",
    "tools/packet_interceptor/iag_command_replacement_injector.py": "src/iag/stellaris/execution/packet/iag_command_replacement_injector.py",
    "tools/packet_interceptor/iag_building_to_zone_replacer.py": "src/iag/stellaris/execution/packet/iag_building_to_zone_replacer.py",
    "tools/packet_interceptor/iag_udp_flow_analyzer.py": "scripts/diagnostics/iag_udp_flow_analyzer.py",
    # Economy governance: 当前已经跑通的殖民地经济 Application。
    "tools/agent_runtime/planner.py": "src/iag/applications/economy_governance/planner.py",
    "tools/agent_runtime/agent_tools.py": "src/iag/applications/economy_governance/agent_tools.py",
    "tools/agent_runtime/conversation_agent.py": "src/iag/applications/economy_governance/conversation_agent.py",
    "tools/agent_runtime/iag_agent.py": "src/iag/applications/economy_governance/iag_agent.py",
    "tools/agent_runtime/schemas/agent_plan.schema.json": "src/iag/applications/economy_governance/schemas/agent_plan.schema.json",
    "tools/agent_runtime/strategy/grey_tempest_conversation_zh.md": "src/iag/applications/economy_governance/prompts/grey_tempest_conversation_zh.md",
    "tools/agent_runtime/strategy/grey_tempest_governor_zh.md": "src/iag/applications/economy_governance/prompts/grey_tempest_governor_zh.md",
    # Infrastructure: 模型接入和不可信网页研究。
    "tools/agent_runtime/model_client.py": "src/iag/infrastructure/llm/model_client.py",
    "tools/agent_runtime/model_templates.py": "src/iag/infrastructure/llm/model_templates.py",
    "tools/agent_runtime/model_templates.json": "src/iag/infrastructure/llm/model_templates.json",
    "tools/agent_runtime/web_research.py": "src/iag/infrastructure/research/web_research.py",
    "tools/agent_runtime/research_tools.py": "src/iag/infrastructure/research/research_tools.py",
    # Control center application and static frontend.
    "tools/agent_runtime/web_console.py": "apps/control_center/web_console.py",
    "tools/agent_runtime/windows_agent_gui.py": "apps/control_center/windows_agent_gui.py",
    "tools/agent_runtime/agent_config.example.json": "apps/control_center/agent_config.linux.example.json",
    "tools/agent_runtime/agent_config.windows.example.json": "apps/control_center/agent_config.windows.example.json",
    "tools/agent_runtime/schemas/fixed_click_profile.schema.json": "apps/control_center/schemas/fixed_click_profile.schema.json",
    "tools/agent_runtime/IAGWindowsAgent.spec": "apps/control_center/IAGWindowsAgent.spec",
    "tools/agent_runtime/web/index.html": "apps/control_center/web/index.html",
    "tools/agent_runtime/web/app.js": "apps/control_center/web/app.js",
    "tools/agent_runtime/web/app_v2.js": "apps/control_center/web/app_v2.js",
    "tools/agent_runtime/web/styles.css": "apps/control_center/web/styles.css",
    "tools/agent_runtime/web/construction.css": "apps/control_center/web/construction.css",
    "tools/agent_runtime/web/conversation.css": "apps/control_center/web/conversation.css",
    # Vanilla 4.4 Content Pack: 当前候选能力与本地规则证据。
    "tools/agent_runtime/capabilities.json": "content_packs/vanilla_4_4/mappings/capabilities.json",
    "tools/agent_runtime/strategy/stellaris_4_4_sources.md": "content_packs/vanilla_4_4/prompts/stellaris_4_4_sources.md",
    # Windows host bridge application.
    "tools/windows_save_uploader/iag_host_interceptor.py": "apps/host_bridge/iag_host_interceptor.py",
    "tools/windows_save_uploader/iag_save_uploader.py": "apps/host_bridge/iag_save_uploader.py",
    "tools/windows_save_uploader/iag_save_uploader_gui.py": "apps/host_bridge/iag_save_uploader_gui.py",
    "tools/windows_save_uploader/iag_save_uploader.example.json": "apps/host_bridge/iag_save_uploader.example.json",
    "tools/windows_save_uploader/IAGSaveUploaderGUI.spec": "apps/host_bridge/IAGSaveUploaderGUI.spec",
    # Build and deployment helpers still required by the two supported runtimes.
    "tools/agent_runtime/Build-IAGWindowsAgent.ps1": "scripts/build/Build-IAGWindowsAgent.ps1",
    "tools/agent_runtime/migrate_config_v2.py": "scripts/migration/migrate_runtime_config_v2.py",
    "tools/windows_save_uploader/Build-IAGHostBridge.ps1": "scripts/build/Build-IAGHostBridge.ps1",
    "tools/agent_runtime/install_ubuntu.sh": "scripts/deploy/install_ubuntu.sh",
    "tools/agent_runtime/install_systemd_service.sh": "scripts/deploy/install_systemd_service.sh",
    "tools/agent_runtime/start_console.sh": "scripts/deploy/start_console.sh",
    "tools/agent_runtime/deploy/systemd/iag-agent-console.service": "scripts/deploy/systemd/iag-agent-console.service",
    "tools/agent_runtime/deploy/systemd/iag-agent-network-observer.service": "scripts/deploy/systemd/iag-agent-network-observer.service",
    # Research services are optional infrastructure, never direct execution authority.
    "tools/research_services/compose.yaml": "services/research/compose.yaml",
    "tools/research_services/Manage-IAGResearchServices.ps1": "services/research/Manage-IAGResearchServices.ps1",
    "tools/research_services/searxng/settings.yml": "services/research/searxng/settings.yml",
    # Durable v0.5.8 evidence retained as reference, not as current architecture docs.
    "docs/TECHNICAL_OVERVIEW.md": "docs/reference/v0_5_8/TECHNICAL_OVERVIEW.md",
    "docs/RESEARCH_SERVICES.md": "docs/reference/v0_5_8/RESEARCH_SERVICES.md",
    "docs/STEAM_RELAY_HOTFIX.md": "docs/reference/v0_5_8/STEAM_RELAY_HOTFIX.md",
    "docs/UBUNTU_AGENT_README.md": "docs/reference/v0_5_8/UBUNTU_AGENT_README.md",
    "docs/WINDOWS_AGENT_README.md": "docs/reference/v0_5_8/WINDOWS_AGENT_README.md",
    "docs/WINDOWS_FULL_DEPLOYMENT.md": "docs/reference/v0_5_8/WINDOWS_FULL_DEPLOYMENT.md",
    "tools/agent_runtime/README.md": "docs/reference/v0_5_8/AGENT_RUNTIME.md",
    "tools/packet_interceptor/README.md": "docs/reference/v0_5_8/PACKET_INTERCEPTOR.md",
    "tools/windows_save_uploader/README.md": "docs/reference/v0_5_8/HOST_BRIDGE.md",
}


# 这些目录中的文件全部属于仍在使用的 Stellaris Mod；测试和缓存不在其中。
TREE_MAP: dict[str, str] = {
    "common": "stellaris_mod/common",
    "events": "stellaris_mod/events",
    "localisation": "stellaris_mod/localisation",
}


# 法律与项目来源文件保持在新仓库根目录，明确延续原项目归属。
ROOT_FILES = (
    "LICENSE",
    "NOTICE",
    "ORIGIN.md",
    "AUTHORS.md",
    "CITATION.cff",
    "CONTRIBUTING.md",
    "DCO",
    "SECURITY.md",
    "TRADEMARKS.md",
    "REUSE.toml",
    "LICENSES/CC-BY-SA-4.0.txt",
    "LICENSES/GPL-3.0-only.txt",
)


MOD_ROOT_FILES = ("descriptor.mod",)


def sha256(path: Path) -> str:
    """返回文件内容哈希，用于证明迁移副本来自哪个源文件。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_files(source: Path) -> set[str]:
    """读取 Git 跟踪清单，防止意外复制被忽略的本机运行数据。"""

    output = subprocess.check_output(
        ["git", "-C", str(source), "ls-files", "-z"]
    )
    return {item.decode("utf-8") for item in output.split(b"\0") if item}


def copy_one(
    source_root: Path,
    target_root: Path,
    source_relative: str,
    target_relative: str,
    tracked: set[str],
) -> dict[str, object]:
    """复制一个受 Git 跟踪的文件，并返回可审计的迁移记录。"""

    normalized_source = source_relative.replace("\\", "/")
    if normalized_source not in tracked:
        raise RuntimeError(f"源文件未受 Git 跟踪，拒绝复制：{normalized_source}")
    source_path = source_root / normalized_source
    target_path = target_root / target_relative
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return {
        "source": normalized_source,
        "target": target_relative.replace("\\", "/"),
        "source_sha256": sha256(source_path),
        "initial_copy_sha256": sha256(target_path),
        "bytes": source_path.stat().st_size,
    }


def source_commit(source: Path) -> str:
    """记录迁移基线提交；脏工作树由主流程直接拒绝。"""

    status = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain"], text=True
    )
    if status.strip():
        raise RuntimeError("源仓库存在未提交修改，无法建立可信迁移基线。")
    return subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--target", type=Path, default=Path(__file__).resolve().parents[2]
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    target = args.target.resolve()
    if source == target or source in target.parents:
        raise RuntimeError("目标目录不得位于源仓库内部。")

    commit = source_commit(source)
    tracked = tracked_files(source)
    records: list[dict[str, object]] = []

    for source_relative, target_relative in FILE_MAP.items():
        records.append(
            copy_one(source, target, source_relative, target_relative, tracked)
        )

    for source_prefix, target_prefix in TREE_MAP.items():
        for source_relative in sorted(tracked):
            if not source_relative.startswith(f"{source_prefix}/"):
                continue
            suffix = source_relative[len(source_prefix) + 1 :]
            records.append(
                copy_one(
                    source,
                    target,
                    source_relative,
                    f"{target_prefix}/{suffix}",
                    tracked,
                )
            )

    for source_relative in ROOT_FILES:
        records.append(
            copy_one(source, target, source_relative, source_relative, tracked)
        )
    for source_relative in MOD_ROOT_FILES:
        records.append(
            copy_one(
                source,
                target,
                source_relative,
                f"stellaris_mod/{source_relative}",
                tracked,
            )
        )

    manifest = {
        "schema": "iag.migration_manifest.v1",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"
        ),
        "source_root": "<LEGACY_REPOSITORY>",
        "source_commit": commit,
        "target_root": ".",
        "policy": {
            "source_is_read_only": True,
            "tests_copied": False,
            "runtime_data_copied": False,
            "release_archives_copied": False,
            "obsolete_packet_probes_copied": False,
        },
        "files": sorted(records, key=lambda item: str(item["target"])),
    }
    manifest_path = target / "MIGRATION_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Copied {len(records)} files from {commit}.")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
