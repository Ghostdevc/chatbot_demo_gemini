import streamlit as st
import requests
import os
import io

# FastAPI backend'inizin çalıştığı URL
BASE_URL = "http://127.0.0.1:8000"

# --- Uygulama Başlığı ve Başlangıç Ayarları ---
st.set_page_config(page_title="AI Chatbot Personalarım", layout="wide")
st.title("🤖 AI Chatbot Personalarım")

# Session state'i başlat veya mevcutsa kullan
if "current_chatbot_id" not in st.session_state:
    st.session_state.current_chatbot_id = None
if "current_chatbot_name" not in st.session_state:
    st.session_state.current_chatbot_name = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = {} # Her chatbot için ayrı sohbet geçmişi
if "chatbots" not in st.session_state:
    st.session_state.chatbots = [] # Tüm chatbotların listesi
if "show_create_bot_form" not in st.session_state:
    st.session_state.show_create_bot_form = False

# --- Backend'den Chatbot Listesini Çekme Fonksiyonu ---
@st.cache_data(ttl=60) # 60 saniye boyunca önbellekte tut
def fetch_chatbots():
    """Backend'den chatbot listesini çeker."""
    try:
        response = requests.get(f"{BASE_URL}/chatbots/")
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Chatbot'lar alınırken hata oluştu: {e}. Backend'in çalıştığından emin olun.")
        return []

# --- Yeni Chatbot Oluşturma Formu ---
def create_new_bot_form():
    """Yeni chatbot oluşturma formunu gösterir."""
    st.subheader("Yeni Chatbot Oluştur")
    with st.form("new_chatbot_form", clear_on_submit=True):
        bot_name = st.text_input("Chatbot Adı", help="Bu chatbot'a vereceğiniz benzersiz bir isim.")
        bot_description = st.text_area("Açıklama (İsteğe Bağlı)", help="Chatbot hakkında kısa bir açıklama.")
        boundary_text = st.text_area("Boundary Metinleri (İsteğe Bağlı)", 
                                     help="Chatbot'un davranışını ve odak alanını sınırlayan yönergeler. Örnek: 'Sadece hukuk belgeleri hakkında cevap ver. Politik konulara değinme.'",
                                     height=150)
        
        uploaded_files = st.file_uploader("Bu Chatbot için Dokümanları Yükle", 
                                           type=["pdf", "txt", "docx"], # Yeni dosya türlerini buraya ekleyin
                                           accept_multiple_files=True,
                                           help="Bu chatbot'un bilgi tabanını oluşturacak belgeler (PDF, TXT, DOCX vb.).")

        submitted = st.form_submit_button("Chatbot'u Kaydet")
        if submitted:
            if not bot_name:
                st.error("Chatbot adı boş bırakılamaz.")
            else:
                try:
                    # 1. Chatbot'u oluştur
                    create_bot_data = {
                        "name": bot_name,
                        "description": bot_description,
                        "boundary_text": boundary_text
                    }
                    create_bot_response = requests.post(f"{BASE_URL}/chatbots/", json=create_bot_data)
                    create_bot_response.raise_for_status()
                    new_chatbot_data = create_bot_response.json()
                    new_chatbot_id = new_chatbot_data["id"]
                    st.success(f"'{bot_name}' adlı chatbot başarıyla oluşturuldu!")

                    # 2. Yüklenen dokümanları tek tek bu chatbota bağla
                    if uploaded_files:
                        st.info("Dokümanlar işleniyor ve chatbot'a ekleniyor...")
                        for uploaded_file in uploaded_files:
                            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")}
                            upload_response = requests.post(
                                f"{BASE_URL}/chatbots/{new_chatbot_id}/upload_document/", 
                                files=files
                            )
                            upload_response.raise_for_status()
                            st.success(f"'{uploaded_file.name}' belgesi başarıyla yüklendi ve '{bot_name}' chatbot'una eklendi.")
                        st.rerun() # Sayfayı yenile ve chatbot listesini güncelle
                    else:
                        st.warning("Hiç doküman yüklenmedi. Chatbot boş bir bilgi tabanı ile oluşturuldu.")
                        st.rerun() # Sayfayı yenile ve chatbot listesini güncelle

                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 400 and "Bu isimde bir chatbot zaten mevcut" in e.response.text:
                        st.error("Bu isimde bir chatbot zaten mevcut. Lütfen başka bir isim seçin.")
                    else:
                        st.error(f"Chatbot oluşturulurken veya doküman yüklenirken HTTP hatası oluştu: {e}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Bir hata oluştu: {e}. Backend'in çalıştığından emin olun.")
                st.session_state.show_create_bot_form = False # Formu kapat
                st.rerun() # Sayfayı yenile

