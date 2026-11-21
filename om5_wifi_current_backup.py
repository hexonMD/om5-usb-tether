"""Export the original OM-5's current settings over its ordinary, paired Wi-Fi link."""

import argparse
import hashlib
import http.client
from io import BytesIO
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from om5_wifi_probe import HOST, local_name, read_endpoint


START = 'request_getmysetdata'
STATE = 'get_mysetbackupstate'
SIZE = 'get_mysetdatasize'
CHUNK = 'get_partialmysetdata'
REQUIRED = (START, STATE, SIZE, CHUNK)
CHUNK_BYTES = 16 * 1024  # Each non-final chunk keeps the next offset 128-byte aligned.
MAX_BACKUP = 8 * 1024 * 1024
PREFIX = b'----OIShareFormBoundary'


def get(endpoint, params):
    if endpoint not in REQUIRED:
        raise ValueError('Unexpected export endpoint')
    path = '/' + endpoint + '.cgi'
    if params:
        path += '?' + urlencode(params)
    connection = http.client.HTTPConnection(HOST, 80, timeout=5)
    try:
        connection.request('GET', path, headers={'User-Agent': 'OlympusCameraKit',
                           'Accept': 'multipart/form-data, text/xml', 'Connection': 'close'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f'{endpoint} returned HTTP {response.status}')
        limit = CHUNK_BYTES + 8192
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f'{endpoint} exceeded the expected response size')
        length = response.getheader('Content-Length')
        if length is not None and (not re.fullmatch(r'[0-9]+', length) or int(length) != len(data)):
            raise ValueError(f'{endpoint} sent an incomplete response')
        return data
    finally:
        connection.close()


def response_xml(data, expected='ok'):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Unexpected XML declaration')
    root = ET.fromstring(data)
    if root.tag != 'response' or root.findtext('result') != expected:
        raise ValueError(f'Camera returned {root.findtext("result")!r}, expected {expected!r}')
    return root


def read_chunk(body, wanted):
    # Stock firmware writes fixed headers and length-delimited parts. An opaque
    # CSV can itself contain a boundary string and can lack a trailing newline.
    stream = BytesIO(body)

    def part(content_type):
        if stream.readline() != PREFIX + b'\r\n':
            raise ValueError('Missing camera multipart boundary')
        if stream.readline() != b'Content-Type: ' + content_type + b'\r\n':
            raise ValueError('Unexpected camera multipart content type')
        size_line = re.fullmatch(rb'Content-Length: ([0-9]{1,8})\r\n', stream.readline())
        if not size_line or stream.readline() != b'\r\n':
            raise ValueError('Invalid camera multipart header')
        size = int(size_line[1])
        if size > CHUNK_BYTES + 1024:
            raise ValueError('Oversized camera multipart component')
        data = stream.read(size)
        if len(data) != size:
            raise ValueError('Truncated camera multipart component')
        return data

    response_xml(part(b'text/xml'), 'success')
    csv = part(b'text/csv')
    if len(csv) != wanted or stream.read() != PREFIX + b'--\r\n':
        raise ValueError('Camera chunk length or closing boundary mismatch')
    return csv


def export_current(destination):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    if not destination.parent.is_dir():
        raise FileNotFoundError(destination.parent)
    _, info = read_endpoint('get_caminfo.cgi')
    if local_name(info) != 'caminfo' or [n.text for n in info.iter() if local_name(n) == 'model'] != ['OM-5']:
        raise ValueError('Expected original OM-5')
    _, commands = read_endpoint('get_commandlist.cgi')
    advertised = {node.get('name') for node in commands.iter() if local_name(node) == 'cgi'
                  and any(local_name(child) == 'http_method' and child.get('type') == 'get' for child in node)}
    if local_name(commands) != 'oishare' or not set(REQUIRED) <= advertised:
        raise ValueError('Camera does not advertise the required backup GET requests')
    response_xml(get(START, {'mode': 'current', 'kind': 'current'}))
    deadline = time.monotonic() + 60
    while True:
        state = response_xml(get(STATE, {})).findtext('status')
        if state == 'success':
            break
        if state != 'busy':
            raise ValueError(f'Backup preparation returned {state!r}')
        if time.monotonic() >= deadline:
            raise TimeoutError('Camera backup was still busy after 60 seconds')
        time.sleep(0.5)
    size_tag = response_xml(get(SIZE, {'kind': 'current'})).findtext('datasize', '')
    if not re.fullmatch(r'[0-9]{1,8}', size_tag) or not 0 < int(size_tag) <= MAX_BACKUP:
        raise ValueError('Camera reported an invalid backup size')
    size = int(size_tag)
    data = bytearray()
    while len(data) < size:
        requested = min(CHUNK_BYTES, size - len(data))
        body = get(CHUNK, {'kind': 'current', 'offset': len(data), 'size': requested})
        data.extend(read_chunk(body, requested))
    with destination.open('xb') as out:
        try:
            out.write(data)
            out.flush()
        except OSError:
            out.close()
            destination.unlink()
            raise
    return {'path': str(destination), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Request an export on the paired camera Wi-Fi')
    parser.add_argument('--out', type=Path, help='New local CSV file for the whole current settings export')
    args = parser.parse_args(argv)
    if not args.execute:
        print('Offline preview. To export after joining the camera Wi-Fi, use --execute --out om5-current.csv')
        return
    if not args.out:
        parser.error('--execute requires --out')
    print(json.dumps(export_current(args.out)))


if __name__ == '__main__':
    main()
