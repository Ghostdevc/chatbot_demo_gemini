import streamlit as st
import requests
import os
import io
import json # JSON işlemleri için

# BASE_URL'i env. değişkeninden veya varsayılan bir değerle al
# Bu, hem yerel hem de Render'daki backend için geçerli olacaktır.
# Streamlit Cloud'da 'BASE_URL' secret olarak tanımlanmalıdır.
BASE_URL = os.getenv("BASE_URL") # Örneğin Render'da veya yerelde bir ENV olarak tanımlanmalı
if not BASE_URL: # Eğer env. değişkeni tanımlı değilse, yerel varsayılanı kullan
    BASE_URL = "http://127.0.0.1:8000" 
    st.warning(f"BASE_URL ortam değişkeni bulunamadı. Varsayılan olarak '{BASE_URL}' kullanılıyor.")


# --- Uygulama Başlığı ve Başlangıç Ayarları ---
st.set_page_config(page_title="AI Chatbot Personalarım", layout="wide")
st.title("🤖 AI Chatbotlarım")

# Session state'i başlat veya mevcutsa kullan
if "current_chatbot_id" not in st.session_state:
    st.session_state.current_chatbot_id = None
if "current_chatbot_name" not in st.session_state:
    st.session_state.current_chatbot_name = None
# chat_history_from_backend'i doğrudan burada başlatmayalım, 
# çünkü her zaman backend'den çekilecek.
# if "chat_history" not in st.session_state:
#     st.session_state.chat_history = {} # Her chatbot için ayrı sohbet geçmişi
if "chatbots" not in st.session_state:
    st.session_state.chatbots = [] # Tüm chatbotların listesi
if "show_create_bot_form" not in st.session_state:
    st.session_state.show_create_bot_form = False
if "edit_chatbot_id" not in st.session_state:
    st.session_state.edit_chatbot_id = None
if "show_edit_bot_form" not in st.session_state:
    st.session_state.show_edit_bot_form = False

# --- Backend'den Chatbot Listesini Çekme Fonksiyonu ---
@st.cache_data(ttl=60) # 60 saniye boyunca önbellekte tut
def fetch_chatbots():
    """Backend'den chatbot listesini çeker."""
    try:
        response = requests.get(f"{BASE_URL}/chatbots/") # <-- Düzeltme: BASE_URL kullanıldı
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Chatbot'lar alınırken hata oluştu: {e}. Backend'in çalıştığından emin olun.")
        return []

