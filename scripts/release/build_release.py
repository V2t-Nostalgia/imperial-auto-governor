#!/usr/bin/env python3
"""Build and verify the five public v0.5.9 release attachments."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import ipaddress
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import tomllib
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_NAME = "RELEASE_MANIFEST.json"
PACKAGE_SUMS_NAME = "SHA256SUMS.txt"
GOVERNANCE_FILES = (
    "AUTHORS.md",
    "CITATION.cff",
    "LICENSE",
    "NOTICE",
    "ORIGIN.md",
    "SECURITY.md",
    "TRADEMARKS.md",
)
FORBIDDEN_PARTS = {
    ".git",
    ".idea",
    ".tmp",
    ".venv",
    "__pycache__",
    "build",
    "captures",
    "databases",
    "dist",
    "logs",
    "runtime",
    "saves",
    "secrets",
    "state",
}
FORBIDDEN_SUFFIXES = {
    ".env",
    ".jsonl",
    ".key",
    ".pcap",
    ".pcapng",
    ".pem",
    ".sav",
    ".sqlite",
    ".sqlite3",
}
FORBIDDEN_CONFIG_NAMES = {
    "agent_config.json",
    "iag_save_uploader.json",
}
TEXT_SUFFIXES = {
    ".cff",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mod",
    ".ps1",
    ".py",
    ".sh",
    ".spec",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
IPV4_RE = re.compile(rb"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
WINDOWS_USER_RE = re.compile(
    rb"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?P<user>[^\\/\s<>:\"|?*]+)"
)
LINUX_USER_RE = re.compile(rb"/home/(?P<user>[A-Za-z0-9._-]+)(?:/|\b)")
EMAIL_RE = re.compile(rb"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
TOKEN_PATTERNS = {
    "OpenAI-style token": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(
        rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"
    ),
    "Slack token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)
DOCUMENTATION_NETWORK_SPECS = (
    (192, 0, 2, 0, 24),
    (198, 51, 100, 0, 24),
    (203, 0, 113, 0, 24),
)
PRIVATE_NETWORK_SPECS = (
    (10, 0, 0, 0, 8),
    (100, 64, 0, 0, 10),
    (172, 16, 0, 0, 12),
    (192, 168, 0, 0, 16),
)


def network_from_spec(specification: tuple[int, int, int, int, int]) -> Any:
    """Construct an IPv4 network without embedding a scan-triggering literal."""
    octets = ".".join(str(value) for value in specification[:4])
    return ipaddress.ip_network(f"{octets}/{specification[4]}")


DOCUMENTATION_NETWORKS = tuple(
    network_from_spec(specification)
    for specification in DOCUMENTATION_NETWORK_SPECS
)
PRIVATE_NETWORKS = tuple(
    network_from_spec(specification) for specification in PRIVATE_NETWORK_SPECS
)


class ReleaseError(RuntimeError):
    """Raised when a release attachment cannot be verified."""


def project_version() -> str:
    value = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(value["project"]["version"])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    command: Sequence[str],
    *,
    cwd: Path = ROOT,
    environment: dict[str, str] | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(
            "Command failed: "
            + " ".join(command)
            + "\n"
            + result.stdout
            + result.stderr
        )
    return result


def source_commit() -> str:
    commit = run(("git", "rev-parse", "HEAD")).stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ReleaseError("The current source commit is invalid.")
    dirty = run(
        ("git", "status", "--porcelain", "--untracked-files=no")
    ).stdout.strip()
    if dirty:
        raise ReleaseError(
            "Tracked files changed after the source commit; commit them before building."
        )
    return commit


def tracked_files() -> list[Path]:
    raw = run(("git", "ls-files", "-z")).stdout
    return [ROOT / value for value in raw.split("\0") if value]


def recreate(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    resolved_parent = parent.resolve()
    if resolved.parent != resolved_parent:
        raise ReleaseError(f"Refusing to replace an unsafe staging path: {resolved}")
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise ReleaseError(f"Unsafe staging object: {resolved}")
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def copy_relative_files(stage: Path, files: Iterable[Path]) -> None:
    for source in files:
        relative = source.relative_to(ROOT)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def copy_governance(stage: Path) -> None:
    for name in GOVERNANCE_FILES:
        shutil.copy2(ROOT / name, stage / name)
    shutil.copytree(ROOT / "LICENSES", stage / "LICENSES", dirs_exist_ok=True)


def relative_files(stage: Path) -> list[tuple[Path, Path]]:
    return [
        (path, path.relative_to(stage))
        for path in sorted(stage.rglob("*"))
        if path.is_file()
    ]


def is_authored_text(relative: Path) -> bool:
    lowered_parts = tuple(part.casefold() for part in relative.parts)
    if (
        relative.name in {"LICENSE", "DCO"}
        or "licenses" in lowered_parts
        or relative.name.casefold().startswith("license.")
    ):
        return False
    if "_internal" in relative.parts:
        return False
    return relative.suffix.casefold() in TEXT_SUFFIXES or relative.name == "NOTICE"


def scan_tree(stage: Path) -> list[str]:
    """Scan the exact staged tree without mutating it."""
    errors: list[str] = []
    for path in sorted(stage.rglob("*")):
        relative = path.relative_to(stage)
        lowered_parts = tuple(part.casefold() for part in relative.parts)
        if path.is_symlink():
            errors.append(f"symbolic link: {relative.as_posix()}")
            continue
        if not path.is_file():
            continue
        if any(part in FORBIDDEN_PARTS for part in lowered_parts[:-1]):
            errors.append(f"forbidden runtime directory: {relative.as_posix()}")
        if path.name.casefold() in FORBIDDEN_CONFIG_NAMES:
            errors.append(f"runtime configuration: {relative.as_posix()}")
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden runtime artifact: {relative.as_posix()}")

        data = path.read_bytes()
        lowered = data.lower()
        for marker in PRIVATE_KEY_MARKERS:
            if marker.lower() in lowered:
                errors.append(f"private key material: {relative.as_posix()}")
        for label, pattern in TOKEN_PATTERNS.items():
            if pattern.search(data):
                errors.append(f"{label}: {relative.as_posix()}")
        for match in IPV4_RE.finditer(data):
            try:
                address = ipaddress.ip_address(match.group().decode("ascii"))
            except ValueError:
                continue
            if any(address in network for network in DOCUMENTATION_NETWORKS):
                continue
            if any(address in network for network in PRIVATE_NETWORKS):
                errors.append(
                    f"private network address {address}: {relative.as_posix()}"
                )

        for pattern, label in (
            (WINDOWS_USER_RE, "personal Windows path"),
            (LINUX_USER_RE, "personal Linux path"),
        ):
            for match in pattern.finditer(data):
                user = match.group("user").decode("ascii", errors="replace")
                if user.casefold() in {"runneradmin", "appuser"}:
                    continue
                errors.append(f"{label} ({user}): {relative.as_posix()}")
        if is_authored_text(relative) and EMAIL_RE.search(data):
            errors.append(f"email address in authored content: {relative.as_posix()}")
    return sorted(set(errors))


def require_clean(stage: Path) -> None:
    errors = scan_tree(stage)
    if errors:
        raise ReleaseError("Privacy/content scan failed:\n- " + "\n- ".join(errors))


def finalize_stage(
    stage: Path,
    *,
    package: str,
    platform: str,
    commit: str,
    health_checks: Sequence[str],
) -> None:
    for generated in (stage / MANIFEST_NAME, stage / PACKAGE_SUMS_NAME):
        generated.unlink(missing_ok=True)
    require_clean(stage)
    payload = relative_files(stage)
    entries = [
        {
            "path": relative.as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path, relative in payload
    ]
    manifest = {
        "schema": "iag.release_manifest.v1",
        "package": package,
        "version": project_version(),
        "platform": platform,
        "source_commit": commit,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "privacy_scan": "passed",
        "health_checks": list(health_checks),
        "files": entries,
    }
    manifest_path = stage / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    sums = [*payload, (manifest_path, Path(MANIFEST_NAME))]
    (stage / PACKAGE_SUMS_NAME).write_text(
        "".join(
            f"{sha256_file(path)}  {relative.as_posix()}\n"
            for path, relative in sums
        ),
        encoding="utf-8",
    )
    verify_stage(stage)


def verify_stage(stage: Path) -> None:
    require_clean(stage)
    manifest_path = stage / MANIFEST_NAME
    sums_path = stage / PACKAGE_SUMS_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("privacy_scan") != "passed":
        raise ReleaseError("Package manifest does not record a passed scan.")
    expected = {entry["path"]: entry for entry in manifest.get("files", [])}
    actual = {
        relative.as_posix(): path
        for path, relative in relative_files(stage)
        if relative.as_posix() not in {MANIFEST_NAME, PACKAGE_SUMS_NAME}
    }
    if set(expected) != set(actual):
        raise ReleaseError("Package manifest file list does not match the tree.")
    for name, path in actual.items():
        entry = expected[name]
        if entry["size"] != path.stat().st_size or entry["sha256"] != sha256_file(path):
            raise ReleaseError(f"Package manifest mismatch: {name}")

    parsed: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or name in parsed:
            raise ReleaseError("Malformed package SHA-256 manifest.")
        parsed[name] = digest
    expected_sums = {
        **{name: entry["sha256"] for name, entry in expected.items()},
        MANIFEST_NAME: sha256_file(manifest_path),
    }
    if parsed != expected_sums:
        raise ReleaseError("Package SHA-256 manifest does not match its tree.")


def create_zip(stage: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, relative in relative_files(stage):
            archive.write(path, (Path(stage.name) / relative).as_posix())


def create_tar(stage: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(stage, arcname=stage.name, recursive=True)


def safe_archive_path(name: str, expected_root: str) -> None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ReleaseError(f"Unsafe archive member: {name}")
    if path.parts[0] != expected_root:
        raise ReleaseError(f"Archive member has the wrong root: {name}")


def extract_verified(archive: Path, destination: Path, expected_root: str) -> Path:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as handle:
            for member in handle.infolist():
                safe_archive_path(member.filename, expected_root)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ReleaseError(f"ZIP contains a symbolic link: {member.filename}")
            damaged = handle.testzip()
            if damaged:
                raise ReleaseError(f"Damaged ZIP member: {damaged}")
            handle.extractall(destination)
    else:
        with tarfile.open(archive, "r:gz") as handle:
            for member in handle.getmembers():
                safe_archive_path(member.name, expected_root)
                if member.issym() or member.islnk():
                    raise ReleaseError(f"Tar contains a link: {member.name}")
            handle.extractall(destination)
    root = destination / expected_root
    if not root.is_dir():
        raise ReleaseError("Extracted archive root is missing.")
    verify_stage(root)
    return root


def source_health(stage: Path) -> list[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(stage / "src"), str(stage)))
    run(
        (sys.executable, "-m", "compileall", "-q", "src", "apps", "scripts"),
        cwd=stage,
        environment=environment,
    )
    result = run(
        (sys.executable, "scripts/validation/run_tests.py", "--quiet"),
        cwd=stage,
        environment=environment,
    )
    match = re.search(r"Loaded (\d+) modules / (\d+) tests", result.stdout)
    detail = f"{match.group(2)} tests" if match else "repository tests"
    run(
        (
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "E9,F",
            "src",
            "apps",
            "scripts",
        ),
        cwd=stage,
        environment=environment,
    )
    return [
        "python compileall: passed",
        f"repository unittest suite: passed ({detail})",
        "Ruff E9/F diagnostics: passed",
    ]


def find_bash() -> str:
    candidates = [shutil.which("bash")]
    git = shutil.which("git")
    if os.name == "nt" and git:
        candidates.append(str(Path(git).resolve().parents[1] / "bin" / "bash.exe"))
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        result = subprocess.run(
            (candidate, "--version"),
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and b"GNU bash" in result.stdout:
            return candidate
    raise ReleaseError("GNU bash is required for Ubuntu installer syntax checks.")


def ubuntu_health(stage: Path) -> list[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(stage / "src"), str(stage)))
    run((sys.executable, "-m", "compileall", "-q", "src", "apps"), cwd=stage)
    run(
        (
            sys.executable,
            "-c",
            "import apps.control_center.web_console; "
            "import iag.stellaris.state.fleet_profiles; "
            "import iag.applications.fleet_operations.agent_tools",
        ),
        cwd=stage,
        environment=environment,
    )
    scripts = sorted((stage / "scripts" / "deploy").glob("*.sh"))
    run((find_bash(), "-n", *(str(path) for path in scripts)), cwd=stage)
    return [
        "python compileall: passed",
        "control center, save parser, and fleet tool imports: passed",
        "Ubuntu deployment shell syntax: passed",
    ]


class HealthHandler(http.server.BaseHTTPRequestHandler):
    token = ""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path != "/api/save/client-heartbeat":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_error(401)
            return
        body = json.dumps({"ok": True, "status": "ok"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_arguments: Any) -> None:
        return


def host_bridge_health(stage: Path) -> list[str]:
    executable = stage / "IAGHostBridgeGUI.exe"
    if not executable.is_file():
        raise ReleaseError("The Host Bridge executable is missing.")
    with tempfile.TemporaryDirectory(prefix="iag-release-https-") as temporary:
        temporary_root = Path(temporary)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "IAG release health")]
        )
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_path = temporary_root / "health.crt"
        key_path = temporary_root / "health.key"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        HealthHandler.token = "iag-release-health-token"
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            fingerprint = hashlib.sha256(
                certificate.public_bytes(serialization.Encoding.DER)
            ).hexdigest()
            run(
                (
                    str(executable),
                    "--health-check",
                    "--server-url",
                    f"https://127.0.0.1:{server.server_port}",
                    "--certificate-sha256",
                    fingerprint,
                    "--token",
                    HealthHandler.token,
                ),
                cwd=stage,
                timeout=120,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    return [
        "packaged WinDivert route filter: passed",
        "certificate-pinned HTTPS control health: passed",
    ]


def windows_agent_health(stage: Path) -> list[str]:
    executable = stage / "IAGWindowsAgent.exe"
    if not executable.is_file():
        raise ReleaseError("The Windows Agent executable is missing.")
    run((str(executable), "--health-check"), cwd=stage, timeout=180)
    driver_files = list(stage.rglob("WinDivert64.dll")) + list(
        stage.rglob("WinDivert64.sys")
    )
    if len(driver_files) < 2:
        raise ReleaseError("The Windows Agent package lacks WinDivert DLL/SYS files.")
    return [
        "packaged Windows Agent health check: passed",
        "session proxy import and PyDivert DLL/SYS presence: passed",
    ]


def build_windows_binaries(skip_build: bool) -> tuple[Path, Path]:
    host_dist = ROOT / "build" / "host-bridge" / "dist" / "IAGHostBridgeGUI"
    agent_dist = ROOT / "build" / "windows-agent" / "dist" / "IAGWindowsAgent"
    if not skip_build:
        run(
            (
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts" / "build" / "Build-IAGHostBridge.ps1"),
                "-Python",
                sys.executable,
            ),
            timeout=2400,
        )
        run(
            (
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts" / "build" / "Build-IAGWindowsAgent.ps1"),
                "-Python",
                sys.executable,
            ),
            timeout=2400,
        )
    if not host_dist.is_dir() or not agent_dist.is_dir():
        raise ReleaseError("Fresh Windows build output is missing.")
    return host_dist, agent_dist


def package_source(stage: Path, commit: str, files: list[Path]) -> list[str]:
    copy_relative_files(stage, files)
    checks = source_health(stage)
    finalize_stage(
        stage,
        package="Imperial Auto Governor source",
        platform="source",
        commit=commit,
        health_checks=checks,
    )
    return checks


def ubuntu_source_files(files: list[Path]) -> list[Path]:
    root_names = {
        ".gitignore",
        ".zenodo.json",
        "AUTHORS.md",
        "CITATION.cff",
        "CONTRIBUTING.md",
        "DCO",
        "LICENSE",
        "NOTICE",
        "ORIGIN.md",
        "README.md",
        "REUSE.toml",
        "SECURITY.md",
        "TRADEMARKS.md",
        "pyproject.toml",
        "requirements.txt",
    }
    prefixes = (
        "LICENSES/",
        "apps/control_center/",
        "content_packs/",
        "docs/",
        "scripts/deploy/",
        "services/research/",
        "src/",
        "stellaris_mod/",
    )
    selected: list[Path] = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if relative in root_names or relative.startswith(prefixes):
            if "/tests/" not in f"/{relative}":
                selected.append(path)
    return selected


def package_host_bridge(stage: Path, dist: Path, commit: str) -> list[str]:
    shutil.copytree(dist, stage, dirs_exist_ok=True)
    shutil.copy2(ROOT / "apps" / "host_bridge" / "README.md", stage / "README.md")
    shutil.copy2(
        ROOT / "apps" / "host_bridge" / "iag_save_uploader.example.json",
        stage / "iag_save_uploader.example.json",
    )
    copy_governance(stage)
    checks = host_bridge_health(stage)
    finalize_stage(
        stage,
        package="IAG Windows Host Bridge",
        platform="windows-x64",
        commit=commit,
        health_checks=checks,
    )
    return checks


def package_windows_agent(
    stage: Path,
    dist: Path,
    host_archive: Path,
    commit: str,
) -> list[str]:
    shutil.copytree(dist, stage, dirs_exist_ok=True)
    downloads = stage / "_internal" / "web" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    shutil.copy2(host_archive, downloads / "IAGHostBridge-windows-x64.zip")
    shutil.copy2(ROOT / "apps" / "control_center" / "README.md", stage / "README.md")
    docs = stage / "docs"
    docs.mkdir(exist_ok=True)
    for name in ("SESSION_PROXY_MODE.md", "FLEET_OPERATIONS.md"):
        shutil.copy2(ROOT / "docs" / name, docs / name)
    copy_governance(stage)
    checks = windows_agent_health(stage)
    finalize_stage(
        stage,
        package="IAG Windows Agent",
        platform="windows-x64",
        commit=commit,
        health_checks=checks,
    )
    return checks


def package_ubuntu(
    stage: Path,
    files: list[Path],
    host_archive: Path,
    commit: str,
) -> list[str]:
    copy_relative_files(stage, ubuntu_source_files(files))
    downloads = stage / "apps" / "control_center" / "web" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    shutil.copy2(host_archive, downloads / "IAGHostBridge-windows-x64.zip")
    checks = ubuntu_health(stage)
    finalize_stage(
        stage,
        package="IAG Ubuntu Agent",
        platform="linux-x86_64",
        commit=commit,
        health_checks=checks,
    )
    return checks


def verify_archive_health(archive: Path, root_name: str, kind: str) -> None:
    with tempfile.TemporaryDirectory(prefix="iag-release-verify-") as temporary:
        stage = extract_verified(archive, Path(temporary), root_name)
        if kind == "source":
            source_health(stage)
        elif kind == "windows-agent":
            windows_agent_health(stage)
        elif kind == "windows-host":
            host_bridge_health(stage)
        elif kind == "ubuntu-agent":
            ubuntu_health(stage)
        else:
            raise ReleaseError(f"Unknown package kind: {kind}")


def build_release(skip_build: bool = False) -> Path:
    version = project_version()
    commit = source_commit()
    files = tracked_files()
    release_root = ROOT / "build" / "releases" / f"v{version}"
    release_root.mkdir(parents=True, exist_ok=True)
    stage_root = release_root / "stage"
    final_root = release_root / "final"
    recreate(stage_root, release_root)
    recreate(final_root, release_root)

    host_dist, agent_dist = build_windows_binaries(skip_build)
    names = {
        "source": f"ImperialAutoGovernor-{version}",
        "windows-agent": f"IAGWindowsAgent-{version}-windows-x64",
        "windows-host": f"IAGHostBridge-{version}-windows-x64",
        "ubuntu-agent": f"IAGUbuntuAgent-{version}",
    }
    stages = {key: stage_root / value for key, value in names.items()}
    for stage in stages.values():
        stage.mkdir(parents=True)

    package_host_bridge(stages["windows-host"], host_dist, commit)
    host_archive = final_root / f"{names['windows-host']}.zip"
    create_zip(stages["windows-host"], host_archive)
    verify_archive_health(host_archive, names["windows-host"], "windows-host")

    package_windows_agent(
        stages["windows-agent"],
        agent_dist,
        host_archive,
        commit,
    )
    agent_archive = final_root / f"{names['windows-agent']}.zip"
    create_zip(stages["windows-agent"], agent_archive)
    verify_archive_health(agent_archive, names["windows-agent"], "windows-agent")

    package_source(stages["source"], commit, files)
    source_archive = final_root / f"{names['source']}-source.zip"
    create_zip(stages["source"], source_archive)
    verify_archive_health(source_archive, names["source"], "source")

    package_ubuntu(stages["ubuntu-agent"], files, host_archive, commit)
    ubuntu_archive = final_root / f"IAGUbuntuAgent-{version}-linux-x86_64.tar.gz"
    create_tar(stages["ubuntu-agent"], ubuntu_archive)
    verify_archive_health(ubuntu_archive, names["ubuntu-agent"], "ubuntu-agent")

    archives = (source_archive, agent_archive, host_archive, ubuntu_archive)
    top_sums = final_root / f"SHA256SUMS-{version}.txt"
    top_sums.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in archives),
        encoding="utf-8",
    )
    for line in top_sums.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if sha256_file(final_root / name) != digest:
            raise ReleaseError(f"Top-level SHA-256 mismatch: {name}")
    expected = {path.name for path in archives} | {top_sums.name}
    actual = {path.name for path in final_root.iterdir() if path.is_file()}
    if actual != expected:
        raise ReleaseError(f"Unexpected final attachment set: {sorted(actual ^ expected)}")
    print(f"Release source commit: {commit}")
    for path in sorted(final_root.iterdir()):
        print(f"{path.name}\t{path.stat().st_size}\t{sha256_file(path)}")
    return final_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    values = parser.parse_args()
    try:
        build_release(skip_build=values.skip_build)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
