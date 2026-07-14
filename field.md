Here's the full field schema for **episodic_db**:

---

## `sessions` (session-level log)

| Field          | Type        | Description                                  |
| -------------- | ----------- | -------------------------------------------- |
| `session_id`   | TEXT PK     | Unique session identifier                    |
| `started_at`   | TEXT        | ISO timestamp of session start               |
| `ended_at`     | TEXT        | ISO timestamp of session end                 |
| `success`      | INTEGER     | 1 = success, 0 = failure                     |
| `total_tokens` | INTEGER     | Total tokens consumed in the session         |
| `total_cost`   | REAL        | Total cost ($)                               |
| `exec_env`     | TEXT (JSON) | OS, shell, runtimes, git info, cwd, platform |

---

## `tool_calls` (per-tool-call log)

| Field                   | Type    | Description                                         |
| ----------------------- | ------- | --------------------------------------------------- |
| `tool_use_id`           | TEXT PK | Unique tool call ID                                 |
| `session_id`            | TEXT FK | Parent session                                      |
| `seq`                   | INTEGER | Sequence number within session                      |
| `timestamp`             | TEXT    | ISO timestamp                                       |
| `model`                 | TEXT    | Model used (e.g. `claude-sonnet-4-5-20250929`)      |
| `tool_name`             | TEXT    | Tool name (Bash, Read, Write, Edit, etc.)           |
| `input_hash`            | TEXT    | Hash of normalized input                            |
| `normalized_input`      | TEXT    | Human-readable normalized tool input                |
| `input_tokens`          | INTEGER | Input tokens for this call                          |
| `output_tokens`         | INTEGER | Output tokens for this call                         |
| `cache_creation_tokens` | INTEGER | Prompt cache creation tokens                        |
| `cache_read_tokens`     | INTEGER | Prompt cache read tokens                            |
| `own_cost`              | REAL    | Direct cost of this call                            |
| `carry_cost`            | REAL    | Accumulated context cost carried forward            |
| `total_cost`            | REAL    | own_cost + carry_cost                               |
| `latency_ms`            | REAL    | Wall-clock latency                                  |
| `contributed_to`        | TEXT    | Whether it contributed to outcome (`DID_NOT`, etc.) |
| `is_wasteful`           | INTEGER | 1 = classified as wasteful                          |
| `episode_id`            | TEXT    | Parent episode ID                                   |

---

## `results` (tool call outputs)

| Field                  | Type       | Description                            |
| ---------------------- | ---------- | -------------------------------------- |
| `result_id`            | INTEGER PK | Auto-increment ID                      |
| `tool_use_id`          | TEXT FK    | Parent tool call                       |
| `result_hash`          | TEXT       | Hash of result content                 |
| `digest_handle`        | TEXT       | Handle for large result storage        |
| `inline_content`       | TEXT       | Inline result text (for small outputs) |
| `model_visible_tokens` | INTEGER    | Tokens visible to model                |
| `is_error`             | INTEGER    | 1 = error result                       |
| `output_chars`         | INTEGER    | Character count of output              |
| `output_lines`         | INTEGER    | Line count of output                   |

---

## `episodes` (grouped tool-call sequences)

| Field                         | Type               | Description                                 |
| ----------------------------- | ------------------ | ------------------------------------------- |
| `episode_id`                  | TEXT PK            | Unique episode ID                           |
| `session_id`                  | TEXT FK            | Parent session                              |
| `created_at`                  | TEXT               | ISO timestamp                               |
| **Identity / Classification** |                    |                                             |
| `converged_by`                | TEXT               | Tool call ID that converged the episode     |
| `waste_type`                  | TEXT               | e.g. `expensive-failure`                    |
| `outcome`                     | TEXT               | e.g. `converged`                            |
| `converged_resource`          | TEXT               | File path that was the convergence target   |
| **Code Context**              |                    |                                             |
| `touched_paths`               | TEXT (JSON array)  | All file paths touched                      |
| `path_prefix`                 | TEXT               | Common path prefix                          |
| `changed_symbols`             | TEXT (JSON array)  | Functions/classes modified                  |
| `test_names`                  | TEXT (JSON array)  | Test names involved                         |
| `grep_terms`                  | TEXT (JSON array)  | Search terms used                           |
| `error_signature`             | TEXT               | Error pattern if any                        |
| `lang`                        | TEXT               | Primary language (e.g. `python`)            |
| `tool_mix`                    | TEXT (JSON object) | Tool usage counts `{Bash: 4, Read: 3, ...}` |
| **Token / Cost Metrics**      |                    |                                             |
| `total_input_tokens`          | INTEGER            | Sum of input tokens                         |
| `total_output_tokens`         | INTEGER            | Sum of output tokens                        |
| `total_cache_creation`        | INTEGER            | Sum of cache creation tokens                |
| `total_cache_read`            | INTEGER            | Sum of cache read tokens                    |
| `own_cost`                    | REAL               | Direct cost of episode                      |
| `carry_cost`                  | REAL               | Accumulated carry cost                      |
| `total_cost`                  | REAL               | own + carry                                 |
| `carry_ratio`                 | REAL               | carry_cost / total_cost                     |
| **Efficiency Metrics**        |                    |                                             |
| `read_output_token_ratio`     | REAL               | Fraction of output tokens from Read calls   |
| `new_information_rate`        | REAL               | Rate of genuinely new info per call         |
| `repeated_read_rate`          | REAL               | Rate of redundant re-reads                  |
| `futility_score`              | REAL               | Composite waste score (0–1)                 |
| **Waste Analysis**            |                    |                                             |
| `is_wasteful`                 | INTEGER            | 1 = episode classified as wasteful          |
| `wasted_member_ids`           | TEXT (JSON array)  | Tool call IDs that were wasteful            |
| `wasted_own_cost`             | REAL               | Cost attributed to waste                    |
| `wasted_carry_cost`           | REAL               | Carry cost from wasteful calls              |
| `wasted_tokens`               | INTEGER            | Tokens wasted                               |
| **Embedding**                 |                    |                                             |
| `embedding_text`              | TEXT               | Serialized text used for vectorization      |
| `embedding_model`             | TEXT               | e.g. `all-MiniLM-L6-v2`                     |
| `embedding_dim`               | INTEGER            | Vector dimension (e.g. 384)                 |
| `embedding`                   | BLOB               | Raw embedding vector                        |

---

## `edges_touches` (tool → resource edges)

| Field         | Type    | Description                   |
| ------------- | ------- | ----------------------------- |
| `tool_use_id` | TEXT FK | Tool call                     |
| `resource_id` | TEXT FK | Resource touched              |
| `mode`        | TEXT    | Access mode (read/write/etc.) |
| `valid_from`  | TEXT    | Start of validity window      |
| `valid_to`    | TEXT    | End of validity window        |

---

## `proxy_calls` (LLM API call log)

| Field                   | Type              | Description                 |
| ----------------------- | ----------------- | --------------------------- |
| `call_index`            | INTEGER           | Sequence within session     |
| `session_id`            | TEXT              | Parent session              |
| `timestamp`             | TEXT              | ISO timestamp               |
| `model`                 | TEXT              | Model called                |
| `tool_use_ids`          | TEXT (JSON array) | Tool calls in this API call |
| `input_tokens`          | INTEGER           | Input tokens                |
| `output_tokens`         | INTEGER           | Output tokens               |
| `cache_creation_tokens` | INTEGER           | Cache creation tokens       |
| `cache_read_tokens`     | INTEGER           | Cache read tokens           |
| `total_cost`            | REAL              | Cost of this API call       |
| `latency_ms`            | REAL              | Latency                     |