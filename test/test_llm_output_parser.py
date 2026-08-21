import unittest

from utils.llmOutputParser import flattenContentBlocks, parseModelJsonOutput


INSIGHTS_JSON = (
    '{\n'
    '  "diagnostic_insights": [{"finding": "Revenue fell 12%", "confidence": 0.85}],\n'
    '  "prescriptive_actions": [{"recommended_action": "Review pricing", "priority": "high"}],\n'
    '  "missing_data": []\n'
    '}'
)


class FlattenContentBlocksTests(unittest.TestCase):
    def test_string_content_is_returned_unchanged(self):
        self.assertEqual("plain", flattenContentBlocks("plain"))

    def test_text_blocks_are_concatenated(self):
        blocks = [{"type": "text", "text": "abc"}, {"type": "text", "text": "def"}]

        self.assertEqual("abcdef", flattenContentBlocks(blocks))

    def test_non_text_blocks_are_dropped(self):
        blocks = [
            {"type": "thinking", "thinking": "internal reasoning", "text": "leak"},
            {"type": "text", "text": "answer"},
        ]

        self.assertEqual("answer", flattenContentBlocks(blocks))

    def test_single_text_block_dict_is_unwrapped(self):
        self.assertEqual("answer", flattenContentBlocks({"type": "text", "text": "answer"}))

    def test_plain_dict_output_is_left_alone(self):
        payload = {"diagnostic_insights": []}

        self.assertEqual(payload, flattenContentBlocks(payload))


class ParseModelJsonOutputTests(unittest.TestCase):
    def test_gemini_content_blocks_yield_the_models_json(self):
        # Recent Gemini models hand back a list of content blocks carrying a
        # thought signature; the JSON inside must survive the unwrapping.
        blocks = [
            {
                "type": "text",
                "text": INSIGHTS_JSON,
                "extras": {"signature": "EjQKMgERTTIP20iQEyz0IwBG7XmWXuaTVs3"},
            }
        ]

        parsed = parseModelJsonOutput(blocks, "Image-to-insights")

        self.assertEqual(
            ["diagnostic_insights", "missing_data", "prescriptive_actions"],
            sorted(parsed.keys()),
        )
        self.assertEqual("Revenue fell 12%", parsed["diagnostic_insights"][0]["finding"])

    def test_plain_string_output_still_parses(self):
        parsed = parseModelJsonOutput(INSIGHTS_JSON, "Image-to-insights")

        self.assertEqual([], parsed["missing_data"])

    def test_fenced_string_output_still_parses(self):
        parsed = parseModelJsonOutput(f"```json\n{INSIGHTS_JSON}\n```", "Image-to-insights")

        self.assertEqual("high", parsed["prescriptive_actions"][0]["priority"])

    def test_content_blocks_without_text_raise(self):
        with self.assertRaises(ValueError):
            parseModelJsonOutput([{"type": "thinking", "thinking": "..."}], "Image-to-insights")


if __name__ == "__main__":
    unittest.main()
