"""Install compatibility hooks in the pinned GPT Researcher 0.16.1.

Run while building the image, before importing GPT Researcher. Legacy callers
still use the native factory; brief-bearing runs provide an instance-local one.
Native exception handlers must not turn invalid web queries into partial success.
An unexpected dependency version/source layout fails the build explicitly.
"""
import ast
from importlib.metadata import distribution

NATIVE = '                    researcher = GPTResearcher(\n'
HOOK = ('                    researcher_factory = getattr(self.researcher, "_child_researcher_factory", GPTResearcher)\n'
        '                    researcher = researcher_factory(\n')
GATHER_NATIVE = 'await asyncio.gather('
GATHER_HOOK = 'await getattr(self.researcher, "_gptr_gather", asyncio.gather)('


def patch_source(source):
    if source.count(HOOK) == 1 and NATIVE not in source:
        return source
    if source.count(NATIVE) != 1 or HOOK in source:
        raise RuntimeError('Unsupported DeepResearchSkill source; child-factory hook was not applied')
    return source.replace(NATIVE, HOOK, 1)


def patch_validation_errors(source):
    """Preserve native recovery except for our marked input validation errors."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    inserts = []
    handlers = 0
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        if not isinstance(handler.type, ast.Name) or handler.type.id != 'Exception':
            continue
        handlers += 1
        guard = f'if getattr({handler.name}, "_gptr_input_error", False):\n    raise\n'
        first = handler.body[0]
        if ast.dump(first) == ast.dump(ast.parse(guard).body[0]):
            continue
        indent = ' ' * first.col_offset
        inserts.append((first.lineno - 1, ''.join(indent + line for line in guard.splitlines(keepends=True))))
    if not handlers:
        raise RuntimeError('Unsupported researcher source; input-error guards were not applied')
    for line, text in sorted(inserts, reverse=True):
        lines.insert(line, text)
    return ''.join(lines)


def patch_gather(source, expected_calls):
    if source.count(GATHER_NATIVE) + source.count(GATHER_HOOK) != expected_calls:
        raise RuntimeError('Unsupported researcher source; parallel cancellation hooks were not applied')
    return source.replace(GATHER_NATIVE, GATHER_HOOK)


def install():
    package = distribution('gpt-researcher')
    if package.version != '0.16.1':
        raise RuntimeError('Research input hooks support only gpt-researcher==0.16.1')
    updates = []
    for name in ('deep_research.py', 'researcher.py'):
        path = package.locate_file('gpt_researcher/skills/' + name)
        source = path.read_text()
        patched = patch_source(source) if name == 'deep_research.py' else source
        patched = patch_validation_errors(patched)
        patched = patch_gather(patched, 1 if name == 'deep_research.py' else 3)
        if patched != source:
            updates.append((path, patched))
    # Validate both sources before writing either one.
    for path, patched in updates:
        path.write_text(patched)


if __name__ == '__main__':
    install()
