# مصفوفة اختبارات التوافق

كل النتائج أدناه من تشغيل فعلي على Linux x86_64 في هذه البيئة بتاريخ 2026-10-09.

**لم يُشغَّل أي شيء على iPad، ولا يوجد Xcode.** لذلك:

- عمود "iPad" كله "غير مُثبت".
- "PASS" يعني أن الاختبار الآلي نجح على المضيف فقط.

## 1. ‏`build/host-tests/run-all.sh`: الفرع مقابل main

الأمر:

```
SWIFTC=.../swiftc LLVM_MINGW=... HOST_WINE=... WINEBUILD=... build/host-tests/run-all.sh
```

| | main ‏(`origin/main`، worktree منفصل) | هذا الفرع |
|---|---|---|
| عدد الاختبارات | 37 | 47 (10 جديدة) |
| PASS | 29 | 40 |
| FAIL | 7 | 6 |
| SKIP | 1 | 1 |

الاختبارات الستة الفاشلة **هي نفسها في main وفي الفرع**، وسبب فشلها بيئي أو سابق للفرع، لا من تغييراته:

| الاختبار | سبب الفشل |
|---|---|
| check-steam-library | يحتاج Python 3.14 ‏(`compression.zstd`)، والبيئة فيها 3.13 |
| check-dock-components | ‏`zlib.h` غير مثبت |
| check-swap-coverage | يفشل على main بالرسالة نفسها: "coverage line printed at tier start" |
| check-cfg-early-docs | يفشل على main أيضًا (BOM + CRLF في قارئ Swift) |
| check-onboarding | يفشل على main أيضًا |
| check-runtime-settings | يفشل على main أيضًا |

أما check-config-catalog فيفشل في main ("ConfigCatalog.generated.swift is out of date") وينجح في الفرع. ‏check-wma-decoder ‏SKIP في الحالتين لأنه يحتاج بناء Wine لـ macOS.

## 2. الاختبارات التي أضافها هذا الفرع أو غيّرها

| الاختبار | ما يتحقق منه | نتيجة المضيف | iPad |
|---|---|---|---|
| check-arm64ec-farm | كل import وdelay-import في المزرعة 64-bit محلول، وفئات COM المهمة لها DLL، و`compat/wine-modules.json` يطابق المزرعة | PASS | غير مُثبت |
| check-game-compat (Swift) | ‏GameCompat: المزرعة لكل معمارية، وAPI sets، وأن كل builtin يدّعيه JSON مشحون فعلًا، ولا تبعيات وصفات معلّقة. تحققت أن فحوص الادّعاء تفشل على البيانات القديمة | ‏PASS ‏(247 فحصًا) | — |
| check-launch-diagnostics | كل فئات التشخيص، ومنها السيناريوهان الجديدان apiset وdotnet (تحققت أن apiset يفشل عند تعطيل المطابقة) | PASS | غير مُثبت |
| check-wine-mono | الجزء 1: حالات الربط الست (C تحت ASan/UBSan). الجزء 2: تحت Wine، قبل الربط "Wine Mono is not installed"، وبعده csc يترجم ويشغّل 64-bit | ‏PASS ‏(16 مع الجزء 2) | غير مُثبت تحت FEX |
| check-winsxs | ‏WinSxS.c، و10/10 تجميعات، وTaskDialog تحت Wine | ‏PASS ‏(78 مع جزء Wine) | غير مُثبت |
| check-d3d8-shader | مترجم شيدرات D3D8 مقابل محلل DXSO في DXMT | ‏PASS ‏(32) | — |
| check-d3d8to9-wine | ‏d3d8.dll تحت Wine WoW64 | انظر §4 | غير مُثبت |
| check-cnc-ddraw | ‏cnc-ddraw فوق d3d9 تحت Wine | انظر §4 | غير مُثبت |
| check-launch-routing | استدعاءات wbem وWinSxS وcnc-ddraw في WineProcessBridge.m | PASS | يحتاج Xcode |
| check-layer-format | جدول صيغ الطبقة، وقرار النسخ أو التحويل في D3D12، وأن رقعة DXMT تنطبق | PASS | غير مُثبت |
| check-child-slots | أبناء المشغّل يحتفظون بالجلسة | PASS | غير مُثبت |

## 3. ‏`compat-layers-x64.exe`: طبقات Windows من عملية 64-bit

تحت Wine x86_64 على سطح المكتب. يحاكي `build/x64-tests/farm-overrides.py` المزرعة بتعطيل ما ليس فيها عبر `WINEDLLOVERRIDES`.

| المزرعة | PASS | FAIL | SKIP |
|---|---|---|---|
| قبل `installer_scripting` (المزرعة كما في `32015f5`، `farm-overrides.py --rev 32015f5`) | 63 | 17 | 4 |
| بعدها (HEAD) | 81 | 0 | 4 |

الطبقات المختبرة:

- **الرسوميات المساعدة:** d3dx9/10/11، وd3dcompiler، وd2d1، وdxdiagn.
- **الصوت:** XAudio2 2.7/2.8/2.9، وX3DAudio، وdsound، وACM، وMIDI map.
- **الفيديو:** DirectShow (FilterGraph، وSampleGrabber)، وMF، وWMV reader، وVfW.
- **وقت التشغيل:** DirectPlay8، وVC runtimes، وGDI+، وMSXML3/4/6، وWMI ‏(Win32_VideoController).
- **المثبّتات والسكربتات:** MSI (قاعدة بيانات وSQL)، وmsiexec، وFileSystemObject، وWScript.Shell، وJScript، وVBScript، وSWbemLocator، وINetFwMgr، وIsNetworkAlive، وSHGetFolderPath.

الملف التنفيذي منسوخ في المزرعة ليُشغَّل على iPad: `C:\windows\system32\compat-layers-x64.exe`.

## 4. نتائج اختبارات Wine الإضافية

| الاختبار | النتيجة |
|---|---|
| check-d3d8to9-wine (Wine 11.4 WoW64 + llvm-mingw) | PASS (27)، 0 FAIL: d3d8.dll الحقيقي يُحمَّل في عملية 32-bit، ويُنشئ جهازًا فوق Direct3D 9 مسجِّل |
| check-cnc-ddraw (الجزء 2 تحت Wine) | PASS (50)، 0 FAIL |
| check-winsxs (جزء Wine) | PASS (78)، 0 FAIL |
| check-wine-mono (الجزء 2) | PASS (16)، 0 FAIL |

لم يتمكّن run-all الكامل من تشغيل هذه الأجزاء لأن متغيرات البيئة كانت ناقصة، فشُغّلت يدويًا بالبيئة الصحيحة.
السجلات في `/tmp/w-*.log` على الجهاز الافتراضي.

## 5. ما لا يمكن اختباره هنا

- أي شيء على iPad: تحميل وحدات ARM64EC عبر FEX، وMetal/DXMT، وأول إطار، وWine Mono تحت FEX، وأي لعبة حقيقية.
- بناء Xcode: `WineMono.c` مضاف إلى `project.pbxproj` يدويًا، ولم يُبنَ بـ Xcode.
- check-wma-decoder: يحتاج بناء Wine لـ macOS.
