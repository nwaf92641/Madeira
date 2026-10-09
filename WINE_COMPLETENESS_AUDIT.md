# تدقيق اكتمال Wine في Madeira

الفرع `universal-game-compatibility` (fork ‏`nwaf92641/Madeira`)، وWine 11.4 (الـ submodule).
التدقيق يولّده `build/tools/wine-audit.py` من الملفات نفسها، ولا يعتمد على الذاكرة:

- ملفات `.spec` في `wine/dlls/*` و`wine/programs/*`: عدد التصديرات، وكم منها `stub`.
- قائمة `SKIP_REASON` في `build/wine-i386/build.sh`: ما لا يُبنى لمزرعة i386 ولماذا. المزرعة نفسها تُبنى على Mac وليست في git.
- المزرعتان المتتبّعتان: `app/Madeira/arm64ec-windows` (عمليات x64 عبر ARM64EC) و`app/Madeira/aarch64-windows` (ARM64 أصلي).
- حدود iOS المعروفة: لا OpenGL، ولا Vulkan، ولا X11/Wayland/ALSA/Pulse، وما تملكه DXMT وd3d8to9 وruntime ‏D3D12 وFEX.

أعد التوليد بهذا الأمر: `python3 build/tools/wine-audit.py --summary` (أو `--json`، أو `--markdown <modules>`).

**الملخص:** 679 modules: backend-unavailable 19, built-not-shipped-64 222, opt-in-backend 2, replaced 9, ships 275, source-not-built 38, stub-heavy 114

## التصنيفات

| التصنيف | المعنى | العدد |
|---|---|---|
| `ships` | تُشحن في المزرعة 32-bit وفي مزرعة 64-bit (ARM64EC) | 275 |
| `replaced` | Madeira يضع بديلًا: DXMT (Metal) لـ d3d9/10/11/dxgi، وd3d8to9 لـ d3d8، وruntime ‏D3D12 الخاص بـ Madeira، وFEX لـ xtajit | 9 |
| `opt-in-backend` | الوحدة موجودة، لكن الواجهة الخلفية اختيارية: mscoree يحتاج مكوّن Wine Mono، وwinegstreamer 64-bit يحتاج `MADEIRA_WG_64BIT=1` | 2 |
| `backend-unavailable` | لا معنى لها على iOS: wined3d وOpenGL وVulkan وX11/Wayland/ALSA/Pulse/OSS، وmshtml بلا wine-gecko | 19 |
| `source-not-built` | في المصدر، ولا تُبنى عمدًا: drivers ‏.sys (تعمل 64-bit فقط تحت WoW64)، وطبقة 16-bit، وwinemac.drv، وwinedbg، وwinemenubuilder، وwineps.drv | 38 |
| `built-not-shipped-64` | تُبنى لـ i386، وغير موجودة في مزرعة ARM64EC: عملية x64 تطلبها تحصل على "not found" | 222 |
| `stub-heavy` | تُشحن، لكن أكثر من نصف تصديراتها `stub` (حين يكون عددها 20 أو أكثر) | 114 |
| `missing-dll` | يطلبها شيء، ولا مصدر لها في Wine | 0 |

**تنبيه:** تصنيف `stub-heavy` مؤشر للتحقيق فقط، وليس حكمًا. بعض الوحدات تعمل عبر مداخل أخرى غير جدول التصديرات:

- وحدات COM مثل mf وmmdevapi وpropsys تعمل عبر الواجهات (vtables).
- win32u جدول syscalls.

لذلك لا يعني كثرة الـ stub في هذه الوحدات أنها معطلة. أما الدالة الناقصة فعلًا، فتظهر عند التشغيل في `unimplemented-function` ضمن تشخيص الإقلاع.

## ما تغيّر في هذه الدفعة (بالأدلة)

| التغيير | الدليل قبل | الدليل بعد |
|---|---|---|
| مجموعة `installer_scripting` في المزرعة 64-bit تضم 15 وحدة: msi، وmsiexec، وmsxml/msxml2/msxml4، وscrrun، وwshom.ocx، وjscript، وvbscript، وwbemdisp، وhnetcfg، وsensapi، وshfolder، وmspatcha، وodbccp32 | `compat-layers-x64.exe` تحت Wine بمزرعة مماثلة: 63 PASS، و17 FAIL، و4 SKIP. فئات COM 64-bit بلا DLL: ‏259 | ‏81 PASS، و0 FAIL، و4 SKIP. الفئات بلا DLL: ‏209. ‏WinSxS 64-bit: ‏10/10 تجميعات (كان msxml4 ناقصًا) |
| API sets: يسجّلها `compat.json` ‏(`wine_api_sets`)، ويفحصها `GameCompat.apiSetProblem` | لم يكن هناك تحقق، فـ api-ms-win-* بلا مضيف يفشل بصمت بـ STATUS_DLL_NOT_FOUND | اختبارات Swift في `check-game-compat.py`، وتلميح في `LaunchDiagnostics` |
| Wine Mono 11.0.0 مكوّن اختياري يُربط بالبادئة (`WineMono.c`) | تحت Wine على سطح المكتب: "Wine Mono is not installed" | ‏`csc.exe` يترجم برنامج C#، والبرنامج يعمل 64-bit (`check-wine-mono.py`، الجزء 2) |

## أكبر الفجوات المتبقية (مرتبة حسب الأثر على الألعاب)

1. **`built-not-shipped-64` (222 وحدة).** معظمها نادر في الألعاب: WinRT ‏(windows.*)، وTWAIN، وأدوات الإدارة.
   ما قد يهم الألعاب منها:
   - DirectMusic ‏(dmime، وdmusic، وdmloader، وdmsynth...). ألعاب DirectMusic كلها تقريبًا 32-bit، وتجد هذه الوحدات في مزرعة i386.
   - `xactengine2_*` و`d3dx9_24..30`: إصدارات قديمة لأدوات 32-bit غالبًا (حوالي 10MB).
   - `winedmo`: يمنع mfsrcsnk وmfmp4srcsnk وmfasfsrcsnk لـ 64-bit.
   - `fusion` ‏(.NET GAC).
   - `wintypes`: مضيف API set من WinRT.
   - `gameinput` و`xinputuap`.
2. **mshtml / wine-gecko:** غير مشحون. أي مشغّل (launcher) يعرض HTML عبر WebBrowser control سيفشل. ‏`LaunchDiagnostics` يصنّف ذلك missing-dll / dependency.
3. **winedbg:** غير مشحون، لذلك لا يوجد backtrace تلقائي عند التعطل. ‏dbghelp موجود.
4. **drivers** ‏(winebus.sys، وwinehid.sys، وwinexinput.sys): غير مبنية. المتحكمات تمر بمسار XInput المباشر في `WiniosGamepad.c` (docs/CONTROLLERS.md). أما DirectInput لأجهزة HID العامة فمحدود.
5. **وحدات `stub-heavy`** مثل dcomp، وwindows.storage، وtwinapi.appcore، وpropsys: تُشحن، لكن أجزاء كبيرة منها stub في Wine نفسه، ولا إصلاح لها داخل Madeira دون كتابة تنفيذ لـ Wine.

