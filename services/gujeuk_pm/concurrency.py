"""Trusted Discord ingress forks busy questions, never changes delivery/actor IDs.

The pinned Hermes session patch serializes request_lane so both the adapter and
runner derive the same key. At capacity the normal, configured queue is used.
"""
GUILD = '1413450780068937790'
MAX_FORKS_PER_CONVERSATION = 3


def fork_busy_question(adapter, event):
    source = event.source
    platform = getattr(source.platform, 'value', source.platform)
    if (platform != 'discord' or str(getattr(source, 'scope_id', '')) != GUILD
            or not getattr(source, 'user_id', None)
            or getattr(source, 'chat_type', '') == 'dm'
            or getattr(source, 'request_lane', None)
            or str(getattr(event, 'text', '')).lstrip().startswith('/')):
        return False
    ident = str(getattr(source, 'message_id', '') or '')
    if not ident.isdecimal():
        return False
    key = adapter._event_session_key(event)
    active = adapter._active_sessions
    if key not in active:
        return False
    prefix = key + ':question:'
    if sum(k.startswith(prefix) for k in active) >= MAX_FORKS_PER_CONVERSATION:
        return False
    # No await between checking and reserving through handle_message's next lines.
    # user_id, scope_id, chat_id and thread_id remain the original Discord values.
    source.request_lane = ident
    return True
