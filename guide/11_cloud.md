# Module 11 — Cloud: AWS (locally) with infrastructure as code

⏱ 6–8 h · Services: `make up-stream up-cloud` · Check: `make check-11` (⏱ takes ~4 minutes: it creates and destroys 22 resources)
**Tools:** AWS (S3, IAM, networking, SNS/SQS), **OpenTofu** (the open fork of Terraform), **Floci** (an AWS emulator, MIT, no account; LocalStack is no longer free).

## What you will build
The platform's cloud infrastructure **described in code** (`infra/aws/*.tf`) and deployed with OpenTofu on Floci:
- **S3**: `landing` and `backups` buckets, with versioning, encryption and public access block.
- **Messaging**: an **SNS** topic that fans out to an **SQS** queue with its **dead-letter queue (DLQ)** and retries.
- **IAM**: a role for the notifier with **least privilege**.
- **Network**: a VPC with two subnets in different zones and a security group for the database.
- **Logging and secrets**: a log group with retention and a secret in Secrets Manager.
And `services/notifier`: a service that reads the alerts from Kafka and **publishes them to SNS** with an “at least once” guarantee.

## Why this module exists
Hand-made infrastructure (“I clicked in the console”) can't be reviewed, repeated or recovered. With code: it is reviewed in a pull request, the same environment is created
10 times identically and a disaster is recovered by running `apply`. And badly designed IAM is the most common cause of cloud breaches: here you practice **least privilege**.

We emulate AWS locally because you learn **the same APIs** (S3, SQS, IAM… with the same Terraform resources) without an account or costs. What you don't get is the reality of production: latencies, quotas, billing.

## Concepts
- **State**: OpenTofu compares the code with what really exists. `plan` shows the change; `apply` performs it. **Idempotency**: a second `plan` must say “no changes”.
- **Variables and `for_each`**: a single block that creates several similar resources. An `env` suffix on every name lets several environments live side by side.
- **Least privilege**: actions and resources **enumerated**, no wildcards (`*`).
- **SNS→SQS fan-out**: you publish once; each subscriber has its own queue and its own pace. The **DLQ** stops a “poison” message from blocking the queue.
- **“At least once” delivery**: you acknowledge the source message **only after** publishing successfully; if it fails, it is retried.
- **Default tags** (`default_tags`): costs and governance (whose resource is this?).

## The contract
**Variables**: `env` (suffix of ALL names; default `dev`) and `endpoint` (default `http://localhost:4566`). The AWS provider is configured for Floci
(`s3_use_path_style`, `skip_credentials_validation`, `skip_requesting_account_id`, endpoints for s3/sqs/sns/iam/sts/ec2/logs/secretsmanager) with `default_tags` `Project=plant`, `Env=<env>`.

| Resource | Name / rules |
|---|---|
| Buckets | `plant-landing-<env>`, `plant-backups-<env>`: versioning **Enabled**, AES256 encryption, public access block |
| Queue | `plant-alerts-<env>`, redrive to `plant-alerts-dlq-<env>` with `maxReceiveCount = 3` |
| Topic | `plant-alerts-<env>` with an **SQS subscription** (raw delivery) and a queue policy that only lets that topic send |
| IAM | role and policy `plant-notifier-<env>`: `sns:Publish` on the topic and `sqs:Receive/Delete/GetAttributes` on the queue. **No `*` in actions or resources.** Trust: the `ecs-tasks.amazonaws.com` service |
| Network | VPC `10.20.0.0/16` tagged `Name=plant-<env>`; **≥ 2 subnets in different AZs**; security group `plant-db-<env>` with ingress on 5432 **only from the VPC** (never `0.0.0.0/0`) |
| Logs / secrets | group `/plant/<env>/api` with retention; secret `plant/<env>/db-url` |
| Outputs | `topic_arn, queue_url, dlq_url, landing_bucket, role_arn` |

