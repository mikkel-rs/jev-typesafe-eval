"""Read a function signature and a spec, ask TypeSafe once per command, build the call.

Rebuilt from the TypeSafe "Function calling" cookbook (its own dispatch.py is not published).
Closed-set arguments become questions:
  choice  Literal[...]        -> one Choice over the literal values (+ a `stated` Noul)
  set     list[Literal[...]]  -> one Noul per member
  flag    bool                -> one Noul
Anything else (int, str, dates) gets no question and keeps its default.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Union

ROUTE = "__tool__"
MULTI = "__multi__"
OTHER = "__other__"
YES = 0.5


def _literal_values(hint) -> list[str] | None:
    origin = typing.get_origin(hint)
    if origin is Literal:
        return list(typing.get_args(hint))
    if origin in (Union, types.UnionType):  # Literal[...] | None
        inner = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(inner) == 1:
            return _literal_values(inner[0])
    return None


def closed_sets(fn: Callable) -> dict[str, tuple[str, list[str]]]:
    """{argument: (shape, values)} for every argument that draws from a fixed list."""
    hints = typing.get_type_hints(fn)
    out: dict[str, tuple[str, list[str]]] = {}
    for name in inspect.signature(fn).parameters:
        hint = hints.get(name)
        if hint is bool:
            out[name] = ("flag", ["true", "false"])
        elif typing.get_origin(hint) is list and _literal_values(typing.get_args(hint)[0]):
            out[name] = ("set", _literal_values(typing.get_args(hint)[0]))
        elif _literal_values(hint):
            out[name] = ("choice", _literal_values(hint))
    return out


@dataclass
class Argument:
    name: str
    value: Any
    probability: float  # how sure we are of this argument as it stands in the call
    distribution: dict[str, float] = field(default_factory=dict)
    omitted: bool = False
    required: bool = False
    unsupported: bool = False  # the user named a value outside the closed set


@dataclass
class Tool:
    name: str
    probability: float
    confidence: float
    distribution: dict[str, float]


@dataclass
class Call:
    command: str
    tool: Tool
    arguments: dict[str, Argument]
    fn: Callable
    input_tokens: int = 0
    latency_ms: float = 0.0
    multi: float = 0.0  # P(the command holds two or more separate asks), guarded mode only

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def kwargs(self) -> dict[str, Any]:
        return {a.name: a.value for a in self.arguments.values() if not a.omitted}

    @property
    def missing(self) -> list[str]:
        """Required arguments the command never stated. The call cannot run as is."""
        return [a.name for a in self.arguments.values() if a.omitted and a.required]

    @property
    def unsupported(self) -> list[str]:
        """Arguments where the user asked for something outside the list (guarded mode only)."""
        return [a.name for a in self.arguments.values() if a.unsupported]

    @property
    def confidence(self) -> float:
        """The least certain judgement behind the call, not the product of all of them."""
        return min([self.tool.probability] + [a.probability for a in self.arguments.values()])

    def weakest(self) -> Argument | Tool:
        args = list(self.arguments.values())
        low = min(args, key=lambda a: a.probability, default=None)
        return low if low and low.probability < self.tool.probability else self.tool

    def run(self):
        if self.unsupported:
            raise ValueError(f"{self.name}: {self.unsupported} asked for a value outside the list")
        if self.missing:
            raise ValueError(f"{self.name} needs {self.missing}, the command did not state them")
        return self.fn(**self.kwargs)

    def __str__(self) -> str:
        return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in self.kwargs.items())})"


class Dispatcher:
    def __init__(self, spec: dict, tools: dict[str, Callable], client, abstain: str | None = None):
        """`abstain`: optional description of a none-of-these route. The cookbook has none."""
        self.spec, self.tools, self.client = spec, tools, client
        self.guarded = bool(abstain)
        self.shapes = {name: closed_sets(fn) for name, fn in tools.items()}
        route = {name: spec["functions"][name]["description"] for name in tools}
        if abstain:
            route["__none__"] = abstain
        q: dict[str, dict] = {
            ROUTE: {"type": "choice", "instructions": spec["route"], "criteria": route}
        }
        for fname, shapes in self.shapes.items():
            for arg, (shape, values) in shapes.items():
                a = spec["functions"][fname]["arguments"][arg]
                qid = f"{fname}.{arg}"
                if shape == "choice":
                    assert set(a["options"]) == set(values), f"{qid}: spec options != Literal"
                    # the TypeSafe skill's advice: give a Choice a no-match outcome when nothing may fit
                    other = {OTHER: a["other"]} if self.guarded and "other" in a else {}
                    q[qid] = {"type": "choice", "instructions": a["question"],
                              "criteria": a["options"] | other}
                    if "stated" in a:
                        q[qid + "?"] = {"type": "noul", "instructions": a["stated"]}
                elif shape == "set":
                    for v in values:
                        q[f"{qid}.{v}"] = {"type": "noul", "instructions": a["question"].replace("{}", v)}
                else:
                    q[qid] = {"type": "noul", "instructions": a["question"]}
        if self.guarded:
            q[MULTI] = {"type": "noul", "instructions": "Does the user ask for two or more separate things that would each need their own chart or report?"}
        self.questions = q
        self.spec_hash = hashlib.sha256(json.dumps(q, sort_keys=True).encode()).hexdigest()[:12]

    def __call__(self, command: str) -> Call:
        r = self.client.ask(command, self.questions, self.spec_hash)
        ans = r["answers"]
        t = ans[ROUTE]
        tool = Tool(t["choice"], t["probabilities"][t["choice"]], t["confidence"], t["probabilities"])
        if tool.name == "__none__":
            return Call(command, tool, {}, lambda: None, r["input_tokens"], r["latency_ms"])
        fn = self.tools[tool.name]
        params = inspect.signature(fn).parameters
        args: dict[str, Argument] = {}
        for arg, (shape, values) in self.shapes[tool.name].items():
            qid = f"{tool.name}.{arg}"
            required = params[arg].default is inspect.Parameter.empty
            if shape == "choice":
                c = ans[qid]
                stated = ans.get(qid + "?", {"noul": 1.0})["noul"]
                if stated > YES and c["choice"] == OTHER:
                    args[arg] = Argument(arg, None, min(stated, c["probabilities"][OTHER]),
                                         c["probabilities"], omitted=True, required=required,
                                         unsupported=True)
                elif stated > YES:
                    # sure of the argument only as far as we are sure it was stated AND which value
                    p = min(stated, c["probabilities"][c["choice"]])
                    args[arg] = Argument(arg, c["choice"], p, c["probabilities"], required=required)
                else:
                    args[arg] = Argument(arg, None, 1 - stated, {}, omitted=True, required=required)
            elif shape == "set":
                member = {v: ans[f"{qid}.{v}"]["noul"] for v in values}
                picked = [v for v, p in member.items() if p > YES]
                p = min(max(p, 1 - p) for p in member.values())
                args[arg] = Argument(arg, picked, p, member, omitted=not picked, required=required)
            else:
                p = ans[qid]["noul"]
                on = p > YES
                # a flag's question asks for the non-default state, so "yes" flips the default
                args[arg] = Argument(arg, (not params[arg].default) if on else None,
                                     max(p, 1 - p), {"flip": p, "keep": 1 - p}, omitted=not on)
        return Call(command, tool, args, fn, r["input_tokens"], r["latency_ms"],
                    ans.get(MULTI, {"noul": 0.0})["noul"])
