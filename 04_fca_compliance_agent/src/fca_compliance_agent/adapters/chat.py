"""The combined graph: chat in front of every review.

START -> chat_adapter -> pr_review | codebase_review | product_spec | ask -> END

`chat_adapter` is plain code. It decides which review is wanted, and which output, from a chat message or from `raw_input`
(the same keys the pipelines take). The pipelines themselves are unchanged and run inside their node.

`output_type` is the adapter's decision, passed along in the state: a PR becomes a `pr_comment`, a repository a
`remediation_pr`, a spec a `pdf_report`, and any of them only a `report` if the message says so. The outbound adapter delivers
what it says. From chat, anything that writes to GitHub waits for a person to approve it: a pasted link is not permission.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..domain.models import DeliverableResult, FindingsReport, OutputType
from .github_codebase import render_codebase_markdown
from .github_pr import render_markdown
from .output_type import GITHUB_WRITES, InvalidInputError, ReviewKind, decide_output_type
from .product_spec import SUFFIXES, render_spec_markdown

SPECS_DIR = Path(__file__).resolve().parents[3] / "specs"  # the only folder a chat message can name a file in

_Target = tuple[ReviewKind, str, dict[str, Any]]
_RENDER: dict[str, Callable[[FindingsReport], str]] = {
    "pr_review": render_markdown, "codebase_review": render_codebase_markdown, "product_spec": render_spec_markdown,
}  # fmt: skip
_RAW_KEYS: dict[str, tuple[str, ...]] = {
    "pr_review": ("pr_url", "pr_number"),
    "codebase_review": ("repo_url", "workspace_path"),
    "product_spec": ("spec_path", "spec_text", "spec_base64"),
}

# The host must be exactly github.com: the lookbehind rejects gist.github.com, notgithub.com and evil.com/github.com/...
_GITHUB_URL = re.compile(
    r"(?<![\w.@/-])(?:https?://)?(?:www\.)?github\.com/([\w-]+)/([\w.-]+)((?:/[^\s<>()\[\]\"'`|?#]*)?)"
    r"(?:[?#][^\s<>()\[\]\"'`|]*)?",
    re.IGNORECASE,
)
_PULL = re.compile(r"^/pull/(\d+)(?:/.*)?$")
_TREE = re.compile(r"^/tree/(.+?)/?$")
_SPEC_FILE = re.compile(r"(?<![\w./:-])(\w[\w./-]*\.(?:pdf|md|txt))(?![\w/])", re.IGNORECASE)
_SPEC_TEXT = re.compile(r"^\s*(?:product\s+)?spec(?:ification)?\s*:\s*(\S.{39,})$", re.IGNORECASE | re.DOTALL)

_NAMED_OUTPUT = re.compile(r"\boutput(?:[ _]type)?\s*[:=]\s*(\w+)", re.IGNORECASE)
_REPORT_ONLY = re.compile(r"\breport\b|\b(?:don'?t|do not|no|not|never|without|only)\b", re.IGNORECASE)

USAGE = f"""Send me one thing and I will review it against the FCA Handbook and the Consumer Credit Act:

- **A pull request:** `https://github.com/owner/repo/pull/12`
- **A repository:** `https://github.com/owner/repo`, or `https://github.com/owner/repo/tree/branch`
- **A product spec:** attach a PDF, name a file in the `{SPECS_DIR.name}` folder, or write `spec:` and paste the text

