"""``HviKnowledgeStack`` — Stack 2 of the HVI Phase 2 CDK app.

Reproduces the web-relevant subset of ``scripts/26-opensearch.sh``,
``30-graph-lambda.sh``, ``31-graph-seed.sh`` and ``25-neptune.sh`` as a single
CDK stack: the ``hvi-logs`` OpenSearch domain, the in-VPC ``hvi-graph`` Lambda,
the Neptune cluster (gated behind ``hvi:enableNeptune``, default off), and the
Knowledge-owned seed custom resources (graph seed — flag-gated; vector-index
seed — always).

Everything is consumed from :class:`HviDataStack` through **typed props**
(the VPC, private subnets, security groups, Neptune subnet-group name, and the
``hvi-lambda-role``/``hvi-agentcore-role`` construct objects) — never by
re-looking-up names.

Deliberate deviations from the reference scripts (documented in ``DESIGN.md``):

* The OpenSearch engine version is a **pinned literal** (``hvi:opensearchEngine``,
  default ``OpenSearch_2.17``) rather than the script's ``list-versions``
  "newest available" lookup — that lookup is a synth-time AWS call this app must
  not make.
* Neptune, the graph Lambda's ``NEPTUNE_ENDPOINT`` wiring, and the graph-seed
  custom resource are all gated on ``hvi:enableNeptune`` (default off) to defer
  Neptune's ~$200/mo idle cost. With the flag off the graph Lambda is still
  created but has no usable ``NEPTUNE_ENDPOINT`` (fail-loud at call time).
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_neptune as neptune,
    aws_opensearchservice as opensearch,
    custom_resources as cr,
)
from constructs import Construct

from stacks.assets import vendored_asset_code
from stacks.context import HviContext
from stacks.data_stack import HviDataStack
from stacks.naming import Naming

# infra/cdk/stacks/knowledge_stack.py -> parents: [stacks, cdk, infra, <root>]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CDK_ROOT = Path(__file__).resolve().parents[1]
_SEED_DIR = _REPO_ROOT / "seed"
_SEED_RESOURCES = _CDK_ROOT / "seed_resources"
_GRAPH_LAMBDA_DIR = _REPO_ROOT / "lambdas" / "graph"
_TOOLS_DIR = _REPO_ROOT / "tools"

#: Cohere multilingual embedding model (EMBED_DIM 1024) — tools/opensearch_admin.py.
_EMBED_MODEL = "cohere.embed-multilingual-v3"
_NEPTUNE_PORT = "8182"


def _requests_package_dir() -> str:
    """Absolute path to the ``requests`` package in the synth interpreter.

    ``requests`` is pure-Python, so copying the installed package into the
    Lambda asset reproduces the script's ``pip install --target`` vendoring with
    no Docker and no network (a local filesystem copy).
    """
    spec = importlib.util.find_spec("requests")
    if spec is None or not spec.origin:
        raise RuntimeError(
            "requests must be importable in the synth venv to vendor it into "
            "the hvi-graph Lambda asset"
        )
    return str(Path(spec.origin).parent)


def _hash_files(paths) -> str:
    digest = hashlib.sha256()
    for path in sorted(str(p) for p in paths):
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


class HviKnowledgeStack(Stack):
    """OpenSearch + graph Lambda + (gated) Neptune + the Knowledge seeds."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        hvi: HviContext,
        data: HviDataStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.hvi = hvi
        self.data = data
        self.naming = Naming(account=hvi.account, region=hvi.region)

        self._build_opensearch_domain()
        self._build_neptune()  # gated; sets self.neptune_endpoint
        self._build_graph_lambda()
        self._build_graph_seed()  # gated
        self._build_vector_index_seed()  # always

    # ----------------------------------------------------------- opensearch
    def _build_opensearch_domain(self) -> None:
        n = self.naming
        hvi = self.hvi

        # PINNED engine version — no synth-time list-versions AWS call. Context
        # carries e.g. "OpenSearch_2.17"; the L2 wants just the "2.17" part.
        engine_token = hvi.opensearch_engine.split("_", 1)[1]
        version = opensearch.EngineVersion.open_search(engine_token)

        # Access policy admits ONLY the two roles and the deploying admin, for
        # es:ESHttp* on the domain. Resource is the deterministic domain ARN
        # (names are load-bearing), avoiding a self-referential policy.
        domain_arn = f"arn:aws:es:{hvi.region}:{hvi.account}:domain/{n.opensearch_domain}/*"
        access_policies = [
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[
                    iam.ArnPrincipal(self.data.lambda_role.role_arn),
                    iam.ArnPrincipal(self.data.agentcore_role.role_arn),
                    iam.ArnPrincipal(f"arn:aws:iam::{hvi.account}:user/adminCLI"),
                ],
                actions=["es:ESHttp*"],
                resources=[domain_arn],
            )
        ]

        self.opensearch_domain = opensearch.Domain(
            self,
            "LogsDomain",
            domain_name=n.opensearch_domain,
            version=version,
            capacity=opensearch.CapacityConfig(
                data_nodes=1,
                data_node_instance_type=hvi.opensearch_instance_type,
                master_nodes=0,
            ),
            zone_awareness=opensearch.ZoneAwarenessConfig(enabled=False),
            ebs=opensearch.EbsOptions(
                enabled=True,
                volume_size=10,
                volume_type=ec2.EbsDeviceVolumeType.GP3,
            ),
            node_to_node_encryption=True,
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            enforce_https=True,
            tls_security_policy=opensearch.TLSSecurityPolicy.TLS_1_2,
            access_policies=access_policies,
            # Holds the real hvi-call-transcripts audit trail in the production
            # account — never auto-destroyed.
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

    # -------------------------------------------------------------- neptune
    def _build_neptune(self) -> None:
        """Gated on ``hvi:enableNeptune`` (default off)."""
        self.neptune_cluster = None
        self.neptune_instance = None
        # Flag off -> graph Lambda has NO usable endpoint (fail-loud at call time).
        self.neptune_endpoint = ""

        if not self.hvi.enable_neptune:
            return

        n = self.naming
        cluster = neptune.CfnDBCluster(
            self,
            "NeptuneCluster",
            db_cluster_identifier=n.neptune_cluster,
            db_subnet_group_name=self.data.neptune_subnet_group_name,
            vpc_security_group_ids=[
                self.data.security_group_neptune.security_group_id
            ],
            iam_auth_enabled=True,
            storage_encrypted=True,
            backup_retention_period=1,
            db_port=8182,
        )
        instance = neptune.CfnDBInstance(
            self,
            "NeptuneInstance",
            db_instance_identifier=n.neptune_instance,
            db_instance_class="db.t4g.medium",
            db_cluster_identifier=cluster.ref,
        )
        instance.add_dependency(cluster)

        self.neptune_cluster = cluster
        self.neptune_instance = instance
        self.neptune_endpoint = cluster.attr_endpoint

    # --------------------------------------------------------- graph lambda
    def _build_graph_lambda(self) -> None:
        n = self.naming

        code = vendored_asset_code(
            _GRAPH_LAMBDA_DIR,
            extra_files={
                "health-kg.json": str(_SEED_DIR / "health-kg.json"),
                "requests": _requests_package_dir(),
            },
        )

        self.graph_fn = lambda_.Function(
            self,
            "GraphFn",
            function_name=n.graph_fn,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler="handler.lambda_handler",
            code=code,
            memory_size=512,
            timeout=Duration.seconds(300),
            role=self.data.lambda_role,  # scripts/30: hvi-lambda-role
            vpc=self.data.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=self.data.private_subnets),
            security_groups=[self.data.security_group_lambda],
            environment={
                # Key is always present so the module imports; empty when Neptune
                # is off, so a call fails loudly rather than silently misreading.
                "NEPTUNE_ENDPOINT": self.neptune_endpoint,
                "NEPTUNE_PORT": _NEPTUNE_PORT,
                "NEPTUNE_REGION": self.hvi.region,
            },
        )
        if self.neptune_instance is not None:
            # The endpoint attribute is only meaningful once the cluster exists.
            self.graph_fn.node.add_dependency(self.neptune_instance)

    # ----------------------------------------------------------- graph seed
    def _build_graph_seed(self) -> None:
        """Only when ``hvi:enableNeptune=true`` — invoke hvi-graph seed/stats."""
        self.graph_seed = None
        if not self.hvi.enable_neptune:
            return

        fn = lambda_.Function(
            self,
            "GraphSeedFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=vendored_asset_code(_SEED_RESOURCES / "graph_seed"),
            memory_size=256,
            timeout=Duration.minutes(10),
            vpc=self.data.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=self.data.private_subnets),
            security_groups=[self.data.security_group_lambda],
            environment={"GRAPH_FN": self.graph_fn.function_name},
        )
        # Least-privilege: lambda:InvokeFunction on hvi-graph only.
        self.graph_fn.grant_invoke(fn)

        provider = cr.Provider(self, "GraphSeedProvider", on_event_handler=fn)
        resource = cdk.CustomResource(
            self,
            "GraphSeedResource",
            service_token=provider.service_token,
            properties={
                "GraphFunctionName": self.graph_fn.function_name,
                "SeedHash": _hash_files([_SEED_DIR / "health-kg.json"]),
            },
        )
        resource.node.add_dependency(self.graph_fn)
        if self.neptune_instance is not None:
            resource.node.add_dependency(self.neptune_instance)
        self.graph_seed = resource

    # --------------------------------------------------- vector index seed
    def _build_vector_index_seed(self) -> None:
        """Always present — reuse tools/opensearch_admin.py (create + reload)."""
        hvi = self.hvi

        fn = lambda_.Function(
            self,
            "VectorIndexSeedFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=vendored_asset_code(
                _SEED_RESOURCES / "vector_index_seed",
                extra_files={
                    # Reused loader logic + its seed corpora, vendored verbatim.
                    "opensearch_admin.py": str(_TOOLS_DIR / "opensearch_admin.py"),
                    "health-kg.json": str(_SEED_DIR / "health-kg.json"),
                    "icd10-reference.json": str(_SEED_DIR / "icd10-reference.json"),
                    "distractors.json": str(_SEED_DIR / "distractors.json"),
                    "requests": _requests_package_dir(),
                },
            ),
            memory_size=512,
            timeout=Duration.minutes(10),
            vpc=self.data.vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=self.data.private_subnets),
            security_groups=[self.data.security_group_lambda],
            environment={
                "OPENSEARCH_ENDPOINT": self.opensearch_domain.domain_endpoint,
                "DATA_REGION": hvi.region,
                "EMBED_MODEL": _EMBED_MODEL,
            },
        )
        # Least-privilege: es:ESHttp* on the domain + Bedrock embed invoke.
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="VectorSeedEsHttp",
                actions=["es:ESHttp*"],
                resources=[
                    self.opensearch_domain.domain_arn,
                    f"{self.opensearch_domain.domain_arn}/*",
                ],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="VectorSeedBedrockEmbed",
                actions=["bedrock:InvokeModel"],
                resources=["*"],
            )
        )

        provider = cr.Provider(self, "VectorIndexSeedProvider", on_event_handler=fn)
        resource = cdk.CustomResource(
            self,
            "VectorIndexSeedResource",
            service_token=provider.service_token,
            properties={
                "SeedHash": _hash_files(
                    [
                        _SEED_DIR / "health-kg.json",
                        _SEED_DIR / "icd10-reference.json",
                        _SEED_DIR / "distractors.json",
                    ]
                ),
            },
        )
        # The embed+index work needs the domain Active first.
        resource.node.add_dependency(self.opensearch_domain)
        self.vector_index_seed = resource
