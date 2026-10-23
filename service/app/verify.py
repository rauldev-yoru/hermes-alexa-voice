"""Verify that an incoming request really came from Amazon Alexa.

Spec: https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-a-web-service.html

Checks, in order:
  1. `SignatureCertChainUrl` has the exact shape Amazon uses (https, s3.amazonaws.com,
     path under /echo.api/, port 443) and points at a *PEM chain* (not a bare cert).
  2. The leaf certificate is currently valid and lists `echo-api.amazon.com` in its SANs.
  3. The chain validates against the system CA bundle (via `openssl verify`).
  4. The `Signature-256` header is a valid RSA-SHA256 signature of the raw body.
  5. The request timestamp is within 150 seconds of now (replay protection).

Anything that fails raises `SignatureError` and the caller must answer 400.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtensionOID, NameOID

from .config import CA_BUNDLE, CACHE_DIR

MAX_TIMESTAMP_SKEW = 150  # seconds, Amazon's documented tolerance
_CERT_CACHE: dict[str, tuple[float, str]] = {}
CERT_TTL = 6 * 3600

class SignatureError(Exception):
    """Raised when a request cannot be proven to come from Alexa."""


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 §5.2.4, so ``/echo.api/../x`` is judged as ``/x``."""
    out: list[str] = []
    for seg in path.split("/"):
        if seg == "..":
            if len(out) > 1:
                out.pop()
        elif seg != ".":
            out.append(seg)
    return "/".join(out)


