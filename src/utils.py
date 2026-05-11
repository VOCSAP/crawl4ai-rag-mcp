"""
Utility functions for the Crawl4AI MCP server.
"""
import os
import concurrent.futures
from typing import List, Dict, Any, Optional, Tuple
import json
from urllib.parse import urlparse
import psycopg2
import psycopg2.extras
import openai
import re
import time

EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))


def _get_openai_client() -> openai.OpenAI:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1"
    api_key = os.getenv("OPENAI_API_KEY", "ollama")
    return openai.OpenAI(base_url=base_url, api_key=api_key)


def get_db_conn():
    """
    Return a new psycopg2 connection using DATABASE_URL from environment.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL must be set in environment variables")
    return psycopg2.connect(db_url)


def create_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Create embeddings for multiple texts in a single API call.
    """
    if not texts:
        return []

    model = os.getenv("EMBEDDING_MODEL", "nomic-embed-text:v1.5")
    client = _get_openai_client()

    max_retries = 3
    retry_delay = 1.0

    for retry in range(max_retries):
        try:
            response = client.embeddings.create(model=model, input=texts)
            return [item.embedding for item in response.data]
        except Exception as e:
            if retry < max_retries - 1:
                print(f"Error creating batch embeddings (attempt {retry + 1}/{max_retries}): {e}")
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                print(f"Failed to create batch embeddings after {max_retries} attempts: {e}")
                print("Attempting to create embeddings individually...")
                embeddings = []
                successful_count = 0

                for i, text in enumerate(texts):
                    try:
                        individual_response = client.embeddings.create(model=model, input=[text])
                        embeddings.append(individual_response.data[0].embedding)
                        successful_count += 1
                    except Exception as individual_error:
                        print(f"Failed to create embedding for text {i}: {individual_error}")
                        embeddings.append([0.0] * EMBEDDING_DIM)

                print(f"Successfully created {successful_count}/{len(texts)} embeddings individually")
                return embeddings


def create_embedding(text: str) -> List[float]:
    """
    Create an embedding for a single text.
    """
    try:
        embeddings = create_embeddings_batch([text])
        return embeddings[0] if embeddings else [0.0] * EMBEDDING_DIM
    except Exception as e:
        print(f"Error creating embedding: {e}")
        return [0.0] * EMBEDDING_DIM


def generate_contextual_embedding(full_document: str, chunk: str) -> Tuple[str, bool]:
    """
    Generate contextual information for a chunk within a document to improve retrieval.
    """
    model_choice = os.getenv("MODEL_CHOICE")

    try:
        prompt = f"""<document>
{full_document[:25000]}
</document>
Here is the chunk we want to situate within the whole document
<chunk>
{chunk}
</chunk>
Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. Answer only with the succinct context and nothing else."""

        client = _get_openai_client()
        response = client.chat.completions.create(
            model=model_choice,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides concise contextual information."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=200
        )

        context = response.choices[0].message.content.strip()
        contextual_text = f"{context}\n---\n{chunk}"

        return contextual_text, True

    except Exception as e:
        print(f"Error generating contextual embedding: {e}. Using original chunk instead.")
        return chunk, False


def process_chunk_with_context(args):
    """
    Process a single chunk with contextual embedding (for use with concurrent.futures).
    """
    url, content, full_document = args
    return generate_contextual_embedding(full_document, content)


