import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

import handler


@pytest.fixture(autouse=True)
def reset_params_cache():
    """Réinitialise le cache SSM entre chaque test (simule un cold start)."""
    handler._params = None
    yield
    handler._params = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_urlopen_response(body: dict):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps(body).encode()
    resp.status = 200
    return resp


def _make_ssm(overrides: dict | None = None):
    defaults = {
        "/mail-sync/gmail/refresh_token": "rt",
        "/mail-sync/gmail/client_id": "cid",
        "/mail-sync/gmail/client_secret": "cs",
        "/mail-sync/gmail/target_user": "me@gmail.com",
        "/mail-sync/free1/user": "u1@free.fr",
        "/mail-sync/free1/password": "p1",
        "/mail-sync/free2/user": "u2@free.fr",
        "/mail-sync/free2/password": "p2",
        "/mail-sync/imap_host": "imap.free.fr",
    }
    if overrides:
        defaults.update(overrides)
    ssm = MagicMock()
    ssm.get_parameters.return_value = {
        "Parameters": [{"Name": k, "Value": v} for k, v in defaults.items()]
    }
    return ssm


def _make_imap(uids: bytes = b"", message_bytes: bytes = b"raw"):
    """Crée un mock IMAP4_SSL.

    return_value (pas side_effect) sur fetch afin que le même mock
    puisse être appelé plusieurs fois sans s'épuiser (free1 + free2).
    """
    imap = MagicMock()
    imap.search.return_value = (None, [uids])
    imap.fetch.return_value = (None, [(None, message_bytes)])
    imap.store.return_value = ("OK", None)
    return imap


# ── get_gmail_token ───────────────────────────────────────────────────────────

class TestGetGmailToken:
    def test_returns_access_token(self):
        resp = _make_urlopen_response({"access_token": "tok_abc"})
        with patch("urllib.request.urlopen", return_value=resp):
            assert handler.get_gmail_token("rt", "cid", "cs") == "tok_abc"

    def test_sends_correct_body(self):
        resp = _make_urlopen_response({"access_token": "t"})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            handler.get_gmail_token("myrefresh", "myclient", "mysecret")
        body = mock_open.call_args[0][0].data.decode()
        assert "grant_type=refresh_token" in body
        assert "myrefresh" in body
        assert "myclient" in body
        assert "mysecret" in body

    def test_calls_google_token_endpoint(self):
        resp = _make_urlopen_response({"access_token": "t"})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            handler.get_gmail_token("rt", "cid", "cs")
        url = mock_open.call_args[0][0].full_url
        assert "oauth2.googleapis.com/token" in url


# ── fetch_unseen ──────────────────────────────────────────────────────────────

class TestFetchUnseen:
    def test_returns_mail_object_and_messages(self):
        imap = _make_imap(uids=b"42", message_bytes=b"email_bytes")
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            mail, messages = handler.fetch_unseen("imap.free.fr", "u", "p")
        assert mail is imap
        assert messages == [(b"42", b"email_bytes")]

    def test_no_unseen_returns_empty_list(self):
        imap = _make_imap(uids=b"")
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            _, messages = handler.fetch_unseen("imap.free.fr", "u", "p")
        assert messages == []

    def test_multiple_messages(self):
        imap = _make_imap(uids=b"1 2 3")
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            _, messages = handler.fetch_unseen("imap.free.fr", "u", "p")
        assert len(messages) == 3

    def test_connects_with_ssl_and_credentials(self):
        imap = _make_imap()
        with patch("imaplib.IMAP4_SSL", return_value=imap) as mock_ssl:
            handler.fetch_unseen("imap.free.fr", "user@free.fr", "mypass")
        mock_ssl.assert_called_once_with("imap.free.fr")
        imap.login.assert_called_once_with("user@free.fr", "mypass")
        imap.select.assert_called_once_with("INBOX")

    def test_uses_body_peek_to_avoid_marking_seen(self):
        imap = _make_imap(uids=b"1")
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            handler.fetch_unseen("imap.free.fr", "u", "p")
        imap.fetch.assert_called_once_with(b"1", "(BODY.PEEK[])")


