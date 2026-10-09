# تقرير نقل طبقات توافق Winlator إلى Madeira

الفرع: `universal-game-compatibility` في fork ‏`nwaf92641/Madeira`. لم يُعدَّل `willfaust/Madeira` ولا
`brunodev85/winlator`، ولم يُدفع أي شيء إلى `main`. بقيت الـ commits الثمانية السابقة كما هي.
الخريطة الكاملة للمكوّنات وتصنيفاتها في `WINLATOR_MADEIRA_GAP_MAP_AR.md`.

## 1. الطبقات التي يملكها Winlator وكانت ناقصة في Madeira

- **مكوّنات wincomponents** (direct3d وdirectshow وdirectplay وxaudio وvcrun2005 وvcrun2010 وwmdecoder):
  يضع Winlator ملفات Microsoft. أما Madeira فلم تكن في مزرعته 64-bit (`app/Madeira/arm64ec-windows`، 143 ملفًا)
  وحدات Wine المقابلة لها: لا quartz ولا devenum ولا qedit ولا xaudio2_* ولا msvcr80/90/100/110 ولا d3dx9/d3dx10/d3dcompiler_4x
  ولا wmvcore ولا dplayx/dpnet ولا wbemprox. ومع ذلك يسجّل قالب البادئة فئات COM لها في system32،
  فكان 458 من 614 فئة COM 64-bit مسجّلة بلا DLL.
- **WMI لـ 64-bit**: كان الرابط `syswow64\wbem` فقط، داخل شرط مزرعة i386.
- **أهداف delay-import** لوحدات موجودة أصلًا (advapi32 → cryptsp، shlwapi → mlang، shell32 → shdocvw،
  urlmon → advpack، bthprops → bluetoothapis): غيابها يظهر كتعطل "unimplemented function" عند أول استدعاء.
- **تشخيص الإقلاع**: لم يكن يميّز الدالة غير المنفّذة، ولا فشل الاعتمادية، ولا اختلاف المعمارية، ولا فشل تهيئة Wine، ولا أخطاء الصوت.
- لم تُنقل لأنها **غير مناسبة** (التفاصيل في الخريطة): DXVK وVKD3D وD8VK وD7VK (Vulkan)، وturnip/zink/virgl/vortek/gladio (Android)،
  وWineD3D كـ backend، وALSA/Pulse، وملفات Microsoft نفسها.

## 2. ما نُقل فعلًا

1. **185 وحدة Wine مبنية لـ ARM64EC** أُضيفت إلى المزرعة 64-bit (حوالي 150MB بعد strip، والمزرعة صارت 243MB)، في 12 مجموعة
   لكل منها سبب مكتوب ومكوّن Winlator المقابل:
   directshow ‏(11)، وmedia_foundation ‏(14، بدون mfsrcsnk/mfasfsrcsnk/mfmp4srcsnk لأنها تعتمد على winedmo)، وwmdecoder ‏(4)،
   وvfw_acm_mci ‏(17)، وxaudio ‏(ملفات xaudio2_0..9 وx3daudio وxapofx وxactengine3)، وdirectplay ‏(9)،
   وdirect3d_helpers ‏(d3dx9_31..42، وd3dx10_33..43، وd3dx11، وd3dcompiler)، وdirect3d10_frontends (d3d10 وd3d10_1 وd2d1 وdxdiagn وddraw)،
   وvcrun_legacy ‏(msvcr80..110 وmsvcp60..120 وatl وvcomp...)، وgame_system_libs ‏(gdiplus وusp10 وriched20 وmsxml3/6 وxmllite وwbemprox...)،
   وdelay_import_closure ‏(5).
   النتيجة: الفئات غير المحلولة في القالب نزلت من 458 إلى 259.
2. **سكربت بناء قابل للتكرار**: `build/wine-pe/build-arm64ec-farm.sh`
   - يرفض الوحدات المملوكة لـ DXMT وd3d12 وntdll وFEX.
   - يفحص قبل التثبيت أن كل import وdelay-import موجود في المزرعة، ولا يثبّت شيئًا إن فشل الفحص (تحققت من ذلك بإزالة mlang مؤقتًا).
   - يزيل أقسام debug، ولا يلمس الوحدات المجرّبة على الجهاز إلا مع `--refresh`.
3. **WMI لـ 64-bit**: `madeira_link_wbem` في `WineProcessBridge.m` يربط `system32\wbem` من المزرعة 64-bit و`syswow64\wbem` من مزرعة i386.
4. **تشخيص الإقلاع**: خمس فئات جديدة هي unimplemented-function وdependency-load-failure وarchitecture-mismatch
   وwine-init-failure وaudio-init-failure. رمز الخطأ c000007b يُصنَّف معمارية، وc0000139 دالة/تصدير ناقص.
   الفئات الموجودة سابقًا (missing-dll، وD3D device، وswapchain/present، وshader، وvideo، وprocess) بقيت، وأُعيد ترتيب الحكم.
