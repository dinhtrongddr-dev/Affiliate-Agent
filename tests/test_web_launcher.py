"""A second UI launch must never create or start duplicate app workers."""
import socket
from unittest.mock import Mock

import pytest

from agent_core.web import launcher


@pytest.fixture
def occupied():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        yield listener.getsockname()[1]


def test_second_launch_opens_existing_app_without_creating_workers(occupied, monkeypatch):
    factory, browser = Mock(), Mock()
    monkeypatch.setattr(launcher, '_is_existing_app', lambda url: True)
    monkeypatch.setattr(launcher.webbrowser, 'open', browser)
    launcher.serve(factory, '127.0.0.1', occupied)
    factory.assert_not_called()
    browser.assert_called_once_with(f'http://127.0.0.1:{occupied}')


def test_second_headless_launch_does_not_open_browser(occupied, monkeypatch):
    factory, browser = Mock(), Mock()
    monkeypatch.setattr(launcher, '_is_existing_app', lambda url: True)
    monkeypatch.setattr(launcher.webbrowser, 'open', browser)
    launcher.serve(factory, '127.0.0.1', occupied, open_browser=False)
    factory.assert_not_called()
    browser.assert_not_called()


def test_other_application_on_port_is_not_reused(occupied, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(launcher, '_is_existing_app', lambda url: False)
    with pytest.raises(RuntimeError, match='Không mở được app'):
        launcher.serve(factory, '127.0.0.1', occupied, open_browser=False)
    factory.assert_not_called()


def test_socket_reserved_before_app_creation_and_passed_to_server(monkeypatch):
    app = object()
    seen = []
    server = Mock()
    def config(candidate, **kwargs):
        assert candidate is app
        seen.append(kwargs)
        return 'config'
    monkeypatch.setattr(launcher.uvicorn, 'Config', config)
    monkeypatch.setattr(launcher.uvicorn, 'Server', lambda _: server)
    launcher.serve(lambda: app, '127.0.0.1', 0, open_browser=False)
    reserved = server.run.call_args.kwargs['sockets'][0]
    assert seen[0]['host'] == '127.0.0.1'
    assert reserved.fileno() == -1  # released when the server finishes
