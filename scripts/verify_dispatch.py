"""Exercise the real patched BasePlatformAdapter, without posting to Discord.
Default uses a barrier stub. --real-model also calls the existing read-only model.
Run inside the pinned Hermes image (SDK is intentionally not a PyPI dependency).
"""
import argparse
import asyncio
import json
import os
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult
from gateway.session import SessionSource


class CaptureAdapter(BasePlatformAdapter):
    async def connect(self):self._running=True;return True
    async def disconnect(self):self._running=False
    async def get_chat_info(self,chat_id):return {'id':chat_id,'name':'regression-only'}
    async def send(self,chat_id,content,**kwargs):
        self.sent.append((str(chat_id),content));return SendResult(success=True,message_id=str(len(self.sent)))


async def exercise(real=False):
    adapter=CaptureAdapter(PlatformConfig(enabled=True,typing_indicator=False),Platform.DISCORD)
    adapter.sent=[]
    adapter._busy_text_mode='queue'
    await adapter.connect()
    entered=[];both=asyncio.Event();keys=[]
    if real:
        from hermes_cli.config import load_env
        from oijwef098234.gujeuk_ai.inference import ReadOnlyInference
        os.environ.update(load_env())
    async def handler(event):
        entered.append(event.text);keys.append(adapter._event_session_key(event))
        if len(entered)==2:both.set()
        await asyncio.wait_for(both.wait(),8)
        if real:
            engine=ReadOnlyInference()
            try:return await asyncio.to_thread(engine.ask,'도구를 쓰지 말고 연결 확인 '+event.text+' 라고만 답하세요.',lambda *_:None)
            finally:engine.close()
        return 'verified-'+event.text
    adapter.set_message_handler(handler)
    def event(ident,text):
        source=SessionSource(platform=Platform.DISCORD,scope_id='1413450780068937790',
            chat_id='11111',thread_id='11111',chat_type='group',user_id='22222',message_id=ident)
        return MessageEvent(text=text,source=source,message_id=ident,user_id='22222')
    await adapter.handle_message(event('1001','A'))
    # Wait for the first callback, not a blind timing sleep.
    async def first_started():
        while not entered:await asyncio.sleep(0)
    await asyncio.wait_for(first_started(),5)
    await adapter.handle_message(event('1002','B'))
    await asyncio.wait_for(both.wait(),8)
    while adapter._background_tasks:
        await asyncio.wait_for(asyncio.gather(*list(adapter._background_tasks)),120)
    assert len(set(keys))==2,keys
    assert len(adapter.sent)==2,[(c,len(t)) for c,t in adapter.sent]
    assert all(c=='11111' for c,_ in adapter.sent)
    assert entered==['A','B']
    await adapter.disconnect()
    print(json.dumps({'dispatcher':'actual_patched_Hermes','model':'real' if real else 'barrier_stub',
        'responses':2,'started_concurrently':True,'independent_keys':2,'delivery_preserved':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--real-model',action='store_true')
    args=parser.parse_args();asyncio.run(exercise(args.real_model))
