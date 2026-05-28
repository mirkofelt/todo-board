"""Tests for worker context-limit detection logic."""
import pytest
from todo_board.worker import _is_context_limit


@pytest.mark.parametrize("subtype", [
    "error_max_turns",
    "error_usage",
    "error_context_window",
])
def test_limit_subtypes_detected(subtype):
    assert _is_context_limit(subtype, "") is True


def test_success_subtype_not_limit():
    assert _is_context_limit("success", "") is False


def test_none_subtype_not_limit():
    assert _is_context_limit(None, "") is False


def test_empty_subtype_not_limit():
    assert _is_context_limit("", "") is False


@pytest.mark.parametrize("stderr", [
    "ContextWindowExceededError: too large",
    "error: context_length_exceeded",
    "max_tokens reached",
    "Too Many Tokens in request",
])
def test_stderr_patterns_detected(stderr):
    assert _is_context_limit(None, stderr) is True


def test_unrelated_stderr_not_limit():
    assert _is_context_limit(None, "some other error occurred") is False


def test_subtype_takes_priority_over_clean_stderr():
    assert _is_context_limit("error_max_turns", "everything fine") is True


def test_stderr_pattern_without_subtype():
    assert _is_context_limit("success", "ContextWindowExceededError") is True
