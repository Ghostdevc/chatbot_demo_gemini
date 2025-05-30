# main.py
import os
from dotenv import load_dotenv

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



def create_tables():
    """Gerekirse PostgreSQL tablolarını oluşturur."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # `documents` tablosu (mevcut hali, küçük bir değişiklik: filename artık zorunlu değil çünkü chatbot_documents ile ilişkilenecek)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                page_number INTEGER,
                content TEXT NOT NULL
            );
        """)

        # Yeni `chatbots` tablosu
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chatbots (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE,
                description TEXT,
                boundary_text TEXT
            );
        """)

        # Yeni `chatbot_documents` ara tablosu (Many-to-Many ilişkisi için)
        # Bir chatbot'un birden fazla dokümanı, bir dokümanın birden fazla chatbot'u olabilir.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chatbot_documents (
                chatbot_id INTEGER NOT NULL REFERENCES chatbots(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                original_filename VARCHAR(255) NOT NULL, -- Dokümanın orijinal adı burada tutulacak
                PRIMARY KEY (chatbot_id, document_id)
            );
        """)

        conn.commit()
        print("`documents`, `chatbots` ve `chatbot_documents` tabloları başarıyla kontrol edildi/oluşturuldu.")
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

@app.post("/chatbots/{chatbot_id}/chat/")
async def chat_with_chatbot(chatbot_id: int, request: ChatRequest):
    """
    Belirli bir chatbot'a göre kullanıcı sorularını yanıtlar.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Chatbot bilgilerini al (özellikle boundary_text için)
        cursor.execute("SELECT name, boundary_text FROM chatbots WHERE id = %s;", (chatbot_id,))
        chatbot_data = cursor.fetchone()
        if not chatbot_data:
            raise HTTPException(status_code=404, detail=f"Chatbot ID {chatbot_id} bulunamadı.")
        chatbot_name, boundary_text = chatbot_data

        # Chatbot'a ait FAISS indeksini yükle
        current_faiss_index = load_or_create_faiss_index(chatbot_id)

        # FAISS indeksi boşsa veya içinde hiç vektör yoksa hata döndür
        if current_faiss_index is None or (hasattr(current_faiss_index.index, 'ntotal') and current_faiss_index.index.ntotal == 0):
            raise HTTPException(status_code=404, detail=f"'{chatbot_name}' chatbot'u için henüz yüklü bir belge bulunmamaktadır. Lütfen önce belge yükleyin.")

        # Kullanıcının sorusuna en yakın ilgili belge parçalarını FAISS'ten al
        docs = current_faiss_index.similarity_search(request.query, k=4)

        # ChatGoogleGenerativeAI modelini kullan
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-preview-05-20", temperature=0.7, google_api_key=GOOGLE_API_KEY)

        # Sorguya cevap vermek için context oluştur
        context_docs = [doc.page_content for doc in docs]
        context = "\n\n".join(context_docs)

        # Prompt'a boundary_text'i ekle
        prompt = f"""
        Sen bir chatbot'sun. '{chatbot_name}' isimli bir karakteri veya bilgi alanını temsil ediyorsun.

        {boundary_text if boundary_text else ""}

        Aşağıdaki bağlamı kullanarak kullanıcının sorusunu yanıtla.
        Eğer soruyu bağlamdan yanıtlayamıyorsan, "Verilen bağlamda bu soruyu yanıtlayacak yeterli bilgi bulunmamaktadır." diye belirt ve başka bir cevap üretme.

        Bağlam:
        {context}

        Soru: {request.query}
        Cevap:
        """

        response = llm.predict(prompt) # Gemini modelinden cevabı al

        return JSONResponse(
            status_code=200,
            content={"answer": response}
        )

    except HTTPException as e:
        raise e # FastAPI HTTPException'ı doğrudan yükselt
    except Exception as e:
        print(f"Chatbot sohbet hatası: {e}")
        raise HTTPException(status_code=500, detail=f"Soru işlenirken bir hata oluştu: {e}")
    finally:
        cursor.close()
        conn.close()
    

# --- Yeni Chatbot Yönetim Endpoints'leri ---

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