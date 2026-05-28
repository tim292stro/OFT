#!/usr/bin/env python3
"""OFT end-to-end test bench.

This script exercises TX/RX round trips for local MP4 inputs across a matrix of
robustness options and can optionally run a webcam capture pass using ffplay.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Sequence

import OFT


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _dir_manifest(root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            manifest[rel] = _sha256_file(path)
    return manifest


def _run_cmd(cmd: Sequence[str], cwd: Path) -> int:
    print("\n$", " ".join(cmd), flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip("\n"))
    return proc.wait()


def _python_cmd() -> str:
    return sys.executable


def _build_profile_matrix(exhaustive: bool) -> list[dict[str, object]]:
    if not exhaustive:
        return [
            {"name": "baseline", "messy": False, "temporal_reps": 1, "add_garbage": 0},
            {"name": "robust", "messy": True, "temporal_reps": 2, "add_garbage": 8},
        ]

    profiles: list[dict[str, object]] = []
    for messy, temporal_reps, add_garbage in itertools.product(
        (False, True),
        (1, 2),
        (0, 8),
    ):
        if temporal_reps == 1 and add_garbage == 0 and not messy:
            name = "baseline"
        else:
            name = (
                f"m{int(messy)}_t{temporal_reps}_g{add_garbage}"
            )
        profiles.append(
            {
                "name": name,
                "messy": messy,
                "temporal_reps": temporal_reps,
                "add_garbage": add_garbage,
            }
        )
    return profiles


def _tx_args(profile: dict[str, object]) -> list[str]:
    args: list[str] = []
    if bool(profile["messy"]):
        args.append("--messy")
    temporal_reps = int(profile["temporal_reps"])
    add_garbage = int(profile["add_garbage"])
    if temporal_reps != 1:
        args.extend(["--temporal-reps", str(temporal_reps)])
    if add_garbage > 0:
        args.extend(["--add-garbage", str(add_garbage)])
    return args


def _detect_webcam(max_index: int = 4) -> int | None:
    try:
        OFT.ensure_runtime_dependencies()
        OFT.import_runtime_dependencies()
        cv2 = OFT.cv2
    except Exception:
        return None

    for index in range(max_index):
        cap = cv2.VideoCapture(index)
        try:
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    return index
        finally:
            cap.release()
    return None


def _launch_camera_roundtrip(
    workspace: Path,
    tx_video: Path,
    camera_index: int,
    output_path: Path,
    max_duration: float,
) -> bool:
    ffplay_bin = shutil.which("ffplay")
    if ffplay_bin is None:
        print("[skip] ffplay not found on PATH; skipping camera playback test.")
        return False

    rx_cmd = [
        _python_cmd(),
        "OFT.py",
        "-RX",
        "--camera",
        str(camera_index),
        "--idle-timeout",
        "4",
        "--max-duration",
        str(max_duration),
        "-o",
        str(output_path),
    ]
    print("\n$", " ".join(rx_cmd), flush=True)
    rx_proc = subprocess.Popen(
        rx_cmd,
        cwd=str(workspace),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # Wait until RX reports webcam source readiness before playback starts.
    rx_output: list[str] = []
    ready_event = threading.Event()

    def _read_rx_output() -> None:
        if rx_proc.stdout is None:
            return
        for line in rx_proc.stdout:
            text = line.rstrip("\n")
            rx_output.append(text)
            if "webcam source opened" in text or "webcam mode: stdin not interactive" in text:
                ready_event.set()

    reader_thread = threading.Thread(target=_read_rx_output, daemon=True)
    reader_thread.start()

    ready_event.wait(timeout=12.0)
    # Give camera capture a brief warmup after readiness.
    time.sleep(1.2)

    ffplay_cmd = [ffplay_bin, "-loglevel", "error", "-autoexit", "-fs", str(tx_video)]
    ffplay_rc = _run_cmd(ffplay_cmd, workspace)

    try:
        rx_rc = rx_proc.wait(timeout=max(5.0, max_duration + 5.0))
    except subprocess.TimeoutExpired:
        rx_proc.kill()
        rx_rc = rx_proc.wait()

    reader_thread.join(timeout=2.0)

    for line in rx_output:
        print(line)

    if ffplay_rc != 0:
        print(f"[warn] ffplay exited with code {ffplay_rc}")
    if rx_rc != 0:
        print(f"[warn] camera RX exited with code {rx_rc}")

    return ffplay_rc == 0 and rx_rc == 0


def run_bench(args: argparse.Namespace) -> int:
    workspace = Path(__file__).resolve().parent
    bench_root = (workspace / "Test_Output" / f"bench_{int(time.time())}").resolve()
    bench_root.mkdir(parents=True, exist_ok=True)

    print(f"[info] Bench root: {bench_root}")

    source_file = OFT.generate_random_2kb_bin_file(bench_root / "source_2kb.bin")
    source_folder = OFT.create_oft_test_folder(bench_root)

    source_specs = [
        ("file", source_file),
        ("folder", source_folder),
    ]

    profiles = _build_profile_matrix(exhaustive=not args.quick)
    total_cases = len(source_specs) * len(profiles)
    if not args.camera_only:
        print(f"[info] Running {total_cases} local MP4 round-trip cases")
    else:
        print("[info] Camera-only mode enabled; skipping local MP4 matrix.")

    failures: list[str] = []
    passed = 0

    if not args.camera_only:
        for source_kind, source_path in source_specs:
            for profile in profiles:
                case_name = f"{source_kind}_{profile['name']}"
                tx_video = bench_root / f"{case_name}.mp4"
                rx_output = bench_root / f"recovered_{case_name}"

                tx_cmd = [
                    _python_cmd(),
                    "OFT.py",
                    "-TX",
                    "--source",
                    str(source_path),
                    "-o",
                    str(tx_video),
                    *_tx_args(profile),
                ]
                tx_rc = _run_cmd(tx_cmd, workspace)
                if tx_rc != 0:
                    failures.append(f"{case_name}: TX failed ({tx_rc})")
                    continue

                rx_cmd = [
                    _python_cmd(),
                    "OFT.py",
                    "-RX",
                    "--video",
                    str(tx_video),
                    "-o",
                    str(rx_output),
                ]
                rx_rc = _run_cmd(rx_cmd, workspace)
                if rx_rc != 0:
                    failures.append(f"{case_name}: RX(video) failed ({rx_rc})")
                    continue

                if source_kind == "file":
                    src_hash = _sha256_file(source_path)
                    out_hash = _sha256_file(rx_output)
                    if src_hash != out_hash:
                        failures.append(f"{case_name}: file hash mismatch")
                        continue
                else:
                    src_manifest = _dir_manifest(source_path)
                    out_manifest = _dir_manifest(rx_output)
                    if src_manifest != out_manifest:
                        failures.append(f"{case_name}: folder manifest mismatch")
                        continue

                passed += 1
                print(f"[pass] {case_name}")

    camera_index = _detect_webcam()
    if camera_index is None:
        print("[skip] No webcam detected; skipping camera playback test.")
    else:
        tx_video_for_camera = bench_root / "camera_source.mp4"
        camera_source = source_file

        tx_cmd = [
            _python_cmd(),
            "OFT.py",
            "-TX",
            "--source",
            str(camera_source),
            "-o",
            str(tx_video_for_camera),
            "--messy",
            "--temporal-reps",
            "2",
            "--add-garbage",
            "8",
        ]
        tx_rc = _run_cmd(tx_cmd, workspace)
        if tx_rc != 0:
            failures.append(f"camera_prepare: TX failed ({tx_rc})")
        else:
            camera_output = bench_root / "recovered_camera.bin"
            print("[info] Webcam found. Running ffplay fullscreen playback + RX camera capture.")
            ok = _launch_camera_roundtrip(
                workspace=workspace,
                tx_video=tx_video_for_camera,
                camera_index=camera_index,
                output_path=camera_output,
                max_duration=args.camera_max_duration,
            )
            if not ok:
                failures.append("camera_roundtrip: ffplay or RX(camera) failed")
            elif _sha256_file(source_file) != _sha256_file(camera_output):
                failures.append("camera_roundtrip: file hash mismatch")
            else:
                passed += 1
                print("[pass] camera_roundtrip")

    print("\n=== OFT Test Bench Summary ===")
    print(f"Passed cases: {passed}")
    print(f"Failed cases: {len(failures)}")
    print(f"Artifacts: {bench_root}")

    if failures:
        print("Failures:")
        for item in failures:
            print(f"- {item}")
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run OFT local and optional webcam test bench routines."
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run a smaller local profile set instead of full exhaustive matrix.",
    )
    parser.add_argument(
        "--camera-max-duration",
        type=float,
        default=90.0,
        help="Max RX camera capture duration for ffplay/webcam test.",
    )
    parser.add_argument(
        "--camera-only",
        action="store_true",
        help="Run only the ffplay/webcam camera round-trip phase.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_bench(parse_args()))
