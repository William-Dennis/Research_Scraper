import ollama


def get_embedding(text: str, model: str = "embeddinggemma:latest") -> list:
    # Use the top-level embed function for simplicity
    result = ollama.embed(model=model, input=text)

    # result["embeddings"] returns a list of lists (for batching)
    # We take the first one since we passed a single string
    return result["embeddings"][0]
