"""Run a still request from the Lightroom Classic plug-in."""

import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import mkdtemp, NamedTemporaryFile

from PIL import Image
from contextlib import contextmanager

if __package__:
    from .jewelry_job import run_job, sample_job
else:
    from jewelry_job import run_job, sample_job


def _gphoto_program():
    program = shutil.which("gphoto2.exe" if os.name == "nt" else "gphoto2")
    if not program and os.name == "posix":
        # macOS GUI apps may start with a smaller PATH than the user's terminal.
        for base in ("/opt/homebrew/bin", "/usr/local/bin"):
            program = shutil.which("gphoto2", path=base)
            if program:
                break
    if not program:
        raise ValueError("Live capture requires the gphoto2 command-line program. The sample works without it.")
    return program


def _gphoto(program, args, timeout, cwd=None):
    proc = subprocess.run([program, *args], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=timeout, env={**os.environ, "LC_ALL": "C", "LANG": "C"})
    if proc.returncode:
        reason = (proc.stderr or proc.stdout).decode("utf-8", errors="replace").strip()
        raise ValueError(reason[-1800:] or f"gphoto2 exited with status {proc.returncode}.")
    return proc.stdout.decode("utf-8", errors="replace")


def _gphoto_capture(root):
    program = _gphoto_program()
    detected = _gphoto(program, ["--auto-detect"], 20)
    devices = re.findall(r"^(.+?)[ \t]+(usb:[^\s]+)[ \t\r]*$", detected, flags=re.MULTILINE)
    if len(devices) != 1:
        raise ValueError(f"gphoto2 found {len(devices)} USB cameras. Connect one camera in a capture-capable USB mode.")
    model, port = devices[0]
    selector = ["--camera", model.strip(), "--port", port]
    abilities = _gphoto(program, [*selector, "--abilities"], 20)
    if not re.search(r"^[ \t]*:[ \t]*Image[ \t\r]*$", abilities, re.MULTILINE):
        raise ValueError(f"The gphoto2 driver for {model.strip()} does not advertise still capture in this mode.")

    parent = root / "Lightroom Captures"
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(mkdtemp(prefix="shoot-", dir=parent))
    try:
        _gphoto(program, [*selector, "--keep", "--filename", "photo-%n.%C",
                          "--capture-image-and-download"], 180, cwd=folder)
        jpegs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")]
        if len(jpegs) != 1:
            raise ValueError(f"Expected one JPEG and received {len(jpegs)}. Set the camera to single-shot JPEG or RAW+JPEG.")
        with Image.open(jpegs[0], formats=("JPEG",)) as image:
            image.load()
        return jpegs
    except Exception as error:
        # A failed download can follow a successful exposure. Preserve all received files.
        raise ValueError(f"Capture did not complete. Check the camera and {folder} before taking another photo. {error}") from error


def _remote_om5_present():
    if os.name != "nt":
        return False
    # Detect before selecting a backend. Never fall back after a shutter request.
    check = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            "if (Get-PnpDevice -PresentOnly -ErrorAction Stop | Where-Object "
                            "{ $_.InstanceId -like 'USB\\VID_33A2&PID_0138\\*' }) { exit 0 }; exit 2"],
                           capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if check.returncode not in (0, 2):
        raise RuntimeError("Windows could not check the camera connection.")
    return check.returncode == 0


@contextmanager
def _windows_capture_lock():
    import msvcrt
    folder = Path(os.environ["LOCALAPPDATA"]) / "Jewelry Studio"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "capture.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ValueError("Another capture is in progress.") from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _take_photo(root):
    if not _remote_om5_present():
        return _gphoto_capture(root)
    if __package__:
        from .om5_wpd_photo import capture_for_editor
    else:
        from om5_wpd_photo import capture_for_editor
    parent = root / "Lightroom Captures"
    parent.mkdir(parents=True, exist_ok=True)
    with _windows_capture_lock():
        folder = Path(mkdtemp(prefix="shoot-", dir=parent))
        try:
            photos = capture_for_editor(folder)
            for photo in photos:
                with Image.open(photo, formats=("JPEG",)) as image:
                    image.load()
            return photos
        except Exception as error:
            raise ValueError(f"Capture incomplete. Check the camera and {folder} before retrying. {error}") from error


def _save_text(path, value):
    stream = NamedTemporaryFile(dir=path.parent, delete=False)
    temp = Path(stream.name)
    try:
        with stream:
            stream.write(value.encode("utf-8"))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def lightroom_request(directory, *, live):
    ticket = Path(directory).expanduser().resolve(strict=True)
    if not ticket.is_dir():
        raise ValueError("The Lightroom request directory is missing.")
    receipt = ticket / "photo.txt"
    claim = ticket / "started.txt"
    # The same ticket, even after a disconnect, can fire the shutter only once.
    try:
        with claim.open("x", encoding="utf-8") as started:
            started.write("live" if live else "sample")
    except FileExistsError:
        if claim.read_text(encoding="utf-8") == ("live" if live else "sample") and receipt.is_file():
            previous = Path(receipt.read_text(encoding="utf-8"))
            if previous.is_file():
                return previous
        raise ValueError("This Lightroom request already started. Check its result before starting another.")
    try:
        target = (ticket / "output-folder.txt").read_text(encoding="utf-8-sig").rstrip("\r\n")
        root = Path(target)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("Choose an existing absolute output folder for the Lightroom shoot.")
        if live:
            photos = _take_photo(root)
        else:
            job = sample_job()
            job["shots"] = [shot for shot in job["shots"] if shot["type"] == "focus_still"]
            result = run_job(job, root)
            photos = [Path(result["directory"]) / result["shots"][0]["file"]]
        for index, extra in enumerate(photos[1:], 2):
            _save_text(ticket / f"photo-{index}.txt", str(extra))
        _save_text(ticket / "photo-count.txt", str(len(photos)))
        photo = photos[0]
        _save_text(receipt, str(photo))
        return photo
    except Exception as error:
        _save_text(ticket / "error.txt", str(error))
        raise