## الجداول الكاملة

### built-not-shipped-64
| Module | Exports (stub) | 32-bit farm | 64-bit ARM64EC | ARM64 | Class | Note |
| --- | --- | --- | --- | --- | --- | --- |
| acledit | 8 (6) | yes | no | no | built-not-shipped-64 |  |
| aclui | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| activeds | 28 (8) | yes | no | no | built-not-shipped-64 |  |
| activeds.tlb | 0 (0) | yes | no | no | built-not-shipped-64 |  |
| actxprxy | 5 (0) | yes | no | yes | built-not-shipped-64 |  |
| adsldp | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| amsi | 13 (4) | yes | no | no | built-not-shipped-64 |  |
| appwiz.cpl | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| browseui | 6 (0) | yes | no | no | built-not-shipped-64 |  |
| capi2032 | 11 (0) | yes | no | no | built-not-shipped-64 |  |
| cards | 5 (0) | yes | no | no | built-not-shipped-64 |  |
| cdosys | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| comcat | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| connect | 15 (13) | yes | no | no | built-not-shipped-64 |  |
| crtdll | 535 (9) | yes | no | no | built-not-shipped-64 |  |
| cryptdlg | 21 (10) | yes | no | no | built-not-shipped-64 |  |
| cryptdll | 14 (11) | yes | no | no | built-not-shipped-64 |  |
| cryptowinrt | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| cryptxml | 19 (13) | yes | no | no | built-not-shipped-64 |  |
| ctapi32 | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| ctl3d32 | 25 (1) | yes | no | no | built-not-shipped-64 |  |
| d3dx9_24 | 320 (62) | yes | no | no | built-not-shipped-64 |  |
| d3dx9_25 | 323 (65) | yes | no | no | built-not-shipped-64 |  |
| d3dx9_26 | 327 (69) | yes | no | no | built-not-shipped-64 |  |
| d3dx9_27 | 327 (69) | yes | no | no | built-not-shipped-64 |  |
| d3dx9_28 | 332 (69) | yes | no | no | built-not-shipped-64 |  |
| d3dx9_29 | 332 (69) | yes | no | no | built-not-shipped-64 |  |
| d3dx9_30 | 332 (69) | yes | no | no | built-not-shipped-64 |  |
| dataexchange | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| dbgeng | 6 (2) | yes | no | no | built-not-shipped-64 |  |
| desk.cpl | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| dhtmled.ocx | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| diasymreader | 8 (5) | yes | no | no | built-not-shipped-64 |  |
| difxapi | 12 (0) | yes | no | no | built-not-shipped-64 |  |
| directmanipulation | 6 (2) | yes | no | no | built-not-shipped-64 |  |
| dispex | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmband | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmcompos | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmime | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmloader | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmscript | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmstyle | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmsynth | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmusic | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dmusic32 | 2 (0) | yes | no | no | built-not-shipped-64 |  |
| dsquery | 18 (14) | yes | no | no | built-not-shipped-64 |  |
| dssenh | 27 (5) | yes | no | yes | built-not-shipped-64 |  |
| dsuiext | 9 (5) | yes | no | no | built-not-shipped-64 |  |
| dswave | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| dxcore | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| explorerframe | 5 (0) | yes | no | no | built-not-shipped-64 |  |
| fntcache | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| fusion | 17 (8) | yes | no | no | built-not-shipped-64 |  |
| gameinput | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| geolocation | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| gphoto2.ds | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| graphicscapture | 2 (0) | yes | no | no | built-not-shipped-64 |  |
| hhctrl.ocx | 6 (0) | yes | no | no | built-not-shipped-64 |  |
| httpapi | 37 (14) | yes | no | no | built-not-shipped-64 |  |
| hvsimanagementapi | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| ia2comproxy | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| icmui | 2 (1) | yes | no | no | built-not-shipped-64 |  |
| ieframe | 9 (0) | yes | no | yes | built-not-shipped-64 |  |
| ieproxy | 5 (0) | yes | no | no | built-not-shipped-64 |  |
| iertutil | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| inetmib1 | 4 (2) | yes | no | no | built-not-shipped-64 |  |
| infosoft | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| initpki | 4 (2) | yes | no | no | built-not-shipped-64 |  |
| inkobj | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| inseng | 12 (6) | yes | no | no | built-not-shipped-64 |  |
| iprop | 8 (0) | yes | no | no | built-not-shipped-64 |  |
| itircl | 4 (1) | yes | no | no | built-not-shipped-64 |  |
| itss | 6 (1) | yes | no | no | built-not-shipped-64 |  |
| joy.cpl | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| kerberos | 11 (8) | yes | no | no | built-not-shipped-64 |  |
| ksproxy.ax | 10 (7) | yes | no | no | built-not-shipped-64 |  |
| loadperf | 14 (8) | yes | no | no | built-not-shipped-64 |  |
| localui | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| lz32 | 12 (0) | yes | no | no | built-not-shipped-64 |  |
| mf3216 | 2 (2) | yes | no | no | built-not-shipped-64 |  |
| mfasfsrcsnk | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| mfh264enc | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| mfmp4srcsnk | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| mfsrcsnk | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| mgmtapi | 9 (7) | yes | no | no | built-not-shipped-64 |  |
| mmcndmgr | 7 (3) | yes | no | no | built-not-shipped-64 |  |
| msado15 | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| mscat32 | 35 (5) | yes | no | no | built-not-shipped-64 |  |
| msctfmonitor | 7 (3) | yes | no | no | built-not-shipped-64 |  |
| msctfp | 5 (0) | yes | no | no | built-not-shipped-64 |  |
| msdaps | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| msdasql | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| msdelta | 15 (15) | yes | no | no | built-not-shipped-64 |  |
| mshtml.tlb | 0 (0) | yes | no | no | built-not-shipped-64 |  |
| msident | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| msimsg | 0 (0) | yes | no | no | built-not-shipped-64 |  |
| msimtf | 6 (2) | yes | no | no | built-not-shipped-64 |  |
| msisip | 8 (4) | yes | no | no | built-not-shipped-64 |  |
| msisys.ocx | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| msports | 11 (9) | yes | no | no | built-not-shipped-64 |  |
| msscript.ocx | 5 (1) | yes | no | no | built-not-shipped-64 |  |
| mssip32 | 9 (7) | yes | no | no | built-not-shipped-64 |  |
| mstask | 28 (14) | yes | no | no | built-not-shipped-64 |  |
| msttsengine | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| msv1_0 | 19 (16) | yes | no | no | built-not-shipped-64 |  |
| msvcp120_app | 3810 (402) | yes | no | no | built-not-shipped-64 |  |
| msvcp70 | 6920 (1208) | yes | no | no | built-not-shipped-64 |  |
| msvcp71 | 7055 (816) | yes | no | no | built-not-shipped-64 |  |
| msvcr120_app | 2158 (404) | yes | no | no | built-not-shipped-64 |  |
| msvcr70 | 910 (26) | yes | no | no | built-not-shipped-64 |  |
| msvcr71 | 907 (26) | yes | no | no | built-not-shipped-64 |  |
| msvcrt20 | 1783 (2) | yes | no | no | built-not-shipped-64 |  |
| msvcrt40 | 1880 (2) | yes | no | no | built-not-shipped-64 |  |
| msvcrtd | 861 (33) | yes | no | no | built-not-shipped-64 |  |
| msvdsp | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| mtxdm | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| nddeapi | 28 (2) | yes | no | no | built-not-shipped-64 |  |
| netcfgx | 16 (12) | yes | no | no | built-not-shipped-64 |  |
| netprofm | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| newdev | 18 (12) | yes | no | yes | built-not-shipped-64 |  |
| npmshtml | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| objsel | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| odbc32 | 174 (56) | yes | no | no | built-not-shipped-64 |  |
| odbccu32 | 37 (1) | yes | no | no | built-not-shipped-64 |  |
| oledb32 | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| olesvr32 | 12 (3) | yes | no | no | built-not-shipped-64 |  |
| olethk32 | 14 (14) | yes | no | no | built-not-shipped-64 |  |
| opcservices | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| packager | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| photometadatahandler | 4 (1) | yes | no | no | built-not-shipped-64 |  |
| pidgen | 7 (5) | yes | no | no | built-not-shipped-64 |  |
| prntvpt | 27 (9) | yes | no | no | built-not-shipped-64 |  |
| pstorec | 7 (2) | yes | no | no | built-not-shipped-64 |  |
| pwrshplugin | 12 (12) | yes | no | no | built-not-shipped-64 |  |
| qmgr | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| qmgrprxy | 5 (0) | yes | no | no | built-not-shipped-64 |  |
| qwave | 14 (11) | yes | no | no | built-not-shipped-64 |  |
| rometadata | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| rsabase | 27 (0) | yes | no | no | built-not-shipped-64 |  |
| rstrtmgr | 12 (4) | yes | no | no | built-not-shipped-64 |  |
| sane.ds | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| sapi | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| sas | 1 (1) | yes | no | no | built-not-shipped-64 |  |
| scarddlg | 5 (5) | yes | no | no | built-not-shipped-64 |  |
| scardsvr | 2 (1) | yes | no | no | built-not-shipped-64 |  |
| schedsvc | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| scrobj | 5 (0) | yes | no | no | built-not-shipped-64 |  |
| security | 36 (0) | yes | no | no | built-not-shipped-64 |  |
| serialui | 7 (0) | yes | no | no | built-not-shipped-64 |  |
| sfc | 16 (0) | yes | no | no | built-not-shipped-64 |  |
| sfc_os | 18 (11) | yes | no | no | built-not-shipped-64 |  |
| shdoclc | 0 (0) | yes | no | no | built-not-shipped-64 |  |
| snmpapi | 45 (21) | yes | no | no | built-not-shipped-64 |  |
| softpub | 24 (3) | yes | no | no | built-not-shipped-64 |  |
| srclient | 13 (11) | yes | no | no | built-not-shipped-64 |  |
| srvsvc | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| sti | 7 (0) | yes | no | no | built-not-shipped-64 |  |
| tapi32 | 183 (2) | yes | no | no | built-not-shipped-64 |  |
| taskschd | 5 (1) | yes | no | no | built-not-shipped-64 |  |
| threadpoolwinrt | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| twain_32 | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| twaindsm | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| tzres | 0 (0) | yes | no | no | built-not-shipped-64 |  |
| uianimation | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| uiribbon | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| unicows | 507 (3) | yes | no | no | built-not-shipped-64 |  |
| updspapi | 78 (4) | yes | no | no | built-not-shipped-64 |  |
| vdmdbg | 19 (17) | yes | no | no | built-not-shipped-64 |  |
| webservices | 193 (50) | yes | no | no | built-not-shipped-64 |  |
| websocket | 13 (10) | yes | no | no | built-not-shipped-64 |  |
| wevtsvc | 1 (0) | yes | no | no | built-not-shipped-64 |  |
| winbrand | 15 (14) | yes | no | no | built-not-shipped-64 |  |
| windows.applicationmodel | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.devices.bluetooth | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.devices.enumeration | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.devices.usb | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.gaming.ui.gamebar | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.globalization | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.graphics | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.media | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.media.devices | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.media.mediacontrol | 11 (8) | yes | no | no | built-not-shipped-64 |  |
| windows.media.playback.backgroundmediaplayer | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.media.playback.mediaplayer | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.media.speech | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.networking | 8 (2) | yes | no | no | built-not-shipped-64 |  |
| windows.networking.connectivity | 10 (5) | yes | no | no | built-not-shipped-64 |  |
| windows.networking.hostname | 12 (6) | yes | no | no | built-not-shipped-64 |  |
| windows.perception.stub | 5 (2) | yes | no | no | built-not-shipped-64 |  |
| windows.security.authentication.onlineid | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.security.credentials.ui.userconsentverifier | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.storage.applicationdata | 4 (1) | yes | no | no | built-not-shipped-64 |  |
| windows.system.profile.systemid | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.system.profile.systemmanufacturers | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.ui.core.textinput | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| windows.ui.xaml | 15 (12) | yes | no | no | built-not-shipped-64 |  |
| windows.web | 6 (0) | yes | no | no | built-not-shipped-64 |  |
| windowscodecsext | 3 (0) | yes | no | no | built-not-shipped-64 |  |
| winedmo | 8 (0) | yes | no | no | built-not-shipped-64 |  |
| winemapi | 11 (0) | yes | no | no | built-not-shipped-64 |  |
| wineusb.sys | 0 (0) | yes | no | no | built-not-shipped-64 |  |
| wing32 | 10 (0) | yes | no | no | built-not-shipped-64 |  |
| winnls32 | 7 (5) | yes | no | no | built-not-shipped-64 |  |
| winprint | 7 (1) | yes | no | no | built-not-shipped-64 |  |
| wintypes | 11 (5) | yes | no | no | built-not-shipped-64 |  |
| wlanui | 6 (6) | yes | no | no | built-not-shipped-64 |  |
| wmi | 45 (0) | yes | no | no | built-not-shipped-64 |  |
| wmp | 8 (4) | yes | no | no | built-not-shipped-64 |  |
| wmphoto | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| wnaspi32 | 7 (1) | yes | no | no | built-not-shipped-64 |  |
| wofutil | 11 (9) | yes | no | no | built-not-shipped-64 |  |
| wpc | 5 (1) | yes | no | no | built-not-shipped-64 |  |
| wuapi | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| wuaueng | 15 (12) | yes | no | no | built-not-shipped-64 |  |
| xactengine2_0 | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| xactengine2_4 | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| xactengine2_7 | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| xactengine2_9 | 4 (0) | yes | no | no | built-not-shipped-64 |  |
| xinputuap | 8 (1) | yes | no | no | built-not-shipped-64 |  |
| xolehlp | 6 (3) | yes | no | no | built-not-shipped-64 |  |
| xpsprint | 5 (5) | yes | no | no | built-not-shipped-64 |  |
| xpssvcs | 9 (9) | yes | no | no | built-not-shipped-64 |  |

