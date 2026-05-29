"""Tests for the __main__.py module entrypoint."""
import unittest.mock as mock


def test_main_calls_uvicorn_run(monkeypatch):
    monkeypatch.setenv("TODO_BOARD_PORT", "9000")
    with mock.patch("uvicorn.run") as mock_run:
        from todo_board.__main__ import main
        main()
    mock_run.assert_called_once()


def test_main_uses_port_from_env(monkeypatch):
    monkeypatch.setenv("TODO_BOARD_PORT", "9001")
    with mock.patch("uvicorn.run") as mock_run:
        from todo_board.__main__ import main
        main()
    _, kwargs = mock_run.call_args
    assert kwargs.get("port") == 9001


def test_main_uses_default_port_when_env_unset(monkeypatch):
    monkeypatch.delenv("TODO_BOARD_PORT", raising=False)
    with mock.patch("uvicorn.run") as mock_run:
        from todo_board.__main__ import main
        main()
    _, kwargs = mock_run.call_args
    assert kwargs.get("port") == 7842


def test_main_binds_to_all_interfaces(monkeypatch):
    monkeypatch.delenv("TODO_BOARD_PORT", raising=False)
    with mock.patch("uvicorn.run") as mock_run:
        from todo_board.__main__ import main
        main()
    _, kwargs = mock_run.call_args
    assert kwargs.get("host") == "0.0.0.0"
