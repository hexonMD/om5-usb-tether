"""Check single-release behavior without opening a camera or Lightroom."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

if __package__:
    from . import jewelry_lightroom_bridge as bridge
else:
    import jewelry_lightroom_bridge as bridge


class LightroomRequestTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.ticket = self.root / "request"
        self.ticket.mkdir()
        (self.ticket / "output-folder.txt").write_text(str(self.root), encoding="utf-8")
        self.port = "usb:001,007"
        self.devices = "Model      Port\n------------------------\nTest camera   " + self.port + "\n"
        self.abilities = "Capture choices                  :\n                                 : Image\n"
        self.shutter_calls = 0
        self.disconnect = False
        for target, replacement in (("_remote_om5_present", lambda: False), ("_gphoto_program", lambda: "test-gphoto2"), ("_gphoto", self.command)):
            mock = patch.object(bridge, target, replacement)
            mock.start()
            self.addCleanup(mock.stop)

    def command(self, program, args, timeout, cwd=None):
        self.assertEqual(program, "test-gphoto2")
        if args == ["--auto-detect"]:
            return self.devices
        self.assertEqual(args[:4], ["--camera", "Test camera", "--port", self.port])
        if args[4:] == ["--abilities"]:
            return self.abilities
        self.assertEqual(args[4:], ["--keep", "--filename", "photo-%n.%C", "--capture-image-and-download"])
        self.shutter_calls += 1
        Image.new("RGB", (20, 10)).save(cwd / "photo-1.JPG")
        if self.disconnect:
            raise OSError("simulated disconnect")

    def test_completed_ticket_returns_same_file_without_second_exposure(self):
        photo = bridge.lightroom_request(self.ticket, live=True)
        self.assertEqual(photo, bridge.lightroom_request(self.ticket, live=True))
        self.assertEqual((self.ticket / "photo.txt").read_text(encoding="utf-8"), str(photo))
        self.assertEqual(self.shutter_calls, 1)

    def test_lost_ack_keeps_received_file_and_never_retries_release(self):
        self.disconnect = True
        with self.assertRaisesRegex(ValueError, "simulated disconnect"):
            bridge.lightroom_request(self.ticket, live=True)
        with self.assertRaisesRegex(ValueError, "already started"):
            bridge.lightroom_request(self.ticket, live=True)
        self.assertEqual(self.shutter_calls, 1)
        self.assertTrue(list((self.root / "Lightroom Captures").glob("*/photo-1.JPG")))
        self.assertFalse((self.ticket / "photo.txt").exists())

    def test_bracket_receipts_are_complete_before_ticket_reuse(self):
        files = [self.root / f"bracket-{n}.jpg" for n in range(9)]
        for path in files:
            Image.new("RGB", (20, 10)).save(path)
        with patch.object(bridge, "_take_photo", return_value=files) as shoot:
            for _ in range(2):
                self.assertEqual(bridge.lightroom_request(self.ticket, live=True), files[0])
            shoot.assert_called_once_with(self.root)
        self.assertEqual((self.ticket / "photo-count.txt").read_text(), "9")
        receipts = ["photo.txt"] + [f"photo-{n}.txt" for n in range(2, 10)]
        self.assertEqual([Path((self.ticket / name).read_text(encoding="utf-8")) for name in receipts], files)

    def test_rejects_ambiguous_camera(self):
        self.devices += "Second camera   usb:001,008\n"
        with self.assertRaisesRegex(ValueError, "found 2"):
            bridge.lightroom_request(self.ticket, live=True)
        self.assertEqual(self.shutter_calls, 0)

    def test_rejects_preview_only_driver(self):
        self.abilities = self.abilities.replace(": Image", ": Preview")
        with self.assertRaisesRegex(ValueError, "does not advertise"):
            bridge.lightroom_request(self.ticket, live=True)
        self.assertEqual(self.shutter_calls, 0)


if __name__ == "__main__":
    unittest.main()
