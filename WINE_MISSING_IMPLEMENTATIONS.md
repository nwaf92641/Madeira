# ما ينقص Wine فعلًا (تنفيذ ناقص) مقابل ما ينقص Madeira (شحن ناقص)

هذا الملف يفرّق بين نوعين من النقص لأن علاجهما مختلف:

- **نقص في Madeira:** الوحدة موجودة في Wine، لكن Madeira لا يشحنها لعملية من المعمارية المطلوبة. هذا يُصلح بالبناء والشحن، وقد أُصلح منه في هذا الفرع ما توفّر له دليل.
- **نقص في Wine نفسه:** الدالة `stub` في ملف `.spec`، أو موجودة وتعيد `E_NOTIMPL` مع `FIXME`. هذا لا يُصلح في Madeira إلا بكتابة تنفيذ في Wine، ولم نزيّفه بـ DLL وهمية.

المصدر: Wine 11.4 في `wine/`. الأعداد تولّدها `build/tools/wine-audit.py` (الجداول الكاملة في `WINE_COMPLETENESS_AUDIT.md`).

## 1. نقص في Madeira أُصلح في هذا الفرع

| الفجوة | العَرَض قبل الإصلاح | الإصلاح | التحقق |
|---|---|---|---|
| msi وmsiexec لعمليات 64-bit | مثبّتات MSI ‏64-bit وMsiOpenDatabase تفشل بـ "not found" | مجموعة `installer_scripting` في `build/wine-pe/arm64ec-farm.json` | ‏`test_installer_scripting` في `compat-layers-x64.c`: إنشاء قاعدة MSI وتنفيذ SQL، و`msiexec /x` يعيد 1605 |
| msxml4 ‏64-bit وتجميع WinSxS الخاص بها | ‏`CoCreateInstance(Msxml2.DOMDocument.4.0)` يعيد REGDB_E_CLASSNOTREG، ومُستلزم manifest لا يُحل | ‏msxml4، وmsxml، وmsxml2 | 10/10 تجميعات WinSxS ‏(`check-winsxs.py`) واختبار DOM |
| ‏Scripting.FileSystemObject، وWScript.Shell، وJScript، وVBScript، وSWbemLocator لـ 64-bit | المشغّلات وسكربتات التثبيت تفشل | ‏scrrun، وwshom.ocx، وjscript، وvbscript، وwbemdisp | اختبارات COM في `compat-layers-x64` |
| ‏INetFwMgr ‏(hnetcfg)، وIsNetworkAlive ‏(sensapi)، وSHGetFolderPathA ‏(shfolder) | مشغّلات ألعاب الشبكة وبعض برامج الإعداد | ‏hnetcfg، وsensapi، وshfolder | ‏`compat-layers-x64` |
| ‏.NET Framework ‏(mscoree) | "Wine Mono is not installed"، والبرنامج لا يبدأ | ‏Wine Mono 11.0.0 مكوّن اختياري تربطه `app/Madeira/WineMono.c` بالبادئة، ويظهر تلميح واضح في تشخيص الإقلاع عند غيابه | ‏`check-wine-mono.py`: الجزء 2 تحت Wine على سطح المكتب، حيث ترجم csc برنامج C# وشغّله 64-bit |
| ‏API sets بلا مضيف | ‏STATUS_DLL_NOT_FOUND صامت | لم يُزيَّف مضيف. ‏`compat.json` يسجّل `wine_api_sets`، و`GameCompat.apiSetProblem` يبلّغ عن الفجوة قبل التشغيل، و`LaunchDiagnostics` يعطي تلميحًا | اختبارات Swift في `check-game-compat.py`، وسيناريو "apiset" |

## 2. نقص في Madeira متبقٍّ (يمكن إصلاحه بالشحن إن ظهر دليل)

