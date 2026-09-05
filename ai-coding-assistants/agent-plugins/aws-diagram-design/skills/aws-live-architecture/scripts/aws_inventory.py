#!/usr/bin/env python3
"""Read-only inventory of one AWS account/region, emitted as a diagram IR digest.

The digest has the same shape as ``drawio_extract.py`` / ``mermaid_extract.py`` in the
sibling ``aws-diagram-design`` skill (containers, nodes, edges, hubs, budget flags), so the
agent can hand it to the architecture type reference and redraw it in the editorial
design system.

Only Describe*/List*/Get* calls are made. Nothing is created, modified, or deleted.
Every unavailable service (AccessDenied, opt-in region, throttling) is reported under
"skipped" and the inventory continues.

    python3 aws_inventory.py --region ap-northeast-2 [--profile NAME]
    python3 aws_inventory.py --region us-east-1 --vpc vpc-0123 --json --out ir.json
    python3 aws_inventory.py --region us-east-1 --services ec2,elbv2,ecs,rds

Self-contained on purpose: only boto3/botocore are imported, so the file can also be
pasted verbatim into the AWS MCP Server's sandboxed Python execution tool when no local
credentials exist.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "boto3 is required: pip install boto3  (or run this script through the AWS MCP "
        "Server's Python execution tool, where boto3 is preinstalled)\n"
    )
    sys.exit(2)

NODE_BUDGET = 9
EDGE_BUDGET = 12
FAITHFUL_CEILING = 24
ALL_SERVICES = (
    "ec2", "elbv2", "ecs", "eks", "lambda", "rds", "elasticache", "dynamodb",
    "s3", "sqs", "sns", "apigateway", "cloudfront", "kinesis", "opensearch",
)

# service key -> path under skills/aws-diagram-design/assets/aws-icons/
ICONS = {
    "vpc": "group/Virtual-private-cloud-VPC_32.svg",
    "public-subnet": "group/Public-subnet_32.svg",
    "private-subnet": "group/Private-subnet_32.svg",
    "region": "group/Region_32.svg",
    "aws-cloud": "group/AWS-Cloud_32.svg",
    "igw": "resource/Networking-Content-Delivery/Res_Amazon-VPC_Internet-Gateway_48.svg",
    "nat": "resource/Networking-Content-Delivery/Res_Amazon-VPC_NAT-Gateway_48.svg",
    "ec2": "service/Compute/Arch_Amazon-EC2_48.svg",
    "alb": "resource/Networking-Content-Delivery/Res_Elastic-Load-Balancing_Application-Load-Balancer_48.svg",
    "nlb": "resource/Networking-Content-Delivery/Res_Elastic-Load-Balancing_Network-Load-Balancer_48.svg",
    "gwlb": "resource/Networking-Content-Delivery/Res_Elastic-Load-Balancing_Gateway-Load-Balancer_48.svg",
    "ecs": "service/Containers/Arch_Amazon-Elastic-Container-Service_48.svg",
    "fargate": "service/Containers/Arch_AWS-Fargate_48.svg",
    "eks": "service/Containers/Arch_Amazon-Elastic-Kubernetes-Service_48.svg",
    "lambda": "service/Compute/Arch_AWS-Lambda_48.svg",
    "rds": "service/Databases/Arch_Amazon-RDS_48.svg",
    "aurora": "service/Databases/Arch_Amazon-Aurora_48.svg",
    "elasticache": "service/Databases/Arch_Amazon-ElastiCache_48.svg",
    "dynamodb": "service/Databases/Arch_Amazon-DynamoDB_48.svg",
    "s3": "service/Storage/Arch_Amazon-Simple-Storage-Service_48.svg",
    "sqs": "service/Application-Integration/Arch_Amazon-Simple-Queue-Service_48.svg",
    "sns": "service/Application-Integration/Arch_Amazon-Simple-Notification-Service_48.svg",
    "apigateway": "service/Networking-Content-Delivery/Arch_Amazon-API-Gateway_48.svg",
    "cloudfront": "service/Networking-Content-Delivery/Arch_Amazon-CloudFront_48.svg",
    "kinesis": "service/Analytics/Arch_Amazon-Kinesis-Data-Streams_48.svg",
    "opensearch": "service/Analytics/Arch_Amazon-OpenSearch-Service_48.svg",
}


@dataclass
class Node:
    id: str
    label: str
    service: str
    container: str | None = None
    sublabel: str = ""
    icon: str = ""
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class Container:
    id: str
    label: str
    kind: str  # aws-cloud | region | vpc | public-subnet | private-subnet | global
    parent: str | None = None
    sublabel: str = ""
    icon: str = ""


@dataclass
class Edge:
    source: str
    target: str
    label: str = ""
    style: str = ""  # "" | dashed


class Inventory:
    def __init__(self, session: boto3.Session, region: str, vpc_filter: str | None) -> None:
        self.session = session
        self.region = region
        self.vpc_filter = vpc_filter
        self.cfg = Config(retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=8, read_timeout=30)
        self.containers: dict[str, Container] = {}
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.skipped: list[str] = []
        self.subnet_kind: dict[str, str] = {}  # subnet-id -> public-subnet | private-subnet
        self.subnet_vpc: dict[str, str] = {}
        self.instance_node: dict[str, str] = {}  # instance-id -> node id
        self.tg_targets: dict[str, list[str]] = defaultdict(list)  # tg arn -> node ids
        self.tg_lb: dict[str, str] = {}  # tg arn -> lb node id
        self.lb_by_dns: dict[str, str] = {}
        self.lambda_by_arn: dict[str, str] = {}
        self.sqs_by_arn: dict[str, str] = {}
        self.bucket_nodes: dict[str, str] = {}
        self.api_by_id: dict[str, str] = {}
        self.stream_by_arn: dict[str, str] = {}
        self.table_stream_by_arn: dict[str, str] = {}

    # ----------------------------------------------------------------- helpers
    def client(self, name: str):
        return self.session.client(name, region_name=self.region, config=self.cfg)

    def run(self, key: str, fn) -> None:
        try:
            fn()
        except NoCredentialsError:
            raise
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            self.skipped.append(f"{key} ({code})")
        except BotoCoreError as exc:
            self.skipped.append(f"{key} ({type(exc).__name__})")

    def add_node(self, node: Node) -> str:
        if not node.icon:
            node.icon = ICONS.get(node.service, "")
        self.nodes[node.id] = node
        return node.id

    def add_edge(self, source: str | None, target: str | None, label: str = "", style: str = "") -> None:
        if not source or not target or source == target:
            return
        if source not in self.nodes or target not in self.nodes:
            return
        if any(e.source == source and e.target == target for e in self.edges):
            return
        self.edges.append(Edge(source, target, label, style))

    def in_scope(self, vpc_id: str | None) -> bool:
        return not self.vpc_filter or vpc_id == self.vpc_filter

    def subnet_container(self, subnet_ids: list[str]) -> str | None:
        """Pick one representative subnet container (first known), else its VPC."""
        for sid in subnet_ids:
            if sid in self.subnet_kind:
                return f"{self.subnet_vpc[sid]}/{self.subnet_kind[sid]}"
        for sid in subnet_ids:
            if sid in self.subnet_vpc:
                return self.subnet_vpc[sid]
        return None

    @staticmethod
    def name_tag(tags: list[dict[str, str]] | None, default: str) -> str:
        for t in tags or []:
            if t.get("Key") == "Name" and t.get("Value"):
                return t["Value"]
        return default

    # ---------------------------------------------------------------- services
    def inv_ec2(self) -> None:
        ec2 = self.client("ec2")
        region_c = self.containers["region"].id

        for vpc in ec2.describe_vpcs().get("Vpcs", []):
            vid = vpc["VpcId"]
            if not self.in_scope(vid):
                continue
            self.containers[vid] = Container(
                vid, self.name_tag(vpc.get("Tags"), vid), "vpc", region_c,
                sublabel=vpc.get("CidrBlock", ""), icon=ICONS["vpc"],
            )

        public_subnets: set[str] = set()
        for rt in ec2.describe_route_tables().get("RouteTables", []):
            has_igw = any(str(r.get("GatewayId", "")).startswith("igw-") for r in rt.get("Routes", []))
            if not has_igw:
                continue
            for assoc in rt.get("Associations", []):
                if assoc.get("SubnetId"):
                    public_subnets.add(assoc["SubnetId"])
                elif assoc.get("Main"):
                    public_subnets.add(f"main:{rt['VpcId']}")

        # Collapse subnets per VPC into one public + one private container.
        # A per-AZ layout is more than the complexity budget can hold; the sublabel
        # keeps the AZ count so the drawing can mention it.
        az_by_group: dict[str, set[str]] = defaultdict(set)
        for sn in ec2.describe_subnets().get("Subnets", []):
            vid, sid = sn["VpcId"], sn["SubnetId"]
            if vid not in self.containers:
                continue
            kind = "public-subnet" if (sid in public_subnets or f"main:{vid}" in public_subnets) else "private-subnet"
            self.subnet_kind[sid] = kind
            self.subnet_vpc[sid] = vid
            cid = f"{vid}/{kind}"
            az_by_group[cid].add(sn.get("AvailabilityZone", ""))
            if cid not in self.containers:
                self.containers[cid] = Container(
                    cid, "Public subnets" if kind == "public-subnet" else "Private subnets",
                    kind, vid, icon=ICONS[kind],
                )
        for cid, azs in az_by_group.items():
            self.containers[cid].sublabel = f"{len(azs)} AZ" + ("s" if len(azs) != 1 else "")

        for igw in ec2.describe_internet_gateways().get("InternetGateways", []):
            for att in igw.get("Attachments", []):
                vid = att.get("VpcId")
                if vid in self.containers:
                    self.add_node(Node(igw["InternetGatewayId"], "Internet Gateway", "igw", vid))

        for nat in ec2.describe_nat_gateways(Filter=[{"Name": "state", "Values": ["available"]}]).get("NatGateways", []):
            vid = nat.get("VpcId")
            if vid in self.containers:
                self.add_node(Node(nat["NatGatewayId"], "NAT Gateway", "nat", f"{vid}/public-subnet" if f"{vid}/public-subnet" in self.containers else vid))

        paginator = ec2.get_paginator("describe_instances")
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["running"]}]):
            for res in page.get("Reservations", []):
                for inst in res.get("Instances", []):
                    vid = inst.get("VpcId")
                    if vid not in self.containers:
                        continue
                    name = self.name_tag(inst.get("Tags"), inst["InstanceId"])
                    # Instances with the same Name in the same subnet kind are one node (an ASG fleet).
                    key = (self.subnet_container([inst.get("SubnetId", "")]) or vid, name)
                    groups[key].append(inst["InstanceId"])
        for (container, name), ids in groups.items():
            nid = f"ec2:{name}"
            sub = f"{len(ids)}× {name}" if len(ids) > 1 else ids[0]
            self.add_node(Node(nid, name, "ec2", container, sublabel=sub))
            for iid in ids:
                self.instance_node[iid] = nid

    def inv_elbv2(self) -> None:
        elb = self.client("elbv2")
        for lb in elb.describe_load_balancers().get("LoadBalancers", []):
            vid = lb.get("VpcId")
            if not self.in_scope(vid) or vid not in self.containers:
                continue
            kind = {"application": "alb", "network": "nlb", "gateway": "gwlb"}.get(lb.get("Type", ""), "alb")
            subnets = [az.get("SubnetId", "") for az in lb.get("AvailabilityZones", [])]
            nid = self.add_node(Node(
                f"lb:{lb['LoadBalancerName']}", lb["LoadBalancerName"], kind,
                self.subnet_container(subnets) or vid,
                sublabel=f"{kind.upper()} · {lb.get('Scheme', '')}",
            ))
            self.lb_by_dns[lb.get("DNSName", "").lower()] = nid
            if lb.get("Scheme") == "internet-facing":
                for n in self.nodes.values():
                    if n.service == "igw" and n.container == vid:
                        self.add_edge(n.id, nid, "HTTPS")
        for tg in elb.describe_target_groups().get("TargetGroups", []):
            for lb_arn in tg.get("LoadBalancerArns", []):
                lb_name = lb_arn.split("/")[-2] if lb_arn.count("/") >= 2 else ""
                if f"lb:{lb_name}" in self.nodes:
                    self.tg_lb[tg["TargetGroupArn"]] = f"lb:{lb_name}"
            if tg["TargetGroupArn"] not in self.tg_lb:
                continue
            try:
                health = elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"]).get("TargetHealthDescriptions", [])
            except ClientError:
                health = []
            for h in health:
                tid = h.get("Target", {}).get("Id", "")
                if tid in self.instance_node:
                    self.tg_targets[tg["TargetGroupArn"]].append(self.instance_node[tid])
                elif tid.startswith("arn:aws:lambda"):
                    self.tg_targets[tg["TargetGroupArn"]].append(f"lambda-arn:{tid}")
        for tg_arn, targets in self.tg_targets.items():
            for t in targets:
                if not t.startswith("lambda-arn:"):
                    self.add_edge(self.tg_lb[tg_arn], t, "FORWARD")

    def inv_ecs(self) -> None:
        ecs = self.client("ecs")
        for cluster_arn in ecs.list_clusters().get("clusterArns", []):
            cname = cluster_arn.split("/")[-1]
            service_arns: list[str] = []
            for page in ecs.get_paginator("list_services").paginate(cluster=cluster_arn):
                service_arns.extend(page.get("serviceArns", []))
            for i in range(0, len(service_arns), 10):
                for svc in ecs.describe_services(cluster=cluster_arn, services=service_arns[i:i + 10]).get("services", []):
                    if svc.get("status") != "ACTIVE":
                        continue
                    subnets = svc.get("networkConfiguration", {}).get("awsvpcConfiguration", {}).get("subnets", [])
                    vid = self.subnet_vpc.get(subnets[0]) if subnets else None
                    if self.vpc_filter and vid != self.vpc_filter:
                        continue
                    launch = svc.get("launchType") or ("FARGATE" if any(
                        s.get("capacityProvider", "").startswith("FARGATE") for s in svc.get("capacityProviderStrategy", [])) else "EC2")
                    nid = self.add_node(Node(
                        f"ecs:{cname}/{svc['serviceName']}", svc["serviceName"],
                        "fargate" if launch == "FARGATE" else "ecs",
                        self.subnet_container(subnets) or "region",
                        sublabel=f"ECS {launch} · {svc.get('desiredCount', 0)} tasks · {cname}",
                    ))
                    for lb in svc.get("loadBalancers", []):
                        tg_arn = lb.get("targetGroupArn")
                        if tg_arn in self.tg_lb:
                            self.add_edge(self.tg_lb[tg_arn], nid, "FORWARD")

    def inv_eks(self) -> None:
        eks = self.client("eks")
        for name in eks.list_clusters().get("clusters", []):
            c = eks.describe_cluster(name=name).get("cluster", {})
            vpc_cfg = c.get("resourcesVpcConfig", {})
            vid = vpc_cfg.get("vpcId")
            if not self.in_scope(vid):
                continue
            self.add_node(Node(
                f"eks:{name}", name, "eks",
                self.subnet_container(vpc_cfg.get("subnetIds", [])) or vid or "region",
                sublabel=f"EKS {c.get('version', '')}",
            ))

    def inv_lambda(self) -> None:
        lam = self.client("lambda")
        fns: list[dict[str, Any]] = []
        for page in lam.get_paginator("list_functions").paginate():
            fns.extend(page.get("Functions", []))
        # Group non-VPC functions by name prefix (before the first '-') when there are many.
        collapse = len(fns) > 6
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fn in fns:
            subnets = fn.get("VpcConfig", {}).get("SubnetIds", [])
            vid = fn.get("VpcConfig", {}).get("VpcId") or None
            if self.vpc_filter and vid != self.vpc_filter:
                continue
            key = fn["FunctionName"].split("-")[0] if collapse and not subnets else fn["FunctionName"]
            buckets[key].append(fn)
        for key, group in buckets.items():
            subnets = group[0].get("VpcConfig", {}).get("SubnetIds", [])
            container = self.subnet_container(subnets) or "region"
            label = key if len(group) == 1 else f"{key}-*"
            sub = group[0].get("Runtime", "") if len(group) == 1 else f"{len(group)} functions"
            nid = self.add_node(Node(f"lambda:{key}", label, "lambda", container, sublabel=sub))
            for fn in group:
                self.lambda_by_arn[fn["FunctionArn"]] = nid
                # versions/aliases resolve to the same node
                self.lambda_by_arn[fn["FunctionArn"].rsplit(":", 1)[0] if fn["FunctionArn"].count(":") > 6 else fn["FunctionArn"]] = nid
        for tg_arn, targets in self.tg_targets.items():
            for t in targets:
                if t.startswith("lambda-arn:"):
                    nid = self.lambda_by_arn.get(t[len("lambda-arn:"):])
                    self.add_edge(self.tg_lb[tg_arn], nid, "INVOKE")
        for page in lam.get_paginator("list_event_source_mappings").paginate():
            for m in page.get("EventSourceMappings", []):
                target = self.lambda_by_arn.get(m.get("FunctionArn", ""))
                src_arn = m.get("EventSourceArn", "")
                source = (self.sqs_by_arn.get(src_arn) or self.stream_by_arn.get(src_arn)
                          or self.table_stream_by_arn.get(src_arn.split("/stream/")[0]))
                self.add_edge(source, target, "EVENT", "dashed")

    def inv_rds(self) -> None:
        rds = self.client("rds")
        seen_cluster_members: set[str] = set()
        for page in rds.get_paginator("describe_db_clusters").paginate():
            for c in page.get("DBClusters", []):
                members = [m["DBInstanceIdentifier"] for m in c.get("DBClusterMembers", [])]
                seen_cluster_members.update(members)
                sg = c.get("DBSubnetGroup")
                subnets = self._rds_subnets(rds, sg) if sg else []
                vid = self.subnet_vpc.get(subnets[0]) if subnets else None
                if self.vpc_filter and vid != self.vpc_filter:
                    continue
                engine = c.get("Engine", "")
                self.add_node(Node(
                    f"rds:{c['DBClusterIdentifier']}", c["DBClusterIdentifier"],
                    "aurora" if "aurora" in engine else "rds",
                    self.subnet_container(subnets) or vid or "region",
                    sublabel=f"{engine} · {len(members)} instance{'s' if len(members) != 1 else ''}",
                ))
        for page in rds.get_paginator("describe_db_instances").paginate():
            for i in page.get("DBInstances", []):
                if i["DBInstanceIdentifier"] in seen_cluster_members:
                    continue
                vid = i.get("DBSubnetGroup", {}).get("VpcId")
                if not self.in_scope(vid):
                    continue
                subnets = [s["SubnetIdentifier"] for s in i.get("DBSubnetGroup", {}).get("Subnets", [])]
                self.add_node(Node(
                    f"rds:{i['DBInstanceIdentifier']}", i["DBInstanceIdentifier"], "rds",
                    self.subnet_container(subnets) or vid or "region",
                    sublabel=f"{i.get('Engine', '')} · {'Multi-AZ' if i.get('MultiAZ') else 'Single-AZ'}",
                ))

    @staticmethod
    def _rds_subnets(rds, subnet_group: str) -> list[str]:
        try:
            groups = rds.describe_db_subnet_groups(DBSubnetGroupName=subnet_group).get("DBSubnetGroups", [])
            return [s["SubnetIdentifier"] for g in groups for s in g.get("Subnets", [])]
        except ClientError:
            return []

    def inv_elasticache(self) -> None:
        ec = self.client("elasticache")
        subnet_groups: dict[str, list[str]] = {}
        for page in ec.get_paginator("describe_cache_subnet_groups").paginate():
            for g in page.get("CacheSubnetGroups", []):
                subnet_groups[g["CacheSubnetGroupName"]] = [s["SubnetIdentifier"] for s in g.get("Subnets", [])]
        in_rg: set[str] = set()
        for page in ec.get_paginator("describe_replication_groups").paginate():
            for rg in page.get("ReplicationGroups", []):
                in_rg.update(rg.get("MemberClusters", []))
                subnets: list[str] = []
                for m in rg.get("MemberClusters", [])[:1]:
                    try:
                        cl = ec.describe_cache_clusters(CacheClusterId=m).get("CacheClusters", [])
                        subnets = subnet_groups.get(cl[0].get("CacheSubnetGroupName", ""), []) if cl else []
                    except ClientError:
                        pass
                vid = self.subnet_vpc.get(subnets[0]) if subnets else None
                if self.vpc_filter and vid != self.vpc_filter:
                    continue
                self.add_node(Node(
                    f"elasticache:{rg['ReplicationGroupId']}", rg["ReplicationGroupId"], "elasticache",
                    self.subnet_container(subnets) or vid or "region",
                    sublabel=f"Redis · {len(rg.get('MemberClusters', []))} nodes",
                ))
        for page in ec.get_paginator("describe_cache_clusters").paginate():
            for cl in page.get("CacheClusters", []):
                if cl["CacheClusterId"] in in_rg:
                    continue
                subnets = subnet_groups.get(cl.get("CacheSubnetGroupName", ""), [])
                vid = self.subnet_vpc.get(subnets[0]) if subnets else None
                if self.vpc_filter and vid != self.vpc_filter:
                    continue
                self.add_node(Node(
                    f"elasticache:{cl['CacheClusterId']}", cl["CacheClusterId"], "elasticache",
                    self.subnet_container(subnets) or vid or "region",
                    sublabel=f"{cl.get('Engine', '')} · {cl.get('NumCacheNodes', 1)} nodes",
                ))

    def inv_dynamodb(self) -> None:
        if self.vpc_filter:
            return
        ddb = self.client("dynamodb")
        names: list[str] = []
        for page in ddb.get_paginator("list_tables").paginate():
            names.extend(page.get("TableNames", []))
        for name in names[:12]:
            try:
                t = ddb.describe_table(TableName=name).get("Table", {})
            except ClientError:
                t = {}
            nid = self.add_node(Node(f"dynamodb:{name}", name, "dynamodb", "region", sublabel="DynamoDB"))
            if t.get("LatestStreamArn"):
                self.table_stream_by_arn[t["TableArn"]] = nid
        if len(names) > 12:
            self.skipped.append(f"dynamodb: {len(names) - 12} more tables not listed (cap 12)")

    def inv_s3(self) -> None:
        if self.vpc_filter:
            return
        s3 = self.client("s3")
        buckets = s3.list_buckets().get("Buckets", [])
        kept = 0
        for b in buckets:
            if kept >= 8:
                self.skipped.append(f"s3: {len(buckets) - kept} more buckets not listed (cap 8)")
                break
            try:
                loc = s3.get_bucket_location(Bucket=b["Name"]).get("LocationConstraint") or "us-east-1"
            except ClientError:
                continue
            if loc != self.region:
                continue
            nid = self.add_node(Node(f"s3:{b['Name']}", b["Name"], "s3", "region", sublabel="S3 bucket"))
            self.bucket_nodes[b["Name"]] = nid
            kept += 1

    def inv_sqs(self) -> None:
        if self.vpc_filter:
            return
        sqs = self.client("sqs")
        urls: list[str] = []
        for page in sqs.get_paginator("list_queues").paginate():
            urls.extend(page.get("QueueUrls", []))
        for url in urls[:10]:
            name = url.rsplit("/", 1)[-1]
            try:
                arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
            except ClientError:
                arn = ""
            nid = self.add_node(Node(f"sqs:{name}", name, "sqs", "region", sublabel="SQS"))
            if arn:
                self.sqs_by_arn[arn] = nid

    def inv_sns(self) -> None:
        if self.vpc_filter:
            return
        sns = self.client("sns")
        topic_nodes: dict[str, str] = {}
        for page in sns.get_paginator("list_topics").paginate():
            for t in page.get("Topics", []):
                arn = t["TopicArn"]
                name = arn.rsplit(":", 1)[-1]
                topic_nodes[arn] = self.add_node(Node(f"sns:{name}", name, "sns", "region", sublabel="SNS topic"))
        for page in sns.get_paginator("list_subscriptions").paginate():
            for s in page.get("Subscriptions", []):
                src = topic_nodes.get(s.get("TopicArn", ""))
                ep = s.get("Endpoint", "")
                target = self.sqs_by_arn.get(ep) or self.lambda_by_arn.get(ep)
                self.add_edge(src, target, "PUBLISH", "dashed")

    def inv_apigateway(self) -> None:
        if self.vpc_filter:
            return
        v2 = self.client("apigatewayv2")
        for api in v2.get_apis().get("Items", []):
            nid = self.add_node(Node(
                f"apigw:{api['ApiId']}", api.get("Name", api["ApiId"]), "apigateway", "region",
                sublabel=f"API Gateway {api.get('ProtocolType', '')}",
            ))
            self.api_by_id[api["ApiId"]] = nid
            try:
                integrations = v2.get_integrations(ApiId=api["ApiId"]).get("Items", [])
            except ClientError:
                integrations = []
            for it in integrations:
                uri = it.get("IntegrationUri", "")
                if "lambda" in uri:
                    fn_arn = uri.split("/functions/")[-1].split("/invocations")[0] if "/functions/" in uri else uri
                    self.add_edge(nid, self.lambda_by_arn.get(fn_arn), "INVOKE")
        v1 = self.client("apigateway")
        for page in v1.get_paginator("get_rest_apis").paginate():
            for api in page.get("items", []):
                nid = self.add_node(Node(
                    f"apigw:{api['id']}", api.get("name", api["id"]), "apigateway", "region",
                    sublabel="API Gateway REST",
                ))
                self.api_by_id[api["id"]] = nid

    def inv_cloudfront(self) -> None:
        if self.vpc_filter:
            return
        cf = self.client("cloudfront")
        for page in cf.get_paginator("list_distributions").paginate():
            for d in page.get("DistributionList", {}).get("Items", []):
                aliases = d.get("Aliases", {}).get("Items", [])
                label = aliases[0] if aliases else d.get("DomainName", d["Id"])
                nid = self.add_node(Node(f"cloudfront:{d['Id']}", label, "cloudfront", "global", sublabel="CloudFront"))
                for o in d.get("Origins", {}).get("Items", []):
                    dom = o.get("DomainName", "").lower()
                    target = None
                    if ".s3" in dom:
                        target = self.bucket_nodes.get(dom.split(".s3")[0])
                    elif "elb.amazonaws.com" in dom:
                        target = self.lb_by_dns.get(dom)
                    elif "execute-api" in dom:
                        target = self.api_by_id.get(dom.split(".")[0])
                    self.add_edge(nid, target, "ORIGIN")

    def inv_kinesis(self) -> None:
        if self.vpc_filter:
            return
        kin = self.client("kinesis")
        for page in kin.get_paginator("list_streams").paginate():
            for s in page.get("StreamSummaries", []) or [{"StreamName": n} for n in page.get("StreamNames", [])]:
                name = s["StreamName"]
                nid = self.add_node(Node(f"kinesis:{name}", name, "kinesis", "region", sublabel="Kinesis Data Streams"))
                if s.get("StreamARN"):
                    self.stream_by_arn[s["StreamARN"]] = nid

    def inv_opensearch(self) -> None:
        os_ = self.client("opensearch")
        names = [d["DomainName"] for d in os_.list_domain_names().get("DomainNames", [])]
        if not names:
            return
        for d in os_.describe_domains(DomainNames=names[:5]).get("DomainStatusList", []):
            vpc = d.get("VPCOptions", {})
            vid = vpc.get("VPCId")
            if not self.in_scope(vid):
                continue
            self.add_node(Node(
                f"opensearch:{d['DomainName']}", d["DomainName"], "opensearch",
                self.subnet_container(vpc.get("SubnetIds", [])) or vid or "region",
                sublabel=f"OpenSearch {d.get('EngineVersion', '')}",
            ))

    # ------------------------------------------------------------------ driver
    def collect(self, services: list[str], account: str) -> None:
        self.containers["aws-cloud"] = Container("aws-cloud", "AWS Cloud", "aws-cloud", None, sublabel=account, icon=ICONS["aws-cloud"])
        self.containers["region"] = Container("region", self.region, "region", "aws-cloud", icon=ICONS["region"])
        self.containers["global"] = Container("global", "Global edge", "global", "aws-cloud")
        # Order matters: VPC/subnet maps first, then things that attach to them, then
        # the messaging layer whose edges resolve against already-known nodes.
        order = ["ec2", "elbv2", "ecs", "eks", "rds", "elasticache", "opensearch", "s3", "sqs",
                 "kinesis", "dynamodb", "lambda", "sns", "apigateway", "cloudfront"]
        for key in order:
            if key in services:
                self.run(key, getattr(self, f"inv_{key}"))
        # Drop the global container if nothing landed in it.
        if not any(n.container == "global" for n in self.nodes.values()):
            self.containers.pop("global", None)
        # Drop empty VPC/subnet containers.
        used = {n.container for n in self.nodes.values()}
        changed = True
        while changed:
            changed = False
            for cid in list(self.containers):
                c = self.containers[cid]
                if c.kind in ("vpc", "public-subnet", "private-subnet") and cid not in used and not any(
                        o.parent == cid for o in self.containers.values()):
                    del self.containers[cid]
                    changed = True

    # ------------------------------------------------------------------ output
    def ir(self, identity: dict[str, str]) -> dict[str, Any]:
        deg: Counter[str] = Counter()
        for e in self.edges:
            deg[e.source] += 1
            deg[e.target] += 1
        n = len(self.nodes)
        info = {
            "identity": identity,
            "region": self.region,
            "vpc_filter": self.vpc_filter,
            "node_count": n,
            "edge_count": len(self.edges),
            "over_node_budget": n > NODE_BUDGET,
            "over_edge_budget": len(self.edges) > EDGE_BUDGET,
            "needs_split": n > FAITHFUL_CEILING,
            "hubs": [nid for nid, _ in deg.most_common(5)],
            "unconnected": [nid for nid in self.nodes if deg[nid] == 0],
            "skipped": self.skipped,
            "icon_root": "skills/aws-diagram-design/assets/aws-icons/",
        }
        return {
            "info": info,
            "containers": [asdict(c) for c in self.containers.values()],
            "nodes": [asdict(x) for x in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges],
        }

    def digest(self, identity: dict[str, str]) -> str:
        ir = self.ir(identity)
        info = ir["info"]
        out = [f"# AWS inventory IR — {identity.get('account', '?')} / {self.region}", ""]
        out.append(f"- identity: `{identity.get('arn', '?')}` (read-only inventory)")
        if self.vpc_filter:
            out.append(f"- scope: VPC `{self.vpc_filter}` only")
        vpcs = [c for c in ir["containers"] if c["kind"] == "vpc"]
        out.append(f"- VPCs: {len(vpcs)}" + (" — " + ", ".join(f"{c['label']} ({c['id']})" for c in vpcs) if vpcs else ""))
        out.append(f"- nodes: {info['node_count']} / edges: {info['edge_count']}")
        budget = (f"nodes {'OVER' if info['over_node_budget'] else 'ok'} (max {NODE_BUDGET}), "
                  f"edges {'OVER' if info['over_edge_budget'] else 'ok'} (max {EDGE_BUDGET})")
        if info["needs_split"]:
            budget += f" — above {FAITHFUL_CEILING}: split into overview + per-VPC detail"
        elif info["over_node_budget"]:
            budget += " — use detail=faithful with zoned layout, or simplified (collapse fleets/buckets)"
        out.append(f"- budget: {budget}")
        if info["hubs"]:
            out.append("- hubs (focal candidates): " + ", ".join(f"{self.nodes[h].label}({Counter(e.source for e in self.edges)[h] + Counter(e.target for e in self.edges)[h]})" for h in info["hubs"] if h in self.nodes))
        if info["unconnected"]:
            shown = info["unconnected"][:12]
            more = len(info["unconnected"]) - len(shown)
            out.append("- unconnected (no inferred edge — confirm with the user or drop): "
                       + ", ".join(self.nodes[u].label for u in shown)
                       + (f", +{more} more" if more > 0 else ""))
        if self.skipped:
            out.append("- skipped: " + "; ".join(self.skipped))
        out += ["", "### Containers", "", "| id | label | kind | parent | sublabel | icon |", "|---|---|---|---|---|---|"]
        for c in ir["containers"]:
            out.append(f"| {c['id']} | {c['label']} | {c['kind']} | {c['parent'] or '-'} | {c['sublabel'] or '-'} | {c['icon'] or '-'} |")
        out += ["", "### Nodes", "", "| id | label | service | container | sublabel | icon |", "|---|---|---|---|---|---|"]
        for x in ir["nodes"]:
            out.append(f"| {x['id']} | {x['label']} | {x['service']} | {x['container'] or '-'} | {x['sublabel'] or '-'} | {x['icon'] or '-'} |")
        out += ["", "### Edges (inferred from configuration — not traffic)", "", "| source | target | label | style |", "|---|---|---|---|"]
        for e in ir["edges"]:
            out.append(f"| {self.nodes[e['source']].label} | {self.nodes[e['target']].label} | {e['label'] or '-'} | {e['style'] or '-'} |")
        out.append("")
        out.append(f"Icon paths are relative to `{info['icon_root']}` inside the plugin.")
        return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", required=True, help="AWS region to inventory, e.g. ap-northeast-2")
    p.add_argument("--profile", help="AWS named profile (prefer a read-only one)")
    p.add_argument("--vpc", help="restrict to one VPC id; regional services (S3, DynamoDB, …) are then omitted")
    p.add_argument("--services", default=",".join(ALL_SERVICES),
                   help="comma list from: " + ",".join(ALL_SERVICES))
    p.add_argument("--json", action="store_true", help="emit the full IR as JSON instead of the markdown digest")
    p.add_argument("--out", help="write to this path instead of stdout")
    args = p.parse_args(argv)

    services = [s.strip() for s in args.services.split(",") if s.strip()]
    unknown = sorted(set(services) - set(ALL_SERVICES))
    if unknown:
        p.error(f"unknown services: {', '.join(unknown)}")

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    try:
        who = session.client("sts", region_name=args.region).get_caller_identity()
    except NoCredentialsError:
        sys.stderr.write("No AWS credentials found. Run `aws login` / `aws sso login`, pass --profile, "
                         "or run this script through the AWS MCP Server instead.\n")
        return 3
    except ClientError as exc:
        sys.stderr.write(f"sts:GetCallerIdentity failed: {exc}\n")
        return 3
    identity = {"account": who.get("Account", ""), "arn": who.get("Arn", "")}

    inv = Inventory(session, args.region, args.vpc)
    inv.collect(services, identity["account"])
    text = json.dumps(inv.ir(identity), indent=2, default=str) if args.json else inv.digest(identity)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
