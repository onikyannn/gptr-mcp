"""Run against the pinned GPT Researcher package with every network boundary fake.

For verification this is additionally executed in a Docker container with
--network none. No research provider is called.
"""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

try:
    from gpt_researcher import GPTResearcher
except ImportError:
    GPTResearcher = None

from research_input import create_researcher, report_prompt_with_brief

@unittest.skipIf(GPTResearcher is None, 'Pinned GPT Researcher runtime is not installed')
class NativeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_native_modes_keep_task_in_planning_and_writing_but_not_web_search(self):
        import gpt_researcher.actions.query_processing as qp
        import gpt_researcher.actions.agent_creator as ac
        import gpt_researcher.actions.report_generation as rg
        import gpt_researcher.skills.deep_research as ds
        from gpt_researcher.skills.browser import BrowserManager
        from gpt_researcher.retrievers.tavily.tavily_search import TavilySearch
        brief = 'Original request: research history, evidence, timeframe and comparison criteria. ' * 90
        seed = 'historical technology comparison independent evidence'
        for mode, malformed, custom in [(m, bad, custom) for m in ('research_report','deep') for bad,custom in [('none',False),('none',True),('none','self-contained'),('overlong',False),('echo',False)]]:
            with self.subTest(mode=mode, malformed=malformed, custom=custom):
                web_inputs=[]; model_inputs=[]; serial=0
                async def llm(messages, **kwargs):
                    nonlocal serial
                    model_inputs.append(messages)
                    text='\n'.join(x['content'] for x in messages)
                    if kwargs.get('stream'):return 'Fixture researcher report'
                    if text.startswith('Write '):
                        return json.dumps(['short targeted web query'] + ([] if malformed=='none' else ['x'*401 if malformed=='overlong' else brief]))
                    if '"researchGoal"' in text:
                        serial+=1
                        return json.dumps([{'query':f'short deep branch {serial}', 'researchGoal':'a focused question'}] + ([] if malformed=='none' else [{'query':'x'*401 if malformed=='overlong' else brief,'researchGoal':'invalid fixture branch'}]))
                    if '"learnings"' in text:
                        return json.dumps({'learnings':[{'insight':'A supported fact','sourceUrl':'https://evidence.example/page'}], 'followUpQuestions':['A follow-up question?']})
                    if '"questions"' in text:
                        return json.dumps({'questions':['What evidence supports the comparison?']})
                    return json.dumps({'server':'Fixture researcher','agent_role_prompt':'Research from evidence.'})
                def search(self, query, **kwargs):
                    web_inputs.append(query)
                    return {'results':[{'url':'https://evidence.example/'+str(len(web_inputs)), 'content':'A search preview.'}]}
                async def browse(self, urls):
                    rows=[{'url':url, 'title':'Evidence page', 'raw_content':'Independent evidence with methodological limitations.'} for url in urls]
                    self.researcher.add_research_sources(rows)
                    return rows
                with patch.dict(os.environ, {'OPENAI_API_KEY':'fixture', 'TAVILY_API_KEY':'fixture','RETRIEVER':'tavily', 'CONTEXT_FILTER':'none', 'CURATE_SOURCES':'false','IMAGE_GENERATION':'false','MAX_ITERATIONS':'2','DEEP_RESEARCH_DEPTH':'2','DEEP_RESEARCH_BREADTH':'1','DEEP_RESEARCH_CONCURRENCY':'1'}), patch.object(qp,'create_chat_completion',llm), patch.object(ac,'create_chat_completion',llm), patch.object(rg,'create_chat_completion',llm), patch.object(ds,'create_chat_completion',llm), patch.object(TavilySearch,'_search',search), patch.object(BrowserManager,'browse_urls',browse):
                    if os.environ.get('VERIFY_PREVIOUS_BEHAVIOR')=='1':
                        researcher=GPTResearcher(query=brief,report_type=mode,verbose=False)
                    else:
                        researcher=create_researcher(GPTResearcher,query=seed,research_brief=brief,report_type=mode,verbose=False,diagnostic_id=f'fixture-{mode}-{malformed}-{custom}')
                    must_fail = malformed != 'none'
                    if must_fail:
                        with self.assertRaises(ValueError):
                            await researcher.conduct_research()
                        self.assertEqual(web_inputs[0], seed)
                        self.assertTrue(all(len(q) <= 400 for q in web_inputs))
                        continue
                    context=await researcher.conduct_research()
                    writer_task='User task: compare technologies in the requested period.'
                    prompt=writer_task if custom=='self-contained' else 'Requested fixture report format'
                    report=await researcher.write_report(custom_prompt=report_prompt_with_brief(
                        researcher,prompt,include_research_brief=custom!='self-contained') if custom else None)
                self.assertTrue(context)
                self.assertEqual(report,'Fixture researcher report')
                self.assertGreaterEqual(len(web_inputs),2)
                self.assertEqual(web_inputs[0],seed)
                self.assertTrue(all(len(q)<=400 and q not in (brief[:400], 'x'*400) for q in web_inputs))
                self.assertTrue(any(brief in x['content'] for messages in model_inputs[:-1] for x in messages))
                if custom=='self-contained':
                    self.assertNotIn(brief,model_inputs[-1][-1]['content'])
                    self.assertEqual(model_inputs[-1][-1]['content'].count(writer_task),1)
                else:
                    self.assertIn(brief,model_inputs[-1][-1]['content'])
                self.assertEqual(researcher.get_costs(),0)
