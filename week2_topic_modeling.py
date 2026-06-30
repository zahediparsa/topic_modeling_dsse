"""Week 2 LDA and BERTopic analysis for the MapReduce issue corpus."""
from __future__ import annotations

import argparse, json, re
from collections import Counter
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from bertopic import BERTopic
from gensim.corpora import Dictionary
from gensim.models import CoherenceModel
from hdbscan import HDBSCAN
from sentence_transformers import SentenceTransformer
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
from umap import UMAP

SEED = 42
DOMAIN_STOP = {
    "hadoop", "mapreduc", "mapreduce", "use", "need", "current", "new", "number",
    "implement", "support", "chang", "add", "make", "allow", "provid", "creat",
}
ONTOLOGY = {
    "component": {"job", "task", "mapper", "reducer", "jobtrack", "tasktrack", "cluster", "node", "server", "client", "servic", "process", "thread", "worker"},
    "connector": {"shuffl", "send", "receiv", "read", "write", "commun", "connect", "transfer", "fetch", "request"},
    "data": {"data", "file", "input", "output", "record", "key", "valu", "block", "split", "metadata", "log"},
    "solution": {"cache", "replic", "compress", "encrypt", "authent", "schedul", "queue", "pool", "checkpoint"},
    "quality_performance": {"perform", "latenc", "throughput", "fast", "slow", "speed", "memori", "cpu", "overhead", "optim", "effici"},
    "quality_reliability": {"reliabl", "fail", "recover", "fault", "error", "retry", "robust", "availab", "restart"},
    "quality_security": {"secur", "auth", "author", "permiss", "credenti", "token", "ssl", "kerbero"},
    "quality_scalability": {"scal", "parallel", "concurr", "distribut", "capac"},
    "quality_maintainability": {"refactor", "maintain", "test", "deprecat", "compat", "upgrad", "clean"},
}
REVERSE_ONTOLOGY = {term: category for category, terms in ONTOLOGY.items() for term in terms}


def load_records(path: Path):
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def refine(tokens):
    return [REVERSE_ONTOLOGY.get(t, t) for t in tokens if t not in DOMAIN_STOP and len(t) > 2]