def _validate_cert_url(url: str) -> str:
    """Check SignatureCertChainUrl and return the normalized URL to fetch."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise SignatureError("cert url: malformed") from exc
    if parts.scheme.lower() != "https":
        raise SignatureError("cert url: scheme must be https")
    if (parts.hostname or "").lower() != "s3.amazonaws.com":
        raise SignatureError("cert url: host must be s3.amazonaws.com")
    path = _remove_dot_segments(parts.path)
    # The spec makes the path case-sensitive: /EcHo.aPi/ is invalid.
    if not path.startswith("/echo.api/"):
        raise SignatureError("cert url: path must be under /echo.api/")
    if port not in (None, 443):
        raise SignatureError("cert url: port must be 443")
    return f"https://s3.amazonaws.com{path}"


async def _fetch_chain(url: str) -> str:
    """Download and cache the PEM chain behind SignatureCertChainUrl."""
    now = time.time()
    hit = _CERT_CACHE.get(url)
    if hit and now - hit[0] < CERT_TTL:
        return hit[1]
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise SignatureError(f"cert fetch failed: HTTP {resp.status_code}")
    pem = resp.text
    if "BEGIN CERTIFICATE" not in pem:
        raise SignatureError("cert fetch: response is not PEM")
    _CERT_CACHE[url] = (now, pem)
    return pem


def _split_chain(pem: str) -> list[str]:
    blocks = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.DOTALL
    )
    if len(blocks) < 2:
        # Amazon sends leaf + intermediate. One block usually means a bare cert
        # or a truncated response; either way we cannot validate the chain.
        raise SignatureError("cert chain: expected leaf + intermediate")
    return blocks


def _check_leaf(cert: x509.Certificate) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
        raise SignatureError("leaf cert is expired or not yet valid")
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    except x509.ExtensionNotFound as exc:
        raise SignatureError("leaf cert has no SAN") from exc
    names = {str(name.value).lower() for name in san.value}
    if "echo-api.amazon.com" not in names:
        raise SignatureError("leaf cert SAN does not include echo-api.amazon.com")


def _verify_chain(blocks: list[str]) -> None:
    """Validate leaf -> intermediate -> system root using openssl."""
    openssl = shutil.which("openssl")
    if not openssl:
        raise SignatureError("openssl binary not found (needed to validate the cert chain)")
    if not CA_BUNDLE:
        raise SignatureError("no CA bundle found; set SSL_CERT_FILE or CA_BUNDLE")
    with tempfile.TemporaryDirectory(dir=CACHE_DIR) as tmp:
        leaf = Path(tmp) / "leaf.pem"
        leaf.write_text(blocks[0])
        untrusted = Path(tmp) / "chain.pem"
        untrusted.write_text("\n".join(blocks[1:]))
        cmd = [openssl, "verify", "-CAfile", CA_BUNDLE]
        if untrusted.stat().st_size:
            cmd += ["-untrusted", str(untrusted)]
        cmd.append(str(leaf))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except (subprocess.SubprocessError, OSError) as exc:
            raise SignatureError(f"chain verification failed: {type(exc).__name__}") from exc
        if proc.returncode != 0:
            raise SignatureError(f"chain verification failed: {proc.stderr.strip()[:300]}")


def _verify_signature(blocks: list[str], signature_b64: str, body: bytes) -> None:
    try:
        sig = base64.b64decode(signature_b64)
    except Exception as exc:  # noqa: BLE001
        raise SignatureError("Signature-256 is not valid base64") from exc
    cert = x509.load_pem_x509_certificate(blocks[0].encode())
    pub = cert.public_key()
    if not isinstance(pub, rsa.RSAPublicKey):
        raise SignatureError("leaf cert key is not RSA")
    try:
        pub.verify(sig, body, padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # noqa: BLE001
        raise SignatureError("body signature does not match") from exc


def _check_timestamp(payload: dict) -> None:
    raw = (payload.get("request") or {}).get("timestamp")
    if not raw or not isinstance(raw, str):
        raise SignatureError("request has no timestamp")
    try:
        ts = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SignatureError("request timestamp is malformed") from exc
    if ts.tzinfo is None:
        raise SignatureError("request timestamp has no timezone")
    skew = abs((dt.datetime.now(dt.timezone.utc) - ts).total_seconds())
    if skew > MAX_TIMESTAMP_SKEW:
        raise SignatureError(f"request timestamp is {int(skew)}s off")


async def verify_request(headers: dict, body: bytes, payload: dict) -> None:
    """Full Alexa request verification. Raises SignatureError on any failure."""
    cert_url = headers.get("signaturecertchainurl") or headers.get("SignatureCertChainUrl")
    signature = headers.get("signature-256") or headers.get("Signature-256")
    if not cert_url or not signature:
        raise SignatureError("missing SignatureCertChainUrl or Signature-256 header")

    pem = await _fetch_chain(_validate_cert_url(cert_url))
    blocks = _split_chain(pem)
    try:
        cert = x509.load_pem_x509_certificate(blocks[0].encode())
    except ValueError as exc:
        raise SignatureError("leaf cert is not valid PEM") from exc
    _check_leaf(cert)
    # openssl is a blocking subprocess: keep it off the event loop.
    await asyncio.to_thread(_verify_chain, blocks)
    _verify_signature(blocks, signature, body)
    _check_timestamp(payload)


def check_application(payload: dict, skill_id: str | None) -> None:
    """Reject signed requests that belong to somebody else's skill.

    A valid Amazon signature only proves the request came from Alexa — any
    developer can point their own skill at this endpoint. With ``skill_id``
    configured, only that skill may reach the handlers (and the agent).
    """
    if not skill_id:
        return
    for holder in ((payload.get("context") or {}).get("System") or {},
                   payload.get("session") or {}):
        app_id = ((holder.get("application") or {}).get("applicationId"))
        if app_id:
            if app_id != skill_id:
                raise SignatureError("request is for a different skill")
            return
    raise SignatureError("request has no applicationId")


def subject_summary(pem: str) -> str:
    """Debug helper: CN of the leaf certificate."""
    cert = x509.load_pem_x509_certificate(
        re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.DOTALL)[0].encode()
    )
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return cn[0].value if cn else "?"


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()[:16]
