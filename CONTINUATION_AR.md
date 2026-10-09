# متابعة نقل طبقات Winlator إلى Madeira

هذا التقرير يكمل `WINLATOR_PORT_REPORT_AR.md`. الفرع هو `universal-game-compatibility`، وكل شيء فيه محلي ولم يُدفع إلى GitHub.
لم يُعدَّل upstream (willfaust/Madeira وbrunodev85/winlator)، ولم يُحذف أي عمل سابق.
لم تُختبر أي لعبة على iPad، ولا يدّعي هذا التقرير نجاح أي لعبة.

## 1. ما أُنجز

### 1.1 إصلاح عدم تطابق مدقّق التوافق (الأولوية 1)

- **المشكلة:** كان `compat/wine-modules.json` يفترض أن كل وحدات Wine موجودة في كل معمارية، ولهذا كان يسمّي إصدارًا وهميًا هو "11.18"، و12 وحدة فيه غير موجودة أصلًا.
- **بعد الإصلاح:** صار الملف يُولَّد من Wine 11.4 الفعلي (الـ submodule). فيه 636 وحدة: 307 منها موجودة في مزرعة ARM64EC (64-bit)، و329 ليست فيها.
- **المولّد** (`build/tools/gen-wine-modules.py`): يقرأ المزرعة الفعلية `app/Madeira/arm64ec-windows` ويكتب `modules_64` و`not_in_64bit_farm`.
- **محرك التوافق** (`GameCompat.swift`):
  - يفحص برامج 64-bit مقابل المزرعة الفعلية فقط، وبرامج 32-bit مقابل القائمة الكاملة.
  - النقص الخاص بـ 64-bit يُبلَّغ عنه حتى لو ذكرته قاعدة عامة (مثل legacy-directdraw). لا يُعفى منه إلا dependency لها payload.
- **اختبار تراجعي:** تأكدتُ أن المحرك القديم يفشل في 5 فحوص، وأن الجديد ينجح فيها كلها.

### 1.2 Direct3D 8 فوق DXMT بدون Vulkan (الأولوية 2)

- **السبب الجذري:** `d3d8` في Wine يستدعي `wined3d_create` بلا أي بديل، و`wined3d` ليس له backend على iOS. لهذا كان `Direct3DCreate8` يعيد NULL، وكانت كل ألعاب D3D8 تتوقف عند البدء.
- **لماذا ليس D8VK:** Winlator يستعمل D8VK، وD8VK يتكلم مع d3d9 الخاص بـ DXVK عبر واجهات خاصة، أي أنه يحتاج Vulkan.
- **الحل:** استيراد `crosire/d3d8to9` (رخصة BSD-2-Clause). يستعمل واجهات d3d9 العامة فقط، فيعمل فوق `d3d9.dll` الخاص بـ DXMT (Metal) كما هو.
  - استُورد حرفيًا في commit مستقل (`third_party/d3d8to9`)، وكل تعديل لاحق موثّق في `MADEIRA_CHANGES.md`.
- **التعديلات الحقيقية:**
  - ترجمة shaders على مستوى الـ tokens (`madeira_d3d8_shader.hpp`) بدل مسار D3DX/regex، لأن ذلك المسار مكتوب لصيغة Microsoft ولا يناسب D3DX في Wine ولا DXMT.
    - vs.1.x: تُضاف `dcl` لكل مُدخل في الـ declaration.
    - ps.1.x: تمرّ كما هي.
  - ثوابت `D3DVSD_CONST` تُحمَّل عند `SetVertexShader`.
  - `GetVertexShaderDeclaration` صار يعمل، ودوال الـ getters تعيد tokens الأصلية من D3D8.
  - حُذف الـ MessageBox (على iPad يبدو كأن اللعبة متجمدة)، وصارت هناك أسطر `[d3d8to9]` في سجل الإطلاق.
