import imaplib
import base64
import json
import urllib.request
import urllib.parse
import boto3

_params: dict | None = None


def get_gmail_token(refresh_token, client_id, client_secret):
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def fetch_unseen(imap_host, user, password):
    mail = imaplib.IMAP4_SSL(imap_host)
    mail.login(user, password)
    mail.select("INBOX")
    _, uids = mail.search(None, "UNSEEN")
    messages = []
    for uid in uids[0].split():
        _, data = mail.fetch(uid, "(BODY.PEEK[])")
        messages.append((uid, data[0][1]))
    return mail, messages


def import_to_gmail(access_token, gmail_user, raw_message):
    encoded = base64.urlsafe_b64encode(raw_message).decode()
    body = json.dumps({"raw": encoded, "labelIds": ["INBOX"]}).encode()
    req = urllib.request.Request(
        f"https://gmail.googleapis.com/gmail/v1/users/{gmail_user}/messages/import",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False


def handler(event, context):
    global _params
    if _params is None:
        ssm = boto3.client("ssm")
        names = [
            "/mail-sync/gmail/refresh_token",
            "/mail-sync/gmail/client_id",
            "/mail-sync/gmail/client_secret",
            "/mail-sync/gmail/target_user",
            "/mail-sync/free1/user",
            "/mail-sync/free1/password",
            "/mail-sync/free2/user",
            "/mail-sync/free2/password",
        ]
        result = ssm.get_parameters(Names=names, WithDecryption=True)
        _params = {p["Name"]: p["Value"] for p in result["Parameters"]}
    params = _params

    access_token = get_gmail_token(
        params["/mail-sync/gmail/refresh_token"],
        params["/mail-sync/gmail/client_id"],
        params["/mail-sync/gmail/client_secret"],
    )
    gmail_user = params["/mail-sync/gmail/target_user"]

    total_imported = 0

    for account in ["free1", "free2"]:
        user = params[f"/mail-sync/{account}/user"]
        password = params[f"/mail-sync/{account}/password"]
        mail, messages = fetch_unseen("imap.free.fr", user, password)
        try:
            for uid, raw in messages:
                if import_to_gmail(access_token, gmail_user, raw):
                    status, _ = mail.store(uid, "+FLAGS", "\\Seen")
                    if status != "OK":
                        print(f"warn: store Seen failed for uid {uid}: {status}")
                    total_imported += 1
        finally:
            mail.logout()

    print(f"imported {total_imported} messages")
    return {"imported": total_imported}
