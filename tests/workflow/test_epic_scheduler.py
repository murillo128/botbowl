import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


planner = load_module(
    "epic_plan_wave", "skills/codex-epic-scheduler/scripts/plan_wave.py"
)
discovery = load_module(
    "find_epic_parent", ".github/scripts/find_epic_parent.py"
)


def snapshot(children, workers=2, parent_state="in-progress"):
    return {
        "parent_state": parent_state,
        "execution_mode": "epic-dag",
        "max_parallel_workers": workers,
        "children": children,
    }


def child(issue, state="queued", depends_on=None, mutex=None):
    return {
        "issue": issue,
        "state": state,
        "depends_on": depends_on or [],
        "mutex": mutex or [],
    }


def epic_body(*issue_numbers):
    rows = "\n".join(
        "  - {{issue: {}, depends_on: [], mutex: []}}".format(number)
        for number in issue_numbers
    )
    return """```yaml
execution_mode: epic-dag
max_parallel_workers: 2
children:
{}
```""".format(rows)


def issue(number, labels, body="", title="epic"):
    return {
        "number": number,
        "title": title,
        "state": "open",
        "labels": [{"name": label} for label in labels],
        "body": body,
    }


def test_parent_and_normal_issue_routing_is_explicit_in_agents():
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "when it declares `execution_mode: epic-dag`, route to `codex-epic-scheduler`" in instructions
    assert "otherwise route to `spec-driven-codex-loop`" in instructions


def test_only_queued_child_with_completed_dependencies_is_candidate():
    plan = planner.plan_wave(
        snapshot(
            [
                child(10, "completed"),
                child(11, depends_on=[10]),
                child(12, depends_on=[11]),
                child(13, "blocked"),
                child(14, "design-required"),
                child(15, "investigation-required"),
            ]
        )
    )
    assert plan["candidates"] == [11]
    assert plan["selected"] == [11]


def test_worker_limit_mutex_and_issue_number_order_bound_wave():
    plan = planner.plan_wave(
        snapshot(
            [
                child(2, mutex=["shared"]),
                child(3, mutex=["shared"]),
                child(4, mutex=["other"]),
                child(8, "in-progress", mutex=["busy"]),
                child(9, mutex=["busy"]),
            ],
            workers=3,
        )
    )
    assert plan["active"] == [8]
    assert plan["available_slots"] == 2
    assert plan["selected"] == [2, 4]


def test_one_remaining_slot_selects_only_lowest_issue_number():
    plan = planner.plan_wave(
        snapshot(
            [
                child(1, "execution-ready"),
                child(20),
                child(10),
            ],
            workers=2,
        )
    )
    assert plan["selected"] == [10]


def test_repeated_plan_does_not_reselect_promoted_child():
    original = snapshot([child(7)], workers=1, parent_state="execution-ready")
    first = planner.plan_wave(original)
    updated = planner.apply_wave(original, first["selected"])
    second = planner.plan_wave(updated)
    assert first["selected"] == [7]
    assert second["selected"] == []


@pytest.mark.parametrize(
    "children",
    [
        [child(1), child(1)],
        [child(1, depends_on=[2])],
        [child(1, depends_on=[2]), child(2, depends_on=[1])],
    ],
)
def test_invalid_graph_fails_before_selection(children):
    with pytest.raises(planner.ContractError):
        planner.plan_wave(snapshot(children))


def test_completed_or_queued_child_wakes_unique_active_parent():
    parents = [issue(3, ["in-progress"], epic_body(4, 5), "active epic")]
    assert discovery.find_parent(4, parents) == {"number": 3, "title": "active epic"}
    assert discovery.find_parent(5, parents) == {"number": 3, "title": "active epic"}


def test_event_outside_active_epic_is_noop():
    parents = [
        issue(3, ["execution-ready"], epic_body(4)),
        issue(8, ["in-progress"], epic_body(9)),
    ]
    assert discovery.find_parent(4, parents) is None


def test_multiple_active_parents_fail_closed():
    parents = [
        issue(3, ["in-progress"], epic_body(4)),
        issue(8, ["in-progress"], epic_body(4)),
    ]
    with pytest.raises(discovery.DiscoveryError, match="multiple active epics"):
        discovery.find_parent(4, parents)


def test_wakeup_reuses_launcher_and_parent_scoped_concurrency():
    wakeup = (ROOT / ".github/workflows/codex-epic-scheduler.yml").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / ".github/workflows/codex-execute-ready.yml").read_text(
        encoding="utf-8"
    )
    assert "uses: ./.github/workflows/codex-execute-ready.yml" in wakeup
    assert "github.event.label.name == 'completed'" in wakeup
    assert "github.event.label.name == 'queued'" in wakeup
    assert "types: [labeled]" in launcher
    assert "github.event.label.name == 'execution-ready'" in launcher
    assert "inputs.issue_number != ''" in launcher
    assert "inputs.issue_number || github.event.issue.number" in launcher
    assert "WAIT_FOR_EXISTING_TURN: ${{ inputs.issue_number != '' }}" in launcher
    assert "waiting to serialize the requested follow-up turn" in launcher
