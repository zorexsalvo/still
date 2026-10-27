"""One-shot voice test; does not publish an entry or change the simulation."""

import argparse
from collections import Counter
import json
import re
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


VOICE = """Write a private diary entry by a courier.
His whole world is a terminal: files, directories, queues, and permissions are ordinary to him.
Do not explain computers or add an outside world.
He takes his world for granted. He does not know anyone reads his diary.
Write the day, not a transcription of event facts. Use first person, past tense, concrete details.
Use only as many sentences as the moment needs, often one or two; never exceed six.
Include I or my at least once. Do not turn facts about other people into things I did.
No greeting, title, explanation, moral, figurative language, or stated emotions.
Never explain being software or describe hardware metrics.
Use only the supplied facts. Do not invent names, conversations, or events.
Do not add scenery, sounds, weather, preferences, or actions absent from the facts.
A changed personal commitment, someone's delayed work, or an activity enabled by a delivery can be the subject.
Use the supplied consequences, not a receipt of pickup, acceptance and signing.
Use the supplied ongoing threads without pretending they were discovered again today.
Mention each named person, object or noun phrase only once; use pronouns after that.
If removing the names and objects leaves only delivery/acceptance/signing, there is no entry.
Do not invent human gestures or reactions to make routine work interesting.
Do not turn future commitments into actions already performed: being asked to return later does not mean I waited.
Let the supplied facts determine the length. Do not pad or list every action.
Never add generic status closings such as 'The operation completed' or 'The transfer is complete'.
Earlier recorded pickup or travel can supply concrete background instead of a closing summary.
Anchor the entry in the current moment; earlier recorded facts are background, not new events.
Do not use the word 'like'.
Output only the entry. Plain and mundane is fine; vague is not."""

MOMENT = """I carried a package to the records office. The usual receptionist was absent.
A substitute signed for it and asked me to leave it on the same shelf.
I finished the route late because moving had been slow today.
This is all I know; I do not know why the receptionist was absent."""


def complete(base_url: str, model: str, messages: list[dict], *, response_format=None,
             timing: dict | None = None) -> str:
    """One chat completion. `timing` receives the measured generation round trip."""
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
    # The timer covers the response body too, so the figure is the model's answer, not a connect.
    started = time.monotonic()
    with urlopen(request, timeout=300) as response:
        result = json.load(response)
    if timing is not None:
        timing["model_reply_ms"] = round((time.monotonic() - started) * 1000, 1)
    choice = result["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("generation did not finish normally; entry may be truncated")
    text = choice["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("model returned no diary text")
    return text.strip()


# shortcut: lexical references and content-word pairs approximate noun phrases; use a parser if false positives matter.
GRAMMAR = set("i my me we our you your he she they it its his her their him them a an the and or but to of for from at in on with by as is was were be been had has have did do does not no still then again while after before below above this that these those there here only first s".split())
RECEIPT = GRAMMAR | set("deliver delivered delivery deliveries took take carried carry collected collect accepted accept signed sign received receive handed went met left leave brought bring dropped drop gave give passed pass picked pick package parcel copy process concluded transfer complete transaction finished done up off out successfully".split())


def check_subject(text: str, references: tuple[str, ...] = ()) -> None:
    """Reject repeated references and entries with nothing left beyond a receipt."""
    words = re.findall(r"\b\w+(?:[-’']\w+)*\b", text)
    folded = [word.casefold() for word in words]
    names = {word for word in words if word[0].isupper() and word.casefold() not in RECEIPT}
    mentions = set(references) | names
    for index in range(1, len(words)):
        if (folded[index - 1] in {"a", "an", "the", "my", "his", "her", "their", "our"}
                and folded[index] not in GRAMMAR
                and (index + 1 == len(words) or folded[index + 1] in GRAMMAR)):
            mentions.add(words[index])
    for reference in mentions:
        if not reference.strip():
            continue
        if len(re.findall(r"(?<!\w)" + re.escape(reference) + r"(?!\w)", text, re.IGNORECASE)) > 1:
            raise ValueError("entry repeats a proper noun or named noun phrase")
    pairs = Counter(zip(folded, folded[1:]))
    if any(count > 1 and not (set(pair) & GRAMMAR) for pair, count in pairs.items()):
        raise ValueError("entry repeats a noun phrase")
    stripped = text
    for reference in sorted(mentions, key=len, reverse=True):
        stripped = re.sub(r"(?<!\w)" + re.escape(reference) + r"(?!\w)", " ", stripped, flags=re.IGNORECASE)
    remainder = set(re.findall(r"\b\w+(?:[-’']\w+)*\b", stripped.casefold())) - RECEIPT
    if not remainder:
        raise ValueError("entry has no subject beyond a routine receipt")


def validate(text: str, *, references: tuple[str, ...] = ()) -> str:
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
    if re.search(r"\b(?:operations?|completed|updated|successfully)\b|\bstate\s+is\s+now\b", text, re.IGNORECASE):
        raise ValueError("entry includes system language")
    check_subject(text, references)
    return text


def generate(base_url: str, model: str, moment: str = MOMENT, *, references: tuple[str, ...] = (),
             timing: dict | None = None) -> str:
    return validate(complete(base_url, model, [
        {"role": "system", "content": VOICE},
        {"role": "user", "content": moment},
    ], timing=timing), references=references)


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