What you send decides the output: a pull request becomes a PR comment, a repository a fix PR, and a spec a PDF report. I always \
reply with the report too, and I ask you to approve before I write anything to GitHub. Say "report only" (or \
`output: report`) to get just the report."""


@dataclass(frozen=True)
class ReviewRequest:
    """What the adapter decided: which review, of what, and (inside `raw_input`) which output."""

    kind: ReviewKind
    target: str
    raw_input: dict[str, Any] = field(repr=False)
    output_type: OutputType
    require_approval: bool
    from_chat: bool


class CombinedInput(MessagesState, total=False):
    """A message, or `raw_input` with the same keys as the pipelines take."""

    raw_input: dict[str, Any]
    require_human_approval: bool


class CombinedState(CombinedInput, total=False):
    # Set by chat_adapter, and not part of the input: a caller cannot skip it
    request: dict[str, Any] | None
    question: str | None
    # Output
    output_type: OutputType | None
    findings_report: FindingsReport | None
    deliverable_result: DeliverableResult | None


class CombinedOutput(MessagesState, total=False):
    output_type: OutputType | None
    findings_report: FindingsReport | None
    deliverable_result: DeliverableResult | None


# ---------------------------------------------------------------------------
# The adapter: what is wanted?
# ---------------------------------------------------------------------------


def _github_targets(text: str) -> list[_Target]:
    """Every distinct pull request or repository linked in the text. Other GitHub pages are left out."""
    found: dict[str, _Target] = {}
    for owner, name, rest in _GITHUB_URL.findall(text):
        rest = rest.rstrip(".,;:!*_")  # the end of a sentence or of **bold**
        name = (name if rest else name.rstrip(".")).removesuffix(".git")
        if not name.strip("."):
            continue
        repo_url = f"https://github.com/{owner}/{name}"
        if pull := _PULL.match(rest):
            url = f"{repo_url}/pull/{pull.group(1)}"
            target: _Target = ("pr_review", url, {"pr_url": url})
        elif rest in ("", "/"):
            target = ("codebase_review", repo_url, {"repo_url": repo_url})
        elif tree := _TREE.match(rest):
            branch = tree.group(1)
            target = ("codebase_review", f"{repo_url}/tree/{branch}", {"repo_url": repo_url, "default_branch": branch})
        else:
            continue
        found.setdefault(target[1].lower(), target)
    return list(found.values())


def _attached_specs(message: HumanMessage) -> list[_Target]:
    found = []
    for block in message.content if isinstance(message.content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "file" or block.get("mime_type") != "application/pdf":
            continue
        encoded = block.get("base64") or block.get("data")
        if not encoded:
            continue
        extra = block.get("extras") or block.get("metadata") or {}
        name = Path(str(extra.get("filename") or extra.get("name") or "attachment.pdf")).name
        name = name if name.lower().endswith(".pdf") else f"{name}.pdf"
        found.append(("product_spec", name, {"spec_base64": encoded, "spec_name": name}))
    return found


def _named_specs(text: str) -> list[_Target]:
    """Files named in the text that are in the specs folder. A message cannot reach any other file."""
    found: dict[str, _Target] = {}
    root = SPECS_DIR.resolve()
    for name in _SPEC_FILE.findall(text):
        path = (root / name.removeprefix(f"{SPECS_DIR.name}/")).resolve()
        if path.suffix.lower() in SUFFIXES and path.is_relative_to(root) and path.is_file():
            found[str(path)] = ("product_spec", path.name, {"spec_path": str(path)})
    return list(found.values())


def _said_output(kind: ReviewKind, text: str) -> str | None:
    """The output that the words ask for, if they say. Anything that reads like a refusal to write is a report."""
    words = _GITHUB_URL.sub(" ", text)  # a repository may be called "no-report"
    if named := _NAMED_OUTPUT.search(words):
        return named.group(1).lower()
    return "report" if kind != "product_spec" and _REPORT_ONLY.search(words.replace("\u2019", "'")) else None


def _decide(kind: ReviewKind, target: str, raw: dict[str, Any], *, from_chat: bool, require_approval: bool = False):
    """Settle the output type, and (from chat) require a person's approval for anything that writes to GitHub."""
    try:
        output_type = decide_output_type(kind, raw)
    except InvalidInputError as error:
        return str(error)
    if from_chat:
        raw = {**raw, "output_type": output_type}
        require_approval = output_type in GITHUB_WRITES
    return ReviewRequest(kind, target, raw, output_type, require_approval, from_chat)


def request_from_message(message: HumanMessage) -> ReviewRequest | str:
    """The review a message asks for, or what to ask the user."""
    text = str(message.text)
    if pasted := _SPEC_TEXT.match(text):
        # What was pasted is a document, so nothing in it is read as a link or as an instruction about the output.
        raw = {"spec_text": pasted.group(1), "spec_name": "pasted-spec.md"}
        return _decide("product_spec", "the pasted text", raw, from_chat=True)
    found = _github_targets(text) + _attached_specs(message)
    found = found or _named_specs(text)
    if not found:
        return USAGE
    if len(found) > 1:
        return f"I found {len(found)} things to review. Please send one at a time.\n\n{USAGE}"
    kind, target, raw = found[0]
    if said := _said_output(kind, text):
        raw = {**raw, "output_type": said}
    return _decide(kind, target, raw, from_chat=True)


def request_from_raw_input(raw: dict[str, Any], require_approval: bool) -> ReviewRequest | str:
    """The same for `raw_input`, which is passed to the pipeline as it is."""
    kinds = [kind for kind, keys in _RAW_KEYS.items() if any(raw.get(key) for key in keys)]
    if not kinds and raw.get("repo"):
        kinds = ["codebase_review"]
    if len(kinds) != 1:
        keys = ", ".join(key for group in _RAW_KEYS.values() for key in group)
        return f"`raw_input` must name exactly one thing to review, with one of: repo, {keys}."
    key = next((k for k in (*_RAW_KEYS[kinds[0]], "repo") if raw.get(k)), "")
    target = str(raw.get("spec_name") or key) if key in ("spec_text", "spec_base64") else str(raw.get(key))
    return _decide(kinds[0], target, raw, from_chat=False, require_approval=require_approval)  # type: ignore[arg-type]


