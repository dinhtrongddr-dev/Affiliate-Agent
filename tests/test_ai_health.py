"""The dashboard's AI row follows the latest real AI call, not just whether a key is set."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_core import ai_client
from agent_core.ai_client import AIClient, AIClientError
from agent_core.settings import Settings


def _client():
    return AIClient("http://127.0.0.1:13000/v1", "k", "gpt-6-thinking")


def test_failed_call_is_remembered_and_explained():
    no_account = AIClientError('AI endpoint returned HTTP 502: {"error":{"message":"no available text account"}}')
    with patch.object(AIClient, "_openai_chat", side_effect=no_account):
        try:
            _client().chat([{"role": "user", "content": "x"}])
        except AIClientError:
            pass
    name, hint = ai_client.health_problem()
    assert "chưa có tài khoản ChatGPT" in name and "127.0.0.1:13000" in hint
    with patch.object(AIClient, "_openai_chat", return_value={"role": "assistant", "content": "ok"}):
        _client().chat([{"role": "user", "content": "x"}])
    assert ai_client.health_problem() is None


def test_dashboard_shows_the_ai_problem_visibly(tmp_path, monkeypatch):
    from agent_core.web import app as web
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                 media_root=tmp_path / "media", muse_embedded=False, ai_api_key="k")
    monkeypatch.setattr(web, "get_settings", lambda: s)
    ai_client._record(False, 'HTTP 502: {"error":{"message":"no available text account"}}')
    try:
        with patch.object(web, "setup_logging"), TestClient(web.create_app(start_worker=False)) as c:
            html = c.get("/").text
        assert "chưa có tài khoản ChatGPT đăng nhập" in html and "Mở http://127.0.0.1:13000" in html
    finally:
        ai_client._record(True)
