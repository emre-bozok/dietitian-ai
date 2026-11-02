# Diyetisyen AI — tarif ve akşam menüsü

Tarifler mevcut Gemma/Ollama akışıyla çıkarılır. Akşam menüleri ayrı bir yerel
betikle PDF'deki açık `Akşam` başlığı ve numaralı seçeneklerden okunur.
Menü çıkarımı model veya internet kullanmaz; kaynak metnini yeniden yazmaz.
Kullanıcının tercihiyle **öğle ve akşam seçenekleri tek katalogda birleştirilir**
ve tamamı **Akşam yemeği menüsü** adıyla sunulur. Kaynağın özgün öğün başlığı
`sources[].original_meal` alanında izlenebilir.

## Menüleri çıkarma / yenileme

Yeni PDF'leri aynı kaynak klasörüne ekledikten sonra proje klasöründe tek komut:

```sh
./.venv/bin/python extract_recipes.py
```

Bu komut tüm PDF'lerden önce Gemma/Ollama ile tarifleri, ardından Python ile
öğle ve akşam menülerini çıkarır. Ollama servisi ve `gemma4:12b` modeli hazır
olmalıdır. Çıktılar proje klasöründeki tarif ve menü kataloglarının yerini alır;
yeni belgeleri mevcut belgelerle aynı klasörde tutun. Farklı klasör için
`./.venv/bin/python extract_recipes.py /PDF/klasoru` kullanın.

Liste numarası yeni dönemde yeniden 1 olursa PDF'leri aynı dosyanın üzerine
yazmayın. Kaynak klasöründe `Donem_2`, `Donem_3` gibi alt klasörlere koyun;
komut alt klasörleri de tarar. Tarif ve menü kaynak numaraları ortak
`.source_registry.local.json` dosyasında PDF içeriğine göre saklanır. Aynı
isimde farklı içerik ayrı kaynak numarası alır; dosyanın taşınması veya yeni
belgelerin sıralamayı değiştirmesi mevcut numaraları değiştirmez. Bu kayıt
dosyasını kaynak PDF'lerle birlikte koruyun. Aynı içeriğin kopyaları tek
kaynak sayılır. İlk geçişte mevcut yerel kaynak eşlemesinden eski numaralar
korunur.

Bir PDF'nin tarif çıkarımı veya menü çıkarımı başarısız olursa mevcut kayıtlar
korunur. Tarifler değişirse eski embedding indeksi silinir; ilk anlamsal aramada
mevcut Gemini embedding akışıyla yeniden oluşturulur. Bu işlem PDF çıkarımı
sırasında çalışmaz.

Yalnızca menüleri yenilemek için ayrı komut da kullanılabilir:

```sh
./.venv/bin/python extract_dinner_menus.py
```

Farklı kaynak klasörü için:

```sh
./.venv/bin/python extract_dinner_menus.py /PDF/klasoru
```

Bu işlem mevcut `recipes.json` ve `recipes_index.json` dosyalarını değiştirmez.
Çıktılar çalışma dizininden bağımsız olarak proje klasörüne kaydedilir:

- `dinner_menus.json`: yemekler, porsiyonları içeren özgün ifadeler, VEYA
  alternatifleri, notlar, açıkça eşleşen tarif adları, anonim PDF kimlikleri,
  sayfa numaraları ve kaynak seçenek başlıkları.
  Kaynak adları tarif çıkarıcısıyla aynı ortak kuralla `Diyet_Listesi_1.pdf`
  biçiminde üretilir; menü kaynağında uzun dosya özeti gösterilmez.
- `dinner_menus_report.json`: belge başına menü sayıları ve çıkarım uyarıları.
- `dinner_menus_sources.local.json`: anonim PDF kimliğinden gerçek dosya yoluna
  yerel eşleme. Kişisel isim içerebilir; asistan araçları bu dosyayı okumaz.
  Dosya yalnızca sahibi okuyup yazabilecek şekilde kaydedilir.

Her kaynak seçeneği bağımsız menüdür. Öğle veya akşamdan gelmesine bakılmaksızın
aynı yemek ve notlardan oluşan menüler
tekilleştirilir; kaynakları ve seçenek başlıkları ayrı ayrı korunur. Bir tarifin
adı menüde açıkça geçmiyorsa aynı PDF'de olması eşleşme sayılmaz. Benzer adlar,
yazım farkları ve genel `sebze yemeği` ifadeleri otomatik tarif eşleştirmesine girmez.

