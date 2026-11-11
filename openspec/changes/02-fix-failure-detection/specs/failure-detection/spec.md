# Spec Delta

## Purpose

Decide whether an agent step failed from the signals that the agent and its tools report, so a
real failure keeps the frontier in charge and a file that only mentions errors does not.

## ADDED Requirements

### Requirement: Results are linked to their calls

The system SHALL link each tool result to its tool call by id, and give the result the step
group of that call. A result whose id matches no call SHALL get the group `irreversible`.

#### Scenario: Parallel calls

- **WHEN** a request has two tool calls and their results come in the opposite order
- **THEN** each result gets the group of its own call

#### Scenario: Unlinked result

- **WHEN** a tool result's id matches no tool call in the request
- **THEN** it gets the group `irreversible`

#### Scenario: Id that is not a string

- **WHEN** a tool call or tool result has an id that is not a string
- **THEN** the result is unlinked and gets the group `irreversible`

### Requirement: Structured signals take precedence

The system SHALL decide failure from the first signal present, in this order: the result's
error flag, a reported exit status, a test runner summary. Keyword matching SHALL apply only
when none is present, and never to `read` steps.

#### Scenario: Error flag

- **WHEN** an Anthropic tool result has `is_error: true`
- **THEN** the step is a failure

#### Scenario: Pi exit status without a keyword

- **WHEN** a shell result ends with `Command exited with code 2` and contains no error keyword
- **THEN** the step is a failure

#### Scenario: Passing test summary with error text

- **WHEN** a shell result ends with `41 passed in 2.1s` and earlier lines contain `Error:` from a
  test that checks error messages
- **THEN** the step is not a failure

#### Scenario: Failing test summary

- **WHEN** a shell result contains `3 failed, 41 passed`
- **THEN** the step is a failure

#### Scenario: No structured signal

- **WHEN** a `run` step's result has no error flag, exit status or test summary, and contains
  `Traceback`
- **THEN** the step is a failure

### Requirement: Structured signals match whole lines

An exit status or test summary SHALL count only when it makes up a whole line, ignoring
surrounding whitespace and `=` framing. A passing pytest summary SHALL count only when it ends
with its timing (`in 2.1s`), and a bare `OK` only after a `Ran N tests` line.

#### Scenario: Signal inside a line

- **WHEN** a `run` step's result contains `Traceback` and a line `processed 3 passed items`
- **THEN** the step is a failure

#### Scenario: Passing pytest summary without timing

- **WHEN** a `run` step's result contains `Error: Cannot find module x` and ends with `12 passed`
- **THEN** the step is a failure

#### Scenario: Failing pytest summary without timing

- **WHEN** a `run` step's result ends with `3 failed, 41 passed`
- **THEN** the step is a failure

#### Scenario: Bare OK without a Ran line

- **WHEN** a `run` step's result contains `Traceback` and ends with `OK`, with no `Ran N tests`
  line before it
- **THEN** the step is a failure

### Requirement: Read steps do not fail on content

The system SHALL NOT mark a `read` step as failed because of its output text. A `read` step
SHALL fail only on an error flag or a non-zero exit status. Exit code 1 from a search command
(`rg`, `grep`, `egrep`, `fgrep`) SHALL NOT count, even when the result has the error flag. A read
tool that runs no command (such as `read` or `grep`) SHALL NOT be checked for exit status lines.

#### Scenario: Reading a file that mentions errors

- **WHEN** the agent reads a Python file containing `except Exception as e:` and
  `raise ValueError("Error: bad")`
- **THEN** the step is not a failure, and the judge summary shows `result ok`

#### Scenario: Read-only shell command

- **WHEN** a shell command `cat .docs/instructions.md` prints `## Exception messages`
- **THEN** the step is not a failure

#### Scenario: Search with no match

- **WHEN** a shell command `rg foo` exits with `Command exited with code 1` and no other output
- **THEN** the step is not a failure

#### Scenario: Search with no match and the error flag

- **WHEN** a shell command `rg foo` returns an Anthropic result with `is_error: true` and the text
  `Exit code 1`
- **THEN** the step is not a failure

#### Scenario: File content that looks like an exit status

- **WHEN** the agent reads a file with the `read` tool and a line of it is `exit code 3`
- **THEN** the step is not a failure

#### Scenario: Read command that fails

- **WHEN** a shell command `cat missing.py` exits with `Command exited with code 1`
- **THEN** the step is a failure

### Requirement: Failure line shown to the judge

The system SHALL show the judge the line that decided the failure: the exit status, the test
summary, or the first matching keyword line.

#### Scenario: Test summary decides

- **WHEN** a step fails because of `3 failed, 41 passed`
- **THEN** the judge summary contains `result FAILED: 3 failed, 41 passed`
