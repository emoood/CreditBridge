import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from ...devlog import events
from ...providers.base import ToolCall
from ...services.catalog_retriever import CatalogRetriever
from ...services.demo_repo import DemoRepo
from ..case_file import CaseFile
from ..precedent import PrecedentStore

MAX_RESULT_CHARS = 3000


@dataclass
class ToolContext:
    repo: DemoRepo
    retriever: CatalogRetriever
    role: str  # "matcher" | "reviewer"
    source_course_id: str  # resource scope: tools may only touch this course
    match_cited: dict[str, Any] | None = None  # reviewer: the matcher's cited pairs
    case: CaseFile = field(default_factory=CaseFile)
    precedent: PrecedentStore = field(default_factory=PrecedentStore)
    deadline: float | None = None  # wall-clock time after which the agent may only submit (set by the loop from the agent's seconds budget)


@dataclass
class WithImages:
    """A tool result that also shows the agent one or more images (base64 PNG). The loop adds them as a user message after the tool result."""
    data: Any
    images: list[str]


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    truncated: bool = False
    images: list[str] = field(default_factory=list)

    def to_message(self) -> str:
        payload = {"ok": self.ok, "data": self.data} if self.ok else {"ok": False, "error": self.error}
        s = json.dumps(payload, ensure_ascii=False)
        return s


class AgentTool:
    name: str
    description: str
    input_schema: type[BaseModel]
    roles: ClassVar[set[str]]

    async def execute(self, args: Any, ctx: ToolContext) -> Any:  # returns plain data
        raise NotImplementedError

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                  "parameters": self.input_schema.model_json_schema()}}


class ToolRegistry:
    def __init__(self, tools: list[AgentTool]):
        self._tools = {t.name: t for t in tools}

    def visible_tools(self, role: str) -> list[dict]:
        return [t.schema() for t in self._tools.values() if role in t.roles]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None or ctx.role not in tool.roles:
            res = ToolResult(False, error=f"Tool '{call.name}' is not available.")
        else:
            try:
                args = tool.input_schema.model_validate(call.arguments)
                data = await tool.execute(args, ctx)
                images: list[str] = []
                if isinstance(data, WithImages):
                    data, images = data.data, data.images
                res = ToolResult(True, data, images=images)
                if len(res.to_message()) > MAX_RESULT_CHARS:
                    res = ToolResult(True, {"truncated": True, "preview": json.dumps(data, ensure_ascii=False)[:MAX_RESULT_CHARS - 200]},
                                     truncated=True, images=images)
            except ValidationError as e:
                res = ToolResult(False, error="Invalid arguments: " + "; ".join(
                    f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
            except ToolError as e:
                res = ToolResult(False, error=str(e))
            except Exception as e:  # noqa: BLE001  a tool that blows up (model error, timeout) is a failed tool call, never a crashed job
                res = ToolResult(False, error=f"The tool failed ({type(e).__name__}: {str(e)[:80]}). Try something else or finish.")
        events.emit("tool_call", role=ctx.role, tool=call.name, arguments=call.arguments, ok=res.ok,
                    error=res.error, truncated=res.truncated, result=res.to_message()[:1500],
                    course=ctx.source_course_id)
        return res


class ToolError(Exception):
    """Expected, model-visible failure (bad id, out of scope...)."""


class LoggedRegistry(ToolRegistry):
    """A registry that writes EVERY tool call of an agent (accepted or refused) to the action trace and to the usage ledger, with its duration.
    Subclasses say which agent they are and where the call belongs (application, row)."""
    agent = ""

    def scope(self, ctx: ToolContext, call: ToolCall) -> tuple[str, str]:
        return getattr(ctx, "app_id", ""), getattr(ctx, "row_id", "")

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        import time

        from ...services.usage import log_tool
        t0 = time.time()
        res = await super().execute(call, ctx)
        app_id, row_id = self.scope(ctx, call)
        if app_id:
            log_tool(app_id, row_id, self.agent, call.name, call.arguments if isinstance(call.arguments, dict) else {}, res.ok, (res.error or res.to_message())[:300], time.time() - t0)
        return res
