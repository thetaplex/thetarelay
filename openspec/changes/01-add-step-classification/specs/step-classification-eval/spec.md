# Spec Delta

## Purpose

Measure the step classifier on tool calls from real agent sessions, so its safety (no step
classified less severe than it is) and its caution (local steps sent to `irreversible`) are
known numbers, not assumptions.

## ADDED Requirements

### Requirement: Labelled dataset from real sessions

The repository SHALL contain a dataset of tool calls taken from captured agent sessions, each
with the tool name, its arguments, the agent it came from, and a hand-assigned group. The
dataset SHALL cover every agent in the tested set (Pi, OpenCode, Claude Code) and SHALL contain
no API keys, tokens or other secrets.

#### Scenario: Dataset check

- **WHEN** the dataset is checked
- **THEN** every line has `agent`, `tool`, `arguments` and `label`, every label is one of the
  four groups, and no line matches the secret patterns (`sk-`, `Bearer `, `ghp_`, `AKIA`)

#### Scenario: Adding calls

- **WHEN** a new agent session is captured
- **THEN** its unique tool calls can be added to the dataset with a command that drops
  duplicates and leaves the label empty for review

### Requirement: Under-classification is a failure

The evaluation SHALL report every call the classifier puts in a less severe group than its
label, and SHALL fail if there is any.

#### Scenario: Write classified as read

- **WHEN** a call labelled `write` is classified `read`
- **THEN** the evaluation lists it and exits with a failure status

### Requirement: Caution is reported

The evaluation SHALL report, per label, how many calls the classifier puts in a more severe
group, as a count and a share, and the full four-by-four confusion matrix.

#### Scenario: Local command sent to irreversible

- **WHEN** 12 of 80 calls labelled `read` are classified `irreversible`
- **THEN** the report shows `read → irreversible: 12 (15 %)` and lists the 12 calls with their
  programs, so the allowlists can be widened

### Requirement: Runs in CI

The evaluation SHALL need no network and no model, and SHALL run as part of the project's check
command.

#### Scenario: CI run

- **WHEN** `just check` runs
- **THEN** the evaluation runs and its under-classification gate applies
