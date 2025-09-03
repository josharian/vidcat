# vidcat

Convert video files to compressed terminal ANSI streams for playback.

Inspired by [Simon Willison](https://simonwillison.net/2025/Sep/2/rich-pixels/),
who was in turn inspired by [Darren Burns](https://github.com/darrenburns/rich-pixels).

Warning: vibe-coded


## How it works

It takes a video file, extracts frames with ffmpeg, converts each frame to ANSI escape sequences with randomized pixel ordering (anti-tearing effect), and compresses the stream with zstd.


## Dependencies

ffmpeg, pv, zstd

```bash
# macOS
brew install ffmpeg pv zstd

# Ubuntu/Debian
sudo apt install ffmpeg pv zstd
```

Python dependencies are managed with uv (see script header).


## Usage

Convert a video to an ANSI stream:

```bash
uv run vidcat.py path/to/video.mp4 --width 80 --output video.zst
```

Warning big width means big output file.

vidcat prints playback instructions at the end of the conversion.


## License

MIT.

If you really want this code. But I don't know why you would.
