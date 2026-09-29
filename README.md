# Tezos Contract Lifetime Safety CI

Bu MVP, Michelson kontratlarının parametrik storage/input büyümesi altında minimum başarılı integer gas bütçesini ölçer, baseline ile karşılaştırır ve politika ihlalinde CI çıkış kodu üretir.

## Bu araç neyi garanti eder?

Araç, sabitlenmiş bir Octez mockup/protocol bağlamında ölçülebilir gas regresyonlarını görünür kılar. Sonuçlar exact consumed gas, güvenlik denetimi, gelecekteki çağrılabilirlik garantisi veya gerçek kullanıcı zararı kanıtı değildir. `run_code` tarafından döndürülen internal operations MVP’de ikinci kez çalıştırılmaz.

## Kurulum ve ilk doğrulama

Python 3.10+ ve Docker gerekir. Proje kökünde:

```text
python -m tlsci doctor
python -m tlsci verify
python -m tlsci validate --manifest fixtures/synthetic/manifest.json
```

Kurulu paketle aynı CLI şu şekilde de kullanılabilir:

```text
python -m pip install .
tlsci verify
```

## Sentetik büyüme raporu

PowerShell:

```text
python -m tlsci run `
  --manifest fixtures/synthetic/manifest.json `
  --output-dir outputs/ci
```

Bu `manifest.json` eski exploratory şemadır; bounded senaryoda 20.000 gas’lık legacy proje cap’i bulunduğundan rapor `exit 1` ile bitebilir. Bu sonuç protocol limitine ulaşıldığı anlamına gelmez. Güncel tam teknik demo için aşağıdaki explicit schema-v2 korpusunu ve tam protokol tavanını kullan.

Linux/macOS ortamında satır devamı için PowerShell backtick’i yerine `\\` kullanılır. Çıktıda `report.json`, `report.md` ve tarayıcıda açılabilen `report.html` oluşur. Yeni run raporları schema v3 kullanır. Eski schema-v2 regression baseline'ları schema-v1 manifest/`integer_budget_search_v2` ile uyumludur; explicit schema-v2 manifest/`integer_budget_search_v3` için schema-v3 baseline gerekir. Report ve baseline dosyaları mevcut hedeflerin üzerine yazılmaz.

## Gerçek kontrat fixture’ları

`fixtures/growth-v2/` iki pinned upstream kontratın lisans bildirimleriyle birlikte derlenmiş Michelson dosyalarını ve her senaryo/boyut için ayrı storage/input/context fixture’larını içerir. TzSafe imza snapshot’ları, fixture üreticisinin pinli Octez mockup’ta `create_proposal` ve tekrarlı `sign_proposal` çağrılarının çıktısını sonraki çağrıya aktarmasıyla üretilir. FA2 transfer batch’i ile sabit transfer altında büyüyen ilgisiz ledger negatif kontrolü ayrıdır. `sources.json` kaynak commit’lerini ve derlenmiş kod hash’lerini kaydeder.

Korpusu yeniden üretmek için Docker ve sabitlenmiş Octez image’ı gerekir. Araç var olan hedefi ezmez; tracked contract source ve lisansları kullanarak ayrı dizin üret:

```text
python tools/build_growth_corpus.py --output fixtures/growth-v2-rebuilt --timeout 60
python -m tlsci validate --manifest fixtures/growth-v2-rebuilt/manifest.json
```

Tüm 61 vakayı iki kez ölçmek için:

```text
python -m tlsci doctor
python -m tlsci run `
  --manifest fixtures/growth-v2/manifest.json `
  --policy policy.json `
  --output-dir outputs/growth-v2-local `
  --timeout 60 --skip-doctor
```

Yüksek boyutlu FA2 vakası sabitlenmiş protokol tavanını gerçekten aşarsa raporda `protocol_limit_exceeded` beklenen durum olarak görünür ve normal politika exit code `1` üretir. Tamamlanmış raporun case kapsamını, iki geçiş eşitliğini, semantic çıktıları ve seçili noktaların doğrudan Octez tekrarını kontrol etmek için:

