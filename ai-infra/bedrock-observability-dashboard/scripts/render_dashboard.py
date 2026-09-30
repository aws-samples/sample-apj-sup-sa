#!/usr/bin/env python3
"""Render the CloudWatch DashboardBody from cloudformation/dashboard.yaml for a
chosen tier, resolving the CloudFormation intrinsic-function subset this template
uses. Used by the test harness to validate per-tier structure without deploying.

Usage:
    python3 scripts/render_dashboard.py --tier 1
    python3 scripts/render_dashboard.py --tier 2 --param ModelInvocationLogGroup=/aws/bedrock/logs
"""
import argparse
import json
import sys

import yaml

PSEUDO_DEFAULTS = {
    "AWS::Region": "us-east-1",
    "AWS::AccountId": "123456789012",
    "AWS::Partition": "aws",
    "AWS::NoValue": None,
}

TIER_PARAMS = {
    1: {"ModelInvocationLogGroup": "", "CloudTrailLogGroup": ""},
    2: {"ModelInvocationLogGroup": "/aws/bedrock/test-logs", "CloudTrailLogGroup": ""},
    3: {"ModelInvocationLogGroup": "/aws/bedrock/test-logs",
        "CloudTrailLogGroup": "/aws/cloudtrail/test"},
}


class CfnLoader(yaml.SafeLoader):
    """Loader that turns !Ref / !Sub / !If etc. into {'Fn::...': ...} dicts.

    SECURITY: subclasses SafeLoader on purpose. SafeLoader refuses
    !!python/object and other arbitrary-type tags, so this cannot execute
    code. The only custom constructors registered below build plain
    dict/list/str values from CloudFormation short-form tags. Do NOT switch
    the base to yaml.Loader/FullLoader. (yaml.safe_load() can't be used
    directly because it doesn't recognise the !Ref/!Sub tags; subclassing
    SafeLoader is the standard cfn-lint/cfn-flip pattern.)"""


def _tag(loader, node, name):
    if isinstance(node, yaml.ScalarNode):
        return {name: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {name: loader.construct_sequence(node)}
    return {name: loader.construct_mapping(node)}


for short, full in [
    ("!Ref", "Ref"), ("!Sub", "Fn::Sub"), ("!If", "Fn::If"),
    ("!Equals", "Fn::Equals"), ("!Not", "Fn::Not"), ("!Join", "Fn::Join"),
    ("!FindInMap", "Fn::FindInMap"), ("!GetAtt", "Fn::GetAtt"),
]:
    CfnLoader.add_constructor(
        short, (lambda f: (lambda l, n: _tag(l, n, f)))(full)
    )


class Renderer:
    def __init__(self, template, params, pseudo):
        self.t = template
        self.params = params
        self.pseudo = pseudo
        self.conditions = {}
        self._eval_conditions()

    def _param(self, name):
        if name in self.params:
            return self.params[name]
        if name in self.pseudo:
            return self.pseudo[name]
        p = self.t.get("Parameters", {}).get(name, {})
        return str(p.get("Default", ""))

    def _eval_conditions(self):
        for name, expr in self.t.get("Conditions", {}).items():
            self.conditions[name] = self.eval(expr)

    def eval(self, node):
        if isinstance(node, dict):
            if "Ref" in node and len(node) == 1:
                return self._param(node["Ref"])
            if "Fn::Equals" in node:
                a, b = node["Fn::Equals"]
                return self.eval(a) == self.eval(b)
            if "Fn::Not" in node:
                return not self.eval(node["Fn::Not"][0])
            if "Fn::If" in node:
                cond, t_val, f_val = node["Fn::If"]
                chosen = t_val if self.conditions[cond] else f_val
                return self.eval(chosen)
            if "Fn::FindInMap" in node:
                m, k, attr = [self.eval(x) for x in node["Fn::FindInMap"]]
                return str(self.t["Mappings"][m][k][attr])
            if "Fn::Join" in node:
                sep, items = node["Fn::Join"]
                parts = [self.eval(i) for i in items]
                return self.eval(sep).join(p for p in parts if p is not None)
            if "Fn::Sub" in node:
                return self._sub(node["Fn::Sub"])
            # AWS::NoValue resolves to None; CloudFormation drops such keys/elements.
            return {k: ev for k, v in node.items()
                    if (ev := self.eval(v)) is not None}
        if isinstance(node, list):
            return [ev for i in node if (ev := self.eval(i)) is not None]
        return node

    def _sub(self, spec):
        if isinstance(spec, list):
            template_str, var_map = spec[0], spec[1]
            local = {k: self.eval(v) for k, v in var_map.items()}
        else:
            template_str, local = spec, {}
        out = template_str
        keys = sorted(
            list(local.keys()) + list(self.pseudo.keys())
            + list(self.t.get("Parameters", {}).keys()),
            key=len, reverse=True,
        )
        for k in keys:
            token = "${" + k + "}"
            if token in out:
                val = local.get(k)
                if val is None:
                    val = self.pseudo.get(k)
                if val is None:
                    val = self._param(k)
                out = out.replace(token, str(val))
        return out


def render(tier, extra_params):
    with open("cloudformation/dashboard.yaml") as fh:
        template = yaml.load(fh, Loader=CfnLoader)
    params = dict(TIER_PARAMS[tier])
    params.update(extra_params)
    r = Renderer(template, params, dict(PSEUDO_DEFAULTS))
    body_node = template["Resources"]["BedrockDashboard"]["Properties"]["DashboardBody"]
    return r.eval(body_node)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, choices=[1, 2, 3], required=True)
    ap.add_argument("--param", action="append", default=[],
                    help="Override as KEY=VALUE")
    args = ap.parse_args()
    extra = dict(kv.split("=", 1) for kv in args.param)
    body = render(args.tier, extra)
    json.loads(body)
    print(body)


if __name__ == "__main__":
    main()