def add_documents_to_db(
    conn,
    urls: List[str],
    chunk_numbers: List[int],
    contents: List[str],
    metadatas: List[Dict[str, Any]],
    url_to_full_document: Dict[str, str],
    batch_size: int = 20
) -> None:
    """
    Add documents to the crawled_pages table in batches.
    Deletes existing records with the same URLs before inserting.
    """
    unique_urls = list(set(urls))

    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM crawled_pages WHERE url = ANY(%s)",
                (unique_urls,)
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Batch delete failed: {e}. Trying one-by-one deletion as fallback.")
        for url in unique_urls:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM crawled_pages WHERE url = %s", (url,))
                conn.commit()
            except Exception as inner_e:
                conn.rollback()
                print(f"Error deleting record for URL {url}: {inner_e}")

    use_contextual_embeddings = os.getenv("USE_CONTEXTUAL_EMBEDDINGS", "false") == "true"
    print(f"\n\nUse contextual embeddings: {use_contextual_embeddings}\n\n")

    for i in range(0, len(contents), batch_size):
        batch_end = min(i + batch_size, len(contents))

        batch_urls = urls[i:batch_end]
        batch_chunk_numbers = chunk_numbers[i:batch_end]
        batch_contents = contents[i:batch_end]
        batch_metadatas = metadatas[i:batch_end]

        if use_contextual_embeddings:
            process_args = []
            for j, content in enumerate(batch_contents):
                url = batch_urls[j]
                full_document = url_to_full_document.get(url, "")
                process_args.append((url, content, full_document))

            contextual_contents = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_idx = {executor.submit(process_chunk_with_context, arg): idx
                                 for idx, arg in enumerate(process_args)}

                for future in concurrent.futures.as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        result, success = future.result()
                        contextual_contents.append(result)
                        if success:
                            batch_metadatas[idx]["contextual_embedding"] = True
                    except Exception as e:
                        print(f"Error processing chunk {idx}: {e}")
                        contextual_contents.append(batch_contents[idx])

            if len(contextual_contents) != len(batch_contents):
                print(f"Warning: Expected {len(batch_contents)} results but got {len(contextual_contents)}")
                contextual_contents = batch_contents
        else:
            contextual_contents = batch_contents

        batch_embeddings = create_embeddings_batch(contextual_contents)

        batch_data = []
        for j in range(len(contextual_contents)):
            chunk_size = len(contextual_contents[j])
            parsed_url = urlparse(batch_urls[j])
            source_id = parsed_url.netloc or parsed_url.path

            batch_data.append((
                batch_urls[j],
                batch_chunk_numbers[j],
                contextual_contents[j],
                json.dumps({"chunk_size": chunk_size, **batch_metadatas[j]}),
                source_id,
                batch_embeddings[j],
            ))

        max_retries = 3
        retry_delay = 1.0

        for retry in range(max_retries):
            try:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        INSERT INTO crawled_pages (url, chunk_number, content, metadata, source_id, embedding)
                        VALUES %s
                        ON CONFLICT (url, chunk_number) DO UPDATE
                          SET content = EXCLUDED.content,
                              metadata = EXCLUDED.metadata,
                              embedding = EXCLUDED.embedding
                        """,
                        batch_data,
                        template="(%s, %s, %s, %s::jsonb, %s, %s::vector)"
                    )
                conn.commit()
                break
            except Exception as e:
                conn.rollback()
                if retry < max_retries - 1:
                    print(f"Error inserting batch (attempt {retry + 1}/{max_retries}): {e}")
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    print(f"Failed to insert batch after {max_retries} attempts: {e}")
                    print("Attempting to insert records individually...")
                    successful_inserts = 0
                    for record in batch_data:
                        try:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    INSERT INTO crawled_pages (url, chunk_number, content, metadata, source_id, embedding)
                                    VALUES (%s, %s, %s, %s::jsonb, %s, %s::vector)
                                    ON CONFLICT (url, chunk_number) DO UPDATE
                                      SET content = EXCLUDED.content,
                                          metadata = EXCLUDED.metadata,
                                          embedding = EXCLUDED.embedding
                                    """,
                                    record
                                )
                            conn.commit()
                            successful_inserts += 1
                        except Exception as individual_error:
                            conn.rollback()
                            print(f"Failed to insert individual record for URL {record[0]}: {individual_error}")

                    if successful_inserts > 0:
                        print(f"Successfully inserted {successful_inserts}/{len(batch_data)} records individually")


