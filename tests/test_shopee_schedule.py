"""Scheduler contracts are synthetic; tests never register an OS task."""
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_core import shopee_schedule as schedule


@pytest.fixture
def setup(tmp_path, monkeypatch):
    s = SimpleNamespace(project_root=tmp_path)
    monkeypatch.setattr(schedule.sys, 'platform', 'win32')
    for relative in ('.venv/Scripts/pythonw.exe', 'scripts/sync-shopee.py'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return s


def test_installs_only_data_runner_with_owner_guard(setup, monkeypatch):
    scripts = []
    monkeypatch.setattr(schedule, '_powershell', lambda script: scripts.append(script) or {'installed': True})
    schedule.install(setup)
    script = scripts[0]
    assert "if($task -and -not $managed)" in script
    assert 'WorkingDirectory -eq $root' in script
    assert 'sync-shopee.py' in script and 'pythonw.exe' in script
    assert 'StartWhenAvailable' in script and 'IgnoreNew' in script
    assert '-LogonType Interactive -RunLevel Limited' in script
    assert '-Force' in script
    assert 'agent_core web' not in script


def test_disable_does_not_invent_installed_task(setup, monkeypatch):
    scripts = []
    monkeypatch.setattr(schedule, '_powershell', lambda script: scripts.append(script) or {'installed': False, 'enabled': False})
    result = schedule.install(setup, enabled=False)
    assert result['installed'] is False
    assert 'installed=[bool]$task' in scripts[0]
    assert 'Register-ScheduledTask' not in scripts[0]


@pytest.mark.parametrize('time', ['24:00', '7:00', '07:99', '07:00;exit'])
def test_invalid_schedule_does_not_execute(setup, monkeypatch, time):
    monkeypatch.setattr(schedule, '_powershell', lambda _: pytest.fail('Unexpected OS call'))
    with pytest.raises(schedule.ScheduleError):
        schedule.install(setup, local_time=time)


def test_os_error_never_exposes_command_output(setup, monkeypatch):
    def execute(command, **kwargs):
        script = base64.b64decode(command[-1]).decode('utf-16le')
        assert 'Get-ScheduledTask' in script
        assert kwargs['timeout'] == 30
        return SimpleNamespace(returncode=1, stdout='PRIVATE', stderr='PRIVATE')
    monkeypatch.setattr(schedule.subprocess, 'run', execute)
    result = schedule.status(setup)
    assert result['installed'] is False
    assert 'PRIVATE' not in str(result)


def test_runner_import_does_not_start_workers():
    path = Path(__file__).parents[1] / 'scripts/sync-shopee.py'
    spec = importlib.util.spec_from_file_location('shopee_schedule_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
    source = path.read_text(encoding='utf-8')
    assert 'Worker' not in source and 'create_app' not in source
