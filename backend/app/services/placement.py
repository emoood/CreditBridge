"""Where a mapped AUD course can go in the student's degree plan. Code only; no model.

Data: policies/data/programs/*.yaml (the nine plans), policies/data/gen_ed_categories.yaml (The Big Questions) and policies/program_constraints.yaml
(areas blocked as general electives). A mapped AUD course decides the slot. Order of options for one course:
  CORE (a core course of the plan) > BQ-<category> (that category has a slot in the plan) > BQ-ELE (any BQ list, once) > GEN-ELE (once, area not blocked).
Program-elective slots (FINA-ELE, MAJOR-ELE, ...) are loaded but have no eligible-course lists in the data, so they are never matched.
`resolve` assigns a whole application: several courses may be eligible for one slot, all stay candidates, and one is the default winner
(officer's pick, then the Case agent's proposal, then transcript order). The officer approves every row."""
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "policies" / "data"
CORE, BQ_ELE, GEN_ELE = "CORE", "BQ-ELE", "GEN-ELE"
CATEGORIES = ("BQ-TECH", "BQ-IMAG", "BQ-CULT", "BQ-ETHI", "BQ-SCIE")


class DataError(ValueError):
    """A data file is wrong; the message names the file."""


def _expand(code: str) -> list[str]:
    m = re.match(r"^([A-Z]{3,4}) (\d{3})-(\d{2,3})$", code.strip())  # ENGL 275-78 = ENGL 275 to 278
    if not m:
        return [code.strip()]
    lo, hi = int(m.group(2)), m.group(3)
    hi_n = int(m.group(2)[: 3 - len(hi)] + hi)
    return [f"{m.group(1)} {n}" for n in range(lo, hi_n + 1)]


@lru_cache(maxsize=1)
def gen_ed() -> dict[str, Any]:
    g = yaml.safe_load((DATA / "gen_ed_categories.yaml").read_text())
    cats = {c["id"]: {"name": c["name"], "codes": [x for e in c["eligible_courses"] for x in _expand(e["code"])]} for c in g["categories"]}
    return {"categories": cats, "exclusions": [e["courses"] for e in g.get("exclusions", [])]}


