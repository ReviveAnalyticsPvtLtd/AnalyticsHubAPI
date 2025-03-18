from dataclasses import dataclass, field

@dataclass
class REPLManager:
    manager: dict = field(default_factory=dict)
replManager = REPLManager()