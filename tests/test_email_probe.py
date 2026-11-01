"""Tests for the keyless control-checked recipient probe.

The probe is what lets a host with no verifier key (no Reoon/ZeroBounce/
Hunter) turn a pattern guess into a real ``verified`` / ``risky`` / ``invalid``
verdict: it RCPTs a guaranteed-fake control address and the candidate in one
session and only trusts an answer the control supports.

Every test here stubs SMTP — nothing leaves the machine (see conftest.py,
which also neutralises the probe for all other tests).
"""

import os
import tempfile

import pytest
import pytest_asyncio

from harvey.state import StateManager
from harvey.integrations import email_finder as ef

# The real probe, captured before conftest's autouse stub replaces the
# module attribute for every test.
REAL_PROBE = ef.probe_recipient


class FakeResponse:
    def __init__(self, code, message=""):
        self.code = code
        self.message = message


class FakeSMTP:
    """Stands in for aiosmtplib.SMTP.

    `codes` maps address -> SMTP reply code, or a (code, reply-text) tuple
    (``"*"`` is the default for any unlisted address).
    """

    last_instance = None

    def __init__(self, codes, fail_connect=False):
        self.codes = codes
        self.fail_connect = fail_connect
        self.sent = []
        FakeSMTP.last_instance = self

    async def connect(self):
        if self.fail_connect:
            raise OSError("tarpit")
        return FakeResponse(220)

    async def ehlo(self, name=None):
        return FakeResponse(250)

    async def mail(self, sender, options=None):
        self.sent.append(("MAIL", sender))
        return FakeResponse(250)

    async def rcpt(self, addr, options=None):
        self.sent.append(("RCPT", addr))
        entry = self.codes.get(addr, self.codes.get("*", 550))
        code, text = entry if isinstance(entry, tuple) else (entry, b"no")
        if code not in (250, 251):
            raise ef.aiosmtplib.SMTPRecipientRefused(code, text, addr)
        return FakeResponse(code, text)

    async def rset(self):
        return FakeResponse(250)

    async def quit(self):
        return FakeResponse(221)

    async def close(self):
        return None


class Env:
    def __init__(self, reoon="", zerobounce="", smtp_probe=True):
        self.reoon_api_key = reoon
        self.zerobounce_api_key = zerobounce
        self.smtp_probe = smtp_probe


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(os.path.join(tmpdir, "test.db"))
        await sm.init_db()
        yield sm


@pytest.fixture(autouse=True)
def _clean_probe_state():
    """Cached verdicts and the probe budget are process globals; isolate them."""
    ef.reset_probe_state()
    yield
    ef.reset_probe_state()


def _use_smtp(monkeypatch, codes, fail_connect=False):
    def factory(*args, **kwargs):
        return FakeSMTP(codes, fail_connect=fail_connect)

    monkeypatch.setattr(ef.aiosmtplib, "SMTP", factory)

    async def mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", mx)


# ── verdict mapping ──


@pytest.mark.asyncio
async def test_probe_valid_when_control_rejected(monkeypatch):
    _use_smtp(monkeypatch, {"jane.doe@acme.com": 250, "*": 550})
    verdict = await REAL_PROBE("jane.doe@acme.com")
    assert verdict["status"] == "valid"
    assert verdict["catch_all"] is False
    assert verdict["evidence"]["control"] == 550
    assert verdict["evidence"]["candidate"] == 250
    # Control was asked first, so a catch-all can never be called valid.
    sent = FakeSMTP.last_instance.sent
    assert sent[0][0] == "MAIL"
    assert sent[1][1].startswith("zz-no-such-user-")
    assert sent[2][1] == "jane.doe@acme.com"


@pytest.mark.asyncio
async def test_probe_catch_all_is_never_valid(monkeypatch):
    _use_smtp(monkeypatch, {"*": 250})
    verdict = await REAL_PROBE("jane.doe@acme.com")
    assert verdict["status"] == "catch_all"
    assert verdict["catch_all"] is True


@pytest.mark.asyncio
async def test_probe_invalid_when_both_rejected(monkeypatch):
    _use_smtp(monkeypatch, {"*": 550})
    verdict = await REAL_PROBE("nobody@acme.com")
    assert verdict["status"] == "invalid"


