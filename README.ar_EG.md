![Warp-Manager](docs/assets/banner.svg)

# Warp-Manager

[English](README.md) · [فارسی](README.fa_IR.md) · [العربية](README.ar_EG.md) · [中文](README.zh_CN.md) · [Español](README.es_ES.md) · [Русский](README.ru_RU.md) · [Türkçe](README.tr_TR.md)

<div dir="rtl">

مدير أنفاق Cloudflare WARP لخوادم Linux VPS: سياسات مستقلة لـ Gemini وYouTube، وواجهة SOCKS5/HTTP محلية، ومراقبة لكل نفق، وتوسعة حسب السعة مع احتياطي جاهز.

**0.4.1**

## المتطلبات والتثبيت

تحتاج إلى root وUbuntu/Debian x86_64 وsystemd و/dev/net/tun والإنترنت وPython 3.10+. جرى الاختبار على Ubuntu 24.04/Python 3.12. تُثبّت الأدوات وDocker/Compose عند غيابها، مع الحفاظ على Docker الموجود. يُنزّل عميل WARP الرسمي أثناء بناء الصورة ويبدأ باستخدام --accept-tos.

من الأرشيف الكامل أو نسخة المستودع:

<div dir="ltr">

```bash
cd Warp-Manager
sudo bash install.sh
```

</div>

بعد رفع Forcus356/Warp-Manager إلى الفرع main، يصبح المثبّت المستقل متاحًا أيضًا:

<div dir="ltr">

```bash
curl -fsSL https://raw.githubusercontent.com/Forcus356/Warp-Manager/main/install_warp_manager.sh -o /tmp/install_warp_manager.sh
sudo bash /tmp/install_warp_manager.sh
```

</div>

يحوّل المثبّت المرجع إلى commit ثابت، وينزّل المصدر الكامل عبر HTTPS ويعرض commit وSHA-256 قبل التثبيت. استخدم install.sh للمصدر المحلي. لا يوجد اكتشاف تلقائي لأحدث GitHub Release.

## الواجهة والأوضاع

يبقى الأمر المثبّت warp-pool، وتُحفظ أسماء خدمات ومسارات warp-google-pool للتوافق. تسميات الواجهة بالروسية حاليًا، وخيارات CLI واحدة في جميع اللغات. يفتح install.sh بلا معاملات القائمة داخل الطرفية. يعرض t القوالب، وتحرّر Easy/Advanced مسودة قابلة للمعاينة والإلغاء.

يتطلب Gemini نتيجة SERVING من اختبار توليد مجهول. REFUSED وCAPTCHA وINCONCLUSIVE ليست نجاحًا. قد تختلف النتيجة للحساب المسجّل دخوله.

يتطلب YouTube أن تكون منطقة Google المرصودة RU، ويُعطّل Gemini في تلك النسخة المستقلة. RU VERIFIED لا يضمن غياب الإعلانات أو جودة الفيديو.

<div dir="ltr">

```bash
sudo bash install.sh --instance 1 --role gemini --target-gbps 1 --tunnels 12 --impersonate
sudo bash install.sh --instance 2 --role youtube --target-gbps 1 --tunnels 12
```

</div>

## السعة والاحتياطي

وحدة target-gbps هي Gbit/s: ‏0.1 تعني 100 Mbit/s و0 يختار مجموعة ثابتة. يُقاس كل نفق منفردًا ثم تُقاس المجموعة الرئيسية بالتزامن. جمع القياسات المنفردة لا يؤكد السعة المشتركة. الحد 99 نسخة و1–128 موضعًا لكل نسخة هو حد إعدادات وليس ضمان قدرة VPS.

الاحتياطي هو max(1, floor(N/3)): ‏10 أنفاق رئيسية + 3 احتياطية، و30 + 10. تُفحص الأنفاق الاحتياطية مستقلة ولا تستقبل اتصالات جديدة إلا بعد ترقيتها، ثم يُستكمل النقص تلقائيًا. يوازن المدير الاتصالات النشطة وقيد الإنشاء بوزن السرعة المقاسة. يظل اتصال TCP الواحد على نفق واحد. المواضع الخاملة لا تبدأ حاويات.

