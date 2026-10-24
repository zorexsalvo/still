"""One-shot voice test; does not publish an entry or change the simulation."""

import argparse
import json
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


VOICE = """Write a private diary entry by a courier whose entire world is a terminal.
He takes his world for granted. He does not know anyone reads his diary.
Use first person, past tense, one to four short sentences, concrete details.
No greeting, title, explanation, moral, figurative language, or stated emotions.
Never explain being software or describe hardware metrics.
Use only the supplied facts. Do not invent names, conversations, or events.
Output only the entry. Plain and mundane is fine; vague is not."""

MOMENT = """I carried a package to the records office. The usual receptionist was absent.
A substitute signed for it and asked me to leave it on the same shelf.
I finished the route late because moving had been slow today.
This is all I know; I do not know why the receptionist was absent."""


def generate(base_url: str, model: str) -> str:
    request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": VOICE},
                    {"role": "user", "content": MOMENT},
                ],
                "temperature": 0.7,
                "max_tokens": 256,
                "stream": False,
            }
        ).encode(),
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
