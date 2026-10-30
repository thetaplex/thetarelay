# How to run thetacode with Headroom

[Headroom](https://github.com/chopratejas/headroom) (`headroom-ai`) compresses the prompt context.
thetacode then drafts on the smaller context. Use one of the three modes below.

| Mode | Processes | Use it when |
|---|---|---|
| 1. Headroom extension | One: `headroom proxy` | You already use Headroom. This is the recommended mode. |
| 2. Embedded middleware | One: `thetacode proxy` | You want one process and one install. |
| 3. Chained proxies | Two: `headroom proxy` and `thetacode proxy` | You must keep the two Python environments separate. |

All examples use Ollama at `http://127.0.0.1:11434/v1` with the model `qwen2.5-coder:7b`.

## Mode 1: Run thetacode as a Headroom extension

1. Install Headroom with thetacode in the same tool environment:

   ```sh
   uv tool install headroom-ai --with thetacode
   ```

   To use a source checkout, run `uv tool install headroom-ai --with-editable .` in the
   repository directory.

2. Start Headroom with the extension:

   ```sh
   THETACODE_LOCAL_BASE_URL=http://127.0.0.1:11434/v1 \
   THETACODE_LOCAL_MODEL=qwen2.5-coder:7b \
   headroom proxy --port 8787 --proxy-extension thetacode
   ```

3. Make sure that the extension is active:

   ```sh
   curl http://127.0.0.1:8787/thetacode/stats
   ```

In this mode, thetacode reads its settings from environment variables only. It does not read the
Headroom configuration. Command-line options of `thetacode proxy` have no effect.

## Mode 2: Run the standalone proxy with embedded Headroom middleware

If `headroom-ai` is installed in the same environment, `thetacode proxy` adds Headroom
`CompressionMiddleware` automatically.

1. Install thetacode with the `headroom` extra:

   ```sh
   uv tool install "thetacode[headroom]"
   ```

   In a source checkout, use `uv run --extra headroom thetacode` in place of `thetacode`.

2. Start the proxy:

   ```sh
   thetacode proxy --port 8787 \
     --local-url http://127.0.0.1:11434/v1 \
     --local-model qwen2.5-coder:7b
   ```

3. Make sure that the middleware is active:

   ```sh
   curl http://127.0.0.1:8787/health
   ```

   The response must contain `"headroom_middleware_active": true`. If the value is `false`,
   `headroom-ai` is not in the environment.

## Mode 3: Chain Headroom in front of thetacode

Headroom receives the agent traffic and compresses it. Headroom then sends the compressed request
to thetacode. thetacode sends it to the provider.

1. In terminal 1, start thetacode without the embedded middleware:

   ```sh
   thetacode proxy --port 8788 --no-headroom-middleware --local-model qwen2.5-coder:7b
   ```

   > **CAUTION:** Always use `--no-headroom-middleware` in this mode. If you do not, thetacode
   > compresses a context that Headroom compressed before.

2. In terminal 2, start Headroom with thetacode as its upstream:

   ```sh
   ANTHROPIC_TARGET_API_URL=http://127.0.0.1:8788 \
   OPENAI_TARGET_API_URL=http://127.0.0.1:8788/v1 \
   headroom proxy --port 8787
   ```

## Next step

[Connect a coding agent](connect-coding-agents.md) to port `8787`.
