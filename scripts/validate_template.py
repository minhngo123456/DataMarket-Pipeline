"""Translate SAM offline and write the CloudFormation result for cfn-lint."""

import json
from copy import deepcopy
from pathlib import Path

import boto3
import yaml
from samtranslator.parser.parser import Parser
from samtranslator.translator.translator import Translator

ROOT = Path(__file__).resolve().parents[1]


class OfflinePolicies:
    def load(self):
        return {
            "AWSLambdaBasicExecutionRole": "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            "AWSLambdaSQSQueueExecutionRole": "arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole",
        }


def translate():
    session = boto3.Session(
        region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    template = deepcopy(
        yaml.safe_load((ROOT / "infra/aws/template.yaml").read_text(encoding="utf-8"))
    )
    for resource in template["Resources"].values():
        if resource["Type"] == "AWS::Serverless::Function":
            # sam build/package replaces this location during a real deployment.
            resource["Properties"]["CodeUri"] = {"Bucket": "offline-artifacts", "Key": "lambda.zip"}
    translator = Translator(None, Parser(), boto_session=session)
    return translator.translate(template, {}, get_managed_policy_map=OfflinePolicies().load)


if __name__ == "__main__":
    result = translate()
    target = ROOT / "build/cloudformation.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"SAM translated offline: {len(result['Resources'])} resources")