- **البناء والشحن:**
  - `build/d3d8/build.sh` يبني الـ DLL (i686 بـ llvm-mingw)، ويعلّمه builtin بـ `winebuild --builtin`، ثم ينسخه إلى `app/Madeira/i386-windows`.
  - `build/wine-i386/build.sh` يتخطى `d3d8` الخاص بـ Wine ويستدعي هذا السكربت.
- **التشخيص والبيانات:**
  - `LaunchDiagnostics.c` يصنّف أسطر `[d3d8to9]` إلى: واجهة رسومية، أو جهاز، أو shader، أو d3d9/d3dx9 ناقص.
  - `d3d8` في `dependencies.json` صار "builtin" لـ 32-bit فقط.
- **عن 64-bit:** لا يوجد `d3d8` بنسخة 64-bit، وWindows نفسه لم يكن فيه واحد.
- **الرخصة:** النص في `LICENSES/BSD-2-d3d8to9.txt` ونسخة داخل الحزمة في `app/Madeira/licenses/`، وأُضيف سطر في `THIRD-PARTY-NOTICES.md`.

### 1.3 DirectDraw (الأولوية 3): جزئي، مع خطة

- **الأدلة:**
  - `ddraw` في Wine يتراجع إلى `WINED3D_NO3D` ويرسم بـ GDI.
  - لكن Winios في وضع اللعبة كان يخفي كل نافذة تغطي سطح المكتب كله. نافذة لعبة DirectDraw ملء الشاشة هي بالضبط هذه النافذة، فلم تكن صورتها تظهر.
  - NO3D يرفض أيضًا أسطح `DDSCAPS_VIDEOMEMORY` ويرفض Direct3D عبر ddraw.
- **ما نُفّذ:** مفتاح اختياري `MADEIRA_GAME_GDI_FULLSCREEN=1` (معطّل افتراضيًا). يرسم نافذة اللعبة التي تغطي سطح المكتب ما دامت لا تعرض عبر Metal.
  - أُضيف إلى config catalog، وتلميح التشخيص صار يذكره.
- **ما لم يُنفّذ:** cnc-ddraw (رخصة MIT) مع renderer=direct3d9 فوق DXMT.
  - تأكدتُ أنه يُبنى بلا تعديل بـ llvm-mingw (390 KB في أقل من ثانيتين).
  - الخطة كاملة في `docs/DIRECTDRAW.md`: الشحن اختياري لكل لعبة مع `ddraw.ini`، لأن `auto` يتجنب d3d9 تحت Wine.
  - لم يُشحن لأنه يحتاج آلية recipe تنسخ ملفات إلى جانب اللعبة، ويحتاج اختبارًا على iPad.

## 2. الملفات التي تغيّرت

| الملف | التغيير |
|---|---|
| `build/tools/gen-wine-modules.py`, `compat/wine-modules.json` | مزرعة 64-bit فعلية، وWine 11.4، و636 وحدة (307 منها في مزرعة 64-bit) |
| `build/tools/gen-game-compat.py`, `app/Madeira/compat.json` | `wine_modules_64` و`wine_not_in_64bit_farm`، وd3d8 صار builtin |
| `app/Madeira/GameCompat.swift` | `providedModules(bits:)` و`absentModules(bits:)` و`farmGaps` |
| `third_party/d3d8to9/**` | الاستيراد الحرفي، ثم `madeira_d3d8_shader.hpp` و`madeira_log.hpp` وتعديلات device/base/d3d8to9 و`d3d8.def` |
| `build/d3d8/build.sh` (جديد)، `build/wine-i386/build.sh` | بناء d3d8.dll وشحنه في مزرعة i386 |
| `app/Madeira/LaunchDiagnostics.c` | أسطر `[d3d8to9]`، وتلميح d3d8، وتلميح النافذة ملء الشاشة |
| `compat/dependencies.json` | d3d8 صار builtin لـ 32-bit |
| `app/Madeira/Winios/Winios.m` | `MADEIRA_GAME_GDI_FULLSCREEN` |
| `app/Madeira/ConfigCatalog.generated.swift` | مُولَّد من جديد |
| `docs/D3D8.md`, `docs/DIRECTDRAW.md` (جديدان)، `docs/GAME_COMPATIBILITY.md`, `docs/BUILDING.md` | التوثيق |
| `THIRD-PARTY-NOTICES.md`, `LICENSES/BSD-2-d3d8to9.txt`, `app/Madeira/licenses/*` | الرخص |
| `build/host-tests/check-game-compat.py`, `check-launch-diagnostics.py` | فحوص جديدة |
| `build/host-tests/check-d3d8-shader.py`, `check-d3d8to9-wine.py` (جديدان) | اختبارات D3D8 |
| `.gitignore` | `__pycache__/`، وأُزيل ملف `.pyc` دخل بالخطأ في `f56a364` |