# --- Yeni Chatbot Oluşturma Formu ---
def create_new_bot_form():
    """Yeni bir chatbot oluşturma formunu gösterir."""
    st.subheader("Yeni Chatbot Oluştur")
    with st.form("new_chatbot_form", clear_on_submit=True):
        name = st.text_input("Chatbot Adı", help="Chatbot'unuz için benzersiz bir ad.")
        description = st.text_area("Açıklama (İsteğe Bağlı)", help="Chatbot'un ne hakkında olduğu hakkında kısa bir açıklama.")
        boundary_text = st.text_area("Boundary Metinleri (İsteğe Bağlı)", 
                                         help="Chatbot'un davranışını ve odak alanını sınırlayan yönergeler. Örneğin: 'Sadece hukuk metinlerinden cevap ver.'",
                                         height=150)
        
        uploaded_files = st.file_uploader("Bu Chatbot için Dokümanları Yükle", 
                                             type=["pdf", "txt", "docx"], 
                                             accept_multiple_files=True,
                                             help="Bu chatbot'un bilgi tabanını oluşturacak belgeler (PDF, TXT, DOCX vb.).")
        
        # BUTONLARIN OLDUĞU KISIM BURASI
        col_submit, col_cancel = st.columns([1, 4]) # Butonlar için sütunlar oluştur
        with col_submit:
            submitted = st.form_submit_button("Chatbot Oluştur")
        with col_cancel:
            cancelled = st.form_submit_button("İptal", type="secondary") # İptal butonu eklendi

        if submitted:
            if not name:
                st.error("Chatbot adı boş bırakılamaz.")
            else:
                try:
                    create_response = requests.post(f"{BASE_URL}/chatbots/", json={ # <-- Düzeltme: BASE_URL kullanıldı
                        "name": name,
                        "description": description,
                        "boundary_text": boundary_text
                    })
                    create_response.raise_for_status()
                    chatbot_id = create_response.json()["id"]

                    if uploaded_files:
                        st.info("Dokümanlar işleniyor ve chatbot'a ekleniyor...")
                        success_count = 0
                        fail_count = 0
                        for uploaded_file in uploaded_files:
                            try:
                                files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                                upload_response = requests.post(
                                    f"{BASE_URL}/chatbots/{chatbot_id}/upload_document/", # <-- Düzeltme: BASE_URL kullanıldı
                                    files=files
                                )
                                upload_response.raise_for_status()
                                st.success(f"'{uploaded_file.name}' belgesi başarıyla yüklendi.")
                                success_count += 1
                            except requests.exceptions.RequestException as e:
                                st.error(f"'{uploaded_file.name}' belgesi yüklenirken hata oluştu: {e}")
                                fail_count += 1
                        st.success(f"Chatbot oluşturuldu ve {success_count} belge başarıyla yüklendi.")
                    else:
                        st.success("Chatbot başarıyla oluşturuldu, henüz doküman yüklenmedi.")
                    
                    st.cache_data.clear() # Önbelleği temizle
                    st.session_state.show_create_bot_form = False # Formu kapat
                    st.rerun()

                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 400 and "Bu isimde bir chatbot zaten mevcut" in e.response.text:
                        st.error("Bu isimde bir chatbot zaten mevcut. Lütfen başka bir isim seçin.")
                    else:
                        st.error(f"Chatbot oluşturulurken HTTP hatası oluştu: {e}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Bir hata oluştu: {e}. Backend'in çalıştığından emin olun.")
        
        # İptal butonuna basıldığında
        if cancelled:
            st.session_state.show_create_bot_form = False # Oluşturma formunu kapat
            st.rerun() # Ana menüye dönmek için sayfayı yenile


