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


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    truncated: bool = False

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
                res = ToolResult(True, data)
                if len(res.to_message()) > MAX_RESULT_CHARS:
                    res = ToolResult(True, {"truncated": True, "preview": json.dumps(data, ensure_ascii=False)[:MAX_RESULT_CHARS - 200]},
                                     truncated=True)
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
