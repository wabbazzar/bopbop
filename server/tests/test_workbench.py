"""The actual workbench: one pane, retained buffers, declared tools and exact evidence."""
import json
import time

from test_cli_tools import installed
from test_editor import editor_model
from test_tui import TmuxTerminal


def state(term, path):
    # Inspect native editor state after driving it with user keys.
    path.unlink(missing_ok=True)
    expression = 'vim.fn.writefile({vim.json.encode({windows=#vim.api.nvim_list_wins(),'
    expression += 'modified=vim.bo.modified,line=vim.fn.line("."),name=vim.api.nvim_buf_get_name(0),'
    expression += 'lines=vim.api.nvim_buf_get_lines(0,0,-1,true)})},' + json.dumps(str(path)) + ')'
    term.send(':lua ' + expression, pane='1'); term.key('Enter', pane='1')
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(.02)
    return json.loads(path.read_text())


def test_one_workbench_retains_dirty_files_cursor_and_undo(installed, tmp_path):
    first, second = tmp_path / 'first.py', tmp_path / 'second.py'
    first.write_text('print(1)\n' * 20)
    second.write_text('print(2)\n' * 20)
    term = TmuxTerminal(installed, tmp_path)
    try:
        term.wait('bopbop>'); term.send('/editor first.py'); term.key('Enter')
        term.wait('connected', pane='1')
        term.send('ggI# unsaved ', pane='1'); term.key('Escape', pane='1')
        term.send('8G', pane='1')
        term.key('F2', pane='1'); term.wait('type to filter', pane='1')
        term.send('sample_count', pane='1'); term.key('Enter', pane='1')
        term.wait('Observation window', pane='1')
        term.key('F5', pane='1'); term.wait('File ·', pane='1')
        value = state(term, tmp_path / 'state.json')
        assert value['modified'] and value['line'] == 8 and value['windows'] == 1
        assert value['lines'][0] == '# unsaved print(1)'
        term.key('F6', pane='1'); term.wait_active('0')
        term.send('/inspect file second.py'); term.key('Enter')
        term.wait('FILE ·', pane='1')
        term.tmux('select-pane', '-t', 'ride:0.1')
        term.send('14Ge', pane='1')  # snapshot header is five lines; edit at source line nine
        term.wait('File ·', pane='1', absent='FILE ·')
        value = state(term, tmp_path / 'state.json')
        assert value['line'] == 9 and value['windows'] == 1 and value['name'] == str(second)
        term.send('ggI# second unsaved ', pane='1'); term.key('Escape', pane='1')
        term.key('F9', pane='1'); term.wait_active('0')
        term.key('F9'); term.wait_active('1'); term.wait('# second unsaved', pane='1')
        term.key('F6', pane='1'); term.wait_active('0')
        term.send('/editor first.py'); term.key('Enter')
        term.wait_active('1'); term.wait('# unsaved print(1)', pane='1')
        assert len(term.tmux('list-panes', '-t', 'ride:0', '-F', '#{pane_id}').splitlines()) == 2
        value = state(term, tmp_path / 'state.json')
        assert value['windows'] == 1 and value['modified']
        term.send('u', pane='1')
        value = state(term, tmp_path / 'state.json')
        assert value['lines'] == first.read_text().splitlines()
        assert second.read_text() == 'print(2)\n' * 20
        assert not (installed / 'called').exists()
    finally:
        term.close()


def test_large_snapshot_pages_without_truncation_and_asks_only_selected_evidence(installed, tmp_path, editor_model):
    url, received = editor_model
    (tmp_path / 'source.py').write_text('print(42)\n')
    (tmp_path / 'large.txt').write_text('x\n' * 29999 + 'END\n')
    term = TmuxTerminal(installed, tmp_path, extra_env={'BOPBOP_OLLAMA_HOST': url})
    try:
        term.wait('bopbop>'); term.send('/editor source.py'); term.key('Enter')
        term.wait('connected', pane='1')
        term.key('F6', pane='1'); term.wait_active('0')
        term.send('/inspect file large.txt'); term.key('Enter')
        term.wait('FILE ·', pane='1')
        term.tmux('select-pane', '-t', 'ride:0.1')
        term.send('/END', pane='1'); term.key('Enter', pane='1')
        term.wait('30000 │ END', pane='1')
        term.send('V', pane='1'); term.key('F8', pane='1')
        term.wait('Attached')
        term.send('\\be', pane='1'); term.wait('shared session / no tools:', pane='1')
        term.send('What does this marker mean?', pane='1'); term.key('Enter', pane='1')
        term.wait('BopBop · answer · q close', pane='1')
        assert len(received) == 1 and received[0]['tools'] == []
        content = received[0]['messages'][-1]['content']
        assert '"text": "END"' in content and '"mode": "evidence"' in content
        assert '"range": [29999, 0, 30000, 0]' in content
        term.send('q', pane='1'); term.wait('30000 │ END', pane='1')
        assert state(term, tmp_path / 'state.json')['windows'] == 1
    finally:
        term.close()
