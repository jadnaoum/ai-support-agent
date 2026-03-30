import tiktoken

# cl100k_base is the encoding used by text-embedding-3-small and most modern models
_enc = tiktoken.get_encoding("cl100k_base")


def chunk_text(
    text: str,
    max_tokens: int = 400,
    overlap_tokens: int = 50,
    split_on_headings: bool = False,
) -> list[str]:
    """Split text into chunks for embedding.

    Args:
        text: Raw document text (markdown or plain).
        max_tokens: Soft upper bound on tokens per chunk (default 400).
            Ignored when split_on_headings=True.
        overlap_tokens: Tokens of context to carry over between chunks (default 50).
            Ignored when split_on_headings=True.
        split_on_headings: If True, emit one chunk per H2 section (## heading).
            Use for lookup-table articles where each section is an independent topic
            and diluting the embedding across sections would hurt retrieval precision.
            The H1 title is prepended to each chunk for context.

    Returns:
        List of chunk strings. Empty input returns [].
    """
    if not text or not text.strip():
        return []

    # --- Heading-aware mode ---
    if split_on_headings:
        lines = text.splitlines()
        title = ""
        for line in lines:
            if line.startswith("# ") and not line.startswith("## "):
                title = line.strip()
                break

        chunks: list[str] = []
        current: list[str] = []

        for line in lines:
            if line.startswith("## ") and current:
                chunk = "\n".join(current).strip()
                if chunk:
                    chunks.append(chunk)
                current = [title, line] if title else [line]
            else:
                current.append(line)

        if current:
            chunk = "\n".join(current).strip()
            if chunk:
                chunks.append(chunk)

        return [c for c in chunks if c]

    # --- Token-bounded mode (default) ---
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    token_chunks: list[str] = []
    buffer: list[str] = []
    buffer_tokens: int = 0

    for para in paragraphs:
        para_tokens = len(_enc.encode(para))

        # If adding this paragraph would exceed the limit AND the buffer is non-empty,
        # finalize the current chunk and start a new one with overlap.
        if buffer and buffer_tokens + para_tokens > max_tokens:
            token_chunks.append("\n\n".join(buffer))

            # Build overlap seed: backfill paragraphs from the end of the buffer
            # until their combined token count reaches overlap_tokens.
            overlap_buffer: list[str] = []
            overlap_count: int = 0
            for prev_para in reversed(buffer):
                prev_tokens = len(_enc.encode(prev_para))
                if overlap_count + prev_tokens > overlap_tokens:
                    break
                overlap_buffer.insert(0, prev_para)
                overlap_count += prev_tokens

            buffer = overlap_buffer
            buffer_tokens = overlap_count

        buffer.append(para)
        buffer_tokens += para_tokens

    # Flush the final buffer
    if buffer:
        token_chunks.append("\n\n".join(buffer))

    return token_chunks
