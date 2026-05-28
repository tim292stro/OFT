# OFT Test Bench

This document defines the full OFT test bench routine and references the automation script in OFTTestBench.py.

## Goal

Validate end-to-end TX/RX behavior under normal and robustness-oriented conditions by:

1. Generating controlled binary fixtures.
2. Performing local MP4 round-trip testing first.
3. Optionally running camera-based capture by fullscreen playback through ffplay when a webcam is available.

## Components Added

### OFT.py helper functions

- generate_random_2kb_bin_file(output_path: Path) -> Path
- create_oft_test_folder(base_dir: Path | None = None) -> Path

Behavior:

- generate_random_2kb_bin_file writes exactly 2048 bytes of random data to a .bin file.
- create_oft_test_folder creates OFT_Test folder and writes two unique random 2048-byte .bin files into it.

### Test bench runner

- Script: OFTTestBench.py

The runner imports OFT.py, creates fixtures, then runs TX->RX verification cases.

## Full Test Routine

### Stage 1: Fixture generation

1. Create a timestamped bench artifact root under Test_Output.
2. Generate one standalone random 2KB file.
3. Create OFT_Test folder containing two unique random 2KB files.

### Stage 2: Local MP4 round-trip (first)

For each source type:

- File source (single .bin)
- Folder source (OFT_Test)

Run TX then RX with local MP4 files across robustness option combinations.

Exhaustive matrix:

- messy in {false, true}
- temporal-reps in {1, 2}
- add-garbage in {0, 8}

Total local cases: 2 source types x 2 x 2 x 2 = 16.

Per-case verification:

- File source: SHA-256 of source vs recovered file.
- Folder source: recursive file-manifest SHA-256 comparison or source vs recovered folder.

### Stage 3: Optional webcam playback/capture

1. Probe for an available local webcam.
2. If not found, camera test is skipped.
3. If webcam is found:
   - Generate a robust TX video.
   - Start RX in camera mode.
   - Launch ffplay fullscreen playback of the TX video.
   - Validate recovered file hash.

If ffplay is missing, the camera test is skipped.

## How to Run

### Full exhaustive bench

```bash
python OFTTestBench.py
```

### Quick bench

```bash
python OFTTestBench.py --quick
```

### Tune camera capture limit

```bash
python OFTTestBench.py --camera-max-duration 120
```

### Camera-only rerun

```bash
python OFTTestBench.py --camera-only --camera-max-duration 120
```

## Outputs

Each run creates:

- Test_Output/bench_TIMESTAMP/

Containing:

- Source fixtures
- Generated TX .mp4 files
- Recovered RX outputs
- Artifacts needed to inspect failures

The script prints a summary with:

- Passed case count
- Failed case count
- Artifact directory path

Exit code:

- 0 when all executed tests pass
- 1 when any case fails

## Notes

- Local MP4 round-trip tests always run before any camera attempt.
- Use --camera-only to rerun only the camera phase without rerunning the local matrix.
- Camera tests depend on physical setup (display-to-camera visibility, focus, exposure).
- Robustness options are TX-only: --messy, --temporal-reps, --add-garbage.
- This is a test bench, not the tool.  I spent most of my development time on the tool.  It shows. Sorry.
