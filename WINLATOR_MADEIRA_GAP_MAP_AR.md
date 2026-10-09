# خريطة الفجوات بين Winlator وMadeira (طبقات توافق Wine/Windows)

المراجع: Winlator ‏`winlator-app` (commit ‏3981d86) و`winlator` (commit ‏b6b2259)، وMadeira على الفرع
`universal-game-compatibility`. يعمل Winlator بـ Wine 10.10 x86_64 فوق box64، ويعمل Madeira بـ Wine 11.4
(فرع `madeira-lgpl`) فوق FEX، والرسوميات فيه DXMT/Metal وmadeira_d3d12.

لكل مكوّن تصنيف واحد:

| التصنيف | المعنى |
|---|---|
| works | موجود ومُختبر في Madeira (اختبار آلي أو على الجهاز سابقًا) |
| partial | موجود لكن جزء منه ناقص |
| source-not-built | المصدر في شجرة Wine لكنه لم يكن يُبنى أو يُشحن لهذه المعمارية |
| missing-worth-port | غير موجود ويستحق النقل |
| unsuitable-for-iPadOS | لا يناسب iPadOS (Vulkan/Android/GPU drivers) أو مرخّص من Microsoft |
| unproven-needs-test | مشحون ومبني، لكن تشغيله على iPad لم يُثبت بعد |

المزرعة 64-bit (`app/Madeira/arm64ec-windows`) كانت 143 ملفًا، وأصبحت 328 (أُضيفت 185 وحدة Wine).

الأعمدة: "قبل" هو حال Madeira قبل هذا العمل، و"بعد" حاله بعده.

## 1. مكوّنات wincomponents في Winlator

يضع Winlator ملفات DLL من Microsoft (redistributables) ويبدّلها بين native وbuiltin.
لا يمكن توزيع ملفات Microsoft مع Madeira، لذلك جاء الرد في Madeira بوحدات Wine المدمجة (builtin).

| مكوّن Winlator | ما يضعه Winlator | قبل (64-bit) | بعد (64-bit) | 32-bit (WoW64) |
|---|---|---|---|---|
| direct3d | d3dx9_*, d3dx10_*, d3dx11_*, d3dcompiler_* | source-not-built | works (آليًا على Wine x86_64)، وunproven-needs-test على iPad | works (مزرعة i386 تبني الكل) |
| directsound | dsound, dsound3d | works (dsound موجود) | works | works |
| directmusic | dmusic, dmime, dmband, dmloader, ... (syswow64 فقط في Winlator) | لا يلزم: Winlator نفسه يضعه لـ 32-bit فقط | لم يُنقل عمدًا إلى 64-bit | works |
| directshow | quartz, amstream, qcap, qedit, devenum, l3codecx.ax | source-not-built (السجل يسجّل CLSIDs تشير إلى system32\quartz.dll غير الموجود) | works آليًا (FilterGraph, SystemDeviceEnum, SampleGrabber)، المُعرِضات (VMR-9/DSoundRender) unproven-needs-test | works |
| directplay | dplayx, dpnet, dpnaddr, dpwsockx, ... (syswow64 فقط) | source-not-built | works آليًا (DirectPlay8Peer) | works |
| xaudio | xaudio2_0..9, x3daudio, xapofx, xactengine3_* | source-not-built (كان فقط X3DAudio1_7 وXAPOFX1_5، بلا أي xaudio2_*.dll) | works آليًا (XAudio2Create 2.8/2.9، و2.7 عبر COM مع Initialize) | works |
| vcrun2005 | msvcr80, msvcp80, atl80, msvcm80 | source-not-built | works آليًا | works |
| vcrun2010 | msvcr100, msvcp100, vcomp100, atl100 | source-not-built | works آليًا (حتى 2012/2013 وvccorlib140) | works |
| wmdecoder | wmvcore, wmasf, wmadmod, wmvdecod (syswow64 فقط) | source-not-built | works آليًا (WMCreateSyncReader)؛ فك الترميز الفعلي يحتاج MADEIRA_WG_64BIT=1: unproven-needs-test | works |

## 2. طبقات الرسوميات في Winlator

