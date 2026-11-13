"""Regression tests for the step-1 bugs fixed in upgrade phase 0."""
import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_core import db, workflow
from agent_core.quality import evaluate_creative_quality
from agent_core.settings import Settings
from agent_core.web import app as web

HEADERS = {'X-Requested-With': 'affiliate-agent'}


@pytest.fixture
def local(tmp_path, monkeypatch):
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / 'test.sqlite3',
                 media_root=tmp_path / 'media', muse_embedded=False)
    monkeypatch.setattr(web, 'get_settings', lambda: s)
    app = web.create_app(start_worker=False)
    with patch.object(web, 'setup_logging'), TestClient(app) as client:
        yield client, s


def _job(sha='a' * 64, mode='base'):
    return {'id': sha[:24], 'source_path': 'x.mp4', 'sha256': sha, 'file_name': 'x.mp4', 'mode': mode}


# 1 -- a deleted video must not come back on the next inbox scan
def test_deleted_video_is_not_resurrected(tmp_path):
    path = tmp_path / 'db.sqlite3'
    assert db.register_job(path, _job())
    db.delete_job(path, 'a' * 24)
    assert not db.register_job(path, _job())
    assert db.get_job(path, 'a' * 24) is None
    # a deliberate re-upload brings it back
    db.forget_deleted_source(path, 'a' * 64)
    assert db.register_job(path, _job())


def test_deleted_trend_job_does_not_block_others(tmp_path):
    path = tmp_path / 'db.sqlite3'
    assert db.register_job(path, _job('b' * 64, mode='trend'))
    db.delete_job(path, 'b' * 24)
    assert db.register_job(path, _job('b' * 64, mode='trend'))


# 3 -- malformed AI JSON is repaired instead of failing the job
@pytest.mark.parametrize('raw', [
    '```json\n{"a": 1, "b": [1, 2,],}\n```',
    'Đây là kết quả: {"a": 1, "b": [1, 2]} chúc vui',
    '{"a": 1, "b": [1, 2], "c": "cắt ngang',
])
def test_repair_json_locally(raw):
    value = workflow._repair_json_locally(raw)
    assert value['a'] == 1 and value['b'] == [1, 2]


class _Client:
    def __init__(self, replies):
        self.replies, self.calls, self.timeout = list(replies), [], 180

    def chat(self, messages, temperature=0.4, max_tokens=2400):
        self.calls.append((messages, self.timeout))
        return {'content': self.replies.pop(0)}


def test_ask_json_asks_ai_to_fix_unrepairable_reply():
    client = _Client(['không có json nào', '{"ok": true}'])
    settings = Settings(_env_file=None)
    assert workflow._ask_json(client, settings, 'p') == {'ok': True}
    assert len(client.calls) == 2


def test_ask_json_extends_timeout_for_long_storyboards():
    client = _Client(['{"ok": 1}'])
    workflow._ask_json(client, Settings(_env_file=None), 'p', max_tokens=7000)
    assert client.calls[0][1] >= 420 and client.timeout == 180


# 4 -- AI review (/10) and QA (/100) live in different columns
def test_old_qa_total_moves_out_of_viral_score(tmp_path):
    path = tmp_path / 'db.sqlite3'
    db.register_job(path, _job())
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE jobs SET viral_score=96.5 WHERE id=?", ('a' * 24,))
    db._initialised.clear()
    db.init_db(path)
    job = db.get_job(path, 'a' * 24)
    assert job['viral_score'] is None and job['qa_score'] == 96.5


# 5 -- QA can no longer pass a 5 s video or credit missing music
def test_qa_penalises_wrong_duration_and_missing_music():
    timeline = {'hook': 'Mẹ ơi con đây', 'music_mood': 'happy', 'scenes': [
        {'voiceover': 'vui', 'duration': 2, 'shot': {'action': 'x'}} for _ in range(4)]}
    good = evaluate_creative_quality(timeline, branch='viral_creator', rendered_duration=22,
                                     music_used=True)
    short = evaluate_creative_quality(timeline, branch='viral_creator', rendered_duration=5.3,
                                      music_used=False)
    assert short.total < good.total - 15
    assert short.status in {'NEEDS_REVISION', 'FAIL'}
    assert 'Video không có nhạc nền' in short.improvements


# 7, 8 -- input validation
def test_empty_trend_title_is_400(local):
    c, _ = local
    assert c.post('/api/trends', json={'title': '  '}, headers=HEADERS).status_code == 400


def test_unknown_clip_is_404(local):
    c, s = local
    db.register_job(s.database_path, _job())
    r = c.post(f"/api/jobs/{'a' * 24}/clips/nope", json={'note': 'x'}, headers=HEADERS)
    assert r.status_code == 404
    r = c.post(f"/api/jobs/{'a' * 24}/clips/nope/delete", headers=HEADERS)
    assert r.status_code == 404


def test_empty_csv_is_400(local):
    c, _ = local
    r = c.post('/api/intelligence/import-csv', headers=HEADERS,
               files={'file': ('x.csv', b'', 'text/csv')})
    assert r.status_code == 400


# 9 -- Task Scheduler status is cached
def test_schedule_status_is_cached(monkeypatch, tmp_path):
    from agent_core import shopee_schedule
    calls = []
    monkeypatch.setattr(shopee_schedule, '_status_uncached',
                        lambda s: calls.append(1) or {'installed': False})
    shopee_schedule._STATUS_CACHE.clear()
    s = Settings(_env_file=None, project_root=tmp_path)
    shopee_schedule.status(s)
    shopee_schedule.status(s)
    assert len(calls) == 1
    shopee_schedule.status(s, fresh=True)
    assert len(calls) == 2


def test_ai_wait_jobs_are_requeued(tmp_path):
    from agent_core.worker import Worker
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / 'db.sqlite3')
    db.register_job(s.database_path, _job())
    db.update_job(s.database_path, 'a' * 24, status='WAITING_FOR_AI_SERVICE')
    w = Worker(s, watch=False)
    w._requeue_ai_waits()
    with db.connect(s.database_path) as conn:
        rows = conn.execute("SELECT kind FROM tasks WHERE job_id=?", ('a' * 24,)).fetchall()
    assert [r['kind'] for r in rows] == ['process']
    assert json.dumps(w.last_run['_ai_retry_counts'])
