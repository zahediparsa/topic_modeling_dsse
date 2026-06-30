# Week 2 - LDA and BERTopic

This analysis uses all 587 MapReduce issues prepared in Week 1.

## Main results

- Refined LDA: 9 topics selected from candidates 3-10.
- Best c_v coherence: 0.4095.
- BERTopic: 16 non-outlier clusters covering 394 issues.
- BERTopic outliers: 193 issues.

## Reproduce

```powershell
python -m pip install -r requirements-week2.txt
python week2_topic_modeling.py --input outputs/week1/mapreduce/issues_processed.jsonl --output outputs/week2/mapreduce
```

The pipeline fixes random seed 42, saves both models, and exports issue-level assignments.

See `Week_2_Report_MapReduce.docx` for the complete answer to Research Question 1.
