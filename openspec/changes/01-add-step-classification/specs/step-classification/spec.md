# Spec Delta

## Purpose

Tell what each agent tool call does to the world (read, run, write, or irreversible), so
routing, failure detection and the turn log can treat steps by their risk.

## ADDED Requirements

### Requirement: Four step groups

The system SHALL classify each tool call as exactly one of `read`, `run`, `write` or
`irreversible`, from the tool name and its arguments, without calling a model. Tool names SHALL
be matched case-insensitively.

#### Scenario: Read tool

- **WHEN** a tool call uses `read`, `grep`, `glob`, `ls`, `list` or `find`, in any case
- **THEN** it is classified `read`

#### Scenario: File-editing tool

- **WHEN** a tool call uses `edit`, `multiedit`, `write`, `patch` or `notebookedit`, in any case
- **THEN** it is classified `write`

### Requirement: Agent-internal and web tools

The system SHALL classify the agent's own planning tools (`todowrite`, `todoread`) and web tools
that only fetch (`webfetch`, `websearch`) as `read`. Tools that start another agent (`task`)
SHALL be classified `irreversible`.

#### Scenario: Todo update

- **WHEN** a tool call uses `TodoWrite`
- **THEN** it is classified `read`

#### Scenario: Web fetch

- **WHEN** a tool call uses `WebFetch` with a URL
- **THEN** it is classified `read`

#### Scenario: Sub-agent

- **WHEN** a tool call uses `Task`
- **THEN** it is classified `irreversible`

### Requirement: Unknown and unparsable steps fail closed

The system SHALL classify a tool call as `irreversible` when the tool is unknown, its arguments
are not valid JSON, or its shell command cannot be parsed.

#### Scenario: Unknown tool

- **WHEN** a tool call uses a tool the system does not know, such as `send_email`
- **THEN** it is classified `irreversible`

#### Scenario: Unclosed quote

- **WHEN** a shell command contains an unclosed quote
- **THEN** it is classified `irreversible`

### Requirement: Shell commands are classified by their most severe part

The system SHALL split a shell command into its parts (separated by `&&`, `||`, `;`, `|`, `&`
or a newline), classify each part, and give the command the most severe group of its parts.

#### Scenario: Pipeline of reads

- **WHEN** a shell command is `rg foo src | head -20 && git diff`
- **THEN** it is classified `read`

#### Scenario: Read then edit

- **WHEN** a shell command is `cat a.py && sed -i s/a/b/ a.py`
- **THEN** it is classified `write`

#### Scenario: Background job

- **WHEN** a shell command is `ls & git push`
- **THEN** it is classified `irreversible`

### Requirement: Heredoc bodies are data

The system SHALL treat the body of a heredoc (`<<EOF` … `EOF`, with or without quotes or `-`)
as data, not as commands. The heredoc counts as input to the command that opens it.

#### Scenario: File written by heredoc

- **WHEN** a shell command is `cat > app.py <<'EOF'` followed by Python lines and `EOF`
- **THEN** it is classified `write`, and the Python lines are not classified as commands

#### Scenario: Heredoc input

- **WHEN** a shell command is `python - <<'EOF'` followed by Python lines and `EOF`
- **THEN** the Python lines are not classified as shell commands, and the command is classified
  as inline interpreter code

### Requirement: Read-only shell programs

The system SHALL classify a shell part as `read` only if its program is on the read list and it
uses none of that program's write options. The read list SHALL include at least `cat`, `head`,
`tail`, `ls`, `pwd`, `cd`, `echo`, `grep`, `rg`, `find`, `tree`, `file`, `stat`, `wc`, `which`,
`diff`, `sort`, `uniq`, `jq`, `sed` without `-i`, `awk` without `system(` or redirection, and
`git` with `status`, `diff`, `log`, `show`, `blame`, `grep`, `ls-files` or `rev-parse`.

#### Scenario: Sed print

- **WHEN** a shell command is `sed -n 40,80p src/app.py`
- **THEN** it is classified `read`

#### Scenario: Write option of a read program

- **WHEN** a shell command is `find . -name '*.pyc' -delete`, `git diff --output=x.patch`,
  `rg --pre ./run.sh foo` or `sed -i s/a/b/ x.py`
- **THEN** it is not classified `read`

### Requirement: Output redirection and substitution

The system SHALL classify a shell part that redirects output to a file as at least `write`, and
a part that contains command substitution (`$(…)`, backticks, `<(…)`) by the most severe group of
the substituted command, or as `irreversible` if that cannot be parsed. Redirection to the null
device or to another file descriptor SHALL NOT count as a write.

#### Scenario: Redirection to a file

