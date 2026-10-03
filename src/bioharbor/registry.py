"""Tool registry and the `@tool` decorator used by built-in tools and plugins.

A tool is a plain function `fn(params: SomeModel, ctx: RunContext) -> ToolResult`.
Third-party packages register tools by exposing a module under the
`bioharbor.tools` entry-point group; importing that module runs its `@tool` decorators.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, get_type_hints

from pydantic import BaseModel

from .results import ToolResult

log = logging.getLogger(__name__)

BUILTIN_MODULES = [
    "bioharbor.tools.sequence",
    "bioharbor.tools.homology",
    "bioharbor.tools.structure",
]


@dataclass(frozen=True)
class Resources:
    """What a tool needs. `gpu_mem_gb` may be a function of the params (e.g. sequence length)."""

    gpu: bool = False
    gpu_mem_gb: float | Callable[[BaseModel], float] = 0.0
    timeout_s: float = 3600.0

    def gpu_mem_for(self, params: BaseModel) -> float:
        return self.gpu_mem_gb(params) if callable(self.gpu_mem_gb) else self.gpu_mem_gb


@dataclass
class RunContext:
    """Passed to every tool invocation."""

    workdir: Path
    gpu_index: int | None = None
    home: Path | None = None
    timeout_s: float = 3600.0
    logs: list[str] = field(default_factory=list)
    # Tool-specific facts for provenance: binary/model/database versions, peak memory...
    provenance: dict[str, Any] = field(default_factory=dict)

    def log(self, msg: str) -> None:
        self.logs.append(msg)
        log.debug(msg)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable[[Any, RunContext], ToolResult]
    params_model: type[BaseModel]
    description: str
    version: str = "0"
    resources: Resources = field(default_factory=Resources)
    # Slow tools run as background jobs; fast ones are answered inline.
    slow: bool = False
    # Cheap checks run *before* queueing (input, binaries, databases), so a bad request
    # fails in milliseconds instead of after waiting hours for a GPU. Args: params, home.
    precheck: Callable[[Any, Path], None] | None = None

    def run(self, params: BaseModel, ctx: RunContext) -> ToolResult:
        return self.fn(params, ctx)


_REGISTRY: dict[str, ToolSpec] = {}
_loaded = False


def tool(
    name: str | None = None,
    *,
    version: str = "0",
    resources: Resources | None = None,
    slow: bool = False,
    precheck: Callable[[Any, Path], None] | None = None,
) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
    """Register a function as a BioHarbor tool. The docstring becomes the agent-facing
    description; the first parameter's pydantic model becomes the input schema."""

    def decorator(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        hints = get_type_hints(fn)
        first = next(iter(inspect.signature(fn).parameters))
        model = hints.get(first)
        if not (inspect.isclass(model) and issubclass(model, BaseModel)):
            raise TypeError(f"{fn.__name__}: first parameter must be a pydantic model")
        spec = ToolSpec(
            name=name or fn.__name__,
            fn=fn,
            params_model=model,
            description=inspect.cleandoc(fn.__doc__ or ""),
            version=version,
            resources=resources or Resources(),
            slow=slow,
            precheck=precheck,
        )
        if spec.name in _REGISTRY and _REGISTRY[spec.name].fn is not fn:
            raise ValueError(f"duplicate tool name: {spec.name}")
        _REGISTRY[spec.name] = spec
        return fn

    return decorator


def load_tools() -> dict[str, ToolSpec]:
    """Import built-in tools and any plugins, then return the registry."""
    global _loaded
    if not _loaded:
        for mod in BUILTIN_MODULES:
            importlib.import_module(mod)
        for ep in entry_points(group="bioharbor.tools"):
            try:
                ep.load()
            except Exception:  # a broken plugin must not take the server down
                log.exception("failed to load plugin %s", ep.name)
        _loaded = True
    return dict(_REGISTRY)


def get_tool(name: str) -> ToolSpec:
    tools = load_tools()
    if name not in tools:
        raise KeyError(f"unknown tool {name!r}; available: {', '.join(sorted(tools))}")
    return tools[name]