### stub-heavy
| Module | Exports (stub) | 32-bit farm | 64-bit ARM64EC | ARM64 | Class | Note |
| --- | --- | --- | --- | --- | --- | --- |
| adsldpc | 189 (189) | yes | no | no | stub-heavy |  |
| apphelp | 168 (155) | yes | no | no | stub-heavy |  |
| appxdeploymentclient | 82 (79) | yes | no | no | stub-heavy |  |
| atmlib | 76 (74) | yes | no | no | stub-heavy |  |
| authz | 42 (35) | yes | yes | yes | stub-heavy |  |
| bcp47langs | 71 (69) | yes | no | no | stub-heavy |  |
| bluetoothapis | 97 (73) | yes | yes | no | stub-heavy |  |
| bthprops.cpl | 64 (42) | yes | yes | no | stub-heavy |  |
| cfgmgr32 | 276 (183) | yes | yes | yes | stub-heavy |  |
| chakra | 160 (156) | yes | no | no | stub-heavy |  |
| cldapi | 51 (51) | yes | no | no | stub-heavy |  |
| clusapi | 115 (108) | yes | no | no | stub-heavy |  |
| coml2 | 33 (21) | yes | yes | yes | stub-heavy |  |
| comsvcs | 20 (16) | yes | no | no | stub-heavy |  |
| coremessaging | 31 (28) | yes | no | no | stub-heavy |  |
| cryptext | 30 (23) | yes | no | no | stub-heavy |  |
| cryptui | 48 (33) | yes | yes | yes | stub-heavy |  |
| d3d8thk | 56 (56) | yes | no | no | stub-heavy |  |
| davclnt | 23 (19) | yes | no | no | stub-heavy |  |
| dciman32 | 21 (17) | yes | no | no | stub-heavy |  |
| dcomp | 26 (22) | yes | yes | no | stub-heavy |  |
| dhcpcsvc | 45 (42) | yes | yes | yes | stub-heavy |  |
| dhcpcsvc6 | 22 (22) | yes | no | no | stub-heavy |  |
| dnsapi | 131 (94) | yes | yes | yes | stub-heavy |  |
| drmclien | 31 (28) | yes | no | no | stub-heavy |  |
| dwmapi | 85 (60) | yes | yes | yes | stub-heavy |  |
| dx8vb | 139 (135) | yes | no | no | stub-heavy |  |
| dxtrans | 23 (19) | yes | no | no | stub-heavy |  |
| esent | 336 (336) | yes | yes | yes | stub-heavy |  |
| feclient | 50 (50) | yes | no | no | stub-heavy |  |
| fltlib | 29 (24) | yes | no | no | stub-heavy |  |
| fwpuclnt | 146 (137) | yes | no | no | stub-heavy |  |
| gamingtcui | 26 (23) | yes | no | no | stub-heavy |  |
| gpkcsp | 26 (23) | yes | no | no | stub-heavy |  |
| inetcomm | 106 (87) | yes | no | no | stub-heavy |  |
| inetcpl.cpl | 25 (19) | yes | no | no | stub-heavy |  |
| irprops.cpl | 51 (33) | yes | no | no | stub-heavy |  |
| ktmw32 | 44 (41) | yes | yes | no | stub-heavy |  |
| localspl | 63 (61) | yes | no | no | stub-heavy |  |
| magnification | 21 (20) | yes | no | no | stub-heavy |  |
| mapi32 | 191 (96) | yes | no | no | stub-heavy |  |
| mapistub | 191 (100) | yes | no | no | stub-heavy |  |
| mf | 86 (52) | yes | yes | yes | stub-heavy |  |
| mmdevapi | 24 (15) | yes | yes | yes | stub-heavy |  |
| mprapi | 132 (129) | yes | no | no | stub-heavy |  |
| msasn1 | 266 (259) | yes | no | no | stub-heavy |  |
| mscorwks | 110 (97) | yes | no | no | stub-heavy |  |
| msctf | 34 (21) | yes | yes | no | stub-heavy |  |
| msdrm | 89 (85) | yes | no | no | stub-heavy |  |
| msls31 | 79 (79) | yes | no | no | stub-heavy |  |
| msnet32 | 96 (95) | yes | no | no | stub-heavy |  |
| mssign32 | 30 (22) | yes | no | no | stub-heavy |  |
| msvcm80 | 306 (296) | yes | yes | no | stub-heavy |  |
| msvcm90 | 181 (171) | yes | yes | no | stub-heavy |  |
| msvcp140_2 | 88 (84) | yes | yes | yes | stub-heavy |  |
| mswsock | 32 (26) | yes | yes | yes | stub-heavy |  |
| ncrypt | 138 (80) | yes | yes | yes | stub-heavy |  |
| netapi32 | 300 (220) | yes | yes | yes | stub-heavy |  |
| netutils | 22 (17) | yes | yes | yes | stub-heavy |  |
| ninput | 24 (17) | yes | no | no | stub-heavy |  |
| npptools | 60 (60) | yes | no | no | stub-heavy |  |
| nsi | 26 (15) | yes | yes | yes | stub-heavy |  |
| ntdsapi | 96 (86) | yes | no | no | stub-heavy |  |
| ntprint | 35 (32) | yes | no | no | stub-heavy |  |
| odbcbcp | 28 (28) | yes | no | no | stub-heavy |  |
| olecli32 | 56 (41) | yes | no | no | stub-heavy |  |
| pdh | 163 (115) | yes | yes | yes | stub-heavy |  |
| printui | 23 (21) | yes | no | no | stub-heavy |  |
| propsys | 208 (160) | yes | yes | yes | stub-heavy |  |
| query | 46 (38) | yes | no | no | stub-heavy |  |
| rasapi32 | 128 (68) | yes | no | no | stub-heavy |  |
| rasdlg | 36 (35) | yes | no | no | stub-heavy |  |
| regapi | 69 (69) | yes | no | no | stub-heavy |  |
| resutils | 71 (71) | yes | no | no | stub-heavy |  |
| rtutils | 56 (54) | yes | no | no | stub-heavy |  |
| samlib | 65 (65) | yes | no | no | stub-heavy |  |
| sccbase | 26 (24) | yes | no | no | stub-heavy |  |
| sechost | 208 (119) | yes | yes | yes | stub-heavy |  |
| shcore | 185 (112) | yes | yes | yes | stub-heavy |  |
| shdocvw | 128 (101) | yes | yes | no | stub-heavy |  |
| slbcsp | 27 (25) | yes | no | no | stub-heavy |  |
| slc | 41 (36) | yes | yes | no | stub-heavy |  |
| spoolss | 151 (131) | yes | no | no | stub-heavy |  |
| sppc | 67 (63) | yes | no | no | stub-heavy |  |
| srvcli | 61 (52) | yes | no | no | stub-heavy |  |
| svrapi | 20 (20) | yes | no | no | stub-heavy |  |
| t2embed | 25 (14) | yes | no | no | stub-heavy |  |
| tbs | 21 (18) | yes | no | no | stub-heavy |  |
| tdh | 34 (31) | yes | no | no | stub-heavy |  |
| traffic | 21 (19) | yes | no | no | stub-heavy |  |
| twinapi.appcore | 127 (120) | yes | no | no | stub-heavy |  |
| uiautomationcore | 98 (62) | yes | yes | yes | stub-heavy |  |
| url | 21 (13) | yes | no | no | stub-heavy |  |
| utildll | 36 (36) | yes | no | no | stub-heavy |  |
| vccorlib140 | 520 (321) | yes | yes | no | stub-heavy |  |
| virtdisk | 21 (18) | yes | no | no | stub-heavy |  |
| vssapi | 216 (197) | yes | no | no | stub-heavy |  |
| wdscore | 93 (93) | yes | no | no | stub-heavy |  |
| wer | 77 (68) | yes | yes | no | stub-heavy |  |
| wevtapi | 46 (31) | yes | yes | yes | stub-heavy |  |
| wiaservc | 57 (54) | yes | no | no | stub-heavy |  |
| wimgapi | 42 (37) | yes | no | no | stub-heavy |  |
| win32u | 1541 (1062) | yes | yes | yes | stub-heavy |  |
| winbio | 74 (74) | yes | no | no | stub-heavy |  |
| windows.storage | 323 (235) | yes | no | no | stub-heavy |  |
| winscard | 63 (32) | yes | no | no | stub-heavy |  |
| winsta | 95 (87) | yes | no | no | stub-heavy |  |
| winusb | 22 (21) | yes | yes | yes | stub-heavy |  |
| wlanapi | 39 (27) | yes | yes | yes | stub-heavy |  |
| wldp | 29 (26) | yes | no | no | stub-heavy |  |
| wmasf | 35 (35) | yes | yes | no | stub-heavy |  |
| wminet_utils | 65 (60) | yes | no | no | stub-heavy |  |
| wsdapi | 45 (31) | yes | no | no | stub-heavy |  |
| wsnmp32 | 48 (43) | yes | no | no | stub-heavy |  |

