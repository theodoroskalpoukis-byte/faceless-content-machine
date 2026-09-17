from faceless_machine.publishers import TikTokPublisher


def test_tiktok_chunk_plan_small_file() -> None:
    assert TikTokPublisher._chunk_plan(10_000_000) == (10_000_000, 1)


def test_tiktok_chunk_plan_large_file() -> None:
    size = 130 * 1024 * 1024
    chunk, count = TikTokPublisher._chunk_plan(size)
    assert TikTokPublisher.MIN_CHUNK <= chunk <= TikTokPublisher.MAX_CHUNK
    assert count == 3
    assert chunk * count >= size
