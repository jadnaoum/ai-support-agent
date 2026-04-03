# LangSmith Trace Lookup

## Config
- Project: ai-support-agent
- Endpoint: https://eu.api.smith.langchain.com (EU region — US endpoint returns 403)
- API key: from LANGCHAIN_API_KEY in .env

## Find a trace by test_id tag
curl -s "https://eu.api.smith.langchain.com/api/v1/runs/search" \
  -H "x-api-key: $LANGCHAIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "ai-support-agent",
    "filter": "has(tags, \"<TEST_ID>\")",
    "limit": 1
  }' | python -m json.tool

## Get full trace details by run_id
curl -s "https://eu.api.smith.langchain.com/api/v1/runs/<RUN_ID>" \
  -H "x-api-key: $LANGCHAIN_API_KEY" | python -m json.tool

## Get child runs (individual nodes) for a trace
curl -s "https://eu.api.smith.langchain.com/api/v1/runs/search" \
  -H "x-api-key: $LANGCHAIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "ai-support-agent",
    "filter": "eq(parent_run_id, \"<RUN_ID>\")",
    "limit": 20
  }' | python -m json.tool
