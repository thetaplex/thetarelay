# How to set up Pi with ThetaRelay, Headroom, RTK and skills

This guide starts from a computer that has none of these tools. At the end, the
[Pi](https://pi.dev/) coding agent sends each request through ThetaRelay. ThetaRelay sends
easy steps to a local model and hard steps to a frontier model on OpenRouter.

| Tool | What it does |
|---|---|
| ThetaRelay | Routes each request to the lite or the frontier model |
| Headroom | Compresses requests before they are sent |
| RTK | Makes command output shorter before the agent reads it |
| Superpowers, Ponytail, Codebase to Course | Skills: instructions that Pi loads when a task needs them |
| OpenSpec | Spec-driven changes, set up in each project |

## Prerequisites

- macOS or Linux, with about 16 GB of free memory for the local models.
- [Ollama](https://ollama.com/download)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Node.js](https://nodejs.org/) 20 or later, with npm
- [Homebrew](https://brew.sh/)
- [Git](https://git-scm.com/)
- An [OpenRouter](https://openrouter.ai/) account and API key

## 1. Get the local models

1. Start Ollama.
2. Download the judge model and the local model:

   ```sh
   ollama pull tev1
   ollama pull gemma4:e4b
   ```

## 2. Install the tools

```sh
uv tool install "thetarelay[headroom] @ git+https://github.com/thetaplex/thetarelay"
npm install -g @earendil-works/pi-coding-agent
brew install rtk openspec
```

## 3. Start the proxy

Do this step in its own terminal, and keep that terminal open while you use Pi.

```sh
export OPENROUTER_API_KEY=<your OpenRouter key>
THETARELAY_TURN_LOG=~/thetarelay-turns.jsonl thetarelay proxy --debug
```

The proxy prints one line for each request: the model that got it and the reason.

## 4. Connect Pi to the proxy

Add a `thetarelay` provider to `~/.pi/agent/models.json`. If the file does not exist, create
it with this content. If it exists, add the `thetarelay` entry to `providers`.

```json
{
  "providers": {
    "thetarelay": {
      "baseUrl": "http://127.0.0.1:8788/v1",
      "api": "openai-completions",
      "apiKey": "unused",
      "models": [{ "id": "auto" }]
    }
  }
}
```

The proxy sends your OpenRouter key, so `apiKey` can be any text. The proxy also replaces the
model name, so `auto` has no effect.

If `~/.pi/agent/settings.json` has an `enabledModels` list, add `"thetarelay/*"` to it.

## 5. Add RTK

```sh
rtk init -g --agent pi
```

## 6. Add the skills

1. Install Superpowers and Ponytail as Pi packages:

   ```sh
   pi install git:github.com/obra/superpowers
   pi install git:github.com/DietrichGebert/ponytail
   ```

2. Install Codebase to Course as a skill directory:

   ```sh
   mkdir -p ~/.pi/agent/skills
   git clone https://github.com/zarazhangrui/codebase-to-course ~/.pi/agent/skills/codebase-to-course
   ```

## 7. Add OpenSpec to a project

Do this one time in each project:

```sh
cd <your-project>
openspec init --tools pi
```

## 8. Start Pi

In a second terminal, in your project:

```sh
pi --model thetarelay/auto
```

## Make sure that it works

1. Ask Pi a simple question, for example "List the files in this directory".
2. In the proxy terminal, make sure that you see a line with `→ lite (gemma4:e4b)`.
3. Ask Pi for a difficult change, for example a bug fix.
4. Make sure that you see lines with `→ frontier (openai/gpt-6.1-terra)`.
5. Show the request counts:

   ```sh
   curl http://127.0.0.1:8788/stats
   ```

The file `~/thetarelay-turns.jsonl` has one line for each request, with the token counts and the
OpenRouter cost.

## If something is wrong

| Problem | Cause and fix |
|---|---|
| Most requests show `too_large_for_lite` | The request is longer than 100,000 characters. Skills, RTK and OpenSpec make the system prompt of Pi longer. Remove skills that you do not use, or see `THETARELAY_LITE_MAX_REQUEST_CHARS` in [Configuration](../reference/configuration.md). |
| Requests show `judge_error` | Ollama does not run, or the `tev1` model is not downloaded. Do step 1 again. |
| The proxy log shows `headroom-ai not installed` | Install again with the `[headroom]` extra, as in step 2. |
| `cached_tokens` in the turn log stays near 0 for frontier requests | Headroom can change earlier messages, and then the frontier prompt cache does not match. Start the proxy with `--no-headroom-middleware` and compare. |
| Pi cannot connect | The proxy does not run. Do step 3 again. |
