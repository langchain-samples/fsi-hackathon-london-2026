"""Load the three hackathon projects side by side in one Python process.

Each project folder has its own top-level `utils`, `tools` and `agent` modules, so importing two of them normally
collides: the second `import utils` returns the first project's package. `load_project` imports one project with
its folder first on `sys.path`, keeps the modules it loaded, then removes them from `sys.modules` so the next
project gets a clean slate. The loaded functions keep working because they hold references to their own modules.

This relies on the projects importing at module top level (they do). A function that runs `from utils import x`
lazily, inside its body, would pick up the wrong project; keep imports at the top when you edit a pillar.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
_SHADOWED = ("utils", "tools", "agent", "sub_agent", "evals", "scripts")
_cache: dict[str, "Project"] = {}


@dataclass
class Project:
    folder: str
    modules: dict[str, ModuleType] = field(default_factory=dict)

    def __getitem__(self, name: str) -> ModuleType:
        """A module this project loaded, for example `project["utils.db"]` or `project["agent"]`."""
        return self.modules[name]


def _is_shadowed(name: str) -> bool:
    return name.split(".")[0] in _SHADOWED


def load_project(folder: str, *extra_modules: str) -> Project:
    """Import a project's `agent.py` (and any `extra_modules`, such as "utils.transaction_processing")."""
    if folder in _cache and all(m in _cache[folder].modules for m in extra_modules):
        return _cache[folder]

    root = str(REPO_ROOT / folder)
    saved = {name: mod for name, mod in sys.modules.items() if _is_shadowed(name)}
    for name in saved:
        del sys.modules[name]
    if folder in _cache:  # put this project's own modules back while we import more of it
        sys.modules.update(_cache[folder].modules)
    sys.path.insert(0, root)
    loaded: dict[str, ModuleType] = {}
    try:
        for module in ("agent", *extra_modules):
            importlib.import_module(module)
        loaded = {name: mod for name, mod in sys.modules.items() if _is_shadowed(name)}
    finally:
        sys.path.remove(root)
        for name in [n for n in sys.modules if _is_shadowed(n)]:
            del sys.modules[name]
        sys.modules.update(saved)

    _cache[folder] = Project(folder, loaded)
    return _cache[folder]
