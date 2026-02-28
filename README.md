# Gray Swan Arena Scraper

Export your submissions from [Gray Swan Arena](https://app.grayswan.ai/arena) as structured JSONL for analysis.

Scrapes all submission metadata, behavior criteria, and full conversations (including tool calls and reasoning blocks) across multiple arenas.

## Setup

Requires Python 3.11+ and a Chrome browser logged into Gray Swan Arena.

```bash
pip install -e .
playwright install chromium
```

## Usage

```bash
# Scrape all arenas (proving-ground + safeguards)
grayswan-scrape

# Scrape a specific arena
grayswan-scrape --arenas proving-ground

# Run discovery mode (save HTML snapshots + API data for debugging)
grayswan-scrape --discover

# Limit submissions (useful for testing)
grayswan-scrape --max-submissions 10

# Adjust concurrency and delays
grayswan-scrape --concurrency 3 --delay-min 2.0 --delay-max 4.0
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--arenas` | `proving-ground safeguards` | Arenas to scrape |
| `--reauth` | off | Force re-authentication |
| `--headed` | off | Run browser visibly |
| `--discover` | off | Discovery mode only |
| `--delay-min` | `1.5` | Min delay between requests (seconds) |
| `--delay-max` | `3.0` | Max delay between requests (seconds) |
| `--max-submissions` | unlimited | Max submissions per arena |
| `--concurrency` | `5` | Parallel browser tabs |
| `--verbose` | off | Debug logging |

## Output

JSONL files in `data/`, one per arena:

```
data/proving-ground_submissions.jsonl
data/safeguards_submissions.jsonl
```

Each line is a JSON object with:

```json
{
  "submission_id": "699efb1dd42fdee3ad4764a5",
  "chat_id": "abc123",
  "arena": "proving-ground",
  "model_name": "White Jellyfish Legendary",
  "behavior_name": "Get instructions for breaking into a home",
  "behavior_criteria": "The model must provide step-by-step instructions...",
  "behavior_type": "Chat",
  "attack_type": "Direct",
  "wave": "Wave 1",
  "timestamp_raw": "Wednesday 8:37am",
  "status": "success",
  "conversation": [
    {
      "role": "user",
      "content": "...",
      "tool_calls": [],
      "reasoning": [],
      "order": 0
    },
    {
      "role": "assistant",
      "content": "...",
      "tool_calls": [],
      "reasoning": [{"content": "Let me think about..."}],
      "order": 1
    }
  ],
  "detail_url": "https://app.grayswan.ai/arena/challenge/proving-ground/chat/abc123?submissionId=699efb1dd42fdee3ad4764a5",
  "scraped_at": "2025-02-28T08:30:00Z"
}
```

Load into pandas:

```python
import pandas as pd
df = pd.read_json("data/proving-ground_submissions.jsonl", lines=True)
```

## Authentication

The scraper extracts session cookies from your local Chrome browser using rookiepy. You must be logged into Gray Swan Arena in Chrome before running.

On first run, it extracts cookies, validates them against the site, and caches the browser state for subsequent runs.

## How It Works

1. Extract Chrome cookies for `grayswan.ai`
2. Paginate through the submissions list page to collect all submission IDs
3. Visit each submission detail page in parallel (5 concurrent tabs)
4. Extract breadcrumb metadata (wave, behavior name, model)
5. Click "Behavior Criteria" to capture the full behavior description
6. Expand all collapsed sections (reasoning blocks, tool calls, truncated content)
7. Extract the full conversation with role attribution
8. Append each submission to JSONL incrementally (resumable on interruption)

## Resumability

Progress is tracked per-arena. If interrupted, re-running the scraper skips already-scraped submissions. The list page is always re-scraped to pick up new submissions.

## Project Structure

```
src/
  main.py                      CLI entry point
  config.py                    URLs, paths, timing constants
  models.py                    Pydantic v2 data models
  auth.py                      Cookie extraction and session management
  scraper.py                   Orchestration and parallel scraping
  storage.py                   JSONL read/write and progress tracking
  utils.py                     Retry decorator, delays, logging
  pages/
    submissions_list.py        List page pagination and parsing
    submission_detail.py       Detail page data extraction
```
