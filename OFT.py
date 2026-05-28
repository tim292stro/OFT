#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2026 Tim Strommen
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Optical File Transfer (OFT), send and receive files optically via QR code."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import importlib
from importlib import metadata as importlib_metadata
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Callable, Iterable, cast

cv2: Any = None
zxingcpp: Any = None
Image: Any = None
QrCode: Any = None
QrSegment: Any = None

OFT_VERSION = "1.0.0"


MAX_CHUNK_BYTES = 1273
CHUNK_MAGIC = b"QRC1"
CHUNK_VERSION = 1
CHUNK_HEADER_STRUCT = struct.Struct(">4sBIIHI")

SPLIT_PAYLOAD_BYTES = MAX_CHUNK_BYTES - CHUNK_HEADER_STRUCT.size
QR_VERSION = 40
QR_BORDER_MODULES = 2
QR_SIZE_PX = 724
FRAMES_PER_QR = 6
FPS = 30

DEFAULT_7ZIP_CANDIDATES = ("7z", "7zz", "7za")
DEFAULT_FFMPEG_CANDIDATES = ("ffmpeg",)

WATCHDOG_HEARTBEAT_SEC = 5.0
WATCHDOG_WARN_OUTPUT_STALL_SEC = 20.0
WATCHDOG_WARN_PROGRESS_STALL_SEC = 45.0
WATCHDOG_ABORT_OUTPUT_STALL_SEC = 120.0
WATCHDOG_ABORT_PROGRESS_STALL_SEC = 300.0
WATCHDOG_MAX_RENDER_FACTOR = 8.0
WATCHDOG_MAX_RENDER_OVERHEAD_SEC = 180.0

STATUS_MIN_COLUMNS = 40
STATUS_MAX_COLUMNS = 80
DISK_FLUSH_DELAY_SEC = 2.0

PERSISTENCE_FILE = Path(__file__).parent / "OFT_persistence.json"
PERSISTENT_KEYS: tuple[str, ...] = ("sevenzip_bin", "ffmpeg_bin", "qr_workers")

DEPENDENCY_REQUIREMENTS = {
    "cv2": "opencv-python>=4.9.0",
    "zxingcpp": "zxing-cpp>=2.2.0",
    "PIL": "Pillow>=10.0.0",
    "qrcodegen": "qrcodegen>=1.8.0",
}

DEPENDENCY_DISTRIBUTIONS = ("opencv-python", "zxing-cpp", "Pillow", "qrcodegen")
THIRD_PARTY_NOTICES_FILE = Path(__file__).parent / "THIRD_PARTY_NOTICES.txt"


def _resolve_declared_license_paths(
    dist: importlib_metadata.Distribution,
    license_files: list[str],
) -> list[Path]:
    resolved: list[Path] = []
    seen: set[str] = set()
    for rel in license_files:
        rel_norm = rel.replace("\\", "/")
        matched_any = False
        for file_entry in dist.files or []:
            entry_norm = str(file_entry).replace("\\", "/")
            if entry_norm.endswith(rel_norm):
                path_obj = Path(str(dist.locate_file(file_entry)))
                path_key = str(path_obj)
                if path_key not in seen:
                    resolved.append(path_obj)
                    seen.add(path_key)
                matched_any = True
        if matched_any:
            continue
        try:
            path_obj = Path(str(dist.locate_file(rel)))
            path_key = str(path_obj)
            if path_key not in seen:
                resolved.append(path_obj)
                seen.add(path_key)
        except Exception:  # noqa: BLE001
            continue
    return resolved


def _infer_license_from_files(paths: list[Path]) -> tuple[str | None, list[str], Path | None]:
    chunks: list[tuple[str, Path]] = []
    for path_obj in paths:
        if not path_obj.exists() or not path_obj.is_file():
            continue
        try:
            text = path_obj.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunks.append((text[:12000].lower(), path_obj))

    if not chunks:
        return None, [], None

    for snippet, source_path in chunks:
        if "apache license" in snippet and "version 2.0" in snippet:
            return "Apache-2.0 (inferred from license file)", [
                "License :: OSI Approved :: Apache Software License"
            ], source_path
        if "mit-cmu license" in snippet:
            return "MIT-CMU (inferred from license file)", [
                "License :: OSI Approved :: MIT License"
            ], source_path
        if "mit license" in snippet:
            return "MIT (inferred from license file)", [
                "License :: OSI Approved :: MIT License"
            ], source_path

    corpus = "\n".join(snippet for snippet, _ in chunks)
    if "apache license" in corpus and "version 2.0" in corpus:
        return "Apache-2.0 (inferred from license file)", [
            "License :: OSI Approved :: Apache Software License"
        ], chunks[0][1]
    if "mit-cmu license" in corpus:
        return "MIT-CMU (inferred from license file)", [
            "License :: OSI Approved :: MIT License"
        ], chunks[0][1]
    if "mit license" in corpus:
        return "MIT (inferred from license file)", [
            "License :: OSI Approved :: MIT License"
        ], chunks[0][1]
    return None, [], None


def _build_dependency_license_report() -> str:
    lines: list[str] = []
    lines.append(f"OFT Dependency License Report (v{OFT_VERSION})")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("Compliance Checklist")
    lines.append("- Include this report/notices file with distributed binaries or source bundles.")
    lines.append("- Preserve OFT's MIT license notice and copyright attribution.")
    lines.append("- Include Apache-2.0 notices for OpenCV/zxing-cpp and bundled third-party notices.")
    lines.append("- Include Pillow MIT-CMU and qrcodegen MIT license notices.")
    lines.append("- Verify external executable licenses (7-Zip, ffmpeg) for your chosen binaries.")
    lines.append("")

    lines.append("Python Dependency Details")
    for dist_name in DEPENDENCY_DISTRIBUTIONS:
        lines.append(f"- {dist_name}")
        try:
            dist = importlib_metadata.distribution(dist_name)
        except importlib_metadata.PackageNotFoundError:
            lines.append("  status: NOT INSTALLED")
            continue
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  status: metadata error: {exc}")
            continue

        meta = dist.metadata
        version = meta.get("Version", "<unknown>")
        license_field = (meta.get("License") or "").strip() or "<empty>"
        classifiers = [
            entry for entry in (meta.get_all("Classifier") or []) if entry.startswith("License ::")
        ]
        license_files = meta.get_all("License-File") or []
        resolved_license_paths = _resolve_declared_license_paths(dist, license_files)
        inference_source: Path | None = None

        if license_field == "<empty>" or not classifiers:
            inferred_field, inferred_classifiers, inferred_source = _infer_license_from_files(
                resolved_license_paths
            )
            if license_field == "<empty>" and inferred_field:
                license_field = inferred_field
                inference_source = inferred_source
            if not classifiers and inferred_classifiers:
                classifiers = inferred_classifiers
                if inference_source is None:
                    inference_source = inferred_source

        lines.append(f"  version: {version}")
        lines.append(f"  license field: {license_field}")
        if classifiers:
            lines.append("  license classifiers:")
            for classifier in classifiers:
                lines.append(f"    - {classifier}")
        else:
            lines.append("  license classifiers: <none>")
        if inference_source is not None:
            lines.append(f"  inference source: {inference_source}")

        if license_files:
            lines.append("  declared license files:")
            if resolved_license_paths:
                for path_obj in resolved_license_paths:
                    lines.append(f"    - {path_obj}")
            else:
                for rel in license_files:
                    lines.append(f"    - {rel}")
        else:
            lines.append("  declared license files: <none>")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _write_third_party_notices(report_text: str) -> Path:
    THIRD_PARTY_NOTICES_FILE.write_text(report_text, encoding="utf-8")
    return THIRD_PARTY_NOTICES_FILE