```text
python tools/verify_growth_report.py outputs/growth-v2-local/report.json `
  --output outputs/growth-v2-local/acceptance.json
```

20.000 gas proje sinyali ölçümden sonra, aynı raporu yeniden çalıştırmadan uygulanır:

```text
python -m tlsci evaluate `
  --report outputs/growth-v2-local/report.json `
  --policy policies/growth-signal-20000.json `
  --output-dir outputs/growth-v2-policy-20000
```

Bu eşik Foundation şartı veya protokol limiti değildir. Gerçek kontrat ölçümü geçmiş kullanıcı zararını, kullanıcı ihtiyacını, talebi, ödeme niyetini ya da doğal güvenlik açığını kanıtlamaz. `protocol_limit_exceeded`, yalnızca belirtilen script/entrypoint/context altında pinned mockup’ın gas exhaustion döndürdüğünü ifade eder. `single_script_sequence_validated` ise yalnızca kayda alınmış TzSafe `run_code` çağrı zincirini kapsar; internal operations yürütülmez.

## Güncel yerel ölçüm sonucu — 28 Eylül 2026

Bu çalışma ağacında growth-v2’nin 61/61 vakası iki geçişte başarılı oldu; üst seviye hata yoktu, run exit `0` ve reproducibility geçti. Seçilmiş 10 doğrudan Octez replay’i semantic çıktı ve bir-altı gas sınırını doğruladı. Unbounded append size 1→4.096 `410→2.535`; TzSafe `sign_proposal` prior-signers 1→256 `6.154→8.377`; FA2 batch 1→256 `2.150→4.648` minimum successful integer gas budget gösterdi. Bunlar exact consumed gas değildir ve kullanıcı olayı/talebi değildir.

**Tarihsel growth-v2 bounded kabul kararı NO-GO olarak korunur.** Bounded-last-64 fixture’ında size 64→4.096 bütçe `490→2.382` oldu; >64 başlangıç state’leri fixture’ın 64 öğe invariantı dışındadır ama script tüm listeyi iter. Çıkış 64 öğede kalır; bu ölçüm geçerli-state plateau’su kanıtlamaz. Ayrı 0/1/8/64 takip koşusu 435/436/442/490 verdi; bu sonradan tasarlanmış tanı koşusudur ve özgün tarihsel NO-GO’yu değiştirmez. Yeni bounded-sequence-v1 sonucu aşağıdaki ayrı, ulaşılan-state deneyidir. Kullanıcı ihtiyacı, zarar, ödeme isteği, grant kabulü veya gelir iddiası çıkarılamaz.

## Bounded last-64 ardışık yaşam süresi deneyi

`fixtures/bounded-sequence-v1/protocol.json` yeni, sürümlenmiş deneyin sabit kurallarını ve kaynak özetlerini taşır. Üretici boş depodan başlar; aynı pinned Michelson kontratına `Unit` girdisini 4.096 kez Octez mockup üzerinde uygular ve her dönen depoyu hemen sonraki çağrıya verir. `completed_calls` ile `storage_cardinality` ayrı tutulur. Önceden kaydedilmiş noktalar 0, 1, 8, 63, 64, 65, 256, 1.024 ve 4.096 çağrıdır. 64 dolduktan sonraki beş noktada minimum başarılı gas bütçesi birebir aynı olmalıdır.

Tam protokol ve hata koşulları [`docs/bounded-sequence-v1-protocol.md`](docs/bounded-sequence-v1-protocol.md) içinde; üretici, doğrulayıcı ve birleştirilmiş v3 kabul aracı `tools/` altında tanımlıdır. Üretici tek başına yürütüldüğünde Docker gerekir ve run başına altı saatlik üst sınır uygular. Herhangi bir sonuç görülmeden kabul kuralı değiştirilmez. Yeni deney geçse bile iddia yalnızca bu fixture, sabit girdi/bağlam ve pinned mockup için geçerlidir; kullanıcı ihtiyacı, mainnet zararı veya ödeme talebi hakkında kanıt oluşturmaz.