@pytest.mark.asyncio
async def test_probe_inconclusive_on_4xx(monkeypatch):
    _use_smtp(monkeypatch, {"*": 450})
    assert await REAL_PROBE("jane@acme.com") is None


# ── policy refusals are about the sender/IP, never a mailbox verdict ──
# Measured 2026-10-31 against mx.zoho.in from a residential IP: the exact
# same 550 5.7.1 dynamic-IP refusal came back for the control, a mailbox
# another verifier had confirmed live, and postmaster@.

ZOHO_POLICY = (550, b"5.7.1 Mail rejected by <Zoho Mail> for policy reasons. "
                    b"We generally do not accept email from dynamic IP's")
GOOGLE_UNKNOWN = (550, b"5.1.1 The email account that you tried to reach "
                       b"does not exist. Please try double-checking the "
                       b"recipient's email address for typos")


@pytest.mark.asyncio
async def test_probe_policy_refusal_is_never_invalid(monkeypatch):
    _use_smtp(monkeypatch, {"*": ZOHO_POLICY})
    verdict = await REAL_PROBE("owner@policylocked.example")
    assert verdict is None  # blanket dynamic-IP block: inconclusive, not invalid


@pytest.mark.asyncio
async def test_probe_policy_refusal_on_candidate_is_inconclusive(monkeypatch):
    # Control refused as user-unknown, candidate refused with policy text:
    # still about the sender — no verdict either way.
    _use_smtp(monkeypatch, {"*": GOOGLE_UNKNOWN,
                            "owner@policylocked.example": ZOHO_POLICY})
    assert await REAL_PROBE("owner@policylocked.example") is None


@pytest.mark.asyncio
async def test_probe_relaying_disallowed_is_inconclusive(monkeypatch):
    _use_smtp(monkeypatch, {"*": (553, b"5.1.2 Relaying disallowed")})
    assert await REAL_PROBE("team@relaylocked.example") is None


@pytest.mark.asyncio
async def test_probe_records_reply_text_in_evidence(monkeypatch):
    _use_smtp(monkeypatch, {"*": GOOGLE_UNKNOWN})
    verdict = await REAL_PROBE("gone@acme.com")
    assert verdict["status"] == "invalid"
    assert "does not exist" in verdict["evidence"]["candidate_text"]
    assert verdict["evidence"]["control"] == 550


@pytest.mark.asyncio
async def test_verify_email_smtp_user_unknown_is_a_verdict(monkeypatch):
    _use_smtp(monkeypatch, {"*": GOOGLE_UNKNOWN})
    assert await ef.verify_email_smtp("gone@acme.com") is False


@pytest.mark.asyncio
async def test_verify_email_smtp_policy_refusal_is_inconclusive(monkeypatch):
    _use_smtp(monkeypatch, {"*": ZOHO_POLICY})
    assert await ef.verify_email_smtp("owner@policylocked.example") is None


@pytest.mark.asyncio
async def test_detect_catch_all_survives_policy_refusals(monkeypatch):
    """A policy-blocked server must not be recorded as 'not catch-all'."""
    _use_smtp(monkeypatch, {"*": ZOHO_POLICY})
    assert await ef.detect_catch_all("policylocked.example") is None


@pytest.mark.asyncio
async def test_probe_inconclusive_when_connection_fails(monkeypatch):
    _use_smtp(monkeypatch, {"*": 250}, fail_connect=True)
    assert await REAL_PROBE("jane@acme.com") is None


@pytest.mark.asyncio
async def test_probe_budget_caps_network_calls(monkeypatch):
    monkeypatch.setattr(ef, "_probe_budget", {"left": 1})
    _use_smtp(monkeypatch, {"*": 550})
    assert await REAL_PROBE("jane@acme.com") is not None
    assert await REAL_PROBE("mark@acme.com") is None      # budget spent -> inconclusive


@pytest.mark.asyncio
async def test_probe_caches_per_address(monkeypatch):
    _use_smtp(monkeypatch, {"*": 550})
    first = await REAL_PROBE("jane@acme.com")
    second = await REAL_PROBE("jane@acme.com")
    assert first == second
    assert len([s for s in FakeSMTP.last_instance.sent if s[0] == "MAIL"]) == 1


