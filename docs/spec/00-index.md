# ТЗ BF2 по частям — индекс

Источник: `docs/TZ/BF2-TZ.docx` (v1.0, 16.11.2026), разбит на Э0 командой
`pandoc docs/TZ/BF2-TZ.docx -t gfm --wrap=none` и скриптом `tools/spec/split_tz.py`.
Файлы spec не правятся вручную: при новой версии ТЗ — перегенерировать.
**Исключение до ТЗ v1.1:** [07-35](07-35-dorabotki-vladeltsa-produkta.md) и [07-35б](07-35b-dorabotki-po-testu-0-5-0.md) — доработки владельца продукта
(2026-11-17, Д-01…Д-20; 2026-11-18, Д-21…Д-25; 2026-11-19, Д-26; 2026-11-25, Д-27…Д-39) записаны в том виде, который даст `split_tz.py` из раздела «7.35» docx; внести раздел
в `BF2-TZ.docx` (7.35.7) и перегенерировать — файл перезапишется под тем же именем. Изменения принятого текста —
ADR 0008–0010 (проекты).

## Всегда (каждый этап)
`CLAUDE.md`, `PROGRESS.md`, [10](10-trebovaniya-k-kachestvu-i-proverke.md) (готовность 10.5), [17](17-obyazatelnye-pravila-i-izvestnye-grabli.md) (правила и грабли).

## Этап → файлы spec

