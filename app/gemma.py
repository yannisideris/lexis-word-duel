"""LLM provider cascade with per-model profiles. Keys come only from environment variables.

Free models only by default:
  groq        GPT-OSS 120B  (GROQ_API_KEY, GROQ_MODEL)
  groq2       GPT-OSS 20B   (same key, GROQ_MODEL_2; separate free limits)
  google      Gemini 3.5 Flash-Lite (GEMINI_API_KEY, GOOGLE_MODEL)
  gemma       Gemma 4 26B via Google (same key, GEMMA_MODEL)
  openrouter  Gemma 4 26B :free (OPENROUTER_API_KEY, OPENROUTER_MODEL), used only if the key is set
  cloudflare  optional (CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_MODEL)
LLM_PROVIDERS sets the fallback order; LLM_ROTATION=random (default) picks each turn's first provider
at random, weighted by LLM_WEIGHTS ("groq:3,google:2"). The engine fixes positions of legal words for
every model.
"""
from __future__ import annotations

import json
import logging
import os
import asyncio
import random
import re
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("lexis.llm")


def _env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


GROQ_MODEL = _env("GROQ_MODEL") or "openai/gpt-oss-120b"
GROQ_MODEL_2 = _env("GROQ_MODEL_2") or "openai/gpt-oss-20b"
OPENROUTER_MODEL = _env("OPENROUTER_MODEL", "OPENROUTER_GEMMA_MODEL") or "google/gemma-4-26b-a4b-it:free"
GOOGLE_MODEL = _env("GOOGLE_MODEL") or "gemini-3.5-flash-lite"
GEMMA_MODEL = _env("GEMMA_MODEL", "GOOGLE_GEMMA_MODEL") or "gemma-4-26b-a4b-it"
CLOUDFLARE_MODEL = _env("CLOUDFLARE_MODEL", "CLOUDFLARE_GEMMA_MODEL") or "@cf/openai/gpt-oss-120b"
# A stuck provider must not freeze the game: cloud calls give up fast and the next provider answers.
TIMEOUT = float(_env("LLM_TIMEOUT", "GEMMA_TIMEOUT") or 30)
COOLDOWN_SECONDS = 60.0
SHORT_WAIT_MAX = 8.0   # seconds worth waiting on a per-minute limit before switching model

# Shown before the JSON when "thinking" is on but the model has no built-in reasoning (Gemma).
THINK_NOTE = ("Before the JSON, check each move briefly in plain text: which board tile it goes through, "
              "which rack letters it uses, and the cross words it makes. Put the final JSON last.")


@dataclass(frozen=True)
class Profile:
    """How to ask one model family for a move. Values come from the self-play tests (2026-11-08)."""
    name: str
    native: bool            # has built-in reasoning
    json_mode: bool         # provider-enforced JSON
    temperature: float
    fast: str               # reasoning effort / thinking level when the thinking switch is off
    deep: str               # ... when it is on, or after an exchange request
    fast_max: int           # output cap incl. reasoning (fast)
    deep_max: int           # output cap incl. reasoning (deep); Groq counts it against 8000 tokens/min


PROFILES: list[tuple[str, Profile]] = [
    # JSON mode made GPT-OSS reply {"moves":[],"exchange":...} nearly every turn; plain text + our parser works.
    # Low effort uses ~600-1500 tokens; medium needs room but must stay under Groq's 8000 tokens/min.
    # Groq reserves prompt + max_tokens against 8000 tokens/min, so caps are kept as tight as safe.
    # 2000 was too tight (low reasoning sometimes used it all and returned nothing).
    ("gpt-oss", Profile("GPT-OSS", True, False, 0.6, "low", "medium", 2400, 4500)),
    # Gemini 3.x rejects MINIMAL; its thinking counts toward the cap, so give it room.
    # Flash-Lite: LOW 1.7 s/move (1 of 9 placed on its own), MEDIUM 8 s (4 of 10), HIGH 18-49 s (too slow).
    ("gemini", Profile("Gemini", True, True, 0.7, "LOW", "MEDIUM", 2000, 6000)),
    # Gemma has no reasoning switch on Google; without a cap it rambled for 60-110 s. Thinking = plain-text check.
    ("gemma", Profile("Gemma", False, True, 0.7, "MINIMAL", "MINIMAL", 400, 6000)),
    ("qwen", Profile("Qwen", True, True, 0.6, "none", "default", 1200, 5000)),
]
DEFAULT_PROFILE = Profile("model", False, True, 0.7, "", "", 400, 4000)


def configured_providers() -> list[str]:
    order = [p.strip() for p in (_env("LLM_PROVIDERS", "GEMMA_PROVIDERS") or "groq,groq2,google,gemma,openrouter,cloudflare").split(",") if p.strip()]
    have = {
        "groq": bool(_env("GROQ_API_KEY")),
        "groq2": bool(_env("GROQ_API_KEY")),
        "openrouter": bool(_env("OPENROUTER_API_KEY")),
        "google": bool(_env("GEMINI_API_KEY", "GOOGLE_API_KEY")),
        "gemma": bool(_env("GEMINI_API_KEY", "GOOGLE_API_KEY")),
        "cloudflare": bool(_env("CLOUDFLARE_API_TOKEN") and _env("CLOUDFLARE_ACCOUNT_ID")),
    }
    return [p for p in order if have.get(p)]