# ── wiring into verification + the CLI backfill path ──


@pytest.mark.asyncio
async def test_find_email_turns_guess_into_verified_without_keys(state, monkeypatch):
    """No keys at all: the probe is the last resort and may say 'verified'."""
    async def google_mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", google_mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")

    async def fake_probe(email):
        return {"status": "valid", "catch_all": False,
                "evidence": {"control": 550, "candidate": 250}}

    monkeypatch.setattr(ef, "probe_recipient", fake_probe)

    result = await ef.find_email("Jane", "Doe", "acme.com", env=Env(), state=state)
    assert result.status == "verified"
    assert result.sendable


@pytest.mark.asyncio
async def test_probe_not_used_when_a_key_is_configured(state, monkeypatch):
    """A paid verifier's silence stays 'guess' — no cheating with port 25."""
    async def google_mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", google_mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")
    calls = []

    async def fake_probe(email):
        calls.append(email)
        return {"status": "valid", "catch_all": False, "evidence": {}}

    monkeypatch.setattr(ef, "probe_recipient", fake_probe)

    async def silent_reoon(email, key):
        return None

    monkeypatch.setattr(ef, "verify_reoon", silent_reoon)

    result = await ef.find_email(
        "Jane", "Doe", "acme.com", env=Env(reoon="k"), state=state
    )
    assert result.status == "guess"
    assert calls == []


@pytest.mark.asyncio
async def test_probe_respects_the_env_switch(state, monkeypatch):
    async def google_mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", google_mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")
    monkeypatch.setattr(ef, "probe_recipient", REAL_PROBE)
    _use_smtp(monkeypatch, {"*": 550})

    result = await ef.find_email(
        "Jane", "Doe", "acme.com", env=Env(smtp_probe=False), state=state
    )
    assert result.status == "guess"


@pytest.mark.asyncio
async def test_verify_address_regrades_a_stored_guess(state, monkeypatch):
    async def google_mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", google_mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")
    monkeypatch.setattr(ef, "probe_recipient", REAL_PROBE)
    _use_smtp(monkeypatch, {"jane.doe@acme.com": 250, "*": 550})

    result = await ef.verify_address("jane.doe@acme.com", env=Env(), state=state)
    assert result.status == "verified"
    assert result.mx_type == "google"
    assert ef.last_probe_evidence("jane.doe@acme.com")["status"] == "valid"


# ── an auth/quota-dead key must not block the keyless probe ──
#
# A revoked Reoon key (403) or an exhausted Hunter quota (429) comes back
# every month. Such a key can't decide anything for anyone, so it counts as
# absent and the probe answers — instead of freezing every new address at
# 'guess' until someone fixes the key.


@pytest.mark.parametrize("mx_host", [
    "aspmx.l.google.com",                       # google
    "contoso-com.mail.protection.outlook.com",  # microsoft
    "mx01-0001f001.pphosted.net",               # gateway
])
@pytest.mark.asyncio
async def test_dead_verifier_key_falls_through_to_probe(state, monkeypatch, mx_host):
    async def mx(domain):
        return mx_host

    monkeypatch.setattr(ef, "get_mx_host", mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")
    calls = []

    async def fake_probe(email):
        calls.append(email)
        return {"status": "valid", "catch_all": False,
                "evidence": {"control": 550, "candidate": 250}}

    monkeypatch.setattr(ef, "probe_recipient", fake_probe)

    async def revoked_reoon(email, key):
        raise ef.VerifierUnavailable("Reoon HTTP 403")

    monkeypatch.setattr(ef, "verify_reoon", revoked_reoon)

    result = await ef.find_email(
        "Jane", "Doe", "acme.com", env=Env(reoon="k"), state=state)
    assert result.status == "verified"
    assert calls == ["jane.doe@acme.com"]


@pytest.mark.asyncio
async def test_dead_hunter_quota_falls_through_to_probe(state, monkeypatch):
    async def mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "k")

    async def unreachable(domain, key):
        raise ef.HunterUnavailable("HTTP 429")  # pattern lookup: transient

    monkeypatch.setattr(ef, "_hunter_domain_pattern", unreachable)

    calls = []

    async def fake_probe(email):
        calls.append(email)
        return {"status": "catch_all", "catch_all": True, "evidence": {}}

    monkeypatch.setattr(ef, "probe_recipient", fake_probe)

    async def exhausted_hunter(email, key):
        raise ef.VerifierUnavailable("Hunter HTTP 429")

    monkeypatch.setattr(ef, "verify_hunter", exhausted_hunter)

    result = await ef.find_email("Jane", "Doe", "acme.com", env=Env(), state=state)
    assert result.status == "risky"  # the probe's catch-all verdict, not "guess"
    assert calls == ["jane.doe@acme.com"]


