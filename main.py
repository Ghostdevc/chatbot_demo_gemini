# main.py
import os
from dotenv import load_dotenv

import json

from fastapi import FastAPI, Response, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List

# --- Google Gemini için gerekli Langchain importları ---
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
# --- ---

# --- Diğer Langchain ve yardımcı kütüphane importları ---
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document

from langchain.docstore.in_memory import InMemoryDocstore # Bu import'u dosyanızın en üstüne ekleyin
from langchain_community.vectorstores.faiss import FAISS

from langchain_community.document_loaders import PyPDFLoader, TextLoader, Docx2txtLoader

from langchain.prompts import PromptTemplate # Eğer PromptTemplate kullanıyorsanız

from langchain.memory import ConversationBufferWindowMemory # Önceki N mesajı tutmak için
from langchain.chains import ConversationalRetrievalChain
from langchain.schema import HumanMessage, AIMessage, BaseMessage # Sohbet geçmişini temsil etmek için
from typing import List, Dict, Any # Tip belirtmeleri için


# Guardrails
from guardrails import Guard
# Özel doğrulayıcıları import edin
from validators import IsNotMedicalAdvice, IsNotHarmful, IsEmpatheticAndSupportive, IsNotOverlyLong, IsNotLegalFinancialAdvice 



import psycopg2
import pickle
import faiss # FAISS kütüphanesini doğrudan kullanmak için
# --- ---

# --- Ortam Değişkenlerini Yükleme ---
load_dotenv()

# Google Gemini API anahtarınızı .env dosyasından okuyun
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
FAISS_INDEX_PATH = "faiss_index.bin" # FAISS indeksini diske kaydedeceğimiz yer
# --- ---

# --- FastAPI Uygulaması ve Global Değişkenler ---
app = FastAPI()


try:
    guard_therapist = Guard.for_rail("therapist_bot.rail")
    print("Guardrails terapist botu için RAIL dosyası başarıyla yüklendi.")
except Exception as e:
    print(f"Hata: Guardrails RAIL dosyası yüklenirken sorun oluştu: {e}")
    # Uygulamanın başlatılmasını engellemek için bir hata fırlatabilir veya varsayılan davranışa dönebilirsiniz.
    raise e # Eğer hata olursa uygulamanın başlamasını istemiyorsanız bunu açabilirsiniz.


# Guardrails için LLM çağrısını saran yardımcı fonksiyon
def call_llm_with_guardrails(llm_model: ChatGoogleGenerativeAI, messages: List[Dict[str, str]], **kwargs) -> str:
    langchain_messages: List[BaseMessage] = []
    for msg_dict in messages:
        if msg_dict["role"] == "user":
            langchain_messages.append(HumanMessage(content=msg_dict["content"]))
        elif msg_dict["role"] == "assistant":
            langchain_messages.append(AIMessage(content=msg_dict["content"]))
        # Diğer roller (system vs.) varsa buraya eklenebilir.

    # Guardrails'tan gelen ancak llm_model.invoke() tarafından desteklenmeyen argümanları filtrele.
    # Genellikle bu, LLM modelinin başlangıçta ayarlanması gereken parametrelerdir.
    # LLM modeli oluşturulurken zaten temperature=0.7 ayarlanmıştır.
    filtered_kwargs = {k: v for k, v in kwargs.items() if k not in ["temperature", "max_tokens", "top_p", "top_k"]}
    # 'temperature' gibi parametreler, Guardrails'ın dahili olarak RAIL dosyasından veya 
    # varsayılan olarak LLM'e geçirmeye çalıştığı ancak LangChain modelinin invoke metodunun kabul etmediği parametrelerdir.
    # Buraya modelinizin invoke metodunun kabul etmediği diğer tüm parametreleri ekleyebilirsiniz.

    # LLM'i dönüştürülmüş mesajlarla ve filtrelenmiş kwargs ile çağır
    ai_message_response = llm_model.invoke(langchain_messages, **filtered_kwargs)
    
    # LLM'den gelen AI yanıtının content'ini döndür
    return ai_message_response.content



# Google Generative AI Embeddings modelini başlatın
# models/embedding-001 modeli 768 boyutlu vektörler üretir.
# Eğer farklı bir embedding modeli kullanacaksanız, boyutunu dökümantasyondan kontrol edin ve GEMINI_EMBEDDING_DIM değerini güncelleyin.
embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001", google_api_key=GOOGLE_API_KEY)

# Gemini embedding modelinin boyutu (models/embedding-001 için 768)
GEMINI_EMBEDDING_DIM = 768

faiss_index = None # FAISS indeksini global olarak tanımlıyoruz
# --- ---

FAISS_INDEX_DIR = "faiss_indexes"

