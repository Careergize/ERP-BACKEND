from decimal import Decimal
from unittest import TestCase

from .order_control_foundation import (
    OrderControlError, capture_snapshot, require_order_revision,
    require_version, verify_snapshot,
)


class OrderControlFoundationTests(TestCase):
    def rejects(self, code, function, *args, **kwargs):
        with self.assertRaises(OrderControlError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_fixed_snapshot_vector(self):
        snapshot = capture_snapshot({'z': ['1.20', True, None], 'a': 'é'})
        self.assertEqual(snapshot.canonical, b'{"a":"\\u00e9","z":["1.20",true,null]}')
        self.assertEqual(capture_snapshot({}).digest,
                         '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a')

    def test_source_and_decoded_mutation_do_not_change_snapshot(self):
        source = {'items': [{'quantity': '1.20'}]}
        snapshot = capture_snapshot(source)
        source['items'][0]['quantity'] = '9'
        snapshot.decode()['items'].clear()
        self.assertEqual(snapshot.decode(), {'items': [{'quantity': '1.20'}]})

    def test_order_whitespace_units_and_numeric_types_are_significant(self):
        pairs = [(['a', 'b'], ['b', 'a']), (' kg', 'kg'), ('kg', 'KG'),
                 ('1.20', '1.2'), (1, '1'), (1, 1.0), ('é', 'e\u0301')]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(capture_snapshot(left).digest, capture_snapshot(right).digest)
        self.assertEqual(capture_snapshot({'a': 1, 'b': 2}), capture_snapshot({'b': 2, 'a': 1}))

    def test_unsupported_values_never_coerce(self):
        for value in (float('nan'), float('inf'), -float('inf'), Decimal('1.20'),
                      {1: 'a'}, (1,), {1}, object(), {'nested': [Decimal('2')]}):
            with self.subTest(value=type(value)):
                self.rejects('VALIDATION_ERROR', capture_snapshot, value)

    def test_hash_verification_rejects_tampering_and_malformed_hashes(self):
        content = {'items': [1, 2]}
        snapshot = capture_snapshot(content)
        self.assertEqual(verify_snapshot(content, snapshot.digest), snapshot)
        for digest in (None, '', 'A' * 64, 'a' * 63, '0' * 64):
            self.rejects('REVISION_CONFLICT', verify_snapshot, content, digest)
        self.rejects('REVISION_CONFLICT', verify_snapshot, {'items': [2, 1]}, snapshot.digest)

    def test_exact_version_and_create_context(self):
        require_version(1, 1)
        require_version(2**63 - 1, 2**63 - 1)
        require_version(0, 0, creating=True)
        self.rejects('VERSION_CONFLICT', require_version, 1, 2)
        self.rejects('VALIDATION_ERROR', require_version, 1, 1, creating=True)

    def test_invalid_and_legacy_versions_never_default(self):
        for value in (True, False, '1', 1.0, -1, 0, 2**63, None):
            self.rejects('VALIDATION_ERROR', require_version, value, 1)
        for value in (True, '1', 1.0, -1, 0, 2**63):
            self.rejects('VALIDATION_ERROR', require_version, 1, value)
        self.rejects('LEGACY_REVIEW_REQUIRED', require_version, 1, None)

    def test_revision_parent_identity_is_exact(self):
        require_order_revision(12, 12)
        self.rejects('REVISION_CONFLICT', require_order_revision, 12, 13)
        for value in (None, True, '12', 'wf-order-12', 12.0, 0, -1, 2**63):
            self.rejects('VALIDATION_ERROR', require_order_revision, value, 12)
            self.rejects('VALIDATION_ERROR', require_order_revision, 12, value)
