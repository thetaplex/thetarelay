# Spec Delta

## Purpose

Decide whether an agent step failed from the signals that the agent and its tools report, so a
real failure keeps the frontier in charge and a file that only mentions errors does not.

## ADDED Requirements

### Requirement: Only executed commands can fail

The system SHALL treat a tool result as a possible failure only if its tool call is a shell
command or another tool that executes code. Results of read tools SHALL never count as failures.

#### Scenario: Reading a file that mentions errors

- **WHEN** the agent reads a Python file containing `except Exception as e:` and `raise ValueError("Error: bad")`
- **THEN** the step is not a failure, and the judge summary shows `result ok`

#### Scenario: Grep output with error lines

- **WHEN** the agent runs the `grep` tool for `Error:` and gets matching lines
- **THEN** the step is not a failure

#### Scenario: Search with no match

- **WHEN** a shell command `rg foo` or `grep foo a.py` exits with code 1 and no output
- **THEN** the step is not a failure

#### Scenario: Unlinked result

- **WHEN** a tool result's id matches no tool call in the request
- **THEN** it is treated as the result of a shell command

### Requirement: Structured signals take precedence

The system SHALL decide failure from structured signals when present, in this order: the
result's error flag, a reported exit status, a test runner summary. Keyword matching SHALL apply
only when none is present.

#### Scenario: Error flag

- **WHEN** an Anthropic tool result has `is_error: true`
- **THEN** the step is a failure

#### Scenario: Non-zero exit status

- **WHEN** a shell result contains `Command exited with code 2`
- **THEN** the step is a failure

#### Scenario: Passing test summary with error text

- **WHEN** a shell result ends with `41 passed in 2.1s` and earlier lines contain `Error:` from a
  test that checks error messages
- **THEN** the step is not a failure

#### Scenario: Failing test summary

- **WHEN** a shell result contains `3 failed, 41 passed`
- **THEN** the step is a failure

#### Scenario: No structured signal

- **WHEN** a shell result has no error flag, exit status or test summary, and contains `Traceback`
- **THEN** the step is a failure

### Requirement: Failure line shown to the judge

The system SHALL show the judge the line that decided the failure (the exit status, the test
summary, or the first matching keyword line).

#### Scenario: Test summary decides

- **WHEN** a step fails because of `3 failed, 41 passed`
- **THEN** the judge summary contains `result FAILED: 3 failed, 41 passed`