| Этап | Содержание | Файлы spec (кроме «всегда») |
|---|---|---|
| Э0 | Подготовка, ADR, спайки, CI | 02, 05, 06, 11, 12–16, pril-a…pril-zh, 07-33 |
| Э1 | Каркас приложения, лента, дерево, реестр, окна, лицензия (пробный) | 07-00 (7.0), 07-01, 07-02, 06 (6.4, 6.9), 05 (5.6, 5.7) |
| Э2 | Проекты, демо, автокопии, `.bfggis`, задания | 07-03, 07-04, 07-05, 07-06, 06 (6.5, 6.7) |
| Э3 | Импорт DAT/CSV/Excel, мастер, журналы, СК | 07-07, 07-08, 07-09, 09 (9.1–9.2, 9.6, 9.10–9.12) |
| Э4 | Наборы скважин, таблицы, десуртинг, экспорт таблиц | 07-10, 07-11, 09 (9.2, 9.12) |
| Э5 | Аудит 27 правил, отчёт Excel | 07-12 |
| Э6 | 3D-окно, разрезы, стволы, отображения, каротаж | 07-13, 07-14, 07-15, 07-16, 07-17, 06 (6.8) |
| Э6.1 | Доработки интерфейса (начало В3): Д-03…Д-12, вкладка «Настройки проекта» | 07-35 (7.35.2, Д-03…Д-12), 07-00 (7.0.3, 7.0.4), 07-01, 07-06, 07-13, 07-20; ADR 0008 |
| Э7 | Линии, растры, ЦТМ; SHP, MIF/MID, канавы | 07-18, 07-19, 07-20, 09 (9.3, 9.5–9.7), 07-35 (Д-07, Д-13, Д-16); ADR 0009, 0011 |
| Э7.1 | Фильтр «земли»: LAS, шум, CSF, ЦТМ из облака точек (Д-22; между Э7 и Э8) | 07-20, 07-35 (Д-22); ADR 0011, 0012 |
| Э8 | Каркасы; DWG, Datamine (каркасы, стринги) | 07-21, 09 (9.4, 9.5, 9.12), 07-35 (Д-13); ADR 0009 |
| Э9 | Редактор стрингов, операции с линиями и каркасами | 07-22, 07-23 |
| Э10 | Серии разрезов | 08-00, 08-01 |
| Э11 | OMF v1; пакет экспорта, менеджер данных | 07-24, 09 (9.8), 07-04, 07-35 (Д-17, Д-20, Д-21); ADR 0010 |
| Э12 | Композитирование; рудные интервалы по ГКЗ | 08-00, 08-02, 07-35 (Д-26) |
| Э13 | Графики и статистика; swath в плавающем окне | 07-25 … 07-30, 08-03, 07-35 (Д-01); ADR 0008 |
| Э14 | QAQC | 08-00, 08-04 |
| Э15 | Истинная мощность | 08-00, 08-05 |
| Э16 | Вариография; эллипсоид как в Leapfrog, тренды | 08-00, 08-06, 07-29, 07-35 (Д-14, Д-15, Д-21) |
| Э17 | Блочные модели; библиотека фильтров, Datamine DM | 07-31, 08-00, 08-07, 07-35 (Д-02, Д-13, Д-27, Д-35); ADR 0023, 0025 |
| Э18 | Оценка содержаний (Э18б — SGS, SIS) | 08-00, 08-08, 07-35 (Д-21, Д-23, Д-27…Д-30, Д-38); ADR 0012, 0024, 0025 |
| Э19 | Неявное моделирование (RBF); структурные тренды | 08-00, 08-09, 07-35 (Д-15, Д-24, Д-25, Д-31…Д-34, Д-39); ADR 0012, 0025 |
| Э20 | Подсчёт ресурсов | 08-00, 08-10, 07-35 (Д-36, Д-37); ADR 0025 |
| Э21 | Оптимизация карьера (LG) | 08-00, 08-11, 07-35 (Д-21) |
| Э22 | Вывод и печать; листы в пакете экспорта | 08-00, 08-12, 07-35 (Д-17) |
| Э23 | Ассистент Claude | 07-32, 06 (6.10) |
| Э24 | Упаковка, обновление, лицензия | 05 (5.6, 5.7), 16 (16.5) |
| Э25 | Сервер ЛВС | 06 (6.11), 05 (5.6) |
| Э25а | BedrockExplorer 0.5.0 (после Э25, перед Э26) | 07-36, 06 (6.11), 07-03, 07-04, 07-06 |
| Э27 | Исправления 0.5.0 (часть А перечня 2026-11-28) → выпуск v0.5.1 | 07-35б (Д-40…Д-47), 09 (9.2), 07-07, 07-31, 07-22, 07-01; ADR 0030 |
| Э28 | Пачка «Дерево и лента» (Б3–Б12, Б23, Б24, Б26/38, Б34, Б41) | 07-35б (Д-50…Д-59, Д-70, Д-71, Д-73, Д-81, Д-88), 07-01, 07-13 |
| Э29 | Пачка «3D, разрезы, окна» (Б25, Б27, Б31, Б35, Б40) | 07-35б (Д-72, Д-74, Д-78, Д-82, Д-87), 07-13, 07-14, 07-15, 08-01 |
| Э30 | Пачка «Импорт скважин и хранение» (Б16, Б19, Б20, Б21, Б28, Б29) | 07-35б (Д-63, Д-66…Д-68, Д-75, Д-76), 07-07, 07-19, 07-21, 07-03; ADR 0031 |
| Э31 | Swath plot 2 (Б1) | 07-35б (Д-48), 07-30, 08-01; ADR 0032 |
| Э32 | Фильтры, тренды, вариография — доводка (Б2, Б14, Б15) | 07-35б (Д-49, Д-61, Д-62), 08-06, 08-07, 08-09; ADR 0033 |
| Э33 | Форматы импорта (Б13) | 07-35б (Д-60), 09; ADR 0034 |
| Э34 | Экспорт проекта и менеджер данных (Б17, Б18) | 07-35б (Д-64, Д-65), 07-04, 07-24; ADR 0035 |
| Э35 | Окна за пределами главного окна (Б37) | 07-35б (Д-84), 07-00 (7.0.12), 06; ADR 0036 |
| Э36 | Мастер оптимизации карьера (Б42) | 07-35б (Д-89), 08-11; ADR 0037 |
| Э37 | AI-провайдеры (Б36) | 07-35б (Д-83), 07-32; ADR 0038 |
| Э38 | Вкладка «Блочная модель» (Б39) | 07-35б (Д-86), 07-02, 07-31; ADR 0039 |
| Э26 | Финальная приёмка | 07-33, 07-34, 08-13, 11 |

