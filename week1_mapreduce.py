"""Week 1 pipeline for the DSSE topic-modeling assignment (Apache MapReduce).

Reads issue IDs from Issues.xlsx, downloads complete Jira records, extracts the
required metadata, preprocesses summary + description, and writes a vocabulary
and sparse document-term matrix. Network responses are cached for reproducible
reruns.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
from nltk.stem.snowball import SnowballStemmer
from scipy import io as scipy_io, sparse
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS


JIRA_BASE = "https://issues.apache.org/jira/rest/api/2"
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{1,}")
BOT_AUTHORS = {
    "hive qa", "cnsgithub", "trafficserver bot", "mail delivery subsystem",
    "asf subversion and git services", "hadoop qa", "qabot from busbey",
    "thomas smets - a3 system", "atlas qa", "m", "flink jira bot",
    "asf irc bot", "beam jira bot", "tester", "mahout qa", "laurent chabot",
    "tezqa", "faure systems", "sentryqa", "bug reporter", "chris chabot",
    "ignite tc bot", "asapsystems", "rangerqa", "flume qa", "knox qa",
    "giraph qa", "jerry chabot", "sqoop qa bot", "apache@tingo.org",
    "github import", "tajo qa", "hudson", "asf github bot", "genericqa",
}


def fetch_json(url: str, retries: int = 5) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "DSSE-week1/1.0"})
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == retries - 1:
                raise
        except (URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Unable to fetch {url}")


def download_issue(issue_id: str, cache_dir: Path) -> tuple[str, dict | None, str | None]:
    cache_file = cache_dir / f"{issue_id}.json"
    if cache_file.exists():
        return issue_id, json.loads(cache_file.read_text(encoding="utf-8")), None
    url = f"{JIRA_BASE}/issue/{issue_id}?fields=*all&expand=names,schema"
    try:
        payload = fetch_json(url)
        cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return issue_id, payload, None
    except Exception as exc:  # continue the batch and report failures explicitly
        return issue_id, None, f"{type(exc).__name__}: {exc}"


def author_is_bot(comment: dict) -> bool:
    author = comment.get("author") or {}
    names = {str(author.get(k, "")).strip().lower() for k in ("name", "key", "displayName", "emailAddress")}
    return bool(names & BOT_AUTHORS) or any("bot" in name or name.endswith(" qa") for name in names)


def normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def preprocess(text: str, stemmer: SnowballStemmer) -> list[str]:
    tokens = []
    for raw in TOKEN_RE.findall(text.lower()):
        token = raw.strip("_+-")
        if len(token) < 3 or token in ENGLISH_STOP_WORDS or token.isdigit():
            continue
        if token.startswith(("http", "www")):
            continue
        tokens.append(stemmer.stem(token))
    return tokens


def compact_record(issue_id: str, labels: str, payload: dict, stemmer: SnowballStemmer) -> dict:
    fields = payload.get("fields") or {}
    comments = (fields.get("comment") or {}).get("comments") or []
    parent = fields.get("parent") or {}
    text = f"{normalize_text(fields.get('summary'))} {normalize_text(fields.get('description'))}"
    tokens = preprocess(text, stemmer)
    return {
        "issue_id": issue_id,
        "design_decisions": labels,
        "parent_id": parent.get("key"),
        "summary": normalize_text(fields.get("summary")),
        "description": normalize_text(fields.get("description")),
        "issue_type": (fields.get("issuetype") or {}).get("name"),
        "status": (fields.get("status") or {}).get("name"),
        "priority": (fields.get("priority") or {}).get("name"),
        "resolution": (fields.get("resolution") or {}).get("name"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "resolved": fields.get("resolutiondate"),
        "labels": fields.get("labels") or [],
        "components": [x.get("name") for x in fields.get("components") or []],
        "comment_count": (fields.get("comment") or {}).get("total", len(comments)),
        "downloaded_comment_count": len(comments),
        "downloaded_human_comment_count": sum(not author_is_bot(c) for c in comments),
        "attachment_count": len(fields.get("attachment") or []),
        "tokens": tokens,
        "raw_fields": fields,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", default="Issues.xlsx")
    parser.add_argument("--output", default="outputs/week1/mapreduce")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    output = Path(args.output)
    cache = output / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    frame = pd.read_excel(args.workbook, sheet_name="Mapreduce", dtype=str).dropna(subset=["Issue ID"])
    pairs = [(row["Issue ID"].strip(), row["Types of design decisions"].strip()) for _, row in frame.iterrows()]

    results: dict[str, dict] = {}
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_issue, issue_id, cache): issue_id for issue_id, _ in pairs}
        for number, future in enumerate(as_completed(futures), 1):
            issue_id, payload, error = future.result()
            if payload is not None:
                results[issue_id] = payload
            else:
                failures.append({"issue_id": issue_id, "error": error})
            if number % 50 == 0 or number == len(futures):
                print(f"Downloaded/cached {number}/{len(futures)} issues; failures={len(failures)}", flush=True)

    stemmer = SnowballStemmer("english")
    records = [compact_record(issue_id, labels, results[issue_id], stemmer) for issue_id, labels in pairs if issue_id in results]
    with (output / "issues_processed.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    metadata_columns = [
        "issue_id", "design_decisions", "parent_id", "summary", "issue_type", "status",
        "priority", "resolution", "created", "updated", "resolved", "comment_count",
        "downloaded_comment_count", "downloaded_human_comment_count", "attachment_count",
    ]
    pd.DataFrame([{key: record.get(key) for key in metadata_columns} for record in records]).to_csv(
        output / "issues_metadata.csv", index=False, encoding="utf-8"
    )
    (output / "download_failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")

    vocabulary = Counter(token for record in records for token in record["tokens"])
    vocab_items = sorted(vocabulary.items(), key=lambda item: (-item[1], item[0]))
    with (output / "vocabulary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["token", "corpus_frequency", "document_frequency"])
        for token, count in vocab_items:
            writer.writerow([token, count, sum(token in set(r["tokens"]) for r in records)])

    vocab_index = {token: index for index, (token, _) in enumerate(vocab_items)}
    rows, cols, values = [], [], []
    for row_index, record in enumerate(records):
        for token, count in Counter(record["tokens"]).items():
            rows.append(row_index); cols.append(vocab_index[token]); values.append(count)
    matrix = sparse.csr_matrix((values, (rows, cols)), shape=(len(records), len(vocab_items)), dtype="int32")
    sparse.save_npz(output / "document_term_matrix.npz", matrix)
    scipy_io.mmwrite(output / "document_term_matrix.mtx", matrix)
    (output / "document_ids.json").write_text(json.dumps([r["issue_id"] for r in records], indent=2), encoding="utf-8")
    (output / "vocabulary_terms.json").write_text(json.dumps([x[0] for x in vocab_items], indent=2), encoding="utf-8")
    summary = {
        "assigned_issues": len(pairs), "downloaded_issues": len(records), "failed_issues": len(failures),
        "issues_with_parent": sum(bool(r["parent_id"]) for r in records),
        "vocabulary_size": len(vocab_items), "total_tokens": sum(vocabulary.values()),
        "matrix_shape": list(matrix.shape), "matrix_nonzero_entries": int(matrix.nnz),
        "top_30_tokens": vocab_items[:30],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