## 3. نتائج الاختبارات (Linux x86_64، ‏9 أكتوبر 2026)

- **`run-all.sh`:** نجح 37 من 44 اختبارًا.
  - الاختبارات السبعة الفاشلة هي `cfg-early-docs` و`dock-components` و`onboarding` و`runtime-settings` و`steam-library` و`swap-coverage` و`wma-decoder`.
  - شغّلتُها على `main` نفسه في worktree منفصل، ففشلت كلها بالطريقة نفسها. السبب هو بيئة Linux: Swift Foundation على Linux، و`wine/build-macos` غير موجود.
  - لا يوجد أي فشل جديد بسبب هذا العمل.
- **`check-game-compat`:** ناجح، ويشمل الانحدار على كل `wineNotIn64BitFarm` وd3d8 ‏x86 مقابل x64.
- **`check-d3d8-shader`:** ‏31 فحصًا ناجحًا تحت ASan/UBSan. مخرجات المترجم تمر عبر محلّل DXSO الخاص بـ DXMT نفسه.
- **`check-d3d8to9-wine`:** ‏26 فحصًا ناجحًا تحت Wine WoW64 حقيقي (i386 + x86_64، مبني محليًا) فوق d3d9 تسجيلي.
  - الـ d3d9 التسجيلي مولَّد آليًا من `d3d9.h`.
  - المسار الذي فُحص: تحميل الـ DLL من مزرعة builtin، ثم `Direct3DCreate8` ثم `CreateDevice` ثم `Clear`/`BeginScene`/`EndScene`/`Present`.
  - vertex shader: وصلت `dcl_position v0` و`dcl_color v5`، وحُمّل الثابت c10.
  - pixel shader، والـ getters، ورفض `vs_2_0`، وحالة "لا يوجد d3d9" مع سطر في السجل.
- **اكتشاف أثناء الاختبار:** DLL مُعلَّم builtin ومنسوخ بجانب الـ exe مع `d3d8=n` يرفضه Wine (`c0000135`). المكان الصحيح هو المزرعة، وهذا ما يفعله البناء.
- **`check-launch-diagnostics` و`check-config-catalog`:** ناجحان.

## 4. ما يحتاج Xcode أو iPad (غير مُثبت)

- الرسم الفعلي لـ D3D8 عبر DXMT/Metal. الاختبار استعمل d3d9 تسجيليًا، ولم يُختبر محوّل Metal الخاص بـ DXMT. ما زالت هذه الأمور غير مختبرة: fixed-function، والأنسجة ذات الـ palette، و`CopyRects` بين صيغ مختلفة.
- بناء مزرعة i386 كاملة على macOS مع d3d8.dll، ثم تغليفها في Xcode.
- `Winios.m` (`MADEIRA_GAME_GDI_FULLSCREEN`): لم يُترجم أصلًا لأنه Objective-C/UIKit، ويحتاج تجربة لعبة DirectDraw على iPad.
- تعديل `GameCompat.swift` نجح مع swiftc على Linux فقط، ولم يُبنَ في Xcode.

## 5. العمل المتبقي مرتبًا حسب الأثر (الدفعة الأولى؛ الترتيب المحدَّث في القسم 7.6)

