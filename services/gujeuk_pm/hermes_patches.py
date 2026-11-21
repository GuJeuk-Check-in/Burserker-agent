"""Fail-closed compatibility patches for the pinned Hermes image, not live updates."""
from pathlib import Path
import argparse


def replace_once(text, old, new):
    if new in text:
        return text
    if text.count(old) != 1:
        raise ValueError('Hermes upstream changed; review compatibility before deployment')
    return text.replace(old, new, 1)


def session_patch(text):
    text = replace_once(text,
        '    shared_session: bool = False\n',
        '    shared_session: bool = False\n    request_lane: Optional[str] = None  # burserker-independent-question-v1\n')
    text = replace_once(text,
        '    _OPTIONAL_TAIL = ("auto_thread_initial_name", "prospective_thread_id")',
        '    _OPTIONAL_TAIL = ("auto_thread_initial_name", "prospective_thread_id", "request_lane")')
    return replace_once(text,
        '    return ":".join(str(part) for part in parts)\n',
        '''    key = ":".join(str(part) for part in parts)
    lane = getattr(source, "request_lane", None)
    if (source.platform == Platform.DISCORD and str(source.scope_id) == "1413450780068937790"
            and isinstance(lane, str) and lane.isdecimal()):
        key += ":question:" + lane
    return key
''')


def ingress_patch(text):
    return replace_once(text,
        '        session_key = self._event_session_key(event)\n        if expected_session_key and session_key != expected_session_key:',
        '''        # burserker-independent-question-v1: keep the first run, fork the second.
        from burserker_concurrency import fork_busy_question
        if not expected_session_key:
            fork_busy_question(self, event)
        session_key = self._event_session_key(event)
        if expected_session_key and session_key != expected_session_key:''')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/opt/hermes'))
    args = parser.parse_args()
    from discord_mentions import patched_source
    changes = [('gateway/session.py', session_patch),
               ('gateway/platforms/base.py', ingress_patch),
               ('plugins/platforms/discord/adapter.py', patched_source)]
    # Validate everything before writing any source.
    outputs = []
    for name, transform in changes:
        path = args.root / name
        updated = transform(path.read_text())
        compile(updated, name, 'exec')
        outputs.append((path, updated))
    for path, text in outputs:
        path.write_text(text)


if __name__ == '__main__':
    main()
