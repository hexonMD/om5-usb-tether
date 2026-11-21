"""Find focus with relative lens moves and optical feedback from OM-5 USB previews."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import struct
import time

import cv2
import numpy as np

from om5_wpd_photo import Camera


def preview(camera):
    for _ in range(20):
        try:
            data = camera.operation(0x9484, [1])
        except RuntimeError as err:
            if '2019' not in str(err):
                raise
            data = b''
        start, end = data.find(b'\xff\xd8\xff'), data.rfind(b'\xff\xd9')
        if start >= 0 and end > start:
            return data[start:end + 2]
        time.sleep(0.1)
    raise RuntimeError('No JPEG preview received')


def measure(data):
    gray = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    blurred = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.1)
    energy = cv2.magnitude(cv2.Sobel(blurred, cv2.CV_32F, 1, 0),
                           cv2.Sobel(blurred, cv2.CV_32F, 0, 1))
    h, w = energy.shape
    return float(np.percentile(energy[h//6:5*h//6, w//6:5*w//6], 98))


def search(directory, direction):
    directory.mkdir(exist_ok=False)
    camera = Camera([], 'readwrite', 'remote')
    readings = []
    pos = 0

    def visit(target):
        nonlocal pos
        while pos != target:
            step = 1 if target > pos else -1
            camera.operation(0x9487, [direction if step > 0 else 3-direction, 60], no_data=True)
            pos += step
        time.sleep(.30)
        data = preview(camera)
        path = directory / f'preview-{len(readings):03d}.jpg'
        path.write_bytes(data)
        score = measure(data)
        readings.append(dict(position=pos, score=score, file=path.name))
        print(f'{pos:4} {score:.1f}', flush=True)
        return score

    try:
        camera.open()
        with ExitStack() as cleanup:
            focus = camera.prop(0xd003)
            if focus != 1:
                camera.setprop(0xd003, 1)
                cleanup.callback(camera.setprop, 0xd003, focus)
            mode = camera.prop(0xd052)
            if mode != 1:
                camera.setprop(0xd052, 1)
                cleanup.callback(camera.setprop, 0xd052, mode)
            lv = camera.operation(0x1015, [0xd06d])
            if not any(lv):
                camera.operation(0x1016, [0xd06d], data=struct.pack('<I', 0x050003c0))
                cleanup.callback(camera.operation, 0x1016, [0xd06d], data=lv)
            time.sleep(.6)
            start = best = visit(0)
            best_pos = 0
            for target in range(8, 225, 8):
                score = visit(target)
                if score > best:
                    best, best_pos = score, target
                if best > max(start*2.5, 28) and target >= best_pos+8 and score < best*.7:
                    break
            if best > max(start*1.5, 20):
                visit(best_pos)
                for target in range(best_pos-4, best_pos+5, 2):
                    score = visit(target)
                    if score > best:
                        best, best_pos = score, target
                visit(best_pos)
            (directory/'final.jpg').write_bytes(preview(camera))
    finally:
        camera.close()
        (directory/'measurements.json').write_text(json.dumps(readings, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--direction', choices=['near','far'], required=True)
    args = parser.parse_args()
    search(args.directory, 2 if args.direction == 'far' else 1)