## Hosted bounded-sequence doğrulaması — 29 Eylül 2026

Sabitlenmiş public `main` commit’i `65e7f021416efeb9f66dfa0af141d80c2cedb23b` için manual workflow run `36602735671` / attempt `1`, üç ayrı temiz Ubuntu hosted runner’ında (`one`, `two`, `three`) tamamlandı. Her runner iki yeni 4.096-transition zinciri, dokuz checkpoint ve minimum-minus-one doğrudan sınır tekrarlarını yürüttü. Üç runner’ın bounded raporları karşılaştırmada eşleşti ve fail-closed hosted kabul sonucu `hosted_technical_go: go` oldu. Tarihsel growth-v2 sonucu paket içinde `measured_technical_criteria_not_met` olarak korunur.

İmzalanmamış fakat checksum’lanmış kabul özeti SHA-256 `d1ca275b62ff500f5b7d14abee30956d8ded7744c7bdc95ad2f93e21e428a2df`; yayımlanan evidence arşivi SHA-256 `2e07e7ba3c2b9395108df6492a5679aa1e8277635a975cc33eab23d71bca9ecc` değerindedir. [Immutable hosted evidence prerelease](https://github.com/Enver0908/tezos-contract-lifetime-safety-ci/releases/tag/technical-mvp-go-65e7f021416e-run-36602735671-a1) ve [workflow run](https://github.com/Enver0908/tezos-contract-lifetime-safety-ci/actions/runs/36602735671) herkese açık olarak incelenebilir.

Bu sonuç, yalnızca isimlendirilmiş fixture’ların sabit input/context ve pinned Octez mockup altında üç temiz runner’da tekrar üretilebilirliğini kanıtlar. Bağımsız kullanıcı incelemesi, kullanıcı talebi, ödeme niyeti, mainnet olayı, grant kabulü veya gelir kanıtı değildir.

Kontrollü maliyet varyantı üretmek için:

```text
python -m tlsci controlled-manifest `
  --source-manifest fixtures/real/manifest.json `
  --output-manifest fixtures/real/controlled-regression-manifest.json `
  --repetitions 500
```

Bu komut özgün script’in code bölümünün başına semantik olarak etkisiz `PUSH nat 0; DROP` çiftleri ekler. Varyant, özgün kontratta doğal bir açık olduğunu göstermez; baseline politikasının gerçek kontrat kodunda kontrollü maliyet artışını yakaladığını test eder.

Repo lisansı `LICENSE` içindeki MIT lisansıdır. `fixtures/growth-v2/contracts/` altındaki iki upstream derlenmiş Michelson örneğinin kaynak commit ve MIT bildirimleri `sources.json` ve `*-UPSTREAM-LICENSE.txt` dosyalarında kayıtlıdır. Bu fixture bildirimleri başka `references/` kaynakları veya gerçek mainnet snapshot’ları için genel yeniden dağıtım izni anlamına gelmez.

## Çıkış kodları

| Kod | Anlam |
|---:|---|
| 0 | Ölçüm ve politika başarılı |
| 1 | Geçerli ölçümde politika ihlali veya gas regresyonu |
| 2 | Geçersiz fixture, eksik/uyumsuz baseline veya tekrar üretilebilirlik başarısızlığı |
| 3 | Docker, timeout, RPC veya başka yürütme altyapısı hatası |

## Kapsam dışı

- Internal operations’ın ikinci kez yürütülmesi.
- Exact protocol gas accounting ve storage burn hesabı.
- Formal proof, security audit veya gelecekteki çağrıların garantisi.
- Kullanıcı talebi, ödeme niyeti, grant kabulü veya gelir kanıtı.
- `STEPS_TO_QUOTA` gibi gas-budget-sensitive instruction’ların otomatik ölçümü.
