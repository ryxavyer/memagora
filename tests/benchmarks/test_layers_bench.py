"""
Memory stack (layers.py) benchmarks.

Tests MemoryStack.wake_up() and Layer2/L3 at scale.
"""

import time

import pytest

from tests.benchmarks.data_generator import PalaceDataGenerator
from tests.benchmarks.report import record_metric


@pytest.mark.benchmark
class TestWakeUpCost:
    """Measure wake_up() time (L0 only) at different palace sizes."""

    SIZES = [500, 1_000, 2_500, 5_000]

    @pytest.mark.parametrize("n_drawers", SIZES)
    def test_wakeup_latency(self, n_drawers, tmp_path, bench_scale):
        """L0 generation is constant-time (file read), independent of palace size."""
        gen = PalaceDataGenerator(seed=42, scale=bench_scale)
        palace_path = str(tmp_path / "palace")
        gen.populate_palace_directly(palace_path, n_drawers=n_drawers, include_needles=False)

        # Create identity file
        identity_path = str(tmp_path / "identity.txt")
        with open(identity_path, "w") as f:
            f.write("I am a test AI. Traits: precise, fast.\n")

        from mempalace.layers import MemoryStack

        stack = MemoryStack(palace_path=palace_path, identity_path=identity_path)

        latencies = []
        for _ in range(5):
            start = time.perf_counter()
            text = stack.wake_up()
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)
            assert "L0" in text or "IDENTITY" in text

        avg_ms = sum(latencies) / len(latencies)
        record_metric("layers_wakeup", f"avg_ms_at_{n_drawers}", round(avg_ms, 1))


@pytest.mark.benchmark
class TestWakeUpTokenBudget:
    """Verify L0 stays within token budget regardless of palace size."""

    SIZES = [500, 1_000, 2_500, 5_000]

    @pytest.mark.parametrize("n_drawers", SIZES)
    def test_token_budget(self, n_drawers, tmp_path):
        """L0-only wake-up should be ~100 tokens regardless of palace size."""
        gen = PalaceDataGenerator(seed=42, scale="small")
        palace_path = str(tmp_path / "palace")
        gen.populate_palace_directly(palace_path, n_drawers=n_drawers, include_needles=False)

        identity_path = str(tmp_path / "identity.txt")
        with open(identity_path, "w") as f:
            f.write("I am a benchmark AI.\n")

        from mempalace.layers import MemoryStack

        stack = MemoryStack(palace_path=palace_path, identity_path=identity_path)
        text = stack.wake_up()
        token_estimate = len(text) // 4

        record_metric("wakeup_budget", f"tokens_at_{n_drawers}", token_estimate)
        record_metric("wakeup_budget", f"chars_at_{n_drawers}", len(text))

        # L0 is identity-file content; budget bounded by file size, not palace size.
        assert token_estimate < 500, (
            f"Wake-up exceeded budget: ~{token_estimate} tokens at {n_drawers} drawers"
        )


@pytest.mark.benchmark
class TestLayer2Retrieval:
    """Layer2 on-demand retrieval with filters."""

    def test_layer2_latency(self, tmp_path, bench_scale):
        """L2 retrieval with wing filter at scale."""
        gen = PalaceDataGenerator(seed=42, scale=bench_scale)
        palace_path = str(tmp_path / "palace")
        gen.populate_palace_directly(palace_path, n_drawers=2_000, include_needles=False)

        from mempalace.layers import Layer2

        layer = Layer2(palace_path=palace_path)
        wing = gen.wings[0]

        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            layer.retrieve(wing=wing, n_results=10)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

        avg_ms = sum(latencies) / len(latencies)
        record_metric("layer2", "avg_retrieval_ms", round(avg_ms, 1))


@pytest.mark.benchmark
class TestLayer3Search:
    """Layer3 semantic search through the MemoryStack interface."""

    def test_layer3_latency(self, tmp_path, bench_scale):
        """L3 search latency through MemoryStack."""
        gen = PalaceDataGenerator(seed=42, scale=bench_scale)
        palace_path = str(tmp_path / "palace")
        gen.populate_palace_directly(palace_path, n_drawers=2_000, include_needles=False)

        identity_path = str(tmp_path / "identity.txt")
        with open(identity_path, "w") as f:
            f.write("I am a benchmark AI.\n")

        from mempalace.layers import MemoryStack

        stack = MemoryStack(palace_path=palace_path, identity_path=identity_path)

        queries = ["authentication", "database", "deployment", "testing", "monitoring"]
        latencies = []
        for q in queries:
            start = time.perf_counter()
            stack.search(q, n_results=5)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)

        avg_ms = sum(latencies) / len(latencies)
        record_metric("layer3", "avg_search_ms", round(avg_ms, 1))
