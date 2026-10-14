from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_architect.integrations.clients import OllamaEmbeddingClient


def _chunks(text: str, maximum_characters: int = 1_200, overlap: int = 160) -> list[str]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    values: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= maximum_characters:
            current = candidate
            continue
        if current:
            values.append(current)
        current = paragraph
        while len(current) > maximum_characters:
            values.append(current[:maximum_characters])
            current = current[maximum_characters - overlap :]
    if current:
        values.append(current)
    return values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a hybrid RAG JSONL index using real embeddings."
    )
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-embedding-model", default="nomic-embed-text")
    parser.add_argument("--version", required=True)
    parser.add_argument("--allowed-role", action="append", dest="allowed_roles", default=[])
    args = parser.parse_args()

    client = OllamaEmbeddingClient(
        base_url=args.ollama_base_url,
        model=args.ollama_embedding_model,
        timeout_seconds=30,
    )
    documents = sorted(
        path for path in args.documents.rglob("*") if path.suffix.lower() in {".md", ".txt"}
    )
    if not documents:
        raise FileNotFoundError(f"No .md or .txt documents found under {args.documents}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.output.open("w", encoding="utf-8") as output:
        for document in documents:
            title = document.stem.replace("_", " ").replace("-", " ").title()
            for sequence, text in enumerate(_chunks(document.read_text(encoding="utf-8")), start=1):
                row = {
                    "chunk_id": f"{document.stem}-{sequence:04d}",
                    "source": str(document),
                    "title": title,
                    "version": args.version,
                    "text": text,
                    "embedding": client.embed(text),
                    "allowed_roles": args.allowed_roles,
                }
                output.write(json.dumps(row, ensure_ascii=True) + "\n")
                count += 1
    print(json.dumps({"documents": len(documents), "chunks": count, "output": str(args.output)}))


if __name__ == "__main__":
    main()