| الطبقة | التصنيف | السبب |
|---|---|---|
| DXVK 1.10/2.4 (D3D9/10/11 → Vulkan) | unsuitable-for-iPadOS | يتطلب Vulkan؛ Madeira يستخدم DXMT فوق Metal، والقيود تمنع إضافة Vulkan |
| VKD3D (D3D12 → Vulkan) | unsuitable-for-iPadOS | Madeira لديه madeira_d3d12 فوق Metal |
| D8VK / D7VK (D3D8/D3D7 → Vulkan) | unsuitable-for-iPadOS | Vulkan. البديل المناسب ترجمة D3D8 إلى D3D9 فوق DXMT (انظر القسم 6) |
| cnc-ddraw 6.6 (DirectDraw → GDI/OpenGL) | missing-worth-port | الألعاب القديمة (ddraw) تبقى بلا مسار: Wine ddraw فوق wined3d بلا backend على iOS. نسخة GDI-only ممكنة لكنها لم تُنقل في هذه الجولة |
| WineD3D (installable_components) | unsuitable-for-iPadOS كـ backend | يحتاج OpenGL أو Vulkan؛ لا يوجد أيٌّ منهما على iOS |
| turnip, zink, virgl, vortek, gladio | unsuitable-for-iPadOS | تعريفات GPU خاصة بـ Android/Adreno |
| واجهات D3D10/D3D10.1 (d3d10, d3d10_1) وd2d1 وdxdiagn | قبل: source-not-built. بعد: works آليًا للتحميل والتصدير وD2D1CreateFactory وDxDiagProvider؛ إنشاء جهاز D3D10 فوق DXMT d3d10core: unproven-needs-test | |
| ddraw (64-bit) | unproven-needs-test | شُحن لأن avicap32/الكودكات تستورده؛ لا يوجد backend للرسم به |
| d3d8 | missing-worth-port | لا d3d8 بـ backend في أيٍّ من المزرعتين |
| OpenGL للألعاب | unsuitable-for-iPadOS | opengl32 على iOS stub |

## 3. وقت التشغيل والبادئة (prefix)

| المكوّن | التصنيف | الملاحظة |
|---|---|---|
| wine-mono (.NET) | missing-worth-port | Winlator يضعه كـ addon. mscoree موجود في المزرعة لكن بلا Mono؛ يحتاج بناء Mono لـ iOS أو FEX، وهو كبير |
| wine-gecko (mshtml) | missing-worth-port (أولوية منخفضة) | mshtml وieframe غير مشحونين؛ تستخدمه المشغّلات (launchers) أحيانًا |
| winsxs لـ 64-bit (amd64_ VC80/VC90، comctl32 v6) | missing-worth-port | البادئة بلا winsxs؛ غياب assembly غير قاتل والاستيراد يرجع إلى system32، والتشخيص الآن يصنّف "Could not find dependent assembly" |
| WMI لـ 64-bit | قبل: source-not-built (لا wbemprox.dll في مزرعة 64-bit ولا رابط system32\wbem، مع أن السجل يشير إلى system32\wbem\wbemprox.dll). بعد: works آليًا (ConnectServer وExecQuery Win32_VideoController)، والرابط على الجهاز unproven-needs-test |
| تسجيل COM في القالب | partial | 614 فئة 64-bit مسجلة؛ كانت 458 منها بلا DLL، وأصبحت 259 (معظم الباقي mshtml/ieframe/wmp/برامج النظام) |
| برامج Wine 64-bit (rundll32, regsvr32, msiexec, dpnsvr) | missing-worth-port | يحتاجها بعض المثبّتات |
| أهداف delay-import ناقصة في المزرعة (mlang, cryptsp, advpack, shdocvw, bluetoothapis) | قبل: missing (خلل كامن). بعد: works | غياب هدف delay-load لا يظهر عند التحميل، بل كتعطل "unimplemented function" عند أول استدعاء؛ ظهر هذا فعلًا في الاختبار مع wmvcore → winegstreamer |
| Media Foundation sources (mfsrcsnk, mfasfsrcsnk, mfmp4srcsnk) | source-not-built | تعتمد في Wine 11 على winedmo، ولا يوجد له جانب unix على iOS. FFmpeg مبني أصلًا، لذا نقله ممكن |
| winegstreamer جانب unix لـ 64-bit | unproven-needs-test | موجود خلف MADEIRA_WG_64BIT=1 ولم يُغيّر |
| قائمة compat/wine-modules.json | partial (خلل مثبت) | تعامل كل الوحدات الـ 648 كأنها مشحونة لأي معمارية، مع أن 341 منها غير موجودة في مزرعة 64-bit |

## 4. الصوت والإدخال

| المكوّن | التصنيف | الملاحظة |
|---|---|---|
| ALSA/PulseAudio في Winlator | unsuitable-for-iPadOS | Madeira يستخدم مشغّل صوت خاصًا بـ CoreAudio (unixlib "audio") |
| SoundFont لـ MIDI (SONiVOX) | missing-worth-port (أولوية منخفضة) | midimap مشحون الآن لـ 64-bit، لكن لا synth MIDI مضمّن |
| ACM codecs (IMA ADPCM, MS ADPCM, GSM, G.711, MP3) | قبل: source-not-built لـ 64-bit. بعد: works آليًا (acmFormatSuggest IMA ADPCM→PCM) | |
| VfW (avifil32, msvfw32, iccvid, msrle32) | قبل: source-not-built. بعد: works آليًا (AVIFileInit، ICOpen Cinepak) | |
| Input controls (Winlator) | لا يخص طبقات Wine | Madeira لديه طبقة إدخال خاصة به |
