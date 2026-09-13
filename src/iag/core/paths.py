"""集中解析开发工作区中的平台资源路径。

当前工程化迁移仍以可编辑安装运行。所有跨目录资源都通过本模块定位，避免业务
模块使用脆弱的 ``parents[n]``。未来打包为 wheel 时可以在这里统一切换到
``importlib.resources``，无需再次修改规划器。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    """返回仓库根目录，允许打包器通过 ``IAG_PROJECT_ROOT`` 显式覆盖。"""

    configured = os.environ.get("IAG_PROJECT_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).resolve()
    return Path(__file__).resolve().parents[3]


def vanilla_content_pack_root() -> Path:
    """返回第一版原版 4.4 Content Pack 的资源目录。"""

    return project_root() / "content_packs" / "vanilla_4_4"


def application_root(application_id: str) -> Path:
    """Locate one built-in Application's resources in source and frozen builds."""
    relative = Path("iag") / "applications" / application_id
    if getattr(sys, "_MEIPASS", None):
        return project_root() / relative
    return project_root() / "src" / relative


def economy_governance_root() -> Path:
    """Locate economy-governance resources in source and frozen builds."""
    return application_root("economy_governance")


def fleet_operations_root() -> Path:
    """Locate fleet-operations resources in source and frozen builds."""
    return application_root("fleet_operations")


def research_strategy_root() -> Path:
    """Locate research-strategy resources in source and frozen builds."""
    return application_root("research_strategy")
