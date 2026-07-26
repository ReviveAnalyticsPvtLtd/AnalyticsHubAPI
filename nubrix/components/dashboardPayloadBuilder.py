"""
dashboardPayloadBuilder.py

Assembles the text payload the insight LLM reasons over. Replaces the previous
approach of pretty-printing raw widget JSON alongside a dashboard screenshot:
each widget becomes a labelled block carrying its provenance, its downsampled
rows as CSV, and its own statistical signals, under a global token budget that
degrades low-priority widgets to aggregate summaries rather than truncating.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["DashboardPayloadBuilder", "estimateTokens"]

from nubrix.components.widgetProvenance import (
    extractProvenance,
    formatFilters,
    formatProvenance,
    referencedTables,
)
from nubrix.components.widgetSerializer import (
    downsampleRows,
    normalizeWidgetData,
    renderTable,
    summariseRows,
)
from nubrix.components.signalEngine import formatWidgetSignals
from utils.exceptionHandler import CustomException
from utils.logger import logger

# Lower number renders first and degrades last. Cards carry the headline
# numbers, time-series charts carry trend, distributions carry structure, and
# raw tables and pivots are the most token-hungry per unit of insight.
CHART_PRIORITY = {
    "card": 0,
    "line": 1,
    "bar": 1,
    "area": 1,
    "radar": 2,
    "polarArea": 2,
    "pie": 2,
    "doughnut": 2,
    "scatter": 2,
    "geoMap": 3,
    "table": 4,
    "pivot": 4,
}
DEFAULT_PRIORITY = 3

CHARS_PER_TOKEN = 4


def estimateTokens(text: str) -> int:
    """Approximates the token cost of a text block at four characters per token."""
    if not text:
        return 0
    return -(-len(text) // CHARS_PER_TOKEN)


class DashboardPayloadBuilder:
    """
    Builds a compact, prioritized, token-bounded text representation of a
    dashboard page for LLM insight generation.
    """

    def __init__(self, fullRowLimit: int = 40, compactRowLimit: int = 10, tokenBudget: int = 12000):
        """
        Args:
            fullRowLimit (int): Rows rendered per widget before any degradation.
            compactRowLimit (int): Rows rendered per widget at the first
                degradation step.
            tokenBudget (int): Ceiling for the whole payload, in estimated tokens.
        """
        logger.info("Initializing DashboardPayloadBuilder.")
        self.fullRowLimit = fullRowLimit
        self.compactRowLimit = compactRowLimit
        self.tokenBudget = tokenBudget

    @staticmethod
    def _widgetRef(widget: dict, index: int) -> str:
        """Returns the stable short reference the LLM cites for a widget."""
        return str(widget.get("ref") or widget.get("id") or f"W{index + 1}")

    @staticmethod
    def _priority(widget: dict) -> int:
        """Returns the render priority for a widget's chart type."""
        return CHART_PRIORITY.get(str(widget.get("chartType") or "").strip(), DEFAULT_PRIORITY)

    def _renderCardLine(self, widget: dict, ref: str, normalized: dict, provenance: dict) -> str:
        """Renders a KPI card as a single line."""
        title = widget.get("title") or "Untitled"
        value = normalized.get("value")
        label = normalized.get("label") or widget.get("label")
        line = f"- {ref} {title}: {value if value is not None else 'unavailable'}"
        if label:
            line += f" ({label})"
        source = formatProvenance(provenance)
        if source:
            line += f" — {source}"
        return line

    def _renderWidgetBlock(self, widget: dict, ref: str, normalized: dict, provenance: dict,
                           signalLine: str | None, rowLimit: int, summaryOnly: bool) -> str:
        """
        Renders one non-card widget as a labelled block.

        Args:
            rowLimit (int): Maximum rows to render when not summary-only.
            summaryOnly (bool): When True, emit an aggregate line instead of rows.
        """
        title = widget.get("title") or "Untitled"
        chartType = widget.get("chartType") or "unknown"
        columns = normalized.get("columns") or []
        rows = normalized.get("rows") or []

        lines = [f"### {ref} {title} [{chartType}]"]

        source = formatProvenance(provenance)
        if source:
            lines.append(f"source: {source}")
        filters = formatFilters(provenance)
        if filters:
            lines.append(f"filters: {filters}")
        if signalLine:
            lines.append(f"signals: {signalLine}")

        if not rows:
            lines.append("data: unavailable")
            return "\n".join(lines)

        if summaryOnly:
            summary = summariseRows(columns, rows) or f"{len(rows)} rows, no numeric columns"
            lines.append(f"rows: {len(rows)}")
            lines.append(f"summary: {summary}")
            return "\n".join(lines)

        selectedRows, note = downsampleRows(columns, rows, rowLimit)
        lines.append(f"rows: {len(rows)}" + (f" ({note})" if note else ""))
        lines.append(renderTable(columns, selectedRows))
        return "\n".join(lines)

    def _renderSchema(self, metadata: dict, usedTables: set) -> str:
        """
        Renders database schema, in full for referenced tables and as a
        names-only index for the rest.

        The index is retained so the model can still propose `missing_data`
        KPIs that the project's raw tables would support.
        """
        if not metadata:
            return ""

        detailed = []
        index = []
        for tableName, table in metadata.items():
            if not isinstance(table, dict):
                continue
            columns = table.get("columns") or []
            if tableName in usedTables:
                description = table.get("description") or ""
                header = f"- {tableName}" + (f": {description}" if description else "")
                columnLines = [
                    "    - {}{}".format(
                        column.get("name"),
                        f" — {column.get('description')}" if column.get("description") else "",
                    )
                    for column in columns
                    if isinstance(column, dict) and column.get("name")
                ]
                detailed.append("\n".join([header] + columnLines))
            else:
                names = ", ".join(
                    str(column.get("name"))
                    for column in columns
                    if isinstance(column, dict) and column.get("name")
                )
                index.append(f"- {tableName}: {names}")

        sections = []
        if detailed:
            sections.append("### tables_used_by_this_dashboard\n" + "\n".join(detailed))
        if index:
            sections.append(
                "### other_tables_available_for_missing_kpis (column names only)\n" + "\n".join(index)
            )
        if not sections:
            return ""
        return "## database_schema\n" + "\n\n".join(sections)

    @staticmethod
    def _renderDomain(domainContext: dict) -> str:
        """Renders the domain playbook as bulleted key/value lines."""
        if not domainContext:
            return ""

        lines = ["## domain_context"]
        for sectionName in ("profile", "playbook"):
            section = domainContext.get(sectionName)
            if not isinstance(section, dict) or not section:
                continue
            lines.append(f"### {sectionName}")
            for key, value in section.items():
                lines.append(f"- {key}: {value}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def build(self, context: dict) -> str:
        """
        Assembles the complete dashboard payload text.

        Args:
            context (dict): Output of `InsightContextBuilder.buildContext` with
                `statisticalSummary` attached.

        Returns:
            str: The payload text, within the configured token budget.

        Raises:
            CustomException: If assembly fails.
        """
        try:
            dashboard = context.get("dashboard") or {}
            chartData = context.get("chartData") or []
            perWidget = (context.get("statisticalSummary") or {}).get("perWidget") or {}

            usedTables = set()
            cards = []
            renderables = []

            for index, widget in enumerate(chartData):
                ref = self._widgetRef(widget, index)
                provenance = extractProvenance(widget.get("generatedCode"))
                usedTables.update(referencedTables(provenance))
                normalized = normalizeWidgetData(widget)

                if normalized.get("kind") == "scalar":
                    cards.append(self._renderCardLine(widget, ref, normalized, provenance))
                    continue

                renderables.append({
                    "widget": widget,
                    "ref": ref,
                    "normalized": normalized,
                    "provenance": provenance,
                    "signalLine": formatWidgetSignals(perWidget.get(ref) or {}),
                    "priority": self._priority(widget),
                    "rowLimit": self.fullRowLimit,
                    "summaryOnly": False,
                })

            renderables.sort(key=lambda item: item["priority"])

            header = "## dashboard\n" + "\n".join(
                filter(None, [
                    f"page: {dashboard.get('pageName') or dashboard.get('pageId') or 'unknown'}",
                    f"widgets: {len(chartData)}",
                ])
            )
            cardSection = "## kpi_cards\n" + "\n".join(cards) if cards else ""
            schemaSection = self._renderSchema(context.get("metadata") or {}, usedTables)
            domainSection = self._renderDomain(context.get("domainContext") or {})

            fixedCost = sum(
                estimateTokens(section)
                for section in (header, cardSection, schemaSection, domainSection)
            )

            def renderAll() -> list[str]:
                return [
                    self._renderWidgetBlock(
                        item["widget"], item["ref"], item["normalized"], item["provenance"],
                        item["signalLine"], item["rowLimit"], item["summaryOnly"],
                    )
                    for item in renderables
                ]

            blocks = renderAll()

            # Degradation ladder: compact the lowest-priority widgets first, then
            # reduce them to aggregate summaries, and only then drop blocks.
            for degrade in ("compact", "summarise"):
                for item in sorted(renderables, key=lambda entry: -entry["priority"]):
                    if fixedCost + sum(estimateTokens(block) for block in blocks) <= self.tokenBudget:
                        break
                    if degrade == "compact":
                        item["rowLimit"] = self.compactRowLimit
                    else:
                        item["summaryOnly"] = True
                    blocks = renderAll()

            while renderables and fixedCost + sum(estimateTokens(block) for block in blocks) > self.tokenBudget:
                dropped = renderables.pop()
                logger.warning(f"Dropping widget {dropped['ref']} from insight payload: token budget exceeded.")
                blocks = renderAll()

            widgetSection = "## widgets\n" + "\n\n".join(blocks) if blocks else ""
            payload = "\n\n".join(
                section for section in (header, cardSection, widgetSection, schemaSection, domainSection)
                if section
            )

            logger.info(
                f"Dashboard payload built: {len(chartData)} widgets, "
                f"~{estimateTokens(payload)} tokens (budget {self.tokenBudget})."
            )
            return payload
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
