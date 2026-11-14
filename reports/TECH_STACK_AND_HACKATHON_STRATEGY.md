# 🚀 NASA Space Apps Challenge: Health Monitoring — Teknoloji Mimarisi, Jüri Tercihleri ve Hackathon Strateji Raporu

**Doküman:** `TECH_STACK_AND_HACKATHON_STRATEGY.md`  
**Rol:** Tech Stack & Hackathon Strategist  
**Hedef:** 3 Kişilik Ekip İçin Mimari Karar, Altyapı ve Kazanma Stratejisi Kılavuzu  
**Proje Deposu:** `c:\Users\efea3\Documents\Health Monitorring NASA` | `efea0/space-apps-2026-health-monitoring`  

---

## 1. Proje & Yarışma Bağlamı Analizi

### 1.1. Mevcut Durum & Çalışma Alanı
* Çalışma alanında yerel denetim gerçekleştirilmiş ve GitHub üzerinde `efea0/space-apps-2026-health-monitoring` adıyla taze (greenfield) bir özel depo oluşturulduğu doğrulanmıştır.
* Ekip 15–20 dakikalık bir ön araştırma fazındadır ve toplantı sonrası doğrudan mimari ve teknoloji kararlarını netleştirerek geliştirmeye başlayacaktır.

### 1.2. Hedeflenen NASA Space Apps Challenge Konusu
* **Resmi Başlık:** *"Create Health Monitoring Software for Astronauts on Space Missions"* (Uzay Görevlerindeki Astronotlar İçin Sağlık İzleme Yazılımı).
* **Temel Problem:** Artemis Ay Üssü ve gelecekteki Mars derin uzay görevlerinde, astronotlar Dünya'dan yüz binlerce veya milyonlarca kilometre uzaktadır.
* **Kritik Dinamik (İletişim Karartması / Latency):** Mars ile Dünya arasındaki tek yönlü radyo sinyali gecikmesi **3 ila 22 dakika** arasındadır (çift yön 6–44 dakika). Acil bir tıbbi durumda (ör. aritmi, akut hipoksi, güneş fırtınası kaynaklı radyasyon zehirlenmesi, SANS sendromu) astronotların Houston Kontrol Merkezi'ndeki uçuş cerrahını araması ve anlık yanıt alması fiziksel olarak imkansızdır.
* **Yazılımın Misyonu:** Sistem **%100 otonom**, uçta çalışan (**edge-first / offline-first**), çok parametreli biyometrik telemetriyi anlık analiz edebilen, anomalileri erkenden yakalayan ve acil klinik karar desteği (**Clinical Decision Support System - CDSS**) sunabilen bir "Yapay Zeka Destekli Uçuş Cerrahı" (AI Flight Surgeon) olmalıdır.
* **Resmi Bilimsel Referanslar:**
  1. **NASA-STD-3001:** *Spaceflight Human-System Standard* (Vol 1: Crew Health, Vol 2: Habitability and Environmental Design).
  2. **NASA Spaceflight Standard Measures Project:** Uzay uçuşlarında toplanan standart biyometrik, nörolojik ve kardiyovasküler veri protokolleri.

---

## 2. Teknoloji Yığını (Tech Stack) & Altyapı Değerlendirmesi

3 kişilik bir ekiple 24–48 saat gibi çok kısıtlı bir sürede yarışırken en büyük risk **"aşırı mühendislik" (over-engineering)** yaparak çalışan bir ürün ve görsel arayüz çıkaramamaktır.

### 2.1. Programlama Dilleri & Backend Framework Karşılaştırması

