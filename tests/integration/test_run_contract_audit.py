"""Integration audit for Archer-owned test commands and nightly separation."""

import json
import shlex

from tests.integration.v05_contract_helpers import run_m_impl_journey
from tracks.executor.test_select import resolve_selected_command
from tracks.project import load_contract


def _result_argument(template, actual):
    template_argv = shlex.split(template)
    matches = [index for index, token in enumerate(template_argv) if "{result}" in token]
    assert len(matches) == 1
    index = matches[0]
    prefix, suffix = template_argv[index].split("{result}")
    observed = actual[index]
    assert observed.startswith(prefix) and observed.endswith(suffix)
    end = len(observed) - len(suffix) if suffix else len(observed)
    return observed[len(prefix) : end]


# AC-FR0255-01@v0.6 TRACKS-TRACE command echo equals contract with no injection
def test_command_echo_matches_contract_verbatim_no_injection(trac, event_log, host_repo):
    run_id, _result, events = run_m_impl_journey(trac, event_log)
    full = next(event for event in events if event["type"] == "full.executed")
    selection = max(
        (
            event
            for event in events
            if event["type"] == "test.selected"
            and event["payload"].get("scope") == "full"
            and event["seq"] < full["seq"]
        ),
        key=lambda event: event["seq"],
    )
    contract = load_contract(host_repo)
    sections = {
        "unit": contract.unit,
        "integration": contract.integration,
        "e2e": contract.e2e,
    }
    for layer, actual in full["payload"]["command_echo"].items():
        section = sections[layer]
        assert section is not None
        result_path = _result_argument(section.run, actual)
        cwd = host_repo / section.cwd if section.cwd != "." else host_repo
        expected = resolve_selected_command(section.run, [], result_path, cwd)
        assert tuple(actual) == expected

    outcomes_ref = full["payload"].get("outcomes_ref")
    assert outcomes_ref
    outcomes = json.loads((host_repo / outcomes_ref).read_text(encoding="utf-8"))
    assert sorted(item["node"] for item in outcomes) == sorted(selection["payload"]["nodes"])
    assert all(item["status"] not in ("failed", "error") for item in outcomes)
    assert run_id


# AC-FR0255-02@v0.6 TRACKS-TRACE nightly exists but is never a local gate
def test_nightly_contract_present_and_not_local_gate(trac, event_log, host_repo):
    _run_id, _result, events = run_m_impl_journey(trac, event_log)
    contract = load_contract(host_repo)
    assert contract.nightly is not None
    assert contract.nightly.workflow == ".github/workflows/nightly.yml"
    assert tuple(contract.nightly.layers) == ("unit", "integration", "e2e")
    full = [event for event in events if event["type"] == "full.executed"]
    assert full
    assert all(event["payload"].get("gate") == "ISLAND_GATE_2" for event in full)
    command_text = json.dumps(
        [event["payload"].get("command_echo") for event in full],
        sort_keys=True,
    )
    assert contract.nightly.workflow not in command_text
