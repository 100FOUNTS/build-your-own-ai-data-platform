"""Module 11 — Cloud (local AWS with Floci): infrastructure as code with OpenTofu, least-privilege IAM and SNS → SQS messaging."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import boto3
import pytest

from conftest import ROOT, kafka_topics, need, ok, py, sh, tail, unique

INFRA = ROOT / "infra" / "aws"
AWS = {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "us-east-1", "AWS_ENDPOINT_URL": "http://localhost:4566"}
TF_ENV = {"TF_PLUGIN_CACHE_DIR": str(Path.home() / ".cache" / "platform-tofu"), "TF_IN_AUTOMATION": "1", **AWS}


def client(name):
    return boto3.client(name, endpoint_url=AWS["AWS_ENDPOINT_URL"], aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")


def tofu(args, cwd, timeout=600):
    return sh(["tofu", *args], cwd=cwd, env=TF_ENV, timeout=timeout)


def test_format_and_syntax():
    ok(shutil.which("tofu"), "Install OpenTofu: `brew install opentofu`.")
    ok(list(INFRA.glob("*.tf")), "There are no .tf files in infra/aws/ (see guide/11_cloud.md).")
    r = tofu(["fmt", "-check", "-diff"], INFRA)
    ok(r.returncode == 0, "`tofu fmt -check` finds unformatted files (run `tofu fmt`): the canonical format avoids noise in reviews.\n" + tail(r, 8))


@pytest.fixture(scope="module")
def cloud(tmp_path_factory):
    need("floci", "redpanda")
    (Path.home() / ".cache" / "platform-tofu").mkdir(parents=True, exist_ok=True)
    work = tmp_path_factory.mktemp("tofu")
    for f in INFRA.glob("*.tf"):
        shutil.copy(f, work / f.name)
    env = unique("chk").replace("_", "-")[:12]
    r = tofu(["init", "-input=false"], work)
    ok(r.returncode == 0, "`tofu init` failed:\n" + tail(r))
    r = tofu(["validate"], work)
    ok(r.returncode == 0, "`tofu validate` failed:\n" + tail(r))
    a = tofu(["apply", "-auto-approve", "-input=false", "-var", f"env={env}"], work, timeout=900)
    ok(a.returncode == 0, "`tofu apply` against Floci failed. Last lines:\n" + tail(a, 14) + "\n\nContract: the `env` variable must exist and ALL names must carry it as a suffix.")
    out = tofu(["output", "-json"], work)
    outputs = {k: v["value"] for k, v in json.loads(out.stdout).items()}
    yield {"work": work, "env": env, "out": outputs}
    tofu(["destroy", "-auto-approve", "-input=false", "-var", f"env={env}"], work, timeout=900)


def test_outputs_and_idempotency(cloud):
    need_out = {"topic_arn", "queue_url", "dlq_url", "landing_bucket", "role_arn"}
    ok(need_out <= set(cloud["out"]), f"Missing OpenTofu outputs: {sorted(need_out - set(cloud['out']))}. Other teams and your application consume them.")
    r = tofu(["plan", "-detailed-exitcode", "-input=false", "-var", f"env={cloud['env']}"], cloud["work"])
    ok(r.returncode == 0, "IDEMPOTENCY: a second `tofu plan` right after the apply proposes changes. Infrastructure as code must converge and stay still.\n" + tail(r, 14))


def test_storage_versioned_encrypted_and_tagged(cloud):
    s3 = client("s3")
    for kind in ("landing", "backups"):
        b = f"plant-{kind}-{cloud['env']}"
        ok(s3.get_bucket_versioning(Bucket=b).get("Status") == "Enabled", f"The bucket {b} must have versioning: it is your protection against accidental deletes and overwrites.")
        try:
            enc = s3.get_bucket_encryption(Bucket=b)["ServerSideEncryptionConfiguration"]["Rules"][0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
        except Exception:  # noqa: BLE001
            enc = None
        ok(enc in ("AES256", "aws:kms"), f"The bucket {b} must be encrypted at rest (AES256 or KMS).")
        tags = {t["Key"]: t["Value"] for t in s3.get_bucket_tagging(Bucket=b)["TagSet"]}
        ok(tags.get("Project") == "plant" and tags.get("Env") == cloud["env"], f"The bucket {b} must carry the tags Project=plant and Env=<environment> (costs and governance). Use the provider's default_tags. It has: {tags}")


def test_messaging_with_a_dead_letter_queue(cloud):
    sqs = client("sqs")
    a = sqs.get_queue_attributes(QueueUrl=cloud["out"]["queue_url"], AttributeNames=["All"])["Attributes"]
    ok("RedrivePolicy" in a, "The main queue needs a redrive policy to a dead-letter queue (DLQ).")
    rp = json.loads(a["RedrivePolicy"])
    ok(rp["maxReceiveCount"] == 3 and "plant-alerts-dlq" in rp["deadLetterTargetArn"], f"Expected redrive: maxReceiveCount=3 to the DLQ plant-alerts-dlq-<env> (you have {rp}). A “poison” message must not block the queue forever.")
    sns = client("sns")
    subs = sns.list_subscriptions_by_topic(TopicArn=cloud["out"]["topic_arn"])["Subscriptions"]
    ok(any(s["Protocol"] == "sqs" and "plant-alerts-" in s["Endpoint"] for s in subs), "The SNS topic must have a subscription to the SQS queue (fan-out: you publish once, each consumer has its own queue).")


def test_iam_least_privilege(cloud):
    iam = client("iam")
    role = f"plant-notifier-{cloud['env']}"
    trust = iam.get_role(RoleName=role)["Role"]["AssumeRolePolicyDocument"]
    for st in trust["Statement"]:
        ok(st.get("Principal") != "*" and st.get("Principal", {}).get("AWS") != "*", "The role's trust policy lets ANYONE assume it (Principal \"*\").")
    attached = iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]
    ok(attached, "The notifier role has no attached policy.")
    allowed_actions: set[str] = set()
    for p in attached:
        doc = iam.get_policy_version(PolicyArn=p["PolicyArn"], VersionId=iam.get_policy(PolicyArn=p["PolicyArn"])["Policy"]["DefaultVersionId"])["PolicyVersion"]["Document"]
        for st in doc["Statement"] if isinstance(doc["Statement"], list) else [doc["Statement"]]:
            acts = [st["Action"]] if isinstance(st["Action"], str) else st["Action"]
            res = [st["Resource"]] if isinstance(st["Resource"], str) else st["Resource"]
            ok(not any(a == "*" or a.endswith(":*") for a in acts), f"Wildcard actions in the policy: {acts}. List only what is needed (sns:Publish, sqs:ReceiveMessage…).")
            ok("*" not in res, "The policy uses Resource \"*\": limit it to the specific ARNs of the topic and the queue.")
            allowed_actions.update(acts)
    ok("sns:Publish" in allowed_actions, "The notifier must be able to publish to SNS (sns:Publish).")
    ok(not allowed_actions & {"iam:*", "s3:*", "sns:DeleteTopic", "sqs:DeleteQueue"}, "The role has destructive permissions it doesn't need.")


def test_private_network(cloud):
    ec2 = client("ec2")
    vpc = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [f"plant-{cloud['env']}"]}])["Vpcs"]
    ok(vpc, f"Cannot find the VPC tagged Name=plant-{cloud['env']}.")
    subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc[0]["VpcId"]]}])["Subnets"]
    ok(len({s["AvailabilityZone"] for s in subs}) >= 2, "At least 2 subnets in different availability zones are expected (high availability: a zone can go down).")
    sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc[0]["VpcId"]]}, {"Name": "group-name", "Values": [f"plant-db-{cloud['env']}"]}])["SecurityGroups"]
    ok(sgs, "Missing the security group plant-db-<env>.")
    cidrs = [r["CidrIp"] for p in sgs[0]["IpPermissions"] for r in p["IpRanges"]]
    ok(cidrs and "0.0.0.0/0" not in cidrs, f"The database security group opens the port to the world ({cidrs}). A database is never public: restrict it to the VPC CIDR.")


def test_logging_with_retention_and_secret(cloud):
    logs = client("logs")
    lg = [g for g in logs.describe_log_groups(logGroupNamePrefix=f"/plant/{cloud['env']}")["logGroups"]]
    ok(lg and lg[0].get("retentionInDays"), "The log group /plant/<env>/api needs a defined retention (without retention it piles up and you pay for it forever).")
    sm = client("secretsmanager")
    names = [s["Name"] for s in sm.list_secrets()["SecretList"]]
    ok(f"plant/{cloud['env']}/db-url" in names, "Missing the secret plant/<env>/db-url in Secrets Manager (the real value is loaded outside the code).")


# ------------------------------------------------------------------ the application
def drain(queue_url, want, timeout=20):
    sqs = client("sqs")
    got, end = [], time.time() + timeout
    while len(got) < want and time.time() < end:
        for m in sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=2).get("Messages", []):
            got.append(json.loads(m["Body"]))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    return got


def test_notifier_kafka_to_sns_to_sqs(cloud):
    ok((ROOT / "services" / "notifier" / "notifier.py").exists(), "Missing services/notifier/notifier.py (consumes alerts from Kafka and publishes them to SNS).")
    from confluent_kafka import Producer

    topic = unique("chk.notif")
    kafka_topics(topic)
    p = Producer({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP"]})
    for i in (101, 102, 103):
        p.produce(topic, key="PLT01-P107", value=json.dumps({"alert_id": i, "asset_id": "PLT01-P107", "severity": "HIGH", "risk_score": 0.9}).encode())
    p.flush()
    group = unique("g")
    base = {**AWS, "KAFKA_TOPIC_ALERTS": topic, "KAFKA_GROUP": group, "IDLE_EXIT_SECONDS": "6"}
    bad = py(["-m", "services.notifier.notifier"], env={**base, "ALERTS_TOPIC_ARN": "arn:aws:sns:us-east-1:000000000000:no-such-topic"}, timeout=120)
    ok(bad.returncode != 0, "If SNS rejects the publish (nonexistent topic) the notifier must FAIL visibly (code ≠ 0), not swallow the error.")
    ok(drain(cloud["out"]["queue_url"], 1, 5) == [], "With a nonexistent topic nothing should have reached the queue.")
    good = py(["-m", "services.notifier.notifier"], env={**base, "ALERTS_TOPIC_ARN": cloud["out"]["topic_arn"]}, timeout=120)
    ok(good.returncode == 0, "The notifier failed with a valid topic:\n" + tail(good))
    got = drain(cloud["out"]["queue_url"], 3)
    ok(sorted(m["alert_id"] for m in got) == [101, 102, 103], f"After the failure and the retry, the 3 alerts should reach the queue (got {sorted(m.get('alert_id') for m in got)}). If you didn't commit the offset after the error, the retry recovers them: that is “at least once”.")
    again = py(["-m", "services.notifier.notifier"], env={**base, "ALERTS_TOPIC_ARN": cloud["out"]["topic_arn"]}, timeout=120)
    ok(drain(cloud["out"]["queue_url"], 1, 6) == [], "Rerunning with the same group must not republish what was already notified (commit offsets).")