| Kriter | Rust | Go (Golang) | Python (FastAPI) | TypeScript / Next.js |
| :--- | :--- | :--- | :--- | :--- |
| **Geliştirme Hızı (24–48h)** | 🔴 Çok Düşük (Borrow checker, async Tokio) | 🟡 Orta | 🟢 **Maksimum** (En hızlı prototipleme) | 🟢 Yüksek |
| **Biyomedikal Sinyal İşleme** | 🔴 Kütüphane ekosistemi çok dar | 🔴 Neredeyse yok | 🟢 **Sektör Standardı** (SciPy, NeuroKit2, BioSPPy) | 🟡 Sınırlı (WASM gerekebilir) |
| **Yapay Zeka & Anomali Tespiti**| 🔴 Çok zorlu ve zaman alıcı | 🔴 Yetersiz | 🟢 **Kusursuz** (scikit-learn, PyOD, PyTorch) | 🟡 Yalnızca API çağrıları |
| **Gerçek Zamanlı Telemetri** | 🟢 Çok Hızlı (Düşük kaynak) | 🟢 Mükemmel eşzamanlılık | 🟢 **Mükemmel** (FastAPI async WebSocket) | 🟢 Çok İyi |
| **3 Kişilik Ekip İçi Risk** | 🔴 1 kişinin takılması projeyi kilitler | 🟡 3 farklı dil karmaşası | 🟢 **Sıfır Risk** (Herkes katkı verebilir) | 🟢 Düşük |

#### Neden Rust Değil?
Rust bellek güvenliği ve gömülü uzay uçuş donanımları için mükemmel bir dildir. Ancak hackathon ortamında 3 kişilik ekibin 1 geliştiricisi 14 saat boyunca bellek referansları, lifetime parametreleri ve asenkron veri modelleri ile boğuşursa; ön yüze bağlanacak çalışan bir API zamanında çıkamaz. Hackathon'u en hatasız kod yazan değil, **en işlevsel ve en etkili hikayeyi çalışan bir prototiple anlatan ekip kazanır**.

#### Neden Go Değil?
Go harika bir ağ sunucusudur; ancak NASA projelerinde asıl puan getiren şey biyomedikal sinyal filtreleme (Butterworth filtreleri, R-peak tespiti, HRV spektral analizi) ve anomali tespitidir. Go'da bu kütüphaneler olmadığı için ekip tekerleği yeniden icat etmek zorunda kalır.

#### Neden Python (FastAPI)?
Python; havacılık tıbbı, sinyal işleme, veri bilimi ve modern yapay zekanın ortak dilidir. FastAPI ise asenkron yapısıyla (`async/await`) milisaniyenin altında WebSocket yanıt süreleri verir, Swagger/OpenAPI dokümantasyonunu otomatik üretir ve Pydantic v2 ile güçlü tip güvenliği sunar.

---

### 2.2. Gerçek Zamanlı Veri İletimi: WebSockets vs. SSE vs. MQTT

* **Server-Sent Events (SSE):** Tek yönlü telemetri akışı için pratiktir, HTTP tabanlıdır ve tarayıcı yerleşik `EventSource` ile dinler.
* **WebSockets (ÖNERİLEN KAZANMA SEÇENEĞİ):** Tam çift yönlü (Full-Duplex) iletişim sağlar.  
  * *Neden Kritik?* Jüri canlı demoyu incelerken sadece akan grafikleri izlemekle kalmamalı, ekrandaki interaktif kontrol panelinden simülasyona müdahale edebilmelidir:
    * *"Kardiyak Aritmi / Taşifilaksi Simüle Et"*
    * *"Solar Partikül Olayı (Güneş Fırtınası) Radyasyon Sızıntısı Başlat"*
    * *"Mikroyerçekimi Sıvı Kayması (Fluid Shift) Tetikle"*
  * Butona basıldığı an WebSocket üzerinden FastAPI'ye olay sinyali gider, telemetri parametreleri anlık değişir ve arayüzdeki 3D model ile EKG dalgaları kırmızı alarma geçer. Bu etkileşim jüride unutulmaz bir iz bırakır.

---

### 2.3. Veri Tabanı & Bellek Stratejisi

