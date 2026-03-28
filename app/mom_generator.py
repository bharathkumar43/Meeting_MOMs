import json
import logging
import openai
from config import Config

logger = logging.getLogger(__name__)


def generate_mom_from_transcript(transcript: str, meeting_subject: str = "") -> tuple[dict, str | None]:
    """
    Send the transcript to OpenAI and get back structured MOM content.

    Returns:
        (result_dict, error_message)
        error_message is None on success, a string describing the problem on failure.
    """
    if not transcript or not transcript.strip():
        return _empty(), "Transcript is empty — nothing to generate from."

    if not Config.OPENAI_API_KEY or Config.OPENAI_API_KEY.startswith("your-"):
        return _empty(), "OPENAI_API_KEY is not configured in .env"

    prompt = f"""You are an expert meeting facilitator. Analyse the following meeting transcript and extract the key information needed for a Minutes of Meeting (MOM) document.

Meeting subject: {meeting_subject}

Transcript:
{transcript}

Return ONLY a valid JSON object (no markdown fences, no explanation) with exactly these four keys:

{{
  "summary": "<2-4 sentence overview of what was discussed and the main outcomes>",
  "discussion_points": [
    "<concise statement of a key topic discussed>"
  ],
  "action_items": [
    {{
      "description": "<what needs to be done>",
      "assigned_to": "<person name if mentioned, else empty string>",
      "due_date": ""
    }}
  ],
  "decisions": [
    "<a firm decision or agreement reached>"
  ]
}}

Rules:
- discussion_points: 3-8 items, each a single clear sentence
- action_items: only concrete tasks with a clear next step; omit vague ones
- decisions: only firm agreements; omit tentative items
- Return an empty list [] for any section with nothing relevant
- Output raw JSON only — no ```json``` fences, no extra text
"""

    try:
        client = openai.OpenAI(api_key=Config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        logger.debug("OpenAI raw response: %s", raw)

        # Strip markdown code fences if the model adds them despite instructions
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        return _validate(data), None

    except openai.AuthenticationError:
        msg = "OpenAI API key is invalid. Check OPENAI_API_KEY in .env"
        logger.error(msg)
        return _empty(), msg
    except openai.RateLimitError:
        msg = "OpenAI rate limit hit. Please try again in a moment."
        logger.error(msg)
        return _empty(), msg
    except json.JSONDecodeError as e:
        msg = f"OpenAI returned non-JSON output: {e}"
        logger.error(msg)
        return _empty(), msg
    except Exception as e:
        msg = f"MOM generation failed: {e}"
        logger.exception(msg)
        return _empty(), msg


def _empty() -> dict:
    return {
        "summary": "",
        "discussion_points": [],
        "action_items": [],
        "decisions": [],
    }


def _validate(data: dict) -> dict:
    result = _empty()
    if isinstance(data.get("summary"), str):
        result["summary"] = data["summary"]
    if isinstance(data.get("discussion_points"), list):
        result["discussion_points"] = [str(p) for p in data["discussion_points"] if p]
    if isinstance(data.get("action_items"), list):
        for item in data["action_items"]:
            if isinstance(item, dict) and item.get("description"):
                result["action_items"].append({
                    "description": str(item.get("description", "")),
                    "assigned_to": str(item.get("assigned_to", "")),
                    "due_date": str(item.get("due_date", "")),
                })
    if isinstance(data.get("decisions"), list):
        result["decisions"] = [str(d) for d in data["decisions"] if d]
    return result
