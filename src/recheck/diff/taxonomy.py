"""Why a cell could not be reproduced.

Half the value of a reproduction report is in this file. A cell that did not run
gets a specific code and concrete evidence — a path, a package name, an error
line — never a bare "failed". `OTHER` exists as an escape hatch and is the one
code that *must* carry free-text evidence explaining itself.
"""

from __future__ import annotations

from enum import StrEnum


class FailureCode(StrEnum):
    MISSING_CHECKPOINT = "MISSING_CHECKPOINT"
    GATED_DATASET = "GATED_DATASET"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    UNDOCUMENTED_FLAG = "UNDOCUMENTED_FLAG"
    NONDETERMINISTIC_NO_SEED = "NONDETERMINISTIC_NO_SEED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ENV_UNRESOLVABLE = "ENV_UNRESOLVABLE"
    SCRIPT_NOT_FOUND = "SCRIPT_NOT_FOUND"
    OTHER = "OTHER"


#: What a reader should understand each code to mean, rendered in reports.
DESCRIPTIONS: dict[FailureCode, str] = {
    FailureCode.MISSING_CHECKPOINT: "Model weights the table depends on are not published",
    FailureCode.GATED_DATASET: "Data requires credentials, a licence, or manual approval",
    FailureCode.MISSING_DEPENDENCY: "A required package or binary could not be installed",
    FailureCode.UNDOCUMENTED_FLAG: "The script needs an argument the paper never specifies",
    FailureCode.NONDETERMINISTIC_NO_SEED: "Run is stochastic and no seed is documented",
    FailureCode.BUDGET_EXCEEDED: "Run exceeded the configured compute budget",
    FailureCode.ENV_UNRESOLVABLE: "No dependency set satisfies the stated requirements",
    FailureCode.SCRIPT_NOT_FOUND: "No script in the repo produces this table",
    FailureCode.OTHER: "Unreproducible for a reason outside the taxonomy",
}

#: Codes whose evidence field is mandatory rather than merely encouraged.
EVIDENCE_REQUIRED: frozenset[FailureCode] = frozenset({FailureCode.OTHER})


class TaxonomyError(ValueError):
    """Raised when a failure is reported without the evidence its code requires."""


def parse_code(raw: str) -> FailureCode:
    try:
        return FailureCode(raw.strip().upper())
    except ValueError as exc:
        known = ", ".join(c.value for c in FailureCode)
        raise TaxonomyError(f"unknown failure code {raw!r}; expected one of: {known}") from exc


def validate(code: FailureCode, evidence: str | None) -> None:
    if code in EVIDENCE_REQUIRED and not (evidence or "").strip():
        raise TaxonomyError(f"failure code {code.value} requires non-empty evidence")


def describe(code: FailureCode) -> str:
    return DESCRIPTIONS[code]
