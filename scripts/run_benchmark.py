# scripts/run_benchmark.py
from app.eval.harness import evaluate_scenarios, print_summary

if __name__ == "__main__":
    results = evaluate_scenarios("benchmarks/scenarios.yaml")
    print_summary(results)
