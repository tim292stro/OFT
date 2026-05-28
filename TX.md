# TX Mode (Transmit)

TX mode converts a file or folder into a QR video that can later be decoded by RX mode. It is up to the user how that video file gets off the source machine. Personally I recommend ffplay fullscreen since this codebase already relies on ffmpeg, and it gets installed already by the package.

Back to summary: [README](README.md)

## Purpose

Use TX when you want to package source data into a sequence of QR chunks and render those chunks into a video file.

## Required TX arguments

- `-TX`
- `--source <path>` (file or folder)
- `-o, --output <video-path>`

## Optional TX arguments

- `--work-dir <path>`
- `--keep-intermediate`
- `--sevenzip-bin <path-or-name>`
- `--ffmpeg-bin <path-or-name>`
- `-p, --password <text>`
- `--qr-workers <int>` (`0` uses all CPU cores)
- `--messy` (shuffle QR frame order for robustness testing)
- `--temporal-reps <int>` (repeat the QR sequence N times; default `1`)
- `--add-garbage <int>` (append N garbage QR frames for robustness testing)

## TX usage examples

### Basic folder transmit

```bash
python OFT.py -TX --source ./Example_Folder_Input -o ./Test_Output/roundtrip_tx.mp4
```

### Password-protected transmit

```bash
python OFT.py -TX --source ./Example_File_Input.pdf -o ./Test_Output/roundtrip_tx.mp4 -p ExampleSecretPassword
```

### Explicit executable paths

```bash
python OFT.py -TX --source ./Example_Folder_Input -o ./Test_Output/roundtrip_tx.mp4 --sevenzip-bin "C:/Program Files/7-Zip/7z.exe" --ffmpeg-bin ffmpeg
```

## TX behavior notes

- TX does not accept `--video` or `--camera`.
- `--messy`, `--temporal-reps`, and `--add-garbage` are TX-only options and are not accepted in RX mode.
- Password is optional, but if used in TX it must match during RX extraction.
- Output is a video file containing encoded QR frames.

## Related docs

- [RX mode guide](RX.md)
- [Third-party notices](THIRD_PARTY_NOTICES.txt)
