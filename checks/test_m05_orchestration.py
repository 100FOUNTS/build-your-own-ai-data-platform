"""Module 5 — Orchestration with Dagster: the whole pipeline as an asset graph, tested end to end, plus the MLOps closed loop (retrain, compare, promote)."""

from __future__ import annotations

import ast
import importlib
import os
import sys

from unittest import mock

import duckdb
import pandas as pd
import psycopg
import pytest
from dagster import AssetSelection, DagsterInstance, Definitions, build_sensor_context, define_asset_job
from mlflow import MlflowClient

from conftest import ROOT, drop_namespace, need, ok, py, seeded_db, sh, tail, unique
from expected import M5_DECISIONS, M5_DRIFT_DAYS, M5_MARGIN, M5_RETRAIN_JOB, M5_SHIFT_FACTOR, M5_TAG_CHALLENGER_AP, M5_TAG_CHAMPION_AP, M5_TAG_DECISION, expected_gold, m5_degraded_gold, m5_shifted_gold, m5_stationary_gold

MODULE = "pipelines.dagster.definitions"


def load_defs():
    err, mod = None, None
    try:
        mod = importlib.import_module(MODULE)
    except Exception as exc:  # noqa: BLE001
        err = exc
    ok(err is None, f"Cannot import pipelines/dagster/definitions.py ({err!r}).\nIt must expose `defs = Definitions(assets=[...])` (guide/05_orchestration.md).")
    ok(hasattr(mod, "defs"), "definitions.py must define `defs = Definitions(...)`.")
    return mod.defs


def chain_depth(graph) -> int:
    keys = list(graph.get_all_asset_keys())
    parents = {k: list(graph.get(k).parent_keys) for k in keys}
    memo: dict = {}

    def depth(k) -> int:
        if k not in memo:
            memo[k] = 1 + max([depth(p) for p in parents[k] if p in parents] or [0])
        return memo[k]

    return max(depth(k) for k in keys)


def test_the_graph_chains_the_layers():
    g = load_defs().resolve_asset_graph()
    n = len(list(g.get_all_asset_keys()))
    ok(n >= 4, f"Your graph has {n} asset(s); at least 4 are expected (bronze → dbt → model → scores).")
    d = chain_depth(g)
    ok(d >= 4, f"The longest dependency chain has length {d} and must be ≥ 4. Without declared dependencies (deps=[...]) Dagster doesn't know in what order to run, or what to rerun when something changes.")
    descs = [g.get(k).description for k in g.get_all_asset_keys()]
    ok(all(d and len(d) > 15 for d in descs), "Every asset must have a description (what it produces and from where): the data catalog starts there.")


def test_has_checks_and_a_schedule():
    defs = load_defs()
    g = defs.resolve_asset_graph()
    ok(list(g.asset_check_keys), "Add at least one Dagster *asset check* (data quality: rows, duplicates, ranges…). A green pipeline with bad data is worse than a red one.")
    ok(list(defs.schedules or []), "Define a schedule (ScheduleDefinition) that runs the pipeline every day at 06:00 UTC.")


def test_end_to_end_run_in_isolation(tmp_path):
    """Materializes the WHOLE graph with Dagster, in a new namespace, warehouse and database: your real data is not touched."""
    need("iceberg", "s3", "postgres", "mlflow", "redpanda")
    ns = unique("bronze")
    wh = tmp_path / "warehouse.duckdb"
    home = tmp_path / "dagster_home"
    home.mkdir()
    try:
        with seeded_db() as url:
            env = {"DAGSTER_HOME": str(home), "LAKE_NS": ns, "WAREHOUSE_PATH": str(wh), "DATABASE_URL": url,
                   "MLFLOW_EXPERIMENT": unique("dagster"), "MODEL_NAME": unique("pump-risk-dg"), "PYTHONPATH": str(ROOT)}
            r = sh([sys.executable, "-m", "dagster", "asset", "materialize", "-m", MODULE, "--select", "*"], env=env, timeout=1500)
            ok(r.returncode == 0, "`dagster asset materialize --select '*'` failed. Last lines:\n" + tail(r, 14)
               + "\n\nIf it was because of a hard-coded path or name in your code: use the variables LAKE_NS, WAREHOUSE_PATH, DATABASE_URL…")
            ok(wh.exists(), "The run finished but didn't create the warehouse given by WAREHOUSE_PATH (does your dbt read it from the environment?).")
            con = duckdb.connect(str(wh), read_only=True)
            n = con.execute("select count(*) from asset_daily_features").fetchone()[0]
            con.close()
            ok(n == 1080, f"The gold produced by Dagster has {n} rows (expected 1,080).")
            with psycopg.connect(url) as c:
                scored = c.execute("select count(*) from risk_scores").fetchone()[0]
            ok(scored == 1080, f"risk_scores has {scored} rows after the run (expected 1,080).")
    finally:
        drop_namespace(ns)


