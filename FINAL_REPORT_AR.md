# التقرير النهائي: فرع `universal-game-compatibility`

> **تنبيه:** لم يُختبر أي شيء على iPad، ولا يوجد Xcode في هذه البيئة. كل ما وُصف بأنه "مُختبر" اختُبر آليًا على Linux x86_64 فقط.
> لا يُدّعى توافق بنسبة 100%، ولا نجاح أي لعبة بعينها.

## 1. الحماية

- قبل أي تغيير أُنشئ الـ tag ‏`backup/universal-game-compatibility-1791577204` على `32015f5`،
  مع bundle في `/workspace/madeira-compat-out/backup-1791577204.bundle`.
- لم يُعدَّل `willfaust/Madeira` ولا `brunodev85/winlator`.
- لم يُدفع شيء إلى `main`، ولم يُستخدم force-push ولا `reset --hard`.
- تحققت أن كل عمل الدفعات السابقة موجود:
  - التشخيص (`LaunchDiagnostics.c`).
  - ‏`MADEIRA_WAIT_CHILDREN`.
  - رقعة طبقة Metal.
  - تحويل الصيغة في D3D12.
  - المزرعة 64-bit.
  - ‏d3d8to9.
  - ‏cnc-ddraw.
  - ‏WinSxS.
  - مدقق GameCompat للمزرعة 64-bit.

## 2. ما أُنجز في هذه الدفعة (commit لكل موضوع)

| الـ commit | الموضوع | الدليل |
|---|---|---|
| ‏`cad890a` | ‏`run-all.sh` يشغّل كل اختبارات المضيف | الفرع مقابل main، في `WINE_COMPATIBILITY_TEST_MATRIX.md` |
| ‏`30c2f62` | المزرعة 64-bit: ‏MSI، وmsiexec، وMSXML4، وscrrun، وWSH، وJScript، وVBScript، وWMI scripting، والجدار الناري، وsensapi، وshfolder (15 وحدة Wine حقيقية) | فئات COM بلا DLL نزلت من 259 إلى 209، وWinSxS ‏10/10 |
| ‏`5ede471` | اختبار طبقة المثبّتات والسكربتات، ومحاكاة المزرعة على Wine سطح المكتب | ‏`compat-layers-x64`: قبل 63/17/4، وبعد 81/0/4 |
| ‏`e0d0907` | محرك التوافق يحل API sets عبر schema الخاص بـ Wine ومزرعة البرنامج | اختبارات Swift |
| ‏`c511adc` | تصحيح بيانات التوافق، واختبارات انحدار تفشل إن ادّعى JSON وجود DLL غير مشحون | تحققت أنها تفشل على البيانات القديمة |
| ‏`c5e6a8e` | تلميحات تشخيص لـ API sets والمثبّتات | سيناريو apiset، وتحققت أنه يفشل عند تعطيل المطابقة |
| ‏`84f934f` | ‏Wine Mono 11.0.0 مكوّن اختياري يُربط بالبادئة | تحت Wine: ‏csc يترجم برنامج C# ويشغّله 64-bit |
| ‏`498b03f` | ‏`wine-audit.py`: تدقيق آلي لكل وحدات Wine (679 وحدة) | ‏`WINE_COMPLETENESS_AUDIT.md` |
| ‏`5509b28` | ‏CI يشغّل check-wine-mono وcheck-winsxs وcheck-d3d8-shader | ‏— |
| (هذا الـ commit) | التقارير | ‏— |

بلا DLL مزيفة، وبلا ملفات تعريف لكل لعبة، وبلا Vulkan. ‏DXMT وMetal كما هما.

**التراخيص:**