1. **DirectDraw، المرحلة 2: cnc-ddraw فوق d3d9 الخاص بـ DXMT.** أثر عالٍ لألعاب 2D القديمة، وكلفة متوسطة. يحتاج recipe تنسخ ملفات إلى جانب اللعبة، واختبار Wine مع display driver، وiPad. الخطة في `docs/DIRECTDRAW.md`.
2. **side-by-side (WinSxS).** قالب البادئة `prefix-template.tar.gz` لا يحتوي على `windows/winsxs` أصلًا (138 مدخلًا فقط).
   - إذا لم يُنشئ wineboot الـ manifests على الجهاز، فستفشل برامج VC80/VC90 CRT وcomctl32 v6 بخطأ c0150002.
   - هذه فرضية: يجب فحص البادئة على الجهاز بعد أول تشغيل. التحقق رخيص والأثر عالٍ.
3. **wine-mono.** لألعاب .NET/XNA (كثير من ألعاب indie). `mscoree` موجودة في مزرعة 64-bit، لكن runtime ‏Mono نفسه غير مشحون (نحو 80MB+). أثر متوسط إلى عالٍ، وكلفة كبيرة (الحجم والتشغيل تحت FEX).
4. **winedmo.** ليس في مزرعة 64-bit، والجانب الذي يعمل على iOS غير موجود. لكن Madeira يفك الوسائط حاليًا عبر winegstreamer فوق FFmpeg، فالقيمة الإضافية منخفضة إلى متوسطة. يفيد فقط مسارات Media Foundation التي تحتاج winedmo تحديدًا.
5. **wine-gecko و`mshtml`.** `mshtml` غير موجودة في مزرعة 64-bit. تفيد المشغّلات وصفحات HTML فقط، والأثر على الألعاب منخفض والحجم كبير.
6. **Direct3D 7 وما قبله عبر ddraw.** لا يوجد له حل بدون Vulkan حتى في Winlator. يحتاج مشروعًا مستقلًا: frontend لـ D3D7 فوق d3d9.

## 6. الـ commits (لم يُدفع أي شيء)

- `f56a364` Compat checker: check 64-bit programs against the ARM64EC farm
- `b481532` third_party: import d3d8to9 (BSD-2-Clause) verbatim at 255338f
- `d938f44` d3d8to9: token-level shader translation for DXMT, D3D8 getters, launch-log lines
- `c5143b4` Ship d3d8.dll in the i386 farm: d3d8to9 over DXMT's Direct3D 9
- `f6b1549` Launch diagnostics and compat data for Direct3D 8
- `d5339b4` Host tests: d3d8 shader translator; d3d8.dll under WoW64 Wine
- `a2fa5d6` DirectDraw: opt-in drawing of full-desktop GDI windows; cnc-ddraw plan
- ثم commit هذا التقرير.

بعد ربط GitHub: `git push origin universal-game-compatibility`، ثم PR إلى `nwaf92641/Madeira:main`.

## 7. الدفعة الثانية: cnc-ddraw وWinSxS (9 أكتوبر 2026)

ما زال كل شيء محليًا على الفرع `universal-game-compatibility`، ولم يُدفع شيء. لم يُعدَّل upstream. لم يُستعمل Vulkan، ولم تُضَف DLLs وهمية. لم تُختبر أي لعبة على iPad.

### 7.1 DirectDraw، المرحلة 2: cnc-ddraw فوق d3d9 الخاص بـ DXMT (نُفّذ، اختياري لكل لعبة)

- **الاستيراد:** `third_party/cnc-ddraw` من FunkyFr3sh/cnc-ddraw (رخصة MIT) عند الـ commit ‏`279a057`، في commit مستقل.
  - لم يُستورد `inc/ddraw.h` و`inc/d3dcaps.h` لأنهما نسخ من ترويسات Microsoft ("All Rights Reserved")، ولا `config/` ولا `src/detours/` ولا `.github/`. التفاصيل في `MADEIRA_IMPORT.md`.
