![Warp-Manager](docs/assets/banner.svg)

# Warp-Manager

[English](README.md) · [فارسی](README.fa_IR.md) · [العربية](README.ar_EG.md) · [中文](README.zh_CN.md) · [Español](README.es_ES.md) · [Русский](README.ru_RU.md) · [Türkçe](README.tr_TR.md)

<div dir="rtl">

مدیر تونل‌های Cloudflare WARP برای VPS لینوکس: سیاست‌های مستقل Gemini و YouTube، پراکسی محلی SOCKS5/HTTP، پایش هر تونل و افزایش ظرفیت همراه با ذخیرهٔ آماده.

**0.4.1**

## پیش‌نیاز و نصب

به root، ‏Ubuntu/Debian x86_64، ‏systemd، ‏/dev/net/tun، اینترنت و Python 3.10+ نیاز است. Ubuntu 24.04/Python 3.12 روی VPS واقعی آزموده شده است. ابزارهای لازم و Docker/Compose در صورت نبودن نصب می‌شوند و Docker موجود حفظ می‌شود. کلاینت رسمی WARP هنگام ساخت تصویر دریافت و با --accept-tos اجرا می‌شود.

نصب از آرشیو کامل یا کپی مخزن:

<div dir="ltr">

```bash
cd Warp-Manager
sudo bash install.sh
```

</div>

پس از انتشار مخزن Forcus356/Warp-Manager روی شاخهٔ main، نصب‌کنندهٔ مستقل نیز در دسترس است:

<div dir="ltr">

```bash
curl -fsSL https://raw.githubusercontent.com/Forcus356/Warp-Manager/main/install_warp_manager.sh -o /tmp/install_warp_manager.sh
sudo bash /tmp/install_warp_manager.sh
```

</div>

نصب‌کننده ابتدا مرجع را به commit ثابت تبدیل می‌کند، کل منبع را با HTTPS می‌گیرد و commit و SHA-256 را نمایش می‌دهد. برای منبع محلی از install.sh استفاده کنید. کشف خودکار جدیدترین GitHub Release پیاده‌سازی نشده است.

## کنسول و حالت‌ها

فرمان نصب‌شده warp-pool است؛ مسیرها و سرویس‌های warp-google-pool برای سازگاری حفظ شده‌اند. برچسب‌های کنسول روسی هستند و گزینه‌های CLI در همهٔ راهنماها یکسان‌اند. اجرای بدون آرگومان install.sh در ترمینال منو را باز می‌کند؛ t برای الگوهاست و Easy/Advanced پیش‌نویس را با پیش‌نمایش و امکان لغو ویرایش می‌کند.

Gemini به پاسخ SERVING از آزمون تولید ناشناس نیاز دارد. REFUSED، ‏CAPTCHA و INCONCLUSIVE موفقیت نیستند. نتیجهٔ حساب واردشده می‌تواند متفاوت باشد.

YouTube به منطقهٔ مشاهده‌شدهٔ Google برابر RU نیاز دارد و Gemini در آن نمونهٔ مستقل خاموش است. RU VERIFIED تضمین نبود تبلیغ یا کیفیت ویدئو نیست.

<div dir="ltr">

```bash
sudo bash install.sh --instance 1 --role gemini --target-gbps 1 --tunnels 12 --impersonate
sudo bash install.sh --instance 2 --role youtube --target-gbps 1 --tunnels 12
```

</div>

## ظرفیت و ذخیره

واحد target-gbps گیگابیت‌برثانیه است: 0.1 برابر 100 Mbit/s و 0 به معنی استخر ثابت است. ابتدا هر تونل و سپس مجموعهٔ اصلی به‌طور هم‌زمان اندازه‌گیری می‌شود. جمع آزمون‌های جداگانه رسیدن به ظرفیت مشترک را ثابت نمی‌کند. حداکثر 99 نمونه و 1–128 جایگاه در هر نمونه صرفاً محدودیت پیکربندی است، نه تضمین توان VPS.

تعداد ذخیره max(1, floor(N/3)) است: 10 تونل اصلی + 3 ذخیره، و 30 + 10. ذخیره‌ها جداگانه تأیید می‌شوند و فقط پس از ارتقا ترافیک جدید می‌گیرند؛ سپس کمبود ذخیره جبران می‌شود. وزن تقسیم اتصال بر اساس سرعت اندازه‌گیری‌شده و اتصال‌های فعال/درحال‌برقراری است. هر اتصال TCP روی یک تونل می‌ماند. جایگاه غیرفعال کانتینر اجرا نمی‌کند.

