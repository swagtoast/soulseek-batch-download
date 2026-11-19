"""One-shot MLX inference worker for fine-tuned MiniCPM5-2B + LoRA adapter.

Invoked by llm_formatter.py via the configured MLX Python environment. Reads a JSON payload
from stdin (`{"model": "...", "adapter": "...", "requests": [{"system": "...", "user": "..."}]}`)
and outputs a JSON list of raw response strings on stdout, then exits so 0 MB VRAM remains used.
"""

from __future__ import annotations

import json
import os
import re
import sys


def main() -> int:
    payload = json.loads(sys.stdin.read())
    model_path = str(payload["model"])
    adapter_path = payload.get("adapter") or None
    if adapter_path and not os.path.isdir(adapter_path):
        adapter_path = None
    requests = payload.get("requests") or []
    max_tokens = int(payload.get("max_tokens") or 1500)

    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_logits_processors, make_sampler

    model, tok = load(model_path, adapter_path=adapter_path)
    sampler = make_sampler(temp=0.0)
    logits_proc = make_logits_processors(repetition_penalty=1.02, repetition_context_size=64)

    outputs: list[str] = []
    for req in requests:
        messages = [
            {"role": "system", "content": str(req.get("system") or "")},
            {"role": "user", "content": str(req.get("user") or "")},
        ]
        prompt = tok.apply_chat_template(
            messages, add_generation_prompt=True, enable_thinking=False, tokenize=False
        )
        raw = generate(
            model,
            tok,
            prompt=prompt,
            max_tokens=max_tokens,
            verbose=False,
            sampler=sampler,
            logits_processors=logits_proc,
        )
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
        outputs.append(cleaned)

    print(json.dumps({"outputs": outputs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