**Format**: a clean `tofu fmt -check`. **Notifier**: `services/notifier/notifier.py` with `publish_alert(alert, topic_arn=None)` and `python -m services.notifier.notifier`
(environment: `KAFKA_BOOTSTRAP`, `KAFKA_TOPIC_ALERTS`, `KAFKA_GROUP`, `ALERTS_TOPIC_ARN`, `IDLE_EXIT_SECONDS`; boto3 reads `AWS_ENDPOINT_URL`, credentials and region from the environment). If SNS rejects, the process **fails (code ≠ 0) without committing the offset**.

## Your task
1. Look at what Floci offers: `aws --endpoint-url http://localhost:4566 s3 ls` (or `boto3`). Create a bucket by hand and delete it: that way you'll know what OpenTofu will do for you.
2. `infra/aws/versions.tf`: provider, variables `env` and `endpoint`. `tofu init` (the first time it downloads the provider, ~200 MB; set `TF_PLUGIN_CACHE_DIR` so you don't repeat it).
3. Write `storage.tf`, `messaging.tf`, `iam.tf`, `network.tf`, `observability.tf` and `outputs.tf`. After each file: `tofu fmt`, `tofu validate`, `tofu plan -var env=test`.
4. `tofu apply -var env=test` and check with boto3 that the resources are what you asked for. Then `tofu plan` again: **it must say “No changes”**.
5. Break least privilege on purpose: change an action to `sns:*` and see how the check reacts; then put it back.
6. Write the notifier and test it: publish an alert to Kafka, run it and watch the message reach SQS.

## How you will know you are done
`make check-11` copies your `.tf` files to a temporary folder, runs `init`/`validate`/`apply` with a unique `env` against Floci, checks that a second `plan` proposes no changes, checks with boto3 the versioning, encryption, tags, redrive, subscription, **IAM policy documents**, network and logs,
runs your notifier **first with a nonexistent topic** (it must fail without losing messages) and then with the right one (the 3 alerts must reach the queue), and finally destroys everything.

## Comprehension questions
1. Why does it matter that `tofu plan` right after `apply` proposes no changes? What usually causes it when it does?
2. A teammate changes a bucket by hand in the console. What will happen on the next `plan`? How do you prevent it (SCPs, console permissions, GitOps)?
3. Why does the notifier commit the Kafka offset **after** publishing to SNS? What happens if you do it before?
4. What are the trade-offs between a low (3) and a high (100) `maxReceiveCount`?
5. Write in one sentence the risk of `Resource: "*"` in a policy whose role can publish to SNS.

## Hints
<details><summary>AWS provider for an emulator</summary>

Besides `endpoints { … }`, you need `skip_credentials_validation`, `skip_metadata_api_check`, `skip_requesting_account_id` and `s3_use_path_style = true`. Any test credentials (`test`/`test`).
</details>
<details><summary>Fan-out</summary>

SNS topic + SQS queue + `aws_sqs_queue_policy` (allows `sqs:SendMessage` from the topic ARN with the `aws:SourceArn` condition) + `aws_sns_topic_subscription` (protocol `sqs`, `raw_message_delivery = true`).
</details>
<details><summary>Readable policy documents</summary>

`data "aws_iam_policy_document"` gives you syntax validation and ready-to-use `.json`, better than hand-written JSON.
</details>
<details><summary>Tagging everything without repeating yourself</summary>

`default_tags { tags = { Project = "plant", Env = var.env } }` inside the `provider "aws"` block.
</details>

## Common traps
- Resource names without the `env` suffix (two environments collide).
- `Action: "*"` or `Resource: "*"` “to make it work”, and then forgetting it.
- A security group open to the world on the database port.
- OpenTofu's local state committed to git (it contains sensitive data); teams use a remote *backend* with locking.
- Committing the offset before publishing.

## On a real job
Reusable modules, environments separated by account, a remote backend with locking, `tofu plan` as an automatic pull-request comment, policies as code (OPA/Conftest) and cost estimates before applying. On real AWS: IAM Identity Center, SCPs, CloudTrail, a VPC with NAT and private endpoints.
**Courses:** *AWS Cloud Practitioner* (freeCodeCamp) and Uplimit's DevOps course (Terraform).

→ Next: [`12_containers_k8s.md`](12_containers_k8s.md)
