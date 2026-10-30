# ThetaCode: Local-to-Frontier Speculative Drafting Proxy & Headroom Extension

**ThetaCode** intercepts prompt and context requests from coding agents (**Pi**, **OpenCode**, **Cursor**, **Claude Code**, **Aider**), generates a fast speculative draft using a local model (**Ollama**, **MLX**, **llama.cpp**, **vLLM**), and feeds that draft to the target frontier model (**Anthropic Claude** or **OpenAI GPT**) for near-instant verification.

Designed from the ground up to pair with **[Headroom (`headroom-ai`)](https://github.com/chopratejas/headroom)** so that your local model only ever reads the **Headroom-compressed** context (60–90% smaller prompt -> sub-second local time-to-first-token).

---

## Architecture: How ThetaCode Works with Headroom

```
Coding Agent (Pi / OpenCode / Cursor / Claude Code)
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. Headroom Compression Pipeline                                │
│    SmartCrusher + CodeAwareCompressor + RollingWindow           │
│    (Shrinks 80k token context -> ~12k tokens; preserves prefix) │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. ThetaCode Speculative Drafter (Local Model)                  │
│    Calls Ollama / MLX / vLLM / llama.cpp on compressed tail     │
│    Produces candidate code / tool_use draft                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
          ┌────────────────────┴────────────────────┐
          ▼ (OpenAI Target)                         ▼ (Anthropic / Generic Target)
┌───────────────────────────────┐         ┌──────────────────────────────────────┐
│ Native `prediction` Field     │         │ Live-Zone `<speculative_draft>`      │
│ Injects OpenAI Predicted      │         │ Appends draft ONLY to last user turn │
│ Outputs payload for server-   │         │ (100% Anthropic prefix cache hit!).  │
│ side speculative decoding.    │         │ If frontier replies `<ACCEPT/>`,     │
│                               │         │ swaps in the local draft & tools!    │
└───────────────────────────────┘         └──────────────────────────────────────┘
```

### Why Appending to the Final User Turn Matters
Headroom's `CacheAligner` stabilizes the system prompt and historical messages so Anthropic/OpenAI prefix caches hit at 90% discount. ThetaCode **never mutates the system prompt or historical messages**—it injects `<speculative_draft>` exclusively into the final user message (the uncached "live zone"), preserving 100% of your prompt cache hits.

---

## Installation

```bash
# Install into your active environment
pip install -e ".[dev]"

# Or install directly into your existing Headroom uv tool environment:
uv pip install --python ~/.local/share/uv/tools/headroom-ai/bin/python -e .
```

---

## 3 Ways to Run with Headroom

### Mode 1: Native Headroom Proxy Plugin (Recommended)
ThetaCode registers a `headroom.proxy_extension` entry point (`thetacode = "thetacode.headroom_ext:install"`). Headroom runs its full proxy server, compresses the context, invokes `ThetaCodeTurnHook` right after compression, and attributes speculative token savings directly into Headroom's `/stats` dashboard:

```bash
THETACODE_LOCAL_BASE_URL=http://127.0.0.1:11434/v1 \
THETACODE_LOCAL_MODEL=qwen2.5-coder:7b \
headroom proxy --port 8787 --proxy-extension thetacode
```

### Mode 2: Standalone ThetaCode Proxy (with Embedded Headroom ASGI Middleware)
Run `thetacode proxy` directly. If `headroom-ai` is installed in the Python environment, ThetaCode automatically mounts `headroom.integrations.asgi.CompressionMiddleware` in-process:

```bash
thetacode proxy \
  --port 8787 \
  --local-url http://127.0.0.1:11434/v1 \
  --local-model qwen2.5-coder:7b
```

### Mode 3: Chained Proxies (Zero Shared Environment)
Keep `headroom proxy` and `thetacode proxy` as separate processes:

```bash
# Terminal 1: Start ThetaCode on port 8788
thetacode proxy --port 8788 --no-headroom-middleware --local-model qwen2.5-coder:7b

# Terminal 2: Point Headroom's upstream target to ThetaCode
ANTHROPIC_TARGET_API_URL=http://127.0.0.1:8788 \
OPENAI_TARGET_API_URL=http://127.0.0.1:8788/v1 \
headroom proxy --port 8787
```

---

## Connecting Coding Agents

Point your coding agent at port `8787`:

- **Claude Code / Pi / OpenCode (Anthropic)**:
  ```bash
  export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
  ```
- **OpenCode / Pi / Aider / Cursor (OpenAI)**:
  ```bash
  export OPENAI_BASE_URL=http://127.0.0.1:8787/v1
  ```

## Metrics & Observability

Query `/thetacode/stats` (available on both `thetacode proxy` and `headroom proxy --proxy-extension thetacode`) to inspect real-time speculative acceptance rates, local draft latency, and output tokens saved:

```bash
curl http://127.0.0.1:8787/thetacode/stats | jq
```
