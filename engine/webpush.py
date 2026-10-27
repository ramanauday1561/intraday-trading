"""Minimal Web Push sender (RFC 8291 aes128gcm payload encryption + RFC 8292 VAPID), using `cryptography`.

Works with Apple (iOS 16.4+ Home Screen web apps), Google and Mozilla push services."""
import base64
import json
import os
import struct
import time
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _pub_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def generate_vapid_keys():
    """Returns (private_b64u, public_b64u). Public goes in the app; private is a GitHub secret."""
    k = ec.generate_private_key(ec.SECP256R1())
    return b64u(k.private_numbers().private_value.to_bytes(32, "big")), b64u(_pub_bytes(k))


def load_vapid_private(private_b64u):
    return ec.derive_private_key(int.from_bytes(unb64u(private_b64u), "big"), ec.SECP256R1())


def encrypt(payload, p256dh_b64u, auth_b64u, salt=None, as_key=None):
    """RFC 8291: encrypt `payload` (bytes) for one subscription; returns the aes128gcm body."""
    ua_pub = unb64u(p256dh_b64u)
    auth = unb64u(auth_b64u)
    as_key = as_key or ec.generate_private_key(ec.SECP256R1())
    as_pub = _pub_bytes(as_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    ecdh = as_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth, ecdh, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) + 1 + 16 > RECORD_SIZE:
        raise ValueError("payload too large for a single record")
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_pub)) + as_pub + ct


def vapid_header(endpoint, private_key, subject, ttl_s=12 * 3600):
    """RFC 8292: 'vapid t=<ES256 JWT>, k=<public key>' for the push service's origin."""
    u = urlparse(endpoint)
    seg = lambda o: b64u(json.dumps(o, separators=(",", ":")).encode())
    signing_input = seg({"typ": "JWT", "alg": "ES256"}) + "." + seg(
        {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + ttl_s, "sub": subject})
    r, s = decode_dss_signature(private_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={b64u(_pub_bytes(private_key))}"


def send(subscription, message, private_key, subject, ttl=3600, urgency="high"):
    """POST one encrypted push. Returns the HTTP status (201 = accepted; 404/410 = subscription gone)."""
    body = encrypt(json.dumps(message).encode(), subscription["keys"]["p256dh"], subscription["keys"]["auth"])
    headers = {"Authorization": vapid_header(subscription["endpoint"], private_key, subject),
               "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
               "TTL": str(ttl), "Urgency": urgency}
    r = requests.post(subscription["endpoint"], data=body, headers=headers, timeout=20)
    if r.status_code >= 300:
        print(f"push failed {r.status_code}: {r.text[:200]}")
    return r.status_code
