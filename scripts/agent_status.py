"""Run inside the agent image: sanitized counts only, never source bodies/secrets."""
from pathlib import Path
import argparse
import json
from oijwef098234.gujeuk_ai.wiki import WikiWorkspace


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    args=parser.parse_args()
    store=WikiWorkspace(args.state/'workspace.db')
    try:
        sources={}
        for kind, row in store.source_status()['sources'].items():
            coverage=row['coverage']
            sources[kind]={'documents':row['documents'],'issues':len(coverage.get('issues',[])),
                **{k:coverage[k] for k in ('checked','last_success','credential_missing','tree_cycle_finished',
                    'pending_possible','messages','history_pending') if k in coverage}}
        wiki=store.wiki_status()
        result={'sources':sources,'notion':store.notion_frontier_status(),
                'wiki':{k:v for k,v in wiki.items() if k!='source_coverage'}}
        token=Path('/opt/data/pm-figma/token')
        result['figma_token_present']=token.is_file() and token.stat().st_size>0
        print(json.dumps(result,ensure_ascii=False))
    finally:
        store.close()


if __name__=='__main__':main()
