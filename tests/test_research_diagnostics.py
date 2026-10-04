"""Offline checks of observed research data; no model or search provider calls."""
import importlib.util
import json
import logging
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_research_input import load_tool, OfflineResearcher, RecorderRetriever

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger('gptr_mcp.research_diagnostics')


def events(records):
    return [json.loads(r.getMessage().split('research_debug ',1)[1]) for r in records if 'research_debug ' in r.getMessage()]


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def test_default_level_is_info(self):
        old_level = LOGGER.level
        self.addCleanup(LOGGER.setLevel, old_level)
        with patch.dict(os.environ, clear=True):
            spec = importlib.util.spec_from_file_location('diagnostics_default_fixture', ROOT / 'research_diagnostics.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertEqual(module.LOGGER.level, logging.INFO)

    def test_info_does_not_replace_observed_methods(self):
        import research_diagnostics as diag
        old_level = LOGGER.level
        self.addCleanup(LOGGER.setLevel, old_level)
        LOGGER.setLevel(logging.INFO)
        result = ['original result']
        def search():
            return result
        target = SimpleNamespace(search=search)
        diag.observe_method(target, 'search', 'web_search', 'fixture')
        self.assertIs(target.search, search)
        self.assertIs(target.search(), result)

    async def test_info_keeps_query_guard_without_search_observer(self):
        from research_input import create_researcher
        old_level = LOGGER.level
        self.addCleanup(LOGGER.setLevel, old_level)
        LOGGER.setLevel(logging.INFO)
        researcher = create_researcher(OfflineResearcher, query='seed', research_brief='Full task',
                                       diagnostic_id='fixture', query_domains=[])
        retriever = researcher.retrievers[0]('Full task')
        self.assertNotIn('search', retriever.__dict__)
        self.assertEqual(retriever.query, 'seed')
        with self.assertRaises(ValueError):
            researcher.retrievers[0]('x' * 401)

    async def test_report_and_actual_writer_input_are_logged_without_rewriting(self):
        ns=load_tool(); brief='Полное задание с ограничениями. '*100
        original='Исходный отчёт\n'+'Сведения и оговорки. '*2000
        with self.assertLogs(LOGGER,level='DEBUG') as capture:
            result=await ns['deep_research'](query='короткая фраза',research_brief=brief,query_domains=['source.example'])
            r=OfflineResearcher.instances[-1]
            async def writer(custom_prompt): r.received_prompt=custom_prompt; return original
            r.write_report=writer; r.get_costs=lambda:0
            output=await ns['write_report'](result['research_id'],custom_prompt='Формат отчёта')
        by_name={x['event']:x for x in events(capture.records)}
        self.assertEqual(output['report'],original)
        self.assertEqual(by_name['report.result']['report'],original)
        self.assertIn(brief,by_name['report.request']['custom_prompt'])
        self.assertEqual(by_name['report.request']['custom_prompt'],r.received_prompt)
        self.assertEqual(by_name['research.context']['context'],brief)
        self.assertEqual(by_name['research.sources']['sources'][0]['body'],'Evidence')
        self.assertTrue(all(x['research_id']==result['research_id'] for x in events(capture.records)))

    async def test_search_results_are_logged_for_the_query_actually_sent(self):
        ns=load_tool();brief='Полное задание. '*100
        with self.assertLogs(LOGGER,level='DEBUG') as capture:
            result=await ns['deep_research'](query='короткая фраза',research_brief=brief,query_domains=['source.example'])
        actual=[x for x in events(capture.records) if x['event']=='web_search.result']
        self.assertEqual(actual[0]['query'],'короткая фраза')
        self.assertEqual(actual[0]['result'],[{'href':'https://source.example','body':'Evidence'}])
        self.assertEqual(actual[0]['research_id'],result['research_id'])

    async def test_async_retriever_and_planner_keep_their_results(self):
        from research_input import create_researcher
        raw=[{'url':'https://source.example','content':'Полный текст'}]
        plan=['целевая фраза']
        class AsyncRetriever:
            def __init__(self,query):self.query=query
            async def search(self):return raw
        class Planner:
            async def plan_research(self,query,search_results):return plan
        def factory(query,**kwargs):
            return SimpleNamespace(query=query,retrievers=[AsyncRetriever],research_conductor=Planner(),deep_researcher=None)
        with self.assertLogs(LOGGER,level='DEBUG') as capture:
            r=create_researcher(factory,query='seed',research_brief='Full task',diagnostic_id='fixture-id')
            result=await r.retrievers[0]('Full task').search()
            chosen=await r.research_conductor.plan_research('Full task',search_results=raw)
        self.assertIs(result,raw)
        self.assertIs(chosen,plan)
        observed=events(capture.records)
        self.assertTrue(any(x['event']=='planner.result' and x['result']==plan for x in observed))

    async def test_failed_sink_does_not_discard_report(self):
        module=ROOT/'research_diagnostics.py'
        self.assertTrue(module.exists(),'Passive diagnostic logger is missing')
        import research_diagnostics as diag
        old_level = LOGGER.level
        self.addCleanup(LOGGER.setLevel, old_level)
        LOGGER.setLevel(logging.DEBUG)
        ns=load_tool()
        with patch.object(diag.LOGGER,'debug',side_effect=OSError('fixture sink failure')):
            result=await ns['deep_research'](query='seed',research_brief='Complete task')
        self.assertEqual(result['status'],'success')
