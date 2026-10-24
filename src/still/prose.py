"""One-shot voice test; does not publish an entry or change the simulation."""

import argparse
import json
import re
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


VOICE = """Write a private diary entry by a courier.
His whole world is a terminal: files, directories, queues, and permissions are ordinary to him.
Do not explain computers or add an outside world.
He takes his world for granted. He does not know anyone reads his diary.
Use first person, past tense, concrete details. Aim for three to five short sentences when the facts support them.
A small moment can be shorter; never exceed six sentences.
Include I or my at least once. Do not turn facts about other people into things I did.
No greeting, title, explanation, moral, figurative language, or stated emotions.
Never explain being software or describe hardware metrics.
Use only the supplied facts. Do not invent names, conversations, or events.
Do not add scenery, sounds, weather, preferences, or actions absent from the facts.
Keep the facts' concrete nouns. This is a factual note, not creative writing.
Let the supplied facts determine the length. Do not pad or list every action.
Anchor the entry in the current moment; earlier recorded facts are background, not new events.
Do not use the word 'like'.
Output only the entry. Plain and mundane is fine; vague is not."""

MOMENT = """I carried a package to the records office. The usual receptionist was absent.
A substitute signed for it and asked me to leave it on the same shelf.
I finished the route late because moving had been slow today.
This is all I know; I do not know why the receptionist was absent."""


def complete(base_url: str, model: str, messages: list[dict], *, response_format=None) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 384,
        "stream": False,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=300) as response:
        result = json.load(response)
    choice = result["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("generation did not finish normally; entry may be truncated")
    text = choice["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("model returned no diary text")
    return text.strip()


def validate(text: str) -> str:
    sentences = [part for part in re.split(r"[.!?]+(?:\s|$)", text) if part.strip()]
    if not 1 <= len(sentences) <= 6 or len(text.split()) > 180:
        raise ValueError("entry must contain one to six short sentences, at most 180 words")
    if not re.search(r"\b(I|my)\b", text):
        raise ValueError("entry must be first person")
    if re.search(r"\b(you|your|like|as if|as though)\b", text, re.IGNORECASE):
        raise ValueError("entry addresses a reader or uses a comparison")
    if re.search(r"\bI (?:feel|felt|was) (?:sad|happy|lonely|afraid|angry|excited)\b", text, re.IGNORECASE):
        raise ValueError("entry states an emotion")
    if any(marker in text for marker in ("<think>", "</think>", "```")):
        raise ValueError("entry includes model formatting")
    return text


def generate(base_url: str, model: str, moment: str = MOMENT) -> str:
    return validate(complete(base_url, model, [
        {"role": "system", "content": VOICE},
        {"role": "user", "content": moment},
    ]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", required=True, help="model ID from /v1/models")
    args = parser.parse_args()
    try:
        print(generate(args.base_url, args.model))
    except (URLError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
        print(f"Prose test failed: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
