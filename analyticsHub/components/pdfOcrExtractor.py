"""
pdfOcrExtractor.py

This module provides the PdfOcrExtractor class for extracting text and tables
from scanned / image-based PDF pages using the Groq vision API.

It renders each page to an image with PyMuPDF, sends the base64-encoded image
to a Groq vision model, and parses the structured JSON response into tables
and narrative text that match the output contract of the native pdfplumber
extraction pipeline.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["PdfOcrExtractor"]

from utils.exceptionHandler import CustomException
from analyticsHub.utils import readYaml, getConfig
from dataclasses import dataclass, field
from utils.logger import logger
from groq import Groq
import base64
import fitz  
import json
import time
import io
import os


@dataclass
class PdfOcrExtractorConfig:
    """
    Configuration dataclass for PdfOcrExtractor.

    Attributes:
        yamlPath (str): Path to prompts.yaml.
        configPath (str): Path to config.ini.
        dpi (int): Rendering resolution for PDF pages (default 200).
    """
    yamlPath: str = os.path.join(os.getcwd(), "prompts.yaml")
    configPath: str = os.path.join(os.getcwd(), "config.ini")
    dpi: int = 200


class PdfOcrExtractor:
    """
    Extracts tables and narrative text from scanned PDF pages by rendering
    them as images and sending them to the Groq vision API for OCR.

    The output contract matches what loadPdfData expects:
        - tables: list of list-of-lists  (each table = [headers, row1, row2, …])
        - narrative: str
    """

    def __init__(self, dpi: int = 200):
        """
        Initializes the PdfOcrExtractor:
            - Loads system prompt from YAML
            - Loads model configuration from config.ini [PDFOCR]
            - Sets up the Groq API client
        """
        logger.info("Initializing PdfOcrExtractor.")
        self.cfg = PdfOcrExtractorConfig(dpi=dpi)
        self.config = getConfig(self.cfg.configPath)
        self.prompt = readYaml(filePath=self.cfg.yamlPath).get(
            "pdfOcrExtractionPrompt", ""
        )
        self.client = Groq()
        self.model = self.config.get("PDFOCR", "model")
        self.temperature = self.config.getfloat("PDFOCR", "temperature", fallback=0.3)
        self.maxTokens = self.config.getint("PDFOCR", "maxTokens", fallback=4096)
        self.maxRetries = self.config.getint("PDFOCR", "maxRetries", fallback=2)
        self.retryDelay = self.config.getfloat("PDFOCR", "retryDelay", fallback=2.0)

    @staticmethod
    def renderPageToBase64(page: fitz.Page, dpi: int = 200) -> str:
        """
        Render a PyMuPDF page to a PNG base64 string.

        Args:
            page: A fitz.Page object.
            dpi: Target resolution (default 200).

        Returns:
            str: Base64-encoded PNG image data.
        """
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return base64.b64encode(pix.tobytes("png")).decode("utf-8")

    def extractPage(self, b64Image: str) -> dict:
        """
        Send a single page image to the Groq vision model and return the
        parsed OCR result.

        Args:
            b64Image (str): Base64-encoded PNG of the page.

        Returns:
            dict: {"tables": [...], "narrative": "..."} parsed from the
                  model response.

        Raises:
            CustomException: On Groq API failure after retries, including
                             429 rate-limit errors which are surfaced with
                             a user-friendly message.
        """
        lastException = None

        for attempt in range(1, self.maxRetries + 2):  
            try:
                logger.info(
                    f"PdfOcrExtractor: Groq vision call attempt {attempt} "
                    f"(model={self.model})."
                )
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": [{"type": "text", "text": self.prompt}],
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{b64Image}"
                                    },
                                }
                            ],
                        },
                    ],
                    temperature=self.temperature,
                    max_completion_tokens=self.maxTokens,
                    top_p=1,
                    stream=False,
                    stop=None,
                )
                raw = completion.choices[0].message.content
                return self._parseResponse(raw)

            except Exception as e:
                lastException = e
                errMsg = str(e).lower()

                if "429" in errMsg or "rate" in errMsg or "quota" in errMsg:
                    raise CustomException(
                        e,
                        statusCode=429,
                        uiMessage=(
                            "Groq API rate limit reached while performing OCR. "
                            "Please wait a moment and try again."
                        ),
                    )

                if attempt <= self.maxRetries:
                    logger.warning(
                        f"PdfOcrExtractor: attempt {attempt} failed – "
                        f"retrying in {self.retryDelay}s. Error: {e}"
                    )
                    time.sleep(self.retryDelay)

        raise CustomException(
            lastException,
            uiMessage="OCR extraction failed after retries. Try again later.",
        )

    @staticmethod
    def _parseResponse(raw: str) -> dict:
        """
        Parse the raw model response into the expected dict structure.

        Handles markdown code fences that some models include despite
        instructions not to.

        Returns:
            dict with keys "tables" (list[list[list[str]]]) and
            "narrative" (str).
        """
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
                "PdfOcrExtractor: model returned non-JSON – treating entire "
                "response as narrative text."
            )
            return {"tables": [], "narrative": text}

        tables = data.get("tables", [])
        narrative = data.get("narrative", "")

        validTables = []
        for tbl in tables:
            if isinstance(tbl, list) and len(tbl) >= 2:
                validTables.append(tbl)
            else:
                logger.warning(
                    "PdfOcrExtractor: Skipping malformed table entry."
                )
        return {"tables": validTables, "narrative": narrative}
