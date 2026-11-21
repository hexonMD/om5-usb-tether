"""Deliver finished Jewelry Studio JPEGs to a local editor folder."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from shutil import copyfile
from stat import S_ISREG
from tempfile import TemporaryDirectory

from PIL import Image

if __package__:
    from .jewelry_job import _filename
else:
    from jewelry_job import _filename


def _digest(path):
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identical_or_missing(path, expected):
    try:
        if not S_ISREG(path.lstat().st_mode) or _digest(path) != expected:
            raise FileExistsError(f"An unrelated file or folder already occupies {path}.")
    except FileNotFoundError:
        return False
    return True


def _publish(staged, target, expected):
    # Both operations publish complete bytes without replacing an existing entry.
    for _ in range(3):
        try:
            if os.name == "nt":
                os.rename(staged, target)
            else:
                os.link(staged, target)
            return True
        except FileExistsError:
            if _identical_or_missing(target, expected):
                return False
            # A watcher may have moved the conflicting entry before inspection.
    raise OSError(f"The editor folder kept changing during export of {target.name}. Retry the export.")


def export_stills(manifest_path, inbox) -> dict:
    """Copy checked stills from a completed run and return paths and delivery counts."""
    manifest = Path(manifest_path).expanduser().resolve()
    inbox = Path(inbox).expanduser().resolve()
    if not inbox.is_dir():
        raise ValueError(f"Choose an existing editor folder: {inbox}")
    if inbox == manifest.parent:
        raise ValueError("Choose an editor folder separate from the original run folder.")
    result = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict) or result.get("status") != "complete":
        raise ValueError("Choose a manifest from a completed Jewelry Studio run.")
    sku = _filename(result.get("sku"), "Manifest SKU")
    run = _filename(result.get("run_id"), "Manifest run ID")
    if not isinstance(result.get("shots"), list):
        raise ValueError("The completed manifest needs a shots list.")

    files = []
    copied = 0
    names = set()
    with TemporaryDirectory(prefix=".jewelry-export-", dir=inbox) as work:
        pending = []
        for index, shot in enumerate(result["shots"], 1):
            if not isinstance(shot, dict) or shot.get("type") not in ("focus_still", "video"):
                raise ValueError(f"Unrecognized shot type at manifest entry {index}.")
            if shot["type"] == "video":
                continue
            name = _filename(shot.get("name"), f"Shot {index} name")
            if shot.get("file") != name + ".jpg" or name.casefold() in names:
                raise ValueError(f"Invalid or duplicate JPEG filename for shot {name}.")
            names.add(name.casefold())
            source = manifest.parent / shot["file"]
            if not source.is_file() or source.resolve() != source:
                raise ValueError(f"Expected a local JPEG file in the run folder: {source}")
            expected = shot.get("sha256")
            size = shot.get("bytes")
            if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
                raise ValueError(f"Invalid SHA-256 digest for {source.name}.")
            if type(size) is not int or size <= 0:
                raise ValueError(f"Invalid byte count for {source.name}.")
            staged = Path(work) / f"{index}.tmp"
            copyfile(source, staged)
            if staged.stat().st_size != size or _digest(staged) != expected:
                raise ValueError(f"Size or SHA-256 mismatch for {source.name}.")
            try:
                with Image.open(staged, formats=("JPEG",)) as image:
                    image.load()
            except OSError as error:
                raise ValueError(f"Cannot decode {source.name} as a JPEG: {error}") from error
            # Tilde cannot appear in any of the three filename components.
            target = inbox / f"{sku}~{run}~{name}.jpg"
            _identical_or_missing(target, expected)
            pending.append((staged, target, expected))
        if not pending:
            raise ValueError("This completed run contains no still JPEGs to export.")
        for staged, target, expected in pending:
            if _publish(staged, target, expected):
                copied += 1
            files.append(str(target))
    return {"sku": sku, "run_id": run, "inbox": str(inbox), "files": files,
            "copied": copied, "existing": len(files) - copied}
