"""Outbound Adapter nodes & subgraphs (Deliverable Producers) + ContextModule bundles."""

from .stubs import (
    build_codebase_remediation_outbound_subgraph,
    build_codebase_review_module,
    build_pr_review_module,
    build_product_spec_module,
    pr_review_outbound_stub,
    product_spec_outbound_stub,
)

__all__ = [
    "build_codebase_remediation_outbound_subgraph",
    "build_codebase_review_module",
    "build_pr_review_module",
    "build_product_spec_module",
    "pr_review_outbound_stub",
    "product_spec_outbound_stub",
]
