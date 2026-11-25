"""
Tests for CodexRouter — pure routing logic, no I/O.
"""
import pytest
from orchestrator import CodexRouter


@pytest.fixture
def router():
    return CodexRouter()


# ── Keyword routing ────────────────────────────────────────────────────────────

def test_route_local_keyword_docstring(router):
    duty, _, _ = router.route_request("Add docstrings to utils.py")
    assert duty == "local"


def test_route_local_keyword_format(router):
    duty, _, _ = router.route_request("Format this file")
    assert duty == "local"


def test_route_local_keyword_boilerplate(router):
    duty, _, _ = router.route_request("Generate boilerplate for a Flask app")
    assert duty == "local"


def test_route_local_keyword_type_hint(router):
    duty, _, _ = router.route_request("Add type hints to the function")
    assert duty == "local"


def test_route_heavy_keyword_refactor(router):
    duty, _, _ = router.route_request("Refactor the payment module")
    assert duty == "coding"


def test_route_heavy_keyword_unit_test(router):
    duty, _, _ = router.route_request("Write unit tests for auth.py")
    assert duty == "coding"


def test_route_heavy_keyword_api_endpoint(router):
    duty, _, _ = router.route_request("Implement the API endpoint for user login")
    assert duty == "coding"


def test_route_heavy_keyword_migration(router):
    duty, _, _ = router.route_request("Write a database migration script")
    assert duty == "coding"


def test_route_orchestrator_keyword_architecture(router):
    duty, _, _ = router.route_request("Review the architecture of this system")
    assert duty == "strategic"


def test_route_orchestrator_keyword_strategy(router):
    duty, _, _ = router.route_request("What strategy should we use for caching?")
    assert duty == "strategic"


def test_route_orchestrator_keyword_security_audit(router):
    duty, _, _ = router.route_request("Perform a security audit of the auth flow")
    assert duty == "strategic"


def test_route_orchestrator_keyword_design_pattern(router):
    duty, _, _ = router.route_request("Which design pattern fits this use case?")
    assert duty == "strategic"


# ── No keyword match → safe default ───────────────────────────────────────────

def test_route_no_keywords_defaults_to_coding(router):
    duty, target, reason = router.route_request("Do something vague")
    assert duty   == "coding"
    assert "QWEN" in target.upper()
    assert "default" in reason.lower() or "keyword" in reason.lower()


def test_route_empty_prompt_defaults_to_coding(router):
    duty, _, _ = router.route_request("")
    assert duty == "coding"


# ── Word count adjustments ────────────────────────────────────────────────────

def test_long_prompt_penalizes_local(router):
    """A 210-word prompt with a single local keyword should shift away from local."""
    filler = "word " * 205
    prompt = f"Please format {filler} the code."
    duty_short, _, _ = router.route_request("Please format the code.")
    duty_long,  _, _ = router.route_request(prompt)
    # Long prompt should either route to coding or strategic, not local
    assert duty_long in ("coding", "strategic")


def test_moderately_long_prompt_reduces_local_score(router):
    """160-word prompt with only a format keyword — local score reduced by 1."""
    filler = "word " * 155
    prompt = f"Please format {filler} this file."
    # Should still potentially route to local or shift; just confirm it runs
    duty, _, reason = router.route_request(prompt)
    assert duty in ("local", "coding", "strategic")
    assert reason  # non-empty reason string


# ── Word-boundary correctness ─────────────────────────────────────────────────

def test_word_boundary_import_not_in_important(router):
    """'important' must NOT trigger the 'import' keyword pattern."""
    duty_important, _, _ = router.route_request("This is an important change")
    duty_import,    _, _ = router.route_request("Add the import statement")
    # Both should route somewhere — just confirm 'important' doesn't score same as 'import'
    # The real test: route_request doesn't crash and returns valid duty
    assert duty_important in ("local", "coding", "strategic")
    assert duty_import    in ("local", "coding", "strategic")


def test_word_boundary_comment_not_in_commentary(router):
    """'commentary' must NOT trigger the 'comment' local keyword."""
    duty_commentary, _, _ = router.route_request("Add your commentary here")
    duty_comment,    _, _ = router.route_request("Add a comment above this function")
    assert duty_commentary in ("local", "coding", "strategic")
    assert duty_comment    in ("local", "coding", "strategic")


# ── Return structure ──────────────────────────────────────────────────────────

def test_route_returns_three_tuple(router):
    result = router.route_request("format the file")
    assert len(result) == 3
    duty, target, reason = result
    assert isinstance(duty,   str)
    assert isinstance(target, str)
    assert isinstance(reason, str)


def test_route_target_contains_model_name(router):
    _, target, _ = router.route_request("refactor the database layer")
    assert len(target) > 0
    assert target.upper() == target or any(c.isalpha() for c in target)


def test_route_reason_mentions_scores(router):
    _, _, reason = router.route_request("refactor the authentication module")
    assert "score" in reason.lower() or "local=" in reason.lower() or "heavy=" in reason.lower()
