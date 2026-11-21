# SPDX-License-Identifier: 0BSD
"""Rehearse a silent product clip from local footage using PyAV and NumPy."""

import os
from fractions import Fraction
from itertools import chain
from math import ceil
from pathlib import Path
from tempfile import NamedTemporaryFile

import av
import numpy as np


def _sample_interval(container, stream, start, end, rate):
    """Sample the frames on screen during [start, end), relative to the first frame."""
    frames = container.decode(stream)
    current = next(frames, None)
    if current is None or current.pts is None:
        raise ValueError("The input needs a video track with timed frames.")
    origin = current.pts * current.time_base
    at = Fraction(0)
    sample_at = start
    period = 1 / rate
    for following in frames:
        if following.pts is None:
            raise ValueError("A source frame has no presentation timestamp.")
        next_at = following.pts * following.time_base - origin
        if next_at < at:
            raise ValueError("Source presentation timestamps are out of order.")
        while sample_at < min(next_at, end):
            yield current
            sample_at += period
        if next_at >= end:
            return
        current, at = following, next_at
    available = at + (current.duration * current.time_base if current.duration else period)
    if end > available:
        raise ValueError(f"Requested clip ends at {float(end):g}s; footage ends at {float(available):g}s.")
    while sample_at < end:
        yield current
        sample_at += period


def _picture(frame):
    if frame.rotation % 90:
        raise ValueError("Apply non-right-angle display rotation before exporting this clip.")
    rgb = np.rot90(frame.to_ndarray(format="rgb24"), k=frame.rotation // 90)
    height, width = rgb.shape[:2]
    if height % 2 or width % 2:
        rgb = np.pad(rgb, ((0, height % 2), (0, width % 2), (0, 0)), mode="edge")
    return av.VideoFrame.from_ndarray(np.ascontiguousarray(rgb), format="rgb24")


def _verify(path, width, height, rate, expected_frames):
    with av.open(str(path)) as container:
        if len(container.streams.video) != 1 or container.streams.audio:
            raise ValueError("Clip verification expected one silent video track.")
        stream = container.streams.video[0]
        if stream.codec_context.name != "h264":
            raise ValueError("Clip verification expected H.264.")
        count = 0
        for frame in container.decode(stream):
            if (frame.width, frame.height, frame.format.name) != (width, height, "yuv420p"):
                raise ValueError("The output frame dimensions or pixel format changed.")
            if frame.pts is None or abs(frame.pts * frame.time_base - count / rate) > stream.time_base:
                raise ValueError("Clip verification found unexpected frame timing.")
            count += 1
        if count != expected_frames or not stream.duration:
            raise ValueError("Clip verification found an incomplete video.")
        duration = stream.duration * stream.time_base
        if abs(duration - count / rate) > stream.time_base:
            raise ValueError("Clip duration does not match the decoded frames.")
        return {"width": width, "height": height, "frames": count, "fps": float(rate),
                "duration_seconds": float(duration), "codec": "h264", "pixel_format": "yuv420p",
                "audio_tracks": 0}


def make_video_clip(source, output_path, start_seconds, duration_seconds):
    """Write a verified MP4, rounding the requested length up to a whole output frame."""
    source, output = Path(source), Path(output_path)
    if not source.is_file():
        raise ValueError("Supply a local video file.")
    if source.resolve() == output.resolve() or output.suffix.lower() != ".mp4":
        raise ValueError("Choose a separate MP4 output path.")
    start, duration = Fraction(str(start_seconds)), Fraction(str(duration_seconds))
    if start < 0 or duration <= 0:
        raise ValueError("Start must be nonnegative and duration must be positive.")
    with NamedTemporaryFile(suffix=".mp4", dir=output.parent, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        # Restrict the input to self-contained camera file formats.
        options = {"format_whitelist": "mov,matroska,avi,mpegts", "protocol_whitelist": "file"}
        with source.open("rb") as handle, av.open(handle, options=options) as media:
            if not media.streams.video:
                raise ValueError("The input file has no video track.")
            track = media.streams.video[0]
            rate = Fraction(track.average_rate or track.guessed_rate or 30).limit_denominator(1001)
            if rate <= 0:
                raise ValueError("The input has an invalid frame rate.")
            frames = (_picture(f) for f in _sample_interval(media, track, start, start + duration, rate))
            first = next(frames)
            with av.open(str(temporary), "w", format="mp4", options={"movflags": "+faststart"}) as movie:
                video = movie.add_stream("libx264", rate=rate, options={"preset": "fast", "crf": "18"})
                video.width, video.height, video.pix_fmt = first.width, first.height, "yuv420p"
                count = 0
                for frame in chain((first,), frames):
                    if (frame.width, frame.height) != (first.width, first.height):
                        raise ValueError("Source dimensions changed within the selected clip.")
                    frame.pts, frame.time_base = count, 1 / rate
                    movie.mux(video.encode(frame))
                    count += 1
                movie.mux(video.encode())
        result = _verify(temporary, first.width, first.height, rate, ceil(duration * rate))
        os.replace(temporary, output)
        return result
    finally:
        temporary.unlink(missing_ok=True)