5. **اختبارات**:
   - `compat-layers-x64.exe`: اختبار لكل طبقة من عملية 64-bit، ونُسخ إلى المزرعة ليُشغَّل على iPad.
   - `check-arm64ec-farm.py`، وأُضيف إلى CI.
   - سيناريوهات جديدة في `check-launch-diagnostics.py`.

لا توجد DLL مزيفة: كل الوحدات وحدات Wine حقيقية، والاختبار يتحقق أن نقاط الدخول المفحوصة ليست stub في ملفات `.spec`.
ولا توجد ملفات تعريف لألعاب بعينها.

## 3. الملفات التي تغيّرت ولماذا

| الملف | السبب |
|---|---|
| `build/wine-pe/arm64ec-farm.json` (جديد) | قائمة الوحدات وأسبابها ومقابلها في Winlator |
| `build/wine-pe/build-arm64ec-farm.sh` (جديد) | البناء، والفحص، والتثبيت |
| `app/Madeira/arm64ec-windows/*.dll` (185 جديدة + compat-layers-x64.exe) | الوحدات نفسها، دون تعديل أي ملف كان متتبَّعًا |
| `app/Madeira/WineProcessBridge.m` | رابط system32\wbem لجلسات 64-bit |
| `app/Madeira/LaunchDiagnostics.c/.h` | الفئات والمطابقات الجديدة |
| `build/host-tests/check-launch-routing.py` | يتحقق من استدعاءَي wbem |
| `build/host-tests/check-launch-diagnostics.py` | سيناريوهات arch، وentry-point، وchain، وunimplemented، وsxs، وwine-init، وaudio |
| `build/host-tests/check-arm64ec-farm.py` (جديد) | فحص المزرعة |
| `build/x64-tests/compat-layers-x64.c`، `build-compat-layers.sh` (جديد) | اختبار الطبقات |
| `.github/workflows/host-tests.yml` | إضافة check-arm64ec-farm |
| `docs/MEDIA.md`، `docs/BUILDING.md`، `docs/LAUNCH_DIAGNOSTICS.md` | توثيق ما سبق |
| `WINLATOR_MADEIRA_GAP_MAP_AR.md`، `WINLATOR_PORT_REPORT_AR.md` | الخريطة وهذا التقرير |

## 4. نتائج الاختبارات الحقيقية (آلية، على Linux x86_64)

- **بناء ARM64EC**: نجح بناء 185 وحدة من مصدر submodule ‏`wine` بـ llvm-mingw 20260421، و`--check` ناجح.
- **check-arm64ec-farm.py**: PASS.
  - 185 وحدة ARM64EC، بمحاذاة 0x10000، ومجرّدة من debug.
  - كل imports وdelay-imports محلولة. الاستثناء الوحيد المتبقي opengl32 → glu32، وهو مسبق والـ GL على iOS stub.
- **compat-layers-x64.exe** تحت Wine 11.4 x86_64 مبني من upstream (مصدره مطابق للـ submodule في كل هذه الوحدات):
  **63 نجاح، 0 فشل، 4 تخطٍّ** (المعرِضات VR/VMR-9، وDSoundRender، ومقدّم EVR، وجهاز D3D10؛ كلها تحتاج شاشة أو صوتًا).
  كشفت الجولات الأولى أمرين حقيقيين:
  - wmvcore يستورد winegstreamer بـ delay-import، وبدونه يتعطل البرنامج. لذلك أُضيف فحص delay-imports وأهدافه الخمسة الناقصة.
  - dxdiagn يحتاج STA.
- **كل اختبارات المضيف (43)**: 36 نجاح و7 فشل. السبعة كلها كانت تفشل قبل هذا العمل لأسباب بيئية:
  - cfg-early-docs وruntime-settings وonboarding وswap-coverage وdock-components: سلوك Swift على Linux.
  - steam-library: Python بلا وحدة zstd.
  - wma-decoder: يحتاج شجرة Wine مهيأة لـ macOS.
  
  للمقارنة، خط الأساس قبل التغيير كان 28 نجاحًا و13 فشلًا (الستة الإضافية كانت لغياب g++).
- **قائمة CI** (23 اختبارًا بعد الإضافة، بدون swiftc كما في CI): 22 نجاح. الفاشل الوحيد check-cfg-early-docs محليًا، بسبب swiftc
  الموجود في PATH عندي، وهو فشل مسبق.
- تحقق إضافي: هيدر ARM64EC يحمل machine 0x8664 مثل كل وحدات المزرعة السابقة. وملفات .tlb هي PE تحمل typelib بصيغة MSFT أو SLTG.

## 5. ما يحتاج Xcode وiPad (غير مُثبت)

