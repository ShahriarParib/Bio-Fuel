"""The guard rails around the assistant. These tests are the reason we are willing to show model output at all."""
from __future__ import annotations

import asyncio

import pytest
from fsip_common.llm import (
    GeminiClient,
    LlmUnavailable,
    _answer_text,
    check_grounded,
    collect_numbers,
    normalise_number,
)


class TestNumberNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("12,000", "12000"), ("12000.0", "12000"), ("12000", "12000"),
        ("0.87", "0.87"), ("0.870", "0.87"), ("5", "5"),
    ])
    def test_equivalent_spellings_compare_equal(self, raw, expected):
        assert normalise_number(raw) == expected

    def test_a_ratio_is_not_a_percentage(self):
        # 0.87 must never silently match "87": that is exactly the hallucination we are hunting
        assert normalise_number("0.87") != normalise_number("87")


class TestGrounding:
    def test_repeating_our_numbers_is_accepted(self):
        facts = {"liters": 1130.0, "station": "Mirpur", "hours": 12}
        text = "Autopilot is sending 1,130 L of diesel to Mirpur, which covers the next 12 hours."
        assert check_grounded(text, collect_numbers(facts)) is None

    def test_an_invented_number_is_caught(self):
        facts = {"liters": 1130.0, "hours": 12}
        text = "Autopilot is sending 1,130 L, about 45% of the tank, over the next 12 hours."
        assert check_grounded(text, collect_numbers(facts)) == "45"

    def test_a_subtly_wrong_quantity_is_caught(self):
        facts = {"liters": 1130.0}
        assert check_grounded("Sending 1,310 L now.", collect_numbers(facts)) == "1,310"

    def test_numbers_inside_nested_facts_are_allowed(self):
        facts = {"lines": [{"liters": 1650.0, "to": "karnaphuli"}], "impact": {"targets": [{"prob": 0.04}]}}
        allowed = collect_numbers(facts)
        assert check_grounded("1,650 L is enough; the risk falls to 0.04.", allowed) is None

    def test_numbers_appearing_in_fact_text_are_allowed(self):
        # the engine's own reason strings carry numbers ("52% full"); re-using them is legitimate
        facts = {"why": "Mirpur Fuel Station DIESEL LOW: 52% full incl. in-flight"}
        assert check_grounded("Mirpur is 52% full, so it is being topped up.", collect_numbers(facts)) is None

    def test_prose_without_numbers_always_passes(self):
        assert check_grounded("The station is being topped up before the evening peak.", set()) is None


