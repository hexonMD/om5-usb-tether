"""Offline replay of the OM-5 settings-export CGI exchange using firmware formats."""

from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import om5_wifi_current_backup as backup


def multipart(data):
    prefix = b'----OIShareFormBoundary'
    result = b'<response><result>success</result></response>'
    return (prefix + b'\r\nContent-Type: text/xml\r\nContent-Length: ' +
            str(len(result)).encode() + b'\r\n\r\n' + result + prefix +
            b'\r\nContent-Type: text/csv\r\nContent-Length: ' + str(len(data)).encode() +
            b'\r\n\r\n' + data + prefix + b'--\r\n')


class FixtureResponse(BytesIO):
    status = 200

    def getheader(self, header):
        if header == 'Content-Length':
            return str(len(self.getvalue()))
        return None


class BackupTests(unittest.TestCase):
    def exchange(self, path, corrupt=False):
        # Force two offsets and put the boundary text in the payload.
        data = b'1,----OIShareFormBoundary\r\n' * 700
        commands = ''.join(f'<cgi name="{name}"><http_method type="get"/></cgi>'
                           for name in backup.REQUIRED)
        ok = b'<?xml version="1.0"?><response><result>ok</result>'
        chunks = [data[:backup.CHUNK_BYTES], data[backup.CHUNK_BYTES:]]
        second = chunks[1][:-1] if corrupt else chunks[1]
        replies = [
            ('/get_caminfo.cgi', b'<caminfo><model>OM-5</model></caminfo>'),
            ('/get_commandlist.cgi', f'<oishare>{commands}</oishare>'.encode()),
            ('/request_getmysetdata.cgi?mode=current&kind=current', ok + b'</response>'),
            ('/get_mysetbackupstate.cgi', ok + b'<status>busy</status></response>'),
            ('/get_mysetbackupstate.cgi', ok + b'<status>success</status></response>'),
            ('/get_mysetdatasize.cgi?kind=current',
             ok + f'<datasize>{len(data)}</datasize></response>'.encode()),
            (f'/get_partialmysetdata.cgi?kind=current&offset=0&size={len(chunks[0])}',
             multipart(chunks[0])),
            (f'/get_partialmysetdata.cgi?kind=current&offset={len(chunks[0])}&size={len(chunks[1])}',
             multipart(second)),
        ]
        test = self

        class Camera:
            def __init__(self, host, port, timeout):
                test.assertEqual((host, port), ('192.168.0.10', 80))

            def request(self, method, target, headers):
                test.assertEqual(method, 'GET')
                expected, self.body = replies.pop(0)
                test.assertEqual(target, expected)
                test.assertEqual(headers['User-Agent'], 'OlympusCameraKit')

            def getresponse(self):
                return FixtureResponse(self.body)

            def close(self):
                pass

        with patch('http.client.HTTPConnection', Camera), patch('time.sleep'):
            if corrupt:
                with self.assertRaisesRegex(ValueError, 'chunk length'):
                    backup.export_current(path)
                self.assertFalse(path.exists())
            else:
                result = backup.export_current(path)
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(result['bytes'], len(data))
                with self.assertRaises(FileExistsError):
                    backup.export_current(path)
        self.assertEqual(replies, [])

    def test_whole_current_with_two_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.exchange(Path(tmp) / 'capture.csv')

    def test_incomplete_chunk_does_not_publish_partial_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.exchange(Path(tmp) / 'capture.csv', corrupt=True)

    def test_default_offline(self):
        with patch('http.client.HTTPConnection', side_effect=AssertionError('unexpected connection')), redirect_stdout(StringIO()):
            backup.main([])


if __name__ == '__main__':
    unittest.main()