# ================================================================== the MLOps loop: retrain, compare, promote
# The check builds three variants of gold with an independent computation (checks/expected.py), then drives YOUR sensor, YOUR drift
# checks and YOUR `retrain_job` in-process, against the real MLflow. Nothing of your real data, experiments or models is touched.


def fresh_defs():
    """Imports your definitions again so that environment variables set by the check are the ones your code sees."""
    load_defs()
    return importlib.reload(sys.modules[MODULE]).defs


def retrain_sensors(defs) -> list:
    found = []
    for s in defs.sensors or []:
        try:
            targets = {getattr(t, "job_name", None) for t in s.targets}
        except Exception:  # noqa: BLE001
            targets = set()
        if M5_RETRAIN_JOB in targets:
            found.append(s)
    return found


def write_gold(path, df) -> None:
    con = duckdb.connect(str(path))
    con.register("g", df)
    con.execute("create or replace table asset_daily_features as select * from g")
    con.close()


def step_errors(result) -> str:
    out = []
    for ev in result.get_step_failure_events():
        err = ev.event_specific_data.error
        out.append(f"{ev.step_key}: {err.message.strip()[-700:]}" if err else str(ev.step_key))
    return "\n".join(out) or "(no step reported an error)"


def require_retrain_job(defs):
    """Fails with a clear message if there is no job called `retrain_job`; returns it otherwise."""
    job = None
    try:
        job = defs.resolve_job_def(M5_RETRAIN_JOB)
    except Exception:  # noqa: BLE001
        pass
    ok(job is not None, f"There is no job named `{M5_RETRAIN_JOB}` in your Definitions. It is the job that trains a challenger, compares it with the champion and decides.\nWithout it, nothing can retrain the model on its own (guide/05_orchestration.md, “The closed loop”).")
    return job


def test_the_retrain_loop_is_declared():
    defs = load_defs()
    require_retrain_job(defs)
    ok(retrain_sensors(defs), f"No sensor targets `{M5_RETRAIN_JOB}`. Something has to start the loop when the world changes (new data, a failed quality check): a job nobody triggers is just another command.")


@pytest.fixture(scope="module")
def gold():
    return expected_gold()


class Ticker:
    """Evaluates your sensor tick after tick, like the Dagster daemon would, and reports only the requests that would create a NEW run."""

    def __init__(self, defs, sensor, instance):
        self.defs, self.sensor, self.instance, self.cursor, self.seen = defs, sensor, instance, None, set()

    def tick(self) -> list:
        ctx = build_sensor_context(instance=self.instance, cursor=self.cursor, repository_def=self.defs.get_repository_def())
        try:
            data = self.sensor.evaluate_tick(ctx)
        except Exception as exc:  # noqa: BLE001
            ok(False, f"Your sensor `{self.sensor.name}` raised an error when it was evaluated: {exc!r}\nA crashing sensor stops the whole loop silently.")
        self.cursor = data.cursor
        fresh = [r for r in data.run_requests if r.run_key is None or r.run_key not in self.seen]
        self.seen |= {r.run_key for r in data.run_requests if r.run_key}
        return fresh


def run_all_checks(defs, instance) -> list:
    """Runs only the asset checks (no upstream rebuild) and returns the evaluations."""
    keys = list(defs.resolve_asset_graph().asset_check_keys)
    job = define_asset_job("m5_only_checks", selection=AssetSelection.checks(*keys))
    only = Definitions(assets=defs.assets, asset_checks=defs.asset_checks, jobs=[job], resources=defs.resources)
    res = only.resolve_job_def("m5_only_checks").execute_in_process(instance=instance, raise_on_error=False)
    ok(res.success, "Your asset checks could not run on their own:\n" + step_errors(res)
       + "\nA check must read the data it judges from WAREHOUSE_PATH, not receive it from a materialization: the loop evaluates it without rebuilding the pipeline.")
    return list(res.get_asset_check_evaluations())


