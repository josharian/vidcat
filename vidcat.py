# /// script
# dependencies = [
#   "rich",
#   "rich-pixels",
#   "pillow",
# ]
# ///
"""
Convert video files to compressed terminal ANSI streams for playback.

Usage:
  python vidcat.py path/to/video.mp4 --width 80 --output video.zst
"""

import argparse
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Generator, Tuple

from PIL import Image
from rich_pixels import Pixels


class VideoProcessor:
    def __init__(self, video_path: Path, terminal_width: int):
        self.video_path = video_path
        self.terminal_width = terminal_width
        self.fps = None
        self.width = None
        self.height = None
        self.frame_count = None
        self.terminal_height = None

    def get_video_metadata(self) -> dict:
        """Extract video metadata using ffprobe."""
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(self.video_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            metadata = json.loads(result.stdout)

            # Find video stream
            video_stream = None
            for stream in metadata["streams"]:
                if stream["codec_type"] == "video":
                    video_stream = stream
                    break

            if not video_stream:
                raise ValueError("No video stream found")

            self.width = int(video_stream["width"])
            self.height = int(video_stream["height"])

            # Parse FPS from r_frame_rate (e.g., "30/1" or "24000/1001")
            fps_parts = video_stream["r_frame_rate"].split("/")
            self.fps = float(fps_parts[0]) / float(fps_parts[1])

            # Get frame count if available
            if "nb_frames" in video_stream:
                self.frame_count = int(video_stream["nb_frames"])
            else:
                # Calculate from duration and fps
                duration = float(metadata["format"]["duration"])
                self.frame_count = int(duration * self.fps)

            return {
                "fps": self.fps,
                "width": self.width,
                "height": self.height,
                "frame_count": self.frame_count,
            }

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffprobe failed: {e.stderr}")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse ffprobe output: {e}")

    def calculate_terminal_dimensions(self) -> Tuple[int, int]:
        """Calculate terminal height based on video aspect ratio."""
        if not self.width or not self.height:
            raise ValueError("Video dimensions not set - call get_video_metadata first")

        aspect_ratio = self.width / self.height
        # Rich-pixels uses half-blocks, so 1 terminal row = 2 image pixels
        self.terminal_height = int(self.terminal_width / aspect_ratio / 2)

        # Ensure even height for proper half-block rendering
        if self.terminal_height % 2:
            self.terminal_height -= 1

        # Make sure we have at least 2 rows
        self.terminal_height = max(2, self.terminal_height)

        return self.terminal_width, self.terminal_height

    def extract_frames(self, output_dir: Path) -> Generator[Path, None, None]:
        """Extract video frames using ffmpeg."""
        cmd = [
            "ffmpeg",
            "-v",
            "quiet",  # Suppress ffmpeg output
            "-nostats",  # Disable progress stats
            "-i",
            str(self.video_path),
            "-y",  # Overwrite output files
            "-vf",
            "scale={}:{}".format(
                self.terminal_width,
                self.terminal_height * 2,  # *2 because rich-pixels uses half-blocks
            ),
            str(output_dir / "frame_%06d.png"),
        ]

        try:
            subprocess.run(cmd, capture_output=True, check=True)

            # Yield frame paths in order
            frame_files = sorted(output_dir.glob("frame_*.png"))
            for frame_file in frame_files:
                yield frame_file

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffmpeg frame extraction failed: {e.stderr.decode()}")


class ANSIFrameGenerator:
    def __init__(self, terminal_width: int, terminal_height: int):
        self.terminal_width = terminal_width
        self.terminal_height = terminal_height

    def frame_to_ansi(self, image_path: Path) -> str:
        """Convert a frame image to ANSI escape sequences using relative positioning."""
        with Image.open(image_path) as img:
            # Use rich-pixels to get the half-block representation
            pixels = Pixels.from_image(img)

            # Get the raw ANSI output from rich-pixels
            from rich.console import Console
            from io import StringIO

            string_buffer = StringIO()
            console = Console(
                file=string_buffer,
                width=self.terminal_width,
                height=self.terminal_height,
                force_terminal=True,
                color_system="truecolor",
            )
            console.print(pixels)
            ansi_output = string_buffer.getvalue()
            string_buffer.close()

            # Move cursor up by frame height and use the clean output
            frame_data = f"\033[{self.terminal_height}A"  # Move cursor up
            frame_data += ansi_output

            return frame_data