* **Uzak Durulması Gerekenler:** InfluxDB, TimescaleDB, Kafka veya karmaşık Docker kümeleri. Hackathon'un ilk 4 saatini veritabanı portları ve credential ayarlarıyla harcamak ölümcüldür.
* **Kazanan Yaklaşım:**
  * **In-Memory Rolling Buffer:** Python tarafında `collections.deque(maxlen=1000)` ile son 1000 telemetri noktası (EKG dalgası, nabız, $SpO_2$) RAM'de tutulur ve 30–60 FPS ile WebSocket'e basılır.
  * **DuckDB / SQLite:** Sıfır konfigürasyonlu dosya tabanlı motor. Geçmiş NASA görev kayıtları (Apollo, ISS, Twins Study) DuckDB içine Parquet/CSV olarak gömülür ve "Mission Replay" (Geçmiş Görev Telemetrisini Canlandır) özelliği için kullanılır.

---

### 2.4. 3D & Veri Görselleştirme ("The Wow Factor")

NASA Space Apps'te teknik olarak aynı seviyedeki iki projeden **görsel olarak uzay kokpiti hissi veren proje açık ara kazanır**.

1. **Three.js / React Three Fiber (R3F) ile 3D Dijital İkiz (Digital Twin):**
   * Kaynak: NASA 3D Resources Hub (`github.com/nasa/NASA-3D-Resources`) veya optimize edilmiş `codepawl/nasa-3d-glb` veri setindeki uzay giysisi / astronot GLB modeli.
   * Model Next.js içinde `@react-three/fiber` ve `@react-three/drei` ile yüklenir.
   * **Biyometrik Renk Haritalama (Shader / Mesh Glow):**
     * Göğüs bölgesi: Nabız ve aritmiye göre dinamik kırmızı-yeşil yanıp söner.
     * Baş bölgesi: SANS (Kafa içi basınç artışı) durumunda mor/kırmızı uyarı ışığı yayar.
     * Bacaklar: Mikroyerçekimi sıvı toplanması ve kas atrofisi riskinde sarı vurgulanır.
2. **Klinik Monitör Grafikleri:**
   * **Apache ECharts** veya **Recharts**: Gerçek bir havacılık yoğun bakım monitörü gibi sağdan sola süpürme efekti (sweep line) yapan EKG (Lead II), $SpO_2$ Plethysmogram ve solunum eğrisi.
   * **Kognitif & Mental Sağlık Radarı:** Uyku kalitesi, kognitif reaksiyon süresi, kabin $CO_2$ maruziyeti ve psikolojik stres skorunu gösteren örümcek ağı (radar) grafiği.

---

### 2.5. Önerilen Hibrit Mimari Şeması

