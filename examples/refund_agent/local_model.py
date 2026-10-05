"""Real autoregressive fallback using an explicitly supplied local MLX checkpoint."""

from pathlib import Path

from ink import FallbackResult


class LocalModel:
    def __init__(self, checkpoint):
        from mlx_lm import load
        from mlx_lm.sample_utils import make_sampler

        path = Path(checkpoint).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("Local fallback requires an existing checkpoint directory")
        self.model, self.tokenizer = load(str(path))
        self.checkpoint = str(path)
        self.sampler = make_sampler(temp=0)
        self._prefixes = {}

    def __call__(self, state, limit=100):
        from mlx_lm import stream_generate

        prompt = self.tokenizer.apply_chat_template(
            [
                {
                    "role": "system",
                    "content": (
                        "Select exactly one digit: 0=refund, 1=request_information, 2=specialist. "
                        f"Refund only settled payments with amount <= {limit}, no chargeback, "
                        "and consistent merchant settings. If status is unknown but all other "
                        "refund conditions pass, choose 1. Otherwise choose 2. Output only digit."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Amount: {state['amount']}. Status: {state['payment_status']}. "
                        f"Chargeback: {state['chargeback']}. "
                        f"Merchant settings consistent: {state['merchant_consistent']}."
                    ),
                },
            ],
            tokenize=False,
            add_generation_prompt=True,
        )
        from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache

        tokens = self.tokenizer.encode(prompt, add_special_tokens=False)
        key = tuple(tokens)
        cache = self._prefixes.get(key)
        cached = cache is not None
        if cache is None:
            cache = make_prompt_cache(self.model)
        # Reuse only attention KV state. Recompute final prompt token and generate
        # a fresh answer on every invocation, including repeated inputs.
        text, final = "", None
        # Bounded output, but still a real forward/generation call every time.
        # No response cache, labels, or fixture results are substituted.
        for response in stream_generate(
            self.model,
            self.tokenizer,
            tokens[-1:] if cached else tokens,
            prompt_cache=cache,
            max_tokens=1,
            sampler=self.sampler,
        ):
            text += response.text
            final = response
        trim_prompt_cache(cache, cache[0].offset - (len(tokens) - 1))
        if len(self._prefixes) < 32:
            self._prefixes[key] = cache
        labels = {"0": "refund", "1": "request_information", "2": "specialist"}
        if text.strip() not in labels or final is None:
            raise ValueError(f"Local fallback returned invalid bounded output: {text!r}")
        return FallbackResult(
            labels[text.strip()],
            model_calls=1,
            request_attempts=1,
            input_tokens=len(tokens),
            output_tokens=final.generation_tokens,
            provider="local-mlx",
            model=self.checkpoint,
        )
