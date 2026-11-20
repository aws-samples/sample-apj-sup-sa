# AI Infra

Sample projects for AI infrastructure, training, inference, and orchestration on
AWS.

| Sample | Description |
| --- | --- |
| [robotics-foundation-models-on-eks](robotics-foundation-models-on-eks/) | AWS sample for building robotics foundation model workflows on Amazon EKS, including NVIDIA OSMO deployment, infrastructure, examples, benchmarks, ingress, and observability guidance. |
| [finetuning-on-eks](finetuning-on-eks/) | Reusable infrastructure scaffold for distributed LLM fine-tuning on Amazon EKS. |
| [clm-8b-sagemaker](clm-8b-sagemaker/) | Deploy Contrastive-LM CLM-v0.1-8B (Qwen3-8B encoder in vLLM pooling mode plus the CLM projection heads) as a cost-optimised SageMaker AI real-time endpoint, with correctness checks and throughput, TTFT and cost benchmarks. |
| [strands-decider-2b-sagemaker](strands-decider-2b-sagemaker/) | Deploy Strands Decider 2B (typed yes/no, choice and score answers for agents) on SageMaker AI real-time endpoints on a T4 and on the cheapest 24 GB GPU with capacity, with correctness checks and throughput, TTFT and cost benchmarks. |
| [torchcast-decision-12b-sagemaker](torchcast-decision-12b-sagemaker/) | Deploy TorchCast Decision 12B (typed choice, yes/no and score decisions; weights CC BY-NC 4.0, non-commercial) on a SageMaker AI real-time endpoint on ml.g6e.xlarge, invoke it, and measure latency percentiles at 1, 10 and 64 concurrent requests. |
