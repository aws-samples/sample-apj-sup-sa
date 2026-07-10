# Lightweight Semantic Prompt Routing on AWS

An intelligent router that classifies query complexity and routes to the cheapest capable Amazon Bedrock model — spanning **8 model families** and **15 models** across 4 cost tiers.

**Features:**
- Semantic query classification (complexity, task type, language)
- Cost-optimized routing (60-80% savings vs. using only premium models)
- Production-ready resilience (automatic fallbacks, retries, rate limiting)
- Real-time cost tracking and analytics
- Multi-cloud ready (easy to extend beyond AWS Bedrock)

---

## Quick Start

This router can use an LLM from Amazon Bedrock or a local LLM from Ollama as the classifier model. The default classifier model is Amazon Nova Micro from Amazon Bedrock. Note that there is a cost associated for using models from Amazon Bedrock, e.g. for Amazon Nova Micro, it is $0.035 per 1M input tokens, and $0.14 per 1M output tokens, in US East (N. Virginia) region. If you want to use Ollama as the classifier instead, just change the classifi_query() function call inside route_and_respond() (in router.py) to classify_query_ollama(). The default model used in Ollama is Llama 3.2.

### Setup Ollama (if using Ollama as classifier)
Download and install Ollama: https://ollama.com/download
To launch a model (e.g. llama3.2), run:

```bash
ollama run llama3.2
```

Just replace classify_query() function call inside router.py with classify_query_ollama().

### Streamlit Demo
```bash
git clone https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa/agentic-workloads/semantic-prompt-routing
python -m venv .
source bin/activate
pip install -r requirements.txt
streamlit run app/demo.py
```

### Interactive Examples
```bash
python examples/quickstart.py --example all
```

---

### Adding a New Region

To support a new region, add a region-specific model config inside model_definitions folder. Naming convention of the file: For example for us-east-1, it is us_east_1.py.


---

## Why This Matters

The pricing spread across Bedrock models is **312×** — from Gemma 3 4B at $0.08/M output tokens to Claude 4.7 Opus at $25/M.

This router achieves significant cost savings while maintaining quality vs. always using the most capable model.

---

## Model Pool (15 Models × 8 Families × 4 Tiers)

| Tier | Model | Family | Input $/1M | Output $/1M | Best For |
|------|-------|--------|-----------|------------|----------|
| T1 | Nova Micro | Amazon | $0.035 | $0.14 | Classification, FAQ |
| T1 | Gemma 3 4B | Google | $0.04 | $0.08 | Simple text tasks |
| T1 | Nova Lite | Amazon | $0.06 | $0.24 | Multimodal |
| T1 | GLM 4.7 Flash | Z AI | $0.07 | $0.40 | CJK tasks |
| T1 | Gemma 3 12B | Google | $0.09 | $0.29 | Summarization |
| T2 | Llama 4 Scout | Meta | $0.17 | $0.17 | Long context (10M!) |
| T2 | Qwen3 32B | Qwen | $0.20 | $0.78 | Code + CJK |
| T2 | Llama 4 Maverick | Meta | $0.20 | $0.80 | Complex reasoning |
| T2 | Gemma 3 27B | Google | $0.23 | $0.38 | Moderate tasks |
| T3 | Kimi K2.5 | Moonshot | $0.60 | $3.00 | Vision + tools |
| T3 | DeepSeek V3.2 | DeepSeek | $0.62 | $1.85 | Code + math |
| T3 | GLM 4.7 | Z AI | $0.60 | $2.20 | CJK reasoning |
| T3 | Nova Pro | Amazon | $0.80 | $3.20 | Agents, RAG |
| T3 | Claude 4.5 Haiku | Anthropic | $0.80 | $4.00 | Structured output |
| T4 | Claude 4.6 Sonnet | Anthropic | $3.00 | $15.00 | Expert reasoning |
| T4 | Claude 4.7 Opus | Anthropic | $5.00 | $25.00 | Expert reasoning |

---

## Architecture

```
Query → Classify (complexity, task, language) 
          ↓
        Select Model (semantic rules + cost optimization)
          ↓
        Bedrock API → Response
```

### Key Components

1. **Classification** - Analyzes query for complexity (0-1), task type, language
2. **Semantic Selection** - Applies intelligent routing rules (code→DeepSeek, CJK→GLM, etc.)
3. **Cost Tracking** - Real-time monitoring and analytics

---

## Routing Logic (put your own in router.select_model())

### Selection Rules

