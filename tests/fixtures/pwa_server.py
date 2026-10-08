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
    import main
    import uvicorn

    async def fake_turn(prompt, resume_session_id=None):
        yield {'kind': 'session', 'session_id': resume_session_id or 'fixture-agent-session'}
        await asyncio.sleep(.04)
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
