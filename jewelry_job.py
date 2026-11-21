"""Run a jewelry shot list against local image and video files."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


def default_output_root():
    return Path.home() / "Pictures" / "Jewelry Studio"


def sample_job():
    here = Path(__file__).resolve().parent
    samples = here / "samples"
    return {
        "sku": "DEMO-RING",
        "shots": [
            {"type": "focus_still", "name": "detail",
             "frames": [str(samples / f"om5-usb-connected-stack-{p}.jpg")
                        for p in ("far", "middle", "near")]},
            {"type": "video", "name": "product-clip",
             "source": str(samples / "om5-video-focus-sweep.mov"),
             "start_seconds": 0, "duration_seconds": 1},
        ],
    }


def _filename(value, field):
    if (not isinstance(value, str) or not 1 <= len(value) <= 80 or
            not value[0].isalnum() or value[-1] in " ." or
            any(not (c.isalnum() or c in " ._-") for c in value)):
        raise ValueError(f"{field} must be a filename of 1–80 characters. Use letters, digits, spaces, dots, underscores or hyphens.")
    reserved = {"CON", "AUX", "NUL", "PRN", "CONIN$", "CONOUT$"}
    reserved.update(prefix + n for prefix in ("COM", "LPT") for n in "0123456789¹²³")
    if value.partition(".")[0].rstrip().upper() in reserved:
        raise ValueError(f"{field} is a reserved filename.")
    return value


def _file(value, base):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Every source must be a local file path.")
    full = (base / Path(value).expanduser()).resolve()
    if not full.is_file():
        raise ValueError(f"Source file missing: {full}")
    return str(full)


def _check_fields(mapping, allowed, label):
    unexpected = sorted(set(mapping) - set(allowed))
    if unexpected:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(unexpected)}")


def normalize_job(job, base=None):
    if (isinstance(job, dict) and job.get("status") == "complete" and
            job.get("source_mode") == "existing_files" and "job" in job):
        job = job["job"]
    if not isinstance(job, dict) or not isinstance(job.get("shots"), list) or not job["shots"]:
        raise ValueError("A job needs a SKU and at least one shot in its shots list.")
    _check_fields(job, {"sku", "shots"}, "Job")
    sku = _filename(job.get("sku"), "SKU")
    base = Path.cwd() if base is None else Path(base)
    shots = []
    outputs = set()
    for index, raw in enumerate(job["shots"], 1):
        if not isinstance(raw, dict) or raw.get("type") not in ("focus_still", "video"):
            raise ValueError(f"Shot {index} must have type focus_still or video.")
        kind = raw["type"]
        fields = {"frames"} if kind == "focus_still" else {"source", "start_seconds", "duration_seconds"}
        _check_fields(raw, {"type", "name"} | fields, f"Shot {index}")
        name = _filename(raw.get("name", f"{kind}-{index}"), f"Shot {index} name")
        extension = ".jpg" if kind == "focus_still" else ".mp4"
        filename = name.casefold() + extension
        if filename in outputs:
            raise ValueError(f"Two shots would create {name}{extension}. Give them distinct names.")
        outputs.add(filename)
        shot = {"name": name, "type": kind}
        if kind == "focus_still":
            frames = raw.get("frames")
            if not isinstance(frames, list) or len(frames) < 2:
                raise ValueError(f"{name} needs at least two aligned JPEG focus planes.")
            shot["frames"] = [_file(frame, base) for frame in frames]
        else:
            shot["source"] = _file(raw.get("source"), base)
            for key, value in (("start_seconds", raw.get("start_seconds", 0)),
                               ("duration_seconds", raw.get("duration_seconds"))):
                if (type(value) not in (int, float) or not isfinite(value) or value < 0 or
                        (key == "duration_seconds" and value == 0)):
                    raise ValueError(f"{name} needs a valid {key}.")
                shot[key] = value
        shots.append(shot)
    return {"sku": sku, "shots": shots}


def load_job(path):
    path = Path(path).expanduser().resolve()
    return normalize_job(json.loads(path.read_text(encoding="utf-8-sig")), path.parent)


def run_job(job, output_root, progress=None):
    job = normalize_job(job)
    product = Path(output_root).expanduser().resolve() / job["sku"]
    product.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    final = product / run_id
    result = {"status": "complete", "source_mode": "existing_files", "sku": job["sku"],
              "run_id": run_id, "directory": str(final), "job": job, "shots": []}
    with TemporaryDirectory(prefix=".working-", dir=product) as tmp:
        staging = Path(tmp) / "outputs"
        staging.mkdir()
        for index, shot in enumerate(job["shots"], 1):
            kind, name = shot["type"], shot["name"]
            if progress:
                progress(f"{index} of {len(job['shots'])} · {name}")
            try:
                if kind == "focus_still":
                    if __package__:
                        from .jewelry_photo import make_focus_still
                    else:
                        from jewelry_photo import make_focus_still
                    output = staging / (name + ".jpg")
                    info = make_focus_still(shot["frames"], output)
                else:
                    if __package__:
                        from .jewelry_video import make_video_clip
                    else:
                        from jewelry_video import make_video_clip
                    output = staging / (name + ".mp4")
                    info = make_video_clip(shot["source"], output, shot["start_seconds"],
                                           shot["duration_seconds"])
            except Exception as error:
                raise ValueError(f"Shot {index} ({name}) failed: {error}") from error
            with output.open("rb") as media:
                digest = sha256()
                for chunk in iter(lambda: media.read(1024 * 1024), b""):
                    digest.update(chunk)
            result["shots"].append({**info, "type": kind, "name": name, "file": output.name,
                                    "bytes": output.stat().st_size, "sha256": digest.hexdigest()})
        (staging / "manifest.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                               encoding="utf-8")
        staging.rename(final)
    return result