- **البناء بدون ترويسات Microsoft:** المجلد `madeira/` يضيف ما ينقص ترويسات llvm-mingw فقط: بنية `DDCAPS_DX1`، و`d3dtypes.h` قبل `d3dcaps.h`.
  - الاختبار يترجم كل ملف مصدر مرتين، مرة بترويسات Microsoft الأصلية ومرة بترويساتنا، ويقارن الـ disassembly. النتيجة مطابقة تامة في كل الملفات.
- **تعديلات المصدر:** أسطر `[cnc-ddraw]` في السجل فقط عبر `__wine_dbg_output`: الـ renderer المختار ومن أي ini، وجهاز Direct3D 9 أو الخطوة التي فشلت مع الـ HRESULT، والتراجع إلى GDI. السلوك لم يتغير. القائمة في `MADEIRA_CHANGES.md`.
- **`build/ddraw/build.sh`:** يبني `ddraw.dll` ‏(i386، نحو 450 KB، native وغير مُعلَّم builtin) بسطر الربط نفسه الذي يستعمله upstream، ولا يكتب شيئًا داخل `third_party`.
  - يثبّته مع `ddraw.ini` في `app/Madeira/cnc-ddraw/`، وهو مجلد مُضمَّن في الحزمة عبر Xcode ومُستثنى من git مثل `i386-windows`.
  - `build/wine-i386/build.sh` يستدعيه بعد d3d9 الخاص بـ DXMT وd3d8to9.
- **`ddraw.ini` (`build/ddraw/make-ini.py`):** مولَّد من نص الإعدادات الافتراضية في cnc-ddraw نفسه، فبقيت كل أقسام الألعاب (289 قسمًا). كل تغيير معلَّم بـ `; Madeira: was ...`:
  - `renderer=direct3d9` بدل `auto`، لأن `auto` لا يختار Direct3D 9 أبدًا تحت Wine. في الاختبار اختار OpenGL، ولا يوجد OpenGL على iOS.
  - `fullscreen=true` و`maintas=true` و`singlecpu=false` و`no_compat_warning=true`.
  - الأقسام الثمانية التي تطلب `renderer=opengl` صارت `direct3d9`. بقيت أقسام `renderer=gdi` (47).
- **التفعيل:** المتغير `MADEIRA_DDRAW=cnc`، عبر recipe ‏`cnc-ddraw` أو dependency ‏`cnc-ddraw` (صارت builtin) أو `env.MADEIRA_DDRAW` في الإعدادات ("DirectDraw (32-bit games)"). يفعل `WineProcessBridge.m` ‏(`madeira_apply_cnc_ddraw`) ما يلي بعد ربط مزرعة i386 مباشرة:
  1. يربط `syswow64\ddraw.dll` بـ cnc-ddraw. أي تشغيل لاحق بدون المتغير يعيد ddraw الخاص بـ Wine تلقائيًا، لأن المزرعة يُعاد ربطها في كل تشغيل.
  2. ينسخ `ddraw.ini` إلى `C:\ProgramData\cnc-ddraw\ddraw.ini` مرة واحدة فقط، ويضبط `CNC_DDRAW_CONFIG_FILE` كما يفعل Winlator.
  3. يضع `ddraw=n,b` في `WINEDLLOVERRIDES` ويستبدل أي قيمة أخرى لـ ddraw. **هذا ضروري (مُقاس):** بدونه يحمّل Wine نسخته builtin ويتجاهل الملف.
  4. يكتب سطر `[WineProc] cnc-ddraw: ...`. إذا لم يكن cnc-ddraw في الحزمة، يقول ذلك ويبقى ddraw الخاص بـ Wine.
- **بلا قاعدة عامة:** لا يتحول أي برنامج إلى cnc-ddraw لمجرد أنه يستورد ddraw، لأن بعض ألعاب DirectDraw تستعمل Direct3D 7 عبر ddraw.
- **التشخيص:** `LaunchDiagnostics.c` يقرأ أسطر `[cnc-ddraw]` ويحوّلها إلى:
  - مرحلتَي الواجهة الرسومية والجهاز ("DirectDraw (cnc-ddraw over DXMT's Direct3D 9)").
  - مشكلة جهاز أو مشكلة اعتمادية، مع تلميح.
  - وصار تلميح `ddraw.dll` الناقص وتلميح النافذة ملء الشاشة يذكران `MADEIRA_DDRAW=cnc`.

