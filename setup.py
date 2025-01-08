from setuptools import setup, find_packages

HYPHEN_E_DOT = "-e ."

def get_requirements(requirements_path: str) -> list[str]:
    """
    Reads the requirements file and returns a list of packages.

    Args:
        requirements_path (str): Path to the requirements file.

    Returns:
        list[str]: List of packages required for the project.
    """
    with open(requirements_path, "r") as file:
        requirements = file.read().strip().split("\n")
    if HYPHEN_E_DOT in requirements:
        requirements.remove(HYPHEN_E_DOT)
    return requirements

setup(
    name="AnalyticsHub",
    author="Rauhan Ahmed Siddiqui",
    author_email="rauhaan.siddiqui@gmail.com",
    version="0.1",
    packages=find_packages(),
    install_requires=get_requirements(requirements_path="requirements.txt"),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)