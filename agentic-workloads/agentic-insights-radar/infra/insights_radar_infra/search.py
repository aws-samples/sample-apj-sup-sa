# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Vector search: S3 Vectors indexes for the graph and the product knowledge base."""

from __future__ import annotations

from aws_cdk import Aws
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3vectors as s3v
from constructs import Construct

EMBED_DIMENSIONS = 1024
GRAPH_INDEX = "nodes"
KB_INDEX = "knowledge"
DOCS_PREFIX = "docs/"


class Search(Construct):
    def __init__(
        self, scope: Construct, cid: str, prefix: str, key: kms.Key, assets: s3.Bucket
    ) -> None:
        super().__init__(scope, cid)
        region, account = Aws.REGION, Aws.ACCOUNT_ID
        self.embed_model_arn = f"arn:{Aws.PARTITION}:bedrock:{region}::foundation-model/amazon.titan-embed-text-v2:0"

        self.vector_bucket_name = f"{prefix}-vectors-{account}-{region}"
        # This deployment's vector bucket, not every vector bucket in the account: another team's
        # bucket must not be able to use this key, and this deployment's purge must not break theirs
        # (P2-074).
        key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowS3VectorsIndexing",
                principals=[iam.ServicePrincipal("indexing.s3vectors.amazonaws.com")],
                actions=["kms:Decrypt"],
                resources=["*"],
                conditions={
                    "ArnLike": {
                        "aws:SourceArn": f"arn:{Aws.PARTITION}:s3vectors:{region}:{account}:bucket/"
                        f"{self.vector_bucket_name}*"
                    },
                    "StringEquals": {"aws:SourceAccount": account},
                    "ForAnyValue:StringEquals": {
                        "kms:EncryptionContextKeys": [
                            "aws:s3vectors:arn",
                            "aws:s3vectors:resource-id",
                        ]
                    },
                },
            )
        )
        self.vector_bucket = s3v.CfnVectorBucket(
            self,
            "Vectors",
            vector_bucket_name=self.vector_bucket_name,
            encryption_configuration=s3v.CfnVectorBucket.EncryptionConfigurationProperty(
                sse_type="aws:kms", kms_key_arn=key.key_arn
            ),
        )
        self.vector_bucket_name = self.vector_bucket.vector_bucket_name or ""
        self.graph_index = s3v.CfnIndex(
            self,
            "GraphIndex",
            vector_bucket_arn=self.vector_bucket.attr_vector_bucket_arn,
            index_name=GRAPH_INDEX,
            data_type="float32",
            dimension=EMBED_DIMENSIONS,
            distance_metric="cosine",
            # Filters use type and insight_kind; titles are returned, never filtered on.
            metadata_configuration=s3v.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=["title"]
            ),
        )
        kb_index = s3v.CfnIndex(
            self,
            "KnowledgeIndex",
            vector_bucket_arn=self.vector_bucket.attr_vector_bucket_arn,
            index_name=KB_INDEX,
            data_type="float32",
            dimension=EMBED_DIMENSIONS,
            distance_metric="cosine",
            # Bedrock stores chunk text and metadata here; they exceed the filterable size limit.
            metadata_configuration=s3v.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=[
                    "AMAZON_BEDROCK_TEXT",
                    "AMAZON_BEDROCK_METADATA",
                ]
            ),
        )
        self.graph_index_arn = self.graph_index.attr_index_arn

        kb_role = iam.Role(
            self,
            "KnowledgeBaseRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:{Aws.PARTITION}:bedrock:{region}:{account}:knowledge-base/*"
                    },
                },
            ),
            description="Bedrock knowledge base: read product documents, embed, write vectors",
        )
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"], resources=[self.embed_model_arn]
            )
        )
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:PutVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:DeleteVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:GetIndex",
                ],
                resources=[kb_index.attr_index_arn],
            )
        )
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[assets.bucket_arn],
                conditions={"StringLike": {"s3:prefix": [f"{DOCS_PREFIX}*"]}},
            )
        )
        kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[assets.arn_for_objects(f"{DOCS_PREFIX}*")],
            )
        )
        key.grant_encrypt_decrypt(kb_role)

        self.knowledge_base = bedrock.CfnKnowledgeBase(
            self,
            "KnowledgeBase",
            name=f"{prefix}-products",
            description="Product documentation (web crawl per product) and uploaded internal documents",
            role_arn=kb_role.role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=self.embed_model_arn,
                    embedding_model_configuration=bedrock.CfnKnowledgeBase.EmbeddingModelConfigurationProperty(
                        bedrock_embedding_model_configuration=bedrock.CfnKnowledgeBase.BedrockEmbeddingModelConfigurationProperty(
                            dimensions=EMBED_DIMENSIONS, embedding_data_type="FLOAT32"
                        )
                    ),
                ),
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="S3_VECTORS",
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    index_arn=kb_index.attr_index_arn
                ),
            ),
        )
        self.knowledge_base.node.add_dependency(kb_role)
        self.documents = bedrock.CfnDataSource(
            self,
            "Documents",
            name="uploaded-documents",
            knowledge_base_id=self.knowledge_base.attr_knowledge_base_id,
            description="Documents uploaded per product, tagged with product_id metadata sidecars",
            data_deletion_policy="DELETE",
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=assets.bucket_arn, inclusion_prefixes=[DOCS_PREFIX]
                ),
            ),
            server_side_encryption_configuration=bedrock.CfnDataSource.ServerSideEncryptionConfigurationProperty(
                kms_key_arn=key.key_arn
            ),
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy="FIXED_SIZE",
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=512, overlap_percentage=15
                    ),
                )
            ),
        )
        self.kb_id = self.knowledge_base.attr_knowledge_base_id
        self.kb_arn = self.knowledge_base.attr_knowledge_base_arn
        self.documents_source_id = self.documents.attr_data_source_id