Номер = префикс имени файла (`07-13` → `07-13-glavnoe-3d-okno.md`, `09` → `09-formaty-faylov.md`).

## Все файлы

| Файл | Раздел ТЗ |
|---|---|
| [00-preamble.md](00-preamble.md) | BedrockForge by Mikhail Bandurkin |
| [01-naznachenie-dokumenta-i-kak-im.md](01-naznachenie-dokumenta-i-kak-im.md) | 1. Назначение документа и как им пользоваться |
| [02-resheniya-zakazchika.md](02-resheniya-zakazchika.md) | 2. Решения заказчика |
| [03-itogi-analiza-bedrockforge.md](03-itogi-analiza-bedrockforge.md) | 3. Итоги анализа BedrockForge |
| [04-obshchie-svedeniya.md](04-obshchie-svedeniya.md) | 4. Общие сведения |
| [05-trebovaniya-k-sisteme-v-tselom.md](05-trebovaniya-k-sisteme-v-tselom.md) | 5. Требования к системе в целом |
| [06-arkhitektura.md](06-arkhitektura.md) | 6. Архитектура |
| [07-00-intro-funktsionalnye-trebovaniya-bazovye-moduli.md](07-00-intro-funktsionalnye-trebovaniya-bazovye-moduli.md) | 7. Функциональные требования — базовые модули |
| [07-01-derevo-dannykh.md](07-01-derevo-dannykh.md) | 7.1. Дерево данных [Э1] |
| [07-02-reestr-komand.md](07-02-reestr-komand.md) | 7.2. Реестр команд [Э1] |
| [07-03-proekty.md](07-03-proekty.md) | 7.3. Проекты [Э2] |
| [07-04-perenos-proekta.md](07-04-perenos-proekta.md) | 7.4. Перенос проекта (`.bfggis`) [Э2] |
| [07-05-demo-proekt.md](07-05-demo-proekt.md) | 7.5. Демо-проект [Э2] |
| [07-06-fonovye-zadaniya-khranilishche-i-ispolniteli.md](07-06-fonovye-zadaniya-khranilishche-i-ispolniteli.md) | 7.6. Фоновые задания, хранилище и исполнители [Э2] |
| [07-07-import-tablits-skvazhin.md](07-07-import-tablits-skvazhin.md) | 7.7. Импорт таблиц скважин (мастер) [Э3] |
| [07-08-import-obektov.md](07-08-import-obektov.md) | 7.8. Импорт объектов (мастер «Импорт файлов») [Э3] |
| [07-09-zhurnaly-importa-i-eksporta.md](07-09-zhurnaly-importa-i-eksporta.md) | 7.9. Журналы импорта и экспорта [Э3] |
| [07-10-nabory-skvazhin-i-tablitsy.md](07-10-nabory-skvazhin-i-tablitsy.md) | 7.10. Наборы скважин и таблицы [Э4] |
| [07-11-traektorii.md](07-11-traektorii.md) | 7.11. Траектории [Э4] |
| [07-12-audit-dannykh-skvazhin.md](07-12-audit-dannykh-skvazhin.md) | 7.12. Аудит данных скважин [Э5] |
| [07-13-glavnoe-3d-okno.md](07-13-glavnoe-3d-okno.md) | 7.13. Главное 3D-окно [Э6] |
| [07-14-razrezy.md](07-14-razrezy.md) | 7.14. Разрезы (отсечение) [Э6] |
| [07-15-sloi-ustya-i-stvoly.md](07-15-sloi-ustya-i-stvoly.md) | 7.15. Слои «Устья» и «Стволы» [Э6] |
| [07-16-otobrazheniya-dannykh-po-stvolu-i.md](07-16-otobrazheniya-dannykh-po-stvolu-i.md) | 7.16. Отображения данных по стволу и стили [Э6] |
| [07-17-okno-dannye-po-skvazhinam.md](07-17-okno-dannye-po-skvazhinam.md) | 7.17. Окно «Данные по скважинам» [Э6] |
| [07-18-linii.md](07-18-linii.md) | 7.18. Линии [Э7] |
| [07-19-rastry.md](07-19-rastry.md) | 7.19. Растры [Э7] |
| [07-20-tstm-tsifrovye-modeli-relefa.md](07-20-tstm-tsifrovye-modeli-relefa.md) | 7.20. ЦТМ — цифровые модели рельефа [Э7] |
| [07-21-karkasy.md](07-21-karkasy.md) | 7.21. Каркасы [Э8] |
| [07-22-redaktor-stringov.md](07-22-redaktor-stringov.md) | 7.22. Редактор стрингов [Э9] |
| [07-23-operatsii-s-liniyami-i-karkasami.md](07-23-operatsii-s-liniyami-i-karkasami.md) | 7.23. Операции с линиями и каркасами [Э9] |
| [07-24-omf.md](07-24-omf.md) | 7.24. OMF [Э11] |
| [07-25-gistogramma-prob.md](07-25-gistogramma-prob.md) | 7.25. Гистограмма проб [Э13] |
| [07-26-q-q-grafik.md](07-26-q-q-grafik.md) | 7.26. Q-Q график [Э13] |
| [07-27-dendrogramma.md](07-27-dendrogramma.md) | 7.27. Дендрограмма [Э13] |
| [07-28-sokhranennye-grafiki.md](07-28-sokhranennye-grafiki.md) | 7.28. Сохранённые графики [Э13] |
| [07-29-vkladka-statistika.md](07-29-vkladka-statistika.md) | 7.29. Вкладка «Статистика» [Э13, Э16] |
| [07-30-swath-plot.md](07-30-swath-plot.md) | 7.30. Swath plot [Э13] |
| [07-31-blochnye-modeli-bazovye-funktsii.md](07-31-blochnye-modeli-bazovye-funktsii.md) | 7.31. Блочные модели — базовые функции [Э17] |
| [07-32-assistent-claude.md](07-32-assistent-claude.md) | 7.32. Ассистент Claude [Э23] |
| [07-33-zamery-skorosti.md](07-33-zamery-skorosti.md) | 7.33. Замеры скорости [Э0, Э26] |
| [07-34-svodka-chto-bylo-ne-realizovano.md](07-34-svodka-chto-bylo-ne-realizovano.md) | 7.34. Сводка «Что было не реализовано в BF» |
| [07-35-dorabotki-vladeltsa-produkta.md](07-35-dorabotki-vladeltsa-produkta.md) | 7.35. Доработки владельца продукта [Э6.1, Э7, Э7.1, Э8, Э11, Э12, Э13, Э16, Э17, Э18, Э19, Э20, Э21, Э22] (до ТЗ v1.1) |
| [07-35b-dorabotki-po-testu-0-5-0.md](07-35b-dorabotki-po-testu-0-5-0.md) | 7.35б. Доработки владельца по тестированию 0.5.0 [Э27–Э38, Э24, Э25а] (до ТЗ v1.1) |
| [07-36-bedrockexplorer.md](07-36-bedrockexplorer.md) | 7.36. BedrockExplorer 0.5.0 [Э25а] |
| [08-00-intro-novye-moduli.md](08-00-intro-novye-moduli.md) | 8. Новые модули |
| [08-01-serii-razrezov.md](08-01-serii-razrezov.md) | 8.1. Серии разрезов [Э10] |
| [08-02-kompozitirovanie-i-podgotovka-prob.md](08-02-kompozitirovanie-i-podgotovka-prob.md) | 8.2. Композитирование и подготовка проб [Э12] |
| [08-03-dopolnitelnye-grafiki.md](08-03-dopolnitelnye-grafiki.md) | 8.3. Дополнительные графики [Э13, расширение] |
| [08-04-qaqc.md](08-04-qaqc.md) | 8.4. QAQC [Э14] |
| [08-05-istinnaya-moshchnost-i-rudnye-peresecheniya.md](08-05-istinnaya-moshchnost-i-rudnye-peresecheniya.md) | 8.5. Истинная мощность и рудные пересечения [Э15] |
| [08-06-variografiya.md](08-06-variografiya.md) | 8.6. Вариография [Э16] |
| [08-07-blochnye-modeli.md](08-07-blochnye-modeli.md) | 8.7. Блочные модели [Э17] |
| [08-08-otsenka-soderzhaniy.md](08-08-otsenka-soderzhaniy.md) | 8.8. Оценка содержаний [Э18] |
| [08-09-neyavnoe-modelirovanie.md](08-09-neyavnoe-modelirovanie.md) | 8.9. Неявное моделирование [Э19] |
| [08-10-podschet-resursov.md](08-10-podschet-resursov.md) | 8.10. Подсчёт ресурсов [Э20] |
| [08-11-optimizatsiya-karera.md](08-11-optimizatsiya-karera.md) | 8.11. Оптимизация карьера [Э21] |
| [08-12-vyvod-i-pechat.md](08-12-vyvod-i-pechat.md) | 8.12. Вывод и печать [Э22] |
| [08-13-pokrytie-checklist.md](08-13-pokrytie-checklist.md) | 8.13. Покрытие CHECKLIST |
| [09-formaty-faylov.md](09-formaty-faylov.md) | 9. Форматы файлов |
| [10-trebovaniya-k-kachestvu-i-proverke.md](10-trebovaniya-k-kachestvu-i-proverke.md) | 10. Требования к качеству и проверке |
| [11-etapy-vekhi-i-priemka.md](11-etapy-vekhi-i-priemka.md) | 11. Этапы, вехи и приёмка |
| [12-poryadok-raboty-i-roli.md](12-poryadok-raboty-i-roli.md) | 12. Порядок работы и роли |
| [13-modeli-i-uroven-usiliy.md](13-modeli-i-uroven-usiliy.md) | 13. Модели и уровень усилий |
| [14-ekonomiya-tokenov.md](14-ekonomiya-tokenov.md) | 14. Экономия токенов |
| [15-preryvaniya-i-vosstanovlenie.md](15-preryvaniya-i-vosstanovlenie.md) | 15. Прерывания и восстановление |
| [16-git-pr-i-ci.md](16-git-pr-i-ci.md) | 16. Git, PR и CI |
| [17-obyazatelnye-pravila-i-izvestnye-grabli.md](17-obyazatelnye-pravila-i-izvestnye-grabli.md) | 17. Обязательные правила и известные грабли |
| [pril-a-master-promt.md](pril-a-master-promt.md) | Приложение А. Мастер-промт (единый запрос) |
| [pril-b-promt-vozobnovleniya.md](pril-b-promt-vozobnovleniya.md) | Приложение Б. Промт возобновления |
| [pril-d-subagenty-nastroyki-i-khuki.md](pril-d-subagenty-nastroyki-i-khuki.md) | Приложение Д. Субагенты, настройки и хуки |
| [pril-e-karta-pereispolzovaniya-koda-bedrockforge.md](pril-e-karta-pereispolzovaniya-koda-bedrockforge.md) | Приложение Е. Карта переиспользования кода BedrockForge |
| [pril-g-shablon-progress-md.md](pril-g-shablon-progress-md.md) | Приложение Г. Шаблон PROGRESS.md |
| [pril-v-shablon-claude-md.md](pril-v-shablon-claude-md.md) | Приложение В. Шаблон CLAUDE.md |
| [pril-zh-shablony-otchetov.md](pril-zh-shablony-otchetov.md) | Приложение Ж. Шаблоны отчётов |