```
┌────────────────────────────────────────────────────────────────────────┐
│              FRONTEND (Next.js 15 + TypeScript + Tailwind)              │
│  • React Three Fiber: 3D Holografik Astronot Dijital İkizi             │
│  • Recharts / ECharts: 60 FPS Canlı EKG & Biyometri Monitörü           │
│  • shadcn/ui: Karanlık Havacılık/Kokpit Teması (HUD Design)            │
│  • Acil Müdahale & Simülasyon Kontrol Paneli (Stress Event Triggers)   │
└─────────────────────────────────┬──────────────────────────────────────┘
                                  │ Canlı Telemetri / Event Kontrolü
                                  │ (WebSocket: ws://localhost:8000/ws)
┌─────────────────────────────────┴──────────────────────────────────────┐
│                  BACKEND (Python 3.11+ / FastAPI Async)                │
│  • Telemetri Simülatörü & Veri Oynatıcı (NASA Standard Measures)       │
│  • Biyomedikal Sinyal Filtreleme (SciPy Butterworth + NeuroKit2)       │
│  • Anomali Tespit Motoru (scikit-learn Isolation Forest + Kurallar)    │
│  • DuckDB: NASA Görev Kayıtları ve Zaman Serisi Belleği               │
│  • Uçuş Cerrahı AI (Gemini 2.0 / 1.5 Flash): 15 Dk Gecikme Triage Botu  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Ekip İçi İş Bölümü (3 Kişilik Altın Dağılım)

| Rol | Unvan | Sorumluluk Alanları | Kullanacağı Araçlar |
| :--- | :--- | :--- | :--- |
| **Geliştirici 1** | **Frontend & 3D Lead** | • Next.js dashboard iskeleti, karanlık havacılık HUD teması.<br>• React Three Fiber ile 3D astronot modelinin render edilmesi ve anomali bölgelerinin görselleştirilmesi.<br>• ECharts/Recharts ile canlı 60 FPS EKG dalgası ve vital widget'lar. | Next.js, TypeScript, Tailwind, shadcn/ui, Three.js, R3F, Recharts |
| **Geliştirici 2** | **Backend & Telemetri Lead** | • FastAPI WebSocket ve REST API omurgası.<br>• NASA Standard Measures uyumlu sentetik telemetri jeneratörü.<br>• Anomali enjeksiyon API'leri (Aritmi, Hipoksi, Radyasyon Sızıntısı simülatörü).<br>• DuckDB geçmiş görev veri oynatıcısı. | Python 3.11+, FastAPI, WebSockets, DuckDB, Pydantic |
| **Geliştirici 3** | **AI/Bilim & Storytelling Lead** | • SciPy / NeuroKit2 ile EKG filtreleme ve NASA-STD-3001 medikal kural motoru.<br>• Uçuş Cerrahı Karar Destek Asistanı (Gemini API entegrasyonu).<br>• NASA veri setlerinin entegrasyonu, 30 saniyelik demo akışı, 3 dakikalık video senaryosu ve sunum destesi. | Python, scikit-learn, NeuroKit2, Gemini API, Figma/Canva |

---

## 4. Geçmiş Kazananlar ve Jüri Tercihleri Analizi

### 4.1. NASA Space Apps Jüri Değerlendirme Rubriği (100 Puan)

1. **Impact (Etki - %25):** Çözüm gerçek ve ölümcül bir uzay riskine mi değiniyor? (Dünya ile 20 dakikalık iletişim gecikmesinde astronotun kendi hayatını otonom kurtarması = Maksimum Etki).
2. **Validity & Science (Bilimsel Geçerlilik - %25):** Veriler hayal ürünü mü, yoksa **NASA-STD-3001** ve NASA Life Sciences Archive verilerine mi dayanıyor?
3. **Creativity (Yaratıcılık - %20):** Basit bir akıllı saat paneli yerine 3D dijital ikiz, kestirimci anomali motoru ve otonom karar desteği sunulması.
4. **Relevance (Çağrıya Uygunluk - %15):** NASA'nın "Create Health Monitoring Software for Astronauts on Space Missions" çağrısının 5 temel tehlikesini (Radyasyon, İzolasyon, Mesafe, Yerçekimi Değişimi, Kapalı Ortam) karşılama düzeyi.
5. **Presentation (Sunum & Video - %15):** Canlı demoda jüri 30 saniye içinde sistemin değerini kavrıyor mu?

### 4.2. Başarılı Proje Örnekleri (Benchmarking)
* **SpaceGenes+ (2025 Global Winner - Best Use of Science):** Radyasyon ve mikroyerçekiminin astronot sağlığı üzerindeki birleşik etkilerini NASA GeneLab verileriyle haritalayan etkileşimli bir gösterge paneli kurarak kazandı.
* **42 QuakeHeroes (2024 Global Winner - Best Use of Technology):** Karmaşık sinyal işleme algoritmalarını tarayıcıda akıcı ve interaktif görsel grafiklere dönüştürerek jüriyi büyüledi.
* **AstroGuard AI & Project Horizons:** Çok katmanlı (fizyolojik + psikolojik + çevresel) erken uyarı sistemleriyle finale yükseldi.

### 4.3. Güçlü Teknik Ekiplerin Düştüğü Kritik Hatalar
1. **"Ekransız Kod" (The Black Box Syndrome):** Arka planda 5000 satır karmaşık C++/Rust algoritması yazıp ön yüze sadece konsol çıktısı veya çirkin tablolar koyan ekipler **kesinlikle elenir**. Jüri kodu derlemez, deneyimler.
2. **Canlı Demo Çökmesi:** Harici internet API'lerine veya yavaş LLM yanıtlarına bağımlı kalıp sunum anında hata alan ekipler. *(Çözüm: Yerel sentetik telemetri jeneratörü ve slayta gömülü 60 saniyelik yedek ekran kaydı video).*
3. **30 Saniye Kuralının İhlali:** Sunumun ilk 60 saniyesini "Uzay çok zordur" gibi genel laflarla harcayanlar kaybeder. Giriş cümlesi doğrudan çarpıcı olmalıdır: *"Mars görevinde Houston 20 dakika uzaktadır; astronotun kalp krizi geçirdiğinde bekleyecek 20 dakikası yoktur. İşte geliştirdiğimiz otonom uçuş hekimi..."*
4. **Kara Kutu Yapay Zeka Şüpheciliği:** Havacılık hekimleri açıklanamayan yapay zekaya güvenmez. Salt model tahmini yerine: *"NASA-STD-3001 Kuralı gereği sistolik kan basıncı > 140 mmHg ve HRV %35 düştüğü için anomali skoru 0.89 olarak hesaplandı"* şeklinde açıklanabilir AI (XAI) sunulmalıdır.

---

## 5. Ekibin İlk 24 Saatlik Aksiyon Planı (Actionable Roadmap)

* **Saat 00:00 – 00:30 (Ekip Karar Toplantısı):** Bu rapordaki hibrit mimari (`Next.js` + `FastAPI`) onaylanır. GitHub reposu klonlanır, klasör yapısı oluşturulur (`/frontend`, `/backend`, `/data`).
* **Saat 00:30 – 04:00 (Omurga Kurulumu):**
  * Geliştirici 1: Next.js + Tailwind + shadcn/ui + Three.js uzay giysisi modelini ekranda döndürür.
  * Geliştirici 2: FastAPI WebSocket telemetri akışını (Nabız, Tansiyon, $SpO_2$, Kabin Basıncı, Radyasyon) yazar.
  * Geliştirici 3: NASA-STD-3001 eşik değerlerini ve simülasyon senaryolarını (Aritmi, Radyasyon Sızıntısı, Hipoksi) kodlar.
* **Saat 04:00 – 12:00 (Canlı Entegrasyon):**
  * WebSocket frontend'e bağlanır; canlı EKG grafiği akmaya başlar.
  * Kontrol panelindeki acil durum butonları WebSocket üzerinden telemetriyi bozar ve 3D model anomaliye göre kırmızı yanıp söner.
* **Saat 12:00 – 20:00 (Yapay Zeka Triage & Görev Oynatıcı):**
  * Gemini API tabanlı "Offline Flight Surgeon Triage" modülü eklenir. İletişim kopukluğunda astronota adım adım tedavi protokolü verir.
  * DuckDB üzerinden geçmiş NASA görev verisi oynatma özelliği tamamlanır.
* **Saat 20:00 – 24:00 (Cilalama, Canlı Yayın & Video Hazırlığı):**
  * Vercel (Frontend) ve Render/Railway (Backend) üzerine tek tıkla deploy edilir.
  * 3 dakikalık video için senaryo kaydı alınır, 60 saniyelik kusursuz demo yedeği kaydedilir.

---

*Bu rapor, ekibin zaman kaybetmeden şampiyonluk odaklı bir mimariyle sahaya çıkmasını sağlamak amacıyla hazırlanmıştır.*
