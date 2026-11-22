"""Owner-only, non-destructive approval queue audit. Prints counts, never notes."""
import argparse
import json
from pathlib import Path
from oijwef098234.gujeuk_ai.workspace import Workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    path = args.state / 'workspace.db'
    if not path.is_file() or path.is_symlink():
        raise ValueError('existing_private_workspace_required')
    store = Workspace(path)
    try:
        review = store.proposal_review()
        print(json.dumps({key: value for key, value in review.items() if key != 'groups'}, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