# --- PostgreSQL Yardımcı Fonksiyonları ---
def get_db_connection():
    """PostgreSQL veritabanı bağlantısı sağlar."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Veritabanı bağlantı hatası: {e}")
        raise HTTPException(status_code=500, detail="Veritabanı bağlantı hatası.")


def load_chat_history_from_db(chatbot_id: int) -> List[Dict[str, str]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT sender, message FROM chat_messages WHERE chatbot_id = %s ORDER BY timestamp ASC;",
            (chatbot_id,)
        )
        history_rows = cursor.fetchall()

        chat_history = []
        for sender, message in history_rows:
            if sender == 'user':
                chat_history.append(HumanMessage(content=message))
            elif sender == 'bot':
                chat_history.append(AIMessage(content=message))
        return chat_history
    except Exception as e:
        print(f"Error loading chat history from DB: {e}")
        return [] # Hata durumunda boş liste döndür
    finally:
        cursor.close()
        conn.close()

def save_chat_message_to_db(chatbot_id: int, sender: str, message: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO chat_messages (chatbot_id, sender, message) VALUES (%s, %s, %s);",
            (chatbot_id, sender, message)
        )
        conn.commit()
    except Exception as e:
        print(f"Error saving chat message to DB: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def create_tables():
    """Gerekirse PostgreSQL tablolarını oluşturur."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # `documents` tablosu
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                page_number INTEGER,
                content TEXT NOT NULL
            );
        """)

        # `chatbots` tablosu
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chatbots (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                description TEXT,
                boundary_text TEXT
            );
        """)

        # `chatbot_documents` ara tablosu
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chatbot_documents (
                chatbot_id INTEGER NOT NULL REFERENCES chatbots(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                original_filename VARCHAR(255) NOT NULL,
                PRIMARY KEY (chatbot_id, document_id)
            );
        """)

        # Yeni `chat_messages` tablosu
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                chatbot_id INTEGER NOT NULL REFERENCES chatbots(id) ON DELETE CASCADE,
                sender VARCHAR(50) NOT NULL, -- 'user' veya 'bot'
                message TEXT NOT NULL,
                timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.commit()
        print("`documents`, `chatbots`, `chatbot_documents` ve `chat_messages` tabloları başarıyla kontrol edildi/oluşturuldu.")
    except Exception as e:
        print(f"Tablo oluşturma hatası: {e}")
        raise HTTPException(status_code=500, detail="Veritabanı tablo oluşturma hatası.")
    finally:
        if conn:
            cur.close()
            conn.close()
# --- ---

# --- FAISS İndeksi Kaydetme ve Yükleme Fonksiyonları ---
def load_or_create_faiss_index(chatbot_id: int):
    """Belirli bir chatbot'un FAISS indeksini diskten yükler veya yeni bir boş indeks oluşturur."""
    chatbot_faiss_path = os.path.join(FAISS_INDEX_DIR, f"faiss_index_{chatbot_id}.bin")
    os.makedirs(FAISS_INDEX_DIR, exist_ok=True)

    current_faiss_index = None
    if os.path.exists(chatbot_faiss_path):
        try:
            with open(chatbot_faiss_path, "rb") as f:
                faiss_bytes = f.read()
            
            # allow_dangerous_deserialization=True ekliyoruz
            # **UYARI: Güvenilir olmayan kaynaklardan gelen dosyaları yüklerken bunu kullanmaktan kaçının!**
            # Kendi uygulamanızda oluşturduğunuz ve kontrol ettiğiniz dosyalar için güvenli kabul edilir.
            current_faiss_index = FAISS.deserialize_from_bytes(faiss_bytes, embeddings, allow_dangerous_deserialization=True)
            print(f"Chatbot ID {chatbot_id} için FAISS indeksi diskten yüklendi (tehlikeli deserializasyona izin verildi).")
        except Exception as e:
            print(f"Chatbot ID {chatbot_id} için FAISS indeksi yüklenirken hata oluştu: {e}. Yeni bir indeks oluşturuluyor.")
            
            # Yeni indeks oluştururken InMemoryDocstore kullanıyoruz
            faiss_base_index = faiss.IndexFlatL2(GEMINI_EMBEDDING_DIM)
            current_faiss_index = FAISS(
                embedding_function=embeddings.embed_query,
                index=faiss_base_index,
                docstore=InMemoryDocstore(), # Boş bir In-Memory Docstore oluştur
                index_to_docstore_id={} # Boş bir mapping sözlüğü
            )
            print(f"Yeni (boş) bir FAISS indeksi Chatbot ID {chatbot_id} için oluşturuldu.")
    else:
        # Hiç indeks dosyası yoksa yeni bir boş indeks oluştur
        faiss_base_index = faiss.IndexFlatL2(GEMINI_EMBEDDING_DIM)
        current_faiss_index = FAISS(
            embedding_function=embeddings.embed_query,
            index=faiss_base_index,
            docstore=InMemoryDocstore(), # Boş bir In-Memory Docstore oluştur
            index_to_docstore_id={} # Boş bir mapping sözlüğü
        )
        print(f"Yeni bir FAISS indeksi Chatbot ID {chatbot_id} için oluşturuldu.")
    return current_faiss_index


def save_faiss_index(faiss_index_to_save: FAISS, chatbot_id: int):
    """Belirli bir chatbot'un FAISS indeksini diske kaydeder."""
    if faiss_index_to_save:
        chatbot_faiss_path = os.path.join(FAISS_INDEX_DIR, f"faiss_index_{chatbot_id}.bin")
        try:
            faiss_bytes = faiss_index_to_save.serialize_to_bytes()
            with open(chatbot_faiss_path, "wb") as f:
                f.write(faiss_bytes)
            print(f"Chatbot ID {chatbot_id} için FAISS indeksi diske kaydedildi.")
        except Exception as e:
            print(f"Chatbot ID {chatbot_id} için FAISS indeksi kaydedilirken hata oluştu: {e}")

# --- ---

# Uygulama başlangıcında çalışacak fonksiyonlar
@app.on_event("startup")
async def startup_event():
    create_tables()
    # Artık burada tüm FAISS indekslerini yüklememize gerek yok,
    # ilgili chatbot seçildiğinde yüklenecekler.

# Uygulama kapanışında çalışacak fonksiyonlar
@app.on_event("shutdown")
async def shutdown_event():
    # Kapanışta da her FAISS indeksini tek tek kaydetmemize gerek yok,
    # her yükleme/ekleme işleminden sonra save_faiss_index çağrılacak.
    pass # Bu fonksiyon boş kalabilir veya daha sonra temizlik için kullanılabilir.

# --- ---


class CreateChatbotRequest(BaseModel):
    name: str
    description: str | None = None
    boundary_text: str | None = None

class ChatbotResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    boundary_text: str | None = None


class UpdateChatbotRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    boundary_text: str | None = None


# --- FastAPI Uç Noktaları (Endpoints) ---
@app.post("/chatbots/{chatbot_id}/upload_document/")
async def upload_document_to_chatbot(chatbot_id: int, file: UploadFile = File(...)):
    """
    Belirli bir chatbot'a belge yükler. Yüklenen belgeyi işler,
    PostgreSQL'e kaydeder ve embedding'lerini ilgili chatbot'un FAISS'ine ekler.
    """
    # Desteklenen dosya türleri ve yükleyicilerin haritası
    supported_loaders = {
        "application/pdf": PyPDFLoader,
        "text/plain": TextLoader,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": Docx2txtLoader, # .docx için
        # Daha geniş destek için UnstructuredFileLoader'ı kullanabiliriz,
        # ancak bu ek bağımlılıklar gerektirir ve daha yavaş olabilir.
        # "application/octet-stream": UnstructuredFileLoader, # Genel dosya türleri için (uzantıya göre ayırabiliriz)
    }

    # Dosya uzantısına göre de loader seçimi yapabiliriz.
    file_extension = os.path.splitext(file.filename)[1].lower()

    loader_class = None
    if file.content_type in supported_loaders:
        loader_class = supported_loaders[file.content_type]
    elif file_extension == ".txt":
        loader_class = TextLoader
    elif file_extension == ".docx":
        loader_class = Docx2txtLoader
    # elif file_extension == ".epub": # Eğer ebooklib kurduysanız
    #     loader_class = UnstructuredEPubLoader
    
    # Geniş dosya desteği için UnstructuredFileLoader (önerilir)
    # UnstructuredFileLoader, birçok farklı dosya türünü otomatik olarak algılayabilir
    # ve daha fazla kurulum gerektirebilir (örn. libmagic).
    # Daha fazla bilgi için unstructured kütüphanesini inceleyin.
    # elif loader_class is None: # Eğer yukarıdakilerden hiçbiri uymadıysa
    #     try:
    #         from langchain_community.document_loaders import UnstructuredFileLoader
    #         loader_class = UnstructuredFileLoader
    #         print(f"'{file.filename}' için UnstructuredFileLoader kullanılıyor.")
    #     except ImportError:
    #         raise HTTPException(status_code=400, detail="Desteklenmeyen dosya türü. Unstructured kütüphanesi yüklü değil veya dosya tipi bilinmiyor.")


    if loader_class is None:
        raise HTTPException(status_code=400, detail=f"Desteklenmeyen dosya türü: {file.content_type} veya uzantı: {file_extension}. Sadece PDF, TXT, DOCX şu anda desteklenmektedir.")


    # Geçici bir dosyaya kaydet
    file_location = f"temp_{file.filename}"
    with open(file_location, "wb+") as file_object:
        file_object.write(file.file.read())

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Chatbot'un varlığını kontrol et
        cursor.execute("SELECT COUNT(*) FROM chatbots WHERE id = %s;", (chatbot_id,))
        if cursor.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail=f"Chatbot ID {chatbot_id} bulunamadı.")

        # Dinamik olarak loader'ı kullan
        loader = loader_class(file_location)
        # `load()` veya `load_and_split()` metodu loader'a göre değişebilir.
        # Çoğu loader için `load_and_split()` güvenlidir.
        # Eğer hata alırsanız sadece `load()` kullanıp sonra manuel split yapabilirsiniz.
        pages = loader.load_and_split() 

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
        )
        chunks = text_splitter.split_documents(pages)

        document_ids = []
        for i, chunk in enumerate(chunks):
            cursor.execute(
                "INSERT INTO documents (page_number, content) VALUES (%s, %s) RETURNING id;",
                (chunk.metadata.get("page", i), chunk.page_content) # page_number yoksa chunk indexini kullan
            )
            doc_id = cursor.fetchone()[0]
            document_ids.append(doc_id)

            cursor.execute(
                "INSERT INTO chatbot_documents (chatbot_id, document_id, original_filename) VALUES (%s, %s, %s);",
                (chatbot_id, doc_id, file.filename)
            )
            chunk.metadata["doc_id"] = doc_id
            chunk.metadata["chatbot_id"] = chatbot_id
            chunk.metadata["original_filename"] = file.filename

        conn.commit()

        current_faiss_index = load_or_create_faiss_index(chatbot_id)
        current_faiss_index.add_documents(chunks)
        save_faiss_index(current_faiss_index, chatbot_id)

        return JSONResponse(
            status_code=200,
            content={"message": f"Belge '{file.filename}' başarıyla yüklendi ve Chatbot ID {chatbot_id} için işlendi. Toplam {len(chunks)} parça oluşturuldu."}
        )

    except Exception as e:
        conn.rollback()
        print(f"Belge işleme hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Belge işlenirken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()
        if os.path.exists(file_location):
            os.remove(file_location)


@app.put("/chatbots/{chatbot_id}", response_model=ChatbotResponse)
async def update_chatbot(chatbot_id: int, request: UpdateChatbotRequest):
    """Belirli bir chatbot'u günceller."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Önce chatbot'un varlığını kontrol et
        cursor.execute("SELECT name, description, boundary_text FROM chatbots WHERE id = %s;", (chatbot_id,))
        existing_chatbot = cursor.fetchone()
        if not existing_chatbot:
            raise HTTPException(status_code=404, detail=f"Chatbot ID {chatbot_id} bulunamadı.")

        updates = []
        params = []

        if request.name is not None:
            updates.append("name = %s")
            params.append(request.name)
        if request.description is not None:
            updates.append("description = %s")
            params.append(request.description)
        if request.boundary_text is not None:
            updates.append("boundary_text = %s")
            params.append(request.boundary_text)

        if not updates:
            raise HTTPException(status_code=400, detail="Güncellenecek veri sağlanmadı.")

        params.append(chatbot_id) # WHERE koşulu için chatbot_id'yi en sona ekle

        query = f"UPDATE chatbots SET {', '.join(updates)} WHERE id = %s RETURNING id, name, description, boundary_text;"
        cursor.execute(query, params)
        updated_data = cursor.fetchone()

        if updated_data:
            conn.commit()
            return ChatbotResponse(
                id=updated_data[0],
                name=updated_data[1],
                description=updated_data[2],
                boundary_text=updated_data[3]
            )
        else:
            raise HTTPException(status_code=404, detail=f"Chatbot ID {chatbot_id} bulunamadı veya güncellenemedi.")

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Bu isimde bir chatbot zaten mevcut.")
    except Exception as e:
        conn.rollback()
        print(f"Chatbot güncelleme hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Chatbot güncellenirken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()

@app.delete("/chatbots/{chatbot_id}", status_code=204) # 204 No Content for successful deletion
async def delete_chatbot(chatbot_id: int):
    """Belirli bir chatbot'u ve ilişkili tüm dokümanlarını siler."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Önce chatbot'un varlığını kontrol et
        cursor.execute("SELECT COUNT(*) FROM chatbots WHERE id = %s;", (chatbot_id,))
        if cursor.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail=f"Chatbot ID {chatbot_id} bulunamadı.")

        # `ON DELETE CASCADE` sayesinde `chatbot_documents` tablosundaki ilgili girişler otomatik silinecektir.
        # Ancak, `documents` tablosundaki orijinal doküman parçaları silinmez.
        # Eğer bir doküman birden fazla chatbota bağlıysa, sadece o chatbot'a ait bağlantı silinir.
        # Eğer bir doküman sadece bu chatbota bağlıysa ve onu tamamen silmek istiyorsanız, daha karmaşık bir mantık gerekir.
        # Şimdilik, sadece chatbot_documents bağlantısını ve chatbot'u silmek yeterlidir.

        # Chatbot'u sil
        cursor.execute("DELETE FROM chatbots WHERE id = %s RETURNING id;", (chatbot_id,))
        deleted_id = cursor.fetchone()

        if not deleted_id:
            raise HTTPException(status_code=404, detail=f"Chatbot ID {chatbot_id} bulunamadı veya silinemedi.")

        conn.commit()

        # İlişkili FAISS indeks dosyasını diskten sil
        chatbot_faiss_path = os.path.join(FAISS_INDEX_DIR, f"faiss_index_{chatbot_id}.bin")
        if os.path.exists(chatbot_faiss_path):
            os.remove(chatbot_faiss_path)
            print(f"Chatbot ID {chatbot_id} için FAISS indeksi dosyası silindi.")

        return Response(status_code=204) # 204 No Content

    except Exception as e:
        conn.rollback()
        print(f"Chatbot silme hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Chatbot silinirken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()

# Chatbot'a yüklenen belirli bir dokümanı kaldırma endpoint'i (İsteğe Bağlı ama İyi olur)
@app.delete("/chatbots/{chatbot_id}/documents/{document_id}", status_code=204)
async def remove_document_from_chatbot(chatbot_id: int, document_id: int):
    """Belirli bir dokümanı belirli bir chatbota bağlantısından kaldırır."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Chatbot ve doküman bağlantısının varlığını kontrol et
        cursor.execute(
            "SELECT COUNT(*) FROM chatbot_documents WHERE chatbot_id = %s AND document_id = %s;",
            (chatbot_id, document_id)
        )
        if cursor.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail="Belirtilen chatbot ve doküman bağlantısı bulunamadı.")

        # chatbot_documents tablosundaki bağlantıyı sil
        cursor.execute(
            "DELETE FROM chatbot_documents WHERE chatbot_id = %s AND document_id = %s;",
            (chatbot_id, document_id)
        )
        conn.commit()

        # FAISS indeksini yeniden oluştur (veya güncelleyip kaydet)
        # Bu kısım karmaşıklaşabilir, çünkü bir dokümanı FAISS'ten tam olarak kaldırmak zordur.
        # En basit yol, FAISS indeksini tamamen yeniden oluşturmaktır (dokümanları DB'den çekerek).
        # Büyük indeksler için performans sorunu yaratabilir.
        # Daha verimli bir yöntem, FAISS'te "soft delete" veya "rebuild on demand" uygulamaktır.
        # Şimdilik, sadece FAISS indeksini yenilemiyoruz, çünkü bir sonraki yüklemede veya
        # yeni doküman eklemede indeks otomatik güncellenecektir.
        # Veya basitçe tüm dokümanları çekip yeniden indeksleyebiliriz (büyük verilerde sorun).
        # Daha iyi bir yaklaşım: İndeksi yeniden oluşturmak için bir yardımcı fonksiyon yazmak.
        
        # Basit bir yeniden oluşturma örneği (performans açısından sorunlu olabilir):
        # documents_for_reindexing = []
        # cursor.execute("""
        #     SELECT d.page_number, d.content, cd.original_filename, cd.chatbot_id, d.id as doc_id
        #     FROM documents d
        #     JOIN chatbot_documents cd ON d.id = cd.document_id
        #     WHERE cd.chatbot_id = %s;
        # """, (chatbot_id,))
        # for page_num, content, filename, cb_id, d_id in cursor.fetchall():
        #     new_doc = Document(page_content=content, metadata={"page": page_num, "original_filename": filename, "chatbot_id": cb_id, "doc_id": d_id})
        #     documents_for_reindexing.append(new_doc)
        
        # faiss_base_index = faiss.IndexFlatL2(GEMINI_EMBEDDING_DIM)
        # current_faiss_index = FAISS(
        #     embedding_function=embeddings.embed_query,
        #     index=faiss_base_index,
        #     docstore=InMemoryDocstore(),
        #     index_to_docstore_id={}
        # )
        # if documents_for_reindexing:
        #     current_faiss_index.add_documents(documents_for_reindexing)
        # save_faiss_index(current_faiss_index, chatbot_id)
        # print(f"Chatbot ID {chatbot_id} için FAISS indeksi yeniden oluşturuldu.")


        return Response(status_code=204)

    except Exception as e:
        conn.rollback()
        print(f"Doküman kaldırma hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Doküman kaldırılırken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()

# Bir chatbota ait tüm dokümanları listeleme endpoint'i (Frontend için faydalı)
@app.get("/chatbots/{chatbot_id}/documents/", response_model=List[dict])
async def list_chatbot_documents(chatbot_id: int):
    """Belirli bir chatbota ait tüm dokümanları listeler."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Chatbot'un varlığını kontrol et
        cursor.execute("SELECT COUNT(*) FROM chatbots WHERE id = %s;", (chatbot_id,))
        if cursor.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail=f"Chatbot ID {chatbot_id} bulunamadı.")

        cursor.execute("""
            SELECT cd.document_id, cd.original_filename, d.page_number
            FROM chatbot_documents cd
            JOIN documents d ON cd.document_id = d.id
            WHERE cd.chatbot_id = %s
            GROUP BY cd.document_id, cd.original_filename, d.page_number
            ORDER BY cd.original_filename, d.page_number;
        """, (chatbot_id,))
        
        documents_data = {}
        for doc_id, filename, page_number in cursor.fetchall():
            if filename not in documents_data:
                documents_data[filename] = {
                    "filename": filename,
                    "document_ids": [],
                    "pages": []
                }
            documents_data[filename]["document_ids"].append(doc_id)
            documents_data[filename]["pages"].append(page_number)
        
        # Liste haline getir
        response_list = [
            {"filename": k, "document_ids": list(set(v["document_ids"])), "pages": sorted(list(set(v["pages"])))}
            for k, v in documents_data.items()
        ]
        
        return response_list

    except Exception as e:
        print(f"Chatbot dokümanlarını listeleme hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Chatbot dokümanları listelenirken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()



class ChatRequest(BaseModel):
    query: str


# Selamlama kalıplarını belirleyelim
GREETING_PATTERNS = ["merhaba", "selam", "günaydın", "iyi günler", "iyi akşamlar", "iyi geceler", "nasılsın", "naber"]

@app.post("/chatbots/{chatbot_id}/chat/")
async def chat_with_chatbot(chatbot_id: int, request: ChatRequest):
    """
    Belirli bir chatbot'a göre kullanıcı sorularını yanıtlar.
    Konuşma geçmişini yönetir ve Guardrails ile çıktıyı doğrular.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name, boundary_text FROM chatbots WHERE id = %s;", (chatbot_id,))
        chatbot_data = cursor.fetchone()
        if not chatbot_data:
            raise HTTPException(status_code=404, detail=f"Chatbot ID {chatbot_id} bulunamadı.")
        chatbot_name, boundary_text = chatbot_data

        current_faiss_index = load_or_create_faiss_index(chatbot_id)

        context_str = ""
        if current_faiss_index is None or (hasattr(current_faiss_index.index, 'ntotal') and current_faiss_index.index.ntotal == 0):
            print(f"Uyarı: '{chatbot_name}' için henüz taranmış bir belge bulunmuyor. Genel bilgi ile devam ediliyor.")
        else:
            # Kullanıcının sorgusuyla ilgili dokümanları çek
            # LangChainDeprecationWarning'i çözmek için .invoke() kullanıyoruz.
            docs = await current_faiss_index.as_retriever().ainvoke(request.query)
            context_str = "\n".join([doc.page_content for doc in docs])


        loaded_chat_history_messages = load_chat_history_from_db(chatbot_id)

        # LangChain memory nesnesini oluştur (Bu deprecation uyarısı devam edebilir, LangChain'in iç yapısıyla ilgili)
        memory = ConversationBufferWindowMemory(
            memory_key="chat_history", 
            return_messages=True, 
            output_key='answer',
            k=5 
        )
        # Geçmiş mesajları memory'ye ekle
        for msg in loaded_chat_history_messages:
            if isinstance(msg, HumanMessage):
                memory.chat_memory.add_user_message(msg.content)
            elif isinstance(msg, AIMessage):
                memory.chat_memory.add_ai_message(msg.content)

        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-preview-05-20", temperature=0.7, google_api_key=GOOGLE_API_KEY)

        # Guardrails için mesaj listesini oluştur
        messages_for_guardrails: List[Dict[str, str]] = []

        # Geçmişteki konuşmaları mesaj listesine ekle
        for msg in memory.chat_memory.messages:
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            messages_for_guardrails.append({"role": role, "content": msg.content})

        # Kullanıcının mevcut sorusunu küçük harfe çevirerek selamlama tespiti yapalım
        user_query_lower = request.query.lower().strip()

        # Eğer kullanıcının sorgusu bir selamlama ise, bağlamı eklemeyelim
        # Daha sofistike bir selamlama tespiti için burası geliştirilebilir.
        is_greeting = False
        for pattern in GREETING_PATTERNS:
            if pattern in user_query_lower:
                is_greeting = True
                break
        
        # Bağlamı (ilgili dokümanlar) bir kullanıcı mesajı olarak ekle (eğer varsa VE bir selamlama DEĞİLSE)
        if context_str and not is_greeting:
            messages_for_guardrails.append({"role": "user", "content": f"İşte kullanabileceğin bilgiler:\n<documents>\n{context_str}\n</documents>"})
        
        # Kullanıcının mevcut sorusunu ekle
        messages_for_guardrails.append({"role": "user", "content": request.query})


        # Guardrails'ı kullanarak LLM'den yanıt al
        try:
            validated_output = guard_therapist(
                call_llm_with_guardrails, 
                llm_model=llm,            
                messages=messages_for_guardrails, 
                num_reasks=2              
            )
            
            response_data_from_guardrails = None

            if hasattr(validated_output, 'raw_llm_output') and isinstance(validated_output.raw_llm_output, str):
                raw_llm_output_str = validated_output.raw_llm_output
                
                if raw_llm_output_str.startswith("```json") and raw_llm_output_str.endswith("```"):
                    json_content_str = raw_llm_output_str[len("```json\n"):-len("\n```")]
                else:
                    json_content_str = raw_llm_output_str
                
                try:
                    parsed_json_output = json.loads(json_content_str)
                    print(f"DEBUG: LLM'den gelen ham çıktı başarıyla JSON'a dönüştürüldü.")
                    
                    if "therapist_response_schema" in parsed_json_output and \
                    isinstance(parsed_json_output["therapist_response_schema"], dict):
                        response_data_from_guardrails = parsed_json_output["therapist_response_schema"]
                    else:
                        print(f"HATA: 'therapist_response_schema' anahtarı bulunamadı veya dict değil. İçerik: {parsed_json_output}")
                        raise ValueError("Guardrails çıktısı beklenmeyen bir yapıya sahip.")

                except json.JSONDecodeError as e:
                    print(f"HATA: Ayıklanan string JSON'a dönüştürülemedi. Hata: {e}")
                    print(f"Ayıklanmaya çalışılan string: \n{json_content_str}")
                    raise ValueError(f"LLM'den gelen yanıt JSON formatında değil: {e}")
                except Exception as inner_e:
                    print(f"HATA: Guardrails çıktısı işlenirken beklenmedik bir hata oluştu: {inner_e}")
                    raise ValueError("Guardrails çıktısı işlenirken hata oluştu.")
            
            else:
                print(f"HATA: 'raw_llm_output' özelliği bulunamadı veya string değil. Tip: {type(validated_output)}, İçerik: {validated_output}")
                raise ValueError("Guardrails'tan beklenen ham LLM çıktısı alınamadı.")

            if not isinstance(response_data_from_guardrails, dict) or "response" not in response_data_from_guardrails:
                print(f"HATA: Nihai response_data_from_guardrails bir dict değil veya 'response' anahtarı eksik. Tip: {type(response_data_from_guardrails)}, İçerik: {response_data_from_guardrails}")
                raise ValueError("Guardrails'tan beklenen nihai yanıt formatı uygun değil.")


            therapist_response = response_data_from_guardrails.get("response")
            sentiment_score = response_data_from_guardrails.get("sentiment_score")
            safety_flag = response_data_from_guardrails.get("safety_flag")

            save_chat_message_to_db(chatbot_id, "user", request.query)
            save_chat_message_to_db(chatbot_id, "bot", therapist_response)

            return JSONResponse(
                status_code=200,
                content={
                    "answer": therapist_response,
                    "sentiment_score": sentiment_score,
                    "safety_flag": safety_flag
                }
            )

        except Exception as guardrails_or_llm_e:
            print(f"Guardrails veya LLM işleme hatası: {guardrails_or_llm_e}")
            
            error_message = "Üzgünüm, şu anda yanıtımı oluştururken bir sorun oluştu. Profesyonel bir destek almak isterseniz, lütfen bir uzmana danışın."
            
            # Guardrails'tan gelen özel hata mesajlarını yakala ve daha spesifik yanıtlar ver
            if "Validation failed for field" in str(guardrails_or_llm_e):
                error_message = f"Yanıt formatı veya içerik doğrulaması başarısız oldu. Lütfen tekrar deneyin. Detay: {str(guardrails_or_llm_e)}"
            elif "NotFound: 404 models" in str(guardrails_or_llm_e):
                error_message = "Chatbot modeline erişimde bir sorun var. Lütfen daha sonra tekrar deneyin."
            elif "Invalid request" in str(guardrails_or_llm_e) or "Please ensure that your inputs are in the expected format" in str(guardrails_or_llm_e):
                error_message = "Modelin yanıtı işlenirken bir problem oluştu (geçersiz istek formatı). Lütfen farklı bir şekilde ifade etmeyi deneyin."
            elif "is-not-medical-advice" in str(guardrails_or_llm_e):
                error_message = "Üzgünüm, tıbbi tavsiye veremem. Bu tür konularda profesyonel bir uzmana danışmalısınız."
            elif "is-not-harmful" in str(guardrails_or_llm_e):
                error_message = "Güvenliğiniz benim için çok önemli. Lütfen bir kriz hattına veya uzmana başvurun."
            elif "is-not-legal-financial-advice" in str(guardrails_or_llm_e):
                error_message = "Hukuki veya finansal konularda tavsiye veremem. Lütfen ilgili alanda bir profesyonele danışın."
            elif "is-not-overly-long" in str(guardrails_or_llm_e):
                error_message = "Yanıtım çok uzun olamaz. Lütfen sorunuzu daha kısa tutmaya çalışın veya daha genel bir soru sorun."
            elif "is-empathetic-and-supportive" in str(guardrails_or_llm_e):
                error_message = "Yanıtım yeterince empatik değildi. Üzgünüm, daha iyi olacağım. Lütfen kendinizi nasıl hissettiğinizi tekrar ifade edin."


            return JSONResponse(
                status_code=500,
                content={"answer": error_message, "error_details": str(guardrails_or_llm_e)}
            )

    except HTTPException as e:
        # FastAPI'nin kendi HTTP hatalarını doğrudan ilet
        raise e
    except Exception as e:
        print(f"Genel chatbot sohbet hatası (dış blok): {e}")
        # Diğer genel hatalar için yakalama
        raise HTTPException(status_code=500, detail=f"Soru işlenirken beklenmeyen bir hata oluştu: {e}. Güvenliğiniz benim için önemli.")
    finally:
        cursor.close()
        conn.close()

    

# --- Yeni Chatbot Yönetim Endpoints'leri ---

@app.get("/chatbots/{chatbot_id}/history/")
async def get_chatbot_history(chatbot_id: int):
    """
    Belirli bir chatbot'un sohbet geçmişini döndürür.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT sender, message, timestamp FROM chat_messages WHERE chatbot_id = %s ORDER BY timestamp ASC;",
            (chatbot_id,)
        )
        history_rows = cursor.fetchall()
        
        history_list = []
        for sender, message, timestamp in history_rows:
            history_list.append({
                "sender": sender,
                "message": message,
                "timestamp": timestamp.isoformat() # Zaman damgasını ISO formatında döndür
            })
        
        return JSONResponse(
            status_code=200,
            content={"history": history_list}
        )
    except Exception as e:
        print(f"Chat history retrieval error: {e}")
        raise HTTPException(status_code=500, detail=f"Sohbet geçmişi alınırken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()


@app.post("/chatbots/", response_model=ChatbotResponse)
async def create_chatbot(request: CreateChatbotRequest):
    """Yeni bir chatbot (persona) oluşturur."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO chatbots (name, description, boundary_text) VALUES (%s, %s, %s) RETURNING id;",
            (request.name, request.description, request.boundary_text)
        )
        chatbot_id = cursor.fetchone()[0]
        conn.commit()

        # Yeni oluşturulan chatbot için boş bir FAISS indeksi oluştur (ve diske kaydet)
        new_faiss_index = load_or_create_faiss_index(chatbot_id)
        save_faiss_index(new_faiss_index, chatbot_id)

        return ChatbotResponse(
            id=chatbot_id,
            name=request.name,
            description=request.description,
            boundary_text=request.boundary_text
        )
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=400, detail="Bu isimde bir chatbot zaten mevcut.")
    except Exception as e:
        print(f"Chatbot oluşturma hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Chatbot oluşturulurken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()

@app.get("/chatbots/", response_model=List[ChatbotResponse])
async def list_chatbots():
    """Tüm kayıtlı chatbot'ları listeler."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, name, description, boundary_text FROM chatbots ORDER BY name;")
        chatbots_data = cursor.fetchall()
        
        chatbots_list = []
        for cb_id, name, description, boundary_text in chatbots_data:
            chatbots_list.append(ChatbotResponse(
                id=cb_id,
                name=name,
                description=description,
                boundary_text=boundary_text
            ))
        return chatbots_list
    except Exception as e:
        print(f"Chatbot listeleme hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Chatbot'lar listelenirken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()