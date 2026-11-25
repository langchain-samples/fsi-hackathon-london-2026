"""Which output is required. Adapters decide it here, from the input, and pass it on as `output_type`.

    chat_adapter / inbound adapter  --output_type-->  (core, which never reads it)  -->  outbound adapter, which delivers it
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from ..domain.models import OutputType

ReviewKind = Literal["pr_review", "codebase_review", "product_spec"]

# What each kind of input can become. The first is what it becomes if the caller does not say.
OUTPUTS: dict[str, tuple[OutputType, ...]] = {
    "pr_review": ("pr_comment", "report"),
    "codebase_review": ("remediation_pr", "report"),
    "product_spec": ("pdf_report", "report"),
}
GITHUB_WRITES: tuple[OutputType, ...] = ("pr_comment", "remediation_pr")
_OLDER_FLAG = {"pr_review": "post_to_github", "codebase_review": "create_pr"}


class InvalidInputError(ValueError):
    """The caller asked for something that cannot be done. The message is safe to show them."""


def decide_output_type(kind: ReviewKind, raw: dict[str, Any]) -> OutputType:
    """The output required for this `raw_input`.

    `raw["output_type"]` asks for one by name. `post_to_github` and `create_pr` are the older yes/no way. Asking for one thing
    with the first and another with the second is an error.
    """
    flag = _OLDER_FLAG.get(kind, "")
    requested, wanted = raw.get("output_type"), raw.get(flag)
    if requested is not None:
        if requested not in OUTPUTS[kind]:
            raise InvalidInputError(f"output_type must be one of {', '.join(OUTPUTS[kind])} here, not {requested!r}.")
        if wanted is not None and bool(wanted) != (requested != "report"):
            raise InvalidInputError(f"output_type={requested!r} contradicts {flag}={wanted!r}.")
    if kind == "codebase_review" and raw.get("workspace_path") and Path(str(raw["workspace_path"])).is_dir():
        # A local folder is only reported on, unless the caller asks for a PR and says which repo to open it in.
        has_repo = bool(raw.get("repo_url") or raw.get("repo"))
        if requested == "remediation_pr" and not has_repo:
            raise InvalidInputError("output_type 'remediation_pr' needs 'repo_url' or 'repo' as well as 'workspace_path'.")
        return requested or ("remediation_pr" if wanted and has_repo else "report")
    if requested is not None:
        return requested
    return OUTPUTS[kind][0] if wanted is None or wanted else "report"
