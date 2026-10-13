"""Explicit read-only IMAP imports; no polling, message flags, or status changes.

Protocol references: https://docs.python.org/3.11/library/imaplib.html and
https://www.rfc-editor.org/rfc/rfc3501.html (EXAMINE, FETCH, UID SEARCH).
"""
from __future__ import annotations

import imaplib
import ipaddress
import json
import re
import socket
import ssl

import keyring

from .models import now, uid
from .security import public_url
from .tracking import MAX_EMAIL_BYTES, TrackingStore, _email

SERVICE = "linkedin-local-agent:imap"
META_KEY = "email_connection"
MAX_MESSAGES = 20
TIMEOUT = 20


def _public_host(value):
    raw = str(value or "")
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ValueError("Enter an exact public IMAP hostname")
    host = raw.strip().lower()
    if len(host) > 253 or "." not in host or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in host.split(".")):
        raise ValueError("Enter an exact public IMAP hostname without a scheme, path, or port")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Use a public hostname rather than an IP address")
    if not public_url("https://" + host, resolve=True):
        raise ValueError("IMAP hostname must resolve only to public addresses")
    return host


def _identity(host, username, folder, port):
    if port != 993 or isinstance(port, bool):
        raise ValueError("Only verified TLS on IMAP port 993 is supported")
    if not isinstance(username, str) or len(username) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", username):
        raise ValueError("Enter an email-safe mailbox username")
    if not isinstance(folder, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _./-]{0,199}", folder):
        raise ValueError("Use a simple mailbox folder name without quotes or control characters")
    return {"host": _public_host(host), "username": username, "folder": folder, "port": 993}


def _quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class BoundedIMAP4SSL(imaplib.IMAP4_SSL):
    """Pin validated public addresses and cap server literals before allocation."""
    def __init__(self, host, port=993, *, ssl_context=None, timeout=TIMEOUT):
        context = ssl_context or ssl.create_default_context()
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("Verified TLS is required")
        self._received_bytes = 0
        super().__init__(host, port, ssl_context=context, timeout=timeout)

    def _create_socket(self, timeout):
        # Resolve once for this connection, validate every address, then connect
        # to an IP while keeping the exact hostname for certificate validation.
        # A second DNS answer can never redirect the socket to a private host.
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if self.port != 993 or not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("IMAP destination must remain public on port 993")
        last_error = None
        for address in addresses:
            raw = None
            try:
                raw = socket.create_connection((address[4][0], 993), timeout)
                return self.ssl_context.wrap_socket(raw, server_hostname=self.host)
            except OSError as exc:
                last_error = exc
                if raw is not None:
                    raw.close()
        raise OSError("Could not establish verified IMAP TLS") from last_error

    def _consume(self, amount):
        self._received_bytes += amount
        if self._received_bytes > 24 * MAX_EMAIL_BYTES:
            raise ValueError("IMAP response exceeds the session size limit")

    def read(self, size):
        if size < 0 or size > MAX_EMAIL_BYTES:
            raise ValueError("IMAP literal exceeds the email size limit")
        self._consume(size)
        return super().read(size)

    def readline(self):
        line = super().readline()
        self._consume(len(line))
        if len(line) > 8192:
            raise ValueError("IMAP response line exceeds the size limit")
        return line


def _open_transport(host, port, *, ssl_context, timeout):
    return BoundedIMAP4SSL(host, port, ssl_context=ssl_context, timeout=timeout)


def _explicit_reference(parsed, job):
    text = parsed["subject"] + "\n" + parsed["text"]
    for reference in (job.requisition, job.id):
        if reference and re.search(r"\b(?:job|requisition)(?:\s+(?:id|reference|ref))?\s*[:#]\s*" + re.escape(reference) + r"(?![\w-])", text, re.I):
            return True
    return False


