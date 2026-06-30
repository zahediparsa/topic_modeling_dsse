# Week 1 - Apache MapReduce

The Week 1 pipeline completed all 587 assigned issues with zero download failures.

## Reproduce

From the workspace root:

```powershell
python -m pip install -r requirements-week1.txt
python week1_mapreduce.py --workbook Issues.xlsx --output outputs/week1/mapreduce
```

Jira responses are cached in `cache/`, so reruns only request missing issues.

## Main outputs

- `issues_processed.jsonl`: complete raw Jira fields, assignment labels, parent ID, metadata, and processed tokens.
- `issues_metadata.csv`: compact metadata for analysis.
- `vocabulary.csv`: token corpus frequency and document frequency.
- `document_term_matrix.npz` and `.mtx`: sparse count matrices.
- `document_ids.json` and `vocabulary_terms.json`: matrix row/column labels.
- `summary.json`: corpus validation statistics.
- `Week_1_Report_MapReduce.docx`: Week 1 methods and findings report.

The bot-author list supplied with the assignment is embedded in the pipeline.
