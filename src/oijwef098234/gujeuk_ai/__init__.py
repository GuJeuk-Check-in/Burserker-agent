"""Server core and compatibility namespace for the existing SSH client."""
import os


def _remote_mode(args):
    if args.local:
        return False
    if args.remote:
        return True
    if args.import_discord:
        return False
    return os.environ.get('GUJEUK_AI_REMOTE', '1') != '0'