@lru_cache(maxsize=1)
def plans() -> dict[str, dict[str, Any]]:
    out = {}
    for f in sorted((DATA / "programs").glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        items = d["plan_items"]
        core, slots, satisfied, suggested = {}, {}, {}, {}
        for i in items:
            if i["type"] == "course" and i.get("block") == "core":
                for c in [i["code"], *(i.get("alternatives") or [])]:
                    core[c] = i["code"]
                for cat in i.get("categories_satisfied") or []:
                    satisfied[cat] = i["code"]
            elif i["type"] == "slot":
                slots[i["slot_id"]] = slots.get(i["slot_id"], 0) + 1  # a repeated slot_id is several instances
                if i.get("suggested"):
                    suggested.setdefault(i["slot_id"], []).extend(i["suggested"])
        out[d["program"]["name"]] = {"name": d["program"]["name"], "core": core, "slots": slots, "satisfied": satisfied, "suggested": suggested,
                                     "exclusions": [e["courses"] for e in d.get("exclusions", [])] + gen_ed()["exclusions"], "file": f.name,
                                     "total_credits": d["program"].get("total_credits")}
    return out


@lru_cache(maxsize=1)
def _blocked() -> list[dict[str, Any]]:
    return yaml.safe_load((ROOT / "policies" / "program_constraints.yaml").read_text())["programs"]


def plan(program: str) -> dict[str, Any] | None:
    return plans().get((program or "").strip())


def blocked_reason(program: str, code: str) -> str | None:
    """Why this area may not be a general elective for the program, or None. Applies only to GEN-ELE candidates."""
    p = (program or "").strip().lower()
    for c in _blocked().values():
        if p in [n.lower() for n in c.get("names", [])] and code.split()[0] in c.get("blocked_areas", []):
            return c["reasons"]["blocked"]
    return None


def options(program: str, code: str | None) -> list[str]:
    """Every valid slot for this AUD course in the program's plan, best first. Empty = it cannot be placed."""
    pl = plan(program)
    if not code or pl is None:
        return []
    if code in pl["core"]:
        return [CORE]
    cats = gen_ed()["categories"]
    mine = [c for c in CATEGORIES if code in cats[c]["codes"]]
    out = [c for c in mine if pl["slots"].get(c)]
    if mine and pl["slots"].get(BQ_ELE):
        out.append(BQ_ELE)
    if pl["slots"].get(GEN_ELE) and not blocked_reason(program, code):
        out.append(GEN_ELE)
    return out


def why_none(program: str, code: str) -> str:
    return blocked_reason(program, code) or f"{code} is not part of the {program} plan and cannot be a general elective."


def group_of(slot: str) -> str:
    return {CORE: "Core courses", BQ_ELE: "Big Questions elective", GEN_ELE: "General elective"}.get(slot) or (gen_ed()["categories"][slot]["name"] if slot in CATEGORIES else slot)


def suggested_for(program: str, slot: str, code: str) -> bool:
    pl = plan(program)
    return bool(pl and code in pl["suggested"].get(slot, []))


def resolve(program: str, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """items (transcript order): {row_id, code, credits, picked (officer chose this one), proposal (Case agent's slot or None)}.
    Returns row_id -> {slot, options, contended, candidates, winner, fallback, status ('placed'|'candidate'|'not_applicable'), reason, suggested}."""
    pl = plan(program)
    res: dict[str, dict[str, Any]] = {}
    if pl is None:
        return res
    live = []
    covered: dict[str, str] = {}
    for it in items:
        opts = options(program, it["code"])
        base = {"slot": None, "options": opts, "contended": False, "candidates": [], "winner": True, "fallback": None, "status": "not_applicable", "reason": "", "suggested": False}
        res[it["row_id"]] = base
        if not opts:
            base["reason"] = why_none(program, it["code"])
        elif it["code"] in covered:
            base["reason"] = f"Already covered by {covered[it['code']]}."
        elif (it.get("credits") or 0) <= 0:  # mapped, no credit awarded: shown where it would go, never takes a slot
            base.update(slot=opts[0], status="placed")
            covered.setdefault(it["code"], it["title"] if "title" in it else it["code"])
        else:
            live.append((it, opts))
            covered.setdefault(it["code"], it.get("title") or it["code"])
    taken: dict[str, set[str]] = {}
    assigned: dict[str, str] = {}

    def settle(slot: str, cap: int) -> None:
        cands = [(it, o) for it, o in live if it["row_id"] not in assigned and slot in o]
        if not cands:
            return
        order = sorted(cands, key=lambda c: (not c[0].get("picked"), c[0].get("proposal") != slot, len(c[1]), live.index(c)))
        winners = order[:cap]
        ids = [c[0]["row_id"] for c in cands]
        for it, _o in winners:
            assigned[it["row_id"]] = slot
            taken.setdefault(slot, set()).add(it["row_id"])
        for it, o in cands:
            r = res[it["row_id"]]
            won = assigned.get(it["row_id"]) == slot
            if r["contended"] and not r["winner"]:  # a loser stays shown under the slot it was contesting; this is where it lands if not picked
                if won and r["fallback"] is None:
                    r["fallback"] = slot
                continue
            r.update(slot=slot, contended=len(cands) > cap, candidates=ids, winner=won, fallback=None,
                     status="placed" if len(cands) <= cap else "candidate")
    for it, o in live:  # core first
        if CORE in o:
            settle(CORE, 99)
    cat_order = sorted(CATEGORIES, key=lambda c: sum(1 for it, o in live if c in o))
    for c in cat_order:
        if pl["slots"].get(c):
            settle(c, pl["slots"][c])
    settle(BQ_ELE, pl["slots"].get(BQ_ELE, 0) or 0)
    settle(GEN_ELE, pl["slots"].get(GEN_ELE, 0) or 0)
    for it, o in live:
        r = res[it["row_id"]]
        if it["row_id"] not in assigned and r["slot"] is None:
            r["reason"] = "Every slot it could fill is already taken."
        elif r["contended"] and not r["winner"] and r["fallback"] is None:
            r["reason"] = "If it is not chosen, no other slot is open for it."
        r["suggested"] = bool(r["slot"] and suggested_for(program, r["slot"], it["code"]))
    # exclusion groups: credit for only one of the courses
    seen: dict[int, str] = {}
    for it in items:
        r = res[it["row_id"]]
        if r["status"] == "not_applicable" or (it.get("credits") or 0) <= 0:
            continue
        for gi, g in enumerate(pl["exclusions"]):
            if it["code"] in g:
                if gi in seen and seen[gi] != it["code"]:
                    r.update(status="not_applicable", slot=None, reason=f"Credit is allowed for only one of {', '.join(g)}.")
                else:
                    seen[gi] = it["code"]
    return res


def totals(items: list[dict[str, Any]], placed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Credits per slot group, and the running total of approved (confirmed) credits."""
    by: dict[str, dict[str, int]] = {}
    approved = 0
    for it in items:
        r = placed.get(it["row_id"])
        if not r or r["status"] == "not_applicable" or not r["slot"]:
            continue
        g = by.setdefault(r["slot"], {"credits": 0, "approved": 0})
        cr = it.get("credits") or 0
        if r["winner"]:
            g["credits"] += cr
        if it.get("confirmed") and r["winner"]:
            g["approved"] += cr
            approved += cr
    return {"approved": approved, "by_slot": by}
