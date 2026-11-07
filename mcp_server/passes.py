"""The Durian OS pass at this server's door, checked on every request to /mcp before any tool runs.

Durian OS's AI front door sends `Authorization: Bearer <pass>` on every call: an EdDSA JWT for one person and one app
(typ "pat", aud "pat:orm", iss Durian OS, sub the person's Durian OS id, a one-time jti, 60 seconds). This server
checks what it can on its own, with no call to ORM: the signature against Durian OS's public keys, iss,
typ, aud, the clock window (at most 60 seconds of life, 30 seconds of skew, as Rails allows), no View as
claims, and that sub and jti are there. A pass that fails is answered 401 by FastMCP's bearer check before any tool
runs.

The keys: OS_JWKS_JSON (a pinned key set, the one Rails holds) wins when set; otherwise OS_JWKS_URL (default
https://durianos.in/.well-known/jwks.json), cached for 5 minutes, fetched again for an unknown kid at most once a
minute. A pinned set with no usable key refuses every pass.

The rest is ORM's (chatwoot/custom/app/services/durian_os/ai/): it gets the same pass, unchanged, on the one API call a
request makes, records its jti once, finds the person by sub and judges the route. A pass is single use, so a request
makes at most ONE API call (backend.py).

Ported from Content Studio's mcp_server/passes.py.
"""
import json
import logging
import os
import time

import httpx
import jwt
from fastmcp.server.auth import AccessToken, TokenVerifier

log = logging.getLogger('orm.mcp')

AUDIENCE = 'pat:orm'
DEFAULT_ISSUER = 'https://durianos.in'
DEFAULT_JWKS_URL = 'https://durianos.in/.well-known/jwks.json'
# Seconds of clock difference allowed either side, and the longest life a pass may have: Rails' own rule
# for Durian OS's tokens (DurianOs::Ai::Pass).
LEEWAY = 30
MAX_LIFE = 60
# A pass is always the person's own: View as, or acting for someone else, is refused.
NOT_ONES_OWN = ('impersonated_by', 'imp', 'imp_exp', 'act')
KEYS_MAX_AGE = 300
KEYS_MIN_REFETCH = 60


def parse_keys(document):
    """Durian OS's Ed25519 public keys by kid, from a JWKS document. Anything else in it is ignored."""
    keys = {}
    for jwk in document.get('keys', []) if isinstance(document, dict) else []:
        if not isinstance(jwk, dict) or jwk.get('kty') != 'OKP' or jwk.get('crv') != 'Ed25519' or not jwk.get('kid'):
            continue
        try:
            keys[str(jwk['kid'])] = jwt.PyJWK(jwk, algorithm='EdDSA').key
        except (jwt.PyJWTError, ValueError, TypeError):
            continue
    return keys


async def _fetch_jwks(url):
    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
        response = await client.get(url, headers={'accept': 'application/json'})
        response.raise_for_status()
        return response.json()


class KeySet:
    """Durian OS's public keys: a pinned set, or the published one fetched and cached."""

    def __init__(self, pinned=None, url=None, fetch=None):
        self.pinned, self.url, self.fetch = pinned, url, fetch or _fetch_jwks
        self._keys, self._loaded, self._tried = {}, float('-inf'), float('-inf')

    async def get(self, kid):
        if self.pinned is not None:
            return self.pinned.get(kid)
        if not self.url:
            return None
        now = time.monotonic()
        if (now - self._loaded >= KEYS_MAX_AGE or kid not in self._keys) and now - self._tried >= KEYS_MIN_REFETCH:
            self._tried = now
            try:
                document = await self.fetch(self.url)
            except Exception as exc:  # noqa: BLE001 - any failure leaves the keys as they were
                log.warning('Durian OS key set not loaded (%s): %s', self.url, type(exc).__name__)
            else:
                self._keys, self._loaded = parse_keys(document), now
        return self._keys.get(kid)


def client_of(claims):
    """The AI's name a pass may carry (`client`, at most 60 characters), else "AI"."""
    value = claims.get('client')
    return ' '.join(value.split()) if isinstance(value, str) and 0 < len(value.strip()) <= 60 else 'AI'