def test_sensor_and_drift_checks_drive_the_loop(gold, tmp_path):
    defs = fresh_defs()
    sensors = retrain_sensors(defs)
    ok(sensors, f"No sensor targets `{M5_RETRAIN_JOB}` (see test_the_retrain_loop_is_declared).")
    ok(list(defs.resolve_asset_graph().asset_check_keys), "There are no asset checks: the loop needs a quality/drift check whose failure can start a retrain.")
    stable = m5_stationary_gold(gold)
    last = stable["feature_date"].max()
    wh = tmp_path / "loop.duckdb"
    write_gold(wh, stable[stable["feature_date"] < last])
    with mock.patch.dict(os.environ, {"WAREHOUSE_PATH": str(wh)}):
        inst = DagsterInstance.ephemeral()
        t = Ticker(defs, sensors[0], inst)
        t.tick()  # first sight of the data: whether it fires or only records what it saw is your design
        again = t.tick()
        ok(not again, f"With no new data, a second evaluation of the sensor asked for {len(again)} more run(s). A sensor that fires on every tick retrains forever: remember what it has already acted on (cursor, or a run_key Dagster can deduplicate).")

        write_gold(wh, stable)  # one new day (partition) arrives
        new = t.tick()
        ok(new, "A new day of data arrived in `asset_daily_features` and the sensor asked for no run. The model would silently go stale while the data moves on.")
        again = t.tick()
        ok(not again, "After acting on the new day, the next evaluation asked for another run for the same data (not idempotent).")

        evals = run_all_checks(defs, inst)
        failed = [e.check_name for e in evals if not e.passed]
        ok(not failed, f"Your check(s) {failed} fail on gold whose features come from one single distribution (no drift by construction).\nA check that cries wolf on stable data retrains the model every day and teaches everyone to ignore it. Is your PSI over the whole table? Are the two windows big enough to be stable?")
        quiet = t.tick()
        ok(not quiet, "The sensor asked for a run although every asset check passed and there was no new data.")

        write_gold(wh, m5_shifted_gold(stable))  # same days, but the last 14 have a unit change on every feature
        evals = run_all_checks(defs, inst)
        failed = [e.check_name for e in evals if not e.passed]
        ok(failed, f"No asset check failed after every feature of the last {M5_DRIFT_DAYS} days was multiplied by {M5_SHIFT_FACTOR} (a silent unit change).\nAdd a drift check to module 5: the PSI of a few features between an older reference window and the latest window, over the whole table, failing above a threshold.")
        fired = t.tick()
        ok(fired, f"Asset check(s) {failed} failed and the sensor asked for no run. A failed check is exactly the signal that the model may now be looking at a different world.")
        again = t.tick()
        ok(not again, "The same failed check started a second run on the next evaluation. Remember which evaluation you already reacted to.")


def tagged_runs(exp_name: str) -> list:
    c = MlflowClient()
    e = c.get_experiment_by_name(exp_name)
    if e is None:
        return []
    runs = c.search_runs([e.experiment_id], filter_string=f"tags.`{M5_TAG_DECISION}` != ''", order_by=["attributes.start_time ASC"], max_results=100)
    return list(runs)


class Loop:
    """One scenario: its own MLflow experiment and model name, and a way to run YOUR job against a given warehouse."""

    def __init__(self, tag: str):
        self.exp, self.model = unique(f"m5-{tag}"), unique(f"m5-{tag}-model")
        self.seen: set[str] = set()
        self.runs: list = []  # the new tagged run of each execution, in order

    def champion(self):
        try:
            return MlflowClient().get_model_version_by_alias(self.model, "champion")
        except Exception:  # noqa: BLE001
            return None

    def execute(self, warehouse, margin: float | None = M5_MARGIN, extra: dict | None = None):
        env = {"WAREHOUSE_PATH": str(warehouse), "MLFLOW_EXPERIMENT": self.exp, "MODEL_NAME": self.model, "PYTHONPATH": str(ROOT),
               "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "false",  # model downloads go through the MLflow server, not presigned URLs to a Docker-only hostname
               **(extra or {})}
        with mock.patch.dict(os.environ, env):
            if margin is None:
                os.environ.pop("PROMOTION_MARGIN", None)
            else:
                os.environ["PROMOTION_MARGIN"] = str(margin)
            res = require_retrain_job(fresh_defs()).execute_in_process(instance=DagsterInstance.ephemeral(), raise_on_error=False)
        ok(res.success, f"`{M5_RETRAIN_JOB}` failed:\n" + step_errors(res))
        new = [r for r in tagged_runs(self.exp) if r.info.run_id not in self.seen]
        self.seen |= {r.info.run_id for r in new}
        ok(len(new) == 1, f"After one execution of `{M5_RETRAIN_JOB}`, {len(new)} MLflow runs in the experiment `{self.exp}` carry the tag `{M5_TAG_DECISION}` (exactly 1 expected: the challenger's run).\nThe decision belongs to the challenger, and the experiment is the one named by MLFLOW_EXPERIMENT.")
        self.runs.append(new[0])
        return new[0]