### 7.2 WinSxS (نُفّذ لـ 64-bit، وثبت أنه لازم لـ Common Controls 6 فقط)

- **ما قِيس تحت Wine 11.4:**
  - لا يظهر الخطأ c0150002 تحت Wine. عندما لا يجد Wine التجميعة يكتب `Could not find dependent assembly` ويكمل.
  - VC80/VC90 CRT تُحمَّل من system32، فلا مشكلة فيها.
  - **المشكلة الحقيقية:** بدون winsxs يحصل البرنامج على comctl32 5.x، فيُربط `TaskDialogIndirect` بـ stub ويُجهَض البرنامج عند استدعائها.
- **ما نُفّذ:**
  - `comctl32_v6.dll` أُضيف إلى مزرعة ARM64EC (المزرعة فيها الآن 308 وحدات).
  - `app/Madeira/WinSxS.c/.h` (بلغة C) فيه 10 تجميعات.
  - مخزن arm64 لكل جلسة، يُبنى من مزرعة الجلسة نفسها. مخزن x86 يبقى كما كان لبرامج 32-bit.
  - تلميح تشخيص لـ `TaskDialog*`.
- **الاختبار:** `check-winsxs.py` نجح في 77 فحصًا. شغّل الفحص برنامجين (32 و64-bit) تحت Wine حقيقي، قبل البذر وبعده.
- **التوثيق:** `docs/WINSXS.md`. السطر المتوقع على الجهاز: `9/10 seeded from arm64ec-windows, 1 not in that farm (msxml4)`.

### 7.3 wine-mono: مؤجَّل، مع السبب والخطة

- **الحالة اليوم:** Wine في Madeira يتوقع wine-mono **11.0.0** (`WINE_MONO_VERSION`)، لكن بيانات التوافق تذكر ملفات MSI بإصدار 10.1.0 التي يشحنها Winlator.
  - هناك تجارب سابقة في upstream مع wine-mono على الجهاز: `MADEIRA_WINEMONO_BRIDGE` و`mono-suspend`، وخطأ ترجمة في FEX لمولّد كود Mono (ml623). هذا يعني أن المسار حساس، ولم يستقر بعد.
- **سبب التأجيل:**
  - الحجم: نحو 80 MB للـ MSI، وأكثر من ذلك بعد الفك.
  - Mono يعمل بالـ JIT ويولّد كود x86/x64 تُعيد FEX ترجمته (ترجمة مزدوجة).
  - يحتاج تثبيت msi داخل البادئة، أو مجلد مشترك في `share/wine/mono`.
  - لا يمكن إثبات أنه لا يكسر FEX/ARM64EC بدون جهاز.
- **الخطة:**
  1. شحن tarball ‏`wine-mono-11.0.0-x86.tar.xz` مفكوكًا كمجلد مشترك، يجده `get_mono_path` في `mscoree` بلا msiexec. يكون ذلك خلف خيار، كتنزيل لاحق من Dock، وليس داخل الحزمة الأساسية.
  2. اختبار مضيف تحت Wine WoW64: برنامج .NET 4 بسيط وXNA/FNA.
  3. على الجهاز: تشغيل لعبة FNA (مثل Marvel Cosmic Invasion التي جُرّبت سابقًا) مع `MADEIRA_WINEMONO_BRIDGE` وبدونه.
  4. تصحيح إصدار wine-mono في `compat/dependencies.json` ليطابق 11.0.0.

### 7.4 الملفات (الدفعة الثانية)

