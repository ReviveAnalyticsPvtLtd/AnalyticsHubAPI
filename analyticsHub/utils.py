"""
utils.py

Utility functions for AnalyticsHub project.
This module provides helper functions for Supabase client creation, token verification, YAML reading, and configuration file parsing.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["readYaml", "getConfig"]


import configparser
import yaml

def readYaml(filePath: str) -> dict:
    """
    Read a YAML file and return its contents as a dictionary.

    Args:
        filePath (str): Path to the YAML file.

    Returns:
        dict: Parsed contents of the YAML file.
    """
    with open(filePath, "r", encoding="utf-8") as f:
        content = yaml.safe_load(f)
    return content 

def getConfig(path: str) -> dict:
    """
    Read a configuration file and return a ConfigParser object.

    Args:
        path (str): Path to the configuration file.

    Returns:
        dict: ConfigParser object containing the configuration.
    """
    config = configparser.ConfigParser()
    config.read(path)
    return config