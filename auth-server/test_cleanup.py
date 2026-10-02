import time
import unittest
from unittest.mock import patch

import admin
from store import Store, CONSUMED


class CleanupTest(unittest.TestCase):
    def test_prune_removes_expired_records_without_new_login(self):
        store = Store(10, 60, 10)
        with patch('time.time', return_value=100):
            s = store.create()
            store.approve(s.ticket, {'sub': 'alice'})
            store.issue_token({'sub': 'alice'}, 'client')
            sid, _ = admin.create_session()
        with patch('time.time', return_value=100 + admin.SESSION_MAX_AGE + 1):
            store.prune()
            admin.prune_sessions()
        self.assertEqual(store.stats(), {'sessions': 0, 'codes': 0, 'tokens': 0})
        self.assertNotIn(sid, admin._sessions)

    def test_eviction_also_removes_authorization_code(self):
        store = Store(180, 60, 300, max_sessions=1)
        s = store.create()
        store.approve(s.ticket, {'sub': 'alice'})
        code = s.code
        store.create()
        self.assertEqual(store.stats()['codes'], 0)
        self.assertIsNone(store.consume_code(code)[0])

    def test_expired_ticket_cannot_redeem_code_even_before_gc(self):
        store = Store(10, 60, 300)
        s = store.create()
        store.approve(s.ticket, {'sub': 'alice'})
        s.expires = time.time() - 1
        self.assertIsNone(store.consume_code(s.code)[0])

    def test_get_expired_ticket_removes_both_indexes(self):
        store = Store(10, 60, 300)
        s = store.create()
        store.approve(s.ticket, {'sub': 'alice'})
        s.expires = time.time() - 1
        self.assertIsNone(store.get(s.ticket))
        self.assertEqual(store.stats()['codes'], 0)

    def test_code_expiry_releases_identity_but_keeps_live_token(self):
        store = Store(180, 60, 300)
        s = store.create()
        store.approve(s.ticket, {'sub': 'alice'})
        token = store.issue_token({'sub': 'alice'}, 'client')
        s.code_expires = time.time() - 1
        store.prune()
        self.assertEqual(s.state, CONSUMED)
        self.assertEqual(s.code, '')
        self.assertIsNone(s.identity)
        self.assertEqual(store.token_identity(token), {'sub': 'alice'})
        store.clear()
        self.assertEqual(store.stats(), {'sessions': 0, 'codes': 0, 'tokens': 0})
