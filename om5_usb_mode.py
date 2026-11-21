"""Back up and restore the original OM-5 1.2 USB preference over paired Wi-Fi."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlencode

from om5_wifi_current_backup import CHUNK_BYTES, HOST, export_current, response_xml
from om5_wifi_probe import read_endpoint

STAGED = "send_partialmysetdata"
KEY = b"2,MODE_SELECT_PC,"
MTP = KEY + b"MODE_PC_MTP"
REMOTE = KEY + b"MODE_PC_REMOTE"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def remote_copy(original):
    rows = original.split(b'\r\n')
    if [row for row in rows if row.startswith(KEY)] != [MTP]:
        raise ValueError('Expected exactly one saved MTP preference. Check Custom Menu D4 → USB Mode.')
    index = rows.index(MTP)
    rows[index] = REMOTE
    return b'\r\n'.join(rows)


def identify():
    _, camera = read_endpoint('get_caminfo.cgi')
    serial = camera.findtext('./SerialNumberList/Serial1')
    if camera.tag != 'caminfo' or camera.findtext('model') != 'OM-5' or not serial:
        raise ValueError('Expected an original OM-5 with an identifiable body.')
    _, commands = read_endpoint('get_commandlist.cgi')
    for name in ('switch_cammode', 'request_restoremysetdata', 'get_mysetrestorestate',
                 'get_mysetdatasize', 'set_mysetdatasize', STAGED, 'exec_reboot'):
        method = 'post' if name == STAGED else 'get'
        if commands.find(f".//cgi[@name='{name}']/http_method[@type='{method}']") is None:
            raise ValueError(f'Camera does not advertise {name}.')
    return digest(serial.encode())


def maintenance():
    reply = request('switch_cammode', {'mode': 'maintenance'})
    if reply.strip():
        response_xml(reply)
    time.sleep(1)


def prepare(folder):
    if folder.exists():
        raise FileExistsError('Choose a new backup folder.')
    body = identify()
    folder.mkdir(mode=0o700, parents=True)
    maintenance()
    original = folder / 'original.csv'
    export_current(original)
    data = original.read_bytes()
    remote = remote_copy(data)
    (folder / 'remote.csv').write_bytes(remote)
    (folder / 'body.json').write_text(json.dumps({'body': body, 'original_sha256': digest(data)}))
    print('Saved original.csv and remote.csv. The only changed row is MODE_SELECT_PC, MTP to REMOTE.')


def load_session(folder):
    meta = json.loads((folder / 'body.json').read_text())
    original = (folder / 'original.csv').read_bytes()
    remote = remote_copy(original)
    if digest(original) != meta['original_sha256'] or remote != (folder / 'remote.csv').read_bytes():
        raise ValueError('Backup files changed. Prepare a fresh backup in a new folder.')
    if identify() != meta['body']:
        raise ValueError('Connected camera differs from the body that supplied this backup.')
    maintenance()
    return original, remote


def current_bytes():
    with TemporaryDirectory() as tmp:
        current = Path(tmp) / 'current.csv'
        export_current(current)
        return current.read_bytes()


def apply(folder):
    original, remote = load_session(folder)
    if (folder / 'restore-submitted').exists():
        raise ValueError('A restore was already submitted. Run verify before taking further action.')
    if current_bytes() != original:
        raise ValueError('Current settings have changed. Prepare a fresh backup in a new folder.')
    stage(remote)
    response_xml(request('request_restoremysetdata', {'action': 'errorcheck'}))
    wait_result()
    errors = response_xml(request('get_mysetdatasize', {'kind': 'errorinfo'})).findtext('datasize')
    if errors != '0':
        raise ValueError(f'Camera validation reported {errors!r} bytes of errors. Stopped before restore.')
    # errorcheck consumes the staged data. Upload the same verified bytes again.
    stage(remote)
    with (folder / 'restore-submitted').open('x') as marker:
        marker.write(digest(remote))
    response_xml(request('request_restoremysetdata', {'action': 'restore'}))
    wait_result()
    if current_bytes() != remote:
        raise ValueError('Restore reported success but full readback differs. Check with verify.')
    print('Full camera readback matches remote.csv. Run restart with the same backup folder.')


def verify(folder, restart=False):
    original, remote = load_session(folder)
    current = current_bytes()
    if current == original:
        print('The original MTP backup is still active.')
        if restart:
            raise ValueError('Restore and verify Remote before requesting a restart.')
        return
    if current != remote:
        raise ValueError('Full readback differs from both saved copies. No restart was requested.')
    print('USB Remote confirmed by full readback.')
    if restart:
        reply = request('exec_reboot')
        if reply.strip():
            response_xml(reply)
        print('Camera accepted restart. Wait for startup, then connect the USB data cable.')


def request(endpoint, params=None, body=None):
    path = '/' + endpoint + '.cgi'
    if params:
        path += '?' + urlencode(params)
    headers = {'User-Agent': 'OlympusCameraKit', 'Connection': 'close'}
    if body is not None:
        headers['Content-Type'] = 'application/octet-stream'
    conn = http.client.HTTPConnection(HOST, timeout=30)
    try:
        if body is None:
            conn.request('GET', path, headers=headers)
            resp = conn.getresponse()
        else:
            # Queue the short body and its HTTP headers in a single socket send.
            headers.update({'Host': HOST, 'Content-Length': str(len(body))})
            head = f'POST {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers.items())
            conn.connect()
            conn.sock.sendall((head+'\r\n').encode('ascii')+body)
            resp = http.client.HTTPResponse(conn.sock, method='POST')
            resp.begin()
        payload = resp.read(8193)
        if resp.status not in ((200, 202) if endpoint == 'exec_reboot' else (200,)) or len(payload) > 8192:
            raise RuntimeError(f'{endpoint}: HTTP {resp.status}, {payload[:180]!r}')
        return payload
    finally:
        conn.close()


def wait_result():
    for _ in range(60):
        root = response_xml(request('get_mysetrestorestate'))
        status = root.findtext('status')
        if status == 'success':
            return
        if status != 'busy':
            raise RuntimeError(f'Camera reported restore state {status!r}')
        time.sleep(1)
    raise TimeoutError('Camera has not reported completion')


def stage(remote):
    response_xml(request('set_mysetdatasize', {'size': len(remote)}))
    chunk_size = min(CHUNK_BYTES, 1024)
    for offset in range(0, len(remote), chunk_size):
        chunk = remote[offset:offset+chunk_size]
        if offset % 4096 == 0:
            print(f'Staging bytes at offset {offset}', flush=True)
        for attempt in range(3):
            try:
                response_xml(request(STAGED, {'offset': offset, 'size': len(chunk)}, chunk))
                break
            except (TimeoutError, ConnectionError) as exc:
                if attempt == 2:
                    raise
                print(f'Resending the same staging range at {offset}: {type(exc).__name__}', flush=True)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'apply', 'verify', 'restart'):
        command = commands.add_parser(name)
        command.add_argument('folder', type=Path, help='Private local backup directory')
        if name == 'prepare':
            command.add_argument('--body-version', required=True, choices=['1.2'],
                                 help='Body firmware shown in the camera menu')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args.folder)
        elif args.command == 'apply':
            apply(args.folder)
        else:
            verify(args.folder, restart=args.command == 'restart')
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as problem:
        parser.exit(1, f'{problem}\n')


if __name__ == '__main__':
    main()
