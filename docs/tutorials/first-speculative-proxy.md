# Tutorial: run your first speculative proxy

In this tutorial, you run the built-in benchmark. Then you start the proxy with a local model and
send one request to Anthropic through it. At the end, you can read the `x-thetarelay-*` response
headers and the statistics endpoint.

Time: approximately 20 minutes.

## Before you start

You need:

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [just](https://just.systems/man/en/packages.html)
- [Ollama](https://ollama.com/download)
- An Anthropic API key in the `ANTHROPIC_API_KEY` environment variable

> **CAUTION:** Step 5 sends a request to the Anthropic API. Anthropic charges your account for
> this request.

## Step 1: Install thetarelay from source

1. Clone the repository:

   ```sh
   git clone https://github.com/thetaplex/thetarelay.git
   cd thetarelay
   ```

2. Install the project from the lockfile:

   ```sh
   just setup
   ```

## Step 2: Run the benchmark

The benchmark does not use the network. It sends simulated turns through the real proxy code.

1. Run the benchmark:

   ```sh
   just bench
   ```

2. Read the table. Each row is one scenario. The `Mode` column shows the strategy that thetarelay
   used for that scenario.

The latency and cost values are simulated. They show the method, not your hardware. For more
information, see [Cost and latency model](../explanation/cost-and-latency-model.md).

## Step 3: Start a local draft model

1. Download the default draft model:

   ```sh
   ollama pull gemma4:e4b
   ```

2. Make sure that Ollama runs. Ollama listens on `http://127.0.0.1:11434` by default.

## Step 4: Start the proxy

1. Open a new terminal in the repository directory.
2. Start the proxy:

   ```sh
   just run proxy --local-model gemma4:e4b
   ```

3. Find the banner in the terminal. It shows `Listening: http://127.0.0.1:8788`.

## Step 5: Send a request through the proxy

1. Open a third terminal.
2. Send a request. The `-i` option shows the response headers.

   ```sh
   curl -i http://127.0.0.1:8788/v1/messages \
     -H "x-api-key: $ANTHROPIC_API_KEY" \
     -H "anthropic-version: 2023-06-01" \
     -H "content-type: application/json" \
     -d '{"model": "claude-sonnet-5-5", "max_tokens": 512,
          "messages": [{"role": "user",
            "content": "Write a Python function that returns the sum of a list of integers."}]}'
   ```

3. Find these headers in the response:

   | Header | What it tells you |
   |---|---|
   | `x-thetarelay-drafted: true` | The local model made a draft. |
   | `x-thetarelay-mode: accept_swap` | thetarelay asked the frontier model to accept or replace the draft. |
   | `x-thetarelay-draft-ms` | The time of the local draft, in milliseconds. |
   | `x-thetarelay-accepted` | `true` if the frontier model accepted the draft. |

If `x-thetarelay-accepted` is `true`, the response body contains the local draft. If it is
`false`, the response body contains the normal frontier response.

On multi-turn conversations, ThetaRelay's adaptive draft context keeps the draft window prefix
stable. The local model server reuses its prompt cache on subsequent requests, making later
drafts in the same conversation faster.

## Step 6: Read the statistics

1. Get the statistics:

   ```sh
   curl http://127.0.0.1:8788/thetarelay/stats
   ```

2. Find `acceptance_rate_percent` in the `metrics` object. This is the percentage of drafts that
   the frontier model accepted.

## What you did

You ran the benchmark. You started the proxy with a local draft model. You sent one request and
read the result of the draft verification.

## Next steps

- [Connect a coding agent](../how-to/connect-coding-agents.md)
- [Run thetarelay with Headroom](../how-to/run-with-headroom.md)
- [How speculative drafting works](../explanation/how-speculative-drafting-works.md)
