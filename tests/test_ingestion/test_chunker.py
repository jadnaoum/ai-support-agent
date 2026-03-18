import tiktoken
import pytest
from backend.ingestion.chunker import chunk_text

_enc = tiktoken.get_encoding("cl100k_base")


def token_count(text: str) -> int:
    return len(_enc.encode(text))


def make_paragraphs(n: int, words_each: int = 60) -> str:
    """Generate n paragraphs, each roughly `words_each` words long."""
    para = " ".join(["word"] * words_each)
    return "\n\n".join([para] * n)


def test_empty_string_returns_empty_list():
    assert chunk_text("") == []


def test_whitespace_only_input_returns_empty_list():
    assert chunk_text("   \n\n  \n\n   ") == []


def test_single_short_paragraph_returns_one_chunk():
    result = chunk_text("This is a short paragraph.")
    assert len(result) == 1
    assert result[0] == "This is a short paragraph."


def test_default_parameters_work():
    result = chunk_text("Hello world.")
    assert isinstance(result, list)
    assert len(result) >= 1


def test_chunk_count_scales_with_length():
    # 10 paragraphs of ~60 words each ≈ 600 tokens total; max_tokens=200 → expect multiple chunks
    text = make_paragraphs(10, words_each=60)
    result = chunk_text(text, max_tokens=200, overlap_tokens=20)
    assert len(result) > 1


def test_no_chunk_exceeds_max_tokens_significantly():
    text = make_paragraphs(20, words_each=50)
    max_tokens = 300
    result = chunk_text(text, max_tokens=max_tokens, overlap_tokens=30)
    for chunk in result:
        # Allow 1.5x headroom for the single-oversized-paragraph edge case
        assert token_count(chunk) <= max_tokens * 1.5, (
            f"Chunk of {token_count(chunk)} tokens exceeds {max_tokens * 1.5}"
        )


def test_chunks_have_overlap():
    # Use 6 distinct paragraphs; with overlap the last paragraph(s) of chunk N
    # should appear at the start of chunk N+1.
    paragraphs = [f"Paragraph number {i} contains unique text about topic {i}." for i in range(6)]
    text = "\n\n".join(paragraphs)
    result = chunk_text(text, max_tokens=60, overlap_tokens=15)

    if len(result) < 2:
        pytest.skip("Not enough chunks to test overlap with these parameters")

    # The last paragraph of chunk 0 should appear somewhere in chunk 1
    last_para_of_chunk0 = result[0].split("\n\n")[-1]
    assert last_para_of_chunk0 in result[1], (
        "Expected overlap: last paragraph of chunk 0 should appear in chunk 1"
    )


def test_single_oversized_paragraph_kept_as_one_chunk():
    # A single paragraph that is much longer than max_tokens should be returned as-is
    long_para = " ".join(["word"] * 300)  # ~300 tokens
    result = chunk_text(long_para, max_tokens=100, overlap_tokens=20)
    assert len(result) == 1
    assert result[0] == long_para


def test_all_content_is_preserved():
    paragraphs = [f"Unique paragraph {i}." for i in range(8)]
    text = "\n\n".join(paragraphs)
    result = chunk_text(text, max_tokens=50, overlap_tokens=10)

    # Every paragraph should appear in at least one chunk
    all_chunks_joined = "\n\n".join(result)
    for para in paragraphs:
        assert para in all_chunks_joined, f"Paragraph '{para}' missing from output"