def champion_logged_ap(mv) -> float:
    run = MlflowClient().get_run(mv.run_id)
    ok("average_precision" in run.data.metrics, f"The champion's run ({mv.run_id[:8]}) has no `average_precision` metric.")
    return run.data.metrics["average_precision"]


@pytest.fixture(scope="module")
def worse_challenger(gold, tmp_path_factory):
    """A strong champion trained by YOUR module 4 (`ml.train`); then the job runs on data whose old labels are broken, so its challenger is useless."""
    require_retrain_job(fresh_defs())
    need("mlflow", "s3", "postgres")
    ok((ROOT / "ml" / "train.py").exists(), "Missing ml/train.py (module 4): the champion the loop defends is the one it registers.")
    d = tmp_path_factory.mktemp("m5_worse")
    good, bad = d / "good.duckdb", d / "bad.duckdb"
    write_gold(good, gold)
    write_gold(bad, m5_degraded_gold(gold))
    loop = Loop("worse")
    with seeded_db() as url:
        r = py(["-m", "ml.train"], env={"WAREHOUSE_PATH": str(good), "MLFLOW_EXPERIMENT": loop.exp, "MODEL_NAME": loop.model, "DATABASE_URL": url}, timeout=900)
        ok(r.returncode == 0, "`python -m ml.train` (module 4) failed while preparing the champion:\n" + tail(r, 14))
        v0 = loop.champion()
        ok(v0 is not None, "`python -m ml.train` did not leave a model with the alias `champion` (module 4).")
        ap0 = champion_logged_ap(v0)
        loop.execute(bad, extra={"DATABASE_URL": url})
        v1 = loop.champion()
        loop.execute(bad, extra={"DATABASE_URL": url})
        v2 = loop.champion()
    return {"loop": loop, "v0": v0, "v1": v1, "v2": v2, "ap0": ap0}


@pytest.fixture(scope="module")
def better_challenger(gold, tmp_path_factory):
    """Cold start on broken data (the challenger becomes the first, weak champion); then healthy data brings a much better challenger."""
    require_retrain_job(fresh_defs())
    need("mlflow", "s3", "postgres")
    d = tmp_path_factory.mktemp("m5_better")
    good, bad = d / "good.duckdb", d / "bad.duckdb"
    write_gold(good, gold)
    write_gold(bad, m5_degraded_gold(gold))
    loop = Loop("better")
    with seeded_db() as url:
        extra = {"DATABASE_URL": url}
        loop.execute(bad, extra=extra)
        v0 = loop.champion()
        ok(v0 is not None, "There was no champion yet and `retrain_job` did not create one: with nothing to defend, the first challenger must become the champion (cold start).")
        ap0 = champion_logged_ap(v0)
        loop.execute(good, extra=extra)
        v1 = loop.champion()
        loop.execute(good, margin=None, extra=extra)  # same data again, with YOUR default margin
        v2 = loop.champion()
    return {"loop": loop, "v0": v0, "v1": v1, "v2": v2, "ap0": ap0}


def test_a_worse_challenger_does_not_replace_the_champion(worse_challenger):
    w = worse_challenger
    ok(w["v1"] is not None and w["v1"].version == w["v0"].version,
       f"The champion moved from version {w['v0'].version} to {w['v1'].version if w['v1'] else None} although the challenger was trained on broken data and cannot be better.\nAn automatic promotion that does not check anything is a silent regression waiting to reach production.")
    run = w["loop"].runs[0]
    ok(run.data.tags.get(M5_TAG_DECISION) == "rejected", f"The decision tag is `{run.data.tags.get(M5_TAG_DECISION)}`; a challenger that does not beat the champion must be recorded as `rejected`.")


def test_a_better_challenger_is_promoted(better_challenger):
    b = better_challenger
    ok(b["v1"] is not None and b["v1"].version != b["v0"].version,
       f"The champion stayed at version {b['v0'].version} although a challenger trained on healthy data scores far better on the same holdout.\nA gate that never opens is as useless as one that always does: the loop must be able to improve the model.")
    run = b["loop"].runs[1]
    ok(run.data.tags.get(M5_TAG_DECISION) == "promoted", f"The decision tag is `{run.data.tags.get(M5_TAG_DECISION)}`; the promoted challenger must be recorded as `promoted`.")
    ok(b["v1"].run_id == run.info.run_id, "The champion version points to a different run than the one tagged `promoted`: promote the model you evaluated, not another one.")


