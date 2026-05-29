"""Unit tests for worker._api, _load_sessions, _save_sessions, _invoke_claude, main."""
import importlib
import io
import json
import sys
import time
import unittest.mock as mock
import pytest


def _mock_popen(stdout_text="", returncode=0, stderr_text=""):
    proc = mock.MagicMock()
    proc.stdout = io.StringIO(stdout_text)
    proc.stderr.read.return_value = stderr_text
    proc.returncode = returncode
    proc.wait.return_value = None
    return proc


def _result_event(result_text="done", session_id=None, subtype="success", usage=None):
    return json.dumps({
        "type": "result",
        "result": result_text,
        "session_id": session_id,
        "subtype": subtype,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }) + "\n"


def _make_todo(id=1, text="Test task", status="in_progress", project_id=1, **kwargs):
    t = {
        "id": id, "text": text, "done": False, "status": status,
        "created": int(time.time()), "project_id": project_id,
        "note": None, "status_updated_at": int(time.time()),
    }
    t.update(kwargs)
    return t


# ── _api ──────────────────────────────────────────────────────────────────────

def test_api_makes_post_request():
    import todo_board.worker as worker
    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        worker._api("/api/status/1", {"status": "done"})
    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert req.method == "POST"
    assert "/api/status/1" in req.full_url


def test_api_sends_json_body():
    import todo_board.worker as worker
    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        worker._api("/api/note/1", {"note": "hello"})
    req = mock_urlopen.call_args[0][0]
    assert json.loads(req.data) == {"note": "hello"}


def test_api_handles_exception_silently(capsys):
    import todo_board.worker as worker
    with mock.patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
        worker._api("/api/status/1", {"status": "done"})  # must not raise
    captured = capsys.readouterr()
    assert "API call failed" in captured.err


# ── _load_sessions ────────────────────────────────────────────────────────────

def test_worker_load_sessions_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)
    assert worker._load_sessions() == {}


def test_worker_load_sessions_reads_valid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)
    sessions = {"1": "sess-abc"}
    (tmp_path / "sessions.json").write_text(json.dumps(sessions))
    assert worker._load_sessions() == sessions


def test_worker_load_sessions_empty_on_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)
    (tmp_path / "sessions.json").write_text("{invalid{{")
    assert worker._load_sessions() == {}


# ── _save_sessions ────────────────────────────────────────────────────────────

def test_worker_save_sessions_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)
    sessions = {"3": "sess-xyz"}
    worker._save_sessions(sessions)
    written = json.loads((tmp_path / "sessions.json").read_text())
    assert written == sessions


def test_worker_save_sessions_handles_write_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)
    with mock.patch("pathlib.Path.write_text", side_effect=OSError("no space")):
        worker._save_sessions({"1": "sess"})  # must not raise
    captured = capsys.readouterr()
    assert "Failed to save sessions" in captured.err


# ── _invoke_claude ─────────────────────────────────────────────────────────────

def test_invoke_claude_parses_result_event():
    from todo_board.worker import _invoke_claude

    stdout = _result_event("all done", session_id="sess-42", subtype="success",
                           usage={"input_tokens": 100, "output_tokens": 50,
                                  "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5})
    with mock.patch("subprocess.Popen", return_value=_mock_popen(stdout)):
        rc, lines, final, subtype, tokens, session_id, stderr = _invoke_claude(["claude", "-p", "task"], 1)

    assert rc == 0
    assert final == "all done"
    assert session_id == "sess-42"
    assert subtype == "success"
    assert tokens["input"] == 100
    assert tokens["output"] == 50
    assert tokens["cache_creation"] == 10
    assert tokens["cache_read"] == 5


def test_invoke_claude_captures_assistant_text_lines():
    from todo_board.worker import _invoke_claude

    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "STATUS: doing work\nSome output text"}]},
    }
    stdout = json.dumps(event) + "\n"
    with mock.patch("subprocess.Popen", return_value=_mock_popen(stdout)):
        _, lines, _, _, _, _, _ = _invoke_claude(["claude"], 1)

    assert "STATUS: doing work" in lines
    assert "Some output text" in lines


