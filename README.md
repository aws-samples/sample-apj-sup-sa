## AWS APJ Startup Samples

Welcome. This repository has been prepared by Startup Solution Architects in AWS APJ to help startups easily discover additional resources, including sample code, workshops, and demos.

We hope this repository helps you navigate the available options and maximize the value of running on AWS. As always, if you have any questions, please don't hesitate to contact your local AWS startup team. If you're not already connected with them, you can reach out here:
https://aws.amazon.com/startups/contact-us


## What's Inside

The repository is organized by domain. Below is a summary of the areas covered:

| Domain | Areas Covered |
|--------|---------------|
| [Agentic Workloads](./agentic-workloads) | Agentic Patterns, Model Selection & Agent Platform |
| [Agentic Coding](./ai-coding-assistants) | Kiro, Claude Code, AI-DLC Practices, Kiro Autonomous & Frontier Agents |
| [Analytics](./analytics) | OpenSearch, Ingestion, Streaming, Processing, Cloud Data Warehouse, Data Lake, S3 Tables, QuickSight |
| [Databases](./databases) | Relational, Non-relational, Timestream, Vector Databases |
| [AI Infra](./ai-infra) | SageMaker, Fine-tuning, Inference, Training |
| [Modern Applications](./modern-applications) | AI on EKS, ML Orchestration, Containers, Serverless & Event-Driven Architecture |
| [Security & Compliance](./security) | Identity, IDP, Compliance, Cloud and Data Sovereignty |
| [Amazon Connect](./connect) | Voice, Call Centers & Back Offices |

## How to use this repository

This repository is large and contains many projects, making it a monorepo.  
To save time and disk space, we recommend cloning only the directories you need rather than downloading the entire repository.

You can do this using `git sparse-checkout`.

For example, if you want to clone the `agentic-workloads/sample-1` project, run:

   ```
   git clone https://github.com/aws-samples/sample-apj-sup-sa.git
   cd sample-apj-sup-sa
   git sparse-checkout init --cone
   git sparse-checkout set agentic-workloads/sample-1
   ```

For more details on `git sparse-checkout`, see:
https://github.blog/2020-01-17-bring-your-monorepo-down-to-size-with-sparse-checkout/


## Additional Resources

### Startup Build Solutions

In addition to the resources in this repository, [Startup Build Solutions](https://aws.amazon.com/startups/build) offers sample code and step-by-step guides on many of the topics covered here, tailored to help startups adopt them more effectively. We encourage you to explore it alongside this repository.

You may also find the following resources helpful as you build on AWS:

- [AWS Architecture Center](https://aws.amazon.com/architecture/) – Reference architectures and best practices
- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/) – Design principles for cloud workloads
- [AWS Solutions Library](https://aws.amazon.com/solutions/) – Vetted technical solutions and patterns
- [AWS Sample Code](https://github.com/aws-samples) – Official AWS sample code repositories


## Important Notes

- All samples in this repository are provided for reference purposes only.
- Please ensure a thorough security review before applying any sample to a production environment.
- Usage of AWS services may incur costs. Be sure to review the relevant pricing pages before deploying.


## License

This library is licensed under the MIT-0 License. See the LICENSE file for details. The `agentic-workloads/unicorn-rental-dataset/` folder is licensed under CC0-1.0 — see its own [LICENSE](agentic-workloads/unicorn-rental-dataset/LICENSE) file.
