"""Cancellable local-agent turns; owns the child process and event transport."""
from __future__ import annotations

import contextlib
import io
import multiprocessing
import os
import queue
import signal
import time

from bopbop_editor import editor_prompt, edit_result


class EventWriter(io.TextIOBase):
    """Convert worker output into events without redirecting the UI process."""
    def __init__(self, send):
        self.send = send

    def write(self, text):
        if text:
            self.send({'kind': 'output', 'text': text})
        return len(text)

    def flush(self):
        pass


def run_worker(engine, args, runtime, messages, session, prompt, events, editor=None):
    os.setsid()
    def stop(signum, frame):
        # Unwind native pack handlers so their own process groups are reaped.
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    send = events.put
    streamed = False
    def observe(event):
        nonlocal streamed
        if event['kind'] == 'request':
            streamed = False
        elif event['kind'] == 'stream' and event.get('delta'):
            streamed = True
        send(event)
    runtime.observer = observe
    writer = EventWriter(send)
    try:
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            class WorkerDisplay(engine.Display):
                def answer(self, value):
                    if not streamed:
                        super().answer(value)
            display = WorkerDisplay(False)
            if editor:
                runtime.review_only = True
                runtime.response_format = ({'type': 'object', 'properties': {
                    'replacement': {'type': 'string'}, 'summary': {'type': 'string'}},
                    'required': ['replacement', 'summary']} if editor['action'] == 'edit' else None)
                prompt = editor_prompt(editor)
            elif engine.handle_command(prompt, messages, session, args.model, args.context,
                                       args.workspace, display, runtime):
                return
            messages.append({'role': 'user', 'content': prompt})
            answer, usage = engine.turn(args.model, messages, args.workspace, args.context,
                                        display, runtime=runtime)
            session.record(usage)
            if editor:
                send({'kind': 'editor_result', 'result': edit_result(answer, editor)})
            else:
                display.answer(answer)
    except KeyboardInterrupt:
        send({'kind': 'output', 'text': '\nTurn cancelled.\n'})
        if editor:
            send({'kind': 'editor_result', 'error': 'Cancelled; selection and prompt retained'})
    except Exception as exc:
        send({'kind': 'output', 'text': f'\nError: {type(exc).__name__}: {exc}\n'})
        if editor:
            send({'kind': 'editor_result', 'error': str(exc)})
    finally:
        send({'kind': 'finished', 'messages': messages, 'session': session})


class TurnWorker:
    """One turn's process, event queue, and lifetime; no terminal dependencies."""

    def __init__(self, engine, args, runtime, messages, session, prompt, editor=None):
        context = multiprocessing.get_context('fork')
        self.events = context.Queue()
        self.process = context.Process(target=run_worker, args=(
            engine, args, runtime, messages, session, prompt, self.events, editor))
        self.started = time.monotonic()
        self.closed = False
        try:
            self.process.start()
        except BaseException:
            self.close()
            raise

    @property
    def elapsed(self):
        return time.monotonic() - self.started

    def poll(self, limit=100):
        events = []
        if self.closed:
            return events
        for _ in range(limit):
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                break
        # After the process exits its queue feeder has flushed. Wait until a
        # whole poll is empty so a queued completion is consumed before this.
        if not events and not self.process.is_alive():
            events.append({'kind': 'worker_exit', 'exit_code': self.process.exitcode})
        return events

    def close(self, cancel=False):
        if self.closed:
            return
        if cancel and self.process.is_alive():
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                self.process.terminate()
        if self.process.pid:
            self.process.join(timeout=.5)
        for action in (self.process.terminate, self.process.kill):
            if self.process.is_alive():
                action()
                self.process.join(timeout=.5)
        self.process.close()
        self.events.close()
        self.closed = True