class TestClientGuards:
    def test_no_key_means_unavailable_not_an_error(self):
        client = GeminiClient(api_key="")
        assert client.enabled is False
        assert client.status()["state"] == "disabled"
        with pytest.raises(LlmUnavailable):
            asyncio.run(client.complete("instruction", "facts"))

    def test_repeated_failures_open_a_cooldown(self):
        client = GeminiClient(api_key="k", failure_threshold=2, cooldown_s=30)
        client._record_failure()
        assert client.in_cooldown is False
        client._record_failure()
        assert client.in_cooldown is True
        assert client.status()["state"] == "cooling_down"
        with pytest.raises(LlmUnavailable, match="cooldown"):
            asyncio.run(client.complete("instruction", "facts"))

    def test_a_timeout_is_reported_as_unavailable(self, monkeypatch):
        import httpx

        class Boom:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **k): raise httpx.ConnectTimeout("too slow")

        monkeypatch.setattr("fsip_common.llm.httpx.AsyncClient", lambda **kw: Boom())
        client = GeminiClient(api_key="k", timeout_s=0.5)
        with pytest.raises(LlmUnavailable, match="timed out"):
            asyncio.run(client.complete("instruction", "facts"))

    def test_an_empty_candidate_is_not_served_as_an_answer(self, monkeypatch):
        class Resp:
            status_code = 200
            def raise_for_status(self): return None
            def json(self): return {"candidates": [{"content": {"parts": [{"text": ""}]}}]}

        class Ok:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **k): return Resp()

        monkeypatch.setattr("fsip_common.llm.httpx.AsyncClient", lambda **kw: Ok())
        with pytest.raises(LlmUnavailable):
            asyncio.run(GeminiClient(api_key="k").complete("instruction", "facts"))

    def test_an_overloaded_primary_model_falls_through_to_the_spare(self, monkeypatch):
        """We measured a live 503 on the pinned model. One model name must not be able to kill the feature."""
        seen: list[str] = []

        class Resp:
            def __init__(self, status, text=""):
                self.status_code = status
                self._t = text
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": self._t}]}}]}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, **k):
                model = url.rsplit("/", 1)[-1].split(":")[0]
                seen.append(model)
                return Resp(503) if model == "primary" else Resp(200, "Topping up Mirpur.")

        monkeypatch.setattr("fsip_common.llm.httpx.AsyncClient", lambda **kw: Client())
        client = GeminiClient(api_key="k", model="primary", spare_model="spare")
        text, _ = asyncio.run(client.complete("instruction", "facts"))
        assert seen == ["primary", "spare"]
        assert text == "Topping up Mirpur."
        assert client.status()["detail"] == "answered last by spare"

    def test_a_rejected_key_is_not_retried_on_the_spare(self, monkeypatch):
        """403 means our credentials are wrong; asking a second model wastes an operator's seconds."""
        seen: list[str] = []

        class Resp:
            status_code = 403
            def json(self): return {}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, **k):
                seen.append(url.rsplit("/", 1)[-1].split(":")[0])
                return Resp()

        monkeypatch.setattr("fsip_common.llm.httpx.AsyncClient", lambda **kw: Client())
        client = GeminiClient(api_key="bad", model="primary", spare_model="spare")
        with pytest.raises(LlmUnavailable, match="auth_error"):
            asyncio.run(client.complete("instruction", "facts"))
        assert seen == ["primary"]

    def test_the_api_key_never_appears_in_the_raised_message(self, monkeypatch):
        secret = "AQ.super-secret-key-value"

        class Ok:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **k): raise RuntimeError(f"failed calling ...?key={secret}")

        monkeypatch.setattr("fsip_common.llm.httpx.AsyncClient", lambda **kw: Ok())
        client = GeminiClient(api_key=secret)
        with pytest.raises(LlmUnavailable) as err:
            asyncio.run(client.complete("instruction", "facts"))
        assert secret not in str(err.value)


class TestAnswerAssembly:
    """Regression: a real answer arrived split across parts and we showed only the first one."""

    def test_an_answer_split_across_parts_is_joined(self, monkeypatch):
        body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "The redis component went down because it was unreachable and has now recovered. The"},
            {"text": " platform kept serving from the engine throughout."},
        ]}}]}
        assert _answer_text(body)[0].endswith("from the engine throughout.")

    def test_reasoning_parts_are_not_shown_to_the_operator(self):
        body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "Let me think about the litres...", "thought": True},
            {"text": "Autopilot topped up Mirpur."},
        ]}}]}
        assert _answer_text(body)[0] == "Autopilot topped up Mirpur."

    def test_a_single_part_still_works(self):
        body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "One sentence."}]}}]}
        assert _answer_text(body) == ("One sentence.", "STOP")

    def test_the_finish_reason_survives_the_join(self):
        body = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [
            {"text": "Half a "}, {"text": "sentence"}]}}]}
        assert _answer_text(body) == ("Half a sentence", "MAX_TOKENS")

    def test_an_answer_the_provider_cut_short_is_refused(self, monkeypatch):
        class Resp:
            status_code = 200
            def json(self):
                return {"candidates": [{"finishReason": "MAX_TOKENS",
                                        "content": {"parts": [{"text": "Autopilot sent 1130 L and then"}]}}]}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **k): return Resp()

        monkeypatch.setattr("fsip_common.llm.httpx.AsyncClient", lambda **kw: Client())
        with pytest.raises(LlmUnavailable, match="stopped early"):
            asyncio.run(GeminiClient(api_key="k", model="m", spare_model="").complete("i", "f"))
