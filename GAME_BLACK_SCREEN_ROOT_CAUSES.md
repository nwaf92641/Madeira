# أسباب الشاشة السوداء في الألعاب: الحالة في هذا الفرع

نقطة البداية هي التحقيق السابق في `/workspace/madeira-investigation/REPORT_AR.md`، وفيه 12 سببًا مرتبة. هنا الحالة الحالية لكل سبب في
`universal-game-compatibility`، والـ commit المسؤول، والاختبار الذي يثبت ذلك.

**لا يُدّعى هنا إصلاح شاشة سوداء في لعبة بعينها**، لأنه لم يُشغَّل أي شيء على iPad.

"مُصلح" يعني أن مسار الكود الذي يسبب السواد تغيّر، وأن هناك اختبار مضيف يغطيه.

## أولًا: كيف يعرف المستخدم أي سبب حدث

يكتب `app/Madeira/LaunchDiagnostics.c` (منذ commit ‏`399f518`) عند كل تشغيل الملف
`Documents/madeira-diagnostics/last-launch.txt`، وفيه:

- **مراحل الإقلاع حتى أول إطار:** ‏launch، ثم wine-started، ثم child-process، ثم device، ثم metal-layer، ثم swapchain، ثم first-present، ثم gdi-window.
- **الفصل بين حالتين:** "العملية تعمل" ليست نجاحًا، والنجاح هو "Display: FIRST FRAME PRESENTED".
- **حكم من فئات محددة:** ‏missing-dll، وarchitecture-mismatch، وunimplemented-function، وdependency-load-failure، و`wine-init-failure`، وgraphics-device-failure، وswapchain-failure، و`metal-present-failure`، وwindow-visibility، و`shader-translation-failure`، و`video-init-failure`، و`audio-init-failure`.

في هذه الدفعة أُضيفت تلميحات لـ:

- ‏api-ms-win-*/ext-ms-win-* (العقد بلا مضيف).
- وحدات المثبّتات والسكربتات.
- "Wine Mono is not installed"، مع تلميح يشرح أين يوضع المكوّن.

الوثيقة: `docs/LAUNCH_DIAGNOSTICS.md`. الاختبار: `check-launch-diagnostics.py`، بسيناريوهات منها apiset وdotnet، وقد تحققت من أن الاختبار يفشل عند تعطيل كل مطابقة.

## ثانيًا: الأسباب وحالتها

| # | السبب | الحالة | أين | الاختبار |
|---|---|---|---|---|
| 1 | المشغّل (launcher) يُغلق فتنتهي الجلسة واللعبة ما زالت تحمّل | **مُصلح**: الجلسة تنتظر العمليات الأبناء افتراضيًا (`MADEIRA_WAIT_CHILDREN`) | ‏`1ae0484` و`fc94efc` | ‏`check-child-slots.py` |
| 2 | نافذة GDI أو DirectDraw تغطي الشاشة ولا تُرسم في وضع اللعبة | **مُصلح جزئيًا**: نوافذ المشغّل والرسائل تُرسم. ‏DirectDraw يمر عبر cnc-ddraw فوق DXMT d3d9 بشكل اختياري (`MADEIRA_DDRAW=cnc`). التشخيص `window-visibility` | ‏`ad2529e`، و`a2fa5d6`، و`6dc7bcf` | ‏`check-cnc-ddraw.py` (تحت Wine) |
| 3 | الـ compositor لا يستهلك الإطارات، فيتوقف `nextDrawable` | خُفّف من قبل في main. لم يتغير هنا. التشخيص `metal-present-failure` | ‏— | ‏— |
| 4 | طبقة Metal واحدة مشتركة، وD3D11 بلا حماية من تغيير إعداداتها | **مُصلح**: رقعة `dxmt-ios-layer-safety` | ‏`229b826` | ‏`check-layer-format.py` |
| 5 | ‏swapchain يُنشأ قبل تسجيل الطبقة فيحدث abort | **مُصلح**: انتظار حتى `MADEIRA_LAYER_WAIT_MS`، ثم فشل CreateSwapChain بدل abort | ‏`229b826` | ‏`check-layer-format.py` |
| 6 | صيغة swapchain في D3D12 لا يقبلها `CAMetalLayer` | **مُصلح**: الصيغة تُنسخ أو تُحوَّل (`mad_swap_apply_layer`) | ‏`229b826` و`ab325f9` | ‏`check-layer-format.py` |
| 7 | فشل تحويل الشيدرات | **مُشخَّص**: فئة `shader-translation-failure`. شيدرات D3D8 تُترجم بمترجم مُختبر | ‏`d938f44` | ‏`check-d3d8-shader.py` |
| 8 | ميزات D3D12 تُتخطى أو تُرفض | مفتوح، ويحتاج iPad | ‏— | ‏— |
| 9 | أنسجة BC غير مدعومة على GPU فتبقى صفرًا | مفتوح، ويحتاج iPad | ‏— | ‏— |
| 10 | ‏`dispatch_sync` إلى الـ main thread عند ضبط الطبقة | فرضية بلا دليل، ولم يتغير شيء | ‏— | ‏— |
| 11 | مقاطع الفيديو في ألعاب 64-bit | **جزئي**: وحدات DirectShow وMF وWMV شُحنت لـ 64-bit. فك الترميز يحتاج `MADEIRA_WG_64BIT=1` و`winedmo` | ‏`e14fb15` | ‏`compat-layers-x64` |
| 12 | الذاكرة وJIT | لم يتغير | ‏— | ‏— |

## ثالثًا: أسباب جديدة لم تكن في القائمة الأصلية

| السبب | العَرَض | الحالة |
|---|---|---|
| ‏DLL أو فئة COM تطلبها عملية 64-bit وليست في مزرعة ARM64EC | تفشل اللعبة بصمت أو يُغلق المشغّل | **مُصلح للوحدات التي لها دليل**: أكثر من 200 وحدة، منها 15 في هذه الدفعة. ‏`GameCompat` يحذّر قبل التشغيل، و`missing-dll` يظهر بعده |
| ‏API set بلا مضيف، مثل `api-ms-win-*` المحلول إلى windows.storage أو wintypes | ‏STATUS_DLL_NOT_FOUND دون أن يُذكر اسم DLL حقيقي | **مُشخَّص** (`e0d0907` و`c5e6a8e`). لم يُشحن مضيف وهمي |
| ‏.NET بلا Mono | ‏"Wine Mono is not installed"، ثم لا شيء يحدث | **مكوّن اختياري** (`84f934f`)، وتلميح في التشخيص |
| ‏Common Controls 6 ناقص لـ 64-bit | ‏TaskDialog يُربط بـ stub فيُجهض البرنامج | **مُصلح** (`93503a7` و`d39e9c3`)، و`check-winsxs.py` |
| ‏D3D8 بلا backend | الجهاز لا يُنشأ | **مُصلح لـ 32-bit**: d3d8to9 (`c5143b4`) |

## رابعًا: ما يحتاج iPad للتأكد

1. تشغيل `compat-layers-x64.exe` (في المزرعة) على الجهاز. يجب أن تكون النتيجة 81 PASS كما على سطح المكتب.
2. لعبة D3D11 حقيقية، وقراءة `last-launch.txt` بعدها.
3. ‏cnc-ddraw وd3d8to9 مع لعبة 32-bit حقيقية.
4. ‏Wine Mono تحت FEX.
