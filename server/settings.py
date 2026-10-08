"""Persisted agent preferences, with a frozen configuration per running turn.

Only model/harness/profile/context are editable. Credentials, executable paths,
network endpoints, and workspace authority remain in the installation env.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

HARNESSES = ('claude', 'codex', 'ollama')
MODEL_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/+\[\]-]{0,127}$')


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    harness: Literal['claude', 'codex', 'ollama']
    models: dict[str, str]
    profile: str = Field(default='', pattern=r'^(?:[a-z0-9][a-z0-9-]*)?$')
    ollama_context: int = Field(default=16384, ge=2048, le=131072, strict=True)

    @field_validator('models')
    @classmethod
    def valid_models(cls, models):
        if set(models) != set(HARNESSES):
            raise ValueError('models must cover each supported harness')
        for harness, model in models.items():
            if model and not MODEL_NAME.fullmatch(model):
                raise ValueError('invalid model name')
            if harness == 'ollama' and not model:
                raise ValueError('select an installed Ollama model')
        return models


class SettingsChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    harness: Literal['claude', 'codex', 'ollama']
    model: str = Field(max_length=128)
    profile: str = Field(pattern=r'^(?:[a-z0-9][a-z0-9-]*)?$')
    ollama_context: int = Field(ge=2048, le=131072, strict=True)
    revision: str = Field(pattern=r'^[0-9a-f]{64}$')

    @field_validator('model')
    @classmethod
    def valid_model(cls, value):
        if value and not MODEL_NAME.fullmatch(value):
            raise ValueError('invalid model name')
        return value


@dataclass(frozen=True)
class AgentConfig:
    harness: str
    model: str
    profile: str
    ollama_context: int

    @property
    def session_key(self):
        return digest(self.__dict__)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def settings_path():
    data = Path(os.path.expanduser(os.environ.get('BOPBOP_DATA_DIR', '~/.bopbop/data')))
    return Path(os.path.expanduser(os.environ.get('BOPBOP_SETTINGS_PATH', str(data / 'agent-settings.json'))))


def defaults():
    harness = os.environ.get('BOPBOP_AGENT_HARNESS', 'claude').strip().lower()
    if harness not in HARNESSES:
        raise ValueError(f'unsupported BOPBOP_AGENT_HARNESS={harness!r}')
    return Preferences(
        harness=harness,
        models={
            'claude': os.environ.get('BOPBOP_CLAUDE_MODEL', 'sonnet').strip(),
            'codex': os.environ.get('BOPBOP_CODEX_MODEL', '').strip(),
            'ollama': os.environ.get('BOPBOP_OLLAMA_MODEL', 'gpt-oss:20b').strip() or 'gpt-oss:20b',
        },
        profile=os.environ.get('BOPBOP_AGENT_PROFILE', '').strip(),
        ollama_context=int(os.environ.get('BOPBOP_OLLAMA_CONTEXT', '16384')),
    )


def load():
    path = settings_path()
    if not path.exists():
        return defaults()
    raw = path.read_bytes()
    if len(raw) > 8192:
        raise ValueError('Saved agent settings exceed the size limit')
    return Preferences.model_validate_json(raw)


def snapshot():
    config = load()
    return AgentConfig(config.harness, config.models[config.harness], config.profile, config.ollama_context)


_active: ContextVar[AgentConfig | None] = ContextVar('bopbop_agent_config', default=None)


def current():
    return _active.get() or snapshot()


@contextmanager
def use(config):
    token = _active.set(config)
    try:
        yield config
    finally:
        _active.reset(token)


def public(config):
    values = config.model_dump()
    return {**values, 'model': config.models[config.harness], 'revision': digest(values)}


def available_harnesses():
    return [
        {'id': harness, 'available': harness == 'ollama' or bool(shutil.which(
            os.environ.get(f'BOPBOP_{harness.upper()}_BIN', harness)))}
        for harness in HARNESSES
    ]


def profiles():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
    from bopbop_packs import Pack, Registry
    context = Path(os.path.expanduser(os.environ.get('BOPBOP_CONTEXT_DIR', '~/.bopbop/context')))
    found = []
    for manifest in sorted((context / 'packs').glob('*/pack.toml')):
        try:
            pack = Pack(manifest.parent)
            if pack.definitions:
                Registry(context, [pack.profile])  # excludes ambiguous/invalid profiles
                found.append({'id': pack.profile, 'name': pack.name})
        except (ValueError, OSError):
            logging.getLogger('bopbop').warning('Skipping invalid pack in settings catalog')
    return found


async def local_models():
    url = os.environ.get('BOPBOP_OLLAMA_HOST', 'http://127.0.0.1:11434').rstrip('/')
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        response = await client.get(url + '/api/tags')
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise ValueError('Invalid Ollama model catalog')
    return sorted({m['name'] for m in data['models'] if isinstance(m, dict) and isinstance(m.get('name'), str) and MODEL_NAME.fullmatch(m['name'])})


class SettingsConflict(ValueError):
    pass


def save(change):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Protect optimistic updates across processes as well as concurrent requests.
    lock_fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = load()
        if digest(existing.model_dump()) != change.revision:
            raise SettingsConflict('Settings changed elsewhere. Reload settings before saving.')
        values = existing.model_dump()
        values.update(harness=change.harness, profile=change.profile, ollama_context=change.ollama_context)
        values['models'][change.harness] = change.model
        updated = Preferences.model_validate(values)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
                temporary = Path(file.name)
                os.chmod(file.name, 0o600)
                file.write(updated.model_dump_json(indent=2) + '\n')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return updated