### backend-unavailable و opt-in-backend و replaced و source-not-built
| Module | Exports (stub) | 32-bit farm | 64-bit ARM64EC | ARM64 | Class | Note |
| --- | --- | --- | --- | --- | --- | --- |
| aero.msstyles | 0 (0) | no | no | no | source-not-built | 7.4 MiB of theme data nothing in the prefix selects |
| cng.sys | 63 (28) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| d3d10core | 2 (0) | no | yes | yes | replaced | DXMT (Metal) |
| d3d11 | 45 (33) | no | yes | yes | replaced | DXMT (Metal) |
| d3d12 | 11 (4) | yes | yes | yes | replaced | Madeira's D3D12 runtime (Metal) |
| d3d12core | 2 (2) | yes | no | no | backend-unavailable | vkd3d, needs Vulkan |
| d3d8 | 5 (0) | no | no | no | replaced | d3d8to9 over DXMT d3d9 (32-bit only) |
| d3d9 | 15 (3) | no | yes | yes | replaced | DXMT's Direct3D 9 (Metal) |
| d3dim | 13 (13) | yes | no | no | backend-unavailable | wined3d |
| d3dim700 | 18 (18) | yes | no | no | backend-unavailable | wined3d |
| d3drm | 23 (0) | yes | no | no | backend-unavailable | wined3d |
| ddraw | 28 (16) | yes | yes | no | backend-unavailable | wined3d; cnc-ddraw over DXMT d3d9 is opt-in for 32-bit games (MADEIRA_DDRAW=cnc) |
| ddrawex | 4 (0) | yes | no | no | backend-unavailable | wined3d |
| dxgi | 6 (0) | no | yes | yes | replaced | DXMT (Metal) |
| fltmgr.sys | 163 (153) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| glu32 | 53 (0) | yes | no | no | backend-unavailable | needs OpenGL |
| hal | 92 (71) | no | no | no | source-not-built | 16-bit layer; no 16-bit modules in a WoW64 tree |
| hidclass.sys | 1 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| hidparse.sys | 32 (10) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| http.sys | 0 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| ir50_32 | 1 (0) | no | no | no | source-not-built | needs GStreamer's codec; the winegstreamer unix side here is FFmpeg-based (docs/MEDIA.md) |
| ksecdd.sys | 104 (104) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| mouhid.sys | 0 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| mountmgr.sys | 0 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| mscoree | 118 (70) | yes | yes | no | opt-in-backend | needs the optional Wine Mono component (docs/WINE_MONO.md) |
| mshtml | 15 (5) | yes | no | yes | backend-unavailable | needs wine-gecko, not shipped |
| ndis.sys | 276 (271) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| netio.sys | 391 (387) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| nsiproxy.sys | 0 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| ntoskrnl.exe | 1715 (743) | no | no | yes | source-not-built | the driver world (see .sys) |
| opencl | 98 (0) | no | no | no | source-not-built | wrappers over host unix libraries that are not built for iOS |
| opengl32 | 361 (0) | yes | yes | yes | backend-unavailable | GL-absent stub table: no OpenGL on iOS |
| scsiport.sys | 48 (48) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| tdi.sys | 56 (56) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| usbd.sys | 35 (24) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| vga | 0 (0) | no | no | no | source-not-built | 16-bit layer; no 16-bit modules in a WoW64 tree |
| vulkan-1 | 262 (10) | no | no | no | backend-unavailable | no Vulkan |
| w32skrnl | 95 (91) | no | no | no | source-not-built | 16-bit layer; no 16-bit modules in a WoW64 tree |
| winealsa.drv | 0 (0) | yes | no | no | backend-unavailable | no ALSA (audio is wineios.drv) |
| wineandroid.drv | 0 (0) | yes | no | no | backend-unavailable | Android only |
| winebth.sys | 0 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| winebus.sys | 0 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| wineconsole.exe | 0 (0) | no | no | no | source-not-built | host console; conhost is the WoW64-side console |
| winecoreaudio.drv | 0 (0) | yes | no | no | backend-unavailable | macOS only (audio is wineios.drv) |
| wined3d | 371 (0) | yes | yes | yes | backend-unavailable | no backend (no GL / Vulkan) |
| winedbg.exe | 0 (0) | no | no | no | source-not-built | 4.5 MiB debugger only the (unshown) crash dialog spawns; dbghelp.dll ships |
| winedevice.exe | 0 (0) | no | no | no | source-not-built | the driver world (see .sys) |
| winegstreamer | 6 (0) | yes | yes | no | opt-in-backend | 64-bit unix side only with MADEIRA_WG_64BIT=1 (FFmpeg-based) |
| winehid.sys | 0 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| winemac.drv | 0 (0) | no | no | no | source-not-built | macOS display driver; the display goes through win32u + Winios |
| winemenubuilder.exe | 0 (0) | no | no | no | source-not-built | writes host desktop menu entries; there is no host desktop |
| winemetal | 0 (0) | no | yes | yes | replaced | DXMT (Metal) |
| wineoss.drv | 0 (0) | yes | no | no | backend-unavailable | no OSS |
| wineps.drv | 9 (0) | no | no | no | source-not-built | PostScript/CUPS; no CUPS unix side on iOS |
| winepulse.drv | 0 (0) | yes | no | no | backend-unavailable | no PulseAudio (audio is wineios.drv) |
| winevdm.exe | 0 (0) | no | no | no | source-not-built | 16-bit layer; no 16-bit modules in a WoW64 tree |
| winevulkan | 267 (10) | no | no | no | backend-unavailable | no Vulkan |
| winewayland.drv | 0 (0) | yes | no | no | backend-unavailable | no Wayland |
| winex11.drv | 0 (0) | yes | no | no | backend-unavailable | no X11 |
| winexinput.sys | 0 (0) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| wmilib.sys | 5 (5) | no | no | no | source-not-built | kernel drivers; under WoW64 drivers are 64-bit only |
| wow32 | 17 (0) | no | no | no | source-not-built | 16-bit layer; no 16-bit modules in a WoW64 tree |
| wow64 | 28 (19) | no | no | yes | source-not-built | 64-bit side of WoW64, never i386 |
| wow64cpu | 9 (0) | no | no | no | source-not-built | 64-bit side of WoW64, never i386 |
| wow64win | 1 (0) | no | no | yes | source-not-built | 64-bit side of WoW64, never i386 |
| wpcap | 111 (51) | no | no | no | source-not-built | wrappers over host unix libraries that are not built for iOS |
| xtajit | 0 (0) | no | no | yes | replaced | FEX (x86 emulation) |
| xtajit64 | 20 (0) | no | yes | no | replaced | FEX (x86 emulation) |