لا يوفر WARP اختيار دولة المزوّد أو PIN لخادم خروج محدد. قوائم الدول تفلتر منطقة Google المرصودة. المراقبة والعزل والتأخير التصاعدي وحدود التدوير مستقلة لكل نفق.

## الأوامر

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

تضع check/search/speed/rotate الطلب في قائمة الانتظار؛ رمز الخروج 0 يعني القبول فقط. راجع status/logs للنتيجة. يعمل --all مع status/logs/config/xray/update فقط. يستخدم --interval والمُدخل في القائمة الدقائق؛ حقول وقت JSON بالثواني.

## دمج Xray / 3x-ui

المسار هو Xray → SOCKS5 محلي → WARP → الإنترنت. لا يعدّل التصدير اللوحة. حافظ على outbound الافتراضي الموجود في البداية، وأضف قواعد الخدمة قبل القواعد العامة. توضح الوثائق شبكة host/Docker وإرسال أسماء النطاقات وDNS البعيد وحظر UDP/443 المحدود بالخدمات المحددة. لا يوجد UDP ASSOCIATE.

<div dir="ltr">

```bash
warp-pool xray-routing --youtube-instance 2 --gemini-instance 1
```

</div>

[Xray JSON + sniffing](docs/xray/README.md)

## التحديث والإزالة

<div dir="ltr">

```bash
warp-pool --instance 1 update /root/Warp-Manager
warp-pool --all update /root/Warp-Manager-v0.4.1.zip --sha256 "$EXPECTED_SHA256"
warp-pool --instance 2 uninstall
```

</div>

تحافظ التحديثات على الإعدادات والمنافذ وأحجام التسجيل. يتطلب ZIP عبر HTTPS قيمة SHA-256 متوقعة. تحفظ الإزالة المعتادة ملفات التسجيل المحلية؛ يحذفها --purge-profiles. يطلب --purge-deps تنظيف الاعتماديات المملوكة لهذا المشروع والتي لم يعد لها مستهلك آخر فقط.

## الاختبارات والحدود المقاسة

على VPS بموارد 1 vCPU/2 GiB اجتازت ثمانية أنفاق WARP حقيقية اختبارات 8/32/64 عميلًا بلا أخطاء. مع ثمانية عملاء أعطت 1/2/4/8 أنفاق نحو 85/132/165/172 Mbit/s. بلغ CPU نسبة 100٪ وكان مولّد TLS على المعالج نفسه. لم تُثبت سرعة جيجابت أو 128 عميلًا حقيقيًا بلا أخطاء. سُجلت ملاحظتان Gemini SERVING، لكن لم تُنشأ مجموعة ثابتة باحتياطي كامل. نجح اختبار استعادة YouTube بهدف تحكم 100 Mbit/s.

<div dir="ltr">

```bash
python3 scripts/check_project.py
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
```

</div>

[Test methodology and results](docs/testing/RESULTS.md)

## الوثائق والترخيص

المراجع التفصيلية بالإنجليزية والروسية. لا تتضمن الحزمة العامة عناوين VPS الحقيقية أو بيانات SSH أو ملفات التسجيل. معاينات الواجهة تستخدم حالة تجريبية اصطناعية وليست إثباتًا لخروج فعلي.

[Documentation](docs/README.md) · [Configuration](docs/CONFIGURATION.md) · [Commands](docs/COMMANDS.md) · [Operations](docs/OPERATIONS.md) · [FAQ](docs/FAQ.md) · [Publishing](docs/PUBLISHING.md) · [Security](SECURITY.md)

مدير غير رسمي بترخيص GPL-3.0-only؛ يحتفظ عميل Cloudflare المنزّل منفصلًا بشروطه الخاصة.

[LICENSE](LICENSE) · [Attribution](THIRD_PARTY_NOTICES.md) · [Upstream notices](UPSTREAM_NOTICES.md)

</div>