def test_decision_and_both_metrics_are_recorded_as_tags(worse_challenger, better_challenger):
    cases = [("worse", worse_challenger, 0), ("better", better_challenger, 1)]
    for name, s, i in cases:
        run = s["loop"].runs[i]
        tags = run.data.tags
        for t in (M5_TAG_DECISION, M5_TAG_CHALLENGER_AP, M5_TAG_CHAMPION_AP):
            ok(t in tags, f"[{name}] the challenger's run has no tag `{t}`. Whoever audits a promotion months later needs the decision and both numbers, not only the new alias.")
        ok(tags[M5_TAG_DECISION] in M5_DECISIONS, f"[{name}] `{M5_TAG_DECISION}` is `{tags[M5_TAG_DECISION]}`; it must be one of {M5_DECISIONS}.")
        try:
            ch, champ = float(tags[M5_TAG_CHALLENGER_AP]), float(tags[M5_TAG_CHAMPION_AP])
        except ValueError:
            ok(False, f"[{name}] the AP tags must be numbers (got {tags[M5_TAG_CHALLENGER_AP]!r} and {tags[M5_TAG_CHAMPION_AP]!r}) when there is a champion to compare with.")
        ok(0 <= ch <= 1 and 0 <= champ <= 1, f"[{name}] average precision is in [0, 1]; the tags say {ch} and {champ}.")
        logged = run.data.metrics.get("average_precision")
        ok(logged is not None and abs(logged - ch) <= 0.005, f"[{name}] the tag `{M5_TAG_CHALLENGER_AP}` ({ch:.3f}) does not match the `average_precision` metric of the same run ({logged}): the number you decide with must be the number you log.")
        ok(abs(champ - s["ap0"]) <= 0.05,
           f"[{name}] you recorded AP={champ:.3f} for the champion, but on the same data its own run measured {s['ap0']:.3f}.\nChampion and challenger must be scored on the SAME time-split holdout (the one from module 4, with purging): different rows make the comparison meaningless.")
        gap = (pd.Timestamp(run.data.params["test_start"]) - pd.Timestamp(run.data.params["train_end"])).days if {"train_end", "test_start"} <= set(run.data.params) else None
        ok(gap is not None and gap >= 7, f"[{name}] the challenger's run must log `train_end` and `test_start` with a gap of at least 7 days (got {gap}): the label looks 7 days ahead, so without purging the holdout leaks into training.")
        margin_ok = (tags[M5_TAG_DECISION] == "promoted") == (ch >= champ + M5_MARGIN - 0.001)
        ok(margin_ok, f"[{name}] decision `{tags[M5_TAG_DECISION]}` with challenger AP {ch:.3f}, champion AP {champ:.3f} and margin {M5_MARGIN}: the gate is 'promote only if the challenger's AP is at least the champion's plus the margin'.")


def test_rerunning_without_new_data_changes_nothing(worse_challenger, better_challenger):
    w, b = worse_challenger, better_challenger
    ok(w["v2"].version == w["v0"].version, f"Running the loop a second time on the same data moved the champion ({w['v0'].version} → {w['v2'].version}).")
    ok(b["v2"].version == b["v1"].version,
       f"Running the loop again on the same data, with your default margin, moved the champion from version {b['v1'].version} to {b['v2'].version}.\nNoise is not improvement: make training deterministic (seeds) and make the default `PROMOTION_MARGIN` larger than the run-to-run noise (and > 0).")
    again = b["loop"].runs[2].data.tags.get(M5_TAG_DECISION)
    ok(again == "rejected", f"The second run on identical data recorded `{again}`; an identical model does not beat the champion by the margin, so it must be `rejected`.")


@pytest.mark.bonus
def test_bonus_airflow_dag():
    files = list((ROOT / "deploy" / "airflow" / "dags").glob("*.py"))
    ok(files, "BONUS: express the same pipeline as an Airflow DAG in deploy/airflow/dags/*.py (what changes when you think in tasks instead of assets?).")
    src = files[0].read_text()
    tree = ast.parse(src)  # valid syntax
    ok("DAG" in src and ">>" in src, "The DAG must declare `DAG(...)` and dependencies between tasks with `>>`.")
    ok(sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "").endswith("Operator")) >= 3, "At least 3 tasks (operators) are expected.")