def model_of(provider: str) -> str:
    return {"groq": GROQ_MODEL, "groq2": GROQ_MODEL_2, "openrouter": OPENROUTER_MODEL,
            "google": GOOGLE_MODEL, "gemma": GEMMA_MODEL, "cloudflare": CLOUDFLARE_MODEL}.get(provider, "")


def profile_of(provider: str) -> Profile:
    """Profile for the provider's model; env overrides such as GEMINI_FAST=MEDIUM or GPT_OSS_FAST_MAX=3000."""
    from dataclasses import replace
    m = model_of(provider).lower()
    prof = next((p for key, p in PROFILES if key in m), DEFAULT_PROFILE)
    prefix = prof.name.upper().replace("-", "_")
    changes: dict = {}
    for field, cast in (("fast", str), ("deep", str), ("fast_max", int), ("deep_max", int), ("temperature", float)):
        raw = _env(f"{prefix}_{field.upper()}")
        if raw:
            try:
                changes[field] = cast(raw)
            except ValueError:
                pass
    return replace(prof, **changes) if changes else prof


def native_reasoning(provider: str) -> bool:
    return profile_of(provider).native


def pretty_model(provider: str) -> str:
    m = model_of(provider).lower()
    for key, name in (("gpt-oss-120b", "GPT-OSS 120B"), ("gpt-oss-20b", "GPT-OSS 20B"), ("gemma", "Gemma 4 26B"),
                      ("flash-lite", "Gemini Flash-Lite"), ("gemini", "Gemini"), ("qwen", "Qwen")):
        if key in m:
            return name
    return model_of(provider) or provider


ROTATE = (_env("LLM_ROTATION") or "random").lower() != "off"
DEFAULT_WEIGHTS = "groq:3,groq2:2,google:1,gemma:1,openrouter:1"


def display_name() -> str:
    if _env("AI_NAME"):
        return _env("AI_NAME")
    providers = configured_providers()
    if ROTATE and len(providers) > 1:
        return "AI"
    return pretty_model(providers[0]) if providers else "AI"


def _weights() -> dict[str, float]:
    """LLM_WEIGHTS like "groq:3,groq2:1,google:2,gemma:2,openrouter:1" (missing = 1)."""
    out: dict[str, float] = {}
    # Default favours GPT-OSS (best at placing its own words); override with LLM_WEIGHTS.
    for part in (_env("LLM_WEIGHTS") or DEFAULT_WEIGHTS).split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = max(0.0, float(v))
            except ValueError:
                pass
    return out


_cooldown: dict[str, float] = {}
LAST_USAGE: list[int] = [0, 0]   # [prompt, completion incl. reasoning] of the latest call


def turn_order() -> list[str]:
    """Provider order for one AI turn: a weighted-random first pick among providers that are
    not resting, then the rest in the configured order as fallbacks."""
    providers = configured_providers()
    if not ROTATE or len(providers) < 2:
        return providers
    now = time.monotonic()
    awake = [p for p in providers if _cooldown.get(p, 0) <= now] or providers
    w = _weights()
    weights = [w.get(p, 1.0) for p in awake]
    if sum(weights) <= 0:
        weights = [1.0] * len(awake)
    first = random.choices(awake, weights=weights, k=1)[0]
    return [first] + [p for p in providers if p != first]


def parse_json(text: str) -> dict:
    text = re.sub(r"<(think|thought)>.*?</\1>", "", text or "", flags=re.S).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    # Models sometimes emit several objects in a row; read them all.
    dec, objs, i = json.JSONDecoder(), [], text.find("{")
    while 0 <= i < len(text):
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            objs.append(obj)
        i = text.find("{", end)
    if not objs:
        raise LLMError("Model reply was not JSON")
    # If the model corrected itself, its LAST answer object is the considered one.
    answers = [o for o in objs if any(k in o for k in ("moves", "pick", "exchange"))]
    if answers:
        return answers[-1]
    return {"moves": [o for o in objs if "word" in o]}


class LLMError(RuntimeError):
    pass


class ShortWait(LLMError):
    """Per-minute limit with a short "try again in Xs": worth waiting instead of switching model."""
    def __init__(self, msg: str, seconds: float):
        super().__init__(msg)
        self.seconds = seconds


GemmaError = LLMError  # backwards-compatible name


def _with_think_note(messages: list[dict]) -> list[dict]:
    out = [dict(m) for m in messages]
    out[-1]["content"] = out[-1]["content"].replace("Reply with the JSON only.", "").rstrip() + "\n" + THINK_NOTE
    return out