class EmailConnector:
    def __init__(self, store):
        self.store = store

    def _metadata(self):
        try:
            value = json.loads(self.store.meta(META_KEY) or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def status(self):
        value = self._metadata()
        enabled = value.get("enabled") is True and bool(re.fullmatch(r"[a-f0-9]{32}", str(value.get("credential_id", ""))))
        return {"enabled": enabled, "configured": enabled, "host": value.get("host", ""), "username": value.get("username", ""), "folder": value.get("folder", "INBOX"), "port": 993, "authorized_at": value.get("authorized_at", ""), "last_sync_at": value.get("last_sync_at", ""), "mode": "manual_imap_readonly", "automatic_polling": False}

    def configure(self, host, username, secret, confirmation=False, port=993, folder="INBOX"):
        if confirmation is not True:
            raise ValueError("Explicit authorization is required to save this read-only connection")
        identity = _identity(host, username, folder, port)
        if not isinstance(secret, str) or not 1 <= len(secret) <= 4096 or any(ord(c) < 32 or ord(c) == 127 for c in secret):
            raise ValueError("Enter a nonempty app-password without control characters")
        previous = self._metadata()
        credential_id = uid()
        try:
            # Binding the destination inside the keychain prevents tampered or
            # restored SQLite metadata from forwarding a secret to another host.
            keyring.set_password(SERVICE, credential_id, json.dumps({**identity, "secret": secret}))
        except keyring.errors.KeyringError as exc:
            raise ValueError("The OS credential store is unavailable; connection was not saved") from exc
        try:
            self.store.meta(META_KEY, json.dumps({**identity, "credential_id": credential_id, "enabled": True, "authorized_at": now(), "last_sync_at": ""}))
        except BaseException:
            try:
                keyring.delete_password(SERVICE, credential_id)
            except keyring.errors.KeyringError:
                pass
            raise
        old_id = previous.get("credential_id", "")
        if re.fullmatch(r"[a-f0-9]{32}", str(old_id)):
            try:
                keyring.delete_password(SERVICE, old_id)
            except keyring.errors.PasswordDeleteError:
                pass
            except keyring.errors.KeyringError as exc:
                raise ValueError("Connection saved, but the previous credential must be removed from the OS keychain") from exc
        return self.status()

    def disconnect(self, confirmation=False):
        if confirmation is not True:
            raise ValueError("Explicitly confirm disconnecting this mailbox")
        previous = self._metadata()
        self.store.meta(META_KEY, "{}")
        credential_id = previous.get("credential_id", "")
        if re.fullmatch(r"[a-f0-9]{32}", str(credential_id)):
            try:
                keyring.delete_password(SERVICE, credential_id)
            except keyring.errors.PasswordDeleteError:
                pass
            except keyring.errors.KeyringError as exc:
                raise ValueError("Connection disabled; remove its remaining credential using the OS keychain") from exc
        return self.status()

    def sync(self, job_id, confirmation=False):
        if confirmation is not True:
            raise ValueError("Explicitly authorize reading recent email for this job")
        job = self.store.job(job_id)
        if job is None:
            raise KeyError(job_id)
        metadata = self._metadata()
        if not self.status()["enabled"]:
            raise ValueError("Configure and authorize a mailbox before syncing")
        identity = _identity(metadata.get("host"), metadata.get("username"), metadata.get("folder"), metadata.get("port"))
        try:
            stored = keyring.get_password(SERVICE, metadata["credential_id"])
        except keyring.errors.KeyringError as exc:
            raise ValueError("The OS credential store is unavailable") from exc
        if not stored:
            raise ValueError("Mailbox credentials are missing; save an app-password again")
        try:
            credential = json.loads(stored)
        except (TypeError, ValueError):
            raise ValueError("Mailbox credential is invalid; save the connection again") from None
        if not isinstance(credential, dict) or any(credential.get(key) != value for key, value in identity.items()) or not isinstance(credential.get("secret"), str):
            raise ValueError("Mailbox credential does not match this authorized connection")
        transport = None
        counts = {"job_id": job_id, "scanned": 0, "matched": 0, "imported": 0, "duplicates": 0, "skipped": 0, "read_only": True, "limit": MAX_MESSAGES}
        try:
            transport = _open_transport(identity["host"], 993, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
            if transport.login(_quoted(identity["username"]), credential["secret"])[0] != "OK":
                raise ValueError("Mailbox authentication failed")
            status, data = transport.select(_quoted(identity["folder"]), readonly=True)
            if status != "OK" or not data or not isinstance(data[0], bytes) or not re.fullmatch(rb"\d{1,10}", data[0]):
                raise ValueError("Mailbox could not be opened read-only")
            total = int(data[0])
            if total > 4294967295:
                raise ValueError("Mailbox message count is invalid")
            if total:
                status, data = transport.uid("search", None, f"{max(1, total - MAX_MESSAGES + 1)}:{total}")
                if status != "OK" or len(data) != 1 or not isinstance(data[0], bytes) or len(data[0]) > 4096:
                    raise ValueError("Mailbox returned an invalid bounded search response")
                identifiers = data[0].split()
                if len(identifiers) > MAX_MESSAGES or any(not re.fullmatch(rb"[1-9]\d{0,9}", value) or int(value) > 4294967295 for value in identifiers) or len(set(identifiers)) != len(identifiers):
                    raise ValueError("Mailbox returned too many or invalid message identifiers")
                tracking = TrackingStore(self.store)
                for identifier in identifiers:
                    counts["scanned"] += 1
                    raw = self._fetch(transport, identifier.decode())
                    if raw is None:
                        counts["skipped"] += 1
                        continue
                    try:
                        parsed = _email(raw, job)
                    except (TypeError, ValueError, RecursionError):
                        counts["skipped"] += 1
                        continue
                    if not (parsed["company_match"] and parsed["role_match"] or _explicit_reference(parsed, job)):
                        counts["skipped"] += 1
                        continue
                    counts["matched"] += 1
                    evidence = tracking.import_email(job_id, raw, authorized=True)
                    if evidence["duplicate"]:
                        counts["duplicates"] += 1
                    else:
                        counts["imported"] += 1
                        with self.store.connection() as db:
                            row = db.execute("SELECT data FROM email_evidence WHERE id=?", (evidence["id"],)).fetchone()
                            record = json.loads(row[0])
                            record["source"] = "authorized_imap_sync"
                            db.execute("UPDATE email_evidence SET data=? WHERE id=?", (json.dumps(record), evidence["id"]))
            metadata["last_sync_at"] = now()
            self.store.meta(META_KEY, json.dumps(metadata))
            return counts
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
            # Providers may include usernames, secrets, or raw server text in
            # errors. Expose a fixed explanation only.
            raise ValueError("Read-only mailbox sync failed; check the host, app-password, and provider IMAP settings") from exc
        finally:
            if transport is not None:
                try:
                    transport.logout()
                except (imaplib.IMAP4.error, OSError, ValueError):
                    pass

    @staticmethod
    def _fetch(transport, identifier):
        status, data = transport.uid("fetch", identifier, "(RFC822.SIZE)")
        if status != "OK" or not data or len(data) != 1 or not isinstance(data[0], bytes):
            return None
        size = re.search(rb"\bRFC822\.SIZE (\d+)\b", data[0])
        message_uid = re.search(rb"\bUID (\d+)\b", data[0])
        if size is None or message_uid is None or int(message_uid[1]) != int(identifier):
            return None
        length = int(size[1])
        if not 0 < length <= MAX_EMAIL_BYTES:
            return None
        status, data = transport.uid("fetch", identifier, f"(BODY.PEEK[]<0.{MAX_EMAIL_BYTES}>)")
        if status != "OK" or not data:
            return None
        literals = [item for item in data if isinstance(item, tuple)]
        if len(literals) != 1 or len(literals[0]) != 2 or not isinstance(literals[0][0], bytes) or not isinstance(literals[0][1], bytes):
            return None
        header, raw = literals[0]
        message_uid = re.search(rb"\bUID (\d+)\b", header)
        if message_uid is None or int(message_uid[1]) != int(identifier) or len(raw) != length:
            return None
        return raw
