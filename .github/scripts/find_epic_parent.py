#!/usr/bin/env python3
"""Find the unique active epic parent containing a changed child issue."""

import argparse
import json
import re
from pathlib import Path


WORKFLOW_STATES = {
    "queued",
    "execution-ready",
    "in-progress",
    "review-ready",
    "design-required",
    "investigation-required",
    "blocked",
    "completed",
}
FENCED_BLOCK = re.compile(r"```(?:yaml|yml)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
EPIC_MODE = re.compile(r"^execution_mode\s*:\s*epic-dag\s*(?:#.*)?$", re.MULTILINE)
CHILDREN_HEADER = re.compile(r"^children\s*:\s*(?:#.*)?$")
CHILD_ISSUE = re.compile(r"^\s*-\s*(?:\{\s*)?issue\s*:\s*(\d+)\b")


class DiscoveryError(ValueError):
    """Raised when parent discovery cannot choose safely."""


def extract_epic_children(body):
    contracts = []
    for block in FENCED_BLOCK.findall(body or ""):
        if not EPIC_MODE.search(block):
            continue
        lines = block.splitlines()
        header_indexes = [
            index for index, line in enumerate(lines) if CHILDREN_HEADER.match(line)
        ]
        if len(header_indexes) != 1:
            raise DiscoveryError("epic contract must contain exactly one children list")
        children = []
        for line in lines[header_indexes[0] + 1 :]:
            if line and not line[0].isspace():
                break
            match = CHILD_ISSUE.match(line)
            if match:
                children.append(int(match.group(1)))
        if not children:
            raise DiscoveryError("epic contract children list is empty or unreadable")
        contracts.append(children)

    if not contracts:
        return None
    if len(contracts) != 1:
        raise DiscoveryError("issue contains more than one epic-dag contract")
    return set(contracts[0])


def _label_names(issue):
    names = []
    for label in issue.get("labels", []):
        names.append(label.get("name") if isinstance(label, dict) else label)
    return {name for name in names if name}


def find_parent(changed_issue, issues):
    matches = []
    for issue in issues:
        state = issue.get("state")
        if issue.get("pull_request") or (
            state is not None and str(state).lower() != "open"
        ):
            continue
        states = _label_names(issue) & WORKFLOW_STATES
        if states != {"in-progress"}:
            continue
        children = extract_epic_children(issue.get("body") or "")
        if children is not None and changed_issue in children:
            matches.append(issue)

    if len(matches) > 1:
        numbers = ", ".join("#{}".format(issue["number"]) for issue in matches)
        raise DiscoveryError(
            "changed issue #{} belongs to multiple active epics: {}".format(
                changed_issue, numbers
            )
        )
    if not matches:
        return None
    return {"number": matches[0]["number"], "title": matches[0].get("title", "")}


def _flatten_pages(payload):
    if not isinstance(payload, list):
        raise DiscoveryError("issues JSON must be a list")
    if payload and all(isinstance(page, list) for page in payload):
        return [issue for page in payload for issue in page]
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-issue", required=True, type=int)
    parser.add_argument("--issues-json", required=True, type=Path)
    args = parser.parse_args()
    with args.issues_json.open(encoding="utf-8") as handle:
        issues = _flatten_pages(json.load(handle))
    print(json.dumps({"parent": find_parent(args.changed_issue, issues)}, sort_keys=True))


if __name__ == "__main__":
    main()
