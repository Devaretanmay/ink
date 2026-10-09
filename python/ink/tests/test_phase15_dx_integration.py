"""Test for Phase 15 DX / Integration bug reported by unassisted design partner.

Issue: External developers naturally call:
1. `ink.decide(site, state, fallback=...)` with positional arguments for site and state.
2. `from ink import decide` or `ink.decide(...)` at the module level.
Previously, Ink.decide() strictly required keyword-only arguments (*), causing:
`TypeError: Ink.decide() takes 1 positional argument but 3 positional arguments were given`
and lacked the `decide` module-level alias for `decision`.
"""

import pytest
import ink
from ink import DecisionSite, Ink, Outcome


def test_ink_decide_positional_arguments_supported(tmp_path):
    db_path = tmp_path / "decisions.db"
    client = Ink(path=db_path)
    site = DecisionSite(
        name="test.positional.site",
        description="Testing positional arguments for decide",
        state_schema={"text": "string"},
        choices=("approve", "reject"),
    )

    # 1. Positional site and state with keyword fallback
    res = client.decide(site, {"text": "sample item"}, fallback=lambda: "approve")
    assert res.choice == "approve"
    assert res.decision_id is not None

    # 2. Keyword arguments still supported
    res2 = client.decide(site=site, state={"text": "sample item"}, fallback=lambda: "reject")
    assert res2.choice == "reject"


def test_module_level_decide_export_and_call(tmp_path):
    # Verify module-level decide is exported and callable
    assert hasattr(ink, "decide")
    assert callable(ink.decide)

    site = DecisionSite(
        name="test.module.site",
        description="Testing module level decide",
        state_schema={"text": "string"},
        choices=("yes", "no"),
    )

    res = ink.decide(site=site, state={"text": "query"}, fallback=lambda: "yes")
    assert res.choice == "yes"
