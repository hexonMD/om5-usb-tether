from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import om5_usb_mode as mode


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / 'private'
        self.original = b'1,Current\r\n' + mode.MTP + b'\r\n2,OTHER,\x80\r\n3'
        self.remote = self.original.replace(mode.MTP, mode.REMOTE)
        self.live = self.original
        self.calls = []
        self.stage_data = None
        self.validation_errors = b'0'
        self.body = 'mock-body'
        self.lose_ack = False
        for name, func in [('identify', lambda: self.body), ('maintenance', lambda: None),
                           ('export_current', lambda p: p.write_bytes(self.live)),
                           ('stage', self.stage), ('wait_result', lambda: None),
                           ('request', self.request)]:
            mock = patch.object(mode, name, func)
            mock.start()
            self.addCleanup(mock.stop)
        mode.prepare(self.folder)

    def stage(self, data):
        self.calls.append('stage')
        self.assertEqual(data, self.remote)
        self.stage_data = data

    def request(self, endpoint, params=None):
        action = (params or {}).get('action', endpoint)
        self.calls.append(action)
        if action in ('errorcheck', 'restore'):
            self.assertEqual(self.stage_data, self.remote)
            self.stage_data = None
            if action == 'restore':
                self.live = self.remote
                if self.lose_ack:
                    raise TimeoutError('lost reply')
        return b'<response><result>ok</result><datasize>' + self.validation_errors + b'</datasize></response>'

    def test_complete_restore_reuploads_and_verifies_before_restart(self):
        mode.apply(self.folder)
        mode.verify(self.folder, restart=True)
        self.assertEqual(self.calls, ['stage', 'errorcheck', 'get_mysetdatasize',
                                      'stage', 'restore', 'exec_reboot'])
        self.assertEqual((self.folder / 'original.csv').read_bytes(), self.original)

    def test_mismatched_body_or_stale_settings_stop_before_upload(self):
        self.body = 'second body'
        with self.assertRaisesRegex(ValueError, 'differs'):
            mode.apply(self.folder)
        self.body = 'mock-body'
        self.live += b'changed'
        with self.assertRaisesRegex(ValueError, 'changed'):
            mode.apply(self.folder)
        self.assertEqual(self.calls, [])

    def test_validation_error_stops_before_restore(self):
        self.validation_errors = b'4'
        with self.assertRaisesRegex(ValueError, 'validation'):
            mode.apply(self.folder)
        self.assertNotIn('restore', self.calls)

    def test_lost_reply_cannot_replay_restore(self):
        self.lose_ack = True
        with self.assertRaises(TimeoutError):
            mode.apply(self.folder)
        mode.verify(self.folder)
        with self.assertRaisesRegex(ValueError, 'already submitted'):
            mode.apply(self.folder)
        self.assertEqual(self.calls.count('restore'), 1)

    def test_reject_ambiguous_or_missing_usb_record(self):
        for bad in (self.remote, self.original + b'\r\n' + mode.MTP):
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                mode.remote_copy(bad)
