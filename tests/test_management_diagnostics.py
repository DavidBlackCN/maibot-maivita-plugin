"""Rejection diagnostics preserve the public contract and omit input values."""
import asyncio
import json
import logging
import unittest
from unittest.mock import patch

import r0_support
import test_host_management as fixtures

command=fixtures.command
from maibot_autonomous_planning_plugin.maivita.host import management, management_requests


class DiagnosticTests(unittest.TestCase):
    def check_rejection(self, path, value, code, stage, bits, bot='99999', delete=False):
        c=command()
        target=c
        for key in path[:-1]:
            target=target[key]
        if path:
            if delete: del target[path[-1]]
            else: target[path[-1]]=value
        with self.assertLogs(management.logger,level='WARNING') as logs:
            with self.assertRaisesRegex(ValueError,'^UNTRUSTED_CONTEXT$'):
                management.local_request(fixtures.ManagementTests().plugin(),c,bot)
        self.assertEqual(len(logs.records),1)
        record=logs.records[0]
        expected=dict(reason_code=code,stage=stage,present=bits[0],type_ok=bits[1],matches=bits[2])
        self.assertEqual(json.loads(record.getMessage()),expected)
        for key,value in expected.items():
            self.assertEqual(getattr(record,key),value)
        self.assertEqual(record.levelno,logging.WARNING)
        for key in ('present','type_ok','matches'):
            self.assertIs(type(getattr(record,key)),bool)
        for sensitive in ('10001','20001','99999','/maivita wake'):
            self.assertNotIn(sensitive,record.getMessage())

    def test_every_context_reason_and_branch(self):
        cases=[
            (('message',),None,'MESSAGE_NOT_DICT','message',(True,False,False)),
            (('message',),None,'MESSAGE_NOT_DICT','message',(False,False,False),True),
            ((),None,'BOT_ACCOUNT_INVALID','bot_account',(False,False,False)),
            (('message','message_info'),None,'USER_INFO_MISSING','user_info',(False,False,False)),
            (('message','message_info','user_info'),[], 'USER_INFO_MISSING','user_info',(True,False,False)),
            (('message','message_info','user_info','user_id'),123,'USER_ACCOUNT_INVALID','user_account',(True,False,False)),
            (('message','message_info','user_info','user_id'),'99999','USER_ACCOUNT_INVALID','user_account',(True,True,False)),
            (('message','message_info','group_info'),[], 'GROUP_INFO_INVALID','group_info',(True,False,False)),
            (('message','message_info','group_info','group_id'),'bad','GROUP_INFO_INVALID','group_info',(True,True,False)),
            (('text',),123,'TEXT_MISMATCH','text',(True,False,False)),
            (('text',),'other','TEXT_MISMATCH','text',(True,True,False)),
            (('message','platform'),'other','PLATFORM_MISMATCH','raw_platform',(True,True,False)),
            (('platform',),None,'PLATFORM_MISMATCH','command_platform',(True,False,False)),
            (('message','account_id'),'other','ACCOUNT_MISMATCH','account_id',(True,True,False)),
            (('user_id',),'other','AUTHOR_MISMATCH','author',(True,True,False)),
            (('group_id',),'other','GROUP_MISMATCH','group_id',(True,True,False)),
            (('message','session_id'),'other','SESSION_MISMATCH','session_id',(True,True,False)),
            (('stream_id',),'s','STREAM_NOT_ALLOWED','stream_id',(True,True,False)),
            (('message','message_id'),'', 'MESSAGE_ID_INVALID','message_id',(True,True,False)),
            (('message','message_id'),123,'MESSAGE_ID_INVALID','message_id',(True,False,False)),
            (('message','scope'),'', 'SCOPE_INVALID','scope',(True,True,False)),
            (('message','scope'),123,'SCOPE_INVALID','scope',(True,False,False)),
        ]
        for flag in ('is_bot','is_system','is_mai_message','is_notify'):
            for value in (True,0,None):
                cases.append((('message',flag),value,'FLAG_SET',flag,(True,type(value) is bool,False)))
        for path,value,code,stage,bits,*delete in cases:
            with self.subTest(code=code,stage=stage,value=value):
                if code=='STREAM_NOT_ALLOWED':
                    with patch.object(management,'stream_allowed',return_value=False):
                        self.check_rejection(path,value,'CONTEXT_'+code,stage,bits)
                else:
                    self.check_rejection(path,value,'CONTEXT_'+code,stage,bits,
                        bot=None if code=='BOT_ACCOUNT_INVALID' else '99999',delete=bool(delete))
        c=command();c['text']='x'*257;c['message']['processed_plain_text']=c['text']
        with patch(__name__+'.command',return_value=c):
            self.check_rejection((),None,'CONTEXT_TEXT_MISMATCH','text',(True,True,True))
        self.check_rejection((),None,'CONTEXT_BOT_ACCOUNT_INVALID','bot_account',(True,True,False),bot='bad')

    def test_dispatch_parse_preserves_original_error(self):
        c=command();c['text']='/maivita sleep 1'
        p=fixtures.ManagementTests().plugin();p.active_config.instance.timezone='Asia/Shanghai'
        with self.assertLogs(management.logger,level='WARNING') as logs:
            result=asyncio.run(management_requests.dispatch(p,c))
        self.assertEqual(result,'MaiVita 未执行：睡眠长度须为5至1080分钟')
        expected=dict(reason_code='MANAGEMENT_PARSE_FAILED',stage='parse',present=True,type_ok=True,matches=False)
        self.assertEqual(json.loads(logs.records[0].getMessage()),expected)
        for key,value in expected.items():self.assertEqual(getattr(logs.records[0],key),value)
        p.ctx.chat.get_all_streams.assert_not_awaited()

    def test_dispatch_uses_same_context_diagnostics(self):
        p=fixtures.ManagementTests().plugin();p.active_config.instance.timezone='Asia/Shanghai'
        c=command();c['message']['message_id']=''
        with self.assertLogs(management.logger,level='WARNING') as logs:
            result=asyncio.run(management_requests.dispatch(p,c))
        self.assertEqual(result,'MaiVita 未执行：UNTRUSTED_CONTEXT')
        self.assertEqual(logs.records[0].reason_code,'CONTEXT_MESSAGE_ID_INVALID')
        self.assertEqual((logs.records[0].present,logs.records[0].type_ok,logs.records[0].matches),(True,True,False))
        p.ctx.call_host_method.assert_not_awaited()

    def test_success_and_unsupported_command_unchanged(self):
        p=fixtures.ManagementTests().plugin();c=command()
        with patch.object(management.logger,'warning') as warning:
            query,key=management.local_request(p,c,'99999')
            self.assertEqual(query['action'],'wake')
            self.assertTrue(key.startswith('local-command:'))
            c['text']='/maivita unsupported';c['message']['processed_plain_text']=c['text']
            with self.assertRaisesRegex(ValueError,'^UNSUPPORTED_COMMAND$'):
                management.local_request(p,c,'99999')
            warning.assert_not_called()
