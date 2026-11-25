![Warp-Manager](docs/assets/banner.svg)

# Warp-Manager

[English](README.md) · [فارسی](README.fa_IR.md) · [العربية](README.ar_EG.md) · [中文](README.zh_CN.md) · [Español](README.es_ES.md) · [Русский](README.ru_RU.md) · [Türkçe](README.tr_TR.md)

Linux VPS için Cloudflare WARP tünel yöneticisi: bağımsız Gemini ve YouTube politikaları, yerel SOCKS5/HTTP ön ucu, tünel başına watchdog ve sıcak yedekli kapasite ölçekleme.

**0.4.1**

## Gereksinimler ve kurulum

Root, Ubuntu/Debian x86_64, systemd, /dev/net/tun, İnternet ve Python 3.10+ gerekir. Ubuntu 24.04/Python 3.12 gerçek VPS üzerinde denendi. Eksik Docker/Compose ve araçlar kurulur; mevcut Docker korunur. Resmî WARP istemcisi imaj oluşturulurken indirilir ve --accept-tos ile başlatılır.

Tam arşivden veya kaynak kopyasından:

```bash
cd Warp-Manager
sudo bash install.sh
```

Forcus356/Warp-Manager deposu main dalına yüklendikten sonra bağımsız kurucu da kullanılabilir:

```bash
curl -fsSL https://raw.githubusercontent.com/Forcus356/Warp-Manager/main/install_warp_manager.sh -o /tmp/install_warp_manager.sh
sudo bash /tmp/install_warp_manager.sh
```

Bootstrap, dal/etiketi değişmez commit kimliğine çözer; tam kaynağı HTTPS ile indirir ve kurulumdan önce commit/SHA-256 gösterir. Yerel kaynak için install.sh kullanılır. En yeni GitHub Release otomatik keşfi yoktur.

## Konsol ve kipler

Kurulu komut warp-pool olarak kalır; warp-google-pool yolları ve servis adları uyumluluk için korunur. Konsol etiketleri Rusçadır; CLI seçenekleri her dilde aynıdır. Terminalde argümansız install.sh menüyü açar. t şablonları gösterir; Easy/Advanced değişiklikleri önizleme ve iptal ile düzenler.

Gemini, anonim model denemesinden SERVING sonucu ister. REFUSED, CAPTCHA ve INCONCLUSIVE başarı sayılmaz. Oturum açılmış hesapta sonuç farklı olabilir.

YouTube, gözlenen Google bölgesi RU olan çıkışları ister; bu bağımsız örnekte Gemini kapalıdır. RU VERIFIED reklamsız kullanım veya video kalitesi garantisi değildir.

```bash
sudo bash install.sh --instance 1 --role gemini --target-gbps 1 --tunnels 12 --impersonate
sudo bash install.sh --instance 2 --role youtube --target-gbps 1 --tunnels 12
```

## Kapasite ve yedekler

target-gbps birimi Gbit/s: 0.1 = 100 Mbit/s; 0 sabit havuzdur. Her tünel ayrı, sonra asıl havuz eşzamanlı ölçülür. Ayrı ölçümlerin toplamı ortak hedefe ulaşıldığını kanıtlamaz. 99 örneğe ve örnek başına 1–128 yuvaya izin verilir; bunlar VPS kapasite garantisi değildir.

Yedek sayısı max(1, floor(N/3)): 10 asıl + 3 yedek; 30 + 10. Yedekler ayrı doğrulanır; yalnız terfi ettiklerinde yeni trafik alır ve eksik yedek yeniden bulunur. Dengeleyici açık/bekleyen bağlantıları ölçülmüş hızla ağırlıklandırır. Tek TCP bağlantısı tek tünelde kalır. Kullanılmayan yuvalar konteyner başlatmaz.

WARP ülke veya belirli çıkış sunucusu PIN seçimi sunmaz. Ülke listeleri Google gözlemini filtreler. Sağlık, karantina, backoff ve döndürme sınırları tünel başınadır.

## Komutlar

```bash
warp-pool
warp-pool --all status
warp-pool --instance 1 check all --full
warp-pool --instance 1 speed all
warp-pool --instance 1 logs -n 100
warp-pool --instance 1 capacity --gbps 1 --max-tunnels 12
warp-pool --instance 1 capacity --gbps 0 --max-tunnels 3
```

check/search/speed/rotate istekleri kuyruğa koyar; çıkış kodu 0 yalnız kabulü belirtir. Sonuç için status/logs kullanın. --all yalnız status/logs/config/xray/update içindir. Kurucu --interval ve menü dakikayı, JSON zamanları saniyeyi kullanır.

## Xray / 3x-ui

Akış Xray → yerel SOCKS5 → WARP → İnternet şeklindedir. Dışa aktarma paneli değiştirmez. Mevcut varsayılan outbound ilk sırada kalmalı, hizmet kuralları genel kurallardan önce gelmelidir. Host/Docker, alan adı iletimi, uzak DNS ve yalnız seçili hizmetler için UDP/443 engeli belgelenmiştir. UDP ASSOCIATE yoktur.

```bash
warp-pool xray-routing --youtube-instance 2 --gemini-instance 1
```

[Xray JSON + sniffing](docs/xray/README.md)

## Güncelleme ve kaldırma

```bash
warp-pool --instance 1 update /root/Warp-Manager
warp-pool --all update /root/Warp-Manager-v0.4.1.zip --sha256 "$EXPECTED_SHA256"
warp-pool --instance 2 uninstall
```

Güncellemeler ayarları, portları ve kayıt volume verilerini korur. HTTPS ZIP için beklenen SHA-256 gerekir. --purge-profiles verilmezse kaldırma yerel kayıtları saklar. --purge-deps yalnız bu yöneticinin kurduğu, artık gerekmeyen bağımlılıkları kaldırmayı ister.

## Testler ve ölçülen sınırlar

1 vCPU/2 GiB VPS üzerinde sekiz gerçek WARP, 8/32/64 istemci testlerini hatasız tamamladı. Sekiz istemcide 1/2/4/8 tünel yaklaşık 85/132/165/172 Mbit/s verdi. CPU %100 oldu; TLS yük üreticisi aynı CPU üzerinde çalıştı. Gigabit veya hatasız 128 gerçek istemci doğrulanmadı. İki Gemini SERVING gözlemi bulundu; sürekli, yedekli hazır havuz kurulmadı. YouTube kurtarma testi 100 Mbit/s kontrol hedefinde geçti.

```bash
python3 scripts/check_project.py
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
```

[Test methodology and results](docs/testing/RESULTS.md)

## Belgeler ve lisans

Ayrıntılı başvuru İngilizce/Rusçadır. Açık paket gerçek VPS adresi, SSH bilgisi veya WARP kayıt profili içermez. Konsol görselleri sentetik örneklerdir, canlı çıkış kanıtı değildir.

[Documentation](docs/README.md) · [Configuration](docs/CONFIGURATION.md) · [Commands](docs/COMMANDS.md) · [Operations](docs/OPERATIONS.md) · [FAQ](docs/FAQ.md) · [Publishing](docs/PUBLISHING.md) · [Security](SECURITY.md)

GPL-3.0-only lisanslı gayriresmî yöneticidir. Ayrı indirilen Cloudflare istemcisinin kendi koşulları geçerlidir.

[LICENSE](LICENSE) · [Attribution](THIRD_PARTY_NOTICES.md) · [Upstream notices](UPSTREAM_NOTICES.md)
