import io
import json
import unittest
from unittest.mock import patch

from still.prose import generate


class ProseTest(unittest.TestCase):
    def response(self, content, finish_reason="stop"):
        return io.BytesIO(json.dumps({"choices": [{
            "finish_reason": finish_reason,
            "message": {"content": content},
        }]}).encode())

    @patch("still.prose.urlopen")
    def test_request_and_entry(self, urlopen):
        urlopen.return_value = self.response("  I left it on the shelf.\n")
        self.assertEqual(generate("http://localhost:8080/v1/", "local"),
                         "I left it on the shelf.")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:8080/v1/chat/completions")
        self.assertEqual(json.loads(request.data)["model"], "local")

    @patch("still.prose.urlopen")
    def test_three_and_four_sentence_entries_are_preserved(self, urlopen):
        sentences = ["I carried the package.", "Mara signed in the wrong box.",
                     "I waited while she crossed it out.", "She signed again below it."]
        for count in (3, 4):
            text = " ".join(sentences[:count])
            with self.subTest(count=count):
                urlopen.return_value = self.response(text)
                self.assertEqual(generate("http://localhost:8080/v1", "local"), text)
        urlopen.return_value = self.response(" ".join(sentences + ["I left."]))
        with self.assertRaises(ValueError):
            generate("http://localhost:8080/v1", "local")

    @patch("still.prose.urlopen")
    def test_rejects_empty_or_truncated_output(self, urlopen):
        for content, reason in [(" ", "stop"), ("I carried", "length")]:
            with self.subTest(content=content):
                urlopen.return_value = self.response(content, reason)
                with self.assertRaises(ValueError):
                    generate("http://localhost:8080/v1", "local")