- كل ما أُضيف إلى المزرعة وحدات Wine ‏(LGPL، موثّقة في `docs/LICENSING.md` ضمن "PE DLLs in arm64ec-windows").
- ‏Wine Mono لا يُشحن مع التطبيق ولا يُعدَّل، بل يضعه المستخدم بنفسه. تراخيصه بحسب ملف COPYING الخاص به: ‏LGPL/MIT X11، وMS-PL، وzlib، وMIT. وهي مذكورة في `docs/WINE_MONO.md` وفي `docs/LICENSING.md`.
- لا ملفات من Microsoft.

## 3. نتائج الاختبارات (ملخص)

| | main | الفرع |
|---|---|---|
| اختبارات المضيف (run-all) | ‏29 PASS، و7 FAIL، و1 SKIP | ‏40 PASS، و6 FAIL، و1 SKIP |

الستة الفاشلة نفسها في main وفي الفرع، وسببها بيئي: Python 3.14، وzlib.h، واختبارات Swift قائمة سابقة.

نتائج اختبارات Wine:

- d3d8to9-wine: ناجح.
- cnc-ddraw: ناجح.
- WinSxS: ناجح.
- Wine Mono: ناجح.
- compat-layers-x64: ‏81/0/4.

التفاصيل في `WINE_COMPATIBILITY_TEST_MATRIX.md`.

## 4. التقارير

| الملف | المحتوى |
|---|---|
| ‏`WINE_COMPLETENESS_AUDIT.md` | تصنيف كل وحدة Wine: تُشحن، أو مستبدلة، أو بلا backend، أو تُبنى ولا تُشحن لـ 64-bit، أو stub-heavy |
| ‏`WINE_MISSING_IMPLEMENTATIONS.md` | الفرق بين نقص Madeira (يُصلح بالشحن) ونقص Wine (stub أو E_NOTIMPL) |
| ‏`WINLATOR_COMPARISON.md` | المقارنة مع Winlator بعد هذا الفرع |
| ‏`GAME_BLACK_SCREEN_ROOT_CAUSES.md` | الأسباب الـ 12 وحالتها، وأسباب جديدة |
| ‏`WINE_COMPATIBILITY_TEST_MATRIX.md` | كل الاختبارات ونتائجها |
| ‏`WINLATOR_PORT_REPORT_AR.md` | أُضيف القسم 8 عن هذه الدفعة |

## 5. الفجوات المتبقية (مرتبة حسب الأثر)

1. **التحقق على iPad.** هذا أهم ما ينقص:
   - تشغيل `compat-layers-x64.exe` على الجهاز.
   - تجربة لعبة D3D11 حقيقية مع `last-launch.txt`.
   - تجربة d3d8to9 وcnc-ddraw مع ألعاب 32-bit.
   - تجربة Wine Mono تحت FEX.
2. **بناء Xcode:** `WineMono.c` أُضيف إلى `project.pbxproj` يدويًا (A1000D1E، وA2000D1E، وA2000D1F). يجب أن يُبنى.
3. **فيديو 64-bit:** ‏winedmo وجانبه unix فوق FFmpeg، ثم mfsrcsnk وأخواتها.
4. **wine-gecko:** يحتاج mshtml لمشغّلات HTML. يمكن أن يصبح مكوّنًا اختياريًا بطريقة Wine Mono نفسها.
5. **نقص في Wine نفسه:**
   - ‏dcomp: ‏`DCompositionCreateDevice` يعيد E_NOTIMPL.
   - ‏gameinput: ‏`GameInputCreate` يعيد E_NOTIMPL.
   - بعض stubs في dwmapi وd3dcompiler_47.
6. **مضيفو API sets لـ 64-bit:** ‏windows.storage، وtwinapi.appcore، وwintypes. صارت مُشخَّصة، لكنها غير مشحونة.
7. **أسباب السواد المفتوحة:** ميزات D3D12، وأنسجة BC، والذاكرة وJIT. كلها تحتاج iPad.

## 6. GitHub

حالة الدفع وPR في القسم الأخير من `/workspace/madeira-compat-out/PR_DESCRIPTION.md` وفي رسالة الختام.
لم يُخترع رابط PR، ولم يُقرأ أي token.