| الوحدة | لماذا لم تُشحن لـ 64-bit | متى تُضاف |
|---|---|---|
| ‏`winedmo` | يحتاج الجزء unix الخاص به (FFmpeg) لـ 64-bit. بدونه mfsrcsnk وmfmp4srcsnk وmfasfsrcsnk بلا فائدة | عند تفعيل الجزء unix الخاص بالوسائط لـ 64-bit على الجهاز |
| ‏`fusion` | ‏GAC الخاص بـ .NET. ‏Wine Mono لا يحتاجه للتشغيل | إن احتاج مثبّت ‏.NET ذلك (يظهر كـ missing-dll) |
| ‏`wintypes`، و`windows.storage.applicationdata`، و`windows.*` ‏(WinRT) | نادرة في ألعاب Win32 | إن ظهرت في `wine_api_sets` أو في تشخيص لعبة |
| ‏`gameinput` | في Wine 11.4 ‏`GameInputCreate` نفسه يعيد `E_NOTIMPL`، فشحنه يغيّر رسالة الفشل فقط | عند تنفيذه في Wine |
| ‏DirectMusic ‏(dmime، وdmusic...)، و`xactengine2_*`، و`d3dx9_24..30` | ألعاب هذه المكتبات 32-bit، وهي موجودة في مزرعة i386 | لا حاجة معروفة لـ 64-bit |
| ‏mshtml مع wine-gecko | ‏gecko حوالي 100MB، ويحتاج تحققًا على الجهاز | مكوّن اختياري مثل Wine Mono، والعمل نفسه ممكن |
| ‏winedbg | ‏4.5MiB، ولا يُعرض حوار التعطل | للتشخيص فقط |

## 3. نقص في Wine نفسه، والمهم منه للألعاب

أعداد `stub` من ملفات `.spec` في Wine 11.4، بالصيغة: التصديرات / منها stub.

| الوحدة | التصديرات / stub | أثرها على الألعاب |
|---|---|---|
| ‏dinput8 / dinput / xinput1_3 / xinput1_4 / dsound / xaudio2_9 | ‏5/0، و7/0، و9/0، و10/1، و14/0، و7/0 | لا stubs في نقاط الدخول. التنفيذ عبر COM |
| ‏ws2_32 / winhttp | ‏135/3 و49/0 | مكتملة تقريبًا |
| ‏kernelbase / kernel32 / ntdll | ‏1432/70، و1552/119، و1527/101 | الـ stubs داخلية (appcompat cache، وBem*، وlocale helpers) ولا تستدعيها الألعاب عادة |
| ‏user32 / gdi32 | ‏983/202، و545/82 | الـ stubs غالبًا واجهات قديمة أو داخلية. الرسم الفعلي عبر win32u |
| ‏dwmapi | ‏85/60 | ‏`DwmSetDxFrameDuration` و`DwmModifyPreviousDxFrameDuration` stub. الدوال التي تستدعيها الألعاب عادة، مثل DwmIsCompositionEnabled وDwmFlush وDwmGetWindowAttribute، منفّذة جزئيًا |
| ‏dcomp | ‏26/22 | ‏`DCompositionCreateDevice`/`2`/`3` تعيد `E_NOTIMPL`. واجهات Chromium/CEF ومشغّلات حديثة تستخدم DirectComposition، فتقع إلى الرسم العادي أو تفشل |
| ‏mfplat / mf | ‏183/66، و86/52 | ‏COM هو المسار الأساسي. الفشل الفعلي يكون في codecs (راجع docs/MEDIA.md)، لا في stubs |
| ‏d3dcompiler_47 | ‏29/10 | ‏`D3DCompressShaders` و`D3DDecompressShaders` و`D3DSetBlobPart` و`D3DReflectLibrary` stub. ‏D3DCompile وD3DReflect منفّذتان |
| ‏gameinput | ‏3/0 في `.spec`، لكن `GameInputCreate` يعيد `E_NOTIMPL` | ألعاب GDK التي تعتمد على GameInput وحده بلا رجوع إلى XInput لن ترى متحكمًا |
| ‏combase / shcore | ‏355/165، و185/112 | ‏WinRT وDPI. ‏SetProcessDpiAwareness وGetDpiForMonitor منفّذتان |
| ‏setupapi | ‏617/244 | مثبّتات drivers. لا يستخدمها اللعب |
| ‏bcrypt / crypt32 | ‏62/23، و246/35 | ‏DRM وTLS: الخوارزميات الأساسية منفّذة |

لا يكتب هذا الفرع تنفيذات جديدة داخل Wine. أي دالة ناقصة تظهر أثناء التشغيل يلتقطها `LaunchDiagnostics` بهذه الصيغة:

`unimplemented-function: Call from ... to unimplemented function dll.fn`

وتحدد الصيغة الوحدة والدالة بالضبط، فيصبح إصلاحها في Wine مهمة محددة.