# ── import_to_gmail ───────────────────────────────────────────────────────────

class TestImportToGmail:
    def test_returns_true_on_200(self):
        resp = _make_urlopen_response({})
        with patch("urllib.request.urlopen", return_value=resp):
            assert handler.import_to_gmail("tok", "me@gmail.com", b"raw") is True

    def test_returns_false_on_http_error(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            url=None, code=403, msg="Forbidden", hdrs=None, fp=None
        )):
            assert handler.import_to_gmail("tok", "me@gmail.com", b"raw") is False

    def test_sends_inbox_label(self):
        resp = _make_urlopen_response({})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            handler.import_to_gmail("tok", "me@gmail.com", b"raw")
        body = json.loads(mock_open.call_args[0][0].data)
        assert body["labelIds"] == ["INBOX"]

    def test_sends_bearer_token(self):
        resp = _make_urlopen_response({})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            handler.import_to_gmail("mytoken", "me@gmail.com", b"raw")
        auth = mock_open.call_args[0][0].get_header("Authorization")
        assert auth == "Bearer mytoken"


# ── handler ───────────────────────────────────────────────────────────────────

class TestHandler:
    def _run(self, ssm, imap, urlopen_side_effect):
        with patch("boto3.client", return_value=ssm), \
             patch("imaplib.IMAP4_SSL", return_value=imap), \
             patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            return handler.handler({}, None)

    def _make_urlopen(self, import_success: bool = True):
        token_resp = _make_urlopen_response({"access_token": "tok"})

        import_resp = MagicMock()
        import_resp.__enter__ = lambda s: s
        import_resp.__exit__ = MagicMock(return_value=False)
        import_resp.status = 200

        def side_effect(req):
            if "oauth2.googleapis.com" in req.full_url:
                return token_resp
            if import_success:
                return import_resp
            raise urllib.error.HTTPError(url=None, code=500, msg="err", hdrs=None, fp=None)

        return side_effect

    def test_ssm_loaded_once_across_multiple_invocations(self):
        ssm = _make_ssm()
        imap = _make_imap()

        with patch("boto3.client", return_value=ssm), \
             patch("imaplib.IMAP4_SSL", return_value=imap), \
             patch("urllib.request.urlopen", return_value=_make_urlopen_response({"access_token": "t"})):
            handler.handler({}, None)
            handler.handler({}, None)
            handler.handler({}, None)

        ssm.get_parameters.assert_called_once()

    def test_no_messages_returns_zero_imported(self):
        result = self._run(_make_ssm(), _make_imap(), self._make_urlopen())
        assert result == {"imported": 0}

    def test_imports_one_message_per_account(self):
        imap = _make_imap(uids=b"1", message_bytes=b"rawmail")
        result = self._run(_make_ssm(), imap, self._make_urlopen())
        assert result == {"imported": 2}  # 1 message × 2 comptes
        assert imap.store.call_count == 2

    def test_failed_import_does_not_flag_seen(self):
        imap = _make_imap(uids=b"1", message_bytes=b"rawmail")
        result = self._run(_make_ssm(), imap, self._make_urlopen(import_success=False))
        assert result == {"imported": 0}
        imap.store.assert_not_called()

    def test_store_failure_warns_but_still_counts_import(self, capsys):
        imap = _make_imap(uids=b"1", message_bytes=b"rawmail")
        imap.store.return_value = ("NO", None)
        result = self._run(_make_ssm(), imap, self._make_urlopen())
        assert result["imported"] == 2
        assert "warn" in capsys.readouterr().out

    def test_imap_logout_called_even_on_failure(self):
        imap = _make_imap(uids=b"1", message_bytes=b"rawmail")
        self._run(_make_ssm(), imap, self._make_urlopen(import_success=False))
        assert imap.logout.call_count == 2  # une fois par compte
