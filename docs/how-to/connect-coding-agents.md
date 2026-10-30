# How to connect a coding agent

Set the base URL of the agent to the proxy address. The agent continues to send its own API key.
thetacode forwards the key to the provider. thetacode does not store the key.

The examples use port `8787`. This is the port in [Run thetacode with Headroom](run-with-headroom.md).
The default port of `thetacode proxy` alone is `8788`.

## Agents that use the Anthropic Messages API

Examples: Claude Code, Pi, OpenCode with an Anthropic provider.

1. Set the base URL. Do not add `/v1`:

   ```sh
   export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
   ```

2. Start the agent in the same shell.

## Agents that use the OpenAI Chat Completions API

Examples: OpenCode, Pi, Aider and Cursor with an OpenAI provider.

1. Set the base URL. Add `/v1`:

   ```sh
   export OPENAI_BASE_URL=http://127.0.0.1:8787/v1
   ```

2. Start the agent in the same shell.

thetacode supports the Chat Completions endpoint only. It does not support the OpenAI Responses
API.

## Make sure that the connection works

1. Send one coding prompt from the agent.
2. Get the statistics:

   ```sh
   curl http://127.0.0.1:8787/thetacode/stats
   ```

3. Make sure that `requests_total` in `metrics` is more than 0.

If `drafts_failed` increases, thetacode cannot get a draft from the local model. Make sure that the
local model server runs and that `THETACODE_LOCAL_MODEL` is correct.
