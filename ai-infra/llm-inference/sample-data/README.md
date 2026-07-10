# sample-data/

Generic synthetic sample data used by the `batch/` and `benchmark/`
notebooks.

| Domain | Input shape | Task expected of the LLM under test |
| --- | --- | --- |
| `travel/` | Travel booking confirmation emails (flights, trains, buses, hotels, packages, cruises, etc.) | Extract the booking details into a structured JSON record (PNR, traveler, dates, segments, total price, etc.). |

All sample data in this folder is **synthesized by Amazon Bedrock**.
No real customer or PII data is included. The dataset is released under
[CC0 1.0 Universal](LICENSE).

## File layout

```
sample-data/
├── travel/
│   ├── 01-domestic-flight.jsonl
│   ├── 02-international-flight.jsonl
│   ├── 03-train-booking.jsonl
│   ├── 04-bus-booking.jsonl
│   ├── 05-hotel-only.jsonl
│   ├── 06-car-rental.jsonl
│   ├── 07-flight-hotel-package.jsonl
│   ├── 08-multi-city.jsonl
│   ├── 09-cruise.jsonl
│   └── 10-budget-airline.jsonl
│── README.md
├── scripts/
│   └── synthesize.py    # Bedrock-based generator (text/travel)
├── tests/
└── LICENSE              # CC0 1.0 Universal
```

The shipped `travel/` set is **10 sub-domain files × 1K records each =
10K rows** (~12 MB) — enough to drive the smoke tests and the load-test
notebooks out of the box. To regenerate at the benchmark scale (10K per
seed = 100K rows), see [Reproducing the travel dataset](#reproducing-the-travel-dataset)
below.

## Record formats

### `travel/`

Each line is a JSON object:

```json
{
  "text": "Subject: Your booking is confirmed — PNR ABC123\n...",
  "meta": {
    "seed": "domestic-flight",
    "domain": "travel",
    "temperature": 0.8,
    "top_p": 0.95,
    "batch_idx": 234
  }
}
```

## Reproducing the travel dataset

```bash
# Smoke test (cheap): 2 seeds × 50 records each
python sample-data/scripts/synthesize.py --smoke

# Match the in-repo size (10 seeds × 1K records = 10K rows)
python sample-data/scripts/synthesize.py --per-seed 1000

# Benchmark scale (10 seeds × 10K records = 100K rows, ~$6.50)
python sample-data/scripts/synthesize.py --per-seed 10000
```

The script is fully resumable — re-run with the same `--per-seed` and any
incomplete seeds will continue from their last journal checkpoint. To
extend the in-repo dataset to the benchmark scale, run the 10K-per-seed
command and the existing 1K rows will be retained as the first 1K records
of each file.

## Cost

Rough estimate for a full 100K-record run with default settings, using
list prices for a low-cost Bedrock model as of 2026:

* Input tokens ≈ 700 / call × 10,000 calls ≈ **7 M tokens** ≈ $0.42
* Output tokens ≈ 250 / record × 100,000 records ≈ **25 M tokens** ≈ $6.00
* **Total ≈ $6.50**

Use `--dry-run` to print a per-run estimate before spending real money.

## License

The synthesized data files in `travel/` are released to the
public domain under [CC0 1.0 Universal](LICENSE). The generation code in
`scripts/` is licensed under the repository's top-level `LICENSE` (MIT-0).
