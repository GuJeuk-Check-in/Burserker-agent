"""Read-only observation of existing bot threads. Never sends or impersonates.
Reports source URLs/counts only; bot output is not a semantic correctness proof.
"""
import json
import os
from oijwef098234.gujeuk_ai.access import GUILD_ID, discord_get

BOT='1572127703036072029'
THREADS=('1573319987655868447','1573319403846500449','1573322049068466208','1573299031747067996')


def summarize(rows,bot=BOT):
    messages=sorted(rows,key=lambda r:int(r['id']))
    humans=[r for r in messages if not r.get('author',{}).get('bot') and r.get('content','').strip()]
    outputs=[r for r in messages if str(r.get('author',{}).get('id'))==bot and r.get('content','').strip()
             and not r['content'].lstrip().lower().startswith(('working','thinking','redirected','queued','⚙','🧠'))]
    followups=[r for r in humans if not r.get('mentions') and not r.get('mention_roles')
               and any(int(b['id'])<int(r['id']) for b in outputs)
               and any(int(b['id'])>int(r['id']) for b in outputs)]
    return {'human_messages':len(humans),'bot_nonstatus_outputs':len(outputs),
            'nonmention_followups_with_later_output':len(followups),
            'latest_bot_message_id':outputs[-1]['id'] if outputs else None,
            'accuracy_verified':False}


def main():
    from hermes_cli.config import load_env
    os.environ.update(load_env())
    if str(discord_get('/users/@me')['id'])!=BOT:raise ValueError('unexpected_bot_identity')
    result=[]
    for ident in THREADS:
        try:
            channel=discord_get('/channels/'+ident)
            if str(channel.get('guild_id'))!=GUILD_ID:raise ValueError('wrong_guild')
            metrics=summarize(discord_get('/channels/'+ident+'/messages?limit=100'))
            last=metrics['latest_bot_message_id']
            result.append({'thread':ident,**metrics,'last_output_url':
                f'https://discord.com/channels/{GUILD_ID}/{ident}/{last}' if last else None})
        except Exception as error:
            result.append({'thread':ident,'error':type(error).__name__})
    print(json.dumps({'read_only':True,'live_test_messages_sent':0,'threads':result}))


if __name__=='__main__':main()
