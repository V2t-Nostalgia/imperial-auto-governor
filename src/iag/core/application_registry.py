"""第一方 Application 的显式注册表。

注册表不会扫描目录、Python entry point 或第三方代码。平台启动代码必须主动
登记受审查的 Application；Content Pack 只能声明自己依赖这些既有能力。
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field


class ApplicationManifest(BaseModel):
    """一个第一方领域 Application 对平台公开的静态能力。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    application_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    version: str
    display_name_zh: str
    description_zh: str
    agent_roles: tuple[str, ...]
    message_types: tuple[str, ...]
    action_types: tuple[str, ...]
    required_platform_capabilities: tuple[str, ...] = ()


class ApplicationRegistry:
    """保存当前进程明确启用的 Application，并拒绝身份冲突。"""

    def __init__(self, manifests: Iterable[ApplicationManifest] = ()) -> None:
        self._manifests: dict[str, ApplicationManifest] = {}
        for manifest in manifests:
            self.register(manifest)

    def register(self, manifest: ApplicationManifest) -> None:
        if manifest.application_id in self._manifests:
            raise ValueError(
                f"Application 已注册：{manifest.application_id}"
            )
        self._manifests[manifest.application_id] = manifest

    def get(self, application_id: str) -> ApplicationManifest:
        try:
            return self._manifests[application_id]
        except KeyError as error:
            raise KeyError(f"未知 Application：{application_id}") from error

    def all(self) -> tuple[ApplicationManifest, ...]:
        return tuple(self._manifests[key] for key in sorted(self._manifests))
