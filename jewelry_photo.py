"""Build and measure a still from aligned JPEG focus planes."""

from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2
import numpy as np
from PIL import Image, ImageOps

if __package__:
    from .focus_stack import merge
else:
    from focus_stack import merge


def _read_jpeg(path):
    with Image.open(path, formats=("JPEG",)) as image:
        return np.asarray(ImageOps.exif_transpose(image).convert("RGB"))


def _quality(rgb):
    """Whole-frame statistics for comparing images at the same resolution."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_16S, ksize=1)
    _, deviation = cv2.meanStdDev(laplacian)
    near_white = cv2.countNonZero(cv2.inRange(rgb, (250, 250, 250), (255, 255, 255)))
    below_ceiling = cv2.countNonZero(cv2.inRange(rgb, (0, 0, 0), (254, 254, 254)))
    return {
        "luma_laplacian_variance": round(float(deviation[0, 0]) ** 2, 4),
        "any_rgb_channel_at_255_fraction": (gray.size - below_ceiling) / gray.size,
        "all_rgb_channels_at_least_250_fraction": near_white / gray.size,
    }


def make_focus_still(frames, output_path) -> dict:
    """Merge aligned JPEGs, verify the finished file, and return review data.

    Duplicate indices are one-based. Clipping and sharpness readings describe
    the entire decoded image, including the background and metal reflections.
    """
    paths = [Path(frame) for frame in frames]
    output = Path(output_path)
    if len(paths) < 2:
        raise ValueError("Supply at least two aligned JPEG focus frames.")
    if output.resolve() in {path.resolve() for path in paths}:
        raise ValueError("The output path must be separate from the input frames.")

    first_seen = {}
    records = []
    size = None
    for index, path in enumerate(paths, 1):
        rgb = _read_jpeg(path)
        height, width = rgb.shape[:2]
        if size is None:
            size = (width, height)
        elif size != (width, height):
            raise ValueError(f"Frame {index} has different dimensions after orientation.")
        pixel_hash = sha256(rgb).hexdigest()
        records.append({
            "index": index,
            "file": path.name,
            "pixel_sha256": pixel_hash,
            "duplicate_of": first_seen.get(pixel_hash),
        })
        first_seen.setdefault(pixel_hash, index)
        del rgb
    if len(first_seen) < 2:
        raise ValueError("All focus frames have identical decoded pixels.")

    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, delete=False) as target:
        temporary = Path(target.name)
    try:
        merge(paths, temporary)
        rgb = _read_jpeg(temporary)
        height, width = rgb.shape[:2]
        if (width, height) != size:
            raise ValueError("The merged JPEG has different dimensions from its inputs.")
        report = {
            "file": output.name,
            "sha256": sha256(temporary.read_bytes()).hexdigest(),
            "width": width,
            "height": height,
            "input_count": len(paths),
            "distinct_input_frames": len(first_seen),
            "frames": records,
            "quality": _quality(rgb),
        }
        os.replace(temporary, output)
        return report
    finally:
        temporary.unlink(missing_ok=True)
