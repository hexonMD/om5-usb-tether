"""Offline tests. No portable-device Open or real SendCommand is called."""
import sys
import struct
import unittest

import om5_wpd_probe as probe


def fixture():
    def string(value):
        return bytes([len(value) + 1]) + (value + "\0").encode("utf-16le")

    def codes(values):
        return struct.pack("<I", len(values)) + struct.pack(f"<{len(values)}H", *values)

    return b"".join((
        struct.pack("<HIH", 100, 6, 100), string("Test transport"), struct.pack("<H", 0),
        codes(range(0x1001, 0x1015)), codes(range(0x4001, 0x4005)),
        codes([0x5001, 0x5011, 0xd405, 0xd406, 0xd407]), codes([]), codes([0x3801]),
        string("OMSYSTEM"), string("OM-5"), string("1.00"), string("SIMULATED"),
    ))


class FakeTransport:
    def __init__(self, payload, total=None, response=0x2001, chunk=13):
        self.payload = payload
        self.total = len(payload) if total is None else total
        self.response = response
        self.chunk = chunk
        self.calls = []

    def begin(self, opcode):
        self.calls.append(('begin', opcode))
        return 'fake', self.total

    def read(self, context, n):
        self.calls.append(('read', n))
        data = self.payload[:min(n, self.chunk)]
        self.payload = self.payload[len(data):]
        return data

    def end(self, context):
        self.calls.append(('end',))
        return self.response


class ProbeTests(unittest.TestCase):
    def test_device_info_parser(self):
        p = probe.parse_device_info(fixture())
        self.assertEqual((p['model'], p['device_version']), ('OM-5', '1.00'))
        self.assertEqual(p['vendor_extension_id'], 6)
        self.assertEqual((len(p['operations']), len(p['events']), len(p['properties'])), (20, 4, 5))
        self.assertEqual(p['properties'], ['5001', '5011', 'd405', 'd406', 'd407'])
        self.assertNotIn('serial', p)
        self.assertNotIn('serial_number', p)
        self.assertNotIn('9804', p['operations'])

    def test_all_truncations_rejected(self):
        payload = fixture()
        for size in range(len(payload)):
            with self.subTest(size=size), self.assertRaises(probe.ProbeError):
                probe.parse_device_info(payload[:size])
        with self.assertRaises(probe.ProbeError):
            probe.parse_device_info(payload + b'\0')

    def test_usb_version_is_not_body_firmware(self):
        p = probe.parse_device_info(fixture())
        identity = probe.validate_identity(p, '1.2')
        self.assertEqual(identity['owner_menu_body_version'], '1.2')
        self.assertEqual(identity['usb_device_version'], '1.00')
        self.assertFalse(identity['body_firmware_verified_from_usb'])
        p['model'] = 'OM-5MarkII'
        with self.assertRaises(probe.ProbeError):
            probe.validate_identity(p, '1.2')

    def test_split_data_one_opcode(self):
        payload = fixture()
        fake = FakeTransport(payload)
        self.assertEqual(probe.receive_device_info(fake), payload)
        self.assertEqual([x for x in fake.calls if x[0] == 'begin'], [('begin', 0x1001)])
        self.assertEqual(fake.calls[-1], ('end',))

    def test_non_ok_response_rejected(self):
        fake = FakeTransport(fixture(), response=0x200a)
        with self.assertRaises(probe.ProbeError):
            probe.receive_device_info(fake)
        self.assertEqual(sum(x[0] == 'begin' for x in fake.calls), 1)

    def test_invalid_size_rejected_before_read(self):
        for size in (0, -1, probe.MAX_DATA + 1, 0xffffffff):
            fake = FakeTransport(b'', total=size)
            with self.assertRaises(probe.ProbeError):
                probe.receive_device_info(fake)
            self.assertEqual(fake.calls, [('begin', 0x1001)])

    def test_empty_read_fails_without_retry(self):
        fake = FakeTransport(b'', total=40)
        with self.assertRaises(probe.ProbeError):
            probe.receive_device_info(fake)
        self.assertEqual(fake.calls, [('begin', 0x1001), ('read', 40)])

    @unittest.skipUnless(sys.platform == "win32", "Windows COM transport")
    def test_com_request_objects_with_fake_device(self):
        # Real host-memory COM value objects, fake receiver. No camera is opened.
        t = probe.WpdTransport([])
        payload = fixture()
        commands = []

        class FakeDevice:
            def SendCommand(self, flags, params):
                category = params.GetGuidValue(t.key(probe.COMMON, 1001))
                self_outer.assertEqual(str(category).upper(), probe.EXT)
                cmd = params.GetUnsignedIntegerValue(t.key(probe.COMMON, 1002))
                commands.append(cmd)
                r = t.values()
                r.SetErrorValue(t.key(probe.COMMON, 1003), 0)
                if cmd == 13:
                    self_outer.assertEqual(params.GetUnsignedIntegerValue(t.key(probe.EXT, 1001)), 0x1001)
                    coll = params.GetIPortableDevicePropVariantCollectionValue(t.key(probe.EXT, 1002))
                    n = probe.ctypes.c_ulong(0)
                    coll.GetCount(probe.ctypes.byref(n))
                    self_outer.assertEqual(n.value, 0)
                    r.SetStringValue(t.key(probe.EXT, 1006), 'offline-context')
                    r.SetUnsignedLargeIntegerValue(t.key(probe.EXT, 1007), len(payload))
                elif cmd == 15:
                    self_outer.assertEqual(params.GetStringValue(t.key(probe.EXT, 1006)), 'offline-context')
                    self_outer.assertEqual(params.GetUnsignedIntegerValue(t.key(probe.EXT, 1008)), len(payload))
                    buf = (probe.ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
                    r.SetBufferValue(t.key(probe.EXT, 1012), buf, len(payload))
                    r.SetUnsignedIntegerValue(t.key(probe.EXT, 1009), len(payload))
                elif cmd == 17:
                    r.SetUnsignedIntegerValue(t.key(probe.EXT, 1003), 0x2001)
                else:
                    raise AssertionError('Unexpected command')
                return r

        self_outer = self
        t.device = FakeDevice()
        with self.assertRaises(probe.ProbeError):
            t.begin(0x100e)
        self.assertEqual(commands, [])
        self.assertEqual(probe.receive_device_info(t), payload)
        self.assertEqual(commands, [13, 15, 17])
        with self.assertRaises(probe.ProbeError):
            t.begin(0x1001)
        with self.assertRaises(probe.ProbeError):
            t.send(14, lambda p: None)
        self.assertEqual(commands, [13, 15, 17])


if __name__ == '__main__':
    unittest.main()