| الملف | التغيير |
|---|---|
| `third_party/cnc-ddraw/**` | الاستيراد، و`madeira/` ‏(ddraw.h وd3dcaps.h وmadeira_log)، وأسطر السجل في `src/dd.c` و`src/render_d3d9.c`، و`MADEIRA_IMPORT.md` و`MADEIRA_CHANGES.md` |
| `build/ddraw/build.sh`, `build/ddraw/make-ini.py` (جديدان) | بناء الـ DLL وتوليد ddraw.ini |
| `build/wine-i386/build.sh`, `docs/BUILDING.md` | استدعاء بناء cnc-ddraw |
| `app/Madeira/WineProcessBridge.m` | `madeira_apply_cnc_ddraw`، وغلاف `madeira_seed_winsxs` لـ x86 وarm64 |
| `app/Madeira/WinSxS.c/.h` (جديدان) | بذر مخزن side-by-side |
| `app/Madeira/LaunchDiagnostics.c` | أسطر `[cnc-ddraw]`، وتلميحات ddraw والنافذة وTaskDialog |
| `app/Madeira.xcodeproj/project.pbxproj` | `WinSxS.c/.h` والمجلد `cnc-ddraw` |
| `app/Madeira/arm64ec-windows/comctl32_v6.dll`, `build/wine-pe/arm64ec-farm.json` | Common Controls 6 لـ 64-bit |
| `compat/recipes.json`, `compat/dependencies.json`, `compat/wine-modules.json`, `app/Madeira/compat.json` | recipe ‏cnc-ddraw، وdependency builtin، و308 وحدات في مزرعة 64-bit |
| `build/tools/gen-config-catalog.py`, `app/Madeira/ConfigCatalog.generated.swift` | `env.MADEIRA_DDRAW` مع خيارات |
| `LICENSES/MIT-cnc-ddraw.txt`, `app/Madeira/licenses/*`, `THIRD-PARTY-NOTICES.md`, `.gitignore` | الرخص وتجاهل نواتج البناء |
| `docs/DIRECTDRAW.md`, `docs/WINSXS.md` | التوثيق |
| `build/host-tests/check-cnc-ddraw.py`, `check-winsxs.py` (جديدان)؛ `check-launch-diagnostics.py`, `check-launch-routing.py` | الاختبارات |

### 7.5 الاختبارات (Linux x86_64، ‏9 أكتوبر 2026)

- **`check-cnc-ddraw`:** نجح في 50 فحصًا. المتطلبات: `LLVM_MINGW` و`HOST_WINE` و`WINEBUILD`، و`CNC_DDRAW_UPSTREAM` لمقارنة الترويسات. الفحص يشغّل برنامج DirectDraw ‏32-bit تحت Wine WoW64، مع display driver ‏null وd3d9 تسجيلي. النتائج:
  - cnc-ddraw هو الـ ddraw.dll الذي حُمّل، وكتب في السجل `renderer direct3d9`.
  - Direct3D 9 تلقّى: `CreateDevice` (flags ‏0x56)، وvertex buffer، ونسيج `L8` بحجم 1024x1024، ونسيج palette، وshaders ‏ps_2_0، ثم `Present`.
  - الحالات السلبية نجحت كلها: بدون `ddraw=n,b` يُحمَّل ddraw الخاص بـ Wine؛ ومع `auto` يُختار OpenGL؛ ومع فشل `Direct3DCreate9` أو `CreateDevice` يظهر السبب في السجل ثم يحدث التراجع إلى GDI.
- **`check-winsxs`:** نجح في 77 فحصًا. **`check-d3d8to9-wine`:** نجح في 27 فحصًا. **`check-launch-diagnostics` و`check-launch-routing` و`check-game-compat` و`check-config-catalog` و`check-arm64ec-farm`:** كلها ناجحة.
- **`run-all.sh`:** نجح 39 من 46 اختبارًا. الاختبارات السبعة الفاشلة هي نفسها التي تفشل على `main` بسبب بيئة Linux (القسم 3).
  - ظهر فشل ثامن في `check-launch-routing` بسبب نقل بذر WinSxS إلى `WinSxS.c`، وقد أُصلح في commit مستقل.

### 7.6 ما يحتاج Xcode أو iPad

