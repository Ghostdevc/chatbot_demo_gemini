✅ chatbot_demo_gemini

# Chatbot Demo using Gemini, FastAPI and FAISS

This project is a document-aware chatbot powered by Google Gemini, built using FastAPI and LangChain. It allows users to upload documents, generate vector embeddings, and chat with a context-aware assistant. FAISS is used for vector search and PostgreSQL stores chat history.

## Features

- Upload and index documents (PDF, TXT, DOCX)
- Gemini-based chatbot via LangChain
- Text embedding and retrieval using FAISS
- Conversation history stored in PostgreSQL
- Output validation using Guardrails (e.g., to prevent legal/medical advice)
- REST API for full chatbot interaction

## Tech Stack

- Python
- FastAPI
- LangChain
- Google Gemini API
- FAISS (vector store)
- PostgreSQL
- Guardrails

## How to Run

1. Clone the repository:
git clone https://github.com/Ghostdevc/chatbot_demo_gemini.git
cd chatbot_demo_gemini


2. Install dependencies:
pip install -r requirements.txt


3. Set environment variables in a `.env` file:
GOOGLE_API_KEY=your_google_gemini_api_key
DATABASE_URL=postgresql://username:password@host:port/dbname


4. Run the API:
uvicorn main:app --reload


## Endpoints Overview

- `/chatbots/` – Create/list/update/delete chatbots
- `/chatbots/{id}/upload_document/` – Upload document to chatbot
- `/chatbots/{id}/chat/` – Send a message and receive a response
- `/chatbots/{id}/history/` – Get chat history

## Notes

- Document chunks are embedded and searched using Gemini + FAISS
- Outputs are filtered and validated using Guardrails
- Designed for future UI integration and expansion

## License

MIT License