def edit_existing_bot_form():
    """Mevcut bir chatbot'u düzenleme formunu gösterir."""
    chatbot_id = st.session_state.edit_chatbot_id
    if not chatbot_id:
        st.error("Düzenlenecek chatbot seçilmedi.")
        st.session_state.show_edit_bot_form = False
        st.rerun()
        return

    # Chatbot'un mevcut bilgilerini backend'den çek
    try:
        response = requests.get(f"{BASE_URL}/chatbots/") # <-- Düzeltme: BASE_URL kullanıldı
        response.raise_for_status()
        all_chatbots = response.json()
        current_bot = next((bot for bot in all_chatbots if bot['id'] == chatbot_id), None)
        
        if not current_bot:
            st.error(f"Chatbot ID {chatbot_id} bulunamadı.")
            st.session_state.show_edit_bot_form = False
            st.rerun()
            return
    except requests.exceptions.RequestException as e:
        st.error(f"Chatbot bilgileri alınırken hata oluştu: {e}")
        st.session_state.show_edit_bot_form = False
        st.rerun()
        return

    st.subheader(f"'{current_bot['name']}' Chatbot'u Düzenle")
    with st.form("edit_chatbot_form", clear_on_submit=False): # clear_on_submit False, çünkü mevcut değerleri göstereceğiz
        new_name = st.text_input("Chatbot Adı", value=current_bot['name'], help="Chatbot'un yeni adı.")
        new_description = st.text_area("Açıklama (İsteğe Bağlı)", value=current_bot['description'], help="Chatbot hakkında yeni açıklama.")
        new_boundary_text = st.text_area("Boundary Metinleri (İsteğe Bağlı)", 
                                             value=current_bot['boundary_text'],
                                             help="Chatbot'un davranışını ve odak alanını sınırlayan yeni yönergeler.",
                                             height=150)
        
        col_submit, col_cancel = st.columns([1, 4])
        with col_submit:
            submitted = st.form_submit_button("Değişiklikleri Kaydet")
        with col_cancel:
            cancelled = st.form_submit_button("İptal", type="secondary")

        if submitted:
            if not new_name:
                st.error("Chatbot adı boş bırakılamaz.")
            else:
                try:
                    update_bot_data = {
                        "name": new_name,
                        "description": new_description,
                        "boundary_text": new_boundary_text
                    }
                    update_response = requests.put(f"{BASE_URL}/chatbots/{chatbot_id}", json=update_bot_data) # <-- Düzeltme: BASE_URL kullanıldı
                    update_response.raise_for_status()
                    st.success(f"'{new_name}' adlı chatbot başarıyla güncellendi!")
                    st.cache_data.clear() # Önbelleği temizle
                    st.session_state.show_edit_bot_form = False # Formu kapat
                    st.rerun()
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 400 and "Bu isimde bir chatbot zaten mevcut" in e.response.text:
                        st.error("Bu isimde bir chatbot zaten mevcut. Lütfen başka bir isim seçin.")
                    else:
                        st.error(f"Chatbot güncellenirken HTTP hatası oluştu: {e}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Bir hata oluştu: {e}. Backend'in çalıştığından emin olun.")
        
        if cancelled:
            st.session_state.show_edit_bot_form = False
            st.rerun()


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
            </div>
            """
            st.markdown(card_html, unsafe_allow_html=True) # Kart içeriğini göster

            # Butonlar için ayrı bir container/form kullanın ki rerund'larda sorun çıkmasın
            with st.container():
                col_btn1, col_btn2, col_btn3 = st.columns(3)
                with col_btn1:
                    if st.button("Sohbet Et", key=f"chat_btn_{bot['id']}"):
                        st.session_state.current_chatbot_id = bot['id']
                        st.session_state.current_chatbot_name = bot['name']
                        st.session_state.show_create_bot_form = False
                        st.session_state.show_edit_bot_form = False # Düzenleme formunu da kapat
                        st.rerun()
                with col_btn2:
                    if st.button("Düzenle", key=f"edit_btn_{bot['id']}"):
                        st.session_state.edit_chatbot_id = bot['id']
                        st.session_state.show_edit_bot_form = True
                        st.session_state.show_create_bot_form = False # Oluşturma formunu da kapat
                        st.rerun()
                with col_btn3:
                    if st.button("Sil", key=f"delete_btn_{bot['id']}"):
                        if st.session_state.current_chatbot_id == bot['id']:
                             st.session_state.current_chatbot_id = None # Eğer aktifse devreden çıkar
                             st.session_state.current_chatbot_name = None
                             
                        try:
                            delete_response = requests.delete(f"{BASE_URL}/chatbots/{bot['id']}") # <-- Düzeltme: BASE_URL kullanıldı
                            delete_response.raise_for_status()
                            st.success(f"'{bot['name']}' adlı chatbot başarıyla silindi.")
                            st.cache_data.clear() # Önbelleği temizle
                            st.rerun()
                        except requests.exceptions.RequestException as e:
                            st.error(f"Chatbot silinirken hata oluştu: {e}")


# --- Sohbet Ekranı ---
def display_chatbot_chat_interface():
    """Seçilen chatbot ile sohbet arayüzünü gösterir."""
    chatbot_id = st.session_state.current_chatbot_id
    chatbot_name = st.session_state.current_chatbot_name

    st.subheader(f"Chatbot: {chatbot_name}")

    # Geri dön butonu
    if st.button("← Chatbot Listesine Geri Dön"):
        st.session_state.current_chatbot_id = None
        st.session_state.current_chatbot_name = None
        # Sohbet geçmişini silmek için st.session_state.chat_history_from_backend = [] yapın
        # Bu, farklı bir chatbota geçildiğinde eski geçmişin görünmemesini sağlar.
        if "chat_history_from_backend" in st.session_state:
            del st.session_state.chat_history_from_backend
        st.rerun()

    st.markdown("---")

    # Sohbet geçmişini backend'den çekelim
    # Bu kısmı st.session_state içinde tutarak her rerun'da tekrar çekmemeyi optimize edebiliriz.
    if "chat_history_from_backend" not in st.session_state or st.session_state.get("last_history_chatbot_id") != chatbot_id:
        try:
            history_response = requests.get(f"{BASE_URL}/chatbots/{chatbot_id}/history/") # <-- Düzeltme: BASE_URL kullanıldı
            history_response.raise_for_status()
            st.session_state.chat_history_from_backend = history_response.json().get("history", [])
            st.session_state.last_history_chatbot_id = chatbot_id # Hangi chatbot'un geçmişini yüklediğimizi takip et
        except requests.exceptions.RequestException as e:
            st.error(f"Sohbet geçmişi yüklenirken hata oluştu: {e}")
            st.session_state.chat_history_from_backend = []

    # Streamlit'in sohbet arayüzü
    # Mesajları göster
    for message in st.session_state.chat_history_from_backend:
        if message["sender"] == "user":
            with st.chat_message("user"):
                st.markdown(message["message"])
        else:
            with st.chat_message("assistant"):
                st.markdown(message["message"])

    # Kullanıcıdan girdi al
    if prompt := st.chat_input("Mesajınızı yazın..."):
        # Kullanıcının mesajını UI'a ekle
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Backend'e sorguyu gönder
        try:
            with st.spinner("Yanıt oluşturuluyor..."):
                try:
                    chat_response = requests.post(f"{BASE_URL}/chatbots/{chatbot_id}/chat/", json={"query": prompt})
                    chat_response.raise_for_status()
                    
                    # Backend'den gelen tüm yanıtı al
                    response_data = chat_response.json()
                    assistant_response = response_data.get("answer", "Yanıt alınamadı.")
                    sentiment_score = response_data.get("sentiment_score")
                    safety_flag = response_data.get("safety_flag")

                    with st.chat_message("assistant"):
                        st.markdown(assistant_response)
                        # İsteğe bağlı olarak ek bilgileri gösterebiliriz (debugging veya kullanıcıya bilgi vermek için)
                        if sentiment_score is not None:
                            st.caption(f"Duygu Puanı: {sentiment_score}")
                        if safety_flag:
                            st.caption(f"Güvenlik Kontrolü: {safety_flag}")

                    st.session_state.chat_history_from_backend.append({"sender": "user", "message": prompt})
                    st.session_state.chat_history_from_backend.append({"sender": "bot", "message": assistant_response})
                    
                    st.rerun() 
                except requests.exceptions.RequestException as e:
                    error_detail = "Bilinmeyen bir hata oluştu."
                    if e.response and e.response.status_code == 500:
                        try:
                            error_json = e.response.json()
                            error_detail = error_json.get("answer", error_json.get("detail", error_detail))
                        except ValueError: # JSON decode error
                            error_detail = e.response.text
                    
                    st.error(f"Sohbet sırasında bir hata oluştu: {error_detail}")
                    # Hata durumunda da UI'ı yenileyebiliriz, belki bir uyarı mesajı göstermek için
                    st.session_state.chat_history_from_backend.append({"sender": "bot", "message": f"Hata: {error_detail}"})
                    st.rerun()
        except requests.exceptions.RequestException as e:
            st.error(f"Sohbet sırasında bir hata oluştu: {e}")
            # Hata durumunda da UI'ı yenileyebiliriz, belki bir uyarı mesajı göstermek için
            st.rerun()


def display_chatbot_documents_and_upload():
    """Seçilen chatbota ait dokümanları listeler ve yeni doküman yükleme formunu gösterir."""
    chatbot_id = st.session_state.current_chatbot_id
    if not chatbot_id:
        return

    st.subheader("📚 Doküman Yönetimi")

    # Mevcut dokümanları listele
    try:
        response = requests.get(f"{BASE_URL}/chatbots/{chatbot_id}/documents/") # <-- Düzeltme: BASE_URL kullanıldı
        response.raise_for_status()
        documents = response.json()

        if documents:
            st.write("Mevcut Yüklü Dokümanlar:")
            for doc_info in documents:
                filename = doc_info['filename']
                st.markdown(f"- **{filename}** (Sayfalar: {', '.join(map(str, doc_info['pages']))})")
        else:
            st.info("Bu chatbot için henüz yüklenmiş bir doküman bulunmamaktadır.")

    except requests.exceptions.RequestException as e:
        st.warning(f"Dokümanlar listelenirken hata oluştu: {e}")

    # Yeni doküman yükleme formu (mevcut create_new_bot_form'daki yükleme mantığına benzer)
    st.markdown("---")
    st.subheader("Yeni Doküman Yükle")
    with st.form(key=f"upload_doc_form_{chatbot_id}", clear_on_submit=True):
        uploaded_files = st.file_uploader("Yüklenecek Dokümanlar (PDF, TXT, DOCX)", 
                                             type=["pdf", "txt", "docx"], 
                                             accept_multiple_files=True,
                                             key=f"uploader_{chatbot_id}",
                                             help="Bu chatbot'un bilgi tabanını genişletecek belgeler.")
        submit_upload = st.form_submit_button("Dokümanları Yükle")

        if submit_upload and uploaded_files:
            st.info("Dokümanlar işleniyor ve chatbot'a ekleniyor...")
            success_count = 0
            fail_count = 0
            for uploaded_file in uploaded_files:
                try:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                    upload_response = requests.post(
                        f"{BASE_URL}/chatbots/{chatbot_id}/upload_document/", # <-- Düzeltme: BASE_URL kullanıldı
                        files=files
                    )
                    upload_response.raise_for_status()
                    st.success(f"'{uploaded_file.name}' belgesi başarıyla yüklendi.")
                    success_count += 1
                except requests.exceptions.RequestException as e:
                    st.error(f"'{uploaded_file.name}' belgesi yüklenirken hata oluştu: {e}")
                    fail_count += 1
            st.success(f"Yükleme tamamlandı. {success_count} belge başarılı, {fail_count} belge başarısız.")
            st.cache_data.clear() # Önbelleği temizle
            st.rerun()
        elif submit_upload and not uploaded_files:
            st.warning("Lütfen yüklemek için bir doküman seçin.")



# --- Navigasyon ve Ana Akış ---
def reset_chat_selection():
    st.session_state.current_chatbot_id = None
    st.session_state.current_chatbot_name = None
    st.session_state.show_create_bot_form = False
    st.session_state.show_edit_bot_form = False # Yeni: Düzenleme formunu da kapat
    st.cache_data.clear()
    
    # Sohbet geçmişini de silin
    if "chat_history_from_backend" in st.session_state:
        del st.session_state.chat_history_from_backend
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
    display_chatbot_chat_interface()
    # Sohbet arayüzünün altında doküman yönetimini de göster
    st.markdown("---") # Ayırıcı
    display_chatbot_documents_and_upload()
elif st.session_state.show_create_bot_form:
    # Yeni bot oluşturma formu gösterilecekse
    create_new_bot_form()
elif st.session_state.show_edit_bot_form:
    # Düzenleme formu gösterilecekse
    edit_existing_bot_form()
else:
    # Hiçbir şey seçili değilse chatbot listesini göster
    display_chatbot_list()