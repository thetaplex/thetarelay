# How to connect a coding agent

Set the base URL of the agent to the proxy address. The default address is
`http://127.0.0.1:8788`.

ThetaRelay sends the frontier key (`OPENROUTER_API_KEY` or `THETARELAY_FRONTIER_API_KEY`) to
the frontier tier in place of the key of the agent. If neither variable is set, it sends the
key of the agent. It never sends a key to the local tier.

## Agents that use the Anthropic Messages API

Examples: Claude Code, Pi, OpenCode with an Anthropic provider.

1. Set the base URL. Do not add `/v1`:

   ```sh
   export ANTHROPIC_BASE_URL=http://127.0.0.1:8788
   ```

2. Start the agent.

## Agents that use the OpenAI Chat Completions API

Examples: OpenCode, Pi, Aider and Cursor with an OpenAI provider.

1. Set the base URL. Add `/v1`:

   ```sh
   export OPENAI_BASE_URL=http://127.0.0.1:8788/v1
   ```

2. Start the agent.

## Make sure that routing works

1. Start the proxy with `--debug`.
2. Send one request from the agent.
3. Make sure that the proxy prints a line with `→ local` or `→ frontier`.
4. Show the request counts:

   ```sh
   curl http://127.0.0.1:8788/stats
   ```

The model name that the agent sends has no effect. ThetaRelay replaces it with the model of the
tier.
