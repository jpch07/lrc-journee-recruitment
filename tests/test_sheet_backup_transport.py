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
