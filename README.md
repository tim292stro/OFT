# OFT (Optical File Transfer)

OFT is a Python CLI tool that enables transfering files and folders optically by converting payload data into QR frames (TX mode) as a video file to be played and reconstructing the original data from a video or webcam stream (RX mode).  I don't do this for a living, just needed a tool that I couldn't find or understand the code for.  If it is useful to you, awesome.

***DISCLAIMER***

I do not write code for a living, and thought I tried to make this reasonably stable for myself, I make no promises about functionality in your individual use case.  I do not have the available free time to hand-hold through debugging, sorry.  I am also not a security researcher, so I probably created a stack of vulnerabilites I didn't even know to look for in my code, sorry - but that's why I'm providing it as un-compiled Python, if you are a security researcher, I'll happily consider patches.

Current version: **1.0.0**

## How OFT Works

### TX pipeline (source -> QR video)

1. Collect the source input (single file or folder).
2. Build an encrypted/split 7-Zip payload (`payload.7z.001`, etc.).
3. Pack each split part into framed QR chunks.
4. Render QR frames to a video via ffmpeg.  Note, this entire process is a data expansion.  The video file is very likely going to be much larger than your source file or folder, as in geometricly bigger (I've seen 20MB -> 3GB in testing).  Wield this sword with care on your system, you must have the space to generate the intermediate files and the final video.  Obviously the larger the file, the longer the video will be, so this method becomes impractical for larger files simply due to the risk of missed codes on the RX end.

### RX pipeline (video/webcam -> recovered output)

1. Read QR frames from a video file or webcam stream.  Note that a camera with a telephoto lens makes this a long-distance transfer.  I have also considered but not yet had a need to implement taking in web streams.  Feels wasteful to do it this way if you already have a network connection, but there is a case that a mutli-cast stream just sending a file that a bunch of connected clients might want to receive could be usefull, so I might still do this later (my leisure, no time-line proposed or committed to).  Similarly there is nothing stopping anyone from pointing more than one camera at a single display playing the TX generated file to the same end, or abusing the temporal-reps command line option to set up an all-day video output that people could walk up and grab a file - that was why I implemented the password option, to at least access control the source.
2. Decode and de-duplicate chunk IDs.
3. Rebuild `payload.7z.001` from chunk sequence.
4. Extract with 7-Zip using optional password.  Note that I have not exhaustively tested password length and complexity - keep it to UTF-8.
5. Write recovered file/folder to the requested output path.

### Note ###

There are some options about 7-zip and ffmpeg when they are not discoverable in the PATH variable which I find are annoying to keep typing out, so this code will create a persistence file (JSON) in the OFT.py script's working directory so it can just use it next call without having to type it out.  It's nothing crazy or risky, just some key:value pairs in human readable form.  I got tired of walking up to a mahcine that didn't have the environment setup well for this to work without a lot of fidgeting, so I brute forced it.  On that topic, same thing (attempt-auto-fix) with package dependancies.  You're welcome I guess.

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
python OFT.py -TX --source ./Example_Folder_Input -o ./Test_Output/roundtrip_tx.mp4 -p UltraSecretPassword
```

### RX example

```bash
python OFT.py -RX --video ./Test_Output/roundtrip_tx.mp4 -o ./Test_Output/recovered -p UltraSecretPassword
```

### Show dependency license report

```bash
python OFT.py --license-report
```

## More notes, because this is how my mind works, welcome to the mess

- Obviously use matching password and source video between TX and RX, this code uses the built-in AES encryption of 7-Zip when a password is specified, to my knowledge there is no back door to this.  I have only tested this with UTF-8 characters, and have no interest in adding more functionality here.
- If RX decodes all chunks but the extraction fails, check password correctness and archive integrity, try adding temporal repetitions if code blocks are missed.
- If you wish to have this execute as a native application, try pycompiler (no support for this given here)

## Requirements

- Python 3.10+, if you're using <3.9... why? Who hurt you?
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