def search_documents(
    conn,
    query: str,
    match_count: int = 10,
    filter_metadata: Optional[Dict[str, Any]] = None,
    source_id_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search for documents using vector similarity via match_crawled_pages stored function.
    """
    import threading

    timeout_event = threading.Event()

    def set_timeout():
        timeout_event.set()

    timer = threading.Timer(30.0, set_timeout)
    timer.start()

    try:
        print(f"[DEBUG] Creating embedding for query: '{query[:50]}...'")
        query_embedding = create_embedding(query)

        if not query_embedding or all(v == 0.0 for v in query_embedding):
            print("[ERROR] Failed to create valid embedding")
            return []

        if timeout_event.is_set():
            raise TimeoutError("Embedding creation timed out")

        print("[DEBUG] Executing vector search in database...")

        filter_json = json.dumps(filter_metadata) if filter_metadata else "{}"
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM match_crawled_pages(%s::vector, %s, %s::jsonb, %s)",
                (embedding_str, match_count * 3 if source_id_filter else match_count,
                 filter_json, source_id_filter)
            )
            rows = cur.fetchall()

        if timeout_event.is_set():
            raise TimeoutError("Vector search timed out")

        results = [dict(r) for r in rows]

        if results:
            print(f"[DEBUG] Vector search returned {len(results)} results before filtering")

            if source_id_filter:
                filtered = [r for r in results if r.get("source_id") == source_id_filter]
                print(f"[SUCCESS] Vector search completed: {len(filtered)} results after source filtering")
                return filtered[:match_count]
            else:
                print(f"[SUCCESS] Vector search completed: {len(results)} results")
                return results[:match_count]
        else:
            print("[WARNING] Vector search returned no results")
            return []

    except TimeoutError as e:
        print(f"[ERROR] Vector search timed out: {e}")
        return []
    except Exception as e:
        print(f"[ERROR] Error searching documents: {e}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return []
    finally:
        timer.cancel()


def extract_code_blocks(markdown_content: str, min_length: int = 1000) -> List[Dict[str, Any]]:
    """
    Extract code blocks from markdown content along with context.
    """
    code_blocks = []

    content = markdown_content.strip()
    start_offset = 0
    if content.startswith('```'):
        start_offset = 3
        print("Skipping initial triple backticks")

    backtick_positions = []
    pos = start_offset
    while True:
        pos = markdown_content.find('```', pos)
        if pos == -1:
            break
        backtick_positions.append(pos)
        pos += 3

    i = 0
    while i < len(backtick_positions) - 1:
        start_pos = backtick_positions[i]
        end_pos = backtick_positions[i + 1]

        code_section = markdown_content[start_pos+3:end_pos]

        lines = code_section.split('\n', 1)
        if len(lines) > 1:
            first_line = lines[0].strip()
            if first_line and ' ' not in first_line and len(first_line) < 20:
                language = first_line
                code_content = lines[1].strip() if len(lines) > 1 else ""
            else:
                language = ""
                code_content = code_section.strip()
        else:
            language = ""
            code_content = code_section.strip()

        if len(code_content) < min_length:
            i += 2
            continue

        context_start = max(0, start_pos - 1000)
        context_before = markdown_content[context_start:start_pos].strip()

        context_end = min(len(markdown_content), end_pos + 3 + 1000)
        context_after = markdown_content[end_pos + 3:context_end].strip()

        code_blocks.append({
            'code': code_content,
            'language': language,
            'context_before': context_before,
            'context_after': context_after,
            'full_context': f"{context_before}\n\n{code_content}\n\n{context_after}"
        })

        i += 2

    return code_blocks


def generate_code_example_summary(code: str, context_before: str, context_after: str) -> str:
    """
    Generate a summary for a code example using its surrounding context.
    """
    model_choice = os.getenv("MODEL_CHOICE")

    prompt = f"""<context_before>
{context_before[-500:] if len(context_before) > 500 else context_before}
</context_before>

<code_example>
{code[:1500] if len(code) > 1500 else code}
</code_example>

<context_after>
{context_after[:500] if len(context_after) > 500 else context_after}
</context_after>

Based on the code example and its surrounding context, provide a concise summary (2-3 sentences) that describes what this code example demonstrates and its purpose. Focus on the practical application and key concepts illustrated.
"""

    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=model_choice,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides concise code example summaries."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2000
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"Error generating code example summary: {e}")
        return "Code example for demonstration purposes."


def add_code_examples_to_db(
    conn,
    urls: List[str],
    chunk_numbers: List[int],
    code_examples: List[str],
    summaries: List[str],
    metadatas: List[Dict[str, Any]],
    batch_size: int = 20
):
    """
    Add code examples to the code_examples table in batches.
    """
    if not urls:
        return

    unique_urls = list(set(urls))
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM code_examples WHERE url = ANY(%s)",
                (unique_urls,)
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error deleting existing code examples: {e}")

    total_items = len(urls)
    for i in range(0, total_items, batch_size):
        batch_end = min(i + batch_size, total_items)
        batch_texts = []

        for j in range(i, batch_end):
            combined_text = f"{code_examples[j]}\n\nSummary: {summaries[j]}"
            batch_texts.append(combined_text)

        embeddings = create_embeddings_batch(batch_texts)

        valid_embeddings = []
        for embedding in embeddings:
            if embedding and not all(v == 0.0 for v in embedding):
                valid_embeddings.append(embedding)
            else:
                print("Warning: Zero or invalid embedding detected, creating new one...")
                single_embedding = create_embedding(batch_texts[len(valid_embeddings)])
                valid_embeddings.append(single_embedding)

        batch_data = []
        for j, embedding in enumerate(valid_embeddings):
            idx = i + j
            parsed_url = urlparse(urls[idx])
            source_id = parsed_url.netloc or parsed_url.path

            batch_data.append((
                urls[idx],
                chunk_numbers[idx],
                code_examples[idx],
                summaries[idx],
                json.dumps(metadatas[idx]),
                source_id,
                embedding,
            ))

        max_retries = 3
        retry_delay = 1.0

        for retry in range(max_retries):
            try:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        INSERT INTO code_examples (url, chunk_number, content, summary, metadata, source_id, embedding)
                        VALUES %s
                        ON CONFLICT (url, chunk_number) DO UPDATE
                          SET content = EXCLUDED.content,
                              summary = EXCLUDED.summary,
                              metadata = EXCLUDED.metadata,
                              embedding = EXCLUDED.embedding
                        """,
                        batch_data,
                        template="(%s, %s, %s, %s, %s::jsonb, %s, %s::vector)"
                    )
                conn.commit()
                break
            except Exception as e:
                conn.rollback()
                if retry < max_retries - 1:
                    print(f"Error inserting code examples batch (attempt {retry + 1}/{max_retries}): {e}")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    print(f"Failed to insert code examples batch after {max_retries} attempts: {e}")
                    successful_inserts = 0
                    for record in batch_data:
                        try:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    INSERT INTO code_examples (url, chunk_number, content, summary, metadata, source_id, embedding)
                                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::vector)
                                    ON CONFLICT (url, chunk_number) DO UPDATE
                                      SET content = EXCLUDED.content,
                                          summary = EXCLUDED.summary,
                                          metadata = EXCLUDED.metadata,
                                          embedding = EXCLUDED.embedding
                                    """,
                                    record
                                )
                            conn.commit()
                            successful_inserts += 1
                        except Exception as individual_error:
                            conn.rollback()
                            print(f"Failed to insert individual code example for URL {record[0]}: {individual_error}")

                    if successful_inserts > 0:
                        print(f"Successfully inserted {successful_inserts}/{len(batch_data)} code examples individually")

        print(f"Inserted batch {i//batch_size + 1} of {(total_items + batch_size - 1)//batch_size} code examples")


def update_source_info(conn, source_id: str, summary: str, word_count: int):
    """
    Upsert source information in the sources table.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sources (source_id, summary, total_word_count)
                VALUES (%s, %s, %s)
                ON CONFLICT (source_id) DO UPDATE
                  SET summary = EXCLUDED.summary,
                      total_word_count = EXCLUDED.total_word_count,
                      updated_at = now()
                """,
                (source_id, summary, word_count)
            )
        conn.commit()
        print(f"Upserted source: {source_id}")
    except Exception as e:
        conn.rollback()
        print(f"Error updating source {source_id}: {e}")


def extract_source_summary(source_id: str, content: str, max_length: int = 500) -> str:
    """
    Extract a summary for a source from its content using an LLM.
    """
    default_summary = f"Content from {source_id}"

    if not content or len(content.strip()) == 0:
        return default_summary

    model_choice = os.getenv("MODEL_CHOICE")
    truncated_content = content[:25000] if len(content) > 25000 else content

    prompt = f"""<source_content>
{truncated_content}
</source_content>

