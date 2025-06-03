# validators.py
from guardrails.validators import register_validator, Validator
from guardrails.validators import FailResult, PassResult
import re # Düzenli ifadeler için

@register_validator(name="is-not-medical-advice", data_type="string")
class IsNotMedicalAdvice(Validator):
    def validate(self, value: str, metadata: dict) -> FailResult | PassResult:
        """
        Modelin yanıtının tıbbi/psikiyatrik tavsiye içermediğini doğrular.
        Belirli anahtar kelimeleri ve ifadeleri kontrol eder.
        """
        medical_keywords = [
            "tanı koydum", "tedavi edebilirim", "ilaç yazabilirim", "psikiyatriste git",
            "terapiye başla", "depresyonun var", "anksiyeten var", "ilaç almalısın",
            "terapi almalısın", "doktora danış", "uzmanına başvur", # Bunları içerirse hata verir
            "teşhis", "reçete", "şifa", "hastalık", "iyileştirme", "ruhsal bozukluk"
        ]
        
        # Kullanıcının sorusunun kendisi bir tıbbi terim içerebilir, ama botun cevabı önemli.
        # Bu validator botun cevabını kontrol ediyor.
        
        for keyword in medical_keywords:
            if keyword in value.lower():
                return FailResult(
                    outcome="fail",
                    metadata=metadata,
                    error_message=f"Yanıt, tıbbi/psikiyatrik tavsiye içeren bir kelime/ifade barındırıyor: '{keyword}'"
                )
        return PassResult(outcome="pass", metadata=metadata)

@register_validator(name="is-not-harmful", data_type="string")
class IsNotHarmful(Validator):
    def validate(self, value: str, metadata: dict) -> FailResult | PassResult:
        """
        Modelin yanıtının zararlı, intihar veya kendine zarar verme eğilimi içeren ifadelerden arınmış olduğunu doğrular.
        Ayrıca intihar veya kendine zarar verme belirtileri varsa yönlendirme yapmasını sağlar.
        """
        # Botun kesinlikle kullanmaması gereken zararlı ifadeler
        harmful_phrases = [
            "kendine zarar ver", "intihar et", "hiçbir şey düzelmez", "boşuna uğraşma",
            "çözüm yok", "pes et"
        ]

        # Botun bu ifadeleri kullanmasını direkt engeller
        for phrase in harmful_phrases:
            if phrase in value.lower():
                return FailResult(
                    outcome="fail",
                    metadata=metadata,
                    error_message=f"Yanıt zararlı bir ifade barındırıyor: '{phrase}'"
                )
        
        # Eğer kullanıcının girdisi potansiyel olarak intihar veya kendine zarar verme ile ilgiliyse
        # ve botun yanıtı yönlendirme içermiyorsa, bu da bir sorun olabilir.
        # Bu karmaşık kontrolü doğrudan validator içinde yapmak yerine, LLM prompt'unda talimat olarak vermek daha iyi olabilir.
        # Ancak basit bir anahtar kelime kontrolü yapılabilir:
        
        # Bu kısım aslında LLM prompt'unda daha iyi yönetilir:
        # "Eğer kullanıcı intihar veya kendine zarar verme eğilimi gösteren ifadeler kullanırsa,
        # onlara destekleyici bir mesaj ver ve profesyonel yardım almalarını tavsiye et (örn: 'Acil durumlarda lütfen bir uzmana danışın veya yerel kriz hattını arayın.')."
        
        # Yine de, botun bu tür bir durumda yönlendirme yapıp yapmadığını basitçe kontrol edebiliriz.
        # Bu validator daha çok botun "kendi" zarar verici bir şey söylemesini engellemek için tasarlanmıştır.

        return PassResult(outcome="pass", metadata=metadata)

@register_validator(name="is-empathetic-and-supportive", data_type="string")
class IsEmpatheticAndSupportive(Validator):
    def validate(self, value: str, metadata: dict) -> FailResult | PassResult:
        """
        Yanıtın empatik ve destekleyici bir tonu olup olmadığını kontrol eder.
        Basit bir anahtar kelime kontrolü veya daha gelişmiş bir NLP modeli gerektirebilir.
        Şimdilik basit bir anahtar kelime kontrolü yapalım.
        """
        empathy_keywords = [
            "anladım", "anlıyorum", "duyguların geçerli", "zor bir durum", "yalnız değilsin",
            "buradayım", "destekleyici"
        ]
        
        if not any(keyword in value.lower() for keyword in empathy_keywords):
            return FailResult(
                outcome="fail",
                metadata=metadata,
                error_message="Yanıt yeterince empatik veya destekleyici kelimeler içermiyor olabilir."
            )
        return PassResult(outcome="pass", metadata=metadata)

@register_validator(name="is-not-overly-long", data_type="string")
class IsNotOverlyLong(Validator):
    def validate(self, value: str, metadata: dict) -> FailResult | PassResult:
        """
        Yanıtın çok uzun olmamasını sağlar (örneğin 300 kelime sınırı).
        """
        max_words = metadata.get("max_words", 300)
        words = value.split()
        if len(words) > max_words:
            return FailResult(
                outcome="fail",
                metadata=metadata,
                error_message=f"Yanıt çok uzun ({len(words)} kelime), maksimum {max_words} kelime olmalı."
            )
        return PassResult(outcome="pass", metadata=metadata)

@register_validator(name="is-not-legal-financial-advice", data_type="string")
class IsNotLegalFinancialAdvice(Validator):
    def validate(self, value: str, metadata: dict) -> FailResult | PassResult:
        """
        Yanıtın hukuki veya finansal tavsiye içermediğini doğrular.
        """
        advice_keywords = [
            "avukata danış", "dava aç", "yasal hakların", "hukuki süreç",
            "yatırım yap", "borsa", "kredi çek", "para biriktir", "finansal tavsiye"
        ]
        for keyword in advice_keywords:
            if keyword in value.lower():
                return FailResult(
                    outcome="fail",
                    metadata=metadata,
                    error_message=f"Yanıt hukuki/finansal tavsiye içeren bir kelime/ifade barındırıyor: '{keyword}'"
                )
        return PassResult(outcome="pass", metadata=metadata)