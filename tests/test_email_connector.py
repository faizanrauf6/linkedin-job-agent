import asyncio
import json
import ssl
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import email_connector as connector
from agent.email_api import register_email_routes
from agent.email_connector import EmailConnector
from agent.models import Job
from agent.store import Store
from agent.tracking import TrackingStore


def email(subject="Interview: Platform Engineer at Example Systems", body="Please schedule an interview."):
    return f"From: recruiter@example.com\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}".encode()


class Keyring:
    def __init__(self):
        self.values = {}
        self.reads = 0

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def get_password(self, service, account):
        self.reads += 1
        return self.values.get((service, account))

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class Mailbox:
    def __init__(self, messages=None, count=None, sizes=None):
        self.messages = messages or {1: email()}
        self.count = count if count is not None else max(self.messages)
        self.sizes = sizes or {}
        self.commands = []
        self.flags = {uid: set() for uid in self.messages}
        self.logged_out = False

    def login(self, username, password):
        self.commands.append(("login", username, password))
        return "OK", [b"Authenticated"]

    def select(self, folder, readonly=False):
        self.commands.append(("select", folder, readonly))
        if not readonly:
            raise AssertionError("Writable mailbox selection is forbidden")
        return "OK", [str(self.count).encode()]

    def uid(self, command, *arguments):
        self.commands.append((command, *arguments))
        if command == "search":
            first, last = map(int, arguments[-1].split(":"))
            return "OK", [b" ".join(str(uid).encode() for uid in sorted(self.messages) if first <= uid <= last)]
        if command != "fetch":
            raise AssertionError("Mailbox mutation command is forbidden")
        uid, query = arguments
        uid = int(uid)
        if query == "(RFC822.SIZE)":
            size = self.sizes.get(uid, len(self.messages[uid]))
            return "OK", [f"{uid} (UID {uid} RFC822.SIZE {size})".encode()]
        if query != "(BODY.PEEK[]<0.1048576>)":
            raise AssertionError("Only a bounded, non-marking PEEK fetch is allowed")
        raw = self.messages[uid]
        return "OK", [(f"{uid} (UID {uid} BODY[]<0> {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self):
        self.logged_out = True
        return "BYE", [b"Done"]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    store = Store(tmp_path / "agent.db")
    job = store.add_job(Job(linkedin_id="123", title="Platform Engineer", company="Example Systems", status="needs_input", requisition="REQ-123"))
    keyring = Keyring()
    for method in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(connector.keyring, method, getattr(keyring, method))
    monkeypatch.setattr(connector.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", args[1]))])
    connections = []
    mailbox = Mailbox()

    def transport(host, port, *, ssl_context, timeout):
        assert ssl_context.check_hostname
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        connections.append((host, port, timeout))
        return mailbox

    monkeypatch.setattr(connector, "_open_transport", transport)
    return SimpleNamespace(store=store, job=job, keyring=keyring, connections=connections, mailbox=mailbox, connector=EmailConnector(store))


def configure(env, **kwargs):
    return env.connector.configure("imap.example.com", "applicant@example.com", "app-password-private", confirmation=True, **kwargs)


def test_default_status_reads_no_credentials_and_missing_consent_connects_nothing(environment):
    env = environment
    assert env.connector.status()["enabled"] is False
    assert env.keyring.reads == 0
    with pytest.raises(ValueError, match="consent|authoriz|confirm"):
        env.connector.configure("imap.example.com", "applicant@example.com", "private")
    with pytest.raises(ValueError):
        env.connector.sync(env.job.id, confirmation=True)
    assert env.connections == []
    assert env.keyring.values == {}


def test_configuration_keeps_secret_only_in_os_keyring_and_disconnect_disables_access(environment):
    env = environment
    configured = configure(env)
    assert configured["enabled"] is True
    assert "app-password-private" not in json.dumps(configured)
    assert env.connections == []
    assert env.keyring.reads == 0
    with env.store.connection() as db:
        assert "app-password-private" not in json.dumps([tuple(row) for row in db.execute("SELECT * FROM meta")])
    credential = json.loads(next(iter(env.keyring.values.values())))
    assert credential == {"host": "imap.example.com", "username": "applicant@example.com", "folder": "INBOX", "port": 993, "secret": "app-password-private"}
    with pytest.raises(ValueError):
        env.connector.disconnect()
    assert env.connector.disconnect(confirmation=True)["enabled"] is False
    assert env.keyring.values == {}
    with pytest.raises(ValueError):
        env.connector.sync(env.job.id, confirmation=True)
    assert env.connections == []


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "https://imap.example.com", "imap.example.com:993", "imap.example.com/path", "imap.example.com\r\nLOGIN attacker", "user@imap.example.com"])
def test_invalid_hosts_never_use_credentials_or_connect(environment, host):
    env = environment
    with pytest.raises(ValueError):
        env.connector.configure(host, "applicant@example.com", "private", confirmation=True)
    assert env.keyring.values == {}
    assert env.keyring.reads == 0
    assert env.connections == []


def test_dns_private_address_rejected_before_keyring_reads_or_connections(environment, monkeypatch):
    env = environment
    configure(env)
    monkeypatch.setattr(connector.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", args[1]))])
    with pytest.raises(ValueError, match="public|host"):
        env.connector.sync(env.job.id, confirmation=True)
    assert env.keyring.reads == 0
    assert env.connections == []


@pytest.mark.parametrize("field,value", [("username", "user@example.com\r\nLOGOUT"), ("username", "user name@example.com"), ("folder", "INBOX\r\nDELETE INBOX"), ("folder", 'INBOX" LOGOUT'), ("port", 143)])
def test_mailbox_command_injection_and_non_tls_ports_are_rejected(environment, field, value):
    env = environment
    values = {"host": "imap.example.com", "username": "applicant@example.com", "secret": "private", "confirmation": True, field: value}
    with pytest.raises(ValueError):
        env.connector.configure(**values)
    assert env.keyring.values == {}
    assert env.connections == []


def test_sync_requires_new_consent_and_present_credentials_before_connecting(environment):
    env = environment
    configure(env)
    with pytest.raises(ValueError):
        env.connector.sync(env.job.id)
    assert env.keyring.reads == 0
    env.keyring.values.clear()
    with pytest.raises(ValueError, match="credential|password"):
        env.connector.sync(env.job.id, confirmation=True)
    assert env.connections == []


def test_readonly_peek_sync_imports_only_matching_messages_and_preserves_flags_and_job_state(environment):
    env = environment
    configure(env, folder="Employer mail")
    env.mailbox.messages = {1: email(), 2: email("Interview for Designer at Other Employer"), 3: email("Update", "Job reference: REQ-123. Thank you for applying.")}
    env.mailbox.count = 3
    env.mailbox.flags = {uid: set() for uid in env.mailbox.messages}
    result = env.connector.sync(env.job.id, confirmation=True)
    assert result["imported"] == 2
    assert result["skipped"] == 1
    assert result["read_only"] is True
    assert env.connections == [("imap.example.com", 993, 20)]
    assert env.mailbox.commands[0] == ("login", '"applicant@example.com"', "app-password-private")
    assert env.mailbox.commands[1] == ("select", '"Employer mail"', True)
    assert all(not flags for flags in env.mailbox.flags.values())
    assert env.mailbox.logged_out
    assert env.store.job(env.job.id).status == "needs_input"
    assert not env.store.job(env.job.id).submission_started
    history = TrackingStore(Store(env.store.path)).history(env.job.id)
    assert len(history["email_evidence"]) == 2
    assert history["receipts"] == []
    assert history["stage"] == "prospect"
    assert all(item["trusted"] is False for item in history["email_evidence"])
    repeated = env.connector.sync(env.job.id, confirmation=True)
    assert repeated["imported"] == 0
    assert repeated["duplicates"] == 2


def test_sync_limits_search_to_most_recent_twenty_messages(environment):
    env = environment
    configure(env)
    env.mailbox.messages = {uid: email(body=f"Interview message {uid}") for uid in range(1, 101)}
    env.mailbox.count = 100
    result = env.connector.sync(env.job.id, confirmation=True)
    assert result["scanned"] == 20
    assert result["imported"] == 20
    assert ("search", None, "81:100") in env.mailbox.commands
    assert len([command for command in env.mailbox.commands if command[0] == "fetch"]) == 40
    assert len(TrackingStore(env.store).history(env.job.id)["email_evidence"]) == 20


def test_oversized_and_truncated_messages_are_not_fetched_or_imported(environment):
    env = environment
    configure(env)
    env.mailbox.messages = {1: email(), 2: email(), 3: b"not an email"}
    env.mailbox.count = 3
    env.mailbox.sizes = {1: 1024 * 1024 + 1, 2: len(email()) + 10}
    result = env.connector.sync(env.job.id, confirmation=True)
    assert result["imported"] == 0
    assert ("fetch", "1", "(BODY.PEEK[]<0.1048576>)") not in env.mailbox.commands
    assert TrackingStore(env.store).history(env.job.id)["email_evidence"] == []
    assert env.mailbox.logged_out


def test_protocol_literal_limit_rejects_oversize_before_allocating_or_reading():
    client = object.__new__(connector.BoundedIMAP4SSL)
    with pytest.raises(ValueError, match="limit|large|size"):
        client.read(1024 * 1024 + 1)


def test_tampered_metadata_cannot_forward_keychain_secret_to_another_public_host(environment):
    env = environment
    configure(env)
    metadata = json.loads(env.store.meta(connector.META_KEY))
    metadata["host"] = "attacker.example.com"
    env.store.meta(connector.META_KEY, json.dumps(metadata))
    with pytest.raises(ValueError, match="match"):
        env.connector.sync(env.job.id, confirmation=True)
    assert env.connections == []


def test_email_routes_require_paused_idle_and_explicit_authorization(environment):
    env = environment
    app = FastAPI()
    app.state.store = env.store
    worker = SimpleNamespace(busy=False, run_lock=asyncio.Lock())
    register_email_routes(app, env.store, worker, env.store.path.parent)
    client = TestClient(app)
    body = {"host": "imap.example.com", "username": "applicant@example.com", "secret": "app-password-private", "confirmation": False}
    assert client.get("/api/email/connection").json()["enabled"] is False
    assert client.post("/api/email/connection", json=body).status_code == 409
    body["confirmation"] = True
    worker.busy = True
    assert client.post("/api/email/connection", json=body).status_code == 409
    worker.busy = False
    config = env.store.config()
    config.settings.paused = False
    env.store.save_config(config)
    assert client.post("/api/email/connection", json=body).status_code == 409
    config = env.store.config()
    config.settings.paused = True
    env.store.save_config(config)
    response = client.post("/api/email/connection", json=body)
    assert response.status_code == 200
    assert "app-password-private" not in response.text
    path = "/api/jobs/" + env.job.id + "/email-sync"
    assert client.post(path, json={"confirmation": False}).status_code == 409
    assert env.connections == []
    assert client.post(path, json={"confirmation": True}).json()["imported"] == 1
    assert env.store.job(env.job.id).status == "needs_input"
    assert client.post("/api/jobs/missing/email-sync", json={"confirmation": True}).status_code == 404
    assert client.request("DELETE", "/api/email/connection", json={"confirmation": True}).json()["enabled"] is False
