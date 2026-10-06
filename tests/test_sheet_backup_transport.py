import base64
import hashlib
import hmac
import json

import httpx
import pytest

from app.sheet_backup_transport import Receiver, BackupError


def test_signs_exact_payload_and_follows_only_google_response_redirect():
    seen = []
    def handler(request):
        seen.append(request)
        if request.method == 'POST':
            envelope = json.loads(request.content)
            signed = f"{envelope['timestamp']}.{envelope['nonce']}.{envelope['payload']}"
            assert hmac.compare_digest(envelope['signature'], hmac.new(b's' * 40, signed.encode(), hashlib.sha256).hexdigest())
            assert json.loads(base64.b64decode(envelope['payload']))['action'] == 'status'
            return httpx.Response(302, headers={'location': 'https://script.googleusercontent.com/macros/echo?x=1'})
        return httpx.Response(200, json={'ok': True, 'result': {'state': 'ready'}})
    receiver = Receiver('https://script.google.com/macros/s/example/exec', 's' * 40,
                        transport=httpx.MockTransport(handler))
    assert receiver.call('status', {})['state'] == 'ready'
    assert seen[1].method == 'GET'
    assert not seen[1].content


def test_rejects_redirect_and_masks_upstream_body():
    def handler(request):
        return httpx.Response(302, headers={'location': 'https://evil.example/steal'})
    with pytest.raises(BackupError, match='Google'):
        Receiver('https://script.google.com/macros/s/example/exec', 's'*40,
                 transport=httpx.MockTransport(handler)).call('status', {})
    def failure(request):
        return httpx.Response(500, text='SECRET')
    with pytest.raises(BackupError) as caught:
        Receiver('https://script.google.com/macros/s/example/exec', 's'*40,
                 transport=httpx.MockTransport(failure)).call('status', {})
    assert 'SECRET' not in str(caught.value)


@pytest.mark.parametrize('url', ['http://script.google.com/macros/s/x/exec', 'https://evil.test',
    'https://script.google.com@evil.test/macros/s/x/exec', 'https://script.google.com/macros/s/x/exec?other=1'])
def test_bad_receiver_url(url):
    with pytest.raises(BackupError):
        Receiver(url, 's' * 40)


def test_temporary_google_failure_retries_with_backoff_and_fresh_nonce(monkeypatch):
    seen, delays = [], []
    monkeypatch.setattr('app.sheet_backup_transport.time.sleep', delays.append)
    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(429)
        if len(seen) == 2:
            return httpx.Response(200, json={'ok': False, 'code': 'GOOGLE'})
        return httpx.Response(200, json={'ok': True, 'result': {'state': 'ready'}})
    receiver = Receiver('https://script.google.com/macros/s/example/exec', 's' * 40,
                        transport=httpx.MockTransport(handler))
    assert receiver.call('status', {})['state'] == 'ready'
    assert delays == [1, 2]
    assert len({item['nonce'] for item in seen}) == 3


def test_google_request_timeout_allows_slow_operations_but_bounds_connects():
    timeouts = []
    def handler(request):
        timeouts.append(request.extensions['timeout'])
        return httpx.Response(200, json={'ok': True, 'result': {'state': 'ready'}})
    receiver = Receiver('https://script.google.com/macros/s/example/exec', 's' * 40,
                        transport=httpx.MockTransport(handler))

    assert receiver.call('status', {})['state'] == 'ready'
    assert timeouts == [{'connect': 10.0, 'read': 120.0, 'write': 120.0, 'pool': 120.0}]


def test_read_timeout_retries_the_same_operation_with_a_fresh_nonce(monkeypatch):
    seen, delays = [], []
    monkeypatch.setattr('app.sheet_backup_transport.time.sleep', delays.append)
    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            raise httpx.ReadTimeout('response acknowledgement was lost', request=request)
        return httpx.Response(200, json={'ok': True, 'result': {'next': 8}})
    receiver = Receiver('https://script.google.com/macros/s/example/exec', 's' * 40,
                        transport=httpx.MockTransport(handler))
    payload = {'runId': 'a' * 32, 'sequence': 7, 'operation': {'kind': 'image', 'row': 2}}

    assert receiver.call('apply', payload) == {'next': 8}
    assert delays == [1]
    assert len(seen) == 2
    assert seen[0]['payload'] == seen[1]['payload']
    assert seen[0]['nonce'] != seen[1]['nonce']


def test_busy_response_is_not_retried(monkeypatch):
    attempts, delays = [], []
    monkeypatch.setattr('app.sheet_backup_transport.time.sleep', delays.append)
    def handler(request):
        attempts.append(request)
        return httpx.Response(200, json={'ok': False, 'code': 'BUSY'})
    receiver = Receiver('https://script.google.com/macros/s/example/exec', 's' * 40,
                        transport=httpx.MockTransport(handler))

    with pytest.raises(BackupError, match='Another backup is running'):
        receiver.call('status', {})
    assert len(attempts) == 1
    assert delays == []
