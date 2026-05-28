# OFT (Optical File Transfer)

OFT is a Python CLI tool that transfers files and folders optically by converting payload data into QR frames (TX mode) and reconstructing the original data from a video or webcam stream (RX mode).

Current version: **1.0.0**

## How OFT Works

### TX pipeline (source -> QR video)

1. Collect the source input (single file or folder).
2. Build an encrypted/split 7-Zip payload (`payload.7z.001`, etc.).
3. Pack each split part into framed QR chunks.
4. Render QR frames to a video via ffmpeg.

### RX pipeline (video/webcam -> recovered output)

1. Read QR frames from a video file or webcam stream.
2. Decode and de-duplicate chunk IDs.
3. Rebuild `payload.7z.001` from chunk sequence.
4. Extract with 7-Zip using optional password.
5. Write recovered file/folder to the requested output path.

## Documentation

- [TX mode guide](TX.md)
- [RX mode guide](RX.md)
- [Test bench guide](TEST_BENCH.md)

## Quick Start

### Show version

```bash
python OFT.py -v
```

### TX example

```bash
python OFT.py -TX --source ./Example_Folder_Input -o ./Test_Output/roundtrip_tx.mp4 -p GooberSnookums
```

### RX example

```bash
python OFT.py -RX --video ./Test_Output/roundtrip_tx.mp4 -o ./Test_Output/recovered -p GooberSnookums
```

### Show dependency license report

```bash
python OFT.py --license-report
```

## Notes

- Use matching password and source video between TX and RX.
- If RX decodes all chunks but extraction fails, check password correctness and archive integrity.

## Requirements

- Python 3.10+
- 7-Zip executable available on PATH, or configured in OFT persistence
- ffmpeg executable available on PATH for TX mode, or configured in OFT persistence

OFT can attempt runtime dependency resolution for Python packages when missing.

## License

### OFT license

OFT is released under the **MIT License**.

Copyright (c) 2026 Tim Strommen

### Third-party Python dependencies

OFT uses these Python packages at runtime:

- `opencv-python` (Apache-2.0)
- `zxing-cpp` (Apache-2.0)
- `Pillow` (MIT-CMU / MIT family)
- `qrcodegen` (MIT)

For detailed license metadata and inferred sources, see:

- [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)

### External executables

OFT also relies on external binaries:

- 7-Zip (archive create/extract)
- ffmpeg (video rendering in TX mode)

These are distributed separately and have their own licenses depending on the installed build/vendor. Validate redistribution terms for the exact binaries you ship.