@pytest.mark.asyncio
async def test_live_channel_beats_the_probe_despite_a_dead_one(state, monkeypatch):
    async def mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")
    calls = []

    async def fake_probe(email):
        calls.append(email)
        return {"status": "valid", "catch_all": False, "evidence": {}}

    monkeypatch.setattr(ef, "probe_recipient", fake_probe)

    async def dead_zerobounce(email, key):
        raise ef.VerifierUnavailable("ZeroBounce HTTP 401")

    monkeypatch.setattr(ef, "verify_zerobounce", dead_zerobounce)

    async def live_reoon(email, key):
        return {"status": "safe", "catch_all": False}

    monkeypatch.setattr(ef, "verify_reoon", live_reoon)

    result = await ef.find_email(
        "Jane", "Doe", "acme.com",
        env=Env(reoon="k", zerobounce="k"), state=state)
    assert result.status == "verified"
    assert calls == []


@pytest.mark.asyncio
async def test_live_unknown_channel_still_blocks_the_probe(state, monkeypatch):
    """A reachable verifier that can't decide means 'guess', even when the
    other channel is dead: the probe is a fallback for an unreachable API
    tier, not a cheat around a working one."""
    async def mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")
    calls = []

    async def fake_probe(email):
        calls.append(email)
        return {"status": "valid", "catch_all": False, "evidence": {}}

    monkeypatch.setattr(ef, "probe_recipient", fake_probe)

    async def dead_zerobounce(email, key):
        raise ef.VerifierUnavailable("ZeroBounce HTTP 403")

    monkeypatch.setattr(ef, "verify_zerobounce", dead_zerobounce)

    async def uncertain_reoon(email, key):
        return {"status": "unknown", "catch_all": False}

    monkeypatch.setattr(ef, "verify_reoon", uncertain_reoon)

    result = await ef.find_email(
        "Jane", "Doe", "acme.com",
        env=Env(reoon="k", zerobounce="k"), state=state)
    assert result.status == "guess"
    assert calls == []


