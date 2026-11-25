"""
Tests for templates.py — save, get, render, list_all, delete, parse_kwargs.
All tests use tmp_path; config.TEMPLATES_DIR is redirected per-test.
"""
import json
import os
import pytest
import config
import templates as tpl


@pytest.fixture(autouse=True)
def temp_templates_dir(tmp_path, monkeypatch):
    """Redirect TEMPLATES_DIR to a fresh temp folder for every test."""
    monkeypatch.setattr(config, "TEMPLATES_DIR", str(tmp_path / "templates"))
    monkeypatch.setattr(tpl, "_DIR", str(tmp_path / "templates"))
    return tmp_path / "templates"


# ── save / get ─────────────────────────────────────────────────────────────────

def test_save_creates_file():
    tpl.save("greet", "Hello, {name}!")
    assert os.path.exists(tpl._path("greet"))


def test_save_returns_path():
    path = tpl.save("greet", "Hi {name}")
    assert path.endswith(".json")


def test_get_returns_dict():
    tpl.save("greet", "Hello, {name}!")
    result = tpl.get("greet")
    assert result is not None
    assert result["name"] == "greet"
    assert result["text"] == "Hello, {name}!"


def test_get_missing_returns_none():
    assert tpl.get("nonexistent") is None


def test_save_normalises_name_spaces():
    tpl.save("my template", "text")
    result = tpl.get("my template")
    assert result is not None


def test_save_normalises_name_uppercase():
    tpl.save("MyTemplate", "text")
    result = tpl.get("MyTemplate")
    assert result is not None


def test_save_overwrites_existing():
    tpl.save("dup", "first version")
    tpl.save("dup", "second version")
    assert tpl.get("dup")["text"] == "second version"


# ── render ─────────────────────────────────────────────────────────────────────

def test_render_no_placeholders():
    tpl.save("plain", "Write a hello world.")
    assert tpl.render("plain") == "Write a hello world."


def test_render_with_placeholder():
    # Note: avoid using 'name' as a placeholder — it shadows the function's
    # own 'name' parameter and causes a TypeError on Python 3.x.
    tpl.save("greet", "Hello, {person}!")
    assert tpl.render("greet", person="Horacio") == "Hello, Horacio!"


def test_render_multiple_placeholders():
    tpl.save("task", "Write {kind} for {module} in {language}.")
    result = tpl.render("task", kind="tests", module="auth", language="Python")
    assert result == "Write tests for auth in Python."


def test_render_missing_placeholder_raises():
    tpl.save("greet", "Hello, {person}!")
    with pytest.raises(ValueError, match="placeholder"):
        tpl.render("greet")   # person not supplied


def test_render_unknown_name_raises():
    with pytest.raises(ValueError, match="not found"):
        tpl.render("ghost")


# ── list_all ───────────────────────────────────────────────────────────────────

def test_list_all_empty_dir():
    assert tpl.list_all() == []


def test_list_all_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(tpl, "_DIR", str(tmp_path / "no_such_dir"))
    assert tpl.list_all() == []


def test_list_all_returns_all_templates():
    tpl.save("alpha", "first")
    tpl.save("beta",  "second")
    tpl.save("gamma", "third")
    results = tpl.list_all()
    names = [r["name"] for r in results]
    assert len(results) == 3
    assert "alpha" in names
    assert "beta"  in names


def test_list_all_sorted_by_name():
    tpl.save("zz", "last")
    tpl.save("aa", "first")
    names = [r["name"] for r in tpl.list_all()]
    assert names == sorted(names)


# ── delete ─────────────────────────────────────────────────────────────────────

def test_delete_existing_returns_true():
    tpl.save("to_del", "bye")
    assert tpl.delete("to_del") is True


def test_delete_existing_removes_file():
    tpl.save("to_del", "bye")
    tpl.delete("to_del")
    assert tpl.get("to_del") is None


def test_delete_nonexistent_returns_false():
    assert tpl.delete("ghost") is False


# ── parse_kwargs ───────────────────────────────────────────────────────────────

def test_parse_kwargs_simple():
    result = tpl.parse_kwargs("name=Horacio")
    assert result == {"name": "Horacio"}


def test_parse_kwargs_multiple():
    result = tpl.parse_kwargs("lang=Python module=auth")
    assert result["lang"]   == "Python"
    assert result["module"] == "auth"


def test_parse_kwargs_quoted_value():
    result = tpl.parse_kwargs('title="hello world"')
    assert result["title"] == "hello world"


def test_parse_kwargs_empty_string():
    assert tpl.parse_kwargs("") == {}


def test_parse_kwargs_ignores_non_kv():
    # bare words without = are ignored
    result = tpl.parse_kwargs("justtext")
    assert result == {}
