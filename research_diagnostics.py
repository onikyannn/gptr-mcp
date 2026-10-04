"""Passive, lossless observations of existing research calls and results."""
import functools
import inspect
import json
import logging
import os
import time

LOGGER = logging.getLogger('gptr_mcp.research_diagnostics')
LOGGER.setLevel(os.getenv('RESEARCH_LOG_LEVEL', 'INFO').upper())
if not any(getattr(handler, '_research_diagnostics_sink', False) for handler in LOGGER.handlers):
    handler = logging.StreamHandler()
    handler._research_diagnostics_sink = True
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    LOGGER.addHandler(handler)
LOGGER.propagate = False


def log_event(event, research_id, **data):
    if research_id is None or not LOGGER.isEnabledFor(logging.DEBUG):
        return
    try:
        LOGGER.debug('research_debug %s', json.dumps(
            {**data, 'research_id': research_id, 'event': event},
            ensure_ascii=False, default=str,
        ))
    except Exception:
        # Logging must not discard a paid result or trigger another provider call.
        pass


def observe_method(target, name, event, research_id, **context):
    if research_id is None or not LOGGER.isEnabledFor(logging.DEBUG):
        return
    method = getattr(target, name, None)
    if not callable(method):
        return

    def started(args, kwargs):
        log_event(event + '.request', research_id, **context, arguments={'args': args, 'kwargs': kwargs})
        return time.monotonic()

    def completed(result, began):
        log_event(event + '.result', research_id, **context, result=result, elapsed_seconds=round(time.monotonic() - began, 3))

    def failed(exc, began):
        log_event(event + '.error', research_id, **context, error_type=type(exc).__name__, error=str(exc), elapsed_seconds=round(time.monotonic() - began, 3))

    if inspect.iscoroutinefunction(method):
        @functools.wraps(method)
        async def observed(*args, **kwargs):
            began = started(args, kwargs)
            try:
                result = await method(*args, **kwargs)
            except Exception as exc:
                failed(exc, began)
                raise
            completed(result, began)
            return result
    else:
        @functools.wraps(method)
        def observed(*args, **kwargs):
            began = started(args, kwargs)
            try:
                result = method(*args, **kwargs)
            except Exception as exc:
                failed(exc, began)
                raise
            completed(result, began)
            return result
    setattr(target, name, observed)
