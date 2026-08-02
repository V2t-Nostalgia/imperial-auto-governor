#!/usr/bin/env python3
"""Manual Windows GUI for the IAG Stellaris host save uploader."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, W, X, filedialog, messagebox
from tkinter import scrolledtext
from tkinter import ttk
from urllib.parse import urlparse

from iag_host_interceptor import is_windows_admin
from iag_save_uploader import (
    PinnedHTTPSUploader,
    SaveUploader,
    UploaderError,
    atomic_write_json,
    default_save_root,
)


APP_VERSION = "2026.08.02-hostbridge8"
APP_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
DEFAULT_CONFIG_PATH = APP_ROOT / "iag_save_uploader.json"


def run_health_check(arguments: list[str]) -> int:
    """Exercise certificate-pinned HTTPS without opening WinDivert or the GUI."""
    parser = argparse.ArgumentParser(prog="IAGHostBridgeGUI --health-check")
    parser.add_argument("--health-check", action="store_true", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--certificate-sha256", required=True)
    parser.add_argument("--token", required=True)
    values = parser.parse_args(arguments)
    client = PinnedHTTPSUploader(
        values.server_url,
        values.certificate_sha256,
        values.token,
        timeout_seconds=10,
    )
    response = client.heartbeat(
        {
            "client_version": APP_VERSION,
            "health_check": True,
            "capabilities": ["host_inbound_rewrite_v1"],
        }
    )
    if response.get("ok") is not True or response.get("status") != "ok":
        raise UploaderError(f"HTTPS health endpoint returned an invalid response: {response}")
    return 0


def relaunch_as_administrator() -> bool:
    """Return true in the elevated process; otherwise start it and return false."""
    if os.name != "nt" or is_windows_admin():
        return True
    if getattr(sys, "frozen", False):
        executable = sys.executable
        arguments = sys.argv[1:]
    else:
        executable = sys.executable
        arguments = [str(Path(__file__).resolve()), *sys.argv[1:]]
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        subprocess.list2cmdline(arguments),
        str(APP_ROOT),
        1,
    )
    if result <= 32:
        raise UploaderError(f"无法请求管理员权限，ShellExecuteW 返回 {result}。")
    return False


def default_config() -> dict:
    """Return a writable first-run config without embedding connection secrets."""
    return {
        "server_url": "",
        "server_certificate_sha256": "",
        "upload_token_file": "secrets/save_upload_token",
        "save_root": "",
        "poll_seconds": 2,
        "stability_seconds": 5,
        "maximum_initial_save_age_seconds": 7200,
        "bootstrap_latest": True,
        "timeout_seconds": 180,
        "state_file": "state/uploader_state.json",
        "log_file": "logs/save_uploader.jsonl",
        "heartbeat_seconds": 5,
    }


def normalize_server_url(value: str) -> str:
    text = value.strip().rstrip("/")
    if not text:
        raise UploaderError("请填写 LLM 端 IP 地址或 HTTPS URL。")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise UploaderError("LLM 端地址必须是 IP、主机名或 https:// URL。")
    if parsed.port is None:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        path = parsed.path.rstrip("/")
        return f"https://{host}:8765{path}"
    return text


def display_server_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme.lower() == "https" and parsed.hostname:
        port = parsed.port or 443
        path = parsed.path.rstrip("/")
        return f"{parsed.hostname}:{port}{path}"
    return value


def read_config(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        value = default_config()
        atomic_write_json(path, value)
    except json.JSONDecodeError as error:
        raise UploaderError(f"配置文件不是有效 JSON：{error}") from error
    if not isinstance(value, dict):
        raise UploaderError("上传器配置必须是 JSON 对象。")
    return value


def resolve_relative(config_path: Path, value: str, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else config_path.parent / path


class UploaderGUI:
    def __init__(self, config_path: Path):
        import tkinter as tk

        self.tk = tk
        self.config_path = config_path.resolve()
        self.config = read_config(self.config_path)
        self.root = tk.Tk()
        self.root.title("灰风 · 房主执行桥")
        self.root.geometry("790x610")
        self.root.minsize(720, 540)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.events: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event: threading.Event | None = None
        self.close_pending = False
        self.close_wait_ticks = 0
        self.last_connection_event = ""

        self.server_var = tk.StringVar(
            value=display_server_url(str(self.config.get("server_url", "")))
        )
        configured_root = str(self.config.get("save_root", "")).strip()
        self.save_root_var = tk.StringVar(
            value=configured_root or str(default_save_root())
        )
        self.fingerprint_var = tk.StringVar(
            value=str(self.config.get("server_certificate_sha256", ""))
        )
        self.status_var = tk.StringVar(value="已停止")
        self.connection_var = tk.StringVar(value="尚未连接 LLM 端")
        self.bridge_var = tk.StringVar(
            value=(
                "WinDivert 已提权，可接收执行清单"
                if is_windows_admin()
                else "未以管理员身份运行，仅上传存档"
            )
        )
        self.campaign_var = tk.StringVar(value="--")
        self.last_upload_var = tk.StringVar(value="--")
        self.last_game_date_var = tk.StringVar(value="--")

        self._build_ui()
        self._refresh_state_labels()
        self.root.after(150, self._poll_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill=BOTH, expand=True)

        title = ttk.Label(
            outer,
            text="灰风 · 房主执行桥",
            font=("Microsoft YaHei UI", 18, "bold"),
        )
        title.pack(anchor=W)
        ttk.Label(
            outer,
            text="手动启动，窗口关闭即停止上传；不会创建计划任务，也不会开机自启。",
            foreground="#5f6f7a",
        ).pack(anchor=W, pady=(2, 14))

        settings = ttk.LabelFrame(outer, text="连接设置", padding=12)
        settings.pack(fill=X)
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="LLM 端地址").grid(row=0, column=0, sticky=W, padx=(0, 10), pady=5)
        ttk.Entry(settings, textvariable=self.server_var).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(settings, text="填写 <AGENT_IP>、主机名或完整 HTTPS URL；未写端口时使用 8765。", foreground="#6a7880").grid(row=1, column=1, sticky=W, pady=(0, 5))

        ttk.Label(settings, text="Stellaris 存档目录").grid(row=2, column=0, sticky=W, padx=(0, 10), pady=5)
        ttk.Entry(settings, textvariable=self.save_root_var).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Button(settings, text="浏览…", command=self.browse_save_root).grid(row=2, column=2, padx=(8, 0), pady=5)

        settings_buttons = ttk.Frame(settings)
        settings_buttons.grid(row=3, column=1, columnspan=2, sticky=W, pady=(8, 0))
        ttk.Button(settings_buttons, text="保存设置", command=self.save_settings).pack(side=LEFT)
        ttk.Button(settings_buttons, text="高级设置", command=self.open_advanced).pack(side=LEFT, padx=(8, 0))
        ttk.Button(settings_buttons, text="打开日志目录", command=self.open_logs).pack(side=LEFT, padx=(8, 0))

        status = ttk.LabelFrame(outer, text="运行状态", padding=12)
        status.pack(fill=X, pady=(12, 0))
        status.columnconfigure(1, weight=1)
        rows = (
            ("上传器", self.status_var),
            ("LLM 连接", self.connection_var),
            ("房主入站改写", self.bridge_var),
            ("当前战役", self.campaign_var),
            ("最后游戏日期", self.last_game_date_var),
            ("最后成功上传", self.last_upload_var),
        )
        for row, (label, variable) in enumerate(rows):
            ttk.Label(status, text=label).grid(row=row, column=0, sticky=W, padx=(0, 12), pady=3)
            ttk.Label(status, textvariable=variable).grid(row=row, column=1, sticky=W, pady=3)

        controls = ttk.Frame(outer)
        controls.pack(fill=X, pady=12)
        self.start_button = ttk.Button(controls, text="开始上传", command=self.start)
        self.start_button.pack(side=LEFT)
        self.stop_button = ttk.Button(controls, text="停止上传", command=self.stop, state="disabled")
        self.stop_button.pack(side=LEFT, padx=(8, 0))
        self.reset_button = ttk.Button(controls, text="重新选择战役", command=self.reset_campaign)
        self.reset_button.pack(side=LEFT, padx=(8, 0))
        ttk.Button(controls, text="关闭", command=self.close).pack(side=RIGHT)

        log_frame = ttk.LabelFrame(outer, text="本次运行日志", padding=8)
        log_frame.pack(fill=BOTH, expand=True)
        self.log_box = scrolledtext.ScrolledText(
            log_frame,
            height=12,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_box.pack(fill=BOTH, expand=True)
        self.append_log("GUI 已就绪。只有点击“开始上传”后才会建立连接。")

    def append_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert(END, text.rstrip() + "\n")
        self.log_box.see(END)
        self.log_box.configure(state="disabled")

    def browse_save_root(self) -> None:
        selected = filedialog.askdirectory(
            title="选择 Stellaris save games 目录",
            initialdir=self.save_root_var.get() or str(default_save_root()),
        )
        if selected:
            self.save_root_var.set(selected)

    def open_advanced(self) -> None:
        window = self.tk.Toplevel(self.root)
        window.title("高级连接设置")
        window.geometry("710x190")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill=BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="TLS 证书 SHA-256").grid(row=0, column=0, sticky=W, padx=(0, 10), pady=5)
        ttk.Entry(frame, textvariable=self.fingerprint_var).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(
            frame,
            text="更改同一台 LLM 主机的局域网 IP 通常无需修改指纹。更换服务器时必须同步新的指纹和上传令牌。",
            foreground="#6a7880",
            wraplength=650,
        ).grid(row=1, column=0, columnspan=2, sticky=W, pady=(4, 12))
        ttk.Button(frame, text="保存并关闭", command=lambda: (self.save_settings(silent=True), window.destroy())).grid(row=2, column=1, sticky="e")

    def save_settings(self, silent: bool = False) -> bool:
        try:
            if self.worker and self.worker.is_alive():
                raise UploaderError("请先停止上传，再修改连接设置。")
            server_url = normalize_server_url(self.server_var.get())
            save_root = Path(self.save_root_var.get().strip()).expanduser()
            if not save_root.is_dir():
                raise UploaderError(f"Stellaris 存档目录不存在：{save_root}")
            fingerprint = self.fingerprint_var.get().replace(":", "").strip().lower()
            if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
                raise UploaderError("TLS 证书 SHA-256 指纹必须是 64 位十六进制。")
            self.config["server_url"] = server_url
            self.config["save_root"] = str(save_root.resolve())
            self.config["server_certificate_sha256"] = fingerprint
            self.config.setdefault("heartbeat_seconds", 5)
            atomic_write_json(self.config_path, self.config)
            self.server_var.set(display_server_url(server_url))
            self.append_log(f"设置已保存：{server_url}")
            if not silent:
                messagebox.showinfo("灰风存档上传器", "设置已保存。")
            return True
        except Exception as error:
            messagebox.showerror("无法保存设置", str(error))
            return False

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.save_settings(silent=True):
            return
        try:
            uploader = SaveUploader(self.config_path)
        except Exception as error:
            messagebox.showerror("无法启动上传器", str(error))
            return
        self.stop_event = threading.Event()
        self.status_var.set("正在启动")
        self.connection_var.set("正在连接 LLM 端…")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.reset_button.configure(state="disabled")
        self.append_log("开始监控房主 autosave。")

        def worker() -> None:
            try:
                uploader.run(
                    stop_event=self.stop_event,
                    status_callback=self.events.put,
                )
            except Exception as error:
                self.events.put({"event": "fatal_error", "error": str(error)})

        self.worker = threading.Thread(target=worker, name="iag-save-uploader", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        if self.stop_event is not None:
            self.status_var.set("正在停止")
            self.stop_button.configure(state="disabled")
            self.stop_event.set()
            self.append_log("已请求停止；不会再扫描或上传新存档。")

    def reset_campaign(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("请先停止", "停止上传后才能重新选择战役。")
            return
        if not messagebox.askyesno("重新选择战役", "清除当前战役锁定，让上传器在下次启动时选择最新活跃战役？"):
            return
        try:
            uploader = SaveUploader(self.config_path)
            uploader.reset_campaign()
            self._refresh_state_labels()
            self.append_log("战役锁定已清除。")
        except Exception as error:
            messagebox.showerror("无法重置战役", str(error))

    def open_logs(self) -> None:
        try:
            value = str(self.config.get("log_file", "logs/save_uploader.jsonl"))
            log_path = resolve_relative(
                self.config_path,
                value,
                self.config_path.parent / "logs" / "save_uploader.jsonl",
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            os.startfile(log_path.parent)
        except Exception as error:
            messagebox.showerror("无法打开日志目录", str(error))

    def _refresh_state_labels(self) -> None:
        try:
            value = str(self.config.get("state_file", "state/uploader_state.json"))
            state_path = resolve_relative(
                self.config_path,
                value,
                self.config_path.parent / "state" / "uploader_state.json",
            )
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            state = {}
        self.campaign_var.set(str(state.get("campaign_label") or "--"))
        self.last_game_date_var.set(str(state.get("last_game_date") or "--"))
        self.last_upload_var.set(str(state.get("last_success_at") or "--"))

    def _handle_event(self, value: dict) -> None:
        event = str(value.get("event", ""))
        if event == "started":
            self.status_var.set("运行中")
        elif event == "connected":
            self.status_var.set("运行中")
            source_ip = value.get("source_ip")
            self.connection_var.set(
                f"已连接 · LLM 端识别本机为 {source_ip}"
                if source_ip
                else "已连接 LLM 端"
            )
            if value.get("host_bridge_elevated") is True:
                self.bridge_var.set("WinDivert 已提权，等待建设清单")
            else:
                self.bridge_var.set("未以管理员身份运行，仅上传存档")
            if self.last_connection_event != "connected":
                self.append_log(self.connection_var.get())
            self.last_connection_event = "connected"
        elif event == "connection_error":
            self.connection_var.set("连接失败，将自动重试")
            error = str(value.get("error", "未知错误"))
            if self.last_connection_event != error:
                self.append_log("连接错误：" + error)
            self.last_connection_event = error
        elif event == "host_executor_started":
            self.bridge_var.set("已收到清单，正在武装房主入站拦截器")
            self.append_log(
                "房主执行桥开始：" + str(value.get("request_id") or "未知请求")
            )
        elif event == "host_interceptor_ready":
            self.bridge_var.set("READY · 已打开 WinDivert，等待载体点击")
            self.append_log("房主入站拦截器 READY。")
        elif event == "host_inbound_carrier_rewritten":
            self.bridge_var.set("已等长改写，等待房主权威广播")
            self.append_log("已拦截并改写合作端载体命令。")
        elif event == "host_authoritative_command_seen":
            self.bridge_var.set("房主权威结果已确认")
            self.append_log("房主权威建设广播已确认。")
        elif event == "host_executor_completed":
            if value.get("success"):
                self.bridge_var.set("执行成功 · 等待下一份清单")
            else:
                self.bridge_var.set("执行失败 · 未自动重试")
                self.append_log("房主执行失败：" + str(value.get("error") or "未确认"))
        elif event == "host_executor_failed":
            self.bridge_var.set("执行桥错误 · 未点击或未重试")
            self.append_log("房主执行桥错误：" + str(value.get("error") or "未知错误"))
        elif event == "cycle_error":
            self.append_log("扫描错误：" + str(value.get("error", "未知错误")))
        elif event == "upload_succeeded":
            self.append_log(
                "上传成功："
                + str(value.get("campaign_label") or "未知战役")
                + " · "
                + str(value.get("game_date") or "未知日期")
            )
            self._refresh_state_labels()
        elif event == "fatal_error":
            self.status_var.set("异常停止")
            self.connection_var.set(str(value.get("error", "未知错误")))
            self.append_log("上传器异常停止：" + self.connection_var.get())
            self._finish_stopped()
        elif event == "stopped":
            self.append_log("上传已停止。")
            self._finish_stopped()

    def _finish_stopped(self) -> None:
        self.status_var.set("已停止")
        self.connection_var.set("未连接")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.reset_button.configure(state="normal")
        self.worker = None
        self.stop_event = None
        self.last_connection_event = ""
        if self.close_pending:
            self.root.destroy()

    def _poll_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        if self.close_pending and self.worker and self.worker.is_alive():
            self.close_wait_ticks += 1
            if self.close_wait_ticks >= 20:
                self.root.destroy()
                return
        if self.root.winfo_exists():
            self.root.after(150, self._poll_events)

    def close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.close_pending = True
            self.close_wait_ticks = 0
            self.status_var.set("正在停止并关闭")
            if self.stop_event is not None:
                self.stop_event.set()
            return
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    try:
        if "--health-check" in sys.argv[1:]:
            return run_health_check(sys.argv[1:])
        if not relaunch_as_administrator():
            return 0
        return UploaderGUI(DEFAULT_CONFIG_PATH).run()
    except Exception as error:
        try:
            import tkinter.messagebox as messagebox_module

            messagebox_module.showerror("灰风存档上传器", str(error))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
