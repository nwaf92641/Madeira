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

## 5. العمل المتبقي مرتبًا حسب الأثر

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
