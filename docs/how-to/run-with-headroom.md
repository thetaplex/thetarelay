# How to run thetarelay with Headroom

[Headroom](https://github.com/chopratejas/headroom) (`headroom-ai`) compresses the prompt context.
thetarelay then drafts on the smaller context. Use one of the three modes below.

| Mode | Processes | Use it when |
|---|---|---|
| 1. Headroom extension | One: `headroom proxy` | You already use Headroom. This is the recommended mode. |
| 2. Embedded middleware | One: `thetarelay proxy` | You want one process and one install. |
| 3. Chained proxies | Two: `headroom proxy` and `thetarelay proxy` | You must keep the two Python environments separate. |

All examples use Ollama at `http://127.0.0.1:11434/v1` with the model `qwen2.5-coder:7b`.

## Mode 1: Run thetarelay as a Headroom extension

1. Install Headroom with thetarelay in the same tool environment:

   ```sh
   uv tool install headroom-ai --with thetarelay
   ```

   To use a source checkout, run `uv tool install headroom-ai --with-editable .` in the
   repository directory.

2. Start Headroom with the extension:

   ```sh
   THETARELAY_LOCAL_BASE_URL=http://127.0.0.1:11434/v1 \
   THETARELAY_LOCAL_MODEL=qwen2.5-coder:7b \
   headroom proxy --port 8787 --proxy-extension thetarelay
   ```

3. Make sure that the extension is active:

   ```sh
   curl http://127.0.0.1:8787/thetarelay/stats
   ```

In this mode, thetarelay reads its settings from environment variables only. It does not read the
Headroom configuration. Command-line options of `thetarelay proxy` have no effect.

## Mode 2: Run the standalone proxy with embedded Headroom middleware

If `headroom-ai` is installed in the same environment, `thetarelay proxy` adds Headroom
`CompressionMiddleware` automatically.

1. Install thetarelay with the `headroom` extra:

   ```sh
   uv tool install "thetarelay[headroom]"
   ```

   In a source checkout, use `uv run --extra headroom thetarelay` in place of `thetarelay`.

2. Start the proxy:

   ```sh
   thetarelay proxy --port 8787 \
     --local-url http://127.0.0.1:11434/v1 \
     --local-model qwen2.5-coder:7b
   ```

3. Make sure that the middleware is active:

   ```sh
   curl http://127.0.0.1:8787/health
   ```

   The response must contain `"headroom_middleware_active": true`. If the value is `false`,
   `headroom-ai` is not in the environment.

## Mode 3: Chain Headroom in front of thetarelay

Headroom receives the agent traffic and compresses it. Headroom then sends the compressed request
to thetarelay. thetarelay sends it to the provider.

1. In terminal 1, start thetarelay without the embedded middleware:

   ```sh
   thetarelay proxy --port 8788 --no-headroom-middleware --local-model qwen2.5-coder:7b
   ```

   > **CAUTION:** Always use `--no-headroom-middleware` in this mode. If you do not, thetarelay
   > compresses a context that Headroom compressed before.

2. In terminal 2, start Headroom with thetarelay as its upstream:

   ```sh
   ANTHROPIC_TARGET_API_URL=http://127.0.0.1:8788 \
   OPENAI_TARGET_API_URL=http://127.0.0.1:8788/v1 \
   headroom proxy --port 8787
   ```

## Next step

[Connect a coding agent](connect-coding-agents.md) to port `8787`.
