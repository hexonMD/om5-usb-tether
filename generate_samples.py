"""Generate an offline focus target and short video for the Studio sample job."""
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def main():
    output = Path(__file__).resolve().parent / "samples"
    output.mkdir(exist_ok=True)
    target = Image.new("RGB", (720, 360), "#182228")
    draw = ImageDraw.Draw(target)
    for center, color in zip((120, 360, 600), ("#f6cf84", "#d9dad7", "#dca786")):
        for radius in range(8, 101, 5):
            draw.ellipse((center-radius, 180-radius, center+radius, 180+radius),
                         outline=color, width=2)
    blurred = target.filter(ImageFilter.GaussianBlur(4))
    frames = []
    for index, plane in enumerate(("far", "middle", "near")):
        frame = blurred.copy()
        box = (index * 240, 0, (index + 1) * 240, 360)
        frame.paste(target.crop(box), box)
        frame.save(output / f"om5-usb-connected-stack-{plane}.jpg", quality=94)
        frames.append(np.asarray(frame))
    with av.open(str(output / "om5-video-focus-sweep.mov"), "w") as movie:
        stream = movie.add_stream("mpeg4", rate=24)
        stream.width, stream.height = target.size
        stream.pix_fmt = "yuv420p"
        for index in range(36):
            frame = av.VideoFrame.from_ndarray(frames[index // 12], format="rgb24")
            for packet in stream.encode(frame):
                movie.mux(packet)
        for packet in stream.encode():
            movie.mux(packet)


if __name__ == "__main__":
    main()
