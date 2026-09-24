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
  --baseline fixtures/synthetic/baseline.json `
  --output-dir outputs/ci
```

Linux/macOS ortamında satır devamı için PowerShell backtick’i yerine `\\` kullanılır. Çıktıda `report.json`, `report.md` ve tarayıcıda açılabilen `report.html` oluşur.

## Gerçek kontrat fixture’ları

Gerçek kontrat demoları bu dağıtım deposuna dahil edilmez; dağıtım ve türev eser lisansı netleşmemiş snapshot’lar yerel kanıt paketinde tutulur. Bu nedenle aşağıdaki komutlar, kullanıcı kendi hak sahibi olduğu veya dağıtım izni aldığı fixture’ları ayrıca sağladığında çalıştırılabilir. Gerçek snapshot ölçümü geçmiş kullanıcı zararını ya da kontratta doğal açık bulunduğunu kanıtlamaz.

Kontrollü maliyet varyantı üretmek için:

```text
python -m tlsci controlled-manifest `
  --source-manifest fixtures/real/manifest.json `
  --output-manifest fixtures/real/controlled-regression-manifest.json `
  --repetitions 500
```

Bu komut özgün script’in code bölümünün başına semantik olarak etkisiz `PUSH nat 0; DROP` çiftleri ekler. Varyant, özgün kontratta doğal bir açık olduğunu göstermez; baseline politikasının gerçek kontrat kodunda kontrollü maliyet artışını yakaladığını test eder.

Repo lisansı `LICENSE` içindeki MIT lisansıdır. `references/` altındaki üçüncü taraf kaynak kodları ve `fixtures/real/` altındaki gerçek mainnet snapshot’ları dağıtım kapsamına dahil değildir. Proje kodunun MIT lisansı, bu üçüncü taraf materyaller için lisans veya yeniden dağıtım izni vermez.

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
