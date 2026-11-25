"""Test-only mocks and stubs for offline, deterministic unit tests.

All test doubles live exclusively under `tests/` so production code under `src/`
and `scripts/` never ships or falls back to stubs or mocks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fca_compliance_agent.domain.models import (
    AnalysisInput,
    AnalysisUnit,
    DeliverableResult,
    DocumentLocation,
    RegulatoryPassage,
)
from fca_compliance_agent.domain.state import PipelineState
from fca_compliance_agent.ports.adapters import ContextModule

# ---------------------------------------------------------------------------
# In-Memory Mock Regulatory Adapters (FCA Handbook & Consumer Credit Act)
# ---------------------------------------------------------------------------

_FCA_CORPUS: list[RegulatoryPassage] = [
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 3.3.1R",
        title="Financial promotions must be clear, fair and not misleading",
        text=(
            "A firm must ensure that a communication or a financial promotion is clear, fair and "
            "not misleading. Claims such as 'guaranteed approval', 'instant approval without checks', "
            "or omitting key eligibility conditions breach CONC 3.3.1R."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/3/3.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 3.5.7R",
        title="Prominence of Representative APR in credit promotions",
        text=(
            "Where a financial promotion indicates a rate of interest or amount relating to the cost "
            "of credit, it must include a representative example with the Representative APR shown "
            "with no less prominence than any other rate or incentive."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/3/5.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 4.2.5R",
        title="Adequate explanations before a regulated credit agreement is made",
        text=(
            "Before making a regulated credit agreement, the lender must provide an adequate "
            "explanation covering key features, total amount payable, consequences of missed "
            "payments, and the customer's statutory right to withdraw within 14 days."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/4/2.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 5.2A.4R",
        title="Creditworthiness and affordability assessment",
        text=(
            "A firm must undertake a reasonable assessment of creditworthiness before entering into "
            "a regulated credit agreement or significantly increasing the credit limit, considering "
            "both credit risk and affordability without undue hardship."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/5/2A.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 7.3.2G",
        title="Treating customers in default or arrears difficulties with forbearance",
        text=(
            "A firm must treat customers in default or arrears difficulties with forbearance and "
            "due consideration, and must not accelerate or enforce a regulated credit agreement "
            "without first issuing the statutory default notice and respecting the cure period."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/7/3.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="PRIN 2A.2.1R",
        title="Consumer Duty — Act to deliver good outcomes for retail customers",
        text=(
            "A firm must act to deliver good outcomes for retail customers, including acting in "
            "good faith, avoiding causing foreseeable harm, and enabling customers to pursue their "
            "financial objectives. Dark patterns or hidden fees breach Consumer Duty."
        ),
        url="https://www.handbook.fca.org.uk/handbook/PRIN/2A/2.html",
    ),
    *(
        RegulatoryPassage(
            source="uk_gdpr",
            reference=f"UK GDPR Art 5({point})",
            title=title,
            text=text,
            url="https://www.legislation.gov.uk/eur/2016/679/article/5",
        )
        for point, title, text in (
            (
                "1)(a",
                "Lawfulness, fairness and transparency of personal data processing",
                "Personal data shall be processed lawfully, fairly and in a transparent manner in relation to "
                "the data subject ('lawfulness, fairness and transparency').",
            ),
            (
                "1)(b",
                "Purpose limitation: no further use for incompatible purposes such as marketing",
                "SUMMARY, NOT VERBATIM: personal data must be collected for specified, explicit and legitimate "
                "purposes and not further processed by or on behalf of a controller in a manner incompatible "
                "with those purposes ('purpose limitation').",
            ),
            (
                "1)(c",
                "Data minimisation: store only the personal data that is necessary",
                "Personal data shall be adequate, relevant and limited to what is necessary in relation to the "
                "purposes for which they are processed ('data minimisation').",
            ),
            (
                "1)(e",
                "Storage limitation: personal data retention no longer than necessary",
                "SUMMARY, NOT VERBATIM: personal data must be kept in a form which permits identification of "
                "data subjects for no longer than is necessary for the purposes for which it is processed, "
                "subject to limited exceptions for archiving, research and statistics ('storage limitation').",
            ),
            (
                "1)(f",
                "Integrity and confidentiality: security of personal data",
                "Personal data shall be processed in a manner that ensures appropriate security of the personal "
                "data, including protection against unauthorised or unlawful processing and against accidental "
                "loss, destruction or damage, using appropriate technical or organisational measures "
                "('integrity and confidentiality').",
            ),
            (
                "2",
                "Accountability of the controller for personal data processing",
                "The controller shall be responsible for, and be able to demonstrate compliance with, "
                "paragraph 1 ('accountability').",
            ),
        )
    ),
]

_CCA_CORPUS: list[RegulatoryPassage] = [
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.55",
        title="Disclosure of pre-contract information",
        text=(
            "Regulations require prescribed pre-contract credit information (SECCI) to be disclosed "
            "in good time before a regulated agreement is made; failure renders the agreement "
            "enforceable only on an order of the court."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/55",
    ),
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.66A",
        title="Statutory 14-day right of withdrawal",
        text=(
            "The debtor under a regulated consumer credit agreement has a statutory right to "
            "withdraw from the agreement without giving any reason within 14 calendar days starting "
            "the day after the agreement is made or prescribed information is received. This period "
            "cannot be shortened contractually."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/66A",
    ),
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.75",
        title="Liability of creditor for breaches by supplier",
        text=(
            "Where a debtor has a claim for misrepresentation or breach of contract against a "
            "supplier for a financed item priced over £100 and not exceeding £30,000, the creditor "
            "is jointly and severally liable. Terms purporting to waive Section 75 liability are void."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/75",
    ),
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.87",
        title="Need for Default Notice before termination or enforcement",
        text=(
            "Service of a statutory Default Notice in prescribed form is necessary before the "
            "creditor can terminate the agreement, demand earlier payment, or enforce security. "
            "Under s.88, the notice must allow not less than 14 days to remedy the breach."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/87",
    ),
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.140A",
        title="Unfair relationships between creditors and debtors",
        text=(
            "A court may determine that the relationship between a creditor and debtor is unfair "
            "because of any terms of the agreement, the way the creditor exercises or enforces "
            "rights, or anything done or omitted on behalf of the creditor."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/140A",
    ),
]


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 2}


def _score_passage(query: str, passage: RegulatoryPassage) -> float:
    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0
    p_tokens = _tokens(f"{passage.reference} {passage.title} {passage.text}")
    overlap = q_tokens & p_tokens
    return len(overlap) / max(len(q_tokens), 1)


class MockFCAHandbookAdapter:
    """Test-only in-memory keyword-scored mock implementing `FCAHandbookPort`."""

    def __init__(self, passages: list[RegulatoryPassage] | None = None) -> None:
        self._passages = list(passages) if passages is not None else list(_FCA_CORPUS)

    async def search_rules(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        candidates = self._passages
        if sourcebooks:
            norm_books = [b.upper() for b in sourcebooks]
            filtered = [
                p for p in candidates if any(p.reference.upper().startswith(b) for b in norm_books)
            ]
            if filtered:
                candidates = filtered

        scored = [
            (score, passage.model_copy(update={"score": round(score, 3)}))
            for passage in candidates
            if (score := _score_passage(query, passage)) > 0
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return [p.model_copy(update={"score": 0.1}) for p in candidates[: min(2, top_k)]]
        return [p for _, p in scored[:top_k]]

    async def get_rule(self, reference: str) -> RegulatoryPassage | None:
        target = reference.strip().upper()
        for passage in self._passages:
            if passage.reference.strip().upper() == target:
                return passage
        return None


class MockConsumerCreditActAdapter:
    """Test-only in-memory mock implementing `ConsumerCreditActPort`."""

    def __init__(self, passages: list[RegulatoryPassage] | None = None) -> None:
        self._passages = list(passages) if passages is not None else list(_CCA_CORPUS)

    async def query_provisions(
        self,
        query: str,
        *,
        sections: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        candidates = self._passages
        if sections:
            norm_secs = [s.lower().replace("section", "s.").replace(" ", "") for s in sections]
            matched = [
                p
                for p in candidates
                if any(sec in p.reference.lower().replace(" ", "") for sec in norm_secs)
            ]
            if matched:
                candidates = matched

        scored = [
            (score, passage.model_copy(update={"score": round(score, 3)}))
            for passage in candidates
            if (score := _score_passage(query, passage)) > 0
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return [p.model_copy(update={"score": 0.1}) for p in candidates[: min(2, top_k)]]
        return [p for _, p in scored[:top_k]]

    async def get_document(self, section: str | None = None) -> list[RegulatoryPassage]:
        if not section:
            return list(self._passages)
        norm = section.lower().replace("section", "s.").replace(" ", "")
        return [
            p for p in self._passages if norm in p.reference.lower().replace(" ", "")
        ]


# ---------------------------------------------------------------------------
# Test-Only Mock GitHub API Adapter
# ---------------------------------------------------------------------------


class LocalMockGitHubClient:
    """Test-only mock `GitHubClient` API adapter that simulates GitHub on local disk and reports to stdout.

    Inject this in tests so the REAL inbound/outbound behaviour adapters
    (diff splitting, code remediation, PR body rendering, HITL gates) run end-to-end while
    only the external GitHub API/CLI boundary is mocked.
    """

    def __init__(
        self,
        *,
        output_dir: Path,
        repo_files: dict[str, str] | None = None,
        pr_meta: dict[str, Any] | None = None,
        pr_diff: str = "",
        org_repos: list[dict[str, Any]] | None = None,
        repo_prs: dict[str, list[dict[str, Any]]] | None = None,
        fail_on_create: str | None = None,
        fail_on_comment: str | None = None,
        report_to_stdout: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.repo_files = dict(repo_files or {})
        self.pr_meta = dict(pr_meta or {})
        self.pr_diff = pr_diff
        self.org_repos = list(org_repos or [])
        self.repo_prs = dict(repo_prs or {})
        self.fail_on_create = fail_on_create
        self.fail_on_comment = fail_on_comment
        self.report_to_stdout = report_to_stdout

        self.cloned: list[dict[str, Any]] = []
        self.branches: list[str] = []
        self.commits: list[dict[str, Any]] = []
        self.created_prs: list[dict[str, Any]] = []
        self.viewed_prs: list[tuple[str, int]] = []
        self.diffed_prs: list[tuple[str, int]] = []
        self.posted_comments: list[dict[str, Any]] = []
        self.written_paths: list[Path] = []

    def _emit(self, message: str) -> None:
        if self.report_to_stdout:
            print(message)

    def _record_file(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.written_paths.append(path)
        return path

    def clone_repo(self, repo_url: str, branch: str, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        for rel, text in self.repo_files.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        record = {"repo_url": repo_url, "branch": branch, "files": sorted(self.repo_files.keys())}
        self.cloned.append(record)
        self._emit(
            f"[MockGitHubAPI] Cloned {repo_url}@{branch} ({len(self.repo_files)} file(s)) -> {dest}"
        )

    def checkout_new_branch(self, repo_dir: Path, branch_name: str) -> None:
        self.branches.append(branch_name)
        self._emit(f"[MockGitHubAPI] Checked out branch '{branch_name}'")

    def commit_and_push(
        self,
        repo_dir: Path,
        branch_name: str,
        files: list[str],
        commit_message: str,
    ) -> None:
        safe_branch = branch_name.replace("/", "__")
        branch_dir = self.output_dir / "branches" / safe_branch
        written_files: dict[str, str] = {}
        disk_files: dict[str, str] = {}
        for rel in files:
            content = (repo_dir / rel).read_text(encoding="utf-8")
            written_files[rel] = content
            saved = self._record_file(branch_dir / rel, content)
            disk_files[rel] = str(saved)

        meta_path = self._record_file(
            branch_dir / "_commit.json",
            json.dumps(
                {
                    "branch": branch_name,
                    "files": list(files),
                    "commit_message": commit_message,
                    "disk_files": disk_files,
                },
                indent=2,
            ),
        )
        self.commits.append(
            {
                "branch": branch_name,
                "files": list(files),
                "commit_message": commit_message,
                "written_files": written_files,
                "meta_path": str(meta_path),
            }
        )
        self._emit(
            f"[MockGitHubAPI] Pushed branch '{branch_name}' ({len(files)} file(s)) -> {branch_dir}"
        )

    def create_pr(
        self,
        *,
        repo_slug: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body_file: Path,
        draft: bool = False,
        cwd: Path | None = None,
    ) -> str:
        if self.fail_on_create:
            raise RuntimeError(self.fail_on_create)
        pr_number = len(self.created_prs) + 1
        pr_url = f"https://github.com/{repo_slug}/pull/{pr_number}"
        body = body_file.read_text(encoding="utf-8")

        pr_dir = self.output_dir / "prs"
        md_path = self._record_file(pr_dir / f"pr_{pr_number}.md", body)
        meta = {
            "pr_number": pr_number,
            "pr_url": pr_url,
            "repo_slug": repo_slug,
            "base_branch": base_branch,
            "head_branch": head_branch,
            "title": title,
            "draft": draft,
            "body_path": str(md_path),
        }
        json_path = self._record_file(pr_dir / f"pr_{pr_number}.json", json.dumps(meta, indent=2))
        self.created_prs.append({**meta, "body": body, "meta_path": str(json_path)})
        self._emit(
            f"[MockGitHubAPI] Opened {'draft ' if draft else ''}PR {pr_url} "
            f"({head_branch} -> {base_branch}) | saved -> {md_path}"
        )
        return pr_url

    def view_pr(self, repo_slug: str, pr_number: int) -> dict[str, Any]:
        self.viewed_prs.append((repo_slug, pr_number))
        meta = {
            "title": f"PR #{pr_number}",
            "body": "",
            "url": f"https://github.com/{repo_slug}/pull/{pr_number}",
            "headRefOid": "mockheadsha",
            **self.pr_meta,
        }
        self._emit(f"[MockGitHubAPI] Viewed PR {repo_slug}#{pr_number} ({meta['title']})")
        return meta

    def diff_pr(self, repo_slug: str, pr_number: int) -> str:
        self.diffed_prs.append((repo_slug, pr_number))
        self._emit(
            f"[MockGitHubAPI] Fetched diff for {repo_slug}#{pr_number} ({len(self.pr_diff)} chars)"
        )
        return self.pr_diff

    def comment_pr(self, repo_slug: str, pr_number: int, body_file: Path) -> str:
        if self.fail_on_comment:
            raise RuntimeError(self.fail_on_comment)
        comment_id = len(self.posted_comments) + 1
        comment_url = f"https://github.com/{repo_slug}/pull/{pr_number}#issuecomment-{comment_id}"
        body = body_file.read_text(encoding="utf-8")

        comments_dir = self.output_dir / "comments"
        md_path = self._record_file(comments_dir / f"pr_{pr_number}_comment_{comment_id}.md", body)
        meta = {
            "comment_id": comment_id,
            "comment_url": comment_url,
            "repo_slug": repo_slug,
            "pr_number": pr_number,
            "body_path": str(md_path),
        }
        json_path = self._record_file(
            comments_dir / f"pr_{pr_number}_comment_{comment_id}.json",
            json.dumps(meta, indent=2),
        )
        self.posted_comments.append({**meta, "body": body, "meta_path": str(json_path)})
        self._emit(
            f"[MockGitHubAPI] Posted PR comment {comment_url} | saved -> {md_path}"
        )
        return comment_url

    def list_org_repos(self, org: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.org_repos)[:limit]

    def list_repo_prs(
        self,
        repo_slug: str,
        *,
        state: str = "open",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        prs = list(self.repo_prs.get(repo_slug, []))
        if state != "all":
            prs = [pr for pr in prs if str(pr.get("state", "OPEN")).lower() == state.lower()]
        return prs[:limit]

    def format_summary(self) -> str:
        lines = [
            f"[MockGitHubAPI Summary] output_dir={self.output_dir}",
            f"  Clones:   {len(self.cloned)}",
            f"  Commits:  {len(self.commits)}",
            f"  PRs:      {len(self.created_prs)}",
            f"  Comments: {len(self.posted_comments)}",
        ]
        for path in self.written_paths:
            lines.append(f"  Artifact: {path}")
        return "\n".join(lines)

    def print_summary(self) -> str:
        summary = self.format_summary()
        self._emit(summary)
        return summary


# ---------------------------------------------------------------------------
# Test-Only Custom Document Module Stub (Exercises `DocumentLocation` contract)
# ---------------------------------------------------------------------------


def product_spec_inbound_stub(state: PipelineState) -> dict[str, Any]:
    """Test-only stub exercising `DocumentLocation` and custom `ContextModule` pluggability."""
    raw = state.get("raw_input", {})
    pdf_path = str(raw.get("pdf_path", "specs/bnpl_credit_product_v1.pdf"))
    output_pdf_path = str(raw.get("output_pdf_path", "reports/bnpl_credit_findings.pdf"))

    units = [
        AnalysisUnit(
            unit_id="spec-sec-1",
            title="Section 3: Consumer Protection & Liability",
            content_type="spec_section",
            location=DocumentLocation(
                document_name=pdf_path,
                page_start=4,
                page_end=5,
                section_heading="3. Liability & Disputes",
            ),
            content=(
                "Customers purchasing retail goods between £150 and £5,000 using our regulated "
                "instalment credit agree to waive Section 75 claims against the lender and resolve "
                "all disputes solely with the merchant."
            ),
        ),
        AnalysisUnit(
            unit_id="spec-sec-2",
            title="Section 5: Cooling-Off Period",
            content_type="spec_section",
            location=DocumentLocation(
                document_name=pdf_path,
                page_start=8,
                page_end=8,
                section_heading="5. Cancellation & Withdrawal",
            ),
            content=(
                "Borrowers receive a 7-day cooling-off window after signing the credit agreement "
                "to cancel without penalty."
            ),
        ),
    ]

    analysis_input = AnalysisInput(
        target_id=pdf_path,
        source_type="product_spec",
        title=f"Product Specification Review: {pdf_path}",
        summary="New UK regulated retail instalment credit product specification.",
        units=units,
    )
    return {
        "analysis_input": analysis_input,
        "adapter_context": {
            "input_pdf_path": pdf_path,
            "output_pdf_path": output_pdf_path,
        },
    }


def product_spec_outbound_stub(state: PipelineState) -> dict[str, Any]:
    """Test-only stub rendering `FindingsReport` into a descriptor."""
    report = state["findings_report"]
    ctx = state.get("adapter_context", {})
    output_path = str(ctx.get("output_pdf_path", f"reports/{report.report_id}.pdf"))

    result = DeliverableResult(
        module_name="product_spec_review",
        status="completed",
        summary=(
            f"[TEST STUB PDF generator] Rendered {len(report.findings)} finding(s) "
            f"(verdict={report.overall_verdict}) to '{output_path}'."
        ),
        artifacts=[output_path],
        metadata={
            "input_pdf_path": ctx.get("input_pdf_path"),
            "output_pdf_path": output_path,
            "severity_counts": report.severity_counts(),
        },
    )
    return {"deliverable_result": result}


def build_product_spec_stub_module() -> ContextModule:
    return ContextModule(
        name="product_spec_review",
        description="Test-only stub exercising DocumentLocation and custom ContextModule wiring",
        inbound_node=product_spec_inbound_stub,
        outbound_node=product_spec_outbound_stub,
    )
