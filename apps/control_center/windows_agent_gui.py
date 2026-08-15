#!/usr/bin/env python3
"""One-click Windows launcher for the Imperial Auto Governor agent console."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


APP_TITLE = "灰风内政执行端 / IAG Windows Agent"
DEFAULT_RUNTIME_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ImperialAutoGovernor"
_SERVER_LOG_HANDLE: Any = None


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / name


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 必须包含 JSON 对象。")
    return value


def random_secret(length: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def ensure_text_secret(path: Path) -> str:
    try:
        current = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        current = ""
    if current:
        return current
    path.parent.mkdir(parents=True, exist_ok=True)
    current = random_secret()
    path.write_text(current + "\n", encoding="utf-8")
    return current


def certificate_fingerprint(path: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    return hashlib.sha256(certificate.public_bytes(Encoding.DER)).hexdigest().upper()


def local_ipv4_addresses() -> list[str]:
    values = {"127.0.0.1"}
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            values.add(str(item[4][0]))
    except OSError:
        pass
    return sorted(values, key=lambda item: tuple(int(part) for part in item.split(".")))


def ensure_certificate(certificate_path: Path, key_path: Path) -> str:
    if certificate_path.is_file() and key_path.is_file():
        return certificate_fingerprint(certificate_path)

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    certificate_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Imperial Auto Governor Local Console")]
    )
    alternative_names: list[x509.GeneralName] = [x509.DNSName("localhost")]
    for value in local_ipv4_addresses():
        alternative_names.append(x509.IPAddress(ipaddress.ip_address(value)))
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1825))
        .add_extension(x509.SubjectAlternativeName(alternative_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return certificate_fingerprint(certificate_path)


def discover_game_root() -> str:
    candidates: list[Path] = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            candidates.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
    except (ImportError, FileNotFoundError, OSError):
        pass
    candidates.extend(
        [
            Path(r"C:\Program Files (x86)\Steam"),
            Path(r"C:\Program Files\Steam"),
        ]
    )
    for steam_root in candidates:
        game_root = steam_root / "steamapps" / "common" / "Stellaris"
        if (game_root / "stellaris.exe").is_file():
            return str(game_root)
    return ""


def server_command(config_path: Path, host: str, port: int, cert: Path, key: Path) -> list[str]:
    arguments = [
        "--serve",
        "--config",
        str(config_path),
        "--host",
        host,
        "--port",
        str(port),
        "--tls-cert",
        str(cert),
        "--tls-key",
        str(key),
    ]
    if host not in {"127.0.0.1", "::1", "localhost"}:
        arguments.append("--allow-lan")
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), *arguments]


def run_server_mode(arguments: list[str]) -> int:
    global _SERVER_LOG_HANDLE
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--config", type=Path)
    known, remaining = parser.parse_known_args(arguments)
    if not known.serve:
        raise ValueError("Internal server mode was not selected.")
    # PyInstaller windowed applications intentionally start with stdout and
    # stderr set to None. Bind both to a durable log before web_console prints
    # or reports an exception.
    if sys.stdout is None or sys.stderr is None:
        runtime_root = DEFAULT_RUNTIME_ROOT
        if known.config and known.config.is_file():
            try:
                runtime_root = Path(str(read_json(known.config)["runtime_root"]))
            except (KeyError, ValueError, json.JSONDecodeError, OSError):
                pass
        log_path = runtime_root.expanduser() / "logs" / "windows_agent_server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _SERVER_LOG_HANDLE = log_path.open(
            "a",
            encoding="utf-8",
            buffering=1,
        )
        if sys.stdout is None:
            sys.stdout = _SERVER_LOG_HANDLE
        if sys.stderr is None:
            sys.stderr = _SERVER_LOG_HANDLE
    from apps.control_center import web_console

    original = sys.argv
    try:
        forwarded = list(remaining)
        if known.config is not None:
            forwarded = ["--config", str(known.config), *forwarded]
        sys.argv = ["web_console.py", *forwarded]
        return int(web_console.main())
    finally:
        sys.argv = original


def run_session_proxy_worker(arguments: list[str]) -> int:
    """Run the proxy inside the frozen Agent executable."""
    forwarded = list(arguments)
    forwarded.remove("--session-proxy-worker")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--log", type=Path)
    known, _remaining = parser.parse_known_args(forwarded)

    worker_log_handle: Any = None
    if sys.stdout is None or sys.stderr is None:
        log_path = (
            known.log.with_name("session_proxy_worker.log")
            if known.log is not None
            else DEFAULT_RUNTIME_ROOT / "logs" / "session_proxy_worker.log"
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        worker_log_handle = log_path.open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = worker_log_handle
        if sys.stderr is None:
            sys.stderr = worker_log_handle

    from iag.stellaris.execution import session_proxy

    original = sys.argv
    try:
        sys.argv = ["session_proxy.py", *forwarded]
        return int(session_proxy.run())
    finally:
        sys.argv = original
        if worker_log_handle is not None:
            worker_log_handle.close()


def run_health_check() -> int:
    """Verify bundled resources and the save parser import graph without starting UI."""
    from iag.core.paths import economy_governance_root, vanilla_content_pack_root
    from iag.infrastructure.llm.model_templates import TEMPLATES_PATH
    from iag.infrastructure.llm.runtime_config import RuntimeConfig

    required_resources = (
        resource_path("agent_config.windows.example.json"),
        resource_path("web/index.html"),
        vanilla_content_pack_root() / "mappings" / "capabilities.json",
        TEMPLATES_PATH,
        economy_governance_root()
        / "prompts"
        / "grey_tempest_conversation_zh.md",
    )
    for path in required_resources:
        if not path.is_file():
            raise RuntimeError(f"Missing bundled resource: {path}")

    if getattr(sys, "frozen", False):
        bridge = resource_path("web/downloads/IAGHostBridge-windows-x64.zip")
        if bridge.is_file():
            with zipfile.ZipFile(bridge) as archive:
                damaged = archive.testzip()
                if damaged:
                    raise RuntimeError(f"Damaged bundled Host Bridge member: {damaged}")

    from apps.control_center import web_console  # noqa: F401
    from iag.applications.economy_governance import planner  # noqa: F401
    from iag.stellaris.state import extract_game_state  # noqa: F401
    from iag.stellaris.execution import session_proxy  # noqa: F401
    from iag.stellaris.state import planet_profiles  # noqa: F401

    RuntimeConfig.load(resource_path("agent_config.windows.example.json")).snapshot()
    with tempfile.TemporaryDirectory(prefix="iag-legacy-config-health-") as value:
        root = Path(value)
        (root / "legacy_api_key").write_text(
            "packaged-health-secret\n",
            encoding="utf-8",
        )
        config_path = root / "agent_config.json"
        atomic_write_json(
            config_path,
            {
                "base_url": "https://legacy.example/v1",
                "model": "legacy-model",
                "provider": "chat_completions_compatible",
                "auth_mode": "bearer",
                "api_key_file": "legacy_api_key",
                "model_context_window_tokens": 128_000,
                "context_output_reserve_tokens": 8_192,
                "temperature": 0.2,
                "thinking": {"type": "enabled"},
                "runtime_root": str(root),
            },
        )
        legacy = RuntimeConfig.load(config_path).snapshot()
        if legacy.endpoint.model != "legacy-model":
            raise RuntimeError("Legacy runtime configuration migration failed.")
        if legacy.request_options.get("temperature") != 0.2:
            raise RuntimeError("Legacy request options were not migrated.")

    return 0


class Launcher:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("860x650")
        self.root.minsize(760, 580)
        self.root.configure(background="#071619")
        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.server_log_offset = 0

        self.runtime_var = tk.StringVar(value=str(DEFAULT_RUNTIME_ROOT))
        self.game_var = tk.StringVar(value=discover_game_root())
        self.host_var = tk.StringVar(value="0.0.0.0")
        self.port_var = tk.StringVar(value="8765")
        self.status_var = tk.StringVar(value="尚未启动")
        self.credentials_var = tk.StringVar(value="保存设置后生成连接凭据")

        self._style()
        self._layout()
        self._load_existing()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self._poll)

    def _style(self) -> None:
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#071619")
        style.configure("Card.TFrame", background="#0b2427", relief="solid", borderwidth=1)
        style.configure("TLabel", background="#071619", foreground="#b9d7d5", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", foreground="#65dfd0", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Gold.TLabel", foreground="#d9b95b", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", foreground="#062024", background="#65dfd0")
        style.map("Accent.TButton", background=[("active", "#91f0e4")])
        style.configure("TEntry", fieldbackground="#071619", foreground="#d7ecea", insertcolor="#d7ecea")

    def _layout(self) -> None:
        frame = self.ttk.Frame(self.root, padding=20)
        frame.pack(fill="both", expand=True)
        self.ttk.Label(frame, text="灰风内政执行端", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self.ttk.Label(frame, text="Windows co-op client · save analysis · guarded carrier click", style="Gold.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 18))

        labels = [
            ("运行数据目录", self.runtime_var, self._browse_runtime),
            ("Stellaris 安装目录", self.game_var, self._browse_game),
            ("监听地址", self.host_var, None),
            ("监听端口", self.port_var, None),
        ]
        for index, (label, variable, command) in enumerate(labels, start=2):
            self.ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=6)
            self.ttk.Entry(frame, textvariable=variable).grid(row=index, column=1, sticky="ew", padx=(12, 8), pady=6)
            if command:
                self.ttk.Button(frame, text="浏览", command=command).grid(row=index, column=2, sticky="ew", pady=6)

        button_row = self.ttk.Frame(frame)
        button_row.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(16, 10))
        self.ttk.Button(button_row, text="保存设置", command=self.save_settings).pack(side="left", padx=(0, 8))
        self.start_button = self.ttk.Button(button_row, text="启动灰风", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="left", padx=8)
        self.stop_button = self.ttk.Button(button_row, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.ttk.Button(button_row, text="打开控制台", command=self.open_console).pack(side="left", padx=8)

        self.ttk.Label(frame, textvariable=self.status_var, style="Gold.TLabel").grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 4))
        self.ttk.Label(frame, textvariable=self.credentials_var, wraplength=790).grid(row=8, column=0, columnspan=3, sticky="w", pady=(0, 10))

        credential_row = self.ttk.Frame(frame)
        credential_row.grid(row=9, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self.ttk.Button(credential_row, text="复制前端密码", command=lambda: self._copy_secret("frontend_password")).pack(side="left", padx=(0, 8))
        self.ttk.Button(credential_row, text="复制桥接令牌", command=lambda: self._copy_secret("save_upload_token")).pack(side="left", padx=8)
        self.ttk.Button(credential_row, text="复制证书指纹", command=self._copy_fingerprint).pack(side="left", padx=8)

        self.log = self.tk.Text(
            frame,
            height=16,
            background="#041012",
            foreground="#9fc8c4",
            insertbackground="#9fc8c4",
            relief="flat",
            font=("Cascadia Mono", 9),
            state="disabled",
        )
        self.log.grid(row=10, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(10, weight=1)

    @property
    def runtime_root(self) -> Path:
        return Path(self.runtime_var.get()).expanduser().resolve()

    @property
    def config_path(self) -> Path:
        return self.runtime_root / "agent_config.json"

    def _browse_runtime(self) -> None:
        value = self.filedialog.askdirectory(initialdir=str(self.runtime_root.parent))
        if value:
            self.runtime_var.set(value)
            self._load_existing()

    def _browse_game(self) -> None:
        value = self.filedialog.askdirectory(initialdir=self.game_var.get() or str(Path.home()))
        if value:
            self.game_var.set(value)

    def _load_existing(self) -> None:
        try:
            config = read_json(self.config_path)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return
        self.game_var.set(str(config.get("game_root", self.game_var.get())))
        self.host_var.set(str(config.get("frontend_host", "0.0.0.0")))
        self.port_var.set(str(config.get("frontend_port", 8765)))
        self._refresh_credentials(config)

    def _config_template(self) -> dict[str, Any]:
        if self.config_path.is_file():
            return read_json(self.config_path)
        template = resource_path("agent_config.windows.example.json")
        if not template.is_file():
            template = Path(__file__).resolve().parent / "agent_config.windows.example.json"
        return read_json(template)

    def save_settings(self) -> dict[str, Any] | None:
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError("监听端口必须在 1 到 65535 之间。")
            host = self.host_var.get().strip()
            if not host:
                raise ValueError("监听地址不能为空。")
            game_root = Path(self.game_var.get()).expanduser()
            if not (game_root / "stellaris.exe").is_file():
                raise ValueError("Stellaris 安装目录中没有 stellaris.exe。")
            runtime_root = self.runtime_root
            runtime_root.mkdir(parents=True, exist_ok=True)
            config = self._config_template()
            config.update(
                {
                    "runtime_root": str(runtime_root),
                    "game_root": str(game_root.resolve()),
                    "frontend_host": host,
                    "frontend_port": port,
                    "execution_transport": "windows_host_bridge",
                    "save_source_mode": "host_upload",
                }
            )
            secrets_root = runtime_root / "secrets"
            ensure_text_secret(secrets_root / "frontend_password")
            ensure_text_secret(secrets_root / "save_upload_token")
            ensure_text_secret(secrets_root / "overlay_access_token")
            ensure_certificate(
                secrets_root / "frontend_tls.crt",
                secrets_root / "frontend_tls.key",
            )
            atomic_write_json(self.config_path, config)
            self._refresh_credentials(config)
            self._append("设置已写入：" + str(self.config_path))
            self.status_var.set("配置就绪")
            return config
        except Exception as error:
            self.messagebox.showerror(APP_TITLE, str(error))
            return None

    def _refresh_credentials(self, config: dict[str, Any]) -> None:
        secrets_root = self.runtime_root / "secrets"
        certificate = secrets_root / "frontend_tls.crt"
        fingerprint = certificate_fingerprint(certificate) if certificate.is_file() else "未生成"
        addresses = ", ".join(local_ipv4_addresses())
        self.credentials_var.set(
            f"用户 iag · 本机 IPv4 {addresses} · 证书 SHA-256 {fingerprint}"
        )

    def _copy(self, value: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.status_var.set("已复制到剪贴板")

    def _copy_secret(self, name: str) -> None:
        try:
            self._copy((self.runtime_root / "secrets" / name).read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            self.messagebox.showerror(APP_TITLE, "请先保存设置。")

    def _copy_fingerprint(self) -> None:
        try:
            self._copy(certificate_fingerprint(self.runtime_root / "secrets" / "frontend_tls.crt"))
        except FileNotFoundError:
            self.messagebox.showerror(APP_TITLE, "请先保存设置。")

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        config = self.save_settings()
        if config is None:
            return
        cert = self.runtime_root / "secrets" / "frontend_tls.crt"
        key = self.runtime_root / "secrets" / "frontend_tls.key"
        command = server_command(
            self.config_path,
            str(config["frontend_host"]),
            int(config["frontend_port"]),
            cert,
            key,
        )
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        server_log = self.runtime_root / "logs" / "windows_agent_server.log"
        self.server_log_offset = server_log.stat().st_size if server_log.is_file() else 0
        self.process = subprocess.Popen(
            command,
            cwd=str(self.runtime_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read_output, daemon=True).start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("正在启动控制台…")
        self._append("启动命令：" + " ".join(command))

    def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self.output_queue.put(line.rstrip())

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        self.process = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_var.set("已停止")
        self._append("控制台已停止。")

    def open_console(self) -> None:
        try:
            port = int(self.port_var.get())
        except ValueError:
            self.messagebox.showerror(APP_TITLE, "监听端口无效。")
            return
        webbrowser.open(f"https://127.0.0.1:{port}/")

    def _append(self, line: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{timestamp}] {line}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _read_server_log(self) -> None:
        path = self.runtime_root / "logs" / "windows_agent_server.log"
        if not path.is_file():
            return
        size = path.stat().st_size
        if size < self.server_log_offset:
            self.server_log_offset = 0
        if size == self.server_log_offset:
            return
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self.server_log_offset)
            for line in handle:
                self._append(line.rstrip())
            self.server_log_offset = handle.tell()

    def _poll(self) -> None:
        while True:
            try:
                self._append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        self._read_server_log()
        if self.process is not None:
            code = self.process.poll()
            if code is None:
                self.status_var.set("控制台运行中")
            else:
                self._append(f"控制台进程已退出，代码 {code}。")
                self.process = None
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.status_var.set(f"启动失败或已退出（{code}）")
        self.root.after(250, self._poll)

    def close(self) -> None:
        self.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if "--health-check" in sys.argv[1:]:
        return run_health_check()
    if "--session-proxy-worker" in sys.argv[1:]:
        return run_session_proxy_worker(sys.argv[1:])
    if "--serve" in sys.argv[1:]:
        return run_server_mode(sys.argv[1:])
    if os.name != "nt":
        raise SystemExit("The Windows launcher can only run on Windows.")
    Launcher().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
