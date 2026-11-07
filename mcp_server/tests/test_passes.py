"""The pass at the door (mcp_server/passes.py): a good pass, and every way one is refused, with no call to Content
Orm. And where the keys come from: pinned, or Durian OS's published set, cached."""
import asyncio
import json
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcp_server import passes
from mcp_server.tests.support import ISSUER, PERSON, Passes


class PassTests(unittest.TestCase):
    def setUp(self):
        self.minted = Passes()
        self.verifier = self.minted.verifier()

    def check(self, token):
        return asyncio.run(self.verifier.check(token))

    def test_a_good_pass_is_taken_with_its_person_and_client(self):
        token = self.minted.mint()
        claims, reason = self.check(token)
        self.assertEqual((reason, claims['sub'], claims['aud']), ('ok', PERSON, 'pat:orm'))
        checked = asyncio.run(self.verifier.verify_token(token))
        self.assertEqual((checked.token, checked.client_id, checked.subject), (token, 'Claude', PERSON))
        self.assertEqual(checked.expires_at, claims['exp'] + passes.LEEWAY)
        self.assertEqual(asyncio.run(self.verifier.verify_token(self.minted.mint(client=None))).client_id, 'AI')
        now = int(time.time())
        self.assertEqual(self.check(self.minted.mint(iat=now, exp=now + 60))[1], 'ok')
        self.assertEqual(self.check(self.minted.mint(header={'typ': 'pat'}))[1], 'ok')

    def test_every_bad_pass_is_refused(self):
        now = int(time.time())
        cases = {
            'not a jwt': ('not-a-jwt', 'malformed'),
            'another key': (self.minted.mint(key=Ed25519PrivateKey.generate()), 'bad_signature'),
            'unknown kid': (self.minted.mint(kid='not-durian-os'), 'unknown_key'),
            'hs256': (self.minted.mint(key='a-shared-secret-of-thirty-two-bytes!', algorithm='HS256'), 'algorithm'),
            'header typ handoff': (self.minted.mint(header={'typ': 'handoff'}), 'wrong_type'),
            'aud orm': (self.minted.mint(aud='orm'), 'wrong_audience'),
            'aud another app': (self.minted.mint(aud='pat:iof'), 'wrong_audience'),
            'aud a list': (self.minted.mint(aud=['pat:orm', 'pat:stock']), 'wrong_audience'),
            'no aud': (self.minted.mint(aud=None), 'missing_claim'),
            'typ handoff': (self.minted.mint(typ='handoff'), 'wrong_type'),
            'no typ': (self.minted.mint(typ=None), 'wrong_type'),
            'another issuer': (self.minted.mint(iss='https://elsewhere.test'), 'wrong_issuer'),
            'expired': (self.minted.mint(iat=now - 200, exp=now - 120), 'expired'),
            'lives 61 s': (self.minted.mint(iat=now, exp=now + 61), 'too_long'),
            'lives a day': (self.minted.mint(iat=now, exp=now + 86400), 'too_long'),
            'from the future': (self.minted.mint(iat=now + 100, exp=now + 160), 'invalid'),
            'no exp': (self.minted.mint(exp=None), 'missing_claim'),
            'no iat': (self.minted.mint(iat=None), 'missing_claim'),
            'view as': (self.minted.mint(impersonated_by={'sub': 'x', 'email': 'x@orm.test'}), 'view_as'),
            'imp': (self.minted.mint(imp='va-1'), 'view_as'), 'imp_exp': (self.minted.mint(imp_exp=now + 600), 'view_as'),
            'act': (self.minted.mint(act='u-tara'), 'view_as'),
            'no sub': (self.minted.mint(sub=None), 'missing_claim'), 'empty sub': (self.minted.mint(sub=''), 'missing_claim'),
            'no jti': (self.minted.mint(jti=None), 'missing_claim'),
        }
        for name, (token, reason) in cases.items():
            claims, why = self.check(token)
            self.assertIsNone(claims, name)
            if name != 'from the future':  # PyJWT or the window check, either refuses it
                self.assertEqual((name, why), (name, reason))
            self.assertIsNone(asyncio.run(self.verifier.verify_token(token)), name)

    def test_with_no_usable_pinned_key_every_pass_is_refused(self):
        for raw in ('not json', json.dumps({'keys': [{'kty': 'RSA', 'kid': 'x'}]}), json.dumps({'keys': []})):
            verifier = passes.from_env({'OS_JWKS_JSON': raw})
            self.assertEqual(verifier.keys.pinned, {})
            self.assertEqual(asyncio.run(verifier.check(self.minted.mint())), (None, 'unknown_key'))

    def test_the_environment_names_the_keys_and_the_issuer(self):
        verifier = passes.from_env({'OS_JWKS_JSON': self.minted.jwks(), 'OS_ISSUER': ''})
        self.assertEqual((list(verifier.keys.pinned), verifier.issuer), ([self.minted.kid], ISSUER))
        self.assertEqual(asyncio.run(verifier.check(self.minted.mint()))[1], 'ok')
        other = passes.from_env({'OS_JWKS_JSON': self.minted.jwks(), 'OS_ISSUER': 'https://os.test'})
        self.assertEqual(asyncio.run(other.check(self.minted.mint()))[1], 'wrong_issuer')
        self.assertEqual(asyncio.run(other.check(self.minted.mint(iss='https://os.test')))[1], 'ok')
        # No pinned set: the published one, from OS_JWKS_URL or Durian OS's.
        self.assertEqual(passes.from_env({}).keys.url, passes.DEFAULT_JWKS_URL)
        self.assertEqual(passes.from_env({'OS_JWKS_URL': 'https://os.test/jwks'}).keys.url, 'https://os.test/jwks')

    def test_the_published_key_set_is_fetched_once_and_cached(self):
        fetched = []

        async def fetch(url):
            fetched.append(url)
            return json.loads(self.minted.jwks())

        verifier = passes.PassVerifier(passes.KeySet(url='https://os.test/jwks', fetch=fetch), ISSUER)

        async def scenario():
            return [(await verifier.check(self.minted.mint()))[1] for _ in range(3)] + [
                (await verifier.check(self.minted.mint(kid='another-kid')))[1]]

        self.assertEqual(asyncio.run(scenario()), ['ok', 'ok', 'ok', 'unknown_key'])
        # An unknown kid does not fetch again within the minute.
        self.assertEqual(fetched, ['https://os.test/jwks'])

    def test_a_key_set_that_cannot_be_fetched_refuses_and_says_why(self):
        async def fetch(url):
            raise OSError('no route')

        verifier = passes.PassVerifier(passes.KeySet(url='https://os.test/jwks', fetch=fetch), ISSUER)
        self.assertEqual(asyncio.run(verifier.check(self.minted.mint())), (None, 'unknown_key'))

    def test_bearer_token(self):
        self.assertEqual([passes.bearer_token(h) for h in ('Bearer abc', 'bearer abc ', 'Basic abc', 'Bearer', '', None)],
                         ['abc', 'abc', '', '', '', ''])