## Asistan kullanımı

Çalışan ADK uygulamasını yeniden başlatınca yeni araçlar yüklenir:

- `Bu patlıcanlı tirit tarifinin akşam menüsünü göster.`
- `PIRASA BÖREĞİ için akşam yemeği menüsünü göster.`
- `Listelerdeki akşam menülerinden öner.`
- `Başka bir akşam menüsü göster.`

Asistan `find_dinner_menus` ile varsayılan olarak en fazla 5 menü getirir;
toplam eşleşme ve kalan seçenek sayısını ayrıca görür. `get_dinner_menu`
seçilen kaydı ayrıntılarıyla getirir. Kaynak porsiyonları ve alternatifleri
korunur; kalori hesaplama ve yemek değiştirme bu sürümün kapsamında değildir.

## Sınırlar ve veri akışı

Çıkarıcı mevcut listelerin açık başlık/bullet düzenine göre hazırlanmıştır.
Tarif başlıkları, sonraki öğünler ve ölçüm tablosu başlangıçlarında durur;
sayfaya taşan seçenekleri birleştirir. OCR yapmaz. Farklı düzenli veya taranmış
PDF eklendiğinde rapor ve kaynak sayfalar kontrol edilmelidir; uyarı olmaması
tek başına içeriğin eksiksiz veya anonim olduğunu garanti etmez.

Menü çıkarımı ve menü arama fonksiyonları yereldir. Ancak mevcut ADK asistanı
Gemini kullanır; araçların döndürdüğü menü içeriği yanıt üretimi sırasında
bu modele iletilir. Mevcut anlamsal tarif araması da Gemini embedding kullanır.
Ham PDF ve yerel dosya yolu eşlemesi yeni menü araçlarıyla gönderilmez.

## Kontroller

```sh
./.venv/bin/python -m unittest discover -s tests -v
```

Kontroller, sayfa devamlarını, porsiyon/VEYA ifadelerini, seçenek ayrımını,
tarif ve ölçüm tablosu sınırlarını, PDF'den gerçek metin çıkarımını,
tekilleştirmeyi, açık tarif eşleşmesini ve bulunamama durumlarını kapsar.

## Arama indeksi ve API hataları

`gemini-embedding-2` isteklerinde her tarif ayrı bir `Content` nesnesi olarak
gönderilir. Her tarif için tam bir vektör dönmeden indeks kaydedilmez. İndekste
model adı, kaynak tarif dosyasının özeti ve vektör boyutu saklanır; eski,
eksik veya farklı modelle üretilmiş indeks yeniden oluşturulur. Yeni indeks
yalnızca tamamlandığında geçici dosyadan atomik olarak taşınır.

Kota, bağlantı, model veya vektör doğrulama hatasında tarif araması yerel
kelime aramasıyla devam eder. Araç sonucunda `search_mode` alanı `keyword`
veya `hybrid` olur. API hatasından sonra 60 saniye boyunca tekrar anlamsal
arama denenmez. Asistanın sohbet yanıtı üretmesi hâlâ mevcut Gemini modeline
bağlıdır; bu geri dönüş yalnızca tarif aramasını kapsar.

Birden fazla malzemenin veya tarif adının tüm kelimeleri yerelde eşleşiyorsa
arama embedding çağrısı yapmadan tam eşleşmeleri getirir. Örneğin `hellim ve
avokado` sorgusunda yalnızca hellim içeren bir tarif, ikisini de içeren tarifin
yerini alamaz. `ve`, `ile` gibi bağlaçlar sorgudan çıkarılır.

Öğün içindeki `Tatlı Tuzlu Ekmek: ekmek + avokado + hellim ...` gibi isimli
ve miktarlı, pişirme adımı olmayan tarifler de yerel çıkarıcıyla saklanır.
`1. Ana Öğün` başlığı desteklenir. Ayrı yapılışı yoksa `instructions` boş
kalır; asistan kaynakta yapılış olmadığını belirtir. Ek öğün yan ürünleri
tarifin malzemelerine eklenmez. Aynı isimli ama miktar/malzeme veya yapılışı
farklı tarif sürümleri ve kaynakları ayrı korunur; `get_recipe` aracında
`source_pdf` ile sürüm seçilebilir.
