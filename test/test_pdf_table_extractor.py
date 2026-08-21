import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from nubrix.components.pdfTableExtractor import (
    ExtractedTable,
    PageExtractionResult,
    PdfTableExtractor,
)
from utils.exceptionHandler import CustomException


class PdfTableExtractorTestCase(unittest.TestCase):
    def setUp(self):
        llmPatcher = patch("nubrix.components.pdfTableExtractor.getGenaiLlm")
        langfusePatcher = patch("utils.llm.getLangfuseConfig", return_value={})
        self.addCleanup(llmPatcher.stop)
        self.addCleanup(langfusePatcher.stop)
        self.mockLlm = MagicMock()
        llmPatcher.start().return_value = self.mockLlm
        langfusePatcher.start()
        self.extractor = PdfTableExtractor()

    def test_extract_page_parses_valid_json(self):
        self.mockLlm.invoke.return_value = MagicMock(
            content=(
                '{"tables":[{"headers":["A"],"rows":[[1],[2]],"continued":false}],'
                '"narrative":"Page note"}'
            )
        )

        result = self.extractor.extractPage("abc123")

        self.assertEqual(len(result.tables), 1)
        self.assertEqual(result.tables[0].headers, ["A"])
        self.assertEqual(result.tables[0].rows, [[1], [2]])
        self.assertEqual(result.narrative, "Page note")

    def test_extract_page_retries_transient_failure(self):
        self.mockLlm.invoke.side_effect = [
            Exception("temporary network issue"),
            MagicMock(content='{"tables":[],"narrative":"ok"}'),
        ]

        result = self.extractor.extractPage("abc123")

        self.assertEqual(self.mockLlm.invoke.call_count, 2)
        self.assertEqual(result.tables, [])
        self.assertEqual(result.narrative, "ok")

    def test_extract_page_rate_limit_maps_to_429(self):
        self.mockLlm.invoke.side_effect = Exception("429 quota exceeded")

        with self.assertRaises(CustomException) as ctx:
            self.extractor.extractPage("abc123")

        self.assertEqual(ctx.exception.statusCode, 429)
        self.assertIn("rate limit", ctx.exception.uiMessage.lower())

    def test_extract_page_retries_exhausted_raises_custom_exception(self):
        self.extractor.maxRetries = 1
        self.mockLlm.invoke.side_effect = [
            Exception("attempt one failure"),
            Exception("attempt two failure"),
        ]

        with self.assertRaises(CustomException) as ctx:
            self.extractor.extractPage("abc123")

        self.assertEqual(self.mockLlm.invoke.call_count, 2)
        self.assertEqual(
            ctx.exception.uiMessage,
            "PDF table extraction failed after retries. Try again later.",
        )

    def test_plan_merge_parses_valid_groups(self):
        self.mockLlm.bind.return_value.invoke.return_value = MagicMock(
            content=(
                '{"groups":[{"tableIndices":[0,1],"canonicalHeaders":["col_a"]}]}'
            )
        )
        fragments = [
            {
                "pageNum": 1,
                "table": ExtractedTable(
                    headers=["col_a"], rows=[[1]], continued=False
                ),
            },
            {
                "pageNum": 2,
                "table": ExtractedTable(
                    headers=["col_a"], rows=[[2]], continued=True
                ),
            },
        ]

        plan = self.extractor.planMerge(fragments)

        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(plan.groups[0].tableIndices, [0, 1])
        self.assertEqual(plan.groups[0].canonicalHeaders, ["col_a"])

    def test_plan_merge_falls_back_on_invalid_json(self):
        self.mockLlm.bind.return_value.invoke.return_value = MagicMock(
            content="not a json response"
        )
        fragments = [
            {
                "pageNum": 1,
                "table": ExtractedTable(
                    headers=["x", "y"], rows=[[1, 2]], continued=False
                ),
            },
            {
                "pageNum": 2,
                "table": ExtractedTable(
                    headers=["x", "y"], rows=[[3, 4]], continued=True
                ),
            },
        ]

        plan = self.extractor.planMerge(fragments)

        self.assertEqual(len(plan.groups), 2)
        self.assertEqual(plan.groups[0].tableIndices, [0])
        self.assertEqual(plan.groups[1].tableIndices, [1])

    def test_response_normalization_handles_multipart_content(self):
        response = MagicMock(
            content=[
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
                {"type": "other", "data": "ignored"},
            ]
        )
        normalized = self.extractor._responseToText(response)
        self.assertEqual(normalized, "first\nsecond")

    def test_response_normalization_handles_empty_content(self):
        response = MagicMock(content=None)
        normalized = self.extractor._responseToText(response)
        self.assertEqual(normalized, "")


class PdfTableExtractorAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        llmPatcher = patch("nubrix.components.pdfTableExtractor.getGenaiLlm")
        langfusePatcher = patch("utils.llm.getLangfuseConfig", return_value={})
        self.addCleanup(llmPatcher.stop)
        self.addCleanup(langfusePatcher.stop)
        self.mockLlm = MagicMock()
        llmPatcher.start().return_value = self.mockLlm
        langfusePatcher.start()
        self.extractor = PdfTableExtractor()

    async def test_extract_page_async_parses_valid_json(self):
        self.mockLlm.ainvoke = AsyncMock(
            return_value=MagicMock(
                content='{"tables":[{"headers":["B"],"rows":[[10]],"continued":false}],"narrative":""}'
            )
        )

        result = await self.extractor.extractPageAsync("abc123")

        self.assertEqual(len(result.tables), 1)
        self.assertEqual(result.tables[0].headers, ["B"])
        self.assertEqual(result.tables[0].rows, [[10]])

    async def test_extract_pages_parallel_preserves_sorted_page_order(self):
        async def fake_extract_page_async(b64_image: str, **_kwargs) -> PageExtractionResult:
            if b64_image == "img2":
                return PageExtractionResult(
                    tables=[ExtractedTable(headers=["h"], rows=[[2]], continued=False)]
                )
            return PageExtractionResult(
                tables=[ExtractedTable(headers=["h"], rows=[[1]], continued=False)]
            )

        self.extractor.extractPageAsync = AsyncMock(side_effect=fake_extract_page_async)

        fragments = await self.extractor.extractPagesParallel(
            pageImages=[(2, "img2"), (1, "img1")],
            totalPages=2,
        )

        self.assertEqual([f["pageNum"] for f in fragments], [1, 2])
        self.assertEqual(len(fragments), 2)


if __name__ == "__main__":
    unittest.main()