### ships
| Module | Exports (stub) | 32-bit farm | 64-bit ARM64EC | ARM64 | Class | Note |
| --- | --- | --- | --- | --- | --- | --- |
| advapi32 | 587 (42) | yes | yes | yes | ships |  |
| advpack | 81 (0) | yes | yes | no | ships |  |
| amstream | 4 (0) | yes | yes | no | ships |  |
| apisetschema | 0 (0) | yes | yes | yes | ships |  |
| atl | 52 (3) | yes | yes | no | ships |  |
| atl100 | 52 (7) | yes | yes | no | ships |  |
| atl110 | 52 (7) | yes | yes | no | ships |  |
| atl80 | 53 (8) | yes | yes | no | ships |  |
| atl90 | 52 (7) | yes | yes | no | ships |  |
| atlthunk | 4 (0) | yes | yes | no | ships |  |
| avicap32 | 4 (0) | yes | yes | no | ships |  |
| avifil32 | 79 (0) | yes | yes | no | ships |  |
| avrt | 14 (7) | yes | yes | yes | ships |  |
| bcrypt | 62 (23) | yes | yes | yes | ships |  |
| bcryptprimitives | 1 (0) | yes | yes | yes | ships |  |
| cabinet | 14 (2) | yes | yes | no | ships |  |
| colorcnv | 3 (0) | yes | yes | no | ships |  |
| combase | 355 (165) | yes | yes | yes | ships |  |
| comctl32 | 180 (4) | yes | yes | yes | ships |  |
| comctl32_v6 | 191 (4) | yes | yes | no | ships |  |
| comdlg32 | 28 (4) | yes | yes | yes | ships |  |
| compstui | 4 (0) | yes | yes | yes | ships |  |
| concrt140 | 727 (245) | yes | yes | yes | ships |  |
| credui | 21 (6) | yes | yes | yes | ships |  |
| crypt32 | 246 (35) | yes | yes | yes | ships |  |
| cryptbase | 11 (3) | yes | yes | yes | ships |  |
| cryptnet | 18 (12) | yes | yes | yes | ships |  |
| cryptsp | 65 (1) | yes | yes | no | ships |  |
| d2d1 | 12 (0) | yes | yes | no | ships |  |
| d3d10 | 29 (4) | yes | yes | no | ships |  |
| d3d10_1 | 30 (5) | yes | yes | no | ships |  |
| d3dcompiler_33 | 10 (2) | yes | yes | no | ships |  |
| d3dcompiler_34 | 10 (2) | yes | yes | no | ships |  |
| d3dcompiler_35 | 10 (2) | yes | yes | no | ships |  |
| d3dcompiler_36 | 10 (2) | yes | yes | no | ships |  |
| d3dcompiler_37 | 10 (2) | yes | yes | no | ships |  |
| d3dcompiler_38 | 11 (3) | yes | yes | no | ships |  |
| d3dcompiler_39 | 11 (3) | yes | yes | no | ships |  |
| d3dcompiler_40 | 12 (3) | yes | yes | no | ships |  |
| d3dcompiler_41 | 13 (3) | yes | yes | no | ships |  |
| d3dcompiler_42 | 13 (3) | yes | yes | no | ships |  |
| d3dcompiler_43 | 17 (5) | yes | yes | no | ships |  |
| d3dcompiler_46 | 25 (9) | yes | yes | no | ships |  |
| d3dcompiler_47 | 29 (10) | yes | yes | no | ships |  |
| d3dx10_33 | 177 (0) | yes | yes | no | ships |  |
| d3dx10_34 | 177 (0) | yes | yes | no | ships |  |
| d3dx10_35 | 180 (0) | yes | yes | no | ships |  |
| d3dx10_36 | 180 (0) | yes | yes | no | ships |  |
| d3dx10_37 | 181 (2) | yes | yes | no | ships |  |
| d3dx10_38 | 180 (1) | yes | yes | no | ships |  |
| d3dx10_39 | 180 (4) | yes | yes | no | ships |  |
| d3dx10_40 | 176 (0) | yes | yes | no | ships |  |
| d3dx10_41 | 176 (0) | yes | yes | no | ships |  |
| d3dx10_42 | 176 (0) | yes | yes | no | ships |  |
| d3dx10_43 | 176 (26) | yes | yes | no | ships |  |
| d3dx11_42 | 44 (19) | yes | yes | no | ships |  |
| d3dx11_43 | 44 (19) | yes | yes | no | ships |  |
| d3dx9_31 | 329 (67) | yes | yes | no | ships |  |
| d3dx9_32 | 334 (69) | yes | yes | no | ships |  |
| d3dx9_33 | 334 (69) | yes | yes | no | ships |  |
| d3dx9_34 | 334 (69) | yes | yes | no | ships |  |
| d3dx9_35 | 334 (69) | yes | yes | no | ships |  |
| d3dx9_36 | 336 (69) | yes | yes | no | ships |  |
| d3dx9_37 | 336 (69) | yes | yes | no | ships |  |
| d3dx9_38 | 336 (69) | yes | yes | no | ships |  |
| d3dx9_39 | 336 (69) | yes | yes | no | ships |  |
| d3dx9_40 | 336 (69) | yes | yes | no | ships |  |
| d3dx9_41 | 336 (69) | yes | yes | no | ships |  |
| d3dx9_42 | 329 (64) | yes | yes | no | ships |  |
| d3dx9_43 | 329 (64) | yes | yes | no | ships |  |
| d3dxof | 5 (0) | yes | yes | no | ships |  |
| dbghelp | 223 (60) | yes | yes | yes | ships |  |
| devenum | 4 (0) | yes | yes | no | ships |  |
| dinput | 7 (0) | yes | yes | yes | ships |  |
| dinput8 | 5 (0) | yes | yes | yes | ships |  |
| dplay | 2 (0) | yes | yes | no | ships |  |
| dplayx | 11 (0) | yes | yes | no | ships |  |
| dpnaddr | 1 (0) | yes | yes | no | ships |  |
| dpnet | 5 (0) | yes | yes | no | ships |  |
| dpnhpast | 5 (0) | yes | yes | no | ships |  |
| dpnhupnp | 5 (0) | yes | yes | no | ships |  |
| dpnlobby | 1 (0) | yes | yes | no | ships |  |
| dpvoice | 5 (0) | yes | yes | no | ships |  |
| dpwsockx | 3 (2) | yes | yes | no | ships |  |
| dsdmo | 4 (0) | yes | yes | no | ships |  |
| dsound | 14 (0) | yes | yes | yes | ships |  |
| dwrite | 1 (0) | yes | yes | yes | ships |  |
| dxdiagn | 4 (0) | yes | yes | no | ships |  |
| dxva2 | 38 (1) | yes | yes | no | ships |  |
| evr | 28 (9) | yes | yes | no | ships |  |
| faultrep | 14 (11) | yes | yes | no | ships |  |
| fontsub | 2 (1) | yes | yes | yes | ships |  |
| gameux | 5 (1) | yes | yes | no | ships |  |
| gdi32 | 545 (82) | yes | yes | yes | ships |  |
| gdiplus | 630 (12) | yes | yes | no | ships |  |
| hid | 44 (6) | yes | yes | yes | ships |  |
| hlink | 32 (12) | yes | yes | no | ships |  |
| hnetcfg | 4 (0) | yes | yes | no | ships |  |
| hrtfapo | 5 (4) | yes | yes | no | ships |  |
| iccvid | 1 (0) | yes | yes | no | ships |  |
| icmp | 8 (0) | yes | yes | no | ships |  |
| imaadp32.acm | 1 (0) | yes | yes | no | ships |  |
| imagehlp | 109 (10) | yes | yes | yes | ships |  |
| imm32 | 136 (43) | yes | yes | yes | ships |  |
| iphlpapi | 181 (61) | yes | yes | yes | ships |  |
| jscript | 4 (0) | yes | yes | yes | ships |  |
| jsproxy | 6 (3) | yes | yes | yes | ships |  |
| kernel32 | 1552 (119) | yes | yes | yes | ships |  |
| kernelbase | 1432 (70) | yes | yes | yes | ships |  |
| ksuser | 4 (4) | yes | yes | no | ships |  |
| l3codeca.acm | 1 (0) | yes | yes | no | ships |  |
| l3codecx.ax | 3 (0) | yes | yes | no | ships |  |
| mciavi32 | 1 (0) | yes | yes | no | ships |  |
| mcicda | 1 (0) | yes | yes | no | ships |  |
| mciqtz32 | 1 (0) | yes | yes | no | ships |  |
| mciseq | 1 (0) | yes | yes | no | ships |  |
| mciwave | 1 (0) | yes | yes | no | ships |  |
| mferror | 0 (0) | yes | yes | no | ships |  |
| mfmediaengine | 3 (1) | yes | yes | no | ships |  |
| mfplat | 183 (66) | yes | yes | yes | ships |  |
| mfplay | 6 (2) | yes | yes | no | ships |  |
| mfreadwrite | 9 (0) | yes | yes | yes | ships |  |
| midimap | 3 (1) | yes | yes | no | ships |  |
| mlang | 14 (1) | yes | yes | no | ships |  |
| mp3dmod | 5 (1) | yes | yes | no | ships |  |
| mpr | 111 (32) | yes | yes | yes | ships |  |
| msacm32 | 44 (1) | yes | yes | yes | ships |  |
| msacm32.drv | 3 (0) | yes | yes | no | ships |  |
| msadp32.acm | 1 (0) | yes | yes | no | ships |  |
| msauddecmft | 4 (0) | yes | yes | no | ships |  |
| mscms | 104 (41) | yes | yes | no | ships |  |
| msdmo | 15 (4) | yes | yes | yes | ships |  |
| msftedit | 14 (4) | yes | yes | no | ships |  |
| msg711.acm | 1 (0) | yes | yes | no | ships |  |
| msgsm32.acm | 1 (0) | yes | yes | no | ships |  |
| msi | 296 (29) | yes | yes | no | ships |  |
| msimg32 | 5 (0) | yes | yes | yes | ships |  |
| msmpeg2vdec | 14 (10) | yes | yes | no | ships |  |
| mspatcha | 16 (0) | yes | yes | no | ships |  |
| msrle32 | 1 (0) | yes | yes | no | ships |  |
| msvcirt | 1122 (1) | yes | yes | no | ships |  |
| msvcp100 | 3872 (286) | yes | yes | no | ships |  |
| msvcp110 | 3866 (437) | yes | yes | no | ships |  |
| msvcp120 | 3810 (402) | yes | yes | no | ships |  |
| msvcp140 | 3759 (371) | yes | yes | yes | ships |  |
| msvcp140_1 | 7 (0) | yes | yes | yes | ships |  |
| msvcp140_atomic_wait | 32 (11) | yes | yes | yes | ships |  |
| msvcp140_codecvt_ids | 4 (0) | yes | yes | yes | ships |  |
| msvcp60 | 5651 (1337) | yes | yes | no | ships |  |
| msvcp80 | 7857 (898) | yes | yes | no | ships |  |
| msvcp90 | 7881 (865) | yes | yes | no | ships |  |
| msvcp_win | 3760 (386) | yes | yes | no | ships |  |
| msvcr100 | 2062 (317) | yes | yes | no | ships |  |
| msvcr110 | 2249 (406) | yes | yes | no | ships |  |
| msvcr120 | 2493 (469) | yes | yes | yes | ships |  |
| msvcr80 | 1592 (158) | yes | yes | no | ships |  |
| msvcr90 | 1564 (146) | yes | yes | no | ships |  |
| msvcrt | 1403 (23) | yes | yes | yes | ships |  |
| msvfw32 | 47 (2) | yes | yes | no | ships |  |
| msvidc32 | 1 (0) | yes | yes | no | ships |  |
| msvproc | 4 (0) | yes | yes | no | ships |  |
| msxml | 4 (0) | yes | yes | no | ships |  |
| msxml2 | 4 (0) | yes | yes | no | ships |  |
| msxml3 | 11 (7) | yes | yes | no | ships |  |
| msxml4 | 4 (0) | yes | yes | no | ships |  |
| msxml6 | 4 (0) | yes | yes | no | ships |  |
| normaliz | 5 (0) | yes | yes | no | ships |  |
| ntdll | 1527 (101) | yes | yes | yes | ships |  |
| odbccp32 | 57 (5) | yes | yes | no | ships |  |
| ole32 | 302 (32) | yes | yes | yes | ships |  |
| oleacc | 22 (2) | yes | yes | yes | ships |  |
| oleaut32 | 418 (27) | yes | yes | yes | ships |  |
| oledlg | 23 (0) | yes | yes | no | ships |  |
| olepro32 | 11 (0) | yes | yes | no | ships |  |
| powrprof | 33 (0) | yes | yes | yes | ships |  |
| profapi | 17 (17) | yes | yes | no | ships |  |
| psapi | 27 (0) | yes | yes | yes | ships |  |
| qasf | 4 (0) | yes | yes | no | ships |  |
| qcap | 4 (0) | yes | yes | no | ships |  |
| qdvd | 4 (0) | yes | yes | no | ships |  |
| qedit | 4 (0) | yes | yes | no | ships |  |
| quartz | 9 (1) | yes | yes | no | ships |  |
| resampledmo | 4 (0) | yes | yes | no | ships |  |
| riched20 | 9 (0) | yes | yes | no | ships |  |
| riched32 | 1 (0) | yes | yes | no | ships |  |
| rpcrt4 | 532 (214) | yes | yes | yes | ships |  |
| rsaenh | 27 (0) | yes | yes | yes | ships |  |
| rtworkq | 37 (3) | yes | yes | yes | ships |  |
| schannel | 37 (9) | yes | yes | yes | ships |  |
| scrrun | 6 (1) | yes | yes | no | ships |  |
| secur32 | 80 (19) | yes | yes | yes | ships |  |
| sensapi | 3 (0) | yes | yes | no | ships |  |
| setupapi | 617 (244) | yes | yes | yes | ships |  |
| shell32 | 474 (70) | yes | yes | yes | ships |  |
| shfolder | 2 (0) | yes | yes | no | ships |  |
| shlwapi | 851 (125) | yes | yes | yes | ships |  |
| sspicli | 104 (47) | yes | yes | no | ships |  |
| stdole2.tlb | 0 (0) | yes | yes | no | ships |  |
| stdole32.tlb | 0 (0) | yes | yes | no | ships |  |
| strmdll | 5 (5) | yes | yes | no | ships |  |
| sxs | 4 (0) | yes | yes | no | ships |  |
| ucrtbase | 2603 (266) | yes | yes | yes | ships |  |
| urlmon | 110 (23) | yes | yes | yes | ships |  |
| user32 | 983 (202) | yes | yes | yes | ships |  |
| userenv | 23 (0) | yes | yes | yes | ships |  |
| usp10 | 44 (0) | yes | yes | no | ships |  |
| uxtheme | 119 (30) | yes | yes | yes | ships |  |
| vbscript | 4 (0) | yes | yes | yes | ships |  |
| vcomp | 112 (8) | yes | yes | no | ships |  |
| vcomp100 | 112 (8) | yes | yes | no | ships |  |
| vcomp110 | 113 (8) | yes | yes | no | ships |  |
| vcomp120 | 113 (8) | yes | yes | no | ships |  |
| vcomp140 | 113 (8) | yes | yes | no | ships |  |
| vcomp90 | 112 (8) | yes | yes | no | ships |  |
| vcruntime140 | 88 (17) | yes | yes | yes | ships |  |
| vcruntime140_1 | 3 (2) | yes | yes | no | ships |  |
| version | 16 (0) | yes | yes | yes | ships |  |
| vidreszr | 4 (0) | yes | yes | no | ships |  |
| wbemdisp | 4 (0) | yes | yes | no | ships |  |
| wbemprox | 4 (0) | yes | yes | no | ships |  |
| windows.gaming.input | 3 (0) | yes | yes | yes | ships |  |
| windows.ui | 7 (2) | yes | yes | yes | ships |  |
| windowscodecs | 118 (5) | yes | yes | yes | ships |  |
| winhttp | 49 (0) | yes | yes | yes | ships |  |
| wininet | 248 (42) | yes | yes | yes | ships |  |
| winmm | 189 (3) | yes | yes | yes | ships |  |
| winspool.drv | 188 (50) | yes | yes | no | ships |  |
| wintab32 | 44 (0) | yes | yes | no | ships |  |
| wintrust | 127 (38) | yes | yes | yes | ships |  |
| wldap32 | 245 (1) | yes | yes | no | ships |  |
| wmadmod | 4 (0) | yes | yes | no | ships |  |
| wmiutils | 4 (0) | yes | yes | no | ships |  |
| wmvcore | 20 (6) | yes | yes | no | ships |  |
| wmvdecod | 4 (0) | yes | yes | no | ships |  |
| ws2_32 | 135 (3) | yes | yes | yes | ships |  |
| wshom.ocx | 4 (0) | yes | yes | no | ships |  |
| wsock32 | 66 (0) | yes | yes | yes | ships |  |
| wtsapi32 | 52 (2) | yes | yes | yes | ships |  |
| x3daudio1_0 | 4 (0) | yes | yes | no | ships |  |
| x3daudio1_1 | 4 (0) | yes | yes | no | ships |  |
| x3daudio1_2 | 4 (0) | yes | yes | no | ships |  |
| x3daudio1_3 | 2 (0) | yes | yes | no | ships |  |
| x3daudio1_4 | 2 (0) | yes | yes | no | ships |  |
| x3daudio1_5 | 2 (0) | yes | yes | no | ships |  |
| x3daudio1_6 | 2 (0) | yes | yes | no | ships |  |
| x3daudio1_7 | 2 (0) | yes | yes | no | ships |  |
| xactengine3_0 | 4 (0) | yes | yes | no | ships |  |
| xactengine3_1 | 4 (0) | yes | yes | no | ships |  |
| xactengine3_2 | 4 (0) | yes | yes | no | ships |  |
| xactengine3_3 | 4 (0) | yes | yes | no | ships |  |
| xactengine3_4 | 4 (0) | yes | yes | no | ships |  |
| xactengine3_5 | 4 (0) | yes | yes | no | ships |  |
| xactengine3_6 | 4 (0) | yes | yes | no | ships |  |
| xactengine3_7 | 4 (0) | yes | yes | no | ships |  |
| xapofx1_1 | 1 (0) | yes | yes | no | ships |  |
| xapofx1_2 | 1 (0) | yes | yes | no | ships |  |
| xapofx1_3 | 1 (0) | yes | yes | no | ships |  |
| xapofx1_4 | 1 (0) | yes | yes | no | ships |  |
| xapofx1_5 | 1 (0) | yes | yes | no | ships |  |
| xaudio2_0 | 4 (0) | yes | yes | no | ships |  |
| xaudio2_1 | 4 (0) | yes | yes | no | ships |  |
| xaudio2_2 | 4 (0) | yes | yes | no | ships |  |
| xaudio2_3 | 4 (0) | yes | yes | no | ships |  |
| xaudio2_4 | 4 (0) | yes | yes | no | ships |  |
| xaudio2_5 | 4 (0) | yes | yes | no | ships |  |
| xaudio2_6 | 4 (0) | yes | yes | no | ships |  |
| xaudio2_7 | 4 (0) | yes | yes | no | ships |  |
| xaudio2_8 | 7 (0) | yes | yes | no | ships |  |
| xaudio2_9 | 7 (0) | yes | yes | no | ships |  |
| xinput1_1 | 5 (0) | yes | yes | no | ships |  |
| xinput1_2 | 5 (0) | yes | yes | no | ships |  |
| xinput1_3 | 9 (0) | yes | yes | no | ships |  |
| xinput1_4 | 10 (1) | yes | yes | no | ships |  |
| xinput9_1_0 | 4 (0) | yes | yes | no | ships |  |
| xmllite | 6 (0) | yes | yes | no | ships |  |
