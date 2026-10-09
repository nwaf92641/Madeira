# Winlator وMadeira: المقارنة بعد هذا الفرع

اعتمدنا على Winlator ‏(`brunodev85/winlator`، نسخة مرجعية للقراءة فقط في `/workspace/winlator-ref`) **كمرجع** لا كمصدر للنسخ،
فلم نأخذ منه ملفًا ولا كودًا. ‏Winlator يحل الفجوات غالبًا بنسخ ملفات Microsoft ‏(wincomponents) وبطبقات Vulkan، وكلاهما لا يناسب Madeira.
أما Madeira فيبني وحدات Wine الحقيقية أو مكتبات مفتوحة المصدر بتراخيص مسجلة في `THIRD-PARTY-NOTICES.md`.

التفاصيل الكاملة لكل وحدة في `WINLATOR_MADEIRA_GAP_MAP_AR.md` (الخريطة الأصلية)، وفي `WINE_COMPLETENESS_AUDIT.md` (التدقيق الآلي).

الحالات المستخدمة في الجداول:

- **مُختبر آليًا:** يعمل تحت Wine x86_64 على سطح المكتب بمزرعة مماثلة.
- **غير مُثبت على iPad:** لم يُجرَّب على جهاز؛ لا يوجد Xcode ولا iPad في هذه البيئة.

## الرسوميات

| ما يحتاجه اللاعب | Winlator | Madeira (هذا الفرع) | الحالة |
|---|---|---|---|
| ‏D3D9/10/11 | ‏DXVK فوق Vulkan، أو WineD3D | ‏DXMT فوق Metal، مع رقعة `dxmt-ios-layer-safety` | قائم من قبل، وأُضيفت الحماية والتشخيص |
| ‏D3D12 | ‏VKD3D | runtime ‏D3D12 الخاص بـ Madeira، مع تحويل الصيغة في `mad_swap_apply_layer` | قائم، والتحويل في هذا الفرع |
| ‏D3D8 | ‏D8VK (Vulkan) | ‏d3d8to9 فوق DXMT d3d9، لعمليات 32-bit | مُختبر آليًا، وغير مُثبت على iPad |
| ‏DirectDraw / D3D7 | ‏cnc-ddraw، وD7VK | ‏cnc-ddraw فوق DXMT d3d9، ويُفعَّل بـ `MADEIRA_DDRAW=cnc` | مُختبر آليًا، وغير مُثبت على iPad |
| ‏OpenGL | ‏zink/virgl/turnip | لا يوجد (iOS بلا OpenGL) | غير مناسب |
| ‏d3dx9/10/11، وd3dcompiler | ملفات Microsoft | وحدات Wine في المزرعتين | مُختبر آليًا |

## وقت التشغيل والمثبّتات

| ما يحتاجه اللاعب | Winlator | Madeira (هذا الفرع) | الحالة |
|---|---|---|---|
| ‏.NET (mscoree) | ‏wine-mono كـ addon | ‏Wine Mono 11.0.0 مكوّن اختياري يُنسخ إلى `Documents/Components`، و`WineMono.c` يربطه بالبادئة، والتشخيص يعطي تلميحًا عند غيابه | مُختبر آليًا (csc يعمل 64-bit)، وغير مُثبت تحت FEX |
| ‏VC++ 2005–2013 | ملفات Microsoft ‏(vcrun) | وحدات Wine ‏msvcr80..120/msvcp | مُختبر آليًا |
| ‏MSI ‏64-bit، وMSXML4، وWSH/JScript/VBScript، وScripting.FileSystemObject | ‏winetricks / ملفات Microsoft | وحدات Wine في مجموعة `installer_scripting` (جديدة) | مُختبر آليًا: ‏81/0/4 |
| ‏Common Controls 6 لـ 64-bit (WinSxS) | البادئة الجاهزة | ‏`WinSxS.c` يزرع 10/10 تجميعات من المزرعة | مُختبر آليًا |
| ‏WMI لـ 64-bit | البادئة | رابط `system32\wbem` | مُختبر آليًا |
| ‏wine-gecko (mshtml) | ‏addon | غير موجود | فجوة موثقة |

## الصوت والفيديو والإدخال

| ما يحتاجه اللاعب | Winlator | Madeira | الحالة |
|---|---|---|---|
| ‏XAudio2، وX3DAudio، وXACT3 | ملفات Microsoft | وحدات Wine | مُختبر آليًا |
| ‏DirectShow، وMedia Foundation، وWMV | ‏quartz/wmvcore وملفات Microsoft | وحدات Wine. فك الترميز لـ 64-bit يحتاج `MADEIRA_WG_64BIT=1` | جزئي، وغير مُثبت على iPad |
| ‏ALSA/Pulse | نعم | ‏wineios.drv فوق CoreAudio | غير مناسب، وله بديل |
| المتحكمات | ‏evdev، وواجهة Winlator | ‏GameController عبر XInput ‏(`WiniosGamepad.c`) | قائم |

## ما لم يُنقل عمدًا

- **DXVK، وVKD3D، وD8VK، وD7VK، وturnip، وzink، وvirgl، وvortek، وgladio:** كلها تحتاج Vulkan أو Android، والقيود تمنع جعل Vulkan شرطًا.
- **ملفات Microsoft** (vcrun، وd3dx، وxaudio، وwmdecoder، وdirectmusic): غير قابلة لإعادة التوزيع، والبديل وحدات Wine.
- **ملفات تعريف لكل لعبة على حدة:** ممنوعة بحسب القيود. التوافق يُحسب من imports كل لعبة عبر `GameCompat`.
