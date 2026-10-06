"""Signed server-to-script requests; never expose the Google secret to browsers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlsplit

import httpx

from .sheet_backup_export import BackupError, json_text

# Image, layout, and publication steps can legitimately run for about a minute.
GOOGLE_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
SPREADSHEET_ID = '11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0'
SPREADSHEET_URL = f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit'
ERRORS = {
    'AUTH': 'Google backup authorization failed. Check the private receiver secret and deployment.',
    'DESTINATION': 'Google backup is configured for a different spreadsheet or workspace.',
    'BUSY': 'Another backup is running. Wait for it to finish or for its 15-minute idle lease to expire.',
    'EXPIRED': 'This backup was interrupted or expired. Start a new backup; the previous complete backup is unchanged.',
    'SEQUENCE': 'Backup upload sequence changed. Start a new backup.',
    'CAPACITY': 'The spreadsheet has insufficient room for a safe backup. Nothing was truncated.',
    'COLLISION': 'A non-backup tab uses a reserved backup name. Rename that tab before retrying.',
    'VERIFY': 'Google read-back verification failed. The incomplete backup was not published.',
    'TABS': 'The backup tab layout was rejected.',
    'INCOMPLETE': 'The staged backup is incomplete and cannot be published.',
    'GOOGLE': 'Google could not complete this operation. Check its quotas and script authorization, then retry.',
}


class TemporaryGoogleError(BackupError):
    """Retryable transport/provider failure; message is safe for the UI."""


class Receiver:
    def __init__(self, url, secret, *, workspace_id='', transport=None):
        if not re.fullmatch(r'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec', url or '') or len(secret or '') < 32:
            raise BackupError('Google backup is not connected. Complete the one-time private script setup.')
        self.url, self.secret, self.workspace_id, self.transport = url, secret, workspace_id, transport

    def call(self, action, payload):
        for attempt in range(3):
            try:
                return self._call_once(action, payload)
            except TemporaryGoogleError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    def _call_once(self, action, payload):
        # A retry keeps the operation sequence but must use a new signed nonce.
        body = base64.b64encode(json_text({'action': action, 'spreadsheetId': SPREADSHEET_ID,
            'workspaceId': self.workspace_id, **payload}).encode()).decode('ascii')
        timestamp, nonce = int(time.time()), secrets.token_hex(16)
        signed = f'{timestamp}.{nonce}.{body}'
        envelope = {'timestamp': timestamp, 'nonce': nonce, 'payload': body,
            'signature': hmac.new(self.secret.encode(), signed.encode(), hashlib.sha256).hexdigest()}
        try:
            with httpx.Client(timeout=GOOGLE_TIMEOUT, follow_redirects=False, transport=self.transport) as client:
                response = client.post(self.url, json=envelope)
                if response.status_code in (301, 302, 303):
                    location = response.headers.get('location', '')
                    target = urlsplit(location)
                    if target.scheme != 'https' or target.hostname != 'script.googleusercontent.com' or target.port not in (None, 443) or target.username:
                        raise BackupError('Google returned an unexpected redirect; no backup credentials were forwarded.')
                    response = client.get(location)  # ContentService redirect: GET, never resend the signed body.
                if response.status_code in (429, 500, 502, 503, 504):
                    raise TemporaryGoogleError(ERRORS['GOOGLE'])
                if response.status_code != 200 or len(response.content) > 100_000:
                    raise BackupError(ERRORS['GOOGLE'])
                result = response.json()
        except httpx.HTTPError:
            raise TemporaryGoogleError('Google backup could not be reached. Retry the operation; no partial backup is published.') from None
        except ValueError:
            raise BackupError('Google backup could not be reached. Retry the operation; no partial backup is published.') from None
        if not isinstance(result, dict) or not result.get('ok'):
            if isinstance(result, dict) and result.get('code') == 'GOOGLE':
                raise TemporaryGoogleError(ERRORS['GOOGLE'])
            raise BackupError(ERRORS.get(result.get('code') if isinstance(result, dict) else None, ERRORS['GOOGLE']))
        if not isinstance(result.get('result'), dict):
            raise BackupError(ERRORS['GOOGLE'])
        return result['result']