async def _google(client, provider: str, system: str, messages: list[dict], deep: bool) -> str:
    model, prof = model_of(provider), profile_of(provider)
    level = prof.deep if deep else prof.fast
    if model.startswith("gemini-2.5"):
        tc = {"thinkingBudget": -1 if deep else 0}
    else:
        tc = {"thinkingLevel": level or "LOW"}
    gen: dict = {"temperature": prof.temperature, "thinkingConfig": tc,
                 "maxOutputTokens": prof.deep_max if deep else prof.fast_max}
    if prof.json_mode and not (deep and not prof.native):   # Gemma's plain-text check needs free text
        gen["responseMimeType"] = "application/json"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages],
        "generationConfig": gen,
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    r = await client.post(url, json=payload, headers={"x-goog-api-key": _env("GEMINI_API_KEY", "GOOGLE_API_KEY")},
                          timeout=TIMEOUT * (2 if deep else 1))
    _raise_for(r, provider)
    body = r.json()
    u = body.get("usageMetadata") or {}
    LAST_USAGE[:] = [int(u.get("promptTokenCount") or 0), int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)]
    parts = ((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


async def _openai_style(client, provider: str, system: str, messages: list[dict], deep: bool) -> str:
    model, prof = model_of(provider), profile_of(provider)
    effort = prof.deep if deep else prof.fast
    payload: dict = {"model": model, "messages": [{"role": "system", "content": system}] + messages,
                     "temperature": prof.temperature, "max_tokens": prof.deep_max if deep else prof.fast_max}
    if prof.json_mode and not (deep and not prof.native):
        payload["response_format"] = {"type": "json_object"}
    if provider in ("groq", "groq2"):
        url, key = "https://api.groq.com/openai/v1/chat/completions", _env("GROQ_API_KEY")
        if prof.native and effort:
            payload["reasoning_effort"] = effort
            if "gpt-oss" in model:
                payload["include_reasoning"] = False
            else:
                payload["reasoning_format"] = "hidden"
    elif provider == "openrouter":
        url, key = "https://openrouter.ai/api/v1/chat/completions", _env("OPENROUTER_API_KEY")
        if prof.native and effort:
            payload["reasoning"] = {"effort": effort if effort in ("low", "medium", "high") else "low", "exclude": True}
        else:
            payload["reasoning"] = {"enabled": False, "exclude": True}
    else:
        url = f"https://api.cloudflare.com/client/v4/accounts/{_env('CLOUDFLARE_ACCOUNT_ID')}/ai/v1/chat/completions"
        key = _env("CLOUDFLARE_API_TOKEN")
    r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"},
                          timeout=TIMEOUT * (2 if deep else 1))
    _raise_for(r, provider)
    body = r.json()
    if body.get("error"):
        raise LLMError(f"{provider}: {str(body['error'])[:200]}")
    u = body.get("usage") or {}
    LAST_USAGE[:] = [int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)]
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
    return content if isinstance(content, str) else json.dumps(content or "")


def _raise_for(r: httpx.Response, provider: str) -> None:
    if r.status_code == 200:
        return
    if r.status_code == 429:
        # "try again in 2.25s" (per-minute) is worth a short wait; "19m59s" (daily) rests the provider.
        m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", r.text)
        told = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3)) if m else None
        if told is not None and told <= SHORT_WAIT_MAX:
            log.info("%s asks to wait %.1fs", provider, told)
            raise ShortWait(f"{provider} HTTP 429 (wait {told:.1f}s)", told)
        _cooldown[provider] = time.monotonic() + max(COOLDOWN_SECONDS, told or 0)
    elif r.status_code in (401, 403):
        _cooldown[provider] = time.monotonic() + 3600
    log.warning("%s HTTP %s: %s", provider, r.status_code, r.text[:300])
    raise LLMError(f"{provider} HTTP {r.status_code}")


async def ask_json(system: str, messages: list[dict], *, thinking: bool,
                   order: list[str] | None = None) -> tuple[dict, str]:
    """One logical call: tries providers in order, returns (parsed JSON, provider).
    thinking=True asks for the profile's deep setting (or Gemma's plain-text check)."""
    providers = order or configured_providers()
    if not providers:
        raise LLMError("No AI provider key configured")
    errors = []
    async with httpx.AsyncClient(timeout=TIMEOUT * 2) as client:
        for p in providers:
            if _cooldown.get(p, 0) > time.monotonic():
                continue
            prof = profile_of(p)
            msgs = _with_think_note(messages) if thinking and not prof.native else messages
            try:
                text, deep = "", thinking
                for attempt in range(3):
                    try:
                        if p in ("google", "gemma"):
                            text = await _google(client, p, system, msgs, deep)
                        else:
                            text = await _openai_style(client, p, system, msgs, deep)
                    except ShortWait as sw:          # per-minute limit: wait a moment, same model
                        if attempt == 2:
                            raise
                        await asyncio.sleep(sw.seconds + 0.5)
                        continue
                    if _env("LEXIS_DEBUG"):
                        log.warning("RAW %s: %r", p, text[:600])
                    if text.strip() or deep or not prof.native:
                        break
                    deep = True   # empty reply: the reasoning used up the cap; retry once with room to think
                return parse_json(text), p
            except (LLMError, httpx.HTTPError, ValueError) as exc:
                errors.append(f"{p}: {exc}")
                log.warning("provider %s failed: %s", p, exc)
    raise LLMError("; ".join(errors) or "All providers cooling down")