def compress_frames_to_file(
    frame_data_list: list[str], output_path: Path, terminal_height: int
) -> dict:
    """Compress all frame data to file using zstd."""
    # Write all frames to a temporary uncompressed file
    temp_file = output_path.with_suffix(".ansi.tmp")

    try:
        with open(temp_file, "w") as f:
            # Add initial newlines for scrollback space
            f.write("\n" * terminal_height)

            # Hide cursor at start of video
            f.write("\033[?25l")

            for frame_data in frame_data_list:
                f.write(frame_data)

            # Show cursor at end of video
            f.write("\033[?25h")

        # Compress with zstd
        subprocess.run(
            ["zstd", "-f", str(temp_file), "-o", str(output_path)], check=True
        )

        # Calculate statistics
        frame_sizes = [
            len(frame_data.encode("utf-8")) for frame_data in frame_data_list
        ]

        return {
            "frame_sizes": frame_sizes,
            "total_frames": len(frame_sizes),
            "avg_frame_size": sum(frame_sizes) / len(frame_sizes) if frame_sizes else 0,
        }
    finally:
        # Clean up temp file
        if temp_file.exists():
            temp_file.unlink()


def generate_playback_command(
    output_path: Path, fps: float, avg_frame_size: float
) -> str:
    """Generate the command to play back the compressed video stream."""
    bytes_per_second = int(avg_frame_size * fps)
    cmd = f"cat {output_path} | zstd -d | pv -q -L {bytes_per_second}"
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert video to compressed terminal ANSI stream"
    )
    parser.add_argument("video", type=Path, help="Path to the video file")
    parser.add_argument(
        "--width", type=int, default=80, help="Terminal width in columns (default: 80)"
    )
    parser.add_argument(
        "--output", type=Path, help="Output file path (default: video_name.zst)"
    )

    args = parser.parse_args()

    if not args.video.exists():
        print(f"Error: Video file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    if not args.output:
        args.output = args.video.with_suffix(".zst")

    print(f"Processing video: {args.video}")
    print(f"Terminal width: {args.width}")
    print(f"Output: {args.output}")

    try:
        # Initialize video processor
        processor = VideoProcessor(args.video, args.width)

        # Get video metadata
        print("Extracting video metadata...")
        metadata = processor.get_video_metadata()
        print(
            f"Video: {metadata['width']}x{metadata['height']}, {metadata['fps']:.2f} fps, {metadata['frame_count']} frames"
        )

        # Calculate terminal dimensions
        term_width, term_height = processor.calculate_terminal_dimensions()
        print(f"Terminal dimensions: {term_width}x{term_height}")

        # Initialize ANSI generator
        ansi_gen = ANSIFrameGenerator(term_width, term_height)

        # Process frames
        all_frame_data = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            print("Extracting frames...")

            frame_count = 0
            for frame_path in processor.extract_frames(temp_path):
                print(f"Processing frame {frame_count + 1}...", end="\r")

                # Convert frame to ANSI
                ansi_frame = ansi_gen.frame_to_ansi(frame_path)
                all_frame_data.append(ansi_frame)

                frame_count += 1

            print(f"\nProcessed {frame_count} frames")

        # Compress all frames
        print("Compressing frames...")
        compression_metadata = compress_frames_to_file(
            all_frame_data, args.output, term_height
        )

        # Generate playback instructions
        playback_cmd = generate_playback_command(
            args.output, metadata["fps"], compression_metadata["avg_frame_size"]
        )

        print(f"\nOutput file: {args.output}")
        print(f"Compressed {compression_metadata['total_frames']} frames")
        print(f"Average frame size: {compression_metadata['avg_frame_size']:.0f} bytes")
        print(f"\nTo play back:")
        print(f"  {playback_cmd}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