The above content is from the documentation for '{source_id}'. Please provide a concise summary (3-5 sentences) that describes what this library/tool/framework is about. The summary should help understand what the library/tool/framework accomplishes and the purpose.
"""

    try:
        client = _get_openai_client()
        response = client.chat.completions.create(
            model=model_choice,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides concise library/tool/framework summaries."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=150
        )

        summary = response.choices[0].message.content.strip()

        if len(summary) > max_length:
            summary = summary[:max_length] + "..."

        return summary

    except Exception as e:
        print(f"Error generating summary with LLM for {source_id}: {e}. Using default summary.")
        return default_summary


def search_code_examples(
    conn,
    query: str,
    match_count: int = 10,
    filter_metadata: Optional[Dict[str, Any]] = None,
    source_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search for code examples using vector similarity via match_code_examples stored function.
    """
    import threading

    timeout_event = threading.Event()

    def set_timeout():
        timeout_event.set()

    timer = threading.Timer(25.0, set_timeout)
    timer.start()

    try:
        print(f"[DEBUG] Creating enhanced embedding for code query: '{query[:50]}...'")
        enhanced_query = f"Code example for {query}\n\nSummary: Example code showing {query}"
        query_embedding = create_embedding(enhanced_query)

        if not query_embedding or all(v == 0.0 for v in query_embedding):
            print("[ERROR] Failed to create valid embedding for code search")
            return []

        if timeout_event.is_set():
            raise TimeoutError("Embedding creation timed out")

        print("[DEBUG] Executing code example search in database...")

        filter_json = json.dumps(filter_metadata) if filter_metadata else "{}"
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM match_code_examples(%s::vector, %s, %s::jsonb, %s)",
                (embedding_str, match_count * 3 if source_id else match_count,
                 filter_json, source_id)
            )
            rows = cur.fetchall()

        if timeout_event.is_set():
            raise TimeoutError("Code search timed out")

        results = [dict(r) for r in rows]

        if results:
            print(f"[DEBUG] Code example search returned {len(results)} results before filtering")

            if source_id:
                filtered = [r for r in results if r.get("source_id") == source_id]
                print(f"[SUCCESS] Code example search completed: {len(filtered)} results after source filtering")
                return filtered[:match_count]
            else:
                print(f"[SUCCESS] Code example search completed: {len(results)} results")
                return results[:match_count]
        else:
            print("[WARNING] Code example search returned no results")
            return []

    except TimeoutError as e:
        print(f"[ERROR] Code example search timed out: {e}")
        return []
    except Exception as e:
        print(f"[ERROR] Error searching code examples: {e}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return []
    finally:
        timer.cancel()


# ---------------------------------------------------------------------------
# Direct DB query helpers (replace inline supabase calls in crawl4ai_mcp.py)
# ---------------------------------------------------------------------------

def get_raw_content_by_url(conn, url: str) -> List[str]:
    """
    Return all content chunks for a given URL, ordered by chunk_number.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM crawled_pages WHERE url = %s ORDER BY chunk_number",
                (url,)
            )
            rows = cur.fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"Error fetching content for URL {url}: {e}")
        return []


def get_all_sources(conn) -> List[Dict[str, Any]]:
    """
    Return all sources ordered by source_id.
    """
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM sources ORDER BY source_id")
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error fetching sources: {e}")
        return []


def keyword_search_crawled_pages(
    conn,
    query: str,
    source: Optional[str],
    limit: int
) -> List[Dict[str, Any]]:
    """
    Full-text keyword search on crawled_pages.content using ILIKE.
    """
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if source:
                cur.execute(
                    """
                    SELECT id, url, chunk_number, content, metadata, source_id
                    FROM crawled_pages
                    WHERE content ILIKE %s AND source_id = %s
                    LIMIT %s
                    """,
                    (f"%{query}%", source, limit)
                )
            else:
                cur.execute(
                    """
                    SELECT id, url, chunk_number, content, metadata, source_id
                    FROM crawled_pages
                    WHERE content ILIKE %s
                    LIMIT %s
                    """,
                    (f"%{query}%", limit)
                )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error in keyword search (crawled_pages): {e}")
        return []


def keyword_search_code_examples(
    conn,
    query: str,
    source_id: Optional[str],
    limit: int
) -> List[Dict[str, Any]]:
    """
    Full-text keyword search on code_examples.content OR summary using ILIKE.
    """
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if source_id:
                cur.execute(
                    """
                    SELECT id, url, chunk_number, content, summary, metadata, source_id
                    FROM code_examples
                    WHERE (content ILIKE %s OR summary ILIKE %s) AND source_id = %s
                    LIMIT %s
                    """,
                    (f"%{query}%", f"%{query}%", source_id, limit)
                )
            else:
                cur.execute(
                    """
                    SELECT id, url, chunk_number, content, summary, metadata, source_id
                    FROM code_examples
                    WHERE content ILIKE %s OR summary ILIKE %s
                    LIMIT %s
                    """,
                    (f"%{query}%", f"%{query}%", limit)
                )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"Error in keyword search (code_examples): {e}")
        return []
