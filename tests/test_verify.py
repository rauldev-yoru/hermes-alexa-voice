"""Alexa request signature verification (security-critical).

A throwaway root -> intermediate -> leaf chain is generated per session and fed
to the real verifier (including the real ``openssl verify``); only the HTTP fetch
of the chain is stubbed.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import shutil
import subprocess

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import main, verify
from conftest import SKILL_ID

CERT_URL = "https://s3.amazonaws.com/echo.api/echo-api-cert-12.pem"
pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="openssl missing")


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject, issuer, key, signer, *, ca: bool, san: str | None = None,
          days: int = 30, start_offset: int = -1):
    now = dt.datetime.now(dt.timezone.utc)
    builder = (x509.CertificateBuilder()
               .subject_name(_name(subject)).issuer_name(_name(issuer))
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now + dt.timedelta(days=start_offset))
               .not_valid_after(now + dt.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                          content_commitment=False, key_encipherment=False,
                          data_encipherment=False, key_agreement=False,
                          encipher_only=False, decipher_only=False), critical=True)
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    return builder.sign(signer, hashes.SHA256())


def _pem(cert) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="session")
def pki(tmp_path_factory):
    def key():
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    root_key, inter_key, leaf_key = key(), key(), key()
    root = _cert("Test Root", "Test Root", root_key, root_key, ca=True, days=365)
    inter = _cert("Test Intermediate", "Test Root", inter_key, root_key, ca=True, days=365)
    leaf = _cert("echo-api.amazon.com", "Test Intermediate", leaf_key, inter_key,
                 ca=False, san="echo-api.amazon.com")
    wrong_san = _cert("evil.example", "Test Intermediate", leaf_key, inter_key,
                      ca=False, san="evil.example")
    expired = _cert("echo-api.amazon.com", "Test Intermediate", leaf_key, inter_key,
                    ca=False, san="echo-api.amazon.com", days=-1, start_offset=-10)
    # Same SAN, but signed by a key that is not in the trust store.
    rogue_key = key()
    rogue_root = _cert("Rogue", "Rogue", rogue_key, rogue_key, ca=True)
    rogue_leaf = _cert("echo-api.amazon.com", "Rogue", leaf_key, rogue_key,
                       ca=False, san="echo-api.amazon.com")
    bundle = tmp_path_factory.mktemp("ca") / "root.pem"
    bundle.write_text(_pem(root))
    return {
        "bundle": str(bundle),
        "key": leaf_key,
        "chain": _pem(leaf) + _pem(inter),
        "wrong_san": _pem(wrong_san) + _pem(inter),
        "expired": _pem(expired) + _pem(inter),
        "rogue": _pem(rogue_leaf) + _pem(rogue_root),
    }


@pytest.fixture
def served(monkeypatch, pki):
    """Stub the chain download; returns a setter for what the 'S3 bucket' serves."""
    monkeypatch.setattr(verify, "CA_BUNDLE", pki["bundle"])
    state = {"pem": pki["chain"], "urls": []}

    async def fake_fetch(url):
        state["urls"].append(url)
        return state["pem"]

    monkeypatch.setattr(verify, "_fetch_chain", fake_fetch)
    return state


def _payload(ts: dt.datetime | None = None, app_id: str = SKILL_ID) -> dict:
    ts = ts or dt.datetime.now(dt.timezone.utc)
    return {
        "version": "1.0",
        "session": {"application": {"applicationId": app_id}},
        "context": {"System": {"application": {"applicationId": app_id},
                               "device": {"deviceId": "dev-1"}}},
        "request": {"type": "SessionEndedRequest",
                    "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ")},
    }


def _signed(pki, payload: dict) -> tuple[dict, bytes]:
    body = json.dumps(payload).encode()
    sig = pki["key"].sign(body, padding.PKCS1v15(), hashes.SHA256())
    headers = {"signaturecertchainurl": CERT_URL,
               "signature-256": base64.b64encode(sig).decode()}
    return headers, body


def _verify(headers, body, payload):
    asyncio.run(verify.verify_request(headers, body, payload))


# --------------------------------------------------------------- cert URL ---
@pytest.mark.parametrize("url", [
    "https://s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com:443/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com/echo.api/../echo.api/echo-api-cert.pem",
    "HTTPS://s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "https://S3.AMAZONAWS.COM/echo.api/echo-api-cert.pem",
])
def test_cert_url_valid(url):
    assert verify._validate_cert_url(url) == \
        "https://s3.amazonaws.com/echo.api/echo-api-cert.pem"


@pytest.mark.parametrize("url", [
    "http://s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "https://notamazon.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com.evil.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com/EcHo.aPi/echo-api-cert.pem",       # path is case-sensitive
    "https://s3.amazonaws.com/invalid.path/echo-api-cert.pem",
    "https://s3.amazonaws.com:563/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com/echo.api/../attacker-bucket/cert.pem",
    "https://s3.amazonaws.com/echo.api/../../echo.api/x/../../evil.pem",
    "https:///echo.api/echo-api-cert.pem",                       # no host
    "https://s3.amazonaws.com:notaport/echo.api/x.pem",
    "",
])
def test_cert_url_invalid(url):
    with pytest.raises(verify.SignatureError):
        verify._validate_cert_url(url)


# ------------------------------------------------------------ full verify ---
def test_valid_request_passes(pki, served):
    payload = _payload()
    headers, body = _signed(pki, payload)
    _verify(headers, body, payload)


def test_chain_is_fetched_from_the_normalized_url(pki, served):
    payload = _payload()
    headers, body = _signed(pki, payload)
    headers["signaturecertchainurl"] = "https://S3.amazonaws.com/echo.api/../echo.api/c.pem"
    _verify(headers, body, payload)
    assert served["urls"] == ["https://s3.amazonaws.com/echo.api/c.pem"]


def test_tampered_body_fails(pki, served):
    payload = _payload()
    headers, body = _signed(pki, payload)
    with pytest.raises(verify.SignatureError, match="signature"):
        _verify(headers, body.replace(b"SessionEnded", b"Launch_Xxxxx"), payload)


@pytest.mark.parametrize("which,match", [
    ("wrong_san", "SAN"),
    ("expired", "expired"),
    ("rogue", "chain verification failed"),
])
def test_bad_certificates_fail(pki, served, which, match):
    served["pem"] = pki[which]
    payload = _payload()
    headers, body = _signed(pki, payload)
    with pytest.raises(verify.SignatureError, match=match):
        _verify(headers, body, payload)


def test_single_cert_is_not_a_chain(pki, served):
    served["pem"] = pki["chain"].split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"
    payload = _payload()
    headers, body = _signed(pki, payload)
    with pytest.raises(verify.SignatureError, match="leaf \\+ intermediate"):
        _verify(headers, body, payload)


def test_missing_headers_fail(pki, served):
    payload = _payload()
    with pytest.raises(verify.SignatureError, match="missing"):
        _verify({}, b"{}", payload)


@pytest.mark.parametrize("skew", [-160, 160, 3600])  # timestamps are whole seconds
def test_timestamp_outside_tolerance_fails(pki, served, skew):
    payload = _payload(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=skew))
    headers, body = _signed(pki, payload)
    with pytest.raises(verify.SignatureError, match="off"):
        _verify(headers, body, payload)


def test_timestamp_within_tolerance_passes(pki, served):
    payload = _payload(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=120))
    headers, body = _signed(pki, payload)
    _verify(headers, body, payload)


@pytest.mark.parametrize("raw", ["yesterday", "2026-13-45T99:00:00Z", 12345,
                                 "2026-10-22T10:00:00"])
def test_malformed_timestamp_is_a_signature_error(raw):
    # Used to escape as ValueError/TypeError -> HTTP 500 instead of 400.
    with pytest.raises(verify.SignatureError):
        verify._check_timestamp({"request": {"timestamp": raw}})


def test_openssl_timeout_is_a_signature_error(pki, served, monkeypatch):
    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired("openssl", 20)

    monkeypatch.setattr(verify.subprocess, "run", hang)
    payload = _payload()
    headers, body = _signed(pki, payload)
    with pytest.raises(verify.SignatureError, match="TimeoutExpired"):
        _verify(headers, body, payload)


def test_openssl_runs_off_the_event_loop(pki, served, monkeypatch):
    import threading

    seen = {}
    real = verify._verify_chain

    def spy(blocks):
        seen["thread"] = threading.current_thread()
        return real(blocks)

    monkeypatch.setattr(verify, "_verify_chain", spy)
    payload = _payload()
    headers, body = _signed(pki, payload)
    _verify(headers, body, payload)
    assert seen["thread"] is not threading.main_thread()


# -------------------------------------------------------- applicationId -----
def test_application_id_must_match():
    verify.check_application(_payload(), SKILL_ID)
    verify.check_application(_payload(app_id="other"), None)  # not configured
    with pytest.raises(verify.SignatureError, match="different skill"):
        verify.check_application(_payload(app_id="amzn1.ask.skill.someone-else"), SKILL_ID)
    with pytest.raises(verify.SignatureError, match="no applicationId"):
        verify.check_application({"request": {}}, SKILL_ID)


# ------------------------------------------------------------- endpoint -----
@pytest.fixture
def skill_client():
    return TestClient(main.app)


@pytest.mark.parametrize("path", ["/", "/alexa"])
def test_endpoint_rejects_unsigned(skill_client, path):
    assert skill_client.post(path, json=_payload()).status_code == 400


def test_endpoint_accepts_a_signed_request(skill_client, pki, served):
    headers, body = _signed(pki, _payload())
    resp = skill_client.post("/alexa", content=body, headers=headers)
    assert resp.status_code == 200, resp.text


def test_endpoint_rejects_another_skills_signed_request(skill_client, pki, served):
    # Genuinely signed by "Amazon", but for a skill somebody else owns.
    headers, body = _signed(pki, _payload(app_id="amzn1.ask.skill.someone-else"))
    resp = skill_client.post("/alexa", content=body, headers=headers)
    assert resp.status_code == 400


def test_endpoint_rejects_non_object_json(skill_client):
    assert skill_client.post("/alexa", content=b"[1]").status_code == 400


def test_endpoint_malformed_timestamp_is_400_not_500(skill_client, pki, served):
    payload = _payload()
    payload["request"]["timestamp"] = "not-a-date"
    headers, body = _signed(pki, payload)
    assert skill_client.post("/alexa", content=body, headers=headers).status_code == 400
