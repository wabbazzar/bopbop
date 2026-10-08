"""Protocol boundaries, installation lifecycle, MCP and real local transport."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'bin'))
from bopbop_packs import Pack, Registry, validate_arguments, wrapper_options


@pytest.fixture
def installed(tmp_path):
    root=tmp_path/'context'/'packs'/'sample'
    root.mkdir(parents=True)
    (root/'pack.toml').write_text('name = "sample"\nprofile = "test"\nfragment = "guide.md"\ntools = "tools.json"\n')
    (root/'guide.md').write_text('Use sample_count to count observations. Never infer health from missing data.')
    (root/'handler.py').write_text('import sys,json\nr=json.load(sys.stdin)\nprint(json.dumps({"ok":True,"count":r["arguments"].get("limit",5)}))\n')
    schema={'type':'object','additionalProperties':False,'properties':{
        'project':{'type':'string','maxLength':64},'limit':{'type':'integer','minimum':1,'maximum':20},
        'window':{'type':'string','enum':['24h','7d']}},'required':['window']}
    doc={'schema_version':1,'tools':[{'name':'sample_count','description':'Count observations',
        'parameters':schema,'handler':'handler.py','timeout_seconds':1,'max_output_bytes':512}]}
    (root/'tools.json').write_text(json.dumps(doc))
    return root


def test_real_handler_and_profile_alias(installed):
    registry=Registry(installed.parents[1],['test'])
    assert registry.call('sample_count',{'window':'24h','limit':7})=={'ok':True,'count':7}
    assert len(registry.schemas)==1
    assert 'Never infer health' in registry.instructions
    assert Registry(installed.parents[1],['sample']).fingerprint==registry.fingerprint


@pytest.mark.parametrize('args,enabled,workspace', [
    ([], True, True),
    (['--packs', ''], False, True),
    (['--profile', 'test'], True, False),
])
def test_launcher_cli_pack_defaults_and_tools_listing(installed, tmp_path, args, enabled, workspace):
    import shlex
    home = tmp_path / 'bopbop-home'
    home.mkdir()
    (home / 'env').write_text(
        f'BOPBOP_CONTEXT_DIR={shlex.quote(str(installed.parents[1]))}\n'
        'BOPBOP_CLI_PACKS=test\n'
        'BOPBOP_OLLAMA_HOST=http://127.0.0.1:1\n'
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith('BOPBOP_')}
    env['BOPBOP_HOME'] = str(home)
    # No model or API is reachable. Listing must work from an unrelated cwd.
    result = subprocess.run([str(ROOT / 'bin/bopbop'), 'local', '--banner', 'off',
                             '--color', 'never', *args, '/tools'], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert ('sample_count' in result.stderr) is enabled
    assert ('read_file' in result.stderr) is workspace
    if enabled:
        assert 'Pack · sample (test)' in result.stderr
        assert 'installed, not enabled' not in result.stderr
    else:
        assert 'installed, not enabled' in result.stderr
        assert 'bopbop local --packs sample' in result.stderr


@pytest.mark.parametrize('arguments',[
    None,[],{'window':'24h','limit':True},{'window':'24h','limit':0},
    {'window':'24h','limit':21},{'window':'24h','limit':1.5},
    {'window':'24h','limit':'5'},{'window':'30d'},{},{'window':'24h','extra':'x'},
    {'window':'24h','project':'x'*65},{'window':'24h','project':'x\nsecret'},
    {'window':'24h','project':False}
])
def test_bad_arguments_cannot_reach_handler(installed,arguments):
    (installed/'handler.py').write_text('raise RuntimeError("handler should not run")')
    value=Registry(installed.parents[1],['sample']).call('sample_count',arguments)
    assert value['ok'] is False
    assert 'exited' not in value['error']


@pytest.mark.parametrize('field,value',[
    ('handler','../escape.py'),('handler','/tmp/escape.py'),('name','run command'),
    ('timeout_seconds',0),('timeout_seconds',61),('max_output_bytes',100),
    ('max_output_bytes',100000),('description',''),('parameters',{'type':'array'})
])
def test_invalid_manifests_are_rejected(installed,field,value):
    path=installed/'tools.json';doc=json.loads(path.read_text());doc['tools'][0][field]=value
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):Pack(installed)


def test_duplicate_tool_and_escape_symlink(installed,tmp_path):
    path=installed/'tools.json';doc=json.loads(path.read_text());doc['tools']*=2
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):Pack(installed)
    doc['tools']=doc['tools'][:1];path.write_text(json.dumps(doc))
    (installed/'handler.py').unlink();outside=tmp_path/'outside.py';outside.write_text('print("secret")')
    (installed/'handler.py').symlink_to(outside)
    with pytest.raises(ValueError):Pack(installed)


@pytest.mark.parametrize('body,needle',[
    ('print("x"*10000)','output limit'),('import time;time.sleep(3)','deadline'),
    ('print("not json")','Expecting value'),('print("[]")','JSON object'),
    ('import sys;sys.exit(9)','status 9'),('print("{\\\"value\\\":NaN}")','non-finite'),
    ('import sys;sys.stderr.write("x"*20000)','output limit')
])
def test_handler_failures_are_bounded(installed,body,needle):
    (installed/'handler.py').write_text(body)
    value=Registry(installed.parents[1],['sample']).call('sample_count',{'window':'24h'})
    assert value['ok'] is False
    assert needle in value['error']


def test_mcp_real_stdio_roundtrip(installed):
    requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05'}},
      {'jsonrpc':'2.0','method':'notifications/initialized'},
      {'jsonrpc':'2.0','id':2,'method':'tools/list'},
      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'sample_count','arguments':{'window':'24h','limit':3}}},
      {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'write_file','arguments':{}}}]
    proc=subprocess.run([sys.executable,str(ROOT/'bin/bopbop-pack.py'),'--context-dir',str(installed.parents[1]),
        'mcp','--packs','sample'],input='\n'.join(map(json.dumps,requests))+'\n',text=True,capture_output=True,check=True,timeout=5)
    answers=[json.loads(x) for x in proc.stdout.splitlines()]
    assert len(answers)==4
    assert answers[1]['result']['tools'][0]['name']=='sample_count'
    assert json.loads(answers[2]['result']['content'][0]['text'])['count']==3
    assert answers[3]['result']['isError'] is True


def test_install_reinstall_remove_and_invalid_reinstall(tmp_path,installed):
    context=tmp_path/'active';context.mkdir();(context/'CLAUDE.md').write_text('Owner context\n')
    env={**os.environ,'BOPBOP_HOME':str(tmp_path/'home'),'BOPBOP_CONTEXT_DIR':str(context)}
    def cli(*args,check=True):
        return subprocess.run([str(ROOT/'bin/bopbop'),*args],cwd='/tmp',env=env,text=True,capture_output=True,check=check)
    cli('pack','install',str(installed));cli('pack','install',str(installed))
    assert (context/'CLAUDE.md').read_text().count('<!-- bopbop-pack:sample -->')==1
    assert 'sample_count' in cli('pack','tools','sample').stdout
    assert json.loads(cli('pack','call','sample','sample_count','{"window":"24h"}').stdout)['count']==5
    (installed/'tools.json').write_text('{}')
    assert cli('pack','install',str(installed),check=False).returncode!=0
    assert Pack(context/'packs'/'sample').definitions
    assert cli('pack','remove','../../etc',check=False).returncode!=0
    cli('pack','remove','sample')
    assert not (context/'packs'/'sample').exists()
    assert 'bopbop-pack' not in (context/'CLAUDE.md').read_text()


def test_wrapper_configuration_uses_same_registry(installed):
    context=installed.parents[1]
    claude=wrapper_options('claude',context,'test')
    config=json.loads(claude[1])
    assert config['mcpServers']['bopbop_packs']['args'][-1]=='test'
    assert '--append-system-prompt' in claude
    codex=wrapper_options('codex',context,'test')
    assert any('mcp_servers.bopbop_packs.args=' in arg for arg in codex)


def test_profile_from_unrelated_cwd_calls_only_native_tools(installed,tmp_path):
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));seen.append(body)
            message=({'tool_calls':[{'function':{'name':'sample_count','arguments':{'window':'24h'}}}]} if len(seen)==1 else {'content':'Five observations.'})
            output=json.dumps({'message':message,'done':True,'eval_count':3,'prompt_eval_count':20}).encode()+b'\n'
            self.send_response(200);self.send_header('Content-Length',str(len(output)));self.end_headers();self.wfile.write(output)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        env={**os.environ,'BOPBOP_HOME':str(tmp_path/'home'),'BOPBOP_CONTEXT_DIR':str(installed.parents[1]),'BOPBOP_OLLAMA_HOST':f'http://127.0.0.1:{server.server_port}','BOPBOP_DATA_DIR':str(tmp_path/'data')}
        proc=subprocess.run([str(ROOT/'bin/bopbop'),'local','--profile','test','--jsonl','count'],
            cwd='/tmp',env=env,text=True,capture_output=True,check=True,timeout=5)
        output=[json.loads(line) for line in proc.stdout.splitlines()]
        assert [s['function']['name'] for s in seen[0]['tools']]==['sample_count']
        assert seen[0]['think']=='low'
        assert json.loads(seen[1]['messages'][-1]['content'])['count']==5
        assert output[-2]['delta']=='Five observations.'
        assert next(e for e in output if e['kind']=='tool_result')['response_bytes']>0
    finally:server.shutdown();server.server_close();thread.join()


def test_mcp_oversized_record_recovers_and_preserves_request_id(installed):
    requests='x'*70000+'\n'+json.dumps({'jsonrpc':'2.0','id':17,'method':'ping'})+'\n'
    proc=subprocess.run([sys.executable,str(ROOT/'bin/bopbop-pack.py'),'--context-dir',str(installed.parents[1]),
        'mcp','--packs','sample'],input=requests,text=True,capture_output=True,check=True,timeout=5)
    responses=list(map(json.loads,proc.stdout.splitlines()))
    assert responses[0]['error']['message']=='MCP request exceeds 64 KB'
    assert responses[1]=={'jsonrpc':'2.0','id':17,'result':{}}


@pytest.mark.parametrize('harness',['claude','codex','ollama'])
def test_server_harness_receives_selected_pack(installed,monkeypatch,harness):
    import runner
    monkeypatch.setenv('BOPBOP_AGENT_HARNESS',harness)
    monkeypatch.setenv('BOPBOP_AGENT_PROFILE','test')
    monkeypatch.setattr(runner,'CONTEXT_DIR',str(installed.parents[1]))
    _,args=runner._build_args('hello')
    if harness=='ollama':
        assert args[args.index('--profile')+1]=='test'
        assert args[args.index('--context-dir')+1]==str(installed.parents[1])
    else:
        options=wrapper_options(harness,installed.parents[1],'test')
        assert args[1:1+len(options)]==options
        assert args[-1].endswith('hello')


@pytest.mark.parametrize('mode,needle',[
    ('html','non-JSON'),('redirect','non-JSON'),('oversize','exceeds 8 KB'),
    ('schema','Unsupported'),('array','Unsupported'),('nonfinite','compliant'),
    ('auth',None),('ok',None),
])
def test_ice_client_protocol_boundaries(tmp_path,mode,needle):
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path,self.headers.get('Authorization')))
            body={'schema_version':1,'ok':mode!='auth','state':'ready' if mode=='ok' else 'unavailable'}
            raw=json.dumps(body).encode();ctype='application/json';status=401 if mode=='auth' else 200
            if mode=='html':raw=b'<html>Login</html>';ctype='text/html'
            if mode=='redirect':status=302;ctype='text/html'
            if mode=='oversize':raw=b'x'*8193
            if mode=='schema':raw=b'{"schema_version":2}'
            if mode=='array':raw=b'[]'
            if mode=='nonfinite':raw=b'{"schema_version":1,"bad":NaN}'
            self.send_response(status);self.send_header('Content-Type',ctype)
            if mode=='redirect':self.send_header('Location','http://127.0.0.1:1/secret')
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        env={**os.environ,'ICE_DASHBOARD_URL':f'http://127.0.0.1:{server.server_port}','ICE_BOPBOP_TOKEN':'test-token',
             'http_proxy':'http://127.0.0.1:1'}
        proc=subprocess.run([sys.executable,str(ROOT/'packs/ice-dashboard/scripts/ice.py')],
            input=json.dumps({'tool':'ice_query','arguments':{'report':'visits','project':'quizly','window':'7d'}}),
            env=env,text=True,capture_output=True,check=True,timeout=5)
        value=json.loads(proc.stdout)
        assert len(seen)==1 # redirected tokens never leave the configured origin
        assert seen[0][1]=='Bearer test-token'
        assert 'project=quizly' in seen[0][0] and 'window=7d' in seen[0][0]
        if needle:assert value['ok'] is False and needle in value['error']
        else:assert value['ok']==(mode=='ok')
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('url',['file:///etc/passwd','http://user:password@localhost','http://localhost/api',
                                'http://localhost?token=x','http://localhost#fragment'])
def test_ice_client_rejects_origin_credentials_paths_and_non_http(url):
    proc=subprocess.run([sys.executable,str(ROOT/'packs/ice-dashboard/scripts/ice.py')],
        input='{"tool":"ice_status","arguments":{}}',env={**os.environ,'ICE_DASHBOARD_URL':url},
        text=True,capture_output=True,check=True,timeout=5)
    assert json.loads(proc.stdout)['ok'] is False


def load_local():
    spec=importlib.util.spec_from_file_location('bopbop_local_test',ROOT/'bin/bopbop-local.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def test_profile_cannot_dispatch_workspace_actions(installed,tmp_path):
    local=load_local();runtime=local.AgentRuntime(Registry(installed.parents[1],['test']),'test','low',64)
    marker=tmp_path/'must-not-exist'
    for tool,args in [('run_command',{'command':f'touch {marker}'}),('write_file',{'path':str(marker),'content':'bad'}),
                      ('read_file',{'path':'/etc/passwd'})]:
        assert runtime.execute(tmp_path,tool,args).startswith('ERROR:')
    assert not marker.exists()


def test_generation_limit_does_not_publish_partial_answer(installed,tmp_path,monkeypatch):
    local=load_local();runtime=local.AgentRuntime(Registry(installed.parents[1],['test']),'test','low',64)
    stats=local.ModelStats(0,None,0,0,0,0,0,0,0,0);stats.stopped_at_limit=True
    monkeypatch.setattr(local,'chat',lambda *args:({'role':'assistant','content':'Everything is healthy'},stats))
    monkeypatch.setattr(local,'show_stats',lambda *args:None)
    events=[]
    class Quiet:
        def event(self,*args):pass
    messages=[{'role':'system','content':'instructions'},{'role':'user','content':'health?'}]
    answer,_=local.turn('test',messages,tmp_path,8192,Quiet(),emit=events.append,runtime=runtime)
    assert answer.startswith('Stopped at the generation limit')
    assert [e['kind'] for e in events]==['model','error']
    assert len(messages)==2


def test_handler_that_never_reads_large_stdin_still_times_out(installed):
    import time
    path=installed/'tools.json';doc=json.loads(path.read_text())
    doc['tools'][0]['parameters']={'type':'object','additionalProperties':False,'properties':{
        f'field_{n}':{'type':'string','maxLength':4096} for n in range(10)},'required':[]}
    path.write_text(json.dumps(doc));(installed/'handler.py').write_text('import time;time.sleep(5)')
    started=time.monotonic()
    result=Registry(installed.parents[1],['sample']).call('sample_count',{f'field_{n}':'x'*4096 for n in range(10)})
    assert 'deadline' in result['error']
    assert time.monotonic()-started<2


def test_handler_orphan_child_is_cleaned_up(installed,tmp_path):
    import time
    marker=tmp_path/'orphan-wrote-file'
    program=f'import time,pathlib;time.sleep(.4);pathlib.Path({str(marker)!r}).touch()'
    (installed/'handler.py').write_text(f'import subprocess,sys\nsubprocess.Popen([sys.executable,"-c",{program!r}])\nprint("{{\\\"ok\\\":true}}")')
    result=Registry(installed.parents[1],['sample']).call('sample_count',{'window':'24h'})
    assert result['ok'] is True
    time.sleep(.5)
    assert not marker.exists()


def test_resume_rebuilds_system_and_drops_history_after_pack_change(installed,tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    local=load_local();monkeypatch.setenv('BOPBOP_DATA_DIR',str(tmp_path/'data'))
    seen=[]
    def chat(_model,messages,*args):
        seen.append(json.loads(json.dumps(messages)))
        return {'role':'assistant','content':'A completed answer.'},local.ModelStats(0,None,0,0,0,0,0,0,0,0)
    monkeypatch.setattr(local,'chat',chat);monkeypatch.setattr(local,'show_stats',lambda *args:None)
    args=SimpleNamespace(session_id=None,model='test',prompt=['old question'],context=8192)
    runtime=local.AgentRuntime(Registry(installed.parents[1],['test']),'test','low',64)
    local.jsonl_session(args,tmp_path,runtime)
    sid=json.loads(capsys.readouterr().out.splitlines()[0])['session_id']
    args.session_id=sid;args.prompt=['follow up'];local.jsonl_session(args,tmp_path,runtime)
    assert any(m['content']=='old question' for m in seen[-1]);capsys.readouterr()
    (installed/'guide.md').write_text('New instructions. No old evidence may be reused.')
    runtime=local.AgentRuntime(Registry(installed.parents[1],['test']),'test','low',64)
    args.prompt=['new profile question'];local.jsonl_session(args,tmp_path,runtime)
    assert 'New instructions' in seen[-1][0]['content']
    assert all(m['content']!='old question' for m in seen[-1])


def test_history_trimming_preserves_complete_turns_and_tool_pairs(installed,tmp_path,monkeypatch):
    local=load_local();seen=[]
    def chat(_model,messages,*args):
        seen.extend(messages)
        return {'role':'assistant','content':'Answer.'},local.ModelStats(0,None,0,0,0,0,0,0,0,0)
    monkeypatch.setattr(local,'chat',chat);monkeypatch.setattr(local,'show_stats',lambda *args:None)
    class Quiet:
        def event(self,*args):pass
    messages=[{'role':'system','content':'Instructions'}, {'role':'user','content':'old question'},
        {'role':'assistant','content':'','tool_calls':[{'function':{'name':'sample_count','arguments':{'window':'24h'}}}]},
        {'role':'tool','tool_name':'sample_count','content':'x'*20000}, {'role':'user','content':'current question'}]
    local.turn('test',messages,tmp_path,1000,Quiet(),runtime=local.AgentRuntime(Registry(installed.parents[1],['test']),'test','low',64))
    assert [m['role'] for m in seen]==['system','user']
    assert seen[1]['content']=='current question'


def test_profile_tool_budget_blocks_oversized_batch_before_dispatch(installed,tmp_path,monkeypatch):
    local=load_local();runtime=local.AgentRuntime(Registry(installed.parents[1],['test']),'test','low',64)
    monkeypatch.setattr(local,'chat',lambda *args:({'role':'assistant','content':'','tool_calls':[
        {'function':{'name':'sample_count','arguments':{'window':'24h'}}}]*5},local.ModelStats(0,None,0,0,0,0,0,0,0,0)))
    monkeypatch.setattr(local,'show_stats',lambda *args:None)
    monkeypatch.setattr(runtime,'execute',lambda *args:pytest.fail('over-budget tool batch must not execute'))
    class Quiet:
        def event(self,*args):pass
    answer,usage=local.turn('test',[{'role':'user','content':'count'}],tmp_path,8192,Quiet(),runtime=runtime)
    assert answer.startswith('Stopped at the four-tool-call budget');assert usage.tool_calls==0


def test_profile_context_budget_never_sends_oversized_current_turn(installed,tmp_path,monkeypatch):
    local=load_local();runtime=local.AgentRuntime(Registry(installed.parents[1],['test']),'test','low',64)
    monkeypatch.setattr(local,'chat',lambda *args:pytest.fail('over-budget prompt must not call model'))
    class Quiet:
        def event(self,*args):pass
    answer,usage=local.turn('test',[{'role':'user','content':'x'*10000}],tmp_path,1000,Quiet(),runtime=runtime)
    assert answer.startswith('Stopped at the context budget');assert usage.model_calls==0


@pytest.mark.parametrize('harness',['claude','codex'])
def test_wrapper_cli_from_other_directory_preserves_args_and_uses_context(installed,tmp_path,harness):
    binary=tmp_path/'fake-wrapper';binary.write_text('#!/usr/bin/env python3\nimport json,sys,os\nprint(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd()}))\n');binary.chmod(0o755)
    env={**os.environ,'BOPBOP_HOME':str(tmp_path/'home'),'BOPBOP_CONTEXT_DIR':str(installed.parents[1]),
         f'BOPBOP_{harness.upper()}_BIN':str(binary)}
    process=subprocess.run([str(ROOT/'bin/bopbop'),'agent',harness,'--profile','test','--model','example','literal question'],
        cwd='/tmp',env=env,text=True,capture_output=True,check=True,timeout=5)
    output=json.loads(process.stdout)
    assert output['cwd']==str(installed.parents[1])
    assert output['args'][-3:]==['--model','example','literal question']
    assert '--profile' not in output['args']
    import shutil
    # The launcher uses PATH's Python, whereas pytest runs in the server venv.
    expected=[value.replace(json.dumps(sys.executable),json.dumps(shutil.which('python3')))
              for value in wrapper_options(harness,installed.parents[1],'test')]
    assert output['args'][:-3]==expected


def test_install_accepts_standard_toml_quoting_comments_and_profile_default(installed,tmp_path):
    (installed/'pack.toml').write_text("name = 'sample' # inline comment\ndescription = 'Keep # literal'\nfragment = 'guide.md'\ntools = 'tools.json'\n")
    context=tmp_path/'active';context.mkdir()
    env={**os.environ,'BOPBOP_HOME':str(tmp_path/'home'),'BOPBOP_CONTEXT_DIR':str(context)}
    proc=subprocess.run([str(ROOT/'bin/bopbop'),'pack','install',str(installed)],
        env=env,text=True,capture_output=True,check=True,timeout=5)
    assert 'Keep # literal' in proc.stdout
    assert 'bopbop local --profile sample' in proc.stdout
    assert Registry(context,['sample']).call('sample_count',{'window':'24h'})['count']==5