- **ترجمة الكود:** لم يُترجم `WineProcessBridge.m` ‏(`madeira_apply_cnc_ddraw`، وغلاف WinSxS) ولا `Winios.m`، فهما Objective-C ويحتاجان Xcode. الملفات `WinSxS.c` و`LaunchDiagnostics.c` تُرجمت واختُبرت على Linux.
- **cnc-ddraw على الجهاز:**
  - هل يقبل d3d9 الخاص بـ DXMT ‏(shim ثم `d3d9-emulated.dll`) جهاز cnc-ddraw؟ أي: `PUREDEVICE | MULTITHREADED`، وأنسجة `L8` المُدارة، و`LockRect` في كل إطار، وshader ‏ps_2_0 للـ palette.
  - السطر المتوقع في السجل: `[cnc-ddraw] renderer direct3d9` ثم `[cnc-ddraw] Direct3D 9 device ...` ثم مرحلة first-present.
  - تغيير وضع العرض (640x480) تحت Winios، وتحويل إحداثيات الفأرة واللمس (cnc-ddraw يرقّع import tables تحت FEX)، وكلفة رفع السطح كاملًا في كل إطار.
- **WinSxS على الجهاز:** السطر المتوقع هو `[WineProc] winsxs: arm64: 9/10 assemblies seeded from arm64ec-windows`. بعدها يجب أن يعمل TaskDialog في برنامج 64-bit.
- **مزرعة i386 على macOS:** بناؤها كاملة مع d3d8.dll وcnc-ddraw، ثم تغليفها في Xcode.

### 7.7 العمل المتبقي مرتبًا حسب الأثر (محدَّث)

1. **تجربة cnc-ddraw وWinSxS وd3d8 على iPad.** أعلى أثر لأقل كلفة. كل الكود جاهز، والأسطر المتوقعة موثّقة. إذا رفض DXMT جهاز cnc-ddraw، فالسجل سيذكر الخطوة والـ HRESULT.
2. **wine-mono 11.0.0.** أثر متوسط إلى عالٍ لألعاب .NET/XNA/FNA، وكلفة كبيرة. الخطة في 7.3، ويجب أن يكون اختياريًا، ويحتاج جهازًا.
3. **ربط cnc-ddraw بقائمة ألعاب معروفة.** ألعاب Westwood/Blizzard 2D، أي profiles تستعمل recipe ‏`cnc-ddraw`، ولكن فقط بعد نجاح الجهاز في البند 1، وبلا قاعدة عامة.
4. **winedmo.** قيمة منخفضة إلى متوسطة، لأن الوسائط تعمل عبر winegstreamer فوق FFmpeg. يفيد فقط مسارات Media Foundation التي تحتاج winedmo تحديدًا. لم يُنفَّذ.
5. **wine-gecko و`mshtml`.** للمشغّلات وصفحات HTML فقط، والحجم كبير. لم يُنفَّذ.
6. **Direct3D 7 وما قبله عبر ddraw.** لا يوجد له حل بدون Vulkan حتى في Winlator، ويحتاج frontend لـ D3D7 فوق d3d9. لم يُنفَّذ، كما طُلب.

### 7.8 الـ commits (لم يُدفع أي شيء)

- `d39e9c3` 64-bit farm: comctl32_v6 (Common Controls 6.0 for the arm64 side-by-side store)
- `93503a7` WinSxS: seed an arm64 side-by-side store for 64-bit sessions; seeder in plain C with a Wine test
- `ed68a6c` third_party: import cnc-ddraw (MIT) at 279a057, without Microsoft's SDK headers
- `66d012f` cnc-ddraw: build without Microsoft's headers, launch-log lines, Madeira's ddraw.ini
- `6dc7bcf` DirectDraw through cnc-ddraw over DXMT's Direct3D 9, opt-in per game
- `2c086db` check-launch-routing: follow the WinSxS seeder move and the cnc-ddraw step
- ثم commit هذا التحديث.

بعد ربط GitHub: `git push origin universal-game-compatibility`، ثم PR إلى `nwaf92641/Madeira:main`.