- **WHEN** a shell command is `cat a.py > b.py` or `cat a.py > /dev/null.bak`
- **THEN** it is classified `write`

#### Scenario: Redirection to the null device

- **WHEN** a shell command is `rg foo 2>/dev/null` or `cat a.py 2>&1`
- **THEN** it is classified `read`

#### Scenario: Substitution

- **WHEN** a shell command is `echo $(git push)`
- **THEN** it is classified `irreversible`

### Requirement: Project code runs

The system SHALL classify a shell part as `run` when it runs tests, builds or project scripts
through a known runner: at least `pytest`, `python`, `uv run`, `just`, `make`, `npm test`,
`npm run`, `pnpm test`, `pnpm run`, `cargo test`, `cargo build`, `go test`, `go build`.

#### Scenario: Test run

- **WHEN** a shell command is `uv run pytest -q`
- **THEN** it is classified `run`

#### Scenario: Runner target that publishes

- **WHEN** a shell command is `make deploy`, `just release` or `npm run publish`
- **THEN** it is classified `irreversible`, because a runner argument that names deploying,
  releasing, publishing, pushing or uploading is treated as leaving the working tree

#### Scenario: Run then push

- **WHEN** a shell command is `just test && git push`
- **THEN** it is classified `irreversible`

### Requirement: Inline interpreter code

The system SHALL classify an interpreter (`python`, `python3`, `node`) given inline code (`-c`,
`-e`, or `-` reading standard input) as `write`, directly or through `uv run`, and an
interpreter given only `--version` or `-V` as `read`. Inline code is new code from the model
that usually edits files; it is not project code.

#### Scenario: Inline edit

- **WHEN** a shell command is `python3 - <<'EOF'` with a script that rewrites a test file
- **THEN** it is classified `write`

#### Scenario: Inline through uv

- **WHEN** a shell command is `uv run python -c 'import app'`
- **THEN** it is classified `write`

#### Scenario: Version check

- **WHEN** a shell command is `python3 --version`
- **THEN** it is classified `read`

### Requirement: Local writes

The system SHALL classify a shell part as `write` when its program changes only the working tree
or local git state: at least `mv`, `cp`, `rm`, `mkdir`, `touch`, `sed -i`, `git add`,
`git commit`, `git checkout`, `git switch`, `git restore`, `git stash`, `uv add`, `uv remove`,
`uv sync`, `npm install`, `pnpm install`.

#### Scenario: Commit

- **WHEN** a shell command is `git add -A && git commit -m "fix"`
- **THEN** it is classified `write`

#### Scenario: Remove

- **WHEN** a shell command is `rm -rf build`
- **THEN** it is classified `write`

### Requirement: Wrapper commands

The system SHALL classify `timeout [options] DURATION command …` by the wrapped command.

#### Scenario: Timeout around a test run

- **WHEN** a shell command is `timeout 5 uv run pytest -x`
- **THEN** it is classified `run`

#### Scenario: Timeout around a push

- **WHEN** a shell command is `timeout -s KILL 60 git push`
- **THEN** it is classified `irreversible`

### Requirement: Everything else is irreversible

The system SHALL classify a shell part whose program is on none of the lists above as
`irreversible`, including network, cloud, container and remote tools.

#### Scenario: Push

- **WHEN** a shell command is `git push origin main`
- **THEN** it is classified `irreversible`

#### Scenario: Network request

- **WHEN** a shell command is `curl -s https://example.com`
- **THEN** it is classified `irreversible`

#### Scenario: Cloud CLI

- **WHEN** a shell command is `gh pr merge 12` or `terraform apply`
- **THEN** it is classified `irreversible`

### Requirement: Both API formats

The system SHALL classify tool calls the same way in Anthropic Messages and OpenAI Chat
Completions bodies and replies, streamed or not, using the full arguments of each call.

#### Scenario: Streamed tool arguments

- **WHEN** a streamed reply sends a tool call's arguments in several pieces
- **THEN** the classification uses the joined arguments

### Requirement: Group of a reply

The system SHALL give a reply the most severe group of its tool calls. A reply with no tool call
SHALL have no group. A reply that shows a tool call whose name or arguments cannot be read
SHALL have the group `irreversible`.

#### Scenario: Several tool calls

- **WHEN** a reply contains one `read` call and one `write` call
- **THEN** the reply's group is `write`

#### Scenario: Text only

- **WHEN** a reply contains only text
- **THEN** the reply has no group

#### Scenario: Tool call that cannot be parsed

- **WHEN** a reply signals a tool call but its name or arguments cannot be read
- **THEN** the reply's group is `irreversible`
