"""Keep a complete LLM task separate from phrases sent to web retrievers.

Bindings belong to one GPTResearcher instance. No global retriever classes are
changed. The pinned package has a child-factory hook installed at build time.
The original task remains the planner/writer input; only searches for that exact
task use the supplied initial phrase.
"""
import asyncio
import logging
from types import CodeType
from research_diagnostics import LOGGER as DIAGNOSTIC_LOGGER, log_event, observe_method

LOGGER = logging.getLogger(__name__)
MAX_SEARCH_QUERY_CHARS = 400


class ResearchInputError(ValueError):
    # The pinned native handlers re-raise only errors carrying this marker.
    _gptr_input_error = True


def validate_search_query(query):
    if not isinstance(query, str) or not query.strip():
        raise ResearchInputError('query must be a non-empty, concise web search phrase')
    query = query.strip()
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise ResearchInputError('Web search query exceeds 400 characters; refusing to truncate it')
    return query


async def _gather_or_cancel(*awaitables):
    tasks = [asyncio.ensure_future(item) for item in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except (ResearchInputError, asyncio.CancelledError):
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _bind_retriever(retriever, initial_query, research_brief, diagnostic_id=None):
    def bound_retriever(query, *args, **kwargs):
        if research_brief is not None and query == research_brief:
            query = initial_query
            LOGGER.info('Using initial search phrase instead of the full research brief (%d chars)', len(query))
        if research_brief is not None:
            try:
                query = validate_search_query(query)
            except ValueError as exc:
                log_event('web_search.rejected', diagnostic_id, query=query, error=str(exc))
                raise
        instance = retriever(query, *args, **kwargs)
        if diagnostic_id:
            observe_method(instance, 'search', 'web_search', diagnostic_id, query=query, retriever=retriever.__name__)
        return instance

    # GPT Researcher detects MCP retrievers using the factory's name.
    bound_retriever.__name__ = retriever.__name__
    return bound_retriever


def create_researcher(factory, *, query, research_brief=None, diagnostic_id=None, **kwargs):
    if research_brief is None:
        # Backwards-compatible input for existing MCP clients.
        researcher = factory(query=query, **kwargs)
        if diagnostic_id and DIAGNOSTIC_LOGGER.isEnabledFor(logging.DEBUG):
            return _bind_researcher(researcher, factory, query, None, diagnostic_id)
        return researcher
    query = validate_search_query(query)
    if not isinstance(research_brief, str) or not research_brief.strip():
        raise ValueError('research_brief must be a non-empty string when supplied')
    researcher = factory(query=research_brief, **kwargs)
    return _bind_researcher(researcher, factory, query, research_brief, diagnostic_id)


def _bind_researcher(researcher, factory, initial_query, research_brief, diagnostic_id=None):
    # The default writer already receives researcher.query. Native custom prompts
    # omit it, so retain the task separately for the MCP write_report boundary.
    researcher.research_brief = research_brief
    researcher.retrievers = [
        _bind_retriever(retriever, initial_query, research_brief, diagnostic_id)
        for retriever in researcher.retrievers
    ]
    deep_skill = getattr(researcher, 'deep_researcher', None)
    if research_brief is not None:
        researcher._gptr_gather = _gather_or_cancel
        conductor = getattr(researcher, 'research_conductor', None)
        methods = [getattr(conductor, '_get_context_by_search', None)]
        if deep_skill is not None:
            methods.append(deep_skill.deep_research)
            if not _has_code_constant(deep_skill.deep_research.__code__, '_child_researcher_factory'):
                raise RuntimeError('Rebuild the researcher image: the 0.16.1 child-factory hook is missing')
        for method in methods:
            if callable(method) and any(not _has_code_constant(method.__code__, hook)
                                        for hook in ('_gptr_input_error', '_gptr_gather')):
                raise RuntimeError('Rebuild the researcher image: the 0.16.1 input-error hooks are missing')

    if deep_skill is not None:
        def child_factory(*, query, **kwargs):
            # A branch is already a focused LLM-generated phrase, not a full task.
            if research_brief is not None:
                try:
                    query = validate_search_query(query)
                except ResearchInputError as exc:
                    log_event('branch.rejected', diagnostic_id, query=query, error=str(exc))
                    raise
            log_event('branch.created', diagnostic_id, query=query)
            child = factory(query=query, **kwargs)
            return _bind_researcher(child, factory, initial_query, research_brief, diagnostic_id)

        researcher._child_researcher_factory = child_factory
    if diagnostic_id and DIAGNOSTIC_LOGGER.isEnabledFor(logging.DEBUG):
        observe_method(getattr(researcher, 'research_conductor', None), 'plan_research', 'planner', diagnostic_id, task=researcher.query)
        observe_method(getattr(researcher, 'scraper_manager', None), 'browse_urls', 'scrape', diagnostic_id)
        if deep_skill is not None:
            for name in ('generate_research_plan', 'generate_search_queries', 'process_research_results'):
                observe_method(deep_skill, name, 'deep.' + name, diagnostic_id)
    return researcher


def _has_code_constant(code, constant):
    return any(
        value == constant
        or (isinstance(value, CodeType) and _has_code_constant(value, constant))
        for value in code.co_consts
    )


def report_prompt_with_brief(researcher, custom_prompt, *, include_research_brief=True):
    if not include_research_brief:
        return custom_prompt
    brief = getattr(researcher, 'research_brief', None)
    if brief is None or not custom_prompt:
        return custom_prompt
    return f"Complete research task:\n{brief}\n\nReport instructions:\n{custom_prompt}"
