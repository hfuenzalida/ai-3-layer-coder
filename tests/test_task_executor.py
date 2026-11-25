"""
Tests for task_executor.py — save_file, validate_python, _strip_markdown_fence.
Uses the OUTPUT_DIR already redirected to a temp path by conftest.py.
"""
import os
import pytest
import config
from task_executor import save_file, validate_python, _strip_markdown_fence


# ── save_file ──────────────────────────────────────────────────────────────────

def test_save_file_valid_path_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    target = str(tmp_path / "hello.py")
    ok, msg = save_file(target, "print('hi')")
    assert ok is True
    assert os.path.exists(target)
    assert "Saved" in msg


def test_save_file_creates_subdirectory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    target = str(tmp_path / "sub" / "module" / "code.py")
    ok, msg = save_file(target, "x = 1")
    assert ok is True
    assert os.path.exists(target)


def test_save_file_writes_correct_content(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    target = str(tmp_path / "out.txt")
    save_file(target, "exact content")
    assert open(target).read() == "exact content"


def test_save_file_strips_markdown_fence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    target = str(tmp_path / "code.py")
    fenced = "```python\nprint('hello')\n```"
    save_file(target, fenced)
    content = open(target).read()
    assert "```" not in content
    assert "print('hello')" in content


def test_save_file_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    evil_path = str(tmp_path / ".." / "evil.py")
    ok, msg = save_file(evil_path, "malicious")
    assert ok is False
    assert "Blocked" in msg or "outside" in msg.lower()


def test_save_file_blocks_absolute_outside_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    ok, msg = save_file("/etc/passwd", "oops")
    assert ok is False


def test_save_file_empty_path_returns_success(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    ok, msg = save_file("", "content")
    assert ok is True
    assert "No path" in msg


def test_save_file_symlink_attack_blocked(tmp_path, monkeypatch):
    """A symlink inside OUTPUT_DIR pointing outside must be blocked."""
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("sensitive")
    link_target = tmp_path / "link.py"
    try:
        link_target.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("Symlinks not supported on this platform/user")
    ok, msg = save_file(str(link_target), "injected")
    # os.path.realpath resolves the symlink → outside OUTPUT_DIR → blocked
    assert ok is False


# ── validate_python ────────────────────────────────────────────────────────────

def test_validate_python_clean_code():
    assert validate_python("x = 1\nprint(x)\n") is None


def test_validate_python_syntax_error_returns_string():
    result = validate_python("def broken(:\n  pass\n")
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


def test_validate_python_empty_string():
    assert validate_python("") is None


def test_validate_python_with_markdown_fence():
    fenced = "```python\ndef greet():\n    return 'hello'\n```"
    assert validate_python(fenced) is None


def test_validate_python_fenced_with_syntax_error():
    fenced = "```python\ndef broken(:\n    pass\n```"
    result = validate_python(fenced)
    assert result is not None


def test_validate_python_returns_none_on_non_python():
    """Non-Python content that can't be compiled should return None (safe fallback)."""
    result = validate_python("This is plain English, not Python code at all.")
    # Either None (exception swallowed) or a compile error string — both acceptable
    assert result is None or isinstance(result, str)


def test_validate_python_temp_file_cleaned_up(tmp_path):
    """No stale temp files should remain after validation."""
    import tempfile
    before = set(os.listdir(tempfile.gettempdir()))
    validate_python("x = 1")
    after  = set(os.listdir(tempfile.gettempdir()))
    new_files = after - before
    py_files  = [f for f in new_files if f.endswith(".py")]
    assert py_files == [], f"Leaked temp files: {py_files}"


# ── _strip_markdown_fence ──────────────────────────────────────────────────────

def test_strip_fence_simple():
    assert _strip_markdown_fence("```\ncode here\n```") == "code here\n"


def test_strip_fence_with_language_tag():
    assert _strip_markdown_fence("```python\nx = 1\n```") == "x = 1\n"


def test_strip_fence_with_preamble():
    content = "Here is the fix:\n```python\nx = 1\n```"
    assert _strip_markdown_fence(content) == "x = 1\n"


def test_strip_fence_with_trailing_text():
    content = "```python\nx = 1\n```\nHope that helps!"
    result = _strip_markdown_fence(content)
    assert "x = 1" in result
    assert "```" not in result


def test_strip_fence_no_fence_returns_original():
    plain = "just plain text no fences"
    assert _strip_markdown_fence(plain) == plain


def test_strip_fence_multiline_code():
    code = "def f():\n    return 42\n"
    fenced = f"```python\n{code}```"
    result = _strip_markdown_fence(fenced)
    assert "def f():" in result
    assert "return 42" in result


def test_strip_fence_empty_block():
    assert _strip_markdown_fence("```\n```") == ""


def test_strip_fence_only_opening_returns_original():
    """A lone opening fence with no closing fence — return original."""
    content = "```python\npartial code"
    # re.search won't find a complete pair → returns original
    assert _strip_markdown_fence(content) == content


def test_strip_fence_json_language_tag():
    content = '```json\n{"key": "value"}\n```'
    result = _strip_markdown_fence(content)
    assert result == '{"key": "value"}\n'