def chat_adapter(state: CombinedState) -> dict[str, Any]:
    raw = state.get("raw_input")
    if raw:
        decided = request_from_raw_input(raw, bool(state.get("require_human_approval")))
    else:
        last = next((m for m in reversed(state.get("messages", [])) if isinstance(m, HumanMessage)), None)
        decided = request_from_message(last) if last else USAGE
    # Cleared, so that nothing from one turn of a conversation is acted on again in the next.
    clean = {
        "raw_input": {}, "require_human_approval": False, "request": None, "question": None,
        "output_type": None, "findings_report": None, "deliverable_result": None,
    }  # fmt: skip
    if isinstance(decided, str):
        return {**clean, "question": decided}
    return {**clean, "request": dict(vars(decided)), "output_type": decided.output_type}


def route(state: CombinedState) -> str:
    request = state.get("request")
    return request["kind"] if request else "ask"


def ask(state: CombinedState) -> dict[str, Any]:
    return {"messages": [AIMessage(content=state.get("question") or USAGE)]}


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------


def _leaves(error: BaseException) -> Iterator[BaseException]:
    """The errors inside exception groups and causes: an MCP connection failure arrives wrapped in both."""
    yield error
    for inner in getattr(error, "exceptions", ()):
        yield from _leaves(inner)
    if error.__cause__ is not None:
        yield from _leaves(error.__cause__)


def _explain(error: Exception, request: ReviewRequest) -> str | None:
    """What to tell the user about a failure they can do something about. None for anything else, which must be raised."""
    for leaf in _leaves(error):
        if isinstance(leaf, ConnectionError) or type(leaf).__name__ == "ConnectError":
            return (
                "**Not reviewed.** I could not reach a service that I need. Check that the FCA Handbook server is running "
                "(`uv run --no-sync python mcp_server.py` in `04_fca_compliance_agent/handbook_rag`), that `FCA_MCP_URL` "
                "and `CCA_MCP_URL` are right, and that the model gateway is reachable."
            )
    if isinstance(error, InvalidInputError) or (isinstance(error, ValueError) and "Invalid branch name" in str(error)):
        return f"**Not reviewed.** {error}"
    if isinstance(error, RuntimeError) and str(error).startswith(("gh ", "git ")):
        hint = (
            f"If its main branch is not called `main`, send `{request.target}/tree/<branch>`. "
            if request.kind == "codebase_review" and "default_branch" not in request.raw_input
            else ""
        )
        return (
            f"**Not reviewed.** I could not read {request.target}. {hint}Check that the link is right and that your `gh` "
            f"login can see it.\n\n```\n{error}\n```"
        )
    return None


def _reply(request: ReviewRequest, result: dict[str, Any]) -> str:
    report, deliverable = result["findings_report"], result.get("deliverable_result")
    output_type = result.get("output_type")
    notes = [f"Reviewed {request.target}. Output: `{output_type}`."]
    if output_type not in GITHUB_WRITES:
        notes.append("Nothing was written to GitHub.")
    if deliverable is not None and (output_type in GITHUB_WRITES or output_type == "pdf_report"):
        notes.append(f"**{deliverable.status}:** {deliverable.summary}")
    if not report.units_total:
        notes.append("**There was nothing to read, so the verdict below means nothing.**")
    context = result.get("adapter_context") or {}
    if skipped := context.get("files_skipped") or context.get("sections_skipped"):
        notes.append(f"{skipped} file(s) or section(s) were not read, such as lockfiles, images and anything past the limit.")
    return "\n\n".join([*notes, _RENDER[request.kind](report)])


def build_combined_graph(
    *, pr_review: Any, codebase_review: Any, product_spec: Any, **compile_kwargs: Any
) -> CompiledStateGraph:
    """Build the combined graph around the three compiled pipelines."""
    pipelines: dict[str, Any] = {
        "pr_review": pr_review, "codebase_review": codebase_review, "product_spec": product_spec,
    }  # fmt: skip

    def review_with(pipeline: Any):
        async def review(state: CombinedState) -> dict[str, Any]:
            request = ReviewRequest(**(state.get("request") or {}))
            given = {"raw_input": dict(request.raw_input), "require_human_approval": request.require_approval}
            try:
                result = await pipeline.ainvoke(given)
            except Exception as error:
                text = _explain(error, request) if request.from_chat else None
                if text is None:
                    raise
                return {"messages": [AIMessage(content=text)]}
            return {
                "messages": [AIMessage(content=_reply(request, result))],
                "output_type": result.get("output_type"),
                "findings_report": result["findings_report"],
                "deliverable_result": result.get("deliverable_result"),
            }

        return review

    builder = StateGraph(CombinedState, input_schema=CombinedInput, output_schema=CombinedOutput)
    builder.add_node("chat_adapter", chat_adapter)
    builder.add_node("ask", ask)
    builder.add_edge(START, "chat_adapter")
    for name, pipeline in pipelines.items():
        builder.add_node(name, review_with(pipeline))
        builder.add_edge(name, END)
    builder.add_conditional_edges("chat_adapter", route, [*pipelines, "ask"])
    builder.add_edge("ask", END)
    return builder.compile(**compile_kwargs)