| Priority | Condition | Routes To | Reason |
|----------|-----------|-----------|--------|
| 1 | >100K tokens | Llama 4 Scout | 10M context window |
| 2 | Code + complex (>0.6) | DeepSeek V3.2 | Code specialist |
| 3 | Code + simple (≤0.6) | Qwen 32B | Budget code model |
| 4 | CJK + complex (≥0.4) | GLM 4.7 | Best CJK reasoning |
| 5 | CJK + simple (<0.4) | GLM Flash | Cheapest CJK |
| 6 | Non-EN (>0.5) | Claude Sonnet | Best multilingual |
| 7 | Structured output | Claude Haiku | Tool use expert |
| 8 | Complexity < 0.3 | Tier 1 | Budget models |
| 9 | Complexity < 0.5 | Tier 2 | Standard models |
| 10 | Complexity < 0.7 | Tier 3 | Advanced models |
| 11 | Complexity ≥ 0.7 | Tier 4 | Expert models |


---

## Usage

### Basic Example

```python
from app.router import SemanticRouter

# Initialize router
router = SemanticRouter(
    region='us-east-1',
    enabled_families={"Nova", "Claude", "DeepSeek"}
)

# Route a query
result = await router.route_and_respond(
    query="Write a Python function to parse JSON",
    conversation_history=[],
)

print(f"Response: {result.response}")
print(f"Model: {result.family} Tier {result.tier}")
print(f"Cost: ${result.cost_usd:.6f}")
print(f"Complexity: {result.classification_signals.complexity_score:.2f}")
```

---

## Installation

### Prerequisites
- Python 3.10+
- AWS account with Bedrock access
- AWS credentials: `aws configure`

### Install

```bash
# Clone repository
git clone <your-repo>
cd semantic-prompt-routing

# Install dependencies
pip install -r requirements.txt

```

---

## Project Structure

```
semantic-prompt-routing/
├── README.md                 # This file
├── app/
│   ├── router.py            # Main router implementation
│   ├── demo.py              # Streamlit demo
│   └── routing/
│       └── classifier.py    # Query classifier
├── examples/
│   ├── quickstart.py        # Interactive tutorials
│   ├── test_router.py       # Tests
│   └── README.md            # Examples guide
└── requirements.txt         # Dependencies
```

---

## Testing

### Quick Tests (No LLM Response Calls)

```bash
# Test classification only (fast)
python examples/test_router.py --mode classify
```

### Full Tests

```bash
# Test with real LLM calls
python examples/test_router.py --mode full

# Compare routing strategies
python examples/test_router.py --mode strategies

# Run all tests
python examples/test_router.py --mode all
```

### Try Examples

```bash
# Basic usage
python examples/quickstart.py --example basic

# Cost comparison
python examples/quickstart.py --example cost

# Family filtering
python examples/quickstart.py --example family
```

---

## Configuration

### Environment Variables

```bash
export AWS_REGION=us-east-1
export ENABLED_FAMILIES=Nova,Claude,DeepSeek,Qwen
```

### Programmatic Configuration

```python
router = SemanticRouter(
    region='us-east-1',
    enabled_families={"Nova", "Claude"}  # Restrict families
)
```

### Add Custom Models (region specific - flexibility to choose CRIS vs. regional endpoints for data residency considerations)

Edit `MODEL_CONFIGS` in `app/model_definitions/<region>.py`:

---

## Cost Analysis

**Monthly cost for 100K queries:**

```
All Claude Sonnet (no routing)     $1,800   Baseline
Cost-Optimized Router              $415     -77%
Pure cost-based (no semantics)     $380     -79% ⚠️ quality risk
```

**Key insight:** Semantic routing achieves same cost savings as pure cost-based, but with quality guarantees.

---

## Troubleshooting

**Q: High latency?**
- Remove the model causing latency from MODEL_CONFIGS
- Or use Ollama locally for fast classification

**Q: Inaccurate classification?**
- Change the classifier model and prompt

**Q: Want to add more models?**
- Edit `MODEL_CONFIGS` in `app/model_definitions/<region>.py`

---

## Production Deployment

### Features

- **Automatic Fallbacks** - 3-level chains ensure 99.8% success rate. Automatic fallback to heuristic classification in case LLM classification fails
- **Cost Tracking** - Real-time cost tracking and analytics  

### Monitoring

```python
# Get statistics
stats = router.get_stats()
print(f"Total Cost: ${stats['total_cost_usd']:.6f}")
print(f"Avg Latency: {stats['avg_latency_per_request']:.2f}s")
```

---

## License

This library is licensed under the MIT-0 License.

---

## Contributing

Contributions welcome! Please open an issue or PR.

**To add a new routing strategy:**
1. Update `SemanticSelector.select_model()` in `app/router.py`
2. Add test case to `examples/test_router.py`

**To add a new model:**
1. Add to `MODEL_CONFIGS` in `app/model_definitions/<region>.py`
2. Update model pool table above
3. Test with `examples/quickstart.py`
