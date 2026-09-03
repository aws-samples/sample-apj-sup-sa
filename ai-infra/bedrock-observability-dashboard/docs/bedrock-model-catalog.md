# Amazon Bedrock — Model Catalog Reference

> **Purpose:** Reference catalog of models exercised by the synthetic data generator, and broader Bedrock model availability by modality and region. Use this to understand which model IDs the generator targets and what capabilities each model supports.
> **Region:** us-east-1 / Global Cross-region unless noted
> **Date:** June 2026

---

## Table of Contents

1. [Standard On-Demand (Text/Chat)](#1-standard-on-demand-textchat)
2. [Image Generation](#2-image-generation)
3. [Video Generation](#3-video-generation)
4. [Speech & Audio](#4-speech--audio)
5. [Embeddings & Search](#5-embeddings--search)
6. [Priority Tier](#6-priority-tier)
7. [Flex Tier](#7-flex-tier)
8. [Provisioned Throughput](#8-provisioned-throughput)
9. [Fine-Tuning & Training](#9-fine-tuning--training)
10. [Caching Support Summary](#10-caching-support-summary)

---

## 1. Standard On-Demand (Text/Chat)

| Provider | Model | Region | Notes |
|----------|-------|--------|-------|
| AI21 Labs | Jamba 1.5 Large | us-east-1 | |
| AI21 Labs | Jamba 1.5 Mini | us-east-1 | |
| AI21 Labs | Jurassic-2 Mid | us-east-1 | |
| AI21 Labs | Jurassic-2 Ultra | us-east-1 | |
| AI21 Labs | Jamba-Instruct | us-east-1 | |
| Amazon | Nova 2 Lite (Text/Image/Video) | Global Cross-region | Batch available |
| Amazon | Nova 2 Omni Preview (text input) | Global Cross-region | Multimodal: audio, image, video input; image output |
| Amazon | Nova 2 Omni Preview (image input) | Global Cross-region | Image/Video input |
| Amazon | Nova 2 Omni Preview (video input) | Global Cross-region | |
| Amazon | Nova 2 Omni Preview (audio input) | Global Cross-region | |
| Amazon | Nova 2 Omni Preview (text output) | Global Cross-region | |
| Amazon | Nova 2 Omni Preview (image output) | Global Cross-region | |
| Amazon | Nova 2 Pro Preview | Global Cross-region | All inputs supported; Batch available |
| Amazon | Titan Text Premier | us-east-1 | |
| Amazon | Titan Text Lite | us-east-1 | |
| Amazon | Titan Text Express | us-east-1 | |
| Amazon | Titan Text Embeddings | us-east-1 | Embeddings only |
| Amazon | Titan Text Embeddings V2 | us-east-1 | |
| Anthropic | Claude Opus 4.8 | Global Cross-region | Prompt caching supported |
| Anthropic | Claude Opus 4.7 | Global Cross-region | Prompt caching supported |
| Anthropic | Claude Sonnet 4.6 | Global Cross-region | Prompt caching supported |
| Anthropic | Claude Opus 4.6 | Global Cross-region | Prompt caching supported |
| Anthropic | Claude Opus 4.5 | Global Cross-region | Prompt caching supported |
| Anthropic | Claude Haiku 4.5 | Global Cross-region | Prompt caching supported |
| Anthropic | Claude Sonnet 4.5 | Global Cross-region | Prompt caching supported |
| Anthropic | Claude Sonnet 4 | Global Cross-region | Prompt caching supported; 1h cache not available |
| Cohere | Command | us-east-1 | |
| Cohere | Command-Light | us-east-1 | |
| Cohere | Command R+ | us-east-1 | |
| Cohere | Command R | us-east-1 | |
| Cohere | Embed 3 English | us-east-1 | Image input supported |
| Cohere | Embed 3 Multilingual | us-east-1 | Image input supported |
| Cohere | Embed 4 Model | us-east-1 | |
| DeepSeek | DeepSeek-R1 | Global Cross-region | |
| Mistral AI | Mistral 7B | us-east-1 | |
| Mistral AI | Mixtral 8x7B | us-east-1 | |
| Mistral AI | Mistral Small (24.02) | us-east-1 | |
| Mistral AI | Mistral Large (24.02) | us-east-1 | |
| Mistral AI | Pixtral Large (25.02) | us-east-1 | |
| Writer | Palmyra X4 | us-east-1 | |
| Writer | Palmyra X5 | us-east-1 | |
| Writer | Palmyra Vision 7B | us-east-1 | |

## 2. Image Generation

| Provider | Model | Resolution/Type | Notes |
|----------|-------|-----------------|-------|
| Amazon | Nova Canvas | Up to 1024x1024 | Standard and premium quality |
| Amazon | Nova Canvas | Up to 2048x2048 | Standard and premium quality |
| Amazon | Titan Image Gen v1 | <=512x512 | |
| Amazon | Titan Image Gen v1 | >512x512 | |
| Amazon | Titan Image Gen v2 | <=512x512 | |
| Amazon | Titan Image Gen v2 | >1024x1024 | |
| Stability AI | Stable Diffusion 3.5 Large | Per image | |
| Stability AI | Stable Image Core | Per image | |
| Stability AI | Stable Diffusion 3 Large | Per image | |
| Stability AI | Stable Image Ultra | Per image | |
| Stability AI | Remove Background | Service | |
| Stability AI | Erase Object | Service | |
| Stability AI | Control Structure | Service | |
| Stability AI | Control Sketch | Service | |
| Stability AI | Style Guide | Service | |
| Stability AI | Search and Replace | Service | |
| Stability AI | Inpaint | Service | |
| Stability AI | Search and Recolor | Service | |
| Stability AI | Style Transfer | Service | |
| Stability AI | Conservative Upscale | Service | |
| Stability AI | Creative Upscale | Service | |
| Stability AI | Fast Upscale | Service | |
| Stability AI | Outpaint | Service | |

## 3. Video Generation

| Provider | Model | Resolution | Notes |
|----------|-------|-----------|-------|
| Amazon | Nova Reel | 720p, 24fps | |
| Luma AI | Ray2 | 720p | US West Oregon |
| Luma AI | Ray2 | 540p | US West Oregon |

## 4. Speech & Audio

| Provider | Model | Modality | Notes |
|----------|-------|----------|-------|
| Amazon | Nova Sonic | Speech | |
| Amazon | Nova Sonic | Text | |
| Amazon | Nova 2 Sonic | Speech | |
| Amazon | Nova 2 Sonic | Text | |
| Mistral AI | Voxtral Mini | Speech (US East/Ohio/Oregon) | |
| Mistral AI | Voxtral Small | Speech (US East/Ohio/Oregon) | |
| Mistral AI | Voxtral Mini | Speech (Mumbai) | |
| Mistral AI | Voxtral Small | Speech (Mumbai) | |
| Mistral AI | Voxtral Mini | Speech (Sao Paulo/Tokyo) | |
| Mistral AI | Voxtral Small | Speech (Sao Paulo/Tokyo) | |
| Mistral AI | Voxtral Mini | Speech (Ireland/Milan) | |
| Mistral AI | Voxtral Small | Speech (Ireland/Milan) | |
| Mistral AI | Voxtral Mini | Speech (London) | |
| Mistral AI | Voxtral Small | Speech (London) | |
| Mistral AI | Voxtral Mini | Speech (Sydney) | |
| Mistral AI | Voxtral Small | Speech (Sydney) | |

## 5. Embeddings & Search

| Provider | Model | Region | Notes |
|----------|-------|--------|-------|
| Amazon | Nova Multimodal Embeddings (text) | us-east-1 | On-demand and Batch available |
| Amazon | Nova Multimodal Embeddings (standard image) | us-east-1 | On-demand and Batch available |
| Amazon | Nova Multimodal Embeddings (document image) | us-east-1 | On-demand and Batch available |
| Amazon | Nova Multimodal Embeddings (video) | us-east-1 | On-demand and Batch available |
| Amazon | Nova Multimodal Embeddings (audio) | us-east-1 | On-demand and Batch available |
| Amazon | Titan Text Embeddings | us-east-1 | |
| Amazon | Titan Text Embeddings V2 | us-east-1 | Batch available |
| Amazon | Titan Multimodal Embeddings (text) | us-east-1 | Batch available |
| Amazon | Titan Multimodal Embeddings (image) | us-east-1 | Batch available |
| Cohere | Embed 3 English | us-east-1 | Image input supported |
| Cohere | Embed 3 Multilingual | us-east-1 | Image input supported |
| Cohere | Embed 4 Model | us-east-1 | |
| Cohere | Rerank 3.5 | us-east-1 | Reranking service |
| Amazon | Nova 2 Lite (Web Grounding) | us-east-1 | Built-In Tools |
| Amazon | Nova 2 Omni (Web Grounding) | us-east-1 | Built-In Tools |
| Amazon | Nova Premier (Web Grounding) | us-east-1 | Built-In Tools |
| TwelveLabs | Pegasus 1.2 | Global Cross-region | Video + text output |
| TwelveLabs | Marengo Embed 2.7 | Geo/In-region | Video, audio, image, text input |
| TwelveLabs | Pegasus 1.2 | Geo/In-region | Video + text output |
| TwelveLabs | Marengo Embed 3.0 | Geo/In-region | Video, audio, image, text input |

## 6. Priority Tier

| Provider | Model | Region | Notes |
|----------|-------|--------|-------|
| Amazon | Nova 2 Lite | Global Cross-region | |
| Amazon | Nova 2 Omni (text in) | Global Cross-region | Multimodal: image, video, audio input; image output |
| Amazon | Nova 2 Omni (image in) | Global Cross-region | |
| Amazon | Nova 2 Omni (video in) | Global Cross-region | |
| Amazon | Nova 2 Omni (audio in) | Global Cross-region | |
| Amazon | Nova 2 Omni (text out) | Global Cross-region | |
| Amazon | Nova 2 Omni (image out) | Global Cross-region | |
| Amazon | Nova 2 Pro | Global Cross-region | All inputs same; Image out N/A |
| Amazon | Nova 2 Lite | Geo/In-region | |
| Amazon | Nova Pro | Geo/In-region | |
| Amazon | Nova Premier | Geo/In-region | |
| Amazon | Nova 2 Omni (text in) | Geo/In-region | Multimodal: image, video, audio input; image output |
| Amazon | Nova 2 Omni (image in) | Geo/In-region | |
| Amazon | Nova 2 Omni (video in) | Geo/In-region | |
| Amazon | Nova 2 Omni (audio in) | Geo/In-region | |
| Amazon | Nova 2 Omni (text out) | Geo/In-region | |
| Amazon | Nova 2 Omni (image out) | Geo/In-region | |
| Amazon | Nova 2 Pro | Geo/In-region | All inputs same |
| DeepSeek | DeepSeek-V3.1 | US East Ohio | |
| DeepSeek | DeepSeek-V3.1 | Sydney | |
| OpenAI | gpt-oss-20b | US East/Ohio/Oregon | |
| OpenAI | gpt-oss-120b | US East/Ohio/Oregon | |
| OpenAI | gpt-oss-20b | Sydney | |
| OpenAI | gpt-oss-120b | Sydney | |
| Qwen | Qwen3 Coder 30B | US East/Ohio/Oregon | |
| Qwen | Qwen3 32B | US East/Ohio/Oregon | |
| Qwen | Qwen3 Coder 30B | Sydney | |
| Qwen | Qwen3 32B | Sydney | |
| Qwen | Qwen3 235B A22B | Sydney | |

## 7. Flex Tier

| Provider | Model | Region | Notes |
|----------|-------|--------|-------|
| Amazon | Nova 2 Lite | Global Cross-region | |
| Amazon | Nova 2 Omni (text in) | Global Cross-region | Multimodal: image, video, audio input; image output |
| Amazon | Nova 2 Omni (image in) | Global Cross-region | |
| Amazon | Nova 2 Omni (video in) | Global Cross-region | |
| Amazon | Nova 2 Omni (audio in) | Global Cross-region | |
| Amazon | Nova 2 Omni (text out) | Global Cross-region | |
| Amazon | Nova 2 Omni (image out) | Global Cross-region | |
| Amazon | Nova 2 Pro | Global Cross-region | All inputs same |
| Amazon | Nova 2 Lite | Geo/In-region | |
| Amazon | Nova Pro | Geo/In-region | |
| Amazon | Nova Premier | Geo/In-region | |
| Amazon | Nova 2 Omni (text in) | Geo/In-region | Multimodal: image, video, audio input; image output |
| Amazon | Nova 2 Omni (image in) | Geo/In-region | |
| Amazon | Nova 2 Omni (video in) | Geo/In-region | |
| Amazon | Nova 2 Omni (audio in) | Geo/In-region | |
| Amazon | Nova 2 Omni (text out) | Geo/In-region | |
| Amazon | Nova 2 Omni (image out) | Geo/In-region | |
| Amazon | Nova 2 Pro | Geo/In-region | All inputs same |
| DeepSeek | DeepSeek-V3.1 | US East Ohio | |
| DeepSeek | DeepSeek-V3.1 | Sydney | |
| OpenAI | gpt-oss-20b | US East/Ohio/Oregon | |
| OpenAI | gpt-oss-120b | US East/Ohio/Oregon | |
| OpenAI | gpt-oss-20b | Sydney | |
| OpenAI | gpt-oss-120b | Sydney | |
| Qwen | Qwen3 Coder 30B | US East/Ohio/Oregon | |
| Qwen | Qwen3 32B | US East/Ohio/Oregon | |
| Qwen | Qwen3 Coder 30B | Sydney | |
| Qwen | Qwen3 32B | Sydney | |
| Qwen | Qwen3 235B A22B | Sydney | |

## 8. Provisioned Throughput

| Provider | Model | Region | Commitment Options | Notes |
|----------|-------|--------|--------------------|-------|
| Amazon | Nova 2 Lite | Oregon | No Commit, 1-Month, 6-Month | |
| Amazon | Nova Micro | Oregon | No Commit, 1-Month, 6-Month | |
| Amazon | Nova Lite | Oregon | No Commit, 1-Month, 6-Month | |
| Amazon | Nova Pro | Oregon | No Commit, 1-Month, 6-Month | |
| Amazon | Titan Text Lite | us-east-1 | 1-Month, 6-Month | |
| Amazon | Titan Text Express | us-east-1 | 1-Month, 6-Month | |
| Amazon | Titan Embeddings | us-east-1 | 1-Month, 6-Month | No commit N/A |
| Amazon | Titan Image Gen v1 | us-east-1 | 1-Month, 6-Month | No commit N/A |
| Amazon | Titan Image Gen v1 (custom) | us-east-1 | No Commit, 1-Month, 6-Month | |
| Amazon | Titan Image Gen v2 | us-east-1 | No Commit, 1-Month, 6-Month | |
| Amazon | Titan Image Gen v2 (custom) | us-east-1 | No Commit, 1-Month, 6-Month | |
| Amazon | Titan Multimodal Embeddings | us-east-1 | No Commit, 1-Month, 6-Month | |
| Cohere | Command | us-east-1 | No Commit, 1-Month, 6-Month | |
| Cohere | Command-Light | us-east-1 | No Commit, 1-Month, 6-Month | |
| Cohere | Embed 3 English | us-east-1 | No Commit, 1-Month, 6-Month | |
| Cohere | Embed 3 Multilingual | us-east-1 | No Commit, 1-Month, 6-Month | |
| Meta | Llama 3.2 1B | Oregon | No Commit, 1-Month, 6-Month | |
| Meta | Llama 3.2 3B | Oregon | No Commit, 1-Month, 6-Month | |
| Meta | Llama 3.2 11B | Oregon | No Commit, 1-Month, 6-Month | |
| Meta | Llama 3.2 90B | Oregon | No Commit, 1-Month, 6-Month | |
| Meta | Llama 3.1 8B | Oregon | No Commit, 1-Month, 6-Month | |
| Meta | Llama 3.1 70B | Oregon | No Commit, 1-Month, 6-Month | |
| Meta | Llama 2 Pretrained 13B | us-east-1 | 1-Month, 6-Month | No commit N/A |
| Meta | Llama 2 Pretrained 70B | us-east-1 | 1-Month, 6-Month | No commit N/A |

## 9. Fine-Tuning & Training

| Provider | Model | Region | Training Unit | Storage | Notes |
|----------|-------|--------|---------------|---------|-------|
| Amazon | Nova 2 Lite (SFT) | us-east-1 | Per 1K tokens | Per month | |
| Amazon | Nova 2 Lite (RFT) | us-east-1 | Per hour | Per month | |
| Amazon | Nova Micro | us-east-1 | Per 1K tokens | Per month | |
| Amazon | Nova Lite | us-east-1 | Per 1K tokens | Per month | |
| Amazon | Nova Pro | us-east-1 | Per 1K tokens | Per month | |
| Amazon | Nova Canvas | us-east-1 | Per image seen | Per month | PT available |
| Amazon | Titan Text Lite | us-east-1 | Per 1K tokens | Per month | |
| Amazon | Titan Text Express | us-east-1 | Per 1K tokens | Per month | |
| Amazon | Titan Image Gen | us-east-1 | Per image seen | Per month | |
| Amazon | Titan Multimodal Embeddings | us-east-1 | Per image | Per month | |
| Cohere | Command | us-east-1 | Per 1M tokens | Per month | |
| Cohere | Command-Light | us-east-1 | Per 1M tokens | Per month | |
| Meta | Llama 3.2 1B | Oregon | Per 1M tokens | Per month | |
| Meta | Llama 3.2 3B | Oregon | Per 1M tokens | Per month | |
| Meta | Llama 3.2 11B | Oregon | Per 1M tokens | Per month | |
| Meta | Llama 3.2 90B | Oregon | Per 1M tokens | Per month | |
| Meta | Llama 3.1 8B | Oregon | Per 1M tokens | Per month | |
| Meta | Llama 3.1 70B | Oregon | Per 1M tokens | Per month | |
| Meta | Llama 2 Pretrained 13B | us-east-1 | Per 1M tokens | Per month | |
| Meta | Llama 2 Pretrained 70B | us-east-1 | Per 1M tokens | Per month | |
| OpenAI | gpt-oss-20b (RFT) | US East/Oregon | Per hour | Per month | Inference available |
| Qwen | Qwen3 32B (RFT) | US East/Oregon | Per hour | Per month | Inference available |

## 10. Caching Support Summary

Only the following models support prompt caching on Bedrock:

| Provider | Model | Cache Write TTL Options | Cache Read |
|----------|-------|------------------------|------------|
| Anthropic | Claude Opus 4.8 | 5m, 1h | Supported |
| Anthropic | Claude Opus 4.7 | 5m, 1h | Supported |
| Anthropic | Claude Sonnet 4.6 | 5m, 1h | Supported |
| Anthropic | Claude Opus 4.6 | 5m, 1h | Supported |
| Anthropic | Claude Opus 4.5 | 5m, 1h | Supported |
| Anthropic | Claude Haiku 4.5 | 5m, 1h | Supported |
| Anthropic | Claude Sonnet 4.5 | 5m, 1h | Supported |
| Anthropic | Claude Sonnet 4 | 5m only | Supported |

**All other models listed above do NOT support prompt caching.**
