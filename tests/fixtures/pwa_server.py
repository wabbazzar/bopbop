"""Isolated browser-test server: real API/SQLite/static app, deterministic agent.
No paid model, real workspace, Signal connection, or production DB is used.
"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))

with tempfile.TemporaryDirectory(prefix='bopbop-pwa-test-') as data_dir:
    os.environ.update({
        'BOPBOP_DATA_DIR': data_dir, 'BOPBOP_DB_PATH': str(Path(data_dir) / 'history.db'),
        'BOPBOP_SIGNAL_ENABLED': '0', 'BOPBOP_PWA_ENABLED': '1',
        'BOPBOP_AGENT_HARNESS': 'ollama', 'BOPBOP_OLLAMA_MODEL': 'gpt-oss:20b',
        'BOPBOP_REQUIRE_BEARER': '1', 'BOPBOP_BEARER_TOKEN': 'fixture-access',
        'BOPBOP_CONTEXT_DIR': data_dir, 'BOPBOP_AGENT_PROFILE': '',
    })
    os.environ['BOPBOP_SETTINGS_PATH'] = str(Path(data_dir) / 'agent-settings.json')
    import main
    import settings
    import uvicorn

    async def local_models(): return ['gpt-oss:20b', 'fixture-small:latest']
    settings.local_models = local_models
    settings.available_harnesses = lambda: [{'id': h, 'available': True} for h in settings.HARNESSES]
    settings.profiles = lambda: [{'id': 'ice', 'name': 'ice-dashboard'}]

    @main.app.post('/api/fixture/reset')
    def reset_preferences():
        settings.settings_path().unlink(missing_ok=True)
        return {'ok': True}

    # The production static mount is a catch-all; this test-only route must
    # precede it. Verify the reset response in the browser suite as well.
    main.app.router.routes.insert(0, main.app.router.routes.pop())

    async def fake_turn(prompt, resume_session_id=None):
        yield {'kind': 'session', 'session_id': resume_session_id or 'fixture-agent-session'}
        if 'quiet work' in prompt:
            yield {'kind': 'progress', 'elapsed_ms': 5000}
            await asyncio.sleep(.15)
            yield {'kind': 'progress', 'elapsed_ms': 10000}
            yield {'kind': 'text', 'delta': 'Quiet work finished.'}
            yield {'kind': 'done', 'duration_ms': 10000}
            return
        await asyncio.sleep(1 if 'slow reply' in prompt else .04)
        if 'interrupt stream' in prompt:
            yield {'kind': 'text', 'delta': 'Partial reply'}
            return
        if 'tool failure' in prompt:
            yield {'kind': 'tool', 'name': 'ice_query', 'args': {'report': 'health'}}
            yield {'kind': 'tool_result', 'name': 'ice_query', 'ok': False, 'preview': 'Dashboard unavailable', 'duration_ms': 31}
            yield {'kind': 'error', 'message': 'The dashboard is unavailable. Try again shortly.'}
            return
        if 'unsafe markdown' in prompt:
            yield {'kind': 'text', 'delta': '<emote>happy</emote>**Safe** <img src=x onerror="window.pwned=1"><svg onload="window.pwned=1"></svg><script>window.pwned=1</script> [bad](javascript:alert(1)) [good](https://example.com)'}
        elif 'expression ' in prompt:
            name = prompt.rsplit('expression ', 1)[1].strip()
            yield {'kind': 'text', 'delta': f'<emote>{name}</emote>Expression: {name}'}
        elif 'configuration check' in prompt:
            config = settings.current()
            yield {'kind': 'text', 'delta': f"{config.harness} / {config.model} / {config.profile or 'general'} / {config.ollama_context}"}
        elif 'resume check' in prompt:
            yield {'kind': 'text', 'delta': 'Session resumed.' if resume_session_id else 'Fresh session.'}
        else:
            yield {'kind': 'model', 'step': 1, 'duration_ms': 800, 'prompt_tokens': 1240, 'output_tokens': 41, 'load_ms': 100, 'prompt_ms': 200, 'generation_ms': 500, 'thinking_chars': 120, 'first_chunk_ms': 105, 'first_answer_ms': 250, 'stream_chunks': 18}
            yield {'kind': 'tool', 'name': 'ice_query', 'args': {'report': 'health', 'project': 'public-demo'}, 'id': 'call-1'}
            await asyncio.sleep(.04)
            yield {'kind': 'tool_result', 'name': 'ice_query', 'id': 'call-1', 'ok': True, 'preview': '{"project":"public-demo","state":"healthy"}', 'duration_ms': 120, 'state': 'partial', 'response_bytes': 203, 'api_ms': 110, 'source_age_s': 4, 'cache_hit': False}
            yield {'kind': 'text', 'delta': '<emote>ha'}
            yield {'kind': 'text', 'delta': 'ppy</emote>## Project health\n\n**Public demo** is responding. The activity report is incomplete, so I can’t confirm usage yet.\n\n'}
            yield {'kind': 'text', 'delta': '| Check | Result |\n|---|---|\n| Service | Healthy |\n| Activity | Partial |\n\n[Open dashboard](https://example.com)'}
        yield {'kind': 'done', 'duration_ms': 1050, 'usage': {'input_tokens': 1240, 'output_tokens': 81, 'model_calls': 2, 'tool_calls': 1}}

    main.run_turn = fake_turn
    uvicorn.run(main.app, host='127.0.0.1', port=int(os.environ.get('BOPBOP_TEST_PORT', '8191')), log_level='warning')
