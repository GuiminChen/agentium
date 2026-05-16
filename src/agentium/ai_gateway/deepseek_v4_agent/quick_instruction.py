"""Table 5 Quick Instruction formats (reference); optional future wiring."""

from __future__ import annotations

# Verbatim patterns from DeepSeek-V4 report Table 5 (documentation-only hooks).

QUICK_ACTION_TEMPLATE = "<|User|>{prompt}<|Assistant|><think><|action|>"

QUICK_TITLE_TEMPLATE = "<|Assistant|>{response}<|end_of_sentence|><|title|>"

QUICK_QUERY_TEMPLATE = "<|User|>{prompt}<|query|>"

QUICK_AUTHORITY_TEMPLATE = "<|User|>{prompt}<|authority|>"

QUICK_DOMAIN_TEMPLATE = "<|User|>{prompt}<|domain|>"

QUICK_READ_URL_TEMPLATE = "<|User|>{prompt}<|extracted_url|>{url}<|read_url|>"