def _handle_standalone_cli_flags(raw_args: list[str]) -> None:
    standalone_flags = {
        "-v": "version",
        "--version": "version",
        "--license-report": "license_report",
    }
    matched = [arg for arg in raw_args if arg in standalone_flags]
    if not matched:
        return

    if len(raw_args) != 1 or len(matched) != 1:
        raise SystemExit(
            "Error: -v/--version and --license-report cannot be combined with other arguments."
        )

    mode = standalone_flags[matched[0]]
    if mode == "version":
        print(OFT_VERSION)
        raise SystemExit(0)

    ensure_runtime_dependencies()
    report = _build_dependency_license_report()
    print(report, end="")
    notices_path = _write_third_party_notices(report)
    print(f"[OFT] Wrote third-party notices: {notices_path}")
    raise SystemExit(0)


def _missing_runtime_modules() -> list[str]:
    missing: list[str] = []
    for module_name in DEPENDENCY_REQUIREMENTS:
        try:
            importlib.import_module(module_name)
        except Exception:  # noqa: BLE001
            missing.append(module_name)
    return missing


def _run_pip_install(args: list[str]) -> bool:
    cmd = [sys.executable, "-m", "pip", "--disable-pip-version-check", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True
    print(
        "[OFT] Dependency install command failed.\n"
        f"Command: {' '.join(cmd)}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}",
        file=sys.stderr,
    )
    return False


def ensure_runtime_dependencies() -> None:
    """Ensure required third-party modules are available, auto-installing when possible."""
    missing = _missing_runtime_modules()
    if not missing:
        return

    print(
        f"[OFT] Missing Python dependencies detected: {', '.join(sorted(missing))}",
        file=sys.stderr,
    )

    if missing:
        packages = [DEPENDENCY_REQUIREMENTS[module_name] for module_name in missing]
        print(
            "[OFT] Attempting targeted dependency install: "
            + ", ".join(packages),
            file=sys.stderr,
        )
        _run_pip_install(["install", *packages])
        missing = _missing_runtime_modules()

    if missing:
        requested = ", ".join(DEPENDENCY_REQUIREMENTS[module_name] for module_name in missing)
        raise RuntimeError(
            "Unable to auto-resolve required Python dependencies. "
            f"Please install: {requested}"
        )


def import_runtime_dependencies() -> None:
    """Import runtime modules after dependency checks complete."""
    global cv2
    global zxingcpp
    global Image
    global QrCode
    global QrSegment

    cv2 = importlib.import_module("cv2")  # type: ignore[reportMissingTypeStubs]
    zxingcpp = importlib.import_module("zxingcpp")  # type: ignore[reportMissingTypeStubs]

    pil_image_module = importlib.import_module("PIL.Image")
    qrcodegen_module = importlib.import_module("qrcodegen")

    Image = pil_image_module
    QrCode = getattr(qrcodegen_module, "QrCode")
    QrSegment = getattr(qrcodegen_module, "QrSegment")


def load_persistence() -> tuple[dict[str, Any], bool]:
    """Load OFT_persistence.json from the tool directory.

    Returns a tuple of (defaults_dict, is_new_file).
    If the file does not exist it is created with null placeholders; is_new_file=True
    signals that the caller should write back the CLI values once they are known.
    On subsequent runs values present in the file act as argparse defaults;
    the file is not modified during a normal run.
    """
    if not PERSISTENCE_FILE.exists():
        template: dict[str, Any] = {k: None for k in PERSISTENT_KEYS}
        try:
            with PERSISTENCE_FILE.open("w", encoding="utf-8") as fh:
                json.dump(template, fh, indent=2)
                fh.write("\n")
        except OSError as exc:
            print(f"[OFT] Warning: could not create persistence file: {exc}", file=sys.stderr)
        return {}, True

    try:
        with PERSISTENCE_FILE.open("r", encoding="utf-8") as fh:
            raw: dict[str, Any] = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[OFT] Warning: could not read persistence file: {exc}", file=sys.stderr)
        return {}, False

    # Return only the recognised keys whose value is not None.
    return {k: raw[k] for k in PERSISTENT_KEYS if k in raw and raw[k] is not None}, False


def _save_persistence(args: argparse.Namespace) -> None:
    """Write current CLI values for PERSISTENT_KEYS into the persistence file."""
    # Map argparse dest names (underscores) to JSON keys (underscores match dest).
    data: dict[str, Any] = {k: None for k in PERSISTENT_KEYS}
    for key in PERSISTENT_KEYS:
        val = getattr(args, key, None)
        if val is not None:
            data[key] = val
    try:
        with PERSISTENCE_FILE.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        print(
            f"[OFT] Persistence file initialised: {PERSISTENCE_FILE}",
            file=sys.stderr,
        )
    except OSError as exc:
        print(f"[OFT] Warning: could not write persistence file: {exc}", file=sys.stderr)


def _update_persistence_values(updates: dict[str, Any]) -> None:
    """Merge provided key/value pairs into the persistence file."""
    current: dict[str, Any] = {k: None for k in PERSISTENT_KEYS}
    if PERSISTENCE_FILE.exists():
        try:
            with PERSISTENCE_FILE.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                for key in PERSISTENT_KEYS:
                    if key in loaded:
                        current[key] = loaded[key]
        except Exception as exc:  # noqa: BLE001
            print(f"[OFT] Warning: could not read persistence file for update: {exc}", file=sys.stderr)

    changed = False
    for key, value in updates.items():
        if key not in PERSISTENT_KEYS or value is None:
            continue
        if current.get(key) != value:
            current[key] = value
            changed = True

    if not changed:
        return

    try:
        with PERSISTENCE_FILE.open("w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        print(f"[OFT] Warning: could not update persistence file: {exc}", file=sys.stderr)


def _is_windows() -> bool:
    return os.name == "nt"


def _iter_common_search_roots() -> list[Path]:
    roots: list[Path] = []
    env_names = (
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramData",
        "LOCALAPPDATA",
        "USERPROFILE",
    )
    for env_name in env_names:
        raw = os.environ.get(env_name)
        if not raw:
            continue
        candidate = Path(raw)
        if candidate.exists() and candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _find_executable_by_walk(file_names: tuple[str, ...], max_depth: int = 5) -> str | None:
    """Best-effort recursive search for an executable in common install roots."""
    wanted = {name.lower() for name in file_names}
    for root in _iter_common_search_roots():
        try:
            for current, dirs, files in os.walk(root):
                rel_parts = Path(current).relative_to(root).parts
                if len(rel_parts) > max_depth:
                    dirs[:] = []
                    continue

                matches = [name for name in files if name.lower() in wanted]
                if matches:
                    return str(Path(current) / matches[0])
        except OSError:
            continue
    return None


def _candidate_install_paths(tool_key: str) -> list[Path]:
    candidates: list[Path] = []
    if _is_windows():
        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_data = os.environ.get("ProgramData")
        user_profile = os.environ.get("USERPROFILE")

        if tool_key == "sevenzip_bin":
            if program_files:
                candidates.append(Path(program_files) / "7-Zip" / "7z.exe")
            if program_files_x86:
                candidates.append(Path(program_files_x86) / "7-Zip" / "7z.exe")
            if local_app_data:
                candidates.append(Path(local_app_data) / "Programs" / "7-Zip" / "7z.exe")
        elif tool_key == "ffmpeg_bin":
            if program_files:
                candidates.append(Path(program_files) / "ffmpeg" / "bin" / "ffmpeg.exe")
            if program_files_x86:
                candidates.append(Path(program_files_x86) / "ffmpeg" / "bin" / "ffmpeg.exe")
            if program_data:
                candidates.append(Path(program_data) / "chocolatey" / "bin" / "ffmpeg.exe")
            if user_profile:
                candidates.append(Path(user_profile) / "scoop" / "shims" / "ffmpeg.exe")
                candidates.append(Path(user_profile) / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe")
            if local_app_data:
                candidates.append(Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")

    return candidates


def _resolve_preferred_executable(value: str | None, file_names: tuple[str, ...]) -> str | None:
    if not value:
        return None

    direct = Path(value)
    if direct.exists() and direct.is_file():
        return str(direct.resolve())

    if direct.exists() and direct.is_dir():
        for file_name in file_names:
            possible = direct / file_name
            if possible.exists() and possible.is_file():
                return str(possible.resolve())

    found = shutil.which(value)
    if found:
        return found

    return None


def _auto_resolve_executable(tool_key: str, cli_value: str | None, file_names: tuple[str, ...]) -> str | None:
    """Resolve executable with PATH-first lookup, then common installs, then shallow walk."""
    # Keep valid user/persistence-provided value if it already resolves.
    preferred = _resolve_preferred_executable(cli_value, file_names)
    if preferred:
        return preferred

    for file_name in file_names:
        found = shutil.which(file_name)
        if found:
            return found

    for candidate in _candidate_install_paths(tool_key):
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())

    return _find_executable_by_walk(file_names)


def _prompt_for_executable(tool_label: str, file_names: tuple[str, ...]) -> str | None:
    if not sys.stdin.isatty():
        return None

    raw = input(
        f"[OFT] Enter full path (or command name) for {tool_label} executable, "
        "or press Enter to skip: "
    ).strip()
    if not raw:
        return None

    cleaned = raw.strip('"').strip("'")
    return _resolve_preferred_executable(cleaned, file_names)


def ensure_persistent_executables(args: argparse.Namespace) -> None:
    """Ensure 7-Zip and ffmpeg locations are available and persisted when discovered."""
    tool_specs = (
        ("sevenzip_bin", "7-Zip", ("7z.exe", "7zz.exe", "7za.exe") if _is_windows() else ("7z", "7zz", "7za")),
        ("ffmpeg_bin", "ffmpeg", ("ffmpeg.exe",) if _is_windows() else ("ffmpeg",)),
    )

    updates: dict[str, str] = {}

    for attr, label, names in tool_specs:
        current_value = cast(str | None, getattr(args, attr, None))
        resolved = _auto_resolve_executable(attr, current_value, names)
        if resolved:
            setattr(args, attr, resolved)
            if current_value != resolved:
                print(f"[OFT] Resolved {label} executable: {resolved}", file=sys.stderr)
            updates[attr] = resolved
            continue

        prompted = _prompt_for_executable(label, names)
        if prompted:
            setattr(args, attr, prompted)
            print(f"[OFT] Using user-provided {label} executable: {prompted}", file=sys.stderr)
            updates[attr] = prompted
        else:
            print(
                f"[OFT] Warning: could not auto-resolve {label} executable. "
                "Provide --sevenzip-bin/--ffmpeg-bin or update persistence.",
                file=sys.stderr,
            )

    if updates:
        _update_persistence_values(updates)


class StageStatus:
    """Render a stage status line without flooding terminal output."""

    _global_last_len = 0

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self._closed = False
        self._last_len = 0

    def _format_line(self, message: str) -> str:
        safe_message = str(message).replace("\r", " ").replace("\n", " ")
        line = f"[{self.stage}] {safe_message}"
        width = max(
            STATUS_MIN_COLUMNS,
            min(
                STATUS_MAX_COLUMNS,
                shutil.get_terminal_size(fallback=(STATUS_MAX_COLUMNS, 20)).columns - 1,
            ),
        )
        if len(line) > width:
            line = line[: max(1, width - 1)] + "~"
        return line

    def update(self, message: str) -> None:
        if self._closed:
            return
        line = self._format_line(message)
        pad_to = max(self._last_len, StageStatus._global_last_len)
        padded = line + (" " * max(0, pad_to - len(line)))
        self._last_len = len(line)
        StageStatus._global_last_len = max(StageStatus._global_last_len, len(line))
        print(f"\r{padded}", end="", flush=True)

    def done(self, message: str) -> None:
        if self._closed:
            return
        self.update(message)
        print(flush=True)
        self._closed = True
        StageStatus._global_last_len = 0


def _tail_text(lines: collections.deque[str], limit: int = 12) -> str:
    return "\n".join(list(lines)[-limit:]).strip()


def _extract_percent(text: str) -> int | None:
    match = re.search(r"(\d{1,3})%", text)
    if not match:
        return None
    value = int(match.group(1))
    return max(0, min(100, value))


def _format_duration(seconds: float) -> str:
    clamped = max(0, int(seconds))
    minutes, secs = divmod(clamped, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _progress_bar(current: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    ratio = max(0.0, min(1.0, current / total))
    filled = int(round(ratio * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _pump_stream_lines(stream: Any, output_queue: queue.Queue[str | None]) -> None:
    try:
        for raw in stream:
            output_queue.put(raw.rstrip("\r\n"))
    finally:
        output_queue.put(None)


def _pump_stream_tokens(stream: Any, output_queue: queue.Queue[str | None]) -> None:
    pending = ""
    try:
        while True:
            chunk = stream.read(1)
            if chunk == "":
                break
            if chunk in ("\r", "\n"):
                line = pending.strip()
                pending = ""
                if line:
                    output_queue.put(line)
            else:
                pending += chunk
        if pending.strip():
            output_queue.put(pending.strip())
    finally:
        output_queue.put(None)


def _try_fsync_path(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= getattr(os, "O_BINARY")
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return False
    try:
        os.fsync(fd)
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def _stage_disk_barrier(path: Path | None = None, force_delay: bool = False) -> None:
    if force_delay:
        time.sleep(DISK_FLUSH_DELAY_SEC)
        return
    if path is not None and _try_fsync_path(path):
        return
    time.sleep(DISK_FLUSH_DELAY_SEC)


def _write_binary_file(output_path: Path, data: bytes) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return output_path


def generate_random_2kb_bin_file(output_path: Path) -> Path:
    """Generate a binary file containing 2 KiB of random data."""
    return _write_binary_file(output_path, os.urandom(2048))


def create_oft_test_folder(base_dir: Path | None = None) -> Path:
    """Create OFT_Test with two unique random 2 KiB .bin files."""
    target_root = (Path.cwd() if base_dir is None else base_dir).resolve() / "OFT_Test"
    target_root.mkdir(parents=True, exist_ok=True)

    first_file = target_root / "oft_test_01.bin"
    second_file = target_root / "oft_test_02.bin"

    first_data = os.urandom(2048)
    second_data = os.urandom(2048)
    while second_data == first_data:
        second_data = os.urandom(2048)

    _write_binary_file(first_file, first_data)
    _write_binary_file(second_file, second_data)
    return target_root


def _terminate_process(proc: subprocess.Popen[str], label: str, grace_sec: float = 5.0) -> None:
    """Try graceful termination first, then hard-kill if needed."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=grace_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=grace_sec)
    except OSError as exc:
        raise RuntimeError(f"Failed to terminate stalled {label} process: {exc}") from exc


def resolve_executable(preferred: str | None, candidates: tuple[str, ...], label: str) -> str:
    if preferred:
        resolved = shutil.which(preferred)
        if resolved:
            return resolved
        raise RuntimeError(
            f"Configured {label} executable '{preferred}' was not found on PATH."
        )

    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    cand_text = ", ".join(candidates)
    raise RuntimeError(
        f"Could not find {label} on PATH. Tried: {cand_text}. "
        "Install it or pass an explicit executable via command-line option."
    )


def _pack_chunk_payload(payload: bytes, chunk_id: int, total_chunks: int) -> bytes:
    payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = CHUNK_HEADER_STRUCT.pack(
        CHUNK_MAGIC,
        CHUNK_VERSION,
        total_chunks,
        chunk_id,
        len(payload),
        payload_crc,
    )
    wrapped = header + payload
    if len(wrapped) > MAX_CHUNK_BYTES:
        raise RuntimeError(
            f"Wrapped chunk {chunk_id} exceeds {MAX_CHUNK_BYTES} bytes "
            f"({len(wrapped)} bytes)."
        )
    return wrapped


def unpack_chunk_payload(chunk: bytes) -> tuple[int, int, bytes]:
    if len(chunk) < CHUNK_HEADER_STRUCT.size:
        raise RuntimeError(
            f"Decoded QR payload too small for chunk envelope ({len(chunk)} bytes)."
        )

    magic, version, total_chunks, chunk_id, payload_len, payload_crc = CHUNK_HEADER_STRUCT.unpack(
        chunk[: CHUNK_HEADER_STRUCT.size]
    )
    if magic != CHUNK_MAGIC:
        raise RuntimeError(
            "Decoded QR payload missing expected chunk envelope magic. "
            "Regenerate video with updated splitter logic."
        )
    if version != CHUNK_VERSION:
        raise RuntimeError(f"Unsupported chunk envelope version: {version}.")
    if total_chunks <= 0:
        raise RuntimeError(f"Invalid total chunk count in envelope: {total_chunks}.")
    if chunk_id >= total_chunks:
        raise RuntimeError(f"Invalid chunk id {chunk_id} for declared total {total_chunks}.")

    payload = chunk[CHUNK_HEADER_STRUCT.size :]
    if len(payload) != payload_len:
        raise RuntimeError(
            f"Chunk {chunk_id} payload length mismatch: got {len(payload)}, expected {payload_len}."
        )

    actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_crc != payload_crc:
        raise RuntimeError(
            f"Chunk {chunk_id} CRC mismatch: got {actual_crc:08x}, expected {payload_crc:08x}."
        )

    return chunk_id, total_chunks, payload


# ---------------------- TX (source -> QR video) ----------------------

def _estimate_chunk_count(input_source: Path) -> int:
    if input_source.is_file():
        size = input_source.stat().st_size
        estimated_archive_size = size + 512
    else:
        total_size = 0
        total_files = 0
        for child in input_source.rglob("*"):
            if child.is_file():
                total_files += 1
                total_size += child.stat().st_size
        estimated_archive_size = total_size + (512 * max(1, total_files))
    return max(1, (estimated_archive_size + MAX_CHUNK_BYTES - 1) // MAX_CHUNK_BYTES)


def _build_7zip_input_args(input_source: Path) -> list[str]:
    # Pass only the name; tx_run_7zip_split sets cwd=input_source.parent so that
    # 7-Zip stores just "name" or "folder/..." — not the full absolute path.
    return [input_source.name]


def _split_volume_sort_key(path: Path) -> tuple[int, str]:
    suffix = path.name.rsplit(".", 1)[-1]
    try:
        return (int(suffix), path.name)
    except ValueError:
        return (sys.maxsize, path.name)


def tx_run_7zip_split(
    sevenzip_bin: str,
    input_source: Path,
    split_dir: Path,
    password: str | None,
) -> list[Path]:
    archive_stem = split_dir / "payload.7z"
    input_args = _build_7zip_input_args(input_source)
    cmd = [
        sevenzip_bin,
        "a",
        "-t7z",
        "-mx=0",
        "-bsp1",
        f"-v{SPLIT_PAYLOAD_BYTES}b",
        str(archive_stem),
        *input_args,
    ]
    if password:
        cmd.extend([f"-p{password}", "-mhe=on"])

    estimated_chunks = _estimate_chunk_count(input_source)
    status = StageStatus("Split")
    status.update(f"starting; estimated chunks: ~{estimated_chunks}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=str(input_source.parent),
    )

    captured: collections.deque[str] = collections.deque(maxlen=300)
    if proc.stdout is None:
        status.done("failed")
        raise RuntimeError("7-Zip process did not expose a readable stdout stream.")
    tokens: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_pump_stream_tokens, args=(proc.stdout, tokens), daemon=True)
    reader.start()

    started_ts = time.monotonic()
    last_output_ts = started_ts
    last_percent_ts = started_ts
    last_watchdog_ts = 0.0
    last_percent = -1

    while True:
        try:
            token = tokens.get(timeout=0.5)
        except queue.Empty:
            now = time.monotonic()
            if proc.poll() is not None and not reader.is_alive():
                break
            if now - last_watchdog_ts >= WATCHDOG_HEARTBEAT_SEC:
                output_idle = now - last_output_ts
                progress_idle = now - last_percent_ts
                warning = "ok"
                if output_idle >= WATCHDOG_WARN_OUTPUT_STALL_SEC:
                    warning = f"no output {_format_duration(output_idle)}"
                if progress_idle >= WATCHDOG_WARN_PROGRESS_STALL_SEC:
                    warning = f"no progress {_format_duration(progress_idle)}"
                shown_percent = max(0, last_percent)
                status.update(
                    f"{shown_percent:3d}% est~{estimated_chunks} "
                    f"t={_format_duration(now - started_ts)} {warning}"
                )
                last_watchdog_ts = now
            continue

        if token is None:
            if proc.poll() is not None:
                break
            continue

        now = time.monotonic()
        last_output_ts = now
        captured.append(token)
        percent = _extract_percent(token)
        if percent is not None and percent != last_percent:
            last_percent = percent
            last_percent_ts = now
            status.update(f"{percent:3d}% est~{estimated_chunks}")

    return_code = proc.wait()
    if return_code != 0:
        status.done("failed")
        raise RuntimeError(
            "7-Zip failed to split input.\n"
            f"Command: {' '.join(cmd)}\n"
            f"Recent output:\n{_tail_text(captured)}"
        )

    parts = sorted(split_dir.glob("payload.7z.*"), key=_split_volume_sort_key)
    if not parts:
        single = split_dir / "payload.7z"
        if single.exists():
            parts = [single]

    if not parts:
        raise RuntimeError("No split parts were created by 7-Zip.")

    too_large = [p for p in parts if p.stat().st_size > SPLIT_PAYLOAD_BYTES]
    if too_large:
        names = ", ".join(p.name for p in too_large)
        raise RuntimeError(f"Some chunks exceed {SPLIT_PAYLOAD_BYTES} bytes: {names}")

    _stage_disk_barrier(force_delay=True)
    status.done(f"complete; generated {len(parts)} chunks")
    return parts


def tx_render_qr_image_from_bytes(data: bytes, out_path: Path) -> None:
    seg = QrSegment.make_bytes(data)
    qr: Any = QrCode.encode_segments(
        [seg],
        ecl=QrCode.Ecc.HIGH,
        minversion=QR_VERSION,
        maxversion=QR_VERSION,
        mask=-1,
        boostecl=False,
    )

    modules = int(qr.get_size())
    expected_modules = QR_VERSION * 4 + 17
    if modules != expected_modules:
        raise RuntimeError(f"Unexpected QR module size: {modules}, expected {expected_modules}.")

    bordered_modules = modules + (2 * QR_BORDER_MODULES)
    img_small = Image.new("1", (bordered_modules, bordered_modules), 1)
    px: Any = img_small.load()

    for y in range(modules):
        for x in range(modules):
            if qr.get_module(x, y):
                px[x + QR_BORDER_MODULES, y + QR_BORDER_MODULES] = 0

    img = img_small.resize((QR_SIZE_PX, QR_SIZE_PX), Image.Resampling.NEAREST)

    with out_path.open("wb") as stream:
        img.save(stream, format="PNG")
        stream.flush()
        os.fsync(stream.fileno())

    img.close()
    img_small.close()


def tx_render_qr_from_part(part_path: Path, out_path: Path, chunk_id: int, total_chunks: int) -> Path:
    # Worker processes do not execute main(), so lazily initialise dependencies.
    if QrSegment is None or QrCode is None or Image is None:
        import_runtime_dependencies()

    data = part_path.read_bytes()
    wrapped = _pack_chunk_payload(data, chunk_id, total_chunks)
    tx_render_qr_image_from_bytes(wrapped, out_path)
    return out_path


def tx_render_qr_from_part_tuple(paths: tuple[Path, Path, int, int]) -> Path:
    return tx_render_qr_from_part(paths[0], paths[1], paths[2], paths[3])


def _generate_garbage_qr(out_path: Path) -> None:
    """Generate a QR image filled with random binary data for testing robustness."""
    random_data = os.urandom(MAX_CHUNK_BYTES)
    tx_render_qr_image_from_bytes(random_data, out_path)


def _apply_fisher_yates_shuffle(items: list[Path]) -> list[Path]:
    """In-place Fisher-Yates shuffle; returns the shuffled list."""
    result = items.copy()
    for i in range(len(result) - 1, 0, -1):
        j = int(os.urandom(1)[0]) % (i + 1)
        result[i], result[j] = result[j], result[i]
    return result


def tx_make_qr_images(parts: list[Path], qr_dir: Path, qr_workers: int) -> list[Path]:
    qr_paths: list[Path] = [qr_dir / f"qr_{idx:06d}.png" for idx in range(len(parts))]
    max_workers = qr_workers if qr_workers > 0 else max(1, (os.cpu_count() or 1))
    total = len(parts)
    if total == 0:
        raise RuntimeError("No split parts were available for QR image generation.")

    status = StageStatus("Encode")
    status.update(f"w={max_workers} 0/{total} {_progress_bar(0, total, width=16)}   0%")

    work_items = [(part, qr_paths[idx], idx, total) for idx, part in enumerate(parts)]
    completed = 0
    update_every = max(1, total // 100)
    start_ts = time.monotonic()
    last_update_ts = 0.0
    last_completion_ts = start_ts
    stalled_announced = False

    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        pending = {executor.submit(tx_render_qr_from_part_tuple, item) for item in work_items}
        while pending:
            done, pending = concurrent.futures.wait(
                pending,
                timeout=0.5,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            now = time.monotonic()
            if not done:
                if now - last_update_ts >= WATCHDOG_HEARTBEAT_SEC:
                    stall_for = now - last_completion_ts
                    warning = "ok"
                    if stall_for >= WATCHDOG_WARN_PROGRESS_STALL_SEC:
                        warning = f"possible stall {_format_duration(stall_for)}"
                        stalled_announced = True
                    elapsed = max(0.001, now - start_ts)
                    rate = completed / elapsed
                    percent = int((completed * 100) / total)
                    stall_note = f" {warning}" if warning != "ok" else ""
                    status.update(
                        f"w={max_workers} {completed}/{total} {_progress_bar(completed, total, width=16)} "
                        f"{percent:3d}% {rate:0.1f}/s{stall_note}"
                    )
                    last_update_ts = now
                continue

            for future in done:
                future.result()
                completed += 1
                last_completion_ts = now

            now = time.monotonic()
            should_update = (
                completed == total
                or completed == 1
                or (completed % update_every == 0)
                or (now - last_update_ts >= 1.0)
            )
            if should_update:
                percent = int((completed * 100) / total)
                elapsed = max(0.001, now - start_ts)
                rate = completed / elapsed
                warning = " recovered" if stalled_announced else ""
                status.update(
                    f"w={max_workers} {completed}/{total} {_progress_bar(completed, total, width=16)} "
                    f"{percent:3d}% {rate:0.1f}/s{warning}"
                )
                last_update_ts = now
                stalled_announced = False

    _stage_disk_barrier(force_delay=True)
    status.done(
        f"complete; w={max_workers} {total}/{total} {_progress_bar(total, total, width=16)} 100%"
    )
    return qr_paths


def tx_make_qr_images_with_options(
    parts: list[Path],
    qr_dir: Path,
    qr_workers: int,
    enable_shuffle: bool = False,
    temporal_reps: int = 1,
    add_garbage: int = 0,
) -> list[Path]:
    """Generate QR images with optional shuffling, repetition, and garbage frames."""
    # Generate base QR images from parts
    qr_images = tx_make_qr_images(parts, qr_dir, qr_workers)

    # Handle temporal repetition and shuffling
    if temporal_reps > 1 or add_garbage > 0:
        status = StageStatus("Encode")
        if temporal_reps > 1:
            status.update(f"applying temporal reps={temporal_reps}")
            if enable_shuffle:
                # Shuffle entire collection of repeated sequences
                all_repeated = qr_images * temporal_reps
                qr_images = _apply_fisher_yates_shuffle(all_repeated)
            else:
                # Linear repetition
                qr_images = qr_images * temporal_reps
        if add_garbage > 0:
            status.update(f"adding {add_garbage} garbage QR frames")
            next_idx = len(qr_images)
            for i in range(add_garbage):
                garbage_path = qr_dir / f"qr_{next_idx + i:06d}.png"
                _generate_garbage_qr(garbage_path)
                qr_images.append(garbage_path)
        status.done(f"complete; total frames={len(qr_images)}")

    return qr_images


def tx_run_ffmpeg(ffmpeg_bin: str, qr_dir: Path, output_path: Path, total_frames: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    input_fps = FPS / FRAMES_PER_QR
    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-hwaccel",
        "auto",
        "-framerate",
        str(input_fps),
        "-i",
        str(qr_dir / "qr_%06d.png"),
        "-vf",
        f"scale={QR_SIZE_PX}:{QR_SIZE_PX}:flags=neighbor",
        "-r",
        str(FPS),
        "-c:v",
        "libx264rgb",
        "-crf",
        "0",
        "-pix_fmt",
        "rgb24",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_path),
    ]

    status = StageStatus("Generate")
    status.update("starting video render")

    total_duration_s = max(0.001, total_frames / FPS)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    captured: collections.deque[str] = collections.deque(maxlen=300)
    if proc.stdout is None:
        status.done("failed")
        raise RuntimeError("ffmpeg process did not expose a readable stdout stream.")
    lines: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_pump_stream_lines, args=(proc.stdout, lines), daemon=True)
    reader.start()

    start_ts = time.monotonic()
    last_output_ts = start_ts
    last_progress_ts = start_ts
    last_watchdog_ts = 0.0
    max_render_sec = max(
        WATCHDOG_ABORT_PROGRESS_STALL_SEC,
        (total_duration_s * WATCHDOG_MAX_RENDER_FACTOR) + WATCHDOG_MAX_RENDER_OVERHEAD_SEC,
    )
    out_time_us = 0
    frame_no = 0
    speed = "n/a"

    while True:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            now = time.monotonic()
            if proc.poll() is not None and not reader.is_alive():
                break

            output_idle = now - last_output_ts
            progress_idle = now - last_progress_ts
            elapsed = now - start_ts

            if output_idle >= WATCHDOG_ABORT_OUTPUT_STALL_SEC:
                _terminate_process(proc, "ffmpeg")
                status.done("failed")
                raise RuntimeError(
                    "ffmpeg stalled with no output and was terminated.\n"
                    f"Command: {' '.join(cmd)}\n"
                    f"Elapsed: {_format_duration(elapsed)}\n"
                    f"Output idle: {_format_duration(output_idle)}\n"
                    f"Recent output:\n{_tail_text(captured)}"
                )

            if progress_idle >= WATCHDOG_ABORT_PROGRESS_STALL_SEC:
                _terminate_process(proc, "ffmpeg")
                status.done("failed")
                raise RuntimeError(
                    "ffmpeg made no encoding progress and was terminated.\n"
                    f"Command: {' '.join(cmd)}\n"
                    f"Elapsed: {_format_duration(elapsed)}\n"
                    f"Progress idle: {_format_duration(progress_idle)}\n"
                    f"Recent output:\n{_tail_text(captured)}"
                )

            if elapsed >= max_render_sec:
                _terminate_process(proc, "ffmpeg")
                status.done("failed")
                raise RuntimeError(
                    "ffmpeg exceeded max expected render time and was terminated.\n"
                    f"Command: {' '.join(cmd)}\n"
                    f"Elapsed: {_format_duration(elapsed)}\n"
                    f"Expected duration: {_format_duration(total_duration_s)}\n"
                    f"Recent output:\n{_tail_text(captured)}"
                )

            if now - last_watchdog_ts >= WATCHDOG_HEARTBEAT_SEC:
                warning = ""
                if output_idle >= WATCHDOG_WARN_OUTPUT_STALL_SEC:
                    warning = f" no output {_format_duration(output_idle)}"
                if progress_idle >= WATCHDOG_WARN_PROGRESS_STALL_SEC:
                    warning = f" possible stall {_format_duration(progress_idle)}"
                progress = int(min(100.0, max(0.0, (out_time_us / 1_000_000) / total_duration_s * 100.0)))
                shown_frame = min(total_frames, max(0, frame_no))
                status.update(
                    f"rendering {progress:3d}% frame={shown_frame}/{total_frames} "
                    f"speed={speed}{warning}"
                )
                last_watchdog_ts = now
            continue

        if line is None:
            if proc.poll() is not None:
                break
            continue

        now = time.monotonic()
        last_output_ts = now
        line = line.strip()
        if not line:
            continue
        captured.append(line)
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key in ("out_time_us", "out_time_ms"):
            try:
                out_time_us = int(value)
            except ValueError:
                continue
            last_progress_ts = now
            progress = int(min(100.0, max(0.0, (out_time_us / 1_000_000) / total_duration_s * 100.0)))
            shown_frame = min(total_frames, max(0, frame_no))
            status.update(f"rendering {progress:3d}% frame={shown_frame}/{total_frames} speed={speed}")
        elif key == "frame":
            try:
                frame_no = int(value)
            except ValueError:
                continue
        elif key == "speed":
            speed = value

    return_code = proc.wait()
    if return_code != 0:
        status.done("failed")
        raise RuntimeError(
            "ffmpeg failed to render video.\n"
            f"Command: {' '.join(cmd)}\n"
            f"Recent output:\n{_tail_text(captured)}"
        )

    _stage_disk_barrier(force_delay=True)
    status.done(f"complete; rendering 100% frame={total_frames}/{total_frames} speed={speed}")


def tx_run_pipeline(args: argparse.Namespace, work_dir: Path) -> None:
    if args.source is None:
        raise RuntimeError("-TX mode requires --source <file-or-folder>.")

    source_path = args.source.resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")
    if not (source_path.is_file() or source_path.is_dir()):
        raise RuntimeError(f"Source path is not a file or folder: {source_path}")

    if args.output is None:
        raise RuntimeError("-TX mode requires -o/--output for the generated video.")

    sevenzip_bin = resolve_executable(args.sevenzip_bin, DEFAULT_7ZIP_CANDIDATES, "7-Zip")
    ffmpeg_bin = resolve_executable(args.ffmpeg_bin, DEFAULT_FFMPEG_CANDIDATES, "ffmpeg")
    output_path = args.output.resolve()

    pipeline_status = StageStatus("pipeline")
    pipeline_status.update("initializing work directories")
    split_dir = work_dir / "split"
    qr_dir = work_dir / "qrs"
    split_dir.mkdir(parents=True, exist_ok=True)
    qr_dir.mkdir(parents=True, exist_ok=True)

    pipeline_status.update("stage 1/3: split archive with 7-Zip")
    parts = tx_run_7zip_split(sevenzip_bin, source_path, split_dir, args.password)

    pipeline_status.update("stage 2/3: convert chunks to QR images")
    qr_images = tx_make_qr_images_with_options(
        parts,
        qr_dir,
        args.qr_workers,
        enable_shuffle=args.messy,
        temporal_reps=args.temporal_reps,
        add_garbage=args.add_garbage,
    )
    total_frames = len(qr_images) * FRAMES_PER_QR
    if total_frames == 0:
        raise RuntimeError("No frames were generated.")

    pipeline_status.update("stage 3/3: render video with ffmpeg")
    tx_run_ffmpeg(ffmpeg_bin, qr_dir, output_path, total_frames)
    pipeline_status.done("complete")

    print(f"Video written: {output_path}")


# ---------------------- RX (QR video/camera -> file) ----------------------

def rx_decode_payload(result: Any) -> bytes:
    payload = getattr(result, "bytes", None)
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)

    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text.encode("latin-1", errors="replace")

    raise RuntimeError("Could not decode payload bytes from QR detection result.")


def rx_format_missing_ids(ids: list[int], max_preview: int = 32) -> str:
    if not ids:
        return "[]"
    if len(ids) <= max_preview:
        return "[" + ", ".join(str(i) for i in ids) + "]"
    preview = ", ".join(str(i) for i in ids[:max_preview])
    return f"[{preview}, ...] (total missing: {len(ids)})"


def rx_start_camera_stop_listener(stop_event: threading.Event) -> None:
    def listen_for_stop() -> None:
        try:
            input(
                "Webcam mode started. Press Enter when you have finished presenting "
                "all QR codes to stop capture...\n"
            )
        except EOFError:
            return
        stop_event.set()

    thread = threading.Thread(target=listen_for_stop, daemon=True)
    thread.start()


def rx_collect_unique_qr_chunks(args: argparse.Namespace) -> list[bytes]:
    is_video = args.video is not None
    status = StageStatus("Capture")

    if is_video:
        video_path = args.video.resolve()
        if not video_path.exists() or not video_path.is_file():
            raise FileNotFoundError(f"Video file does not exist: {video_path}")
        capture = cv2.VideoCapture(str(video_path))
        status.update(f"video source opened: {video_path.name}")
    else:
        try:
            input("Webcam mode: press Enter when you are ready to start presenting QR codes...\n")
        except EOFError:
            status.update("webcam mode: stdin not interactive; starting now")
        camera_index = 0 if args.camera is None else args.camera
        capture = cv2.VideoCapture(camera_index)
        status.update(f"webcam source opened: index {camera_index}")

    if not capture.isOpened():
        raise RuntimeError("Failed to open input source (video/camera).")

    chunk_payloads: dict[int, bytes] = {}
    expected_total_chunks: int | None = None
    qr_visible = False
    stop_requested = threading.Event()
    if not is_video:
        rx_start_camera_stop_listener(stop_requested)

    start_ts = time.monotonic()
    last_new_ts = start_ts

    try:
        while True:
            ok, frame = capture.read()
            now = time.monotonic()

            if not ok:
                if is_video:
                    break
                if stop_requested.is_set():
                    break
                if now - start_ts >= args.max_duration:
                    break
                continue

            read_barcodes_fn = cast(Callable[..., Any], getattr(zxingcpp, "read_barcodes"))
            qr_format = getattr(getattr(zxingcpp, "BarcodeFormat", object()), "QRCode", None)
            if qr_format is None:
                results_raw: Any = read_barcodes_fn(frame)
            else:
                results_raw = read_barcodes_fn(frame, formats=qr_format)
            results_iter = cast(Iterable[Any], results_raw)
            results: list[Any] = list(results_iter)

            currently_visible = bool(results)
            if currently_visible and not qr_visible:
                status.update(f"qr visible; captured={len(chunk_payloads)}")
            if not currently_visible and qr_visible:
                status.update(f"qr not visible; captured={len(chunk_payloads)}")
            qr_visible = currently_visible

            for result in results:
                try:
                    chunk = rx_decode_payload(result)
                    if args.strict_chunk_size and len(chunk) > MAX_CHUNK_BYTES:
                        raise RuntimeError(
                            f"Decoded QR chunk exceeds {MAX_CHUNK_BYTES} bytes ({len(chunk)} bytes)."
                        )

                    chunk_id, total_chunks, payload = unpack_chunk_payload(chunk)
                except RuntimeError:
                    # Skip malformed or garbage QR codes
                    continue

                if expected_total_chunks is None:
                    expected_total_chunks = total_chunks
                elif expected_total_chunks != total_chunks:
                    # Tolerate mismatches from garbage QR codes; use first valid count
                    if chunk_id >= total_chunks:
                        # Garbage chunk with invalid ID range; skip it
                        continue

                # Skip chunks outside the expected range (garbage or out-of-order duplicates)
                if chunk_id >= expected_total_chunks:
                    continue

                if chunk_id in chunk_payloads:
                    if chunk_payloads[chunk_id] != payload:
                        # Tolerate conflicting payloads by keeping the first valid one
                        pass
                    continue

                chunk_payloads[chunk_id] = payload
                last_new_ts = now
                total_text = str(expected_total_chunks)
                status.update(f"Unique IDs; total={len(chunk_payloads)}/{total_text}")

            if is_video and expected_total_chunks is not None:
                if len(chunk_payloads) >= expected_total_chunks:
                    break

            if args.expected_chunks is not None and len(chunk_payloads) >= args.expected_chunks:
                break

            if not is_video:
                if stop_requested.is_set():
                    break
                if chunk_payloads and now - last_new_ts >= args.idle_timeout:
                    break
                if now - start_ts >= args.max_duration:
                    break
    finally:
        capture.release()

    if not chunk_payloads:
        status.done("failed; no QR codes decoded")
        raise RuntimeError("No QR codes were decoded from the provided source.")

    if expected_total_chunks is None:
        status.done("failed; no chunk envelope metadata")
        raise RuntimeError(
            "Decoded QR chunks did not provide required chunk count metadata. "
            "Regenerate video with updated splitter logic."
        )

    missing_ids = [idx for idx in range(expected_total_chunks) if idx not in chunk_payloads]
    if missing_ids:
        status.done(f"failed; missing ids={len(missing_ids)}")
        raise RuntimeError(
            "Missing required QR chunks before archive rebuild. "
            f"Expected total={expected_total_chunks}, captured={len(chunk_payloads)}. "
            f"Missing IDs: {rx_format_missing_ids(missing_ids)}"
        )

    ordered_chunks = [chunk_payloads[idx] for idx in range(expected_total_chunks)]
    status.done(f"complete; decoded {len(ordered_chunks)}/{expected_total_chunks}")
    return ordered_chunks


def rx_write_rebuilt_archive(chunks: list[bytes], work_dir: Path, strict_chunk_size: bool) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    first_volume_path: Path | None = None

    for idx, chunk in enumerate(chunks, start=1):
        if strict_chunk_size and len(chunk) > MAX_CHUNK_BYTES:
            raise RuntimeError(
                f"Decoded chunk #{idx} exceeds {MAX_CHUNK_BYTES} bytes ({len(chunk)} bytes)."
            )

        volume_path = work_dir / f"payload.7z.{idx:03d}"
        with volume_path.open("wb") as stream:
            stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())

        if first_volume_path is None:
            first_volume_path = volume_path

    if first_volume_path is None or first_volume_path.stat().st_size == 0:
        raise RuntimeError("Rebuilt archive volumes are empty.")

    _stage_disk_barrier(first_volume_path, force_delay=True)
    return first_volume_path


def rx_extract_original_file(
    sevenzip_bin: str,
    archive_path: Path,
    output_path: Path,
    password: str | None,
    extract_dir: Path,
) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)

    # Use 'x' (extract with full paths) so folder structure is preserved.
    cmd = [sevenzip_bin, "x", "-y", str(archive_path), f"-o{extract_dir}"]
    if password:
        cmd.append(f"-p{password}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "7-Zip extraction failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    _stage_disk_barrier(force_delay=True)

    # The archive contains either a single file or a single top-level folder.
    extracted_items = list(extract_dir.iterdir())
    if not extracted_items:
        raise RuntimeError("No items were extracted from rebuilt archive.")
    if len(extracted_items) != 1:
        raise RuntimeError(
            f"Expected exactly one top-level item in archive. "
            f"Found {len(extracted_items)} items in {extract_dir}."
        )

    extracted = extracted_items[0]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if extracted.is_file():
        shutil.copy2(extracted, output_path)
    else:
        if output_path.exists():
            shutil.rmtree(output_path)
        shutil.copytree(extracted, output_path)
    _stage_disk_barrier(output_path)


def rx_run_pipeline(args: argparse.Namespace, work_dir: Path) -> None:
    if args.output is None:
        raise RuntimeError("-RX mode requires -o/--output for the recovered file.")

    sevenzip_bin = resolve_executable(args.sevenzip_bin, DEFAULT_7ZIP_CANDIDATES, "7-Zip")
    chunks = rx_collect_unique_qr_chunks(args)

    split_dir = work_dir / "split"
    extract_dir = work_dir / "extracted"

    rebuilt_archive = rx_write_rebuilt_archive(chunks, split_dir, args.strict_chunk_size)
    rx_extract_original_file(
        sevenzip_bin,
        rebuilt_archive,
        args.output.resolve(),
        args.password,
        extract_dir,
    )

    print(f"Recovered output written: {args.output.resolve()}")


def cleanup_intermediate(work_dir: Path) -> None:
    for child in work_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    raw_args = sys.argv[1:]
    _handle_standalone_cli_flags(raw_args)

    parser = argparse.ArgumentParser(
        add_help=False,
        description=(
            "OFT unified CLI. Use -TX to encode source file/folder into QR video, "
            "or -RX to decode QR video/webcam input back to original file."
        ),
    )
    parser.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS)
    parser.add_argument("-v", "--version", action="store_true", help="Show OFT version and exit.")
    parser.add_argument("--license-report", action="store_true", help="Print dependency license report and exit.")

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("-TX", action="store_true", help="Transmit mode: source file/folder -> QR video.")
    mode_group.add_argument("-RX", action="store_true", help="Receive mode: QR video/webcam -> recovered file.")

    parser.add_argument("--source", type=Path, default=None, help="TX mode source file or folder.")

    parser.add_argument("--video", type=Path, default=None, help="RX mode input video path.")
    parser.add_argument("--camera", type=int, default=None, help="RX mode webcam index (example: 0).")

    parser.add_argument("-o", "--output", type=Path, default=None, help="Output path (video for TX, file for RX).")
    parser.add_argument("--work-dir", type=Path, default=None, help="Optional working directory.")
    parser.add_argument("--keep-intermediate", action="store_true", help="Keep intermediate files in work dir.")

    parser.add_argument("--sevenzip-bin", type=str, default=None, help="Optional explicit 7-Zip executable path/name.")
    parser.add_argument("--ffmpeg-bin", type=str, default=None, help="TX mode optional explicit ffmpeg executable path/name.")
    parser.add_argument("-p", "--password", type=str, default=None, help="Optional 7-Zip password.")

    parser.add_argument("--qr-workers", type=int, default=0, help="TX mode workers; 0 uses all CPU cores.")

    parser.add_argument("--messy", action="store_true", help="TX mode: shuffle QR frames (Fisher-Yates); requires --temporal-reps or adds randomness.")
    parser.add_argument("--temporal-reps", type=int, default=1, help="TX mode: repeat QR sequence N times; 1=no repetition.")
    parser.add_argument("--add-garbage", type=int, default=0, help="TX mode: append N garbage QR frames for RX robustness testing.")

    parser.add_argument("--expected-chunks", type=int, default=None, help="RX mode stop after this many unique QR chunks.")
    parser.add_argument("--idle-timeout", type=float, default=5.0, help="RX webcam: stop after this many idle seconds.")
    parser.add_argument("--max-duration", type=float, default=180.0, help="RX webcam: max capture duration seconds.")
    parser.add_argument(
        "--strict-chunk-size",
        action="store_true",
        help="RX mode fail if any decoded chunk exceeds 1273 bytes before reassembly.",
    )

    # Apply persistence file values as parser defaults before parsing CLI args.
    # CLI-supplied values take priority automatically.
    persistence, _is_new_persistence = load_persistence()
    if persistence:
        parser.set_defaults(**persistence)

    args = parser.parse_args()

    # On first run (file was just created) write the CLI-provided values back.
    if _is_new_persistence:
        _save_persistence(args)

    ensure_persistent_executables(args)

    if args.TX:
        if args.source is None:
            parser.error("-TX requires --source <file-or-folder>.")
        if args.video is not None or args.camera is not None:
            parser.error("-TX does not accept --video/--camera.")
    else:
        if args.video is None and args.camera is None:
            parser.error("-RX requires either --video <path> or --camera <index>.")
        if args.video is not None and args.camera is not None:
            parser.error("-RX accepts only one of --video or --camera.")
        if args.source is not None:
            parser.error("-RX does not accept --source.")
        if args.messy or args.temporal_reps != 1 or args.add_garbage > 0:
            parser.error("-RX does not accept --messy, --temporal-reps, or --add-garbage.")

    return args


def main() -> int:
    args = parse_args()
    ensure_runtime_dependencies()
    import_runtime_dependencies()

    if args.work_dir is None:
        tmp_prefix = "oft_tx_" if args.TX else "oft_rx_"
        with tempfile.TemporaryDirectory(prefix=tmp_prefix) as tmp:
            work_dir = Path(tmp)
            if args.TX:
                tx_run_pipeline(args, work_dir)
            else:
                rx_run_pipeline(args, work_dir)
    else:
        work_dir = args.work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        if args.TX:
            tx_run_pipeline(args, work_dir)
        else:
            rx_run_pipeline(args, work_dir)
        if not args.keep_intermediate:
            cleanup_intermediate(work_dir)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
