"""
taxConfigLoader.py

Loads, validates, and exposes the billing package V1 tax-rule configuration from a
local JSON file.  The config is loaded once at import time and cached
in-memory.  Subsequent calls to ``getTaxConfig()`` return the cached
object; ``reloadTaxConfig()`` forces a re-read from disk.

The loader enforces structural validation (required keys, rule
integrity) and logs the active tax rule version on first load so
operators can confirm which rules are live.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["getTaxConfig", "reloadTaxConfig"]


from utils.logger import logger
import json
import os


_TAX_RULE_FILE_PATH = "config/tax_rules.json"

_cachedTaxConfig: dict | None = None

_REQUIRED_TOP_LEVEL_KEYS = {"version", "default_currency", "rules"}
_REQUIRED_RULE_KEYS = {
    "product_tax_code",
    "jurisdiction_scope",
    "effective_from",
    "effective_to",
    "rule",
}
_REQUIRED_RULE_INNER_KEYS = {
    "type",
    "intra_state",
    "inter_state",
    "cess",
    "rounding",
    "place_of_supply",
}


def _validateTaxConfig(config: dict) -> None:
    """
    Validate structural integrity of the loaded tax config.

    Raises:
        ValueError: If required keys or rule structure is invalid.
    """
    missingTop = _REQUIRED_TOP_LEVEL_KEYS - set(config.keys())
    if missingTop:
        raise ValueError(f"Tax config missing required top-level keys: {missingTop}")

    rules = config.get("rules")
    if not isinstance(rules, list) or len(rules) == 0:
        raise ValueError("Tax config 'rules' must be a non-empty list")

    activeRules: dict[str, list[str]] = {}

    for idx, rule in enumerate(rules):
        missingRule = _REQUIRED_RULE_KEYS - set(rule.keys())
        if missingRule:
            raise ValueError(
                f"Tax rule at index {idx} missing required keys: {missingRule}"
            )

        innerRule = rule.get("rule")
        if not isinstance(innerRule, dict):
            raise ValueError(f"Tax rule at index {idx}: 'rule' must be a dict")

        missingInner = _REQUIRED_RULE_INNER_KEYS - set(innerRule.keys())
        if missingInner:
            raise ValueError(
                f"Tax rule at index {idx}: inner 'rule' missing keys: {missingInner}"
            )

        if rule.get("effective_to") is None:
            compositeKey = f"{rule['product_tax_code']}:{rule['jurisdiction_scope']}"
            activeRules.setdefault(compositeKey, []).append(str(idx))

    for compositeKey, indices in activeRules.items():
        if len(indices) > 1:
            raise ValueError(
                f"Multiple active rules (effective_to=null) for "
                f"'{compositeKey}' at indices {indices}. "
                f"Only one active rule per product_tax_code + jurisdiction_scope is allowed."
            )


def _loadFromFile(filePath: str) -> dict:
    """
    Read and validate the tax config JSON from disk.

    Args:
        filePath: Absolute or relative path to the JSON file.

    Returns:
        Validated tax config dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If the config structure is invalid.
    """
    resolvedPath = os.path.abspath(filePath)
    if not os.path.isfile(resolvedPath):
        raise FileNotFoundError(
            f"Tax config file not found at '{resolvedPath}'. "
            f"Ensure config/tax_rules.json exists in the project root."
        )

    with open(resolvedPath, "r", encoding="utf-8") as f:
        config = json.load(f)

    _validateTaxConfig(config)
    return config


def getTaxConfig() -> dict:
    """
    Return the cached tax configuration, loading from disk on first call.

    Returns:
        dict: The validated tax config.

    Raises:
        RuntimeError: If the tax config could not be loaded.
    """
    global _cachedTaxConfig
    if _cachedTaxConfig is not None:
        return _cachedTaxConfig

    return reloadTaxConfig()


def reloadTaxConfig() -> dict:
    """
    Force-reload the tax configuration from disk.

    Returns:
        dict: The freshly loaded and validated tax config.

    Raises:
        RuntimeError: If loading or validation fails.
    """
    global _cachedTaxConfig

    try:
        config = _loadFromFile(_TAX_RULE_FILE_PATH)
        _cachedTaxConfig = config
        logger.info(
            f"Tax config loaded successfully — "
            f"version={config.get('version')}, "
            f"ruleCount={len(config.get('rules', []))}, "
            f"source={_TAX_RULE_FILE_PATH}"
        )
        return config
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Tax config load failed: {e}")
        raise RuntimeError(f"Tax config load failed: {e}") from e
