import os
import hashlib
from pathlib import Path
from langchain_community.document_loaders import TextLoader, DirectoryLoader
from langchain_text_splitters import CharacterTextSplitter
from langchain_chroma import Chroma
from dotenv import load_dotenv

BACKEND_DIRECTORY = Path(__file__).resolve().parent
DOCS_DIRECTORY = BACKEND_DIRECTORY / "docs"
PERSISTENT_DIRECTORY = BACKEND_DIRECTORY / "db" / "chroma_db"
VECTOR_COLLECTION = "rag_local_embeddings"
load_dotenv(BACKEND_DIRECTORY / ".env")

def load_documents(docs_path=DOCS_DIRECTORY):
    """Load all text files from the docs directory"""
    print(f"Loading documents from {docs_path}...")
    
    # Check if docs directory exists
    if not os.path.exists(docs_path):
        raise FileNotFoundError(f"The directory {docs_path} does not exist. Please create it and add your company files.")
    
    # Load all .txt files from the docs directory
    loader = DirectoryLoader(
        path=str(docs_path),
        glob="*.txt",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8-sig"},
    )
    
    documents = loader.load()
    
    if len(documents) == 0:
        raise FileNotFoundError(f"No .txt files found in {docs_path}. Please add your company documents.")
    
   
    for i, doc in enumerate(documents[:2]):  # Show first 2 documents
        print(f"\nDocument {i+1}:")
        print(f"  Source: {doc.metadata['source']}")
        print(f"  Content length: {len(doc.page_content)} characters")
        print(f"  Content preview: {doc.page_content[:100]}...")
        print(f"  metadata: {doc.metadata}")

    return documents

def split_documents(documents, chunk_size=1000, chunk_overlap=0):
    """Split documents into smaller chunks with overlap"""
    print("Splitting documents into chunks...")
    
    text_splitter = CharacterTextSplitter(
        chunk_size=chunk_size, 
        chunk_overlap=chunk_overlap
    )
    
    chunks = text_splitter.split_documents(documents)
    
    if chunks:
    
        for i, chunk in enumerate(chunks[:5]):
            print(f"\n--- Chunk {i+1} ---")
            print(f"Source: {chunk.metadata['source']}")
            print(f"Length: {len(chunk.page_content)} characters")
            print(f"Content:")
            print(chunk.page_content)
            print("-" * 50)
        
        if len(chunks) > 5:
            print(f"\n... and {len(chunks) - 5} more chunks")
    
    return chunks

def create_vector_store(chunks, persist_directory=PERSISTENT_DIRECTORY):
    """Create ChromaDB vectors with its local default embedding model."""
    print("Creating local embeddings and storing in ChromaDB...")
    ids = [
        hashlib.sha256(
            f"{index}\0{chunk.metadata.get('source', '')}\0{chunk.page_content}".encode("utf-8")
        ).hexdigest()
        for index, chunk in enumerate(chunks)
    ]

    vectorstore = Chroma.from_documents(
        documents=chunks,
        ids=ids,
        persist_directory=str(persist_directory),
        collection_name=VECTOR_COLLECTION,
        collection_metadata={"hnsw:space": "cosine"}
    )
    count = vectorstore._collection.count()
    if count != len(chunks):
        raise RuntimeError(
            f"Expected {len(chunks)} indexed chunks, but found {count}."
        )

    print(f"Vector store created and saved to {persist_directory}")
    return vectorstore

def main():
    """Main ingestion pipeline"""
    print("=== RAG Document Ingestion Pipeline ===\n")

    documents = load_documents(DOCS_DIRECTORY)
    chunks = split_documents(documents)
    if not chunks:
        raise ValueError("No document chunks were created from the input documents.")

    if PERSISTENT_DIRECTORY.is_dir():
        vectorstore = Chroma(
            persist_directory=str(PERSISTENT_DIRECTORY),
            collection_name=VECTOR_COLLECTION,
            collection_metadata={"hnsw:space": "cosine"}
        )
        count = vectorstore._collection.count()
        if count == len(chunks):
            print(f"✅ Vector store already exists with {count} chunks.")
            return vectorstore
        print(f"Vector store has {count} chunks; rebuilding from current documents...")
    else:
        print("Persistent directory does not exist. Initializing vector store...\n")

    vectorstore = create_vector_store(chunks, PERSISTENT_DIRECTORY)
    
    print("\n✅ Ingestion complete! Your documents are now ready for RAG queries.")
    return vectorstore

if __name__ == "__main__":
    main()




# documents = [
#    Document(
#        page_content="Google LLC is an American multinational corporation and technology company focusing on online advertising, search engine technology, cloud computing, computer software, quantum computing, e-commerce, consumer electronics, and artificial intelligence (AI).",
#        metadata={'source': 'docs/google.txt'}
#    ),
#    Document(
#        page_content="Microsoft Corporation is an American multinational corporation and technology conglomerate headquartered in Redmond, Washington.",
#        metadata={'source': 'docs/microsoft.txt'}
#    ),
#    Document(
#        page_content="Nvidia Corporation is an American technology company headquartered in Santa Clara, California.",
#        metadata={'source': 'docs/nvidia.txt'}
#    ),
#    Document(
#        page_content="Space Exploration Technologies Corp., commonly referred to as SpaceX, is an American space technology company headquartered at the Starbase development site in Starbase, Texas.",
#        metadata={'source': 'docs/spacex.txt'}
#    ),
#    Document(
#        page_content="Tesla, Inc. is an American multinational automotive and clean energy company headquartered in Austin, Texas.",
#        metadata={'source': 'docs/tesla.txt'}
#    )
# ]