- تحميل الوحدات الـ 185 فعليًا على iPad عبر FEX/ARM64EC.
- تغيير `WineProcessBridge.m`: يحتاج بناء Xcode. وWMI في جلسة 64-bit يُختبر بتشغيل `compat-layers-x64.exe` على الجهاز.
- المعرِضات: تشغيل `compat-layers-x64.exe --display --audio --d3d10` على الجهاز، بما فيه VMR-9/EVR فوق d3d9، وإنشاء جهاز D3D10 فوق DXMT d3d10core.
- فك ترميز الوسائط لـ 64-bit عبر `MADEIRA_WG_64BIT=1`.
- أي لعبة حقيقية: لم تُختبر أي لعبة، ولا يُدّعى نجاح أي لعبة.

## 6. الطبقات المتبقية مرتبة حسب الأثر

> تحديث: البنود 1 و2 أُنجزت، والبند 3 أُنجز جزئيًا. التفاصيل والترتيب الجديد في `CONTINUATION_AR.md`.

1. **جعل compat/wine-modules.json واعيًا بالمعمارية**: يعامل الآن 648 وحدة كأنها مشحونة، و341 منها غير موجودة في مزرعة 64-bit (مثل d3d8 وdmusic وmsvcr70/71).
   فيقول محرك التوافق إن لعبة 64-bit لا تنقصها DLL وهي ناقصة. الإصلاح يشمل المولّد وGameCompat.swift (launch.bits).
2. **ترجمة D3D8 إلى D3D9 فوق DXMT** (بديل D8VK بدون Vulkan) لألعاب D3D8 32-bit.
3. **DirectDraw فوق GDI** على طريقة cnc-ddraw، لألعاب ddraw القديمة.
4. **جانب unix لـ winedmo على iOS** فوق FFmpeg المبني أصلًا، ثم شحن mfsrcsnk/mfasfsrcsnk/mfmp4srcsnk لتشغيل الفيديو عبر MF.
5. **winsxs لـ 64-bit** (amd64_ VC80/VC90، وcomctl32 v6).
6. **wine-mono** لألعاب .NET/XNA، ثم wine-gecko وmshtml للمشغّلات.
7. **برامج Wine 64-bit** (rundll32، وregsvr32، وmsiexec) للمثبّتات.
8. **MIDI synth** (SoundFont) لـ midimap.

## 7. الـ commits وحالة PR

على `universal-game-compatibility` فوق `eed24d6`:

- `e14fb15` 64-bit farm: ship the Wine modules Winlator's components stand for (185 ARM64EC builtins)
- `163ceaa` Prefix: link the wbem directory into system32 for 64-bit sessions too
- `6b7515b` Launch diagnostics: unimplemented functions, dependency loads, architecture, Wine init, audio
- `2cac729` Tests: per-layer x64 smoke test and a farm check; docs for the 64-bit modules
- ثم commit هذا التقرير والخريطة.

**الدفع وPR**: فشل `git push origin universal-game-compatibility` برسالة
`fatal: could not read Username for 'https://github.com'`، و`gh` غير مسجّل، وتكامل GitHub يعيد `scm_not_connected`.
لم يُخترع أي token ولم يُتجاوز أي تحقق. الـ patches والـ bundle ونص PR جاهزة في `/workspace/madeira-compat-out/`.
بعد ربط GitHub: `git push origin universal-game-compatibility`، ثم PR إلى `nwaf92641/Madeira:main`.

## 8. الدفعة الثالثة (بعد نسخة الحماية `backup/universal-game-compatibility-1791577204`)

| الـ commit | ما أضافه |
|---|---|
| ‏`cad890a` | ‏`build/host-tests/run-all.sh` لتشغيل كل اختبارات المضيف، ويعرض SKIP عند غياب swiftc |
| ‏`30c2f62` | مجموعة `installer_scripting` في المزرعة 64-bit، وتضم 15 وحدة: msi، وmsiexec، وmsxml4، وscrrun، وwshom، وjscript، وvbscript، وwbemdisp، وhnetcfg، وsensapi، وshfolder، وmspatcha، وodbccp32. فئات COM بلا DLL نزلت من 259 إلى 209 |
| ‏`5ede471` | ‏`test_installer_scripting` في `compat-layers-x64`: قبل 63/17/4، وبعد 81/0/4. وأُضيف `farm-overrides.py` |
| ‏`e0d0907` | ‏API sets في `compat.json` ‏(`wine_api_sets`) وفي `GameCompat.apiSetProblem`، مع `gen-game-compat.py --update-local` |
| ‏`c511adc` | تصحيح بيانات التوافق، وأُضيفت فحوص تفشل إن ادّعى JSON وجود DLL غير مشحون |
| ‏`c5e6a8e` | تلميحات التشخيص لـ api-ms-win-*، وللمثبّتات والسكربتات |
| ‏`84f934f` | ‏Wine Mono 11.0.0 مكوّن اختياري (`WineMono.c`) مع `check-wine-mono.py` |
| ‏`498b03f` | ‏`build/tools/wine-audit.py` لتدقيق اكتمال Wine |
| ‏`5509b28` | ‏CI يشغّل check-wine-mono وcheck-winsxs وcheck-d3d8-shader |

المقارنة المحدثة مع Winlator في `WINLATOR_COMPARISON.md`.
