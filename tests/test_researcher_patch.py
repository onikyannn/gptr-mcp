"""The pinned compatibility hook must fail clearly on unsupported source."""
import asyncio
import unittest
from types import SimpleNamespace
from patch_researcher import patch_source, patch_validation_errors, patch_gather, NATIVE, HOOK
from research_input import validate_search_query


class PatchTests(unittest.TestCase):
    def test_patch_is_idempotent_and_preserves_surrounding_source(self):
        source='before\n'+NATIVE+'after\n'
        patched=patch_source(source)
        self.assertEqual(patched,'before\n'+HOOK+'after\n')
        self.assertEqual(patch_source(patched),patched)

    def test_unknown_or_ambiguous_layout_is_rejected(self):
        for source in ('different upstream source', NATIVE+NATIVE, NATIVE+HOOK):
            with self.subTest(source=source):
                with self.assertRaises(RuntimeError):patch_source(source)

    def test_validation_guards_rethrow_only_our_input_errors(self):
        source = '''def recover(error):
    try:
        raise error
    except Exception as exc:
        return "native recovery"
'''
        patched = patch_validation_errors(source)
        self.assertEqual(patch_validation_errors(patched), patched)
        namespace = {}
        exec(compile(patched, '<guard-fixture>', 'exec'), namespace)
        recover = namespace['recover']
        self.assertEqual(recover(ConnectionError('provider unavailable')), 'native recovery')
        self.assertEqual(recover(ValueError('unrelated library validation')), 'native recovery')
        with self.assertRaises(ValueError) as rejected:
            validate_search_query('x' * 401)
        with self.assertRaises(ValueError) as propagated:
            recover(rejected.exception)
        self.assertIs(propagated.exception, rejected.exception)

    def test_unsupported_validation_source_is_rejected(self):
        with self.assertRaises(RuntimeError):
            patch_validation_errors('def changed_upstream():\n    return None\n')


class ParallelHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_hook_uses_instance_callback_or_native_gather(self):
        source = '''async def collect(self, *tasks):
    return await asyncio.gather(*tasks)
'''
        patched = patch_gather(source, 1)
        self.assertEqual(patch_gather(patched, 1), patched)
        namespace = {'asyncio': asyncio}
        exec(compile(patched, '<parallel-fixture>', 'exec'), namespace)
        async def value():
            return 'original result'
        calls = []
        async def callback(*tasks):
            calls.append('instance callback')
            return await asyncio.gather(*tasks)
        for researcher in (SimpleNamespace(), SimpleNamespace(_gptr_gather=callback)):
            result = await namespace['collect'](SimpleNamespace(researcher=researcher), value())
            self.assertEqual(result, ['original result'])
        self.assertEqual(calls, ['instance callback'])

    def test_changed_parallel_layout_is_rejected(self):
        with self.assertRaises(RuntimeError):
            patch_gather('async def collect():\n    return None\n', 1)