def test_invoke_claude_status_calls_progress_api():
    from todo_board.worker import _invoke_claude

    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "STATUS: running tests"}]},
    }
    stdout = json.dumps(event) + "\n"
    with mock.patch("subprocess.Popen", return_value=_mock_popen(stdout)):
        with mock.patch("todo_board.worker._api") as mock_api:
            _invoke_claude(["claude"], 7)

    progress_calls = [c for c in mock_api.call_args_list if "/api/progress/7" in c[0][0]]
    assert len(progress_calls) == 1
    assert "running tests" in progress_calls[0][0][1]["text"]


def test_invoke_claude_skips_empty_text_lines():
    from todo_board.worker import _invoke_claude

    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Line 1\n\n\nLine 2\n   "}]},
    }
    stdout = json.dumps(event) + "\n"
    with mock.patch("subprocess.Popen", return_value=_mock_popen(stdout)):
        _, lines, _, _, _, _, _ = _invoke_claude(["claude"], 1)

    assert "" not in lines


def test_invoke_claude_captures_non_json_lines_verbatim():
    from todo_board.worker import _invoke_claude

    stdout = "raw text line\nanother line\n"
    with mock.patch("subprocess.Popen", return_value=_mock_popen(stdout)):
        _, lines, final, _, _, _, _ = _invoke_claude(["claude"], 1)

    assert "raw text line" in lines
    assert final is None


def test_invoke_claude_returns_stderr_on_failure():
    from todo_board.worker import _invoke_claude

    with mock.patch("subprocess.Popen", return_value=_mock_popen("", returncode=1, stderr_text="fatal error")):
        rc, _, _, _, _, _, stderr = _invoke_claude(["claude"], 1)

    assert rc == 1
    assert "fatal error" in stderr


def test_invoke_claude_returns_empty_tokens_when_no_usage():
    from todo_board.worker import _invoke_claude

    event = {"type": "result", "result": "done", "session_id": None, "subtype": "success", "usage": {}}
    stdout = json.dumps(event) + "\n"
    with mock.patch("subprocess.Popen", return_value=_mock_popen(stdout)):
        _, _, _, _, tokens, _, _ = _invoke_claude(["claude"], 1)

    assert tokens["input"] == 0
    assert tokens["output"] == 0


# ── main ──────────────────────────────────────────────────────────────────────

def test_worker_main_exits_without_args(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)
    monkeypatch.setattr(sys, "argv", ["worker.py"])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 1


def test_worker_main_exits_when_todo_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)
    (tmp_path / "todos.json").write_text(json.dumps([]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "99"])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 1


def test_worker_main_skips_already_done_todo(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)
    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=1, status="done")]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "1"])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 0


def test_worker_main_marks_done_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=1)]))
    (tmp_path / "projects.json").write_text(json.dumps([{"id": 1, "name": "General"}]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "1"])

    stdout = _result_event("Task completed", session_id="sess-x")
    with mock.patch("subprocess.Popen", side_effect=lambda *a, **k: _mock_popen(stdout)):
        with mock.patch("todo_board.worker._api") as mock_api:
            worker.main()

    done_calls = [c for c in mock_api.call_args_list
                  if c[0][0] == "/api/status/1" and c[0][1].get("status") == "done"]
    assert len(done_calls) == 1


def test_worker_main_marks_failed_on_failed_output(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=2)]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "2"])

    stdout = _result_event("FAILED:could not complete")
    with mock.patch("subprocess.Popen", side_effect=lambda *a, **k: _mock_popen(stdout)):
        with mock.patch("todo_board.worker._api") as mock_api:
            worker.main()

    failed_calls = [c for c in mock_api.call_args_list
                    if c[0][0] == "/api/status/2" and c[0][1].get("status") == "failed"]
    assert len(failed_calls) == 1


def test_worker_main_marks_context_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=3)]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "3"])

    stdout = _result_event("", subtype="error_context_window")
    with mock.patch("subprocess.Popen", side_effect=lambda *a, **k: _mock_popen(stdout)):
        with mock.patch("todo_board.worker._api") as mock_api:
            worker.main()

    limit_calls = [c for c in mock_api.call_args_list
                   if c[0][0] == "/api/status/3" and c[0][1].get("status") == "context_limit"]
    assert len(limit_calls) == 1


