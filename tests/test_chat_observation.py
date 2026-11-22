import importlib.util
from pathlib import Path


def test_chat_observation_counts_only_metadata_and_does_not_claim_accuracy():
    spec=importlib.util.spec_from_file_location('chat_observation',Path(__file__).parents[1]/'scripts/verify_chat_delivery.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rows=[{'id':'1','author':{'id':'human'},'content':'질문','mentions':[module.BOT]},
          {'id':'2','author':{'id':module.BOT,'bot':True},'content':'첫 답변'},
          {'id':'3','author':{'id':'human'},'content':'후속 질문'},
          {'id':'4','author':{'id':module.BOT,'bot':True},'content':'Working on it'},
          {'id':'5','author':{'id':module.BOT,'bot':True},'content':'후속 답변'}]
    result=module.summarize(list(reversed(rows)))
    assert result['human_messages']==2 and result['bot_nonstatus_outputs']==2
    assert result['nonmention_followups_with_later_output']==1
    assert result['latest_bot_message_id']=='5'
    assert result['accuracy_verified'] is False
    assert 'content' not in str(result)
