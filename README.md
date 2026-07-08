<h1 align="left">🐳 Crawl4AI+SearXNG MCP Server</h1>

<em>Web Crawling, Search and RAG Capabilities for AI Agents and AI Coding Assistants</em>

> **(FORKED FROM https://github.com/coleam00/mcp-crawl4ai-rag). Added SearXNG integration and batch scrape and processing capabilities.**

A **self-contained Docker solution** that combines the [Model Context Protocol (MCP)](https://modelcontextprotocol.io), [Crawl4AI](https://crawl4ai.com) (v0.9.0), [SearXNG](https://github.com/searxng/searxng), and a bundled **Postgres + pgvector** vector store to provide AI agents and coding assistants with complete web **search, crawling, and RAG capabilities**. This fork runs 100% locally: embeddings and LLM calls target an OpenAI-compatible endpoint (default: a local [Ollama](https://ollama.com/) instance), with no external cloud service required.

**🚀 Complete Stack in One Command**: Deploy everything with `docker compose up -d` - no Python setup, no dependencies, no external services required.

### 🎯 Smart RAG vs Traditional Scraping

Unlike traditional scraping (such as [Firecrawl](https://github.com/mendableai/firecrawl-mcp-server)) that dumps raw content and overwhelms LLM context windows, this solution uses **intelligent RAG (Retrieval Augmented Generation)** to:

- **🔍 Extract only relevant content** using semantic similarity search
- **⚡ Prevent context overflow** by returning focused, pertinent information
- **🧠 Enhance AI responses** with precisely targeted knowledge
- **📊 Maintain context efficiency** for better LLM performance

**Flexible Output Options:**
- **RAG Mode** (default): Returns semantically relevant chunks with similarity scores
- **Raw Markdown Mode**: Full content extraction when complete context is needed
- **Hybrid Search**: Combines semantic and keyword search for comprehensive results

## 💡 Key Benefits

- **🔧 Zero Configuration**: Pre-configured SearXNG instance included
- **🐳 Docker-Only**: No Python environment setup required
- **🔍 Integrated Search**: Built-in SearXNG for private, fast search
- **⚡ Production Ready**: HTTPS, security, and monitoring included
- **🎯 AI-Optimized**: RAG strategies built for coding assistants

## Overview

This Docker-based MCP server provides a complete web intelligence stack that enables AI agents to:
- **Search the web** using the integrated SearXNG instance
- **Crawl and scrape** websites with advanced content extraction
- **Store content** in vector databases with intelligent chunking
- **Perform RAG queries** with multiple enhancement strategies

**Advanced RAG Strategies Available:**
- **Contextual Embeddings** for enriched semantic understanding
- **Hybrid Search** combining vector and keyword search
- **Agentic RAG** for specialized code example extraction
- **Reranking** for improved result relevance using cross-encoder models
- **Knowledge Graph** for AI hallucination detection and repository code analysis

See the [Configuration section](#configuration) below for details on how to enable and configure these strategies.

## Features

- **Smart URL Detection**: Automatically detects and handles different URL types (regular webpages, sitemaps, text files)
- **Recursive Crawling**: Follows internal links to discover content
- **Parallel Processing**: Efficiently crawls multiple pages simultaneously
- **Content Chunking**: Intelligently splits content by headers and size for better processing
- **Vector Search**: Performs RAG over crawled content, optionally filtering by data source for precision
- **Source Retrieval**: Retrieve sources available for filtering to guide the RAG process

## Tools

The server provides essential web crawling and search tools:

### Core Tools (Always Available)

1. **`scrape_urls`**: Scrape one or more URLs and store their content in the vector database. Supports both single URLs and lists of URLs for batch processing.
2. **`smart_crawl_url`**: Intelligently crawl a full website based on the type of URL provided (sitemap, llms-full.txt, or a regular webpage that needs to be crawled recursively)
3. **`get_available_sources`**: Get a list of all available sources (domains) in the database
4. **`perform_rag_query`**: Search for relevant content using semantic search with optional source filtering
5. **NEW!** **`search`**: Comprehensive web search tool that integrates SearXNG search with automated scraping and RAG processing. Performs a complete workflow: (1) searches SearXNG with the provided query, (2) extracts URLs from search results, (3) automatically scrapes all found URLs using existing scraping infrastructure, (4) stores content in vector database, and (5) returns either RAG-processed results organized by URL or raw markdown content. Key parameters: `query` (search terms), `return_raw_markdown` (bypasses RAG for raw content), `num_results` (search result limit), `batch_size` (database operation batching), `max_concurrent` (parallel scraping sessions). Ideal for research workflows, competitive analysis, and content discovery with built-in intelligence.

### Conditional Tools

6. **`search_code_examples`** (requires `USE_AGENTIC_RAG=true`): Search specifically for code examples and their summaries from crawled documentation. This tool provides targeted code snippet retrieval for AI coding assistants.

### Knowledge Graph Tools (requires `USE_KNOWLEDGE_GRAPH=true`, see below)

7. **`parse_github_repository`**: Parse a GitHub repository into a Neo4j knowledge graph, extracting classes, methods, functions, and their relationships for hallucination detection
8. **`check_ai_script_hallucinations`**: Analyze Python scripts for AI hallucinations by validating imports, method calls, and class usage against the knowledge graph
9. **`query_knowledge_graph`**: Explore and query the Neo4j knowledge graph with commands like `repos`, `classes`, `methods`, and custom Cypher queries

### Web Search Tools (SearXNG)

10. **`searxng_search`**: Meta-search the web via the integrated SearXNG instance (titles, URLs, snippets). No scraping or storage.
11. **`searxng_images`**: Image search via SearXNG.
12. **`searxng_news`**: News search via SearXNG.

### Browser & Extraction Tools (mirror of the native crawl4ai MCP)

13. **`capture_screenshot`**: Full-page PNG screenshot of a URL, returned as base64.
14. **`generate_pdf`**: Render a URL to PDF, returned as base64.
15. **`execute_js`**: Run one or more JavaScript snippets on a page and return the crawl result. Each snippet runs as an async function body, so use `return <value>;` to surface a value (crawl4ai 0.9.0 convention).
16. **`get_markdown`**: Convert a URL to Markdown with a selectable content filter: `fit` (readability pruning, default), `raw` (full DOM), or `bm25` (relevance filtering against a query).
17. **`generate_schema_html`**: Return sanitized, schema-ready HTML for building CSS/XPath extraction schemas.
18. **`extract_structured`**: Crawl a URL to Markdown, then extract structured JSON with the configured LLM (Ollama via `OLLAMA_BASE_URL` / `MODEL_CHOICE`). Uses the OpenAI-compatible client directly, not crawl4ai's `LLMExtractionStrategy`.

## Prerequisites

**Required:**
- [Docker and Docker Compose](https://www.docker.com/products/docker-desktop/) - This is a Docker-only solution
- An OpenAI-compatible embeddings/LLM endpoint - This fork targets a local [Ollama](https://ollama.com/) instance via `OLLAMA_BASE_URL` (default embedding model `bge-m3`). Any OpenAI-compatible endpoint works.

The vector database (Postgres + pgvector) is bundled in the Docker Compose stack, so no external database account is required.

**Optional:**
- [Neo4j instance](https://neo4j.com/) - For knowledge graph functionality (see [Knowledge Graph Setup](#knowledge-graph-setup))
- Custom domain - For production HTTPS deployment

## Installation

This is a **Docker-only solution** - no Python environment setup required!

### Quick Start

1. **Clone this repository:**
   ```bash
   git clone https://github.com/VOCSAP/crawl4ai-rag-mcp.git
   cd crawl4ai-rag-mcp
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys (see Configuration section below)
   ```

3. **Deploy the complete stack:**
   ```bash
   docker compose up -d
   ```

That's it! Your complete search, crawl, and RAG stack is now running:
- **MCP Server**: http://localhost:8051
- **SearXNG Search**: http://localhost:8080 (internal)
- **Caddy Proxy**: Handles HTTPS and routing

### What Gets Deployed

The Docker Compose stack includes:
- **MCP Crawl4AI Server** - Main application server
- **Postgres + pgvector** - Local vector database, auto-initialized from `crawled_pages.sql`
- **SearXNG** - Private search engine instance
- **Valkey** - Redis-compatible cache for SearXNG
- **Caddy** - Reverse proxy with automatic HTTPS

## Database Setup

The database (Postgres + pgvector) is bundled in the Docker Compose stack and initializes itself automatically: `crawled_pages.sql` is mounted into `/docker-entrypoint-initdb.d/` and runs on the first `docker compose up`, creating the tables and the `match_crawled_pages` / `match_code_examples` functions.

To re-apply the schema after editing `crawled_pages.sql` (for example after a vector dimension change), recreate the volume:

```bash
docker compose down -v   # DESTRUCTIVE -- wipes all indexed pages
docker compose up -d --build
```

## Knowledge Graph Setup (Optional)

To enable AI hallucination detection and repository analysis features, you need to set up Neo4j.

**Note:** The knowledge graph functionality works fully with Docker and supports all features.

### Neo4j Setup Options

**Option 1: Local AI Package (Recommended)**

The easiest way to get Neo4j running is with the [Local AI Package](https://github.com/coleam00/local-ai-packaged):

1. **Clone and start Neo4j**:
   ```bash
   git clone https://github.com/coleam00/local-ai-packaged.git
   cd local-ai-packaged
   # Follow repository instructions to start Neo4j with Docker Compose
   ```

2. **Connection details for Docker**:
   - URI: `bolt://host.docker.internal:7687` (for Docker containers)
   - URI: `bolt://localhost:7687` (for local access)
   - Username: `neo4j`
   - Password: Check Local AI Package documentation

**Option 2: Neo4j Docker**

Run Neo4j directly with Docker:

```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your-password \
  neo4j:latest
```

**Option 3: Neo4j Desktop**

Use Neo4j Desktop for a local GUI-based installation:

1. **Download and install**: [Neo4j Desktop](https://neo4j.com/download/)
2. **Create a new database** with your preferred settings
3. **Connection details**:
   - URI: `bolt://host.docker.internal:7687` (for Docker containers)
   - URI: `bolt://localhost:7687` (for local access)
   - Username: `neo4j`
   - Password: Whatever you set during database creation

## Configuration

Configure the Docker stack by editing your `.env` file (copy from `.env.example`):

```bash
# ========================================
# MCP SERVER CONFIGURATION
# ========================================
TRANSPORT=streamable-http
HOST=0.0.0.0
PORT=8051

# ========================================
# INTEGRATED SEARXNG CONFIGURATION
# ========================================
# Pre-configured for Docker Compose - SearXNG runs internally
SEARXNG_URL=http://searxng:8080
SEARXNG_USER_AGENT=MCP-Crawl4AI-RAG-Server/1.0
SEARXNG_DEFAULT_ENGINES=google,bing,duckduckgo
SEARXNG_TIMEOUT=30

# Optional: Custom domain for production HTTPS
SEARXNG_HOSTNAME=http://localhost
# SEARXNG_TLS=your-email@example.com  # For Let's Encrypt

# ========================================
# AI SERVICES CONFIGURATION (OpenAI-compatible, e.g. Ollama)
# ========================================
# Embeddings + LLM endpoint (OpenAI-compatible). This fork targets Ollama.
# Use host.docker.internal:11434 for Docker Desktop on Windows/Mac.
OLLAMA_BASE_URL=http://host.docker.internal:11434
# Dummy value for Ollama, or a real key / LiteLLM master key
OPENAI_API_KEY=ollama

# Embedding model and dimension (must match vector(N) in crawled_pages.sql)
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIMENSIONS=1024

# LLM for summaries, contextual embeddings and extract_structured (empty = disabled)
MODEL_CHOICE=

# ========================================
# VECTOR DATABASE (bundled Postgres + pgvector)
# ========================================
DATABASE_URL=postgresql://rag:rag@postgres:5432/ragdb

# ========================================
# RAG ENHANCEMENT STRATEGIES
# ========================================
USE_CONTEXTUAL_EMBEDDINGS=false
USE_HYBRID_SEARCH=false
USE_AGENTIC_RAG=false
USE_RERANKING=false
USE_KNOWLEDGE_GRAPH=false

# Optional: Neo4j for knowledge graph (if USE_KNOWLEDGE_GRAPH=true)
# Use host.docker.internal:7687 for Docker Desktop on Windows/Mac
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_neo4j_password
```

### Key Configuration Notes

**🔍 SearXNG Integration**: The stack includes a pre-configured SearXNG instance that runs automatically. No external setup required!

**🐳 Docker Networking**: The default configuration uses Docker internal networking (`http://searxng:8080`) which works out of the box.

**🔐 Production Setup**: For production, set `SEARXNG_HOSTNAME` to your domain and `SEARXNG_TLS` to your email for automatic HTTPS.

### Embedding model and vector dimension

The vector dimension is fixed at the schema level: `crawled_pages.sql` declares
`embedding vector(N)` and the `match_*` SQL functions accept `query_embedding vector(N)`
with the same `N`. The Python side reads `EMBEDDING_DIMENSIONS` from the env. **Both
must match exactly**, otherwise INSERTs fail with a pgvector dimension mismatch.

Defaults shipped in this repo:

| Variable | Value | Notes |
|---|---|---|
| `EMBEDDING_MODEL` | `bge-m3` | 1024 dims, 8192 tokens native context, multilingual. |
| `EMBEDDING_DIMENSIONS` | `1024` | Must match `vector(1024)` in `crawled_pages.sql`. |

**Migrating to a different embedder (dimension change)** requires:

1. Update `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` in `.env`.
2. Edit `crawled_pages.sql` and replace every `vector(<old>)` with `vector(<new>)`
   (four occurrences: two table columns, two function signatures).
3. Recreate the Postgres volume so the init script is re-executed:
   ```bash
   docker compose down -v   # DESTRUCTIVE -- wipes all indexed pages
   docker compose up -d --build
   ```
   `docker compose restart` is **not** enough: the init SQL is sticky to the volume,
   and `restart` does not reload the `.env`. Without `down -v` the schema stays at the
   old dimension while Python emits the new one, and every INSERT silently triggers
   the zero-embedding fallback (rows indexed with `[0.0, ..., 0.0]` -- they will never
   match any query).
4. Re-crawl your sources via `scrape_urls` / `smart_crawl_url`.

**Chunk size vs embedder context**: if your embedder has a small `n_ctx_train`
(e.g. 2048 for `nomic-embed-text:v1.5`), lower `chunk_size` on `scrape_urls`
/ `smart_crawl_url` to stay under that limit. A safe rule of thumb is
`chunk_size ≈ 3 × n_ctx_train` characters (≈ 1 token per 3-4 chars). When a chunk
exceeds the limit, the embedder returns HTTP 400 and the server falls back to a
zero vector for that chunk; you will see `[WARN] zero-embedding fallback ...`
in the container logs.

### RAG Strategy Options

The Crawl4AI RAG MCP server supports four powerful RAG strategies that can be enabled independently:

#### 1. **USE_CONTEXTUAL_EMBEDDINGS**
When enabled, this strategy enhances each chunk's embedding with additional context from the entire document. The system passes both the full document and the specific chunk to an LLM (configured via `MODEL_CHOICE`) to generate enriched context that gets embedded alongside the chunk content.

- **When to use**: Enable this when you need high-precision retrieval where context matters, such as technical documentation where terms might have different meanings in different sections.
- **Trade-offs**: Slower indexing due to LLM calls for each chunk, but significantly better retrieval accuracy.
- **Cost**: Additional LLM API calls during indexing.

**Tuning variables for local LLM deployments (Ollama):**

| Variable | Default | Description |
|---|---|---|
| `LLM_TIMEOUT` | `60` | Timeout in seconds for all LLM calls. Increase for CPU-backed Ollama instances. |
| `CONTEXTUAL_EMBEDDING_WORKERS` | `2` | Max parallel LLM calls per batch. Keep at 1-2 when Ollama processes one request at a time. |
| `CONTEXTUAL_DOC_TRUNCATION` | `8000` | Max chars of the full document sent as context. Must fit within the model context window (qwen3.5:4b = 4096 tokens; 8000 chars ~ 2000 tokens). Increase for models with larger context windows. |

#### 2. **USE_HYBRID_SEARCH**
Combines traditional keyword search with semantic vector search to provide more comprehensive results. The system performs both searches in parallel and intelligently merges results, prioritizing documents that appear in both result sets.

- **When to use**: Enable this when users might search using specific technical terms, function names, or when exact keyword matches are important alongside semantic understanding.
- **Trade-offs**: Slightly slower search queries but more robust results, especially for technical content.
- **Cost**: No additional API costs, just computational overhead.

#### 3. **USE_AGENTIC_RAG**
Enables specialized code example extraction and storage. When crawling documentation, the system identifies code blocks (≥300 characters), extracts them with surrounding context, generates summaries, and stores them in a separate vector database table specifically designed for code search.

- **When to use**: Essential for AI coding assistants that need to find specific code examples, implementation patterns, or usage examples from documentation.
- **Trade-offs**: Significantly slower crawling due to code extraction and summarization, requires more storage space.
- **Cost**: Additional LLM API calls for summarizing each code example.
- **Benefits**: Provides a dedicated `search_code_examples` tool that AI agents can use to find specific code implementations.

#### 4. **USE_RERANKING**
Applies cross-encoder reranking to search results after initial retrieval. Uses a lightweight cross-encoder model (`cross-encoder/ms-marco-MiniLM-L-6-v2`) to score each result against the original query, then reorders results by relevance.

- **When to use**: Enable this when search precision is critical and you need the most relevant results at the top. Particularly useful for complex queries where semantic similarity alone might not capture query intent.
- **Trade-offs**: Adds ~100-200ms to search queries depending on result count, but significantly improves result ordering.
- **Cost**: No additional API costs - uses a local model that runs on CPU.
- **Benefits**: Better result relevance, especially for complex queries. Works with both regular RAG search and code example search.
- **Backends** (`RERANKING_BACKEND`): `local` (default, in-process CPU CrossEncoder), `remote` (Ollama LLM, RankGPT-style listwise), or `http` (external HuggingFace TEI `/rerank` cross-encoder such as `bge-reranker-v2-m3`). The `http` backend offloads scoring to a shared LAN server via `RERANKING_HTTP_URL` and keeps no reranking model in the server process.

#### 5. **USE_KNOWLEDGE_GRAPH**
Enables AI hallucination detection and repository analysis using Neo4j knowledge graphs. When enabled, the system can parse GitHub repositories into a graph database and validate AI-generated code against real repository structures. **Fully compatible with Docker** - all functionality works within the containerized environment.

- **When to use**: Enable this for AI coding assistants that need to validate generated code against real implementations, or when you want to detect when AI models hallucinate non-existent methods, classes, or incorrect usage patterns.
- **Trade-offs**: Requires Neo4j setup and additional dependencies. Repository parsing can be slow for large codebases, and validation requires repositories to be pre-indexed.
- **Cost**: No additional API costs for validation, but requires Neo4j infrastructure (can use free local installation or cloud AuraDB).
- **Benefits**: Provides three powerful tools: `parse_github_repository` for indexing codebases, `check_ai_script_hallucinations` for validating AI-generated code, and `query_knowledge_graph` for exploring indexed repositories.

**Usage with MCP Tools:**

You can tell the AI coding assistant to add a Python GitHub repository to the knowledge graph:

"Add https://github.com/pydantic/pydantic-ai.git to the knowledge graph"

Make sure the repo URL ends with .git.

You can also have the AI coding assistant check for hallucinations with scripts it creates using the MCP `check_ai_script_hallucinations` tool.

### Recommended Configurations

**For general documentation RAG:**
```
USE_CONTEXTUAL_EMBEDDINGS=false
USE_HYBRID_SEARCH=true
USE_AGENTIC_RAG=false
USE_RERANKING=true
```

**For AI coding assistant with code examples:**
```
USE_CONTEXTUAL_EMBEDDINGS=true
USE_HYBRID_SEARCH=true
USE_AGENTIC_RAG=true
USE_RERANKING=true
USE_KNOWLEDGE_GRAPH=false
```

**For AI coding assistant with hallucination detection:**
```
USE_CONTEXTUAL_EMBEDDINGS=true
USE_HYBRID_SEARCH=true
USE_AGENTIC_RAG=true
USE_RERANKING=true
USE_KNOWLEDGE_GRAPH=true
```

**For fast, basic RAG:**
```
USE_CONTEXTUAL_EMBEDDINGS=false
USE_HYBRID_SEARCH=true
USE_AGENTIC_RAG=false
USE_RERANKING=false
USE_KNOWLEDGE_GRAPH=false
```

## Running the Server

The complete stack is managed through Docker Compose:

### Start the Stack
```bash
docker compose up -d
```

### View Logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f mcp-crawl4ai
docker compose logs -f searxng
```

### Stop the Stack
```bash
docker compose down
```

### Restart Services
```bash
# Restart all
docker compose restart

# Restart specific service
docker compose restart mcp-crawl4ai
```

The MCP server will be available at `http://localhost:8051` for SSE connections.

## Integration with MCP Clients

After starting the Docker stack with `docker compose up -d`, your MCP server will be available for integration.

### SSE Configuration (Recommended)

The Docker stack runs with Streamable HTTP transport by default (the SSE
transport is deprecated upstream). The MCP endpoint is served at `/mcp`.
Connect using:

**Claude Desktop/Windsurf:**
```json
{
  "mcpServers": {
    "crawl4ai-rag": {
      "transport": "http",
      "url": "http://localhost:8051/mcp"
    }
  }
}
```

**Windsurf (alternative syntax):**
```json
{
  "mcpServers": {
    "crawl4ai-rag": {
      "transport": "http",
      "serverUrl": "http://localhost:8051/mcp"
    }
  }
}
```

**Claude Code CLI:**
```bash
claude mcp add-json crawl4ai-rag '{"type":"http","url":"http://localhost:8051/mcp"}' --scope user
```

> Legacy SSE is still available via `TRANSPORT=sse` (endpoint `/sse`) for older
> clients, but is best-effort and no longer the default.

### Docker Networking Notes

- **Same machine**: Use `http://localhost:8051/mcp`
- **Different container**: Use `http://host.docker.internal:8051/mcp`
- **Remote access**: Replace `localhost` with your server's IP address

### Production Deployment

For production use with custom domains:

1. **Update your `.env`**:
   ```bash
   SEARXNG_HOSTNAME=https://yourdomain.com
   SEARXNG_TLS=your-email@example.com
   ```

2. **Access via HTTPS**:
   ```
   https://yourdomain.com:8051/mcp
   ```

### Health Check

Verify the server is running:
```bash
curl http://localhost:8051/health
```

## Knowledge Graph Architecture

The knowledge graph system stores repository code structure in Neo4j with the following components:

### Core Components (`knowledge_graphs/` folder):

- **`parse_repo_into_neo4j.py`**: Clones and analyzes GitHub repositories, extracting Python classes, methods, functions, and imports into Neo4j nodes and relationships
- **`ai_script_analyzer.py`**: Parses Python scripts using AST to extract imports, class instantiations, method calls, and function usage
- **`knowledge_graph_validator.py`**: Validates AI-generated code against the knowledge graph to detect hallucinations (non-existent methods, incorrect parameters, etc.)
- **`hallucination_reporter.py`**: Generates comprehensive reports about detected hallucinations with confidence scores and recommendations
- **`query_knowledge_graph.py`**: Interactive CLI tool for exploring the knowledge graph (functionality now integrated into MCP tools)

### Knowledge Graph Schema:

The Neo4j database stores code structure as:

**Nodes:**
- `Repository`: GitHub repositories
- `File`: Python files within repositories  
- `Class`: Python classes with methods and attributes
- `Method`: Class methods with parameter information
- `Function`: Standalone functions
- `Attribute`: Class attributes

**Relationships:**
- `Repository` -[:CONTAINS]-> `File`
- `File` -[:DEFINES]-> `Class`
- `File` -[:DEFINES]-> `Function`
- `Class` -[:HAS_METHOD]-> `Method`
- `Class` -[:HAS_ATTRIBUTE]-> `Attribute`

### Workflow:

1. **Repository Parsing**: Use `parse_github_repository` tool to clone and analyze open-source repositories
2. **Code Validation**: Use `check_ai_script_hallucinations` tool to validate AI-generated Python scripts
3. **Knowledge Exploration**: Use `query_knowledge_graph` tool to explore available repositories, classes, and methods

## Troubleshooting

### Docker Issues

**Container won't start:**
```bash
# Check logs for specific errors
docker compose logs mcp-crawl4ai

# Verify configuration is valid
docker compose config

# Restart problematic services
docker compose restart mcp-crawl4ai
```

**SearXNG not accessible:**
```bash
# Check if SearXNG is running
docker compose logs searxng

# Verify internal networking
docker compose exec mcp-crawl4ai curl http://searxng:8080
```

**Port conflicts:**
```bash
# Check what's using ports
netstat -tulpn | grep -E ":(8051|8080)"

# Change ports in docker-compose.yml if needed
ports:
  - "8052:8051"  # Changed from 8051:8051
```

### Common Configuration Issues

**Environment variables not loading:**
- Ensure `.env` file is in the same directory as `docker-compose.yml`
- Verify no spaces around `=` in `.env` file
- Check for special characters that need quoting

**API connection failures:**
- Verify `OLLAMA_BASE_URL` points to a reachable OpenAI-compatible endpoint
- Check `DATABASE_URL` matches the bundled Postgres service (default `postgresql://rag:rag@postgres:5432/ragdb`)
- Test the LLM endpoint from within the container:
  ```bash
  docker compose exec mcp-crawl4ai curl "$OLLAMA_BASE_URL/v1/models"
  ```

**Neo4j connection issues:**
- Use `host.docker.internal:7687` instead of `localhost:7687` for Neo4j running on host
- Verify Neo4j is running and accessible
- Check firewall settings for port 7687

### Performance Optimization

**Memory usage:**
```bash
# Monitor resource usage
docker stats

# Adjust memory limits in docker-compose.yml
deploy:
  resources:
    limits:
      memory: 2G
```

**Disk space:**
```bash
# Clean up Docker
docker system prune -a

# Check volume usage
docker volume ls
```

### Getting Help

1. **Check logs first**: `docker compose logs -f`
2. **Verify configuration**: `docker compose config`
3. **Test connectivity**: Use `curl` commands shown above
4. **Reset everything**: `docker compose down -v && docker compose up -d`

## Development & Customization

This Docker stack provides a foundation for building more complex MCP servers:

1. **Modify the MCP server**: Edit files in `src/` and rebuild: `docker compose build mcp-crawl4ai`
2. **Add custom tools**: Extend `src/crawl4ai_mcp.py` with `@mcp.tool()` decorators
3. **Customize SearXNG**: Edit `searxng/settings.yml` and restart
4. **Add services**: Extend `docker-compose.yml` with additional containers

