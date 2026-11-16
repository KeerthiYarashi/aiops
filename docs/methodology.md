# Verify Ops methodology

## Workload and telemetry

Locust creates items and reuses their IDs for reads. FastAPI stores the demo items in memory and caches their JSON in Redis. The catalog check is a simulated dependency. Prometheus scrapes FastAPI every five seconds. Grafana queries these stored measurements, while the live detector polls dependency error counters and exposes findings through the operator dashboard.

The catalog experiment records a 40-second baseline, injects a fault for about 30 seconds, restores the dependency and saves the observations. The captured README dashboard shows HTTP 503 and catalog errors during the same interval while scrape availability remains at one.

## Detection and ranking

Each metric has a rolling median and a median-absolute-deviation scale with a minimum floor. A signal needs three anomalous samples before it is reported. Memory uses CUSUM to accumulate upward changes, and availability uses scrape failures. Candidate ranking weights the dependency prior at 50%, lagged correlation at 35% and onset timing at 15% when multiple candidates are present. Availability has its own handling.

The ground truth is the signal associated with the injected fault. The earliest anomalous signal and the first ranked signal are each compared with that ground truth. The README capture's first observed signal was `http_errors`, while the top-ranked source was `catalog_errors`.

## Diagnosis evaluation

Run from the project directory after recording experiment reports:

```powershell
python -B -m evaluation.summarize
python -B -m evaluation.evaluate_diagnosis
python -B -m evaluation.replay_recovery
```

The diagnosis evaluator withholds the injected fault name and expected metric from the graph. It reports statistical accuracy, final diagnosis accuracy, model response coverage and a paired comparison for cases that received a valid model response. Enable an Ollama model or Bedrock through the configuration in the README before measuring model-assisted accuracy. Statistical fallbacks are identified separately.

The current local archive contains 12 recorded experiments across seven fault types, including repeated catalog runs. The earliest signal matched 2 of 12 cases. The statistical top candidate and Ollama with `qwen3:8b` each matched 12 of 12. All 12 model responses passed the evidence checks. The model preserved the correct rankings on these controlled experiments. Production accuracy and live Bedrock inference remain unverified.

## Recovery evaluation

The action audit records execution and recovery separately. Recovery requires a successful item write, a matching read, Redis readiness and a fresh successful Prometheus scrape. Unavailable evidence produces an insufficient-evidence verdict when the application checks do not already show degradation.

In five replay fixtures, an error-rate-only baseline declared recovery incorrectly twice and the verifier declared recovery incorrectly zero times. The two disagreements were Redis fallback and a mismatched response payload. Run the replay to see each case.
