# SPDX-License-Identifier: 0BSD
"""Merge aligned focus planes into a JPEG. Requires OpenCV, NumPy and Pillow."""

import argparse
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2
import numpy as np
from PIL import Image, ImageOps


def read_rgb(path):
    with Image.open(path) as image:
        profile = image.info.get("icc_profile")
        pixels = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
    return pixels, profile


def score_image(rgb, map_size):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if (gray.shape[1], gray.shape[0]) != map_size:
        gray = cv2.resize(gray, map_size, interpolation=cv2.INTER_AREA)
    # Smooth noise before differentiation. Integrating edge energy over a
    # neighborhood also keeps the blurred shoulders of an edge with its core.
    gray = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.4)
    dx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    dy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    return cv2.GaussianBlur(dx * dx + dy * dy, (0, 0), 24)


def merge(paths, output):
    if len(paths) < 2:
        raise ValueError("Supply at least two aligned input frames.")
    if output.resolve() in {p.resolve() for p in paths}:
        raise ValueError("Choose an output filename separate from the input frames.")

    # Keep only the current best score and its frame index. Ties retain the
    # earlier input. All coordinates are in the EXIF-corrected orientation.
    for i, path in enumerate(paths):
        rgb, profile = read_rgb(path)
        h, w = rgb.shape[:2]
        if i == 0:
            size = (w, h)
            common_profile = profile
            scale = min(1, 2048 / max(size))
            map_size = tuple(max(1, round(n * scale)) for n in size)
            best = np.full(map_size[::-1], -np.inf, dtype=np.float32)
            winner = np.zeros(map_size[::-1], dtype=np.int32)
        elif size != (w, h):
            raise ValueError(f"{path} has different dimensions after EXIF orientation.")
        elif profile != common_profile:
            raise ValueError("Convert the input frames to a common color profile first.")
        score = score_image(rgb, map_size)
        stronger = score > best
        winner[stronger] = i
        np.maximum(best, score, out=best)

    del best, score, stronger, rgb
    result = np.zeros((size[1], size[0], 3), dtype=np.float32)
    for i, path in enumerate(paths):
        rgb, _ = read_rgb(path)
        weight = cv2.GaussianBlur((winner == i).astype(np.float32), (0, 0), 2)
        if map_size != size:
            weight = cv2.resize(weight, size, interpolation=cv2.INTER_LINEAR)
        # Feathered masks sum to one. Process rows in strips to limit the
        # temporary float allocations for full-resolution camera images.
        for start in range(0, size[1], 256):
            rows = slice(start, start + 256)
            result[rows] += rgb[rows] * weight[rows, :, None]
    np.rint(result, out=result)
    np.clip(result, 0, 255, out=result)
    finished = Image.fromarray(result.astype(np.uint8))
    with NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        finished.save(temporary, format="JPEG", quality=96, subsampling=0, icc_profile=common_profile)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path, nargs="+", help="aligned input frames in capture order")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output JPEG path")
    args = parser.parse_args()
    try:
        merge(args.frames, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