@pytest.mark.asyncio
async def test_dead_keys_still_respect_the_env_switch(state, monkeypatch):
    async def mx(domain):
        return "aspmx.l.google.com"

    monkeypatch.setattr(ef, "get_mx_host", mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")
    calls = []

    async def fake_probe(email):
        calls.append(email)
        return {"status": "valid", "catch_all": False, "evidence": {}}

    monkeypatch.setattr(ef, "probe_recipient", fake_probe)

    async def revoked_reoon(email, key):
        raise ef.VerifierUnavailable("Reoon HTTP 403")

    monkeypatch.setattr(ef, "verify_reoon", revoked_reoon)

    result = await ef.find_email(
        "Jane", "Doe", "acme.com", env=Env(reoon="k", smtp_probe=False), state=state)
    assert result.status == "guess"
    assert calls == []


@pytest.mark.asyncio
async def test_small_host_dead_reoon_falls_to_smtp(state, monkeypatch):
    """Small/self-hosted domains keep their order — Reoon, then SMTP — and a
    dead Reoon key just means SMTP answers, as it already would keyless."""
    async def mx(domain):
        return "mail.acme.example"

    monkeypatch.setattr(ef, "get_mx_host", mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "")
    monkeypatch.setattr(
        ef.aiosmtplib, "SMTP",
        lambda *a, **kw: FakeSMTP({"jane.doe@acme.example": 250, "*": 550}))

    async def revoked_reoon(email, key):
        raise ef.VerifierUnavailable("Reoon HTTP 403")

    monkeypatch.setattr(ef, "verify_reoon", revoked_reoon)

    result = await ef.find_email(
        "Jane", "Doe", "acme.example", env=Env(reoon="k"), state=state)
    assert result.status == "verified"  # SMTP accepted, control said not catch-all
    assert result.mx_type == "other"


@pytest.mark.asyncio
async def test_small_host_dead_hunter_and_silent_smtp_stay_guess(state, monkeypatch):
    async def mx(domain):
        return "mail.acme.example"

    monkeypatch.setattr(ef, "get_mx_host", mx)
    monkeypatch.setattr(ef, "_hunter_api_key", lambda: "k")

    async def unreachable(domain, key):
        raise ef.HunterUnavailable("test")

    monkeypatch.setattr(ef, "_hunter_domain_pattern", unreachable)
    _use_smtp(monkeypatch, {}, fail_connect=True)
    monkeypatch.setattr(ef, "get_mx_host", mx)  # _use_smtp pointed at google

    async def exhausted_hunter(email, key):
        raise ef.VerifierUnavailable("Hunter HTTP 429")

    monkeypatch.setattr(ef, "verify_hunter", exhausted_hunter)

    result = await ef.find_email("Jane", "Doe", "acme.example", env=Env(), state=state)
    assert result.status == "guess"  # a dead key never becomes "invalid"


# ── the verifier APIs themselves: dead key vs silent channel ──


class HTTPReply:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


def _http(monkeypatch, status_code, body=None):
    reply = HTTPReply(status_code, body)

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return reply

    monkeypatch.setattr(ef.httpx, "AsyncClient", Client)


@pytest.mark.parametrize("status", [401, 403, 429])
@pytest.mark.asyncio
async def test_verifiers_raise_unavailable_on_auth_quota_death(monkeypatch, status):
    _http(monkeypatch, status)
    with pytest.raises(ef.VerifierUnavailable):
        await ef.verify_reoon("jane.doe@acme.com", "dead-key")
    with pytest.raises(ef.VerifierUnavailable):
        await ef.verify_hunter("jane.doe@acme.com", "dead-key")
    with pytest.raises(ef.VerifierUnavailable):
        await ef.verify_zerobounce("jane.doe@acme.com", "dead-key")


@pytest.mark.asyncio
async def test_zerobounce_reports_a_dead_key_in_band(monkeypatch):
    """ZeroBounce answers HTTP 200 with an 'invalid_api_key' status."""
    _http(monkeypatch, 200, body={"status": "invalid_api_key"})
    with pytest.raises(ef.VerifierUnavailable):
        await ef.verify_zerobounce("jane.doe@acme.com", "dead-key")


@pytest.mark.asyncio
async def test_a_silent_verifier_stays_none_not_unavailable(monkeypatch):
    """5xx is a per-address miss, not a dead channel — a reachable API that
    can't answer must still block the probe (see the wiring tests above)."""
    _http(monkeypatch, 503)
    assert await ef.verify_reoon("jane.doe@acme.com", "k") is None
    assert await ef.verify_hunter("jane.doe@acme.com", "k") is None
    _http(monkeypatch, 200, body={"status": "unknown", "sub_status": ""})
    assert await ef.verify_zerobounce("jane.doe@acme.com", "k") == {
        "status": "unknown", "catch_all": False}


@pytest.mark.asyncio
async def test_state_backfill_query_selects_only_unconfirmed(state):
    from harvey.models.prospect import Prospect

    await state.add_prospect(Prospect(
        first_name="Guess", last_name="Only", email="guess.only@acme.com",
        email_status="guess", company="Acme", status="new",
    ))
    await state.add_prospect(Prospect(
        first_name="Done", last_name="Already", email="done@acme.com",
        email_status="verified", email_verified=True, company="Acme", status="new",
    ))
    rows = await state.get_prospects_by_email_status(("", "guess"))
    assert [p.email for p in rows] == ["guess.only@acme.com"]
    assert await state.get_prospects_by_email_status(("", "guess"), domain="other.com") == []
    assert await state.get_prospects_by_email_status(
        ("", "guess"), domain="acme.com", limit=1)