WARP انتخاب کشور یا PIN یک سرور خروجی مشخص را ارائه نمی‌کند. فهرست کشورها نتیجهٔ مشاهدهٔ Google را فیلتر می‌کند. پایش، قرنطینه، وقفهٔ افزایشی و محدودیت چرخش برای هر تونل مستقل است.

## فرمان‌ها

<div dir="ltr">

```bash
warp-pool
warp-pool --all status
warp-pool --instance 1 check all --full
warp-pool --instance 1 speed all
warp-pool --instance 1 logs -n 100
warp-pool --instance 1 capacity --gbps 1 --max-tunnels 12
warp-pool --instance 1 capacity --gbps 0 --max-tunnels 3
```

</div>

check/search/speed/rotate درخواست را در صف می‌گذارند؛ کد خروج 0 فقط پذیرش است. نتیجه را در status/logs ببینید. --all فقط برای status/logs/config/xray/update است. --interval نصب‌کننده و منو دقیقه‌اند؛ زمان‌های JSON ثانیه‌اند.

## اتصال Xray / 3x-ui

مسیر Xray → SOCKS5 محلی → WARP → اینترنت است. خروجی‌گیر پنل را تغییر نمی‌دهد. outbound پیش‌فرض فعلی را در ابتدای فهرست نگه دارید و قواعد سرویس را پیش از قواعد عمومی اضافه کنید. راهنما شبکهٔ host/Docker، ارسال دامنه، DNS راه‌دور و مسدودسازی UDP/443 فقط برای سرویس‌های منتخب را توضیح می‌دهد. UDP ASSOCIATE وجود ندارد.

<div dir="ltr">

```bash
warp-pool xray-routing --youtube-instance 2 --gemini-instance 1
```

</div>

[Xray JSON + sniffing](docs/xray/README.md)

## به‌روزرسانی و حذف

<div dir="ltr">

```bash
warp-pool --instance 1 update /root/Warp-Manager
warp-pool --all update /root/Warp-Manager-v0.4.1.zip --sha256 "$EXPECTED_SHA256"
warp-pool --instance 2 uninstall
```

</div>

به‌روزرسانی تنظیمات، پورت‌ها و volumeهای ثبت را حفظ می‌کند. ZIP روی HTTPS به SHA-256 مورد انتظار نیاز دارد. حذف معمول دادهٔ ثبت محلی را نگه می‌دارد؛ --purge-profiles آن را حذف می‌کند. --purge-deps فقط پاک‌سازی وابستگی‌های نصب‌شدهٔ همین پروژه و بدون مصرف‌کنندهٔ دیگر را درخواست می‌کند.

## آزمون و محدودیت اندازه‌گیری‌شده

در VPS با 1 vCPU/2 GiB، هشت WARP واقعی آزمون 8/32/64 کلاینت را بدون خطا گذراندند. برای هشت کلاینت، 1/2/4/8 تونل حدود 85/132/165/172 Mbit/s دادند. CPU به 100٪ رسید و مولد TLS روی همان CPU بود. گیگابیت یا 128 کلاینت واقعی بدون خطا تأیید نشد. دو مشاهدهٔ Gemini SERVING ثبت شد، ولی استخر پایدار با ذخیرهٔ کامل ایجاد نشد. بازیابی YouTube در هدف کنترل 100 Mbit/s موفق بود.

<div dir="ltr">

```bash
python3 scripts/check_project.py
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
```

</div>

[Test methodology and results](docs/testing/RESULTS.md)

## مستندات و مجوز

مرجع تفصیلی انگلیسی/روسی است. بستهٔ عمومی آدرس VPS واقعی، اطلاعات SSH یا پروفایل ثبت ندارد. پیش‌نمایش کنسول دادهٔ نمایشی مصنوعی است و تأیید خروجی زنده محسوب نمی‌شود.

[Documentation](docs/README.md) · [Configuration](docs/CONFIGURATION.md) · [Commands](docs/COMMANDS.md) · [Operations](docs/OPERATIONS.md) · [FAQ](docs/FAQ.md) · [Publishing](docs/PUBLISHING.md) · [Security](SECURITY.md)

مدیر غیررسمی با مجوز GPL-3.0-only؛ کلاینت جداگانهٔ Cloudflare شرایط خودش را دارد.

[LICENSE](LICENSE) · [Attribution](THIRD_PARTY_NOTICES.md) · [Upstream notices](UPSTREAM_NOTICES.md)

</div>