def test_worker_main_uses_project_name_in_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=1, project_id=5)]))
    (tmp_path / "projects.json").write_text(json.dumps([{"id": 5, "name": "MySpecialProject"}]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "1"])

    stdout = _result_event("done")
    with mock.patch("subprocess.Popen") as mock_popen:
        mock_popen.side_effect = lambda *a, **k: _mock_popen(stdout)
        with mock.patch("todo_board.worker._api"):
            worker.main()

    call_args = mock_popen.call_args[0][0]
    prompt_idx = call_args.index("-p") + 1
    assert "MySpecialProject" in call_args[prompt_idx]


def test_worker_main_posts_questions_when_waiting(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=4)]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "4"])

    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "QUESTION: What color?\nOPTION: Red\nOPTION: Blue\nWAITING_FOR_ANSWERS"}]},
    }
    stdout = json.dumps(event) + "\n"
    with mock.patch("subprocess.Popen", side_effect=lambda *a, **k: _mock_popen(stdout)):
        with mock.patch("todo_board.worker._api") as mock_api:
            worker.main()

    question_calls = [c for c in mock_api.call_args_list if "/api/questions/4" in c[0][0]]
    assert len(question_calls) == 1
    questions = question_calls[0][0][1]["questions"]
    assert questions[0]["question"] == "What color?"
    assert "Red" in questions[0]["options"]


def test_worker_main_falls_back_to_cold_on_stale_session(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=5, project_id=1)]))
    (tmp_path / "sessions.json").write_text(json.dumps({"1": "stale-session"}))
    monkeypatch.setattr(sys, "argv", ["worker.py", "5"])

    cold_stdout = _result_event("cold done", session_id="new-sess")

    call_count = 0

    def make_proc(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_popen("", returncode=1, stderr_text="session expired")
        return _mock_popen(cold_stdout)

    with mock.patch("subprocess.Popen", side_effect=make_proc):
        with mock.patch("todo_board.worker._api"):
            worker.main()

    assert call_count == 2


def test_worker_main_uses_prev_task_result(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    todos = [
        _make_todo(id=10, status="done", result="output from step 1"),
        _make_todo(id=11, status="in_progress", prev_task_id=10),
    ]
    (tmp_path / "todos.json").write_text(json.dumps(todos))
    monkeypatch.setattr(sys, "argv", ["worker.py", "11"])

    stdout = _result_event("done with context")
    with mock.patch("subprocess.Popen") as mock_popen:
        mock_popen.side_effect = lambda *a, **k: _mock_popen(stdout)
        with mock.patch("todo_board.worker._api"):
            worker.main()

    call_args = mock_popen.call_args[0][0]
    prompt_idx = call_args.index("-p") + 1
    assert "output from step 1" in call_args[prompt_idx]


def test_worker_main_marks_failed_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=6)]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "6"])

    with mock.patch("subprocess.Popen", side_effect=lambda *a, **k: _mock_popen("", returncode=1)):
        with mock.patch("todo_board.worker._api") as mock_api:
            worker.main()

    failed_calls = [c for c in mock_api.call_args_list
                    if c[0][0] == "/api/status/6" and c[0][1].get("status") == "failed"]
    assert len(failed_calls) == 1




def test_worker_saves_session_on_sigterm(monkeypatch, tmp_path):
    """When SIGTERM is received mid-run, worker saves session_id and exits without marking todo done."""
    monkeypatch.setenv("TODO_BOARD_DATA_DIR", str(tmp_path))
    import todo_board.worker as worker
    importlib.reload(worker)

    (tmp_path / "todos.json").write_text(json.dumps([_make_todo(id=1)]))
    monkeypatch.setattr(sys, "argv", ["worker.py", "1"])

    # Simulate: claude outputs a result event (session_id captured), but SIGTERM was set
    stdout = _result_event("partial work done", session_id="sess-interrupted-123")

    worker._sigterm_received = True
    api_calls = []

    def fake_api(path, data):
        api_calls.append((path, data))
        return {}

    with mock.patch("subprocess.Popen", side_effect=lambda *a, **k: _mock_popen(stdout)):
        with mock.patch("todo_board.worker._api", side_effect=fake_api):
            worker.main()

    worker._sigterm_received = False  # reset for other tests

    # Session ID must be persisted for resume
    sessions = json.loads((tmp_path / "sessions.json").read_text())
    assert sessions.get("1") == "sess-interrupted-123"

    # Todo must NOT be marked done or failed
    done_or_failed = [c for c in api_calls
                      if c[0].startswith("/api/status/") and
                      c[1].get("status") in ("done", "failed")]
    assert done_or_failed == []