class PassVerifier(TokenVerifier):
    """FastMCP's bearer check for /mcp: a Durian OS pass for ORM, and nothing else. Refusals are logged
    with their reason only, never the pass."""

    def __init__(self, keys, issuer=DEFAULT_ISSUER):
        super().__init__()
        self.keys = keys if isinstance(keys, KeySet) else KeySet(pinned=dict(keys or {}))
        self.issuer = issuer

    async def verify_token(self, token):
        claims, reason = await self.check(token)
        if claims is None:
            log.info('pass refused: %s', reason)
            return None
        return AccessToken(token=token, client_id=client_of(claims), scopes=[], expires_at=int(claims['exp']) + LEEWAY,
                           subject=claims['sub'], claims=claims)

    async def check(self, token):
        """(claims, 'ok') for a good pass, else (None, why)."""
        try:
            header = jwt.get_unverified_header(token)
        except (jwt.PyJWTError, ValueError, TypeError):
            return None, 'malformed'
        if header.get('alg') != 'EdDSA':
            return None, 'algorithm'
        typ = header.get('typ')
        if typ is not None and (not isinstance(typ, str) or typ.lower() not in ('pat', 'jwt')):
            return None, 'wrong_type'
        kid = header.get('kid')
        key = await self.keys.get(kid) if isinstance(kid, str) and kid else None
        if key is None:
            return None, 'unknown_key'
        try:
            claims = jwt.decode(token, key, algorithms=['EdDSA'], issuer=self.issuer, audience=AUDIENCE, leeway=LEEWAY,
                                options={'require': ['exp', 'iat', 'iss', 'aud', 'sub', 'jti']})
        except jwt.ExpiredSignatureError:
            return None, 'expired'
        except jwt.InvalidAudienceError:
            return None, 'wrong_audience'
        except jwt.InvalidIssuerError:
            return None, 'wrong_issuer'
        except jwt.MissingRequiredClaimError:
            return None, 'missing_claim'
        except jwt.InvalidSignatureError:
            return None, 'bad_signature'
        except (jwt.PyJWTError, ValueError, TypeError):
            return None, 'invalid'
        if claims.get('typ') != 'pat':
            return None, 'wrong_type'
        # One app per pass: aud is the one string, never a list naming several.
        if claims.get('aud') != AUDIENCE:
            return None, 'wrong_audience'
        iat, exp = claims.get('iat'), claims.get('exp')
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (iat, exp)) or not 0 < exp - iat <= MAX_LIFE:
            return None, 'too_long'
        if iat - LEEWAY > time.time():
            return None, 'not_yet'
        if any(key in claims for key in NOT_ONES_OWN):
            return None, 'view_as'
        if not all(isinstance(claims.get(k), str) and claims[k] for k in ('sub', 'jti')):
            return None, 'missing_claim'
        return claims, 'ok'


def from_env(environ=None):
    """The pass check the environment gives: Durian OS's keys (OS_JWKS_JSON pinned, else OS_JWKS_URL) and its issuer
    (OS_ISSUER, default https://durianos.in), the same settings Rails reads. A pinned set with no usable key
    refuses every pass, never lets one through."""
    env = os.environ if environ is None else environ
    raw = (env.get('OS_JWKS_JSON') or '').strip()
    if raw:
        try:
            pinned = parse_keys(json.loads(raw))
        except ValueError:
            pinned = {}
        if not pinned:
            log.warning('OS_JWKS_JSON holds no Ed25519 key: every request is refused')
        keys = KeySet(pinned=pinned)
    else:
        keys = KeySet(url=(env.get('OS_JWKS_URL') or '').strip() or DEFAULT_JWKS_URL)
    return PassVerifier(keys, (env.get('OS_ISSUER') or '').strip() or DEFAULT_ISSUER)


def bearer_token(header):
    """The token of an `Authorization: Bearer <token>` header, else ''."""
    scheme, _, token = (header or '').strip().partition(' ')
    return token.strip() if scheme.lower() == 'bearer' else ''
