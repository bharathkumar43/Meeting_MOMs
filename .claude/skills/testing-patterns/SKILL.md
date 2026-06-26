# Skill: Testing Patterns

## Description
Triggered when the user asks to write tests, add test coverage, or create a test file for a module. Also triggered after a bug fix or new feature is implemented.

## What this skill does
1. Identifies the module to test and reads it fully.
2. Reads `.claude/rules/testing-standard.md` for project conventions.
3. Generates pytest test file with: fixtures, happy-path tests, error-path tests, edge cases.
4. Mocks all external calls (Graph API, Zoom API, OpenAI, email).

## Patterns used in this project

### Route test pattern
```python
def test_dashboard_requires_login(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 302  # redirect to login

def test_dashboard_authenticated(client, auth_session):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
```

### Helper unit test pattern
```python
def test_parse_vtt_transcript_returns_entries():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello world\n"
    entries = parse_vtt_transcript(vtt)
    assert len(entries) == 1
    assert entries[0]["text"] == "Hello world"
```

### OpenAI mock pattern
```python
@patch("app.mom_generator.openai.chat.completions.create")
def test_generate_mom(mock_openai):
    mock_openai.return_value.choices[0].message.content = '{"tldr":[],"action_items":[],"discussion_points":[]}'
    result, err = generate_mom_from_transcript("test transcript")
    assert err is None
```