# --- Chatbot Listesini Gösteren Fonksiyon ---
def display_chatbot_list():
    """Oluşturulan chatbot'ları kartlar halinde listeler."""
    st.subheader("Mevcut Chatbot'lar")
    chatbots = fetch_chatbots()
    st.session_state.chatbots = chatbots # Session state'e kaydet

    if not chatbots:
        st.info("Henüz oluşturulmuş bir chatbot bulunmamaktadır.")
        if st.button("Hemen İlk Chatbot'u Oluştur!"):
            st.session_state.show_create_bot_form = True
            st.rerun()
        return

    # Chatbotları yan yana kartlar halinde göster
    cols = st.columns(3) # Her satırda 3 kart
    for i, bot in enumerate(chatbots):
        with cols[i % 3]:
            card_html = f"""
            <div style="
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 15px;
                margin-bottom: 15px;
                box-shadow: 2px 2px 8px rgba(0,0,0,0.1);
                background-color: #f9f9f9;
            ">
                <h4 style="margin-top:0; color:#333;">{bot['name']}</h4>
                <p style="font-size: 0.9em; color:#555;">{bot['description'] if bot['description'] else 'Açıklama yok.'}</p>
            """
            # Butonlar için form kullan, böylece yeniden yüklemede sorun çıkmaz
            with st.container(): # Her kart için ayrı bir container
                st.markdown(card_html, unsafe_allow_html=True)
                col_btn1, col_btn2, col_btn3 = st.columns(3)
                with col_btn1:
                    if st.button("Sohbet Et", key=f"chat_btn_{bot['id']}"):
                        st.session_state.current_chatbot_id = bot['id']
                        st.session_state.current_chatbot_name = bot['name']
                        st.session_state.show_create_bot_form = False
                        st.rerun()
                with col_btn2:
                    if st.button("Düzenle", key=f"edit_btn_{bot['id']}"):
                        st.warning("Düzenleme özelliği henüz aktif değil!") # TODO: Düzenleme formu ekle
                        # st.session_state.edit_chatbot_id = bot['id']
                        # st.session_state.show_edit_bot_form = True
                        # st.rerun()
                with col_btn3:
                    if st.button("Sil", key=f"delete_btn_{bot['id']}"):
                        if st.session_state.current_chatbot_id == bot['id']:
                             st.session_state.current_chatbot_id = None # Eğer aktifse devreden çıkar
                             st.session_state.current_chatbot_name = None
                             
                        try:
                            delete_response = requests.delete(f"{BASE_URL}/chatbots/{bot['id']}")
                            delete_response.raise_for_status()
                            st.success(f"'{bot['name']}' adlı chatbot başarıyla silindi.")
                            st.cache_data.clear() # Önbelleği temizle
                            st.rerun()
                        except requests.exceptions.RequestException as e:
                            st.error(f"Chatbot silinirken hata oluştu: {e}")

# --- Sohbet Ekranı ---
def display_chat_interface():
    """Seçilen chatbot ile sohbet arayüzünü gösterir."""
    chatbot_id = st.session_state.current_chatbot_id
    chatbot_name = st.session_state.current_chatbot_name

    st.subheader(f"💬 '{chatbot_name}' ile Sohbet")
    st.button("↩️ Chatbot Listesine Dön", on_click=reset_chat_selection)

    # Sohbet geçmişini al veya başlat
    if chatbot_id not in st.session_state.chat_history:
        st.session_state.chat_history[chatbot_id] = []

    for message in st.session_state.chat_history[chatbot_id]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Kullanıcıdan mesaj al
    if prompt := st.chat_input("Bir mesaj yazın..."):
        st.session_state.chat_history[chatbot_id].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Yanıt oluşturuluyor..."):
                try:
                    chat_request = {"query": prompt}
                    response = requests.post(f"{BASE_URL}/chatbots/{chatbot_id}/chat/", json=chat_request)
                    response.raise_for_status()
                    assistant_response = response.json()["answer"]
                    st.markdown(assistant_response)
                    st.session_state.chat_history[chatbot_id].append({"role": "assistant", "content": assistant_response})
                except requests.exceptions.RequestException as e:
                    st.error(f"Chatbot'tan yanıt alınırken hata oluştu: {e}")
                    st.session_state.chat_history[chatbot_id].append({"role": "assistant", "content": f"Hata: {e}"})

# --- Navigasyon ve Ana Akış ---
def reset_chat_selection():
    st.session_state.current_chatbot_id = None
    st.session_state.current_chatbot_name = None
    st.session_state.show_create_bot_form = False # Formu da kapat
    st.cache_data.clear() # Chatbot listesi önbelleğini temizle
    st.rerun()

# Sol kenar çubuğu (sidebar)
with st.sidebar:
    st.header("Seçenekler")
    if st.session_state.current_chatbot_id:
        st.button("Ana Sayfa", on_click=reset_chat_selection)
    else:
        # Yeni bot oluşturma butonu sadece ana sayfadayken görünür
        if st.button("➕ Yeni Chatbot Oluştur"):
            st.session_state.show_create_bot_form = True
            st.session_state.current_chatbot_id = None # Sohbet ekranındaysa kapat
            st.session_state.current_chatbot_name = None
            st.rerun()

# Ana içerik alanı
if st.session_state.current_chatbot_id:
    # Bir chatbot seçiliyse sohbet arayüzünü göster
    display_chat_interface()
elif st.session_state.show_create_bot_form:
    # Yeni bot oluşturma formu gösterilecekse
    create_new_bot_form()
else:
    # Hiçbir şey seçili değilse chatbot listesini göster
    display_chatbot_list()