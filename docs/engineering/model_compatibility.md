# Model compatibility and GPU portability

Status on 2026-11-04: configuration and a shared-CLI bootstrap are implemented.
Local profiles remain **unverified** for actual inference. NIM has passed a synthetic
SQL/tool/output round trip; training results are tracked in the Milestone 1 report.
No model weights were downloaded and no Colab/Kaggle job or Docker model server ran.
The available Windows RTX 3060 Ti has 8 GiB VRAM; the BF16 candidates below exceed
the conservative capacity gates. A successful preflight means capacity only,
not that kernels, chat templates, tool calls, or model quality have been qualified.

## Candidate inventory

All profiles use the same harness/provider boundary. Select a JSON with `--profile`;
there is no model-specific agent implementation. Every profile deliberately limits
operation to 32,768 context tokens, 24,000 input bytes, and 1,024 generated tokens.
An explicit NIM qualification profile in `config/experiments/nim_reasoning_2k.json`
selects 2,048 output tokens; no length finish reason was reported in qualification.
No aggregate
question-token/request/context limit or thinking mode was changed.
These are harness budgets, not the models' advertised context capacities. Thinking
can consume the output budget before an answer appears; record truncations and
qualify a larger budget explicitly when needed.

| Profile in `config/model_profiles/` | Exact model | Native context | Runtime/parser | Eligibility status |
| --- | --- | --- | --- | --- |
| `nim_nemotron_3_5_bolt_e4b.json` | `nvidia/nemotron-3.5-bolt-e4b` | Endpoint up to 64K | NVIDIA hosted NIM; provider parses tools | Development only: cloud service and release 2026-10-23 |
| `gemma4_12b_vllm.json` | `google/gemma-4-12B-it` | 262,144 | vLLM; tools `gemma4`, reasoning `gemma4` | Conditional; Apache-2.0; pinned pre-cutoff snapshot |
| `ornith_1_5_9b_vllm.json` | `ornith-ai/Ornith-1.5-9B` | 262,144 | vLLM; tools `qwen3_xml`, reasoning `qwen3` | Conditional; MIT; pinned pre-cutoff snapshot |
| `qwen3_14b_vllm.json` | `Qwen/Qwen3-14B` | 32,768 | vLLM; tools `hermes`, reasoning `qwen3` | Conditional; Apache-2.0; pinned pre-cutoff snapshot |

