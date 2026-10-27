import io
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from still.prose import check_subject, generate, validate


class ProseTest(unittest.TestCase):
    def response(self, content, finish_reason="stop"):
        return io.BytesIO(json.dumps({"choices": [{
            "finish_reason": finish_reason,
            "message": {"content": content},
        }]}).encode())

    @patch("still.prose.urlopen")
    def test_request_and_entry(self, urlopen):
        urlopen.return_value = self.response("  I left it on the shelf. The usual receptionist was absent.\n")
        self.assertEqual(generate("http://localhost:8080/v1/", "local"),
                         "I left it on the shelf. The usual receptionist was absent.")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:8080/v1/chat/completions")
        self.assertEqual(json.loads(request.data)["model"], "local")
        self.assertIn("His whole world is a terminal", json.loads(request.data)["messages"][0]["content"])

    @patch("still.prose.urlopen")
    def test_three_to_six_sentence_entries_are_preserved(self, urlopen):
        sentences = ["I carried the package.", "Mara signed in the wrong box.",
                     "I waited while she crossed it out.", "She signed again below it.",
                     "I kept the first receipt.", "The ink had not dried."]
        for count in (3, 4, 5, 6):
            text = " ".join(sentences[:count])
            with self.subTest(count=count):
                urlopen.return_value = self.response(text)
                self.assertEqual(generate("http://localhost:8080/v1", "local"), text)
        urlopen.return_value = self.response(" ".join(sentences + ["I left."]))
        with self.assertRaises(ValueError):
            generate("http://localhost:8080/v1", "local")

    def test_stripped_receipts_have_no_subject_even_without_repetition(self):
        for text in (
            "I took the sealed directory copy 123 to Mara. She signed for it.",
            "I delivered it. She accepted it and signed for it.",
            "I delivered the package.",
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "no subject"):
                check_subject(text, ("sealed directory copy 123", "Mara"))

    def test_repeated_names_and_noun_phrases_are_rejected(self):
        for text, references in (
            ("I delivered the sealed directory copy 123. Mara accepted the sealed directory copy 123. Mara signed for it.", ()),
            ("I showed it to Ivo. ivo could not read the owner line.", ("Ivo",)),
            ("I checked the blank owner field. It was the same blank owner field.", ()),
            ("I opened old-index. The owner of old-index was missing.", ("old-index",)),
            ("I opened the file. The file was unreadable.", ()),
            ("I opened my file. The file was unreadable.", ()),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "repeats"):
                validate(text, references=references)

    def test_real_detail_and_unresolved_subject_survive_stripping(self):
        for text in (
            "I took the copy to Mara. She signed for it and did not look up.",
            "I kept the readable line. The owner field was still blank.",
            "I met Mara. Her marathon entry had an empty owner field.",
        ):
            with self.subTest(text=text):
                self.assertEqual(validate(text, references=("copy", "Mara")), text)

    def test_system_language_is_rejected_case_insensitively(self):
        for phrase in ("operation", "OPERATIONS", "completed", "state is now", "updated", "successfully"):
            with self.subTest(phrase=phrase), self.assertRaisesRegex(ValueError, "system language"):
                validate(f"I left the owner line blank. The {phrase} was recorded.")

    @patch("still.prose.urlopen")
    def test_complete_times_the_generation_round_trip(self, urlopen):
        urlopen.return_value = self.response("I left it on the shelf. The usual receptionist was absent.")
        timing = {}
        generate("http://localhost:8080/v1", "local", timing=timing)
        self.assertIsInstance(timing["model_reply_ms"], float)
        self.assertGreaterEqual(timing["model_reply_ms"], 0)

    @patch("still.prose.urlopen")
    def test_failed_reply_records_no_timing(self, urlopen):
        urlopen.side_effect = URLError("model is down")
        timing = {}
        with self.assertRaises(URLError):
            generate("http://localhost:8080/v1", "local", timing=timing)
        self.assertEqual(timing, {})

    @patch("still.prose.urlopen")
    def test_generate_applies_reference_aware_receipt_check(self, urlopen):
        urlopen.return_value = self.response("I took the sealed directory copy 123 to Mara. She signed for it.")
        with self.assertRaisesRegex(ValueError, "no subject"):
            generate("http://local/v1", "still", references=("sealed directory copy 123", "Mara"))

    @patch("still.prose.urlopen")
    def test_rejects_more_than_180_words(self, urlopen):
        urlopen.return_value = self.response("I " + " ".join(["waited"] * 180) + ".")
        with self.assertRaises(ValueError):
            generate("http://localhost:8080/v1", "local")

    @patch("still.prose.urlopen")
    def test_rejects_empty_or_truncated_output(self, urlopen):
        for content, reason in [(" ", "stop"), ("I carried", "length")]:
            with self.subTest(content=content):
                urlopen.return_value = self.response(content, reason)
                with self.assertRaises(ValueError):
                    generate("http://localhost:8080/v1", "local")
