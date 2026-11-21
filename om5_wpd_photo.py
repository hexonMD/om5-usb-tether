"""Take a single SD-targeted photo on the original OM-5 in USB Remote mode."""
import argparse
import ctypes
import json
import struct
import time
from pathlib import Path

if __package__:
    from .om5_wpd_probe import EXT, WpdTransport
else:
    from om5_wpd_probe import EXT, WpdTransport


class Camera(WpdTransport):
    command_ids = (12, 13, 14, 15, 16, 17)
    client_name = 'OM-5 USB capture'

    def operation(self, opcode, params=(), *, data=None, no_data=False):
        def setup(p):
            p.SetUnsignedIntegerValue(self.key(EXT, 1001), opcode)
            args = self.cc.CreateObject(self.T.PortableDevicePropVariantCollection,
                                        interface=self.A.IPortableDevicePropVariantCollection)
            for arg in params:
                item = self.A.tag_inner_PROPVARIANT()
                item.vt = 19  # VT_UI4
                getattr(item, item._fields_[-1][0]).ulVal = arg
                args.Add(ctypes.byref(item))
            p.SetIPortableDevicePropVariantCollectionValue(self.key(EXT, 1002), args)
            if data is not None:
                p.SetUnsignedLargeIntegerValue(self.key(EXT, 1007), len(data))

        msg = f'{opcode:04x} {params}'
        r = self.send(12 if no_data else 13 if data is None else 14, setup)
        buf = bytearray()
        if no_data:
            status = r.GetUnsignedIntegerValue(self.key(EXT, 1003))
        else:
            ctx = r.GetStringValue(self.key(EXT, 1006))
            self.started, self.finished, self.context = True, False, ctx
            try:
                if data is None:
                    size = r.GetUnsignedLargeIntegerValue(self.key(EXT, 1007))
                    if size > 128*1024*1024:
                        raise ValueError(f'Unexpected object size {size}')
                    while len(buf) < size:
                        buf.extend(self.read(ctx, min(65536, size-len(buf))))
                else:
                    def write(p):
                        p.SetStringValue(self.key(EXT, 1006), ctx)
                        p.SetUnsignedIntegerValue(self.key(EXT, 1010), len(data))
                        p.SetBufferValue(self.key(EXT, 1012), (ctypes.c_ubyte*len(data)).from_buffer_copy(data), len(data))
                    written = self.send(16, write).GetUnsignedIntegerValue(self.key(EXT, 1011))
                    if written != len(data):
                        raise ValueError('Partial property write')
            finally:
                status = self.end(ctx)
        self.audit.append({'operation': msg, 'status': hex(status), 'bytes': len(buf)})
        if status != 0x2001:
            raise RuntimeError(f'{msg} returned PTP {status:04x}')
        return bytes(buf)

    def prop(self, code):
        return struct.unpack('<H', self.operation(0x1015, [code]))[0]

    def setprop(self, code, value):
        self.operation(0x1016, [code], data=struct.pack('<H', value))
        for _ in range(20):
            if self.prop(code) == value:
                return
            time.sleep(0.1)
        raise ValueError(f'Camera did not confirm {code:04x} = {value}')

    def handles(self):
        data = self.operation(0x1007, [0xffffffff, 0, 0])
        count, = struct.unpack_from('<I', data)
        return set(struct.unpack(f'<{count}I', data[4:]))


def download_handle(camera, directory, handle, extensions):
    info = camera.operation(0x1008, [handle])
    fmt, _, size = struct.unpack_from('<HHI', info, 4)
    if fmt == 0x3001 or not size:
        return None
    name = info[53:53+max(0, info[52]-1)*2].decode('utf-16le')
    if not name.upper().endswith(extensions):
        return None
    if Path(name).name != name or ':' in name or Path(name).is_reserved():
        raise ValueError('Invalid camera filename')
    image = camera.operation(0x1009, [handle])
    if len(image) != size:
        raise ValueError('Incomplete file')
    dest = directory / name
    with dest.open('xb') as f:
        f.write(image)
    return dest


def download(camera, directory, baseline, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        for handle in sorted(camera.handles()-baseline, reverse=True):
            dest = download_handle(camera, directory, handle, ('.JPG','.JPEG','.ORF'))
            if dest:
                print(f'Downloaded {dest} ({dest.stat().st_size} bytes)', flush=True)
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(1)


def release_once(camera, marker_path):
    try:
        camera.operation(0x9481, [1], no_data=True)
        time.sleep(0.6)
        with marker_path.open('x') as marker:
            marker.write(str(time.time()))
        camera.operation(0x9481, [2], no_data=True)
        time.sleep(0.12)
    finally:
        try:
            camera.operation(0x9481, [4], no_data=True)
        finally:
            camera.operation(0x9481, [5], no_data=True)


def capture_for_editor(directory):
    """Release once and collect this release's JPEGs, including a focus bracket."""
    from contextlib import ExitStack
    import comtypes

    comtypes.CoInitialize()
    with ExitStack() as resources:
        resources.callback(comtypes.CoUninitialize)
        camera = Camera([], 'readwrite', 'remote')
        resources.callback(camera.close)
        camera.open()
        for prop, target in [(0xd052, 1), (0xd0dc, 2)]:
            previous = camera.prop(prop)
            if previous != target:
                resources.callback(camera.setprop, prop, previous)
                camera.setprop(prop, target)
        seen = camera.handles()
        release_once(camera, directory / 'shutter-requested')
        photos = []
        deadline = time.monotonic() + 180
        last_new = None
        while time.monotonic() < deadline:
            for handle in sorted(camera.handles() - seen):
                path = download_handle(camera, directory, handle, ('.JPG', '.JPEG'))
                seen.add(handle)
                if path:
                    photos.append(path)
                    last_new = time.monotonic()
            if last_new is not None and time.monotonic() - last_new >= 8:
                return photos
            time.sleep(0.4)
        raise TimeoutError('Camera did not finish delivering JPEGs. Check the SD card and capture folder.')


def take(directory):
    directory.mkdir(exist_ok=False)
    camera = Camera([], 'readwrite', 'remote')
    try:
        camera.open()
        before = camera.handles()
        (directory/'baseline.json').write_text(json.dumps(sorted(before)))
        initial = camera.prop(0xd052)
        print('Card objects:', len(before), 'PC mode:', initial, flush=True)
        destination = None
        try:
            if initial != 1:
                camera.setprop(0xd052, 1)
            destination = camera.prop(0xd0dc)
            if destination != 2:
                camera.setprop(0xd0dc, 2)
            print('PC control and SD capture confirmed', flush=True)
            release_once(camera, directory/'shutter-requested')
            time.sleep(2)
            if download(camera, directory, before, timeout=3):
                return
        finally:
            try:
                if destination is not None and destination != 2:
                    camera.setprop(0xd0dc, destination)
            finally:
                if initial != 1:
                    camera.setprop(0xd052, initial)
        if not download(camera, directory, before):
            raise RuntimeError('No new image in the card listing')
    finally:
        camera.close()
        (directory/'usb.json').write_text(json.dumps(camera.audit, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('new_directory', type=Path)
    take(parser.parse_args().new_directory)