Identity, licensing, and context evidence: [NVIDIA model card](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3_5-bolt-e4b),
[Gemma model card](https://huggingface.co/google/gemma-4-12B-it),
[Ornith model card](https://huggingface.co/ornith-ai/Ornith-1.5-9B), and
[Qwen model card](https://huggingface.co/Qwen/Qwen3-14B).
Qwen's 131,072 context claim requires YaRN; these profiles do not enable it.
NIM has no immutable hosted revision selector here; `revision: null` acknowledges
that reproducibility limitation instead of pretending a model name pins weights.

| Model | Pinned weights/tokenizer revision | Snapshot date |
| --- | --- | --- |
| Gemma | [`707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7`](https://huggingface.co/google/gemma-4-12B-it/tree/707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7) | 2026-07-20 |
| Ornith | [`489cb97981b8654bcfcf30ce1f94ed1b62e07b53`](https://huggingface.co/ornith-ai/Ornith-1.5-9B/tree/489cb97981b8654bcfcf30ce1f94ed1b62e07b53) | 2026-08-23 |
| Qwen | [`40c069824f4251a91eefaf281ebe4c544efd3e18`](https://huggingface.co/Qwen/Qwen3-14B/tree/40c069824f4251a91eefaf281ebe4c544efd3e18) | 2025-07-26 |

The [competition rules](../official/rule.md) require public weights **before
2026-10-01**, suitable licenses, at most **15B total parameters across all models
ever used during inference**, and final inference completely offline in Docker.
Conditional is an audit status, not organizer approval. Check exact artifact history,
all license obligations and parameter counts before final selection. Qwen's card
reports **14.8B total**, leaving very little budget for any other inference model.
Choose one candidate for a final solution; running all three sequentially still
counts all their parameters. Quantization does not reduce this parameter count.
NIM requires permitted development data use and cannot be part of final inference.

## Capacity estimates and qualification

| BF16 candidate | `minimum_vram_gib` capacity gate | Meaning |
| --- | --- | --- |
| Gemma 12B | 28 GiB | Estimated weights plus modest runtime/KV headroom |
| Ornith 9B | 24 GiB | Estimated weights plus modest runtime/KV headroom |
| Qwen 14B | 34 GiB | Estimated weights plus modest runtime/KV headroom |

These are engineering estimates, **not measured peak VRAM or a guarantee of fit**.
Batch size, context, runtime, multimodal allocation and GPU memory utilization matter.
Preflight checks the selected device order, free memory and configured utilization
budget. For explicit tensor parallelism it divides the estimate across that many
devices; this does not prove sharding, kernels, KV-cache allocation or runtime fit.
CUDA_VISIBLE_DEVICES may select device indices. Qwen needs a larger single GPU or
an explicitly configured, separately qualified TP configuration. Do not lower the
estimate merely to make a check pass.
No quantized artifacts are selected. A future quantized profile needs its own exact
repository/revision, license, parameter inventory, kernel compatibility and measured
tool/answer validation. An 8 GiB device is not qualified by extrapolating BF16 estimates.

Before changing qualification, save: model and tokenizer/template hashes, exact
server image digest/package versions, CUDA/driver/GPU telemetry, launch arguments,
sampling settings, observed peak VRAM, and the synthetic suite report. Exercise
native tool call, tool result, second-turn final answer, malformed arguments,
context exhaustion and abstention using the same shared CLI. Then compare a labeled
training baseline with split/config/revision recorded. Final Docker qualification
also needs network disabled, read-only `/data`, output under `/result`, and the
official runtime/reproducibility limits. Model card benchmarks do not supply this evidence.

## Explicit server setup templates (Linux)

### Shared serving plan

`python -m datamate serve --profile config/model_profiles/ornith_1_5_9b_vllm.json`
prints a dry-run plan. The shared `serving_command` helper builds an argument list
for `python -m vllm.entrypoints.openai.api_server` from the selected profile; it
does not start a process, read credentials, download weights, or select a fallback.
It preserves model ID, served-model name, pinned weight/tokenizer revision, context,
dtype, tensor parallelism, memory fraction and the explicit tool/reasoning parsers.
Optional `model_path`, `cache_dir`, and `chat_template` select an existing snapshot,
download cache and local template respectively. `bf16` is a precision, so configure
`dtype: "bfloat16"` and `quantization: null`; `quantization: "bf16"` is rejected.
Actual quantizers such as AWQ require a separately selected, pinned artifact.

Explicit `--launch` is reserved for Linux/WSL after resource preflight; downloading
remote model artifacts additionally requires `--allow-model-download`. Plan output
and passing resource telemetry do not establish model/server qualification.
No launch or download has been exercised here. Keep `--trust-remote-code` absent;
any runtime needing custom model code requires a separately reviewed setup.

Optional serving requirements are in `requirements/inference-vllm.txt`, outside
the core `uv` environment. Its lower bounds follow the verified Ornith card and
are not an exact runtime lock. A Gemma-compatible build may have additional
requirements. Use the Python version supported by the chosen server build, which
may differ from the core harness's Python >=3.13; the helper's `python_executable`
argument can point to that environment. Freeze tested package versions and CUDA
wheel/image identity before reporting a qualified server. Argument meanings are
documented in the [vLLM server CLI reference](https://docs.vllm.ai/en/latest/cli/serve/).

These commands are **manual opt-in preparation**, not steps executed by the
bootstrap. They can download large artifacts and must run only on a suitable GPU
host. Keep the inference-server environment separate from the Python >=3.13 harness
environment to avoid torch/vLLM dependency conflicts. Pin the exact tested server
version/image digest and Gemma template hash in the run manifest before qualification;
the templates below do not constitute a reproducible, tested runtime lock.

Use a vLLM build containing the Gemma 4 12B architecture and `gemma4` parsers. The
[official Gemma recipe](https://docs.vllm.ai/projects/recipes/en/latest/Google/Gemma4.html)
currently gives a nightly installation path, a tool chat-template override, and
`enable_thinking` support. Copy `examples/tool_chat_template_gemma4.jinja` from the
**same pinned vLLM source/build**, inspect it and preserve its hash; do not fetch an
unpinned template at runtime. For the encoder-free 12B model, use that revision's
tokenizer/processor. The profile requests thinking through chat-template kwargs;
keep thoughts separate from final text.

```bash
vllm serve google/gemma-4-12B-it \
  --revision 707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7 \
  --tokenizer-revision 707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7 \
  --dtype bfloat16 --max-model-len 32768 --max-num-seqs 1 \
  --host 127.0.0.1 --port 8000 --enable-auto-tool-choice \
  --tool-call-parser gemma4 --reasoning-parser gemma4 \
  --chat-template /opt/vllm/examples/tool_chat_template_gemma4.jinja \
  --limit-mm-per-prompt '{"image":0,"audio":0}'
```

Ornith's card specifies **Transformers >=5.8.1, vLLM >=0.19.1, SGLang >=0.5.9**.
It is Qwen3.5 based, thinks by default, and supplies the parser combinations below.
Retain its pinned repository chat template. The published vLLM recipe includes
`--trust-remote-code`; inspect the pinned code before explicitly opting into that
flag if your selected build requires it. Do not silently enable remote code.

```bash
vllm serve ornith-ai/Ornith-1.5-9B \
  --revision 489cb97981b8654bcfcf30ce1f94ed1b62e07b53 \
  --tokenizer-revision 489cb97981b8654bcfcf30ce1f94ed1b62e07b53 \
  --dtype bfloat16 --max-model-len 32768 --max-num-seqs 1 \
  --host 127.0.0.1 --port 8000 --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml --reasoning-parser qwen3
```

SGLang is an alternative serving boundary, not a separate agent. Copy the Ornith
profile, set `runtime` to `sglang`, retain the same model ID/revision, and set its
`tool_parser` to `qwen3_coder`; qualify that configuration independently. For example:

```bash
python -m sglang.launch_server \
  --model-path ornith-ai/Ornith-1.5-9B \
  --revision 489cb97981b8654bcfcf30ce1f94ed1b62e07b53 \
  --dtype bfloat16 --context-length 32768 \
  --host 127.0.0.1 --port 8000 \
  --tool-call-parser qwen3_coder --reasoning-parser qwen3
```

Qwen needs the repository's original tokenizer/chat template and a vLLM version with
the `qwen3` reasoning parser (>=0.9.0). Parser and API kwargs are documented in
[Qwen's vLLM deployment guide](https://qwen.readthedocs.io/en/latest/deployment/vllm.html).

```bash
vllm serve Qwen/Qwen3-14B \
  --revision 40c069824f4251a91eefaf281ebe4c544efd3e18 \
  --tokenizer-revision 40c069824f4251a91eefaf281ebe4c544efd3e18 \
  --dtype bfloat16 --max-model-len 32768 --max-num-seqs 1 \
  --host 127.0.0.1 --port 8000 --enable-auto-tool-choice \
  --tool-call-parser hermes --reasoning-parser qwen3
```

Profiles select temperature/top-p: Gemma and NIM `1.0/0.95`; Ornith `0.6/0.95`
(precise-task starting point); Qwen thinking `0.6/0.95`. Qwen non-thinking experiments
must explicitly set `enable_thinking: false` and use the card's `0.7/0.8` guidance.
Do not inject `top_k`, `min_p`, or other nonstandard sampler keys into portable API
requests without server-specific qualification. Server generation defaults may
still supply them; record those defaults. The NIM card documents conversational
mode but its exact API nesting was not established here, so that field is omitted.

For final offline packaging, download the **pinned** snapshot during build/preparation,
include tokenizer/config/template files, serve its local directory with the same
`--served-model-name` as the profile's `model_id`, and set `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1`. Pin and package all runtime dependencies before disabling
network. Never download weights or templates during evaluation startup.

## Colab / Kaggle / Linux bootstrap

Upload/clone the same checkout and lockfile into a writable working directory;
attach the intended split as read-only input. On Kaggle, use `/kaggle/working` for
state/results and `/kaggle/input/...` for inputs. On Colab, use `/content/...` and
copy resulting reports to durable storage yourself. Obtain a GPU runtime explicitly,
inspect `nvidia-smi`, and select capacity suitable for the chosen profile. No
Colab/Kaggle account, runtime, quota, or successful allocation is assumed.

The stdlib bootstrap does not need harness dependencies to print its plan:

```bash
python scripts/bootstrap_gpu.py --repo-root . \
  --profile config/model_profiles/ornith_1_5_9b_vllm.json
```

Default `--action plan` only prints JSON command arrays; it creates no output,
contacts no endpoint and starts no process. Install `uv` using its official installation
instructions if absent. Explicit `--action setup` invokes `uv sync --frozen` in the
checkout; this can install the required Python and locked packages, but no model
weights. After manually starting the chosen server, explicitly run shared preflight:

```bash
python scripts/bootstrap_gpu.py --repo-root . --action setup
python scripts/bootstrap_gpu.py --repo-root . --action preflight \
  --profile config/model_profiles/ornith_1_5_9b_vllm.json
```

For NIM, choose its profile and set `LLM_API_KEY` in the host environment/secret
store. Never paste the key into JSON, notebook output, run manifests or Git. Local
servers use `LOCAL_LLM_API_KEY`, defaulting to a harmless local credential when absent.
The NIM key is not implicitly forwarded to a local model server.
Keep cloud input limited to data permitted for that development endpoint.

To execute a supplied synthetic suite, all four run paths must be specified:

```bash
python scripts/bootstrap_gpu.py --repo-root . --action run \
  --profile config/model_profiles/ornith_1_5_9b_vllm.json \
  --suite /content/fixtures/synthetic.jsonl \
  --split-root /content/input/train \
  --state-root /content/work/state \
  --output-dir /content/work/results
```

This delegates to `uv run --frozen python -m datamate run` with those paths.
Preflight delegates to `python -m datamate preflight`; neither notebook/bootstrap
contains agent, normalization, tool, scoring or answer logic. `uv run` can install
missing locked dependencies; run setup first and package the environment for offline
use. Relative profile/suite/data paths are resolved by the CLI from `--repo-root`.
The bootstrap exits with the shared command's exit code. A failed capacity check or
missing runtime/model/server remains a failure; no cloud or smaller-model fallback
is selected implicitly.
