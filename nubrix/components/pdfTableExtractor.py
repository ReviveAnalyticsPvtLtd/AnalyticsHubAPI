"""
pdfTableExtractor.py

Extracts tables from PDF pages by rasterizing them to images with PyMuPDF
and sending them to a Gemini Vision Language Model (VLM) via LangChain's
ChatGoogleGenerativeAI integration.

Response validation is enforced with Pydantic models to guarantee consistent
output regardless of the upstream model.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "PdfTableExtractor",
    "ExtractedTable",
    "PageExtractionResult",
    "MergeGroup",
    "MergePlan",
]

from utils.exceptionHandler import CustomException
from nubrix.utils import readYaml, getConfig
from utils.llm import getGenaiLlm
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, field_validator
from utils.logger import logger
from typing import Any
import asyncio
import base64
import json
import time
import fitz
import os


class ExtractedTable(BaseModel):
    headers: list[str]
    rows: list[list[Any]]
    continued: bool = False

    @field_validator("headers")
    @classmethod
    def cleanHeaders(cls, v: list[str]) -> list[str]:
        cleaned = []
        seen: dict[str, int] = {}
        for i, h in enumerate(v):
            h = str(h).strip() if h else ""
            if not h:
                h = f"column_{i}"
            if h in seen:
                seen[h] += 1
                h = f"{h}_{seen[h]}"
            else:
                seen[h] = 0
            cleaned.append(h)
        return cleaned

    @field_validator("rows")
    @classmethod
    def normalizeRows(cls, v: list[list[Any]], info) -> list[list[Any]]:
        headers = info.data.get("headers", [])
        numCols = len(headers) if headers else 0
        if numCols == 0:
            return v
        normalized = []
        for row in v:
            if not isinstance(row, list):
                continue
            if len(row) == numCols:
                normalized.append(row)
            elif len(row) > numCols:
                normalized.append(row[:numCols])
            else:
                normalized.append(row + [None] * (numCols - len(row)))
        return normalized


class PageExtractionResult(BaseModel):
    tables: list[ExtractedTable] = []
    narrative: str = ""


class MergeGroup(BaseModel):
    tableIndices: list[int]
    canonicalHeaders: list[str]


class MergePlan(BaseModel):
    groups: list[MergeGroup]


class PdfTableExtractor:
    """
    Rasterizes PDF pages and extracts tables via a VLM.

    Configuration is read from config.ini [PDFTABLE] and the extraction
    prompt from prompts.yaml (key: pdfTableExtractionPrompt).
    """

    def __init__(self, projectId: str = None, userId: str = None):
        logger.info("Initializing PdfTableExtractor.")
        self.projectId = projectId
        self.userId = userId
        configPath = os.path.join(os.getcwd(), "config.ini")
        yamlPath = os.path.join(os.getcwd(), "prompts.yaml")

        config = getConfig(configPath)
        prompts = readYaml(filePath=yamlPath)
        self.prompt = prompts.get("pdfTableExtractionPrompt", "")
        self.mergePrompt = prompts.get("pdfTableMergePrompt", "")

        self.dpi = config.getint("PDFTABLE", "dpi", fallback=300)
        self.model = config.get("PDFTABLE", "model")
        self.maxTokens = config.getint("PDFTABLE", "maxTokens", fallback=8192)
        self.maxRetries = config.getint("PDFTABLE", "maxRetries", fallback=2)
        self.retryDelay = config.getfloat("PDFTABLE", "retryDelay", fallback=2.0)
        self.maxConcurrency = config.getint("PDFTABLE", "maxConcurrency", fallback=5)
        self.llm = getGenaiLlm(
            model=self.model,
            max_tokens=self.maxTokens,
        )

    @staticmethod
    def renderPageToBase64(page: fitz.Page, dpi: int = 300) -> str:
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        imgBytes = pix.tobytes("jpeg")
        pix = None
        b64 = base64.b64encode(imgBytes).decode("utf-8")
        del imgBytes
        return b64

    @staticmethod
    def _normalizeModelText(content: Any) -> str:
        """Normalize model output content to plain text."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            textParts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    textParts.append(part)
                    continue
                if isinstance(part, dict):
                    partText = part.get("text")
                    if isinstance(partText, str):
                        textParts.append(partText)
                        continue
            normalized = "\n".join(p.strip() for p in textParts if p and p.strip())
            if normalized:
                return normalized
        return str(content)

    @classmethod
    def _responseToText(cls, response: Any) -> str:
        content = getattr(response, "content", response)
        return cls._normalizeModelText(content)

    def extractPage(self, b64Image: str, callbacks: list | None = None) -> PageExtractionResult:
        """
        Send a page image to the VLM and return validated extraction results.

        Raises CustomException on rate-limit errors or after exhausting retries.
        """
        lastException = None

        for attempt in range(1, self.maxRetries + 2):
            try:
                logger.info(
                    f"PdfTableExtractor: VLM call attempt {attempt} "
                    f"(model={self.model})."
                )
                from utils.llm import getLangfuseConfig
                extractConfig = getLangfuseConfig(trace_name="PdfTableExtractor-ExtractPage", projectId=self.projectId, userId=self.userId)
                if callbacks:
                    extractConfig.setdefault("callbacks", []).extend(callbacks)
                response = self.llm.invoke(
                    [
                        SystemMessage(content=self.prompt),
                        HumanMessage(
                            content=[
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{b64Image}"
                                    },
                                }
                            ]
                        ),
                    ],
                    config=extractConfig or None,
                )
                raw = self._responseToText(response)
                return self._parseAndValidate(raw)

            except Exception as e:
                lastException = e
                errMsg = str(e).lower()

                if "429" in errMsg or "rate" in errMsg or "quota" in errMsg:
                    raise CustomException(
                        e,
                        statusCode=429,
                        uiMessage=(
                            "VLM API rate limit reached during PDF extraction. "
                            "Please wait a moment and try again."
                        ),
                    )

                if attempt <= self.maxRetries:
                    logger.warning(
                        f"PdfTableExtractor: attempt {attempt} failed – "
                        f"retrying in {self.retryDelay}s. Error: {e}"
                    )
                    time.sleep(self.retryDelay)

        raise CustomException(
            lastException,
            uiMessage="PDF table extraction failed after retries. Try again later.",
        )

    async def extractPageAsync(self, b64Image: str, callbacks: list | None = None) -> PageExtractionResult:
        """
        Async variant of extractPage. Sends a page image to the VLM and
        returns validated extraction results using ChatGoogleGenerativeAI.

        Raises CustomException on rate-limit errors or after exhausting retries.
        """
        lastException = None

        for attempt in range(1, self.maxRetries + 2):
            try:
                logger.info(
                    f"PdfTableExtractor: async VLM call attempt {attempt} "
                    f"(model={self.model})."
                )
                from utils.llm import getLangfuseConfig
                extractConfig = getLangfuseConfig(trace_name="PdfTableExtractor-ExtractPageAsync", projectId=self.projectId, userId=self.userId)
                if callbacks:
                    extractConfig.setdefault("callbacks", []).extend(callbacks)
                response = await self.llm.ainvoke(
                    [
                        SystemMessage(content=self.prompt),
                        HumanMessage(
                            content=[
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{b64Image}"
                                    },
                                }
                            ]
                        ),
                    ],
                    config=extractConfig or None,
                )
                raw = self._responseToText(response)
                return self._parseAndValidate(raw)

            except Exception as e:
                lastException = e
                errMsg = str(e).lower()

                if "429" in errMsg or "rate" in errMsg or "quota" in errMsg:
                    raise CustomException(
                        e,
                        statusCode=429,
                        uiMessage=(
                            "VLM API rate limit reached during PDF extraction. "
                            "Please wait a moment and try again."
                        ),
                    )

                if attempt <= self.maxRetries:
                    logger.warning(
                        f"PdfTableExtractor: async attempt {attempt} failed – "
                        f"retrying in {self.retryDelay}s. Error: {e}"
                    )
                    await asyncio.sleep(self.retryDelay)

        raise CustomException(
            lastException,
            uiMessage="PDF table extraction failed after retries. Try again later.",
        )

    async def extractPagesParallel(
        self, pageImages: list[tuple[int, str]], totalPages: int, callbacks: list | None = None
    ) -> list[dict]:
        """
        Extract tables from multiple page images concurrently.

        Args:
            pageImages: list of (pageNum, base64Image) tuples.
            totalPages: total page count (for logging).
            callbacks: optional LangChain callbacks for observability/credit tracking.

        Returns:
            Sorted list of {"pageNum": int, "table": ExtractedTable} dicts,
            ordered by pageNum (same ordering the sequential loop produced).
        """
        semaphore = asyncio.Semaphore(self.maxConcurrency)

        async def _processOne(pageNum: int, b64: str) -> list[dict]:
            async with semaphore:
                result = await self.extractPageAsync(b64, callbacks=callbacks)
                logger.info(
                    f"Page {pageNum}/{totalPages}: "
                    f"{len(result.tables)} table(s) extracted"
                )
                return [
                    {"pageNum": pageNum, "table": table}
                    for table in result.tables
                    if table.rows
                ]

        tasks = [_processOne(pn, img) for pn, img in pageImages]
        results = await asyncio.gather(*tasks)

        fragments: list[dict] = []
        for pageFragments in results:
            fragments.extend(pageFragments)

        fragments.sort(key=lambda f: f["pageNum"])
        return fragments

    def planMerge(self, fragments: list[dict], callbacks: list | None = None) -> MergePlan:
        """
        Ask the LLM to group table fragments into logical tables.

        Args:
            fragments: list of {"pageNum": int, "table": ExtractedTable}

        Returns:
            MergePlan with groups of fragment indices and canonical headers.
            On failure, falls back to treating every fragment as its own group.
        """
        fallback = MergePlan(
            groups=[
                MergeGroup(
                    tableIndices=[i],
                    canonicalHeaders=f["table"].headers,
                )
                for i, f in enumerate(fragments)
            ]
        )

        if len(fragments) <= 1:
            return fallback

        summaryLines = []
        for i, f in enumerate(fragments):
            tbl = f["table"]
            sample = [str(r) for r in tbl.rows[:2]]
            summaryLines.append(
                f"Fragment {i} | page {f['pageNum']} | "
                f"cols {len(tbl.headers)} | continued={tbl.continued} | "
                f"headers={tbl.headers} | "
                f"sample_rows={sample}"
            )
        summaryText = "\n".join(summaryLines)

        try:
            logger.info(
                f"PdfTableExtractor: requesting merge plan for "
                f"{len(fragments)} fragments."
            )
            mergeLlm = self.llm.bind(
                max_tokens=self.maxTokens,
            )
            from utils.llm import getLangfuseConfig
            mergeConfig = getLangfuseConfig(trace_name="PdfTableExtractor-MergePlan", projectId=self.projectId, userId=self.userId)
            if callbacks:
                mergeConfig.setdefault("callbacks", []).extend(callbacks)
            response = mergeLlm.invoke(
                [
                    SystemMessage(content=self.mergePrompt),
                    HumanMessage(content=summaryText),
                ],
                config=mergeConfig or None,
            )
            raw = self._responseToText(response)
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            data = json.loads(text)
            plan = MergePlan(**data)

            referenced = set()
            for g in plan.groups:
                for idx in g.tableIndices:
                    if idx < 0 or idx >= len(fragments):
                        logger.warning(
                            f"PdfTableExtractor: merge plan references "
                            f"invalid index {idx} – using fallback."
                        )
                        return fallback
                    referenced.add(idx)

            if referenced != set(range(len(fragments))):
                missing = set(range(len(fragments))) - referenced
                for idx in missing:
                    plan.groups.append(
                        MergeGroup(
                            tableIndices=[idx],
                            canonicalHeaders=fragments[idx]["table"].headers,
                        )
                    )

            logger.info(
                f"PdfTableExtractor: merge plan has "
                f"{len(plan.groups)} group(s)."
            )
            return plan

        except Exception as e:
            logger.warning(
                f"PdfTableExtractor: merge planning failed – "
                f"falling back to individual tables. Error: {e}"
            )
            return fallback

    @staticmethod
    def _parseAndValidate(raw: str) -> PageExtractionResult:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "PdfTableExtractor: model returned non-JSON – "
                "treating entire response as narrative text."
            )
            return PageExtractionResult(narrative=text)

        rawTables = data.get("tables", [])
        validatedTables: list[ExtractedTable] = []
        for tbl in rawTables:
            try:
                if isinstance(tbl, dict):
                    validatedTables.append(ExtractedTable(**tbl))
                elif isinstance(tbl, list) and len(tbl) >= 2:
                    validatedTables.append(
                        ExtractedTable(headers=tbl[0], rows=tbl[1:])
                    )
            except Exception as e:
                logger.warning(f"PdfTableExtractor: skipping malformed table – {e}")

        return PageExtractionResult(
            tables=validatedTables,
            narrative=data.get("narrative", ""),
        )