def clean_natural_text(record):
    text = f"{record.get('summary','')} {record.get('description','')}"
    text = re.sub(r"https?://\S+|\{[^}]*\}|\[[^]]*\]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def top_terms(model, features, n=12):
    return [[features[i] for i in topic.argsort()[::-1][:n]] for topic in model.components_]


def coherence(terms, token_docs):
    dictionary = Dictionary(token_docs)
    return float(CoherenceModel(topics=terms, texts=token_docs, dictionary=dictionary, coherence="c_v", processes=1).get_coherence())


def label_topic(words):
    s = set(words)
    rules = [
        ({"quality_security", "secur", "auth", "permiss"}, "Security and access control"),
        ({"quality_performance", "memori", "cpu", "optim"}, "Performance and resource use"),
        ({"quality_reliability", "fail", "error", "recover"}, "Failure handling and reliability"),
        ({"quality_maintainability", "test", "compat", "refactor"}, "Maintenance, testing and compatibility"),
        ({"solution", "schedul", "queue"}, "Scheduling and coordination"),
        ({"connector", "shuffl", "transfer"}, "Data transfer and shuffle"),
        ({"data", "file", "input", "output"}, "Data and file processing"),
        ({"component", "task", "job", "node"}, "Execution components and lifecycle"),
    ]
    scores = [(len(s & keys), label) for keys, label in rules]
    best = max(scores)
    return best[1] if best[0] else " / ".join(words[:3])


def fit_lda(token_docs, n_topics, max_iter=50):
    texts = [" ".join(x) for x in token_docs]
    vectorizer = CountVectorizer(tokenizer=str.split, preprocessor=None, token_pattern=None, min_df=3, max_df=0.90)
    matrix = vectorizer.fit_transform(texts)
    model = LatentDirichletAllocation(
        n_components=n_topics, doc_topic_prior=0.01, topic_word_prior=0.01,
        learning_method="batch", max_iter=max_iter, random_state=SEED, n_jobs=-1,
    )
    proportions = model.fit_transform(matrix)
    terms = top_terms(model, vectorizer.get_feature_names_out())
    return model, vectorizer, matrix, proportions, terms


def save_topic_table(path, terms, proportions):
    rows = []
    dominant = proportions.argmax(axis=1)
    for i, words in enumerate(terms):
        rows.append({"topic": i, "label": label_topic(words), "issue_count": int((dominant == i).sum()), "top_terms": ", ".join(words)})
    pd.DataFrame(rows).to_csv(path, index=False)
    return rows

def apply_labels(rows, labels, path):
    for row in rows:
        row["label"] = labels.get(row["topic"], row["label"])
    pd.DataFrame(rows).to_csv(path, index=False)
    return rows


def plot_coherence(scores, chosen, path):
    plt.figure(figsize=(7.2, 4.2)); plt.plot(list(scores), list(scores.values()), marker="o", color="#2F5597")
    plt.axvline(chosen, color="#C55A11", ls="--", label=f"selected: {chosen}")
    plt.xlabel("Number of topics"); plt.ylabel("c_v coherence"); plt.title("LDA topic-count optimization")
    plt.grid(alpha=.25); plt.legend(); plt.tight_layout(); plt.savefig(path, dpi=180); plt.close()


def plot_topic_counts(rows, title, path):
    labels = [f"T{x['topic']}: {x['label']}" for x in rows]
    counts = [x["issue_count"] for x in rows]
    order = np.argsort(counts)
    plt.figure(figsize=(8, max(4, .48*len(rows)))); plt.barh(np.array(labels)[order], np.array(counts)[order], color="#4472C4")
    plt.xlabel("Dominant-topic issue count"); plt.title(title); plt.tight_layout(); plt.savefig(path, dpi=180); plt.close()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--input", default="outputs/week1/mapreduce/issues_processed.jsonl"); ap.add_argument("--output", default="outputs/week2/mapreduce")
    args = ap.parse_args(); out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    records = load_records(Path(args.input)); ids = [r["issue_id"] for r in records]
    original = [r["tokens"] for r in records]; refined = [refine(x) for x in original]

    # Iteration 1: deliberately broad 10-topic baseline.
    b_model, b_vec, _, b_prop, b_terms = fit_lda(original, 10)
    baseline_rows = save_topic_table(out/"lda_iteration1_topics.csv", b_terms, b_prop)
    pd.DataFrame(b_prop, columns=[f"topic_{i}" for i in range(10)]).assign(issue_id=ids).to_csv(out/"lda_iteration1_proportions.csv", index=False)

    # Iteration 2 and optimization: ontology replacements + expanded domain stopwords.
    scores = {}
    candidates = {}
    for k in range(3, 11):
        model, vec, matrix, prop, terms = fit_lda(refined, k, max_iter=40)
        score = coherence(terms, refined); scores[k] = score; candidates[k] = (model, vec, matrix, prop, terms)
        print(f"LDA k={k}: c_v={score:.4f}", flush=True)
    chosen = max(scores, key=scores.get)
    model, vec, matrix, prop, terms = candidates[chosen]
    lda_rows = save_topic_table(out/"lda_final_topics.csv", terms, prop)
    lda_rows = apply_labels(lda_rows, {
        0:"Map/reduce execution and speculation", 1:"Sorting, combining and partitioning",
        2:"Commit behavior and workload execution", 3:"Job state, history and submission",
        4:"Caching and resource limits", 5:"HDFS, RAID and input streams",
        6:"DistCp, Rumen and tooling", 7:"Trackers, slots and heartbeats",
        8:"Security, configuration and APIs",
    }, out/"lda_final_topics.csv")
    lda_prop = pd.DataFrame(prop, columns=[f"topic_{i}" for i in range(chosen)]); lda_prop.insert(0, "issue_id", ids); lda_prop["dominant_topic"] = prop.argmax(axis=1); lda_prop["dominant_probability"] = prop.max(axis=1); lda_prop.to_csv(out/"lda_issue_topics.csv", index=False)
    pd.DataFrame([{"n_topics": k, "c_v_coherence": v} for k,v in scores.items()]).to_csv(out/"lda_coherence_scores.csv", index=False)
    joblib.dump({"model": model, "vectorizer": vec}, out/"lda_final_model.joblib")
    plot_coherence(scores, chosen, out/"lda_coherence.png"); plot_topic_counts(lda_rows, "LDA topics by dominant issue count", out/"lda_topic_counts.png")

    # BERTopic: semantic embeddings -> UMAP -> HDBSCAN -> c-TF-IDF.
    docs = [clean_natural_text(record) for record in records]
    embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    embeddings = embedder.encode(docs, show_progress_bar=True, batch_size=32, normalize_embeddings=True)
    umap_model = UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", random_state=SEED)
    clusterer = HDBSCAN(min_cluster_size=12, min_samples=3, metric="euclidean", cluster_selection_method="leaf", prediction_data=True)
    natural_domain_stop = {"hadoop", "mapreduce", "use", "used", "using", "need", "new", "current", "support", "add", "change"}
    vectorizer = CountVectorizer(stop_words=sorted(set(ENGLISH_STOP_WORDS) | natural_domain_stop), min_df=2, ngram_range=(1,2))
    topic_model = BERTopic(embedding_model=embedder, umap_model=umap_model, hdbscan_model=clusterer, vectorizer_model=vectorizer, calculate_probabilities=True, top_n_words=12, verbose=True)
    topics, probabilities = topic_model.fit_transform(docs, embeddings)
    info = topic_model.get_topic_info(); info.to_csv(out/"bertopic_topics.csv", index=False)
    assignments = pd.DataFrame({"issue_id": ids, "bertopic_topic": topics})
    if probabilities is not None and getattr(probabilities, "ndim", 1) == 2 and probabilities.shape[1] > 0:
        assignments["assignment_probability"] = probabilities.max(axis=1)
        np.save(out/"bertopic_probabilities.npy", probabilities)
    assignments.to_csv(out/"bertopic_issue_topics.csv", index=False)
    topic_model.save(out/"bertopic_model", serialization="safetensors", save_ctfidf=True)
    ber_rows=[]
    for _, row in info.iterrows():
        t=int(row.Topic); words=[w for w,_ in (topic_model.get_topic(t) or [])[:12]] if t != -1 else ["outlier"]
        ber_rows.append({"topic":t,"label":"Outliers" if t==-1 else label_topic(words),"issue_count":int(row.Count),"top_terms":", ".join(words)})
    ber_rows = apply_labels(ber_rows, {
        -1:"Outliers", 0:"YARN integration and application clients",
        1:"Sort/merge and reduce pipeline", 2:"Schedulers, queues and preemption",
        3:"Java/YARN server configuration", 4:"Rumen and Gridmix workload traces",
        5:"Shuffle transfer and memory", 6:"Job history and log storage",
        7:"Streaming job execution", 8:"Distributed cache and dependencies",
        9:"Task memory limits", 10:"Input formats and compressed splits",
        11:"Failure recovery and retries", 12:"RAID parity and block placement",
        13:"Input split sizing and file listing", 14:"Serialization and output schemas",
        15:"Heartbeat protocol and task assignment",
    }, out/"bertopic_topic_summary.csv")
    plot_topic_counts([r for r in ber_rows if r["topic"]!=-1], "BERTopic clusters by issue count", out/"bertopic_topic_counts.png")

    summary = {
        "issues": len(records), "baseline_lda_topics": 10, "selected_lda_topics": chosen,
        "selected_lda_coherence": scores[chosen], "lda_coherence_scores": scores,
        "bertopic_topics_excluding_outliers": sum(r["topic"] != -1 for r in ber_rows),
        "bertopic_outliers": next((r["issue_count"] for r in ber_rows if r["topic"] == -1), 0),
        "lda_topics": lda_rows, "bertopic_topics": ber_rows,
        "ontology_replacements": {k: sorted(v) for k,v in ONTOLOGY.items()}, "domain_stopwords": sorted(DOMAIN_STOP),
    }
    (out/"summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k not in {"lda_topics","bertopic_topics","ontology_replacements"}}, indent=2))

if __name__ == "__main__": main()
