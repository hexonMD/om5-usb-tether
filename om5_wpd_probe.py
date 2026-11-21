"""OM-5 GetDeviceInfo through the existing Windows MTP driver.

No arguments: offline plan only. --execute requires the recorded body version
and a NEW output directory. The only PTP opcode requested by this application
is 1001. WPD owns USB transport/session management; this is not a USB packet
capture and cannot certify what Windows itself sends during enumeration/open.
No camera-setting writes, capture, driver changes, reset or update operation.
COM property setters below construct host-side request objects, not camera
property writes. Raw DeviceInfo is local evidence and can contain a serial.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import struct
import sys

GET_DEVICE_INFO = 0x1001
MAX_DATA = 1024 * 1024
EXT = '{4D545058-1A2E-4106-A357-771E0819FC56}'
COMMON = '{F0422A9C-5DC8-4440-B5BD-5DF28835658A}'
CLIENT = '{204D9F0C-2292-4080-9F42-40664E70F859}'


class ProbeError(Exception):
    pass


class CameraNotPresent(ProbeError):
    pass


def parse_device_info(data: bytes) -> dict:
    if not 1 <= len(data) <= MAX_DATA:
        raise ProbeError('DeviceInfo size outside limit')
    pos = 0

    def take(n):
        nonlocal pos
        if n < 0 or pos + n > len(data):
            raise ProbeError('Truncated DeviceInfo')
        result = data[pos:pos+n]
        pos += n
        return result

    def u16():
        return struct.unpack('<H', take(2))[0]

    def u32():
        return struct.unpack('<I', take(4))[0]

    def string():
        count = take(1)[0]
        if not count:
            return ''
        raw = take(count * 2)
        if raw[-2:] != b'\0\0':
            raise ProbeError('PTP string lacks terminator')
        try:
            value = raw[:-2].decode('utf-16-le', errors='strict')
        except UnicodeDecodeError as exc:
            raise ProbeError('Invalid UTF-16') from exc
        if '\0' in value:
            raise ProbeError('Embedded string terminator')
        return value

    def codes():
        count = u32()
        if count > 4096:
            raise ProbeError('Too many advertised codes')
        return [f'{u16():04x}' for _ in range(count)]

    result = {
        'standard_version': u16(),
        'vendor_extension_id': u32(),
        'vendor_extension_version': u16(),
        'vendor_extension_description': string(),
        'functional_mode': u16(),
    }
    for key in ['operations', 'events', 'properties', 'capture_formats', 'image_formats']:
        result[key] = codes()
    for key in ['manufacturer', 'model', 'device_version']:
        result[key] = string()
    serial = string()
    result['serial_present'] = bool(serial)
    if pos != len(data):
        raise ProbeError('Unexpected trailing DeviceInfo bytes')
    return result


def receive_device_info(transport) -> bytes:
    """One GetDeviceInfo request, bounded reads, one matching response."""
    context, total = transport.begin(GET_DEVICE_INFO)
    if not isinstance(total, int) or not 1 <= total <= MAX_DATA:
        raise ProbeError('Unbounded or invalid driver-reported data length')
    pieces = []
    remaining = total
    for _ in range(64):
        request = min(remaining, 65536)
        chunk = transport.read(context, request)
        if not 1 <= len(chunk) <= request:
            raise ProbeError('Empty or oversized driver data chunk')
        pieces.append(chunk)
        remaining -= len(chunk)
        if remaining == 0:
            break
    if remaining:
        raise ProbeError('Read iteration limit exceeded')
    code = transport.end(context)
    if code != 0x2001:
        raise ProbeError(f'PTP response 0x{code:04x}; no retry')
    return b''.join(pieces)


def validate_identity(parsed, owner_version):
    if parsed['model'] != 'OM-5' or parsed['manufacturer'] != 'OMSYSTEM':
        raise ProbeError('Returned model/manufacturer does not match OM-5; stopped')
    # OM-5 stock 1.2 serializes a literal "1.00" from 0x615D79D6.
    # DeviceVersion is therefore NOT an authoritative body-firmware reading.
    return {
        'owner_menu_body_version': owner_version,
        'usb_device_version': parsed['device_version'],
        'body_firmware_verified_from_usb': False,
        'note': 'USB DeviceVersion is distinct from menu firmware; stock OM-5 1.2 uses literal 1.00.',
    }


class WpdTransport:
    """WPD extension bridge: command IDs 13/15/17 only; PTP opcode 1001 only."""
    command_ids = (13, 15, 17)
    client_name = 'OM5 GetDeviceInfo only'
    def __init__(self, audit, transport_access='read', usb_mode='mtp'):
        if transport_access not in ('read', 'readwrite'):
            raise ProbeError('Invalid Windows handle access')
        import comtypes.client
        from comtypes import GUID
        self.cc = comtypes.client
        self.GUID = GUID
        self.A = self.cc.GetModule('PortableDeviceApi.dll')
        self.T = self.cc.GetModule('PortableDeviceTypes.dll')
        self.audit = audit
        self.transport_access = transport_access
        self.product_id = {'mtp': '012f', 'remote': '0138'}[usb_mode]
        self.device = None
        self.started = False
        self.context = None
        self.finished = False
        self.ole = ctypes.OleDLL('ole32')
        self.ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self.ole.CoTaskMemFree.restype = None

    def key(self, guid, pid):
        return self.A._tagpropertykey(self.GUID(guid), pid)

    def values(self):
        return self.cc.CreateObject(self.T.PortableDeviceValues,
                                    interface=self.A.IPortableDeviceValues)

    def open(self):
        manager = self.cc.CreateObject(self.A.PortableDeviceManager,
                                       interface=self.A.IPortableDeviceManager)
        count = ctypes.c_ulong(0)
        # Use the raw COM signature for nullable buffer/count parameters.
        # comtypes' in/out wrapper otherwise converts a NULL buffer to &NULL.
        get_devices = getattr(manager, '_IPortableDeviceManager__com_GetDevices')
        get_name = getattr(manager, '_IPortableDeviceManager__com_GetDeviceFriendlyName')
        get_devices(None, ctypes.byref(count))
        if count.value == 0:
            raise CameraNotPresent('No portable cameras connected')
        if not 1 <= count.value <= 128:
            raise ProbeError('No portable devices or invalid device count')
        slots = (ctypes.c_wchar_p * count.value)()
        get_devices(slots, ctypes.byref(count))
        try:
            if count.value > len(slots):
                raise ProbeError('Portable-device list changed during enumeration')
            matches = [slots[i] for i in range(count.value)
                       if f'vid_33a2&pid_{self.product_id}' in slots[i].lower()]
        finally:
            for pointer in ctypes.cast(slots, ctypes.POINTER(ctypes.c_void_p))[:len(slots)]:
                if pointer:
                    self.ole.CoTaskMemFree(pointer)
        if not matches:
            raise CameraNotPresent('OM-5 USB Remote device is not connected')
        if len(matches) != 1:
            raise ProbeError(f'Expected exactly one OM-5 USB identity; found {len(matches)}')
        pnp_id = matches[0]
        length = ctypes.c_ulong(0)
        get_name(pnp_id, None, ctypes.byref(length))
        if not 1 <= length.value <= 1024:
            raise ProbeError('Invalid friendly-name length')
        buf = (ctypes.c_ushort * length.value)()
        get_name(pnp_id, buf, ctypes.byref(length))
        name = ctypes.wstring_at(ctypes.addressof(buf))
        if name != 'OM-5':
            raise ProbeError('Matching USB identity did not have exact OM-5 friendly name')
        self.audit.append({'action': 'selected', 'vid_pid': f'33a2:{self.product_id}', 'name': name})
        client = self.values()
        client.SetStringValue(self.key(CLIENT, 2), self.client_name)
        client.SetUnsignedIntegerValue(self.key(CLIENT, 3), 1)
        client.SetUnsignedIntegerValue(self.key(CLIENT, 4), 0)
        client.SetUnsignedIntegerValue(self.key(CLIENT, 5), 0)
        client.SetUnsignedIntegerValue(self.key(CLIENT, 8), 0x00020000)
        # A bidirectional driver handle can be required even for data-in MTP
        # escapes. It does not expand the application's one-opcode allowlist.
        access = 0x80000000 if self.transport_access == 'read' else 0xc0000000
        client.SetUnsignedIntegerValue(self.key(CLIENT, 9), access)
        self.device = self.cc.CreateObject(self.A.PortableDevice,
                                          interface=self.A.IPortableDevice)
        self.audit.append({'action': 'WPD Open', 'access': self.transport_access,
                           'access_mask': f'0x{access:08x}'})
        self.device.Open(pnp_id, client)

    def send(self, command, populate):
        if self.device is None or command not in self.command_ids:
            raise ProbeError('WPD command outside allowlist')
        params = self.values()
        params.SetGuidValue(self.key(COMMON, 1001), self.GUID(EXT))
        params.SetUnsignedIntegerValue(self.key(COMMON, 1002), command)
        populate(params)
        self.audit.append({'action': 'WPD SendCommand', 'command_id': command})
        result = self.device.SendCommand(0, params)
        hr = result.GetErrorValue(self.key(COMMON, 1003))
        hr = int(getattr(hr, 'value', hr)) & 0xffffffff
        if hr:
            raise ProbeError(f'WPD command {command} HRESULT 0x{hr:08x}; no fallback')
        return result

    def begin(self, opcode):
        if opcode != GET_DEVICE_INFO or self.started:
            raise ProbeError('Only one GetDeviceInfo request is permitted')
        self.started = True
        def populate(p):
            p.SetUnsignedIntegerValue(self.key(EXT, 1001), GET_DEVICE_INFO)
            empty = self.cc.CreateObject(self.T.PortableDevicePropVariantCollection,
                                        interface=self.A.IPortableDevicePropVariantCollection)
            p.SetIPortableDevicePropVariantCollectionValue(self.key(EXT, 1002), empty)
        self.audit.append({'action': 'request PTP', 'opcode': '1001', 'parameters': []})
        r = self.send(13, populate)
        self.context = r.GetStringValue(self.key(EXT, 1006))
        return self.context, r.GetUnsignedLargeIntegerValue(self.key(EXT, 1007))

    def read(self, context, n):
        if not self.started or self.finished or context != self.context or not 1 <= n <= 65536:
            raise ProbeError('Invalid data-read state')
        def populate(p):
            p.SetStringValue(self.key(EXT, 1006), context)
            p.SetUnsignedIntegerValue(self.key(EXT, 1008), n)
            empty = (ctypes.c_ubyte * n)()
            p.SetBufferValue(self.key(EXT, 1012), empty, n)
        r = self.send(15, populate)
        actual = r.GetUnsignedIntegerValue(self.key(EXT, 1009))
        ptr, size = r.GetBufferValue(self.key(EXT, 1012))
        try:
            if not 0 < actual <= n or not actual <= size <= MAX_DATA:
                raise ProbeError('Inconsistent WPD buffer lengths')
            return ctypes.string_at(ptr, actual)
        finally:
            if ptr:
                self.ole.CoTaskMemFree(ctypes.cast(ptr, ctypes.c_void_p))

    def end(self, context):
        if not self.started or self.finished or context != self.context:
            raise ProbeError('Invalid transfer-end state')
        self.finished = True
        r = self.send(17, lambda p: p.SetStringValue(self.key(EXT, 1006), context))
        response = r.GetUnsignedIntegerValue(self.key(EXT, 1003))
        self.audit.append({'action': 'PTP response', 'code': f'{response:04x}'})
        return response

    def close(self):
        if self.device is not None:
            self.audit.append({'action': 'WPD Close'})
            self.device.Close()
            self.device = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--body-version')
    parser.add_argument('--usb-mode', choices=['mtp', 'remote'], default='mtp')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--transport-access', choices=['read', 'readwrite'], default='read',
                        help='Windows transport handle access; PTP allowlist remains GetDeviceInfo only')
    args = parser.parse_args()
    if not args.execute:
        print('Offline plan only: existing Windows WPD driver, one GetDeviceInfo (1001), bounded data-in, no camera writes.')
        return 0
    if sys.platform != 'win32' or not args.body_version or not args.out:
        parser.error('--execute requires Windows, --body-version and --out NEW_DIRECTORY')
    try:
        expected = Decimal(args.body_version)
        if not expected.is_finite() or expected <= 0:
            raise InvalidOperation
    except InvalidOperation:
        parser.error('Body version must be a finite positive number')
    args.out.mkdir(parents=True, exist_ok=False)
    report = {
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'owner_reported_body_version': args.body_version,
        'transport': 'Windows WPD via existing Microsoft MTP driver',
        'requested_ptp_allowlist': ['1001'],
        'wire_capture': False,
        'limitation': 'Windows owns session and USB housekeeping; only application-requested opcode is controlled here.',
        'audit': [],
    }
    transport = None
    exit_code = 1
    try:
        transport = WpdTransport(report['audit'], args.transport_access, args.usb_mode)
        transport.open()
        payload = receive_device_info(transport)
        report['getdeviceinfo_completed'] = True
        (args.out / 'deviceinfo.bin').write_bytes(payload)
        report['payload_sha256'] = hashlib.sha256(payload).hexdigest()
        report['payload_size'] = len(payload)
        parsed = parse_device_info(payload)
        report['device_info'] = parsed
        report['identity'] = validate_identity(parsed, args.body_version)
        report['status'] = 'success'
        exit_code = 0
    except Exception as exc:
        report['status'] = 'stopped'
        report['error_type'] = type(exc).__name__
        # COM diagnostics may contain a device-instance serial; report HRESULT only.
        report['error'] = (str(exc) if isinstance(exc, ProbeError)
                           else f'HRESULT 0x{getattr(exc, "hresult", 0) & 0xffffffff:08x}')
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                report['close_error_type'] = type(exc).__name__
        (args.out / 'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report, indent=2))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
