"""Per-subject working record shared by both agents. Holds what was looked at and noted, never private
chain-of-thought. It is rendered to the admissions officer as the reasoning panel."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaseFile:
    subject: dict[str, Any] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)  # catalog searches made
    search_hits: dict[str, float] = field(default_factory=dict)  # target id -> best relevance seen
    targets_read: dict[str, set] = field(default_factory=dict)  # role -> target ids actually fetched
    excerpts_read: dict[str, set] = field(default_factory=dict)  # role -> excerpt ids actually fetched
    notes: list[dict[str, str]] = field(default_factory=list)  # {by, kind, text}
    rule_checks: list[dict[str, Any]] = field(default_factory=list)
    precedents_seen: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def read_target(self, role: str, target_id: str) -> None:
        self.targets_read.setdefault(role, set()).add(target_id)

    def read_excerpt(self, role: str, excerpt_id: str) -> None:
        self.excerpts_read.setdefault(role, set()).add(excerpt_id)

    def note(self, by: str, kind: str, text: str) -> None:
        self.notes.append({"by": by, "kind": kind, "text": text[:600]})

    def to_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "queries": self.queries, "search_hits": self.search_hits,
                "targets_read": {k: sorted(v) for k, v in self.targets_read.items()},
                "excerpts_read": {k: sorted(v) for k, v in self.excerpts_read.items()},
                "notes": self.notes, "rule_checks": self.rule_checks,
                "precedents_seen": self.precedents_seen, "requests": self.requests}
