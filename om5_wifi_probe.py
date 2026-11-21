"""Read-only OM-5 Wi-Fi discovery. No arguments performs NO network requests.

After the owner connects this PC to the camera's normal Wi-Fi network:
  python tools/om5_wifi_probe.py --execute --out evidence/wifi-discovery-001

Fixed HTTP allowlist; no proxy, redirects, retries, session/mode/setting changes,
debug shell, capture or firmware commands. Raw XML is saved locally only.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

HOST = '192.168.0.10'
ENDPOINTS = ('get_caminfo.cgi', 'get_connectmode.cgi', 'get_commandlist.cgi')
MAX_BODY = 1024 * 1024


def read_endpoint(endpoint):
    if endpoint not in ENDPOINTS:
        raise ValueError('Endpoint is not in the read-only discovery allowlist')
    connection = http.client.HTTPConnection(HOST, 80, timeout=5)
    try:
        connection.request('GET', '/' + endpoint, headers={
            'User-Agent': 'OlympusCameraKit', 'Accept': 'text/xml', 'Connection': 'close'})
        response = connection.getresponse()
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError('Response exceeds 1 MiB')
        if response.status != 200:
            raise ValueError(f'{endpoint}: HTTP {response.status}; no redirect or retry sent')
        # Reject XML DTD/entity declarations before parsing camera input.
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise ValueError('Unexpected XML declaration')
        root = ET.fromstring(data)
        if any(node.tag.rsplit('}', 1)[-1].lower() in ('error', 'errorcode', 'errcode')
               for node in root.iter()):
            raise ValueError(f'{endpoint}: camera reports an error')
        return data, root
    finally:
        connection.close()


def local_name(node):
    return node.tag.rsplit('}', 1)[-1]


def discover(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    result = {'started_utc': datetime.now(timezone.utc).isoformat(),
              'host': HOST, 'requests': [], 'complete': False,
              'scope': 'Read-only HTTP discovery; no debug shell or USB requests'}
    try:
        for endpoint in ENDPOINTS:
            attempt = {'method': 'GET', 'endpoint': endpoint}
            result['requests'].append(attempt)
            (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
            body, root = read_endpoint(endpoint)
            filename = endpoint.removesuffix('.cgi') + '.xml'
            (out / filename).write_bytes(body)
            attempt.update({'file': filename, 'bytes': len(body),
                            'sha256': hashlib.sha256(body).hexdigest()})
            if endpoint == 'get_caminfo.cgi':
                models = [node.text.strip() for node in root.iter()
                          if local_name(node) == 'model' and node.text]
                if local_name(root) != 'caminfo' or models != ['OM-5']:
                    raise ValueError('Expected original OM-5 identity; stopping discovery')
                # Print only non-identifying version/model fields, never serial/MAC.
                result['camera'] = {local_name(node): node.text for node in root.iter()
                                    if not len(node) and
                                    (local_name(node) == 'model' or 'version' in local_name(node).lower())}
            elif endpoint == 'get_commandlist.cgi':
                if local_name(root) != 'oishare':
                    raise ValueError('Unexpected command-list XML root')
                result['advertised_commands'] = sorted({node.attrib['name'] for node in root.iter()
                                                        if local_name(node) == 'cgi' and 'name' in node.attrib})
            time.sleep(0.1)
        result['complete'] = True
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Send the three read-only discovery queries')
    parser.add_argument('--out', type=Path, help='New local output directory, required with --execute')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'network_access': False, 'host': HOST, 'planned_get_requests': ENDPOINTS}, indent=2))
        return
    if not args.out:
        parser.error('--execute requires --out')
    print(json.dumps(discover(args.out), indent=2))


if __name__ == '__main__':
    main()
