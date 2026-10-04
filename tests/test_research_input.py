import ast
import asyncio
import importlib.util
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

class RecorderRetriever:
    calls = []
    def __init__(self, query, query_domains=None):
        self.query = query
        self.domains = query_domains
    def search(self, max_results=8):
        self.calls.append((self.query, self.domains, max_results))
        return [{'href': 'https://source.example', 'body': 'Evidence'}]

class OfflineResearcher:
    instances = []
    def __init__(self, query, **kwargs):
        self.query = query
        self.kwargs = kwargs
        self.retrievers = [RecorderRetriever]
        self.instances.append(self)
    async def conduct_research(self):
        # Both native modes use the original task for their initial web search.
        self.initial = self.retrievers[0](self.query, query_domains=self.kwargs['query_domains']).search(8)
        self.planner_input = self.query
        self.addressed = self.retrievers[0]('Студия Политехническая 6 отзывы').search(3)
    def get_research_context(self): return self.planner_input
    def get_research_sources(self): return self.initial
    def get_source_urls(self): return ['https://source.example']


def load_tool():
    tree = ast.parse((ROOT / 'server.py').read_text())
    wanted = ['_clean_list', '_clean_source_urls', '_resolve_report_type', 'deep_research', 'write_report']
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in wanted]
    for n in nodes: n.decorator_list = []
    namespace = {'Optional': __import__('typing').Optional, 'List': list, 'Any': object, 'Dict': dict,
        'SUPPORTED_REPORT_TYPES': {'research_report', 'deep'}, 'GPTResearcher': OfflineResearcher,
        'logger': __import__('logging').getLogger('fixture'), 'uuid': __import__('uuid'),
        'mcp': SimpleNamespace(researchers={}), 'format_sources_for_response': lambda x:x,
        'cached': [], 'store_research_results': lambda *args:namespace['cached'].append(args), 'create_success_response': lambda x:{'status':'success', **x},
        'handle_exception': lambda exc,*args:{'status':'error','error':str(exc)}}
    module_path = ROOT / 'research_input.py'
    if module_path.exists():
        spec = importlib.util.spec_from_file_location('research_input_fixture', module_path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        namespace['create_researcher'] = module.create_researcher
        from research_diagnostics import log_event
        namespace['log_event'] = log_event
        namespace['report_prompt_with_brief'] = getattr(module, 'report_prompt_with_brief', lambda r,p:p)
        namespace['get_researcher_by_id'] = lambda store,key:(True,store[key],None)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'server.py'), 'exec'), namespace)
    return namespace

class ResearchInputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        RecorderRetriever.calls.clear(); OfflineResearcher.instances.clear()
        self.ns = load_tool(); self.tool = self.ns['deep_research']
    async def run_brief(self, **kwargs):
        self.assertIn('research_brief', inspect.signature(self.tool).parameters)
        return await self.tool(**kwargs)
    async def test_standard_and_deep_keep_brief_for_llm_but_only_short_queries_reach_retriever(self):
        brief = 'Original user request; constraints; map leads. ' * 100
        for mode in ('research_report','deep'):
            with self.subTest(mode=mode):
                result = await self.run_brief(query='детейлинг СПб Светлановский отзывы', research_brief=brief, report_type=mode, query_domains=['source.example'])
                self.assertEqual(result['status'], 'success')
                instance = OfflineResearcher.instances[-1]
                self.assertEqual(instance.planner_input, brief)
                self.assertEqual(RecorderRetriever.calls[-2], ('детейлинг СПб Светлановский отзывы', ['source.example'], 8))
                self.assertEqual(RecorderRetriever.calls[-1][0], 'Студия Политехническая 6 отзывы')
                self.assertEqual(instance.query, brief)  # also the native writer's task
    async def test_invalid_new_input_fails_before_researcher_is_created(self):
        for query,brief in [('', 'brief'), ('x'*401, 'brief'), ('seed',' ')] :
            with self.subTest(length=len(query)):
                result = await self.run_brief(query=query, research_brief=brief)
                self.assertEqual(result['status'], 'error')
                self.assertEqual(OfflineResearcher.instances, [])
    async def test_new_sessions_do_not_change_retrievers_in_other_sessions(self):
        brief='Full research task. '*100
        await self.run_brief(query='first seed',research_brief=brief)
        first=OfflineResearcher.instances[-1]
        await self.run_brief(query='second seed',research_brief=brief)
        second=OfflineResearcher.instances[-1]
        first.retrievers[0](brief).search(); second.retrievers[0](brief).search()
        self.assertEqual([x[0] for x in RecorderRetriever.calls[-2:]], ['first seed','second seed'])
        self.assertEqual(OfflineResearcher('legacy',query_domains=[]).retrievers[0], RecorderRetriever)
    async def test_generated_overlong_query_is_rejected_before_native_truncation(self):
        await self.run_brief(query='seed',research_brief='Full task. '*100)
        retriever=OfflineResearcher.instances[-1].retrievers[0]
        before=len(RecorderRetriever.calls)
        with self.assertRaises(ValueError):retriever('g'*401).search()
        self.assertEqual(len(RecorderRetriever.calls),before)
    async def test_brief_results_are_kept_by_id_and_do_not_overwrite_topic_cache(self):
        await self.run_brief(query='same seed', research_brief='first complete task')
        await self.run_brief(query='same seed', research_brief='second complete task')
        self.assertEqual(self.ns['cached'], [])
        self.assertEqual(len(self.ns['mcp'].researchers),2)
        await self.tool(query='legacy topic')
        self.assertEqual(self.ns['cached'][0][0], 'legacy topic')
    async def test_standard_research_has_no_unused_child_factory(self):
        await self.run_brief(query='seed',research_brief='Complete root task. '*100)
        researcher=OfflineResearcher.instances[-1]
        self.assertFalse(hasattr(researcher,'_child_researcher_factory'))
        self.assertEqual(researcher.query,'Complete root task. '*100)

    async def test_invalid_query_cancels_parallel_work(self):
        await self.run_brief(query='seed',research_brief='Complete task')
        researcher=OfflineResearcher.instances[-1]
        self.assertTrue(callable(getattr(researcher,'_gptr_gather',None)))
        started=asyncio.Event()
        stopped=asyncio.Event()
        finished=[]
        async def pending_branch():
            started.set()
            try:
                await asyncio.Event().wait()
                finished.append('should not finish')
            finally:
                stopped.set()
        async def rejected_branch():
            await started.wait()
            researcher.retrievers[0]('x'*401)
        with self.assertRaises(ValueError):
            await researcher._gptr_gather(pending_branch(),rejected_branch())
        self.assertTrue(stopped.is_set())
        self.assertEqual(finished,[])
    async def test_custom_report_prompt_contains_complete_task(self):
        brief='Original question, constraints and dialogue. '*100
        result=await self.run_brief(query='seed',research_brief=brief)
        r=OfflineResearcher.instances[-1]
        async def writer(custom_prompt):
            r.received_prompt=custom_prompt
            return 'Fixture report'
        r.write_report=writer; r.get_costs=lambda:0
        result=await self.ns['write_report'](result['research_id'],custom_prompt='Requested report format')
        self.assertEqual(result['status'],'success')
        self.assertIn(brief,r.received_prompt)
        self.assertIn('Requested report format',r.received_prompt)
    async def test_self_contained_writer_prompt_does_not_get_search_brief_appended(self):
        result=await self.run_brief(query='seed',research_brief='Map lead and search-only instructions')
        r=OfflineResearcher.instances[-1]
        async def writer(custom_prompt):
            r.received_prompt=custom_prompt
            return 'Original report'
        r.write_report=writer; r.get_costs=lambda:0
        prompt='Original user request with explicit constraints. Report instructions.'
        result=await self.ns['write_report'](result['research_id'],custom_prompt=prompt,
                                           include_research_brief=False)
        self.assertEqual(result['status'],'success')
        self.assertEqual(r.received_prompt,prompt)
        self.assertEqual(result['report'],'Original report')

    async def test_legacy_call_without_brief_is_unchanged(self):
        result=await self.tool(query='legacy search',query_domains=['source.example'])
        self.assertEqual(result['status'],'success')
        self.assertEqual(OfflineResearcher.instances[-1].query,'legacy search')
        self.assertEqual(RecorderRetriever.calls[-2][0], 'legacy search')

if __name__=='__main__':unittest.main()
