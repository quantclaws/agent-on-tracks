"""IF-ADAPTER-003 demo-host de-tokenization contract (T-014 RED unit).

Pins the language-neutrality contract for ``tracks/executor/demo_host.py``
before the GREEN de-tokenization lands (interfaces.md §IF-ADAPTER-003):

- ``DemoHostReport.adapter_id`` must be resolved from the host project
  contract's ``[adapter]`` id via ``load_contract`` instead of a
  forbidden-zone literal: monkeypatching the contract adapter id must be
  reflected by the report (contract-source pin).
- Against the real demo contract the reported adapter id equals the
  declared ``[adapter]`` id -- value equivalence is preserved by the
  refactor.
- ``synthesize_scenario_fixture`` uses language-neutral display naming:
  the fallback host display name is ``demo-host``.
- The ``tracks/executor/demo_host.py`` source itself carries no language
  tokens (``pytest`` / ``junit`` / ``java``) so the executor zone stays
  language neutral (kernel/executor/cli token-scan invariant).

The contract-source, display-name and source-scan assertions fail today
because ``demo_host.py`` still hardcodes the adapter id literal and the
language-coupled display fallback -- the M-IMPL RED on the contract.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import tracks.executor.demo_host as demo_host
import tracks.project as project_module
from tracks.executor.demo_host import (
    create_demo_host,
    synthesize_scenario_fixture,
)
from tracks.project import load_contract

# AC-FR0264-04@v0.7 TRACKS-TRACE executor-zone source free of language tokens

_FORBIDDEN_TOKENS = ("pytest", "junit", "java")

# A contract-declared adapter id that can never collide with the demo
# contract's real declaration: the report must follow the contract source,
# not any literal baked into the forbidden zone.
_SENTINEL_ADAPTER_ID = "reference-sentinel"


def test_demo_host_source_carries_no_language_token():
    """The executor-zone demo host source is free of language tokens."""
    source = Path(demo_host.__file__).resolve().read_text(encoding="utf-8")
    lowered = source.lower()
    for token in _FORBIDDEN_TOKENS:
        assert token not in lowered, (
            f"language token {token!r} found in tracks/executor/demo_host.py; "
            "the executor zone must stay language neutral (AC-FR0264-04)"
        )


# AC-FR0264-04@v0.7 TRACKS-TRACE adapter id resolved from host contract [adapter] id


def test_adapter_id_follows_host_contract_declaration(tmp_path, monkeypatch):
    """DemoHostReport.adapter_id follows the host contract [adapter] id.

    Monkeypatching the contract-declared adapter id must be reflected by
    the report: the id is consumed from ``load_contract`` (either import
    seam), never from a forbidden-zone literal.
    """
    real_loader = project_module.load_contract

    def _sentinel_loader(repo: Path):
        contract = real_loader(repo)
        if contract.adapter is None:
            return contract
        return replace(
            contract,
            adapter=replace(contract.adapter, id=_SENTINEL_ADAPTER_ID),
        )

    monkeypatch.setattr(project_module, "load_contract", _sentinel_loader)
    monkeypatch.setattr(
        demo_host, "load_contract", _sentinel_loader, raising=False
    )
    report = create_demo_host(
        template_dir=tmp_path / "template",
        target_dir=tmp_path / "demo",
        wheel=tmp_path / "demo.whl",
    )
    assert report.adapter_id == _SENTINEL_ADAPTER_ID, (
        "DemoHostReport.adapter_id must be resolved from the host project "
        "contract [adapter] id via load_contract, not a forbidden-zone "
        f"literal (got {report.adapter_id!r})"
    )


def test_adapter_id_equals_real_demo_contract_declaration(tmp_path):
    """Value equivalence: the report id equals the declared demo contract id."""
    target = tmp_path / "demo"
    report = create_demo_host(
        template_dir=tmp_path / "template",
        target_dir=target,
        wheel=tmp_path / "demo.whl",
    )
    contract = load_contract(target)
    assert contract.adapter is not None, (
        "demo host contract must declare a known [adapter] id"
    )
    assert report.adapter_id == contract.adapter.id


# AC-NFR0141-02@v0.7 TRACKS-TRACE language-neutral display naming (dual check, static side)


def test_scenario_fixture_fallback_host_is_language_neutral():
    """The fallback host display name is the neutral ``demo-host``."""
    fixture = synthesize_scenario_fixture(
        scenario="broad_mutation",
        host_repo=Path(""),
        candidate_digest="sha256:" + "a" * 64,
    )
    assert fixture.host == "demo-host", (
        "synthesize_scenario_fixture fallback display naming must be "
        f"language neutral 'demo-host' (got {fixture.host!r})"
    )
