# Evaluate OpsPilot

The release CI smoke test uses real PostgreSQL, Redis, Celery, Prometheus, Loki, Tempo, and the sample application. It tests signup and viewer restrictions, detection of payment declines, investigation, operator approval, recovery, report download, and sign-out.

The [release benchmark run](https://github.com/Anshuman-utd/OpsPilot/actions/runs/43420030197) verified detection, investigation, operator-approved execution, recovery, and report download for all six scenarios. The run includes a JSON artifact for each scenario.

## Local observations

These are individual runs from the November 2026 release work. The benchmarks used the deterministic RCA path and each started with zero active incidents.

| Scenario | Detected after | Verified recovery after | Report downloaded |
| --- | ---: | ---: | --- |
| Payment declines | 18.83 s | 108.84 s | Yes |
| Order database contention | 18.07 s | 63.99 s | Yes |
| Payment endpoint unavailable | 16.00 s | 152.82 s | Yes |
| Slow payments | 17.13 s | 162.76 s | Yes |

The payment endpoint run identified elevated HTTP 5xx responses and missed the scenario's specific RCA keyword check. A subsequent regression test verifies that the fallback cites the sample service's unavailability log. The fallback also distinguishes latency evidence from payment failures. These observations come from individual runs during development. Repeated runs are needed to measure mean recovery times and accuracy.

## Benchmark a scenario

GitHub Actions runs **Benchmark demo scenarios** when a push changes the benchmark script or workflow. Once the workflow is on the default branch, you can also select **Run workflow** to run all six on isolated runners. Each job publishes its own JSON result.

Use a **fresh disposable Compose project** with its own environment file, volumes, and localhost ports. The example uses ports 18088 and 18448. Run it from the repository root.

```sh
repo_dir="$PWD"
benchmark_dir="$(mktemp -d)"
(cd "$benchmark_dir" && "$repo_dir/deploy/init-env.sh" local)
sed -e 's/8088/18088/g' -e 's/8448/18448/g' \
  "$benchmark_dir/.env.preview" > "$benchmark_dir/benchmark.env"
compose_bench() {
  docker compose --env-file "$benchmark_dir/benchmark.env" -p opspilot-bench \
    -f docker-compose.yml -f compose.release.yml "$@"
}
compose_bench up -d --build --wait
compose_bench exec -T api python - payment_failure_spike < deploy/benchmark.py
compose_bench down -v
```

Repeat in a fresh project for `downstream_latency_spike`, `db_pool_exhaustion`, `order_service_error_spike`, `payment_service_unavailability`, and `cascading_timeout`. The benchmark records a missed detection or a missing matching remediation action explicitly. Timing from an old incident or a previous run is excluded.

The output includes detection time from fault activation, investigation duration, a primary-hypothesis keyword check, evidence sources and unresolved citations, remediation outcome, and elapsed time to confirmed recovery. The benchmark returns a failing exit status if it cannot complete the workflow. One run is a sample. Compute mean detection and recovery times and success rates across repeated clean runs. Record the number of baseline incidents and missed detections alongside the results. The keyword and citation checks are proxies for RCA quality. Read the generated reports to assess causal accuracy and unsupported assertions.

For a provider comparison, repeat the same scenario with `GROQ_API_KEY` unset and then configured. Report any provider errors and fallback separately. A configured key alone does not establish that Groq supplied the report.
