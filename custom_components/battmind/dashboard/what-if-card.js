const whatIfSessions = new Map();
let whatIfGradientId = 0;

const whatIfIntervalHeaders = {
  da: ["Gevinstændring fra forrige størrelse", "Gevinstændring pr. ekstra kWh"],
  de: ["Gewinnänderung gegenüber der vorherigen Größe", "Gewinnänderung je zusätzlicher kWh"],
  en: ["Profit change from previous size", "Profit change per added kWh"],
  pl: ["Zmiana zysku względem poprzedniej pojemności", "Zmiana zysku na dodatkową kWh"],
  uk: ["Зміна прибутку від попередньої ємності", "Зміна прибутку на додаткову кВт·год"],
};
const whatIfInterpolationLabels = {
  da: ["kurveestimat", "Mellem punkterne vises et kurveestimat, ikke en ny simulering eller en prognose. Musemarkøren viser hele kWh, mens simulerede decimalstørrelser vises præcist."],
  de: ["Kurvenschätzung", "Werte zwischen den Punkten sind mit einer Kurve geschätzt, keine neue Simulation oder Prognose. Zeigerschätzungen verwenden ganze kWh; simulierte gebrochene Kapazitäten werden genau angezeigt."],
  en: ["curve estimate", "Values between points are estimated with a curve, not a new simulation or forecast. Pointer estimates use whole kWh; simulated fractional capacities are shown accurately."],
  pl: ["oszacowanie krzywej", "Wartości między punktami są szacowane za pomocą krzywej, a nie symulowane ani prognozowane. Wskaźnik pokazuje pełne kWh; symulowane pojemności ułamkowe są wyświetlane dokładnie."],
  uk: ["оцінка за кривою", "Значення між точками оцінені за кривою, а не отримані новою симуляцією чи прогнозом. Вказівник показує цілі кВт·год; симульовані дробові ємності показано точно."],
};
const whatIfIntervalTitles = {
  da: "Gevinstændring pr. ekstra kWh batterikapacitet",
  de: "Gewinnänderung je zusätzlicher kWh Batteriekapazität",
  en: "Profit change per added kWh of battery capacity",
  pl: "Zmiana zysku na dodatkową kWh pojemności baterii",
  uk: "Зміна прибутку на додаткову кВт·год ємності батареї",
};
const whatIfIntervalCurveNotes = {
  da: "Kurven er en tendens mellem intervallernes gennemsnit, ikke en beregnet gevinst ved en bestemt batteristørrelse. Værdier mellem punkterne er estimater.",
  de: "Die Kurve zeigt einen Trend zwischen den Intervallmittelwerten, keinen berechneten Gewinn bei einer bestimmten Batteriekapazität. Werte zwischen den Punkten sind Schätzungen.",
  en: "The curve shows a trend between interval averages, not a calculated profit at a particular battery size. Values between points are estimates.",
  pl: "Krzywa pokazuje trend między średnimi dla zakresów, a nie obliczony zysk dla konkretnej pojemności baterii. Wartości między punktami są oszacowaniami.",
  uk: "Крива показує тенденцію між середніми значеннями інтервалів, а не розрахований прибуток для конкретної ємності батареї. Значення між точками є оцінками.",
};
const whatIfGradientLegends = {
  da: "Farven viser den gennemsnitlige gevinstændring pr. ekstra kWh i hvert interval: grøn er højere, rød er lavere. Grøn kan også være negativ.",
  de: "Die Farbe zeigt die durchschnittliche Gewinnänderung je zusätzlicher kWh in jedem Intervall: Grün ist höher, Rot niedriger. Auch Grün kann negativ sein.",
  en: "Color shows the average profit change per added kWh in each interval: green is higher, red is lower. Green can also be negative.",
  pl: "Kolor pokazuje średnią zmianę zysku na dodatkową kWh w każdym przedziale: zielony oznacza wyższą, czerwony niższą. Zielony może również oznaczać wartość ujemną.",
  uk: "Колір показує середню зміну прибутку на додаткову кВт·год у кожному інтервалі: зелений означає вищу, червоний — нижчу. Зелене значення також може бути від’ємним.",
};
const whatIfIntervalGradientLegends = {
  da: "Grøn: højere gevinst pr. ekstra kWh. Rød: lavere gevinst pr. ekstra kWh blandt de viste intervaller. Grøn kan også være negativ.",
  de: "Grün: höherer Gewinn je zusätzlicher kWh. Rot: niedrigerer Gewinn je zusätzlicher kWh unter den angezeigten Intervallen. Auch Grün kann negativ sein.",
  en: "Green: higher profit per added kWh. Red: lower profit per added kWh among the displayed intervals. Green can also be negative.",
  pl: "Zielony: wyższy zysk na dodatkową kWh. Czerwony: niższy zysk na dodatkową kWh w pokazanych zakresach. Zielony może również oznaczać wartość ujemną.",
  uk: "Зелений: вищий прибуток на додаткову кВт·год. Червоний: нижчий прибуток на додаткову кВт·год серед показаних інтервалів. Зелене значення також може бути від’ємним.",
};
const whatIfModuleLabels = {
  "da": {
    "module_compare": "Sammenlign batterimoduler",
    "module_capacity": "Modulkapacitet (kWh)",
    "module_price": "Pris pr. modul",
    "module_count": "Ekstra moduler",
    "module_section": "Modulsammenligning",
    "module_baseline_note": "Grundlinjen med nul moduler simuleres ved den nuværende kapacitet med samme forudsætninger som hver sag med ekstra moduler.",
    "module_history": "Matchede historiske timer",
    "module_months": "repræsenterede måneder",
    "module_insufficient_history": "Årlig beregning kræver en periode på 365 dage og mindst 90 % matchede timer i hver lokal måned eller del af måned. Manglende timer udfyldes ikke.",
    "module_coverage_requirement": "Årlig beregning kræver en periode på 365 dage og mindst 90 % matchede timer i hver lokal måned eller del af måned. Manglende timer udfyldes ikke.",
    "module_missing_sell_price": "Historiske salgspriser mangler. Gevinst er usikker, og tilbagebetaling kan ikke beregnes.",
    "module_missing_sell_cost": "Historiske salgsomkostninger mangler. Gevinst er usikker, og tilbagebetaling kan ikke beregnes.",
    "module_rerun": "Dette gemte resultat er fra en ældre version. Kør simulationen igen for at vurdere tilbagebetaling.",
    "module_dst_warning": "Skift til eller fra sommertid kan have nulstillet batteriniveauet i gennemspilningen; antal nulstillinger",
    "module_tariff_warning": "Timer med estimerede historiske tariffer",
    "module_current_cost_warning": "Timer med nuværende salgsomkostninger brugt som historisk estimat",
    "module_added": "Tilføjede moduler",
    "module_profit": "Historisk gevinst",
    "module_gain_baseline": "Gevinst i forhold til nuværende kapacitet",
    "module_incremental_gain": "Historisk gevinst fra forrige størrelse",
    "module_annualized_gain": "Årliggjort historisk gevinst",
    "module_cumulative_cost": "Samlet modulpris",
    "module_payback": "Enkel tilbagebetalingstid",
    "module_no_payback": "Ingen tilbagebetaling ved ikke-positiv gevinst",
    "module_years": "år",
    "module_note": "Dette er en historisk gennemspilning, ikke en prognose eller garanti. Gennemspilningen bruger registrerede priser i planlægningsvinduerne; virkelige prognoser og fremtidige resultater kan afvige. Årliggjorte gevinster skaleres kun ud fra faktisk matchede timer og vises kun, når dækningskravet ovenfor er opfyldt. Enkel tilbagebetaling er modulprisen divideret med den positive årliggjorte mergevinst. Modulpriser vises separat og trækkes ikke fra den simulerede gevinst; de omfatter ikke installation, inverter, kabling, finansiering, vedligeholdelse, skatter eller fremtidig forringelse."
  },
  "de": {
    "module_compare": "Batteriemodule vergleichen",
    "module_capacity": "Modulkapazität (kWh)",
    "module_price": "Preis je Modul",
    "module_count": "Zusätzliche Module",
    "module_section": "Modulvergleich",
    "module_baseline_note": "Die Vergleichsbasis ohne zusätzliche Module wird mit der aktuellen Kapazität und denselben Annahmen wie jeder Modulfall simuliert.",
    "module_history": "Abgeglichene historische Stunden",
    "module_months": "abgedeckte Monate",
    "module_insufficient_history": "Die Annualisierung erfordert einen Zeitraum von 365 Tagen und mindestens 90 % zugeordnete Stunden in jedem lokalen Monat oder Monatsteil. Fehlende Stunden werden nicht ergänzt.",
    "module_coverage_requirement": "Die Annualisierung erfordert einen Zeitraum von 365 Tagen und mindestens 90 % zugeordnete Stunden in jedem lokalen Monat oder Monatsteil. Fehlende Stunden werden nicht ergänzt.",
    "module_missing_sell_price": "Historische Verkaufspreise fehlen. Der Gewinn ist unsicher und die Amortisation kann nicht berechnet werden.",
    "module_missing_sell_cost": "Historische Verkaufskosten fehlen. Der Gewinn ist unsicher und die Amortisation kann nicht berechnet werden.",
    "module_rerun": "Dieses gespeicherte Ergebnis stammt aus einer älteren Version. Simulation zur Beurteilung der Amortisation erneut ausführen.",
    "module_dst_warning": "Zeitumstellungen können den Batteriestand im Rücklauf zurückgesetzt haben; Anzahl der Rücksetzungen",
    "module_tariff_warning": "Stunden mit geschätzten historischen Tarifen",
    "module_current_cost_warning": "Stunden mit aktuellen Verkaufskosten als historische Schätzung",
    "module_added": "Zusätzliche Module",
    "module_profit": "Historischer Gewinn",
    "module_gain_baseline": "Gewinn gegenüber aktueller Kapazität",
    "module_incremental_gain": "Historischer Gewinn gegenüber der vorherigen Größe",
    "module_annualized_gain": "Annualisierter historischer Gewinn",
    "module_cumulative_cost": "Kumulierte Modulkosten",
    "module_payback": "Einfache Amortisationszeit",
    "module_no_payback": "Keine Amortisation bei nicht positivem Gewinn",
    "module_years": "Jahre",
    "module_note": "Dies ist eine historische Simulation, keine Prognose oder Garantie. Die Simulation nutzt aufgezeichnete Preise in den Planungsfenstern; reale Prognosen und zukünftige Ergebnisse können abweichen. Annualisierte Gewinne werden nur anhand tatsächlich abgeglichener Stunden hochgerechnet und nur bei erfüllter Abdeckung angezeigt. Die einfache Amortisation ist der Modulpreis geteilt durch den positiven annualisierten Mehrgewinn. Modulkosten werden separat angezeigt und nicht vom simulierten Gewinn abgezogen; Installation, Wechselrichter, Verkabelung, Finanzierung, Wartung, Steuern und zukünftige Alterung sind nicht enthalten."
  },
  "en": {
    "module_compare": "Compare battery modules",
    "module_capacity": "Module capacity (kWh)",
    "module_price": "Price per module",
    "module_count": "Additional modules",
    "module_section": "Module comparison",
    "module_baseline_note": "The zero-module baseline is simulated at current capacity using the same assumptions as each added-module case.",
    "module_history": "Matched historical hours",
    "module_months": "months represented",
    "module_insufficient_history": "Annualization requires a 365-day period and at least 90% matched hours in each local month or partial month. Missing hours are not filled in.",
    "module_coverage_requirement": "Annualization requires a 365-day period and at least 90% matched hours in each local month or partial month. Missing hours are not filled in.",
    "module_missing_sell_price": "Historical sell prices are missing. Profit is uncertain and payback cannot be calculated.",
    "module_missing_sell_cost": "Historical selling costs are missing. Profit is uncertain and payback cannot be calculated.",
    "module_rerun": "This saved result is from an older version. Run the simulation again to assess payback.",
    "module_dst_warning": "Daylight-saving transitions may have reset the battery level during replay; number of resets",
    "module_tariff_warning": "Hours with estimated historical tariffs",
    "module_current_cost_warning": "Hours using current selling costs as a historical estimate",
    "module_added": "Modules added",
    "module_profit": "Historical profit",
    "module_gain_baseline": "Gain vs current capacity",
    "module_incremental_gain": "Historical gain from previous size",
    "module_annualized_gain": "Annualized historical gain",
    "module_cumulative_cost": "Cumulative module cost",
    "module_payback": "Simple payback",
    "module_no_payback": "No payback for non-positive gain",
    "module_years": "years",
    "module_note": "This is a historical replay, not a forecast or promise. The replay uses recorded prices in its planning windows; real forecasts and future results may differ. Annualized gains scale only actually matched hours and appear only when the coverage requirement above is met. Simple payback is module price divided by positive annualized incremental gain. Module costs are shown separately, not subtracted from simulated profit, and exclude installation, inverter, wiring, financing, maintenance, taxes, and future degradation."
  },
  "pl": {
    "module_compare": "Porównaj moduły baterii",
    "module_capacity": "Pojemność modułu (kWh)",
    "module_price": "Cena modułu",
    "module_count": "Dodatkowe moduły",
    "module_section": "Porównanie modułów",
    "module_baseline_note": "Wariant bazowy bez dodatkowych modułów jest symulowany dla obecnej pojemności przy tych samych założeniach co każdy wariant z modułami.",
    "module_history": "Dopasowane historyczne godziny",
    "module_months": "reprezentowane miesiące",
    "module_insufficient_history": "Annualizacja wymaga okresu 365 dni i co najmniej 90% dopasowanych godzin w każdym lokalnym miesiącu lub jego części. Brakujące godziny nie są uzupełniane.",
    "module_coverage_requirement": "Annualizacja wymaga okresu 365 dni i co najmniej 90% dopasowanych godzin w każdym lokalnym miesiącu lub jego części. Brakujące godziny nie są uzupełniane.",
    "module_missing_sell_price": "Brakuje historycznych cen sprzedaży. Zysk jest niepewny i nie można obliczyć okresu zwrotu.",
    "module_missing_sell_cost": "Brakuje historycznych kosztów sprzedaży. Zysk jest niepewny i nie można obliczyć okresu zwrotu.",
    "module_rerun": "Ten zapisany wynik pochodzi ze starszej wersji. Uruchom symulację ponownie, aby ocenić okres zwrotu.",
    "module_dst_warning": "Zmiany czasu mogły zresetować poziom baterii podczas odtwarzania; liczba resetów",
    "module_tariff_warning": "Godziny z szacowanymi historycznymi taryfami",
    "module_current_cost_warning": "Godziny z bieżącymi kosztami sprzedaży jako szacunkiem historycznym",
    "module_added": "Dodane moduły",
    "module_profit": "Historyczny zysk",
    "module_gain_baseline": "Zysk względem obecnej pojemności",
    "module_incremental_gain": "Historyczny zysk względem poprzedniej wielkości",
    "module_annualized_gain": "Annualizowany zysk historyczny",
    "module_cumulative_cost": "Łączny koszt modułów",
    "module_payback": "Prosty okres zwrotu",
    "module_no_payback": "Brak zwrotu przy zysku nie większym od zera",
    "module_years": "lata",
    "module_note": "To historyczna symulacja, a nie prognoza ani gwarancja. Symulacja wykorzystuje zapisane ceny w oknach planowania; rzeczywiste prognozy i przyszłe wyniki mogą się różnić. Zyski annualizowane są przeliczane wyłącznie na podstawie faktycznie dopasowanych godzin i pokazywane tylko po spełnieniu powyższego wymogu pokrycia. Prosty okres zwrotu to cena modułu podzielona przez dodatni annualizowany przyrost zysku. Koszty modułów są wyświetlane osobno, nie odejmuje się ich od symulowanego zysku; nie obejmują montażu, falownika, okablowania, finansowania, konserwacji, podatków ani przyszłej degradacji."
  },
  "uk": {
    "module_compare": "Порівняти модулі батареї",
    "module_capacity": "Ємність модуля (кВт·год)",
    "module_price": "Ціна за модуль",
    "module_count": "Додаткові модулі",
    "module_section": "Порівняння модулів",
    "module_baseline_note": "Базовий варіант без додаткових модулів симулюється для поточної ємності з тими самими припущеннями, що й кожен варіант із модулями.",
    "module_history": "Зіставлені історичні години",
    "module_months": "представлені місяці",
    "module_insufficient_history": "Річний перерахунок потребує періоду 365 днів та щонайменше 90% зіставлених годин у кожному місцевому місяці або його частині. Пропущені години не заповнюються.",
    "module_coverage_requirement": "Річний перерахунок потребує періоду 365 днів та щонайменше 90% зіставлених годин у кожному місцевому місяці або його частині. Пропущені години не заповнюються.",
    "module_missing_sell_price": "Історичні ціни продажу відсутні. Прибуток невизначений, а окупність неможливо обчислити.",
    "module_missing_sell_cost": "Історичні витрати на продаж відсутні. Прибуток невизначений, а окупність неможливо обчислити.",
    "module_rerun": "Цей збережений результат отримано старішою версією. Запустіть симуляцію знову для оцінки окупності.",
    "module_dst_warning": "Перехід на літній або зимовий час міг скинути рівень заряду під час відтворення; кількість скидань",
    "module_tariff_warning": "Години з оціненими історичними тарифами",
    "module_current_cost_warning": "Години з поточними витратами на продаж як історичною оцінкою",
    "module_added": "Додані модулі",
    "module_profit": "Історичний прибуток",
    "module_gain_baseline": "Прибуток відносно поточної ємності",
    "module_incremental_gain": "Історичний прибуток відносно попереднього розміру",
    "module_annualized_gain": "Річний еквівалент історичного прибутку",
    "module_cumulative_cost": "Сукупна вартість модулів",
    "module_payback": "Простий строк окупності",
    "module_no_payback": "Немає окупності за непозитивного прибутку",
    "module_years": "років",
    "module_note": "Це історичне відтворення, а не прогноз чи гарантія. Відтворення використовує записані ціни у вікнах планування; реальні прогнози та майбутні результати можуть відрізнятися. Річний еквівалент розраховується лише за фактично зіставленими годинами й показується лише за виконання вимоги щодо покриття вище. Простий строк окупності — ціна модуля, поділена на додатний річний приріст прибутку. Вартість модулів показана окремо й не віднімається від змодельованого прибутку; не враховані монтаж, інвертор, проводка, фінансування, обслуговування, податки та майбутня деградація."
  }
};
const whatIfRunLabels = {
  da: { dashboard_running: "Beregner simulering", dashboard_failed: "Simuleringen kunne ikke gennemføres", stop: "Stop simulering", stopping: "Stopper simuleringen …", stopped: "Simuleringen blev stoppet" },
  de: { dashboard_running: "Simulation wird berechnet", dashboard_failed: "Die Simulation konnte nicht abgeschlossen werden", stop: "Simulation stoppen", stopping: "Simulation wird gestoppt …", stopped: "Simulation wurde gestoppt" },
  en: { dashboard_running: "Running simulation", dashboard_failed: "The simulation could not be completed", stop: "Stop simulation", stopping: "Stopping simulation…", stopped: "Simulation stopped" },
  pl: { dashboard_running: "Trwa obliczanie symulacji", dashboard_failed: "Nie udało się ukończyć symulacji", stop: "Zatrzymaj symulację", stopping: "Zatrzymywanie symulacji…", stopped: "Symulacja została zatrzymana" },
  uk: { dashboard_running: "Виконується симуляція", dashboard_failed: "Не вдалося завершити симуляцію", stop: "Зупинити симуляцію", stopping: "Зупинення симуляції…", stopped: "Симуляцію зупинено" },
};
const whatIfProgressLabels = {
  da: {
    starting: "Forbereder historik",
    phases: { statistics: "Indlæser langtidsstatistik", states: "Indlæser historiske tilstande", tariffs: "Indlæser tarifhistorik", hourly: "Matcher historiske timer", preparing: "Forbereder simulering" },
    simulating: "Simulationsfremskridt", case: "Beregning", hours: "Behandlede timer", elapsed: "Forløbet", seconds: "sek.", eta: "Estimeret resterende tid", estimate: "estimat", minutes: "min.", progress: "Simulationsfremskridt",
  },
  de: {
    starting: "Verlauf wird vorbereitet",
    phases: { statistics: "Langzeitstatistiken werden geladen", states: "Historische Zustände werden geladen", tariffs: "Tarifverlauf wird geladen", hourly: "Stundenverlauf wird abgeglichen", preparing: "Simulation wird vorbereitet" },
    simulating: "Simulationsfortschritt", case: "Durchlauf", hours: "Stunden verarbeitet", elapsed: "Vergangen", seconds: "Sek.", eta: "Geschätzte verbleibende Zeit", estimate: "Schätzung", minutes: "Min.", progress: "Simulationsfortschritt",
  },
  en: {
    starting: "Preparing history",
    phases: { statistics: "Loading long-term statistics", states: "Loading historical states", tariffs: "Loading tariff history", hourly: "Matching hourly history", preparing: "Preparing simulation" },
    simulating: "Simulation progress", case: "Replay", hours: "Hours processed", elapsed: "Elapsed", seconds: "sec", eta: "Estimated time remaining", estimate: "estimate", minutes: "min", progress: "Simulation progress",
  },
  pl: {
    starting: "Przygotowywanie historii",
    phases: { statistics: "Wczytywanie statystyk długoterminowych", states: "Wczytywanie historycznych stanów", tariffs: "Wczytywanie historii taryf", hourly: "Dopasowywanie danych godzinowych", preparing: "Przygotowywanie symulacji" },
    simulating: "Postęp symulacji", case: "Przebieg", hours: "Przetworzone godziny", elapsed: "Upłynęło", seconds: "s", eta: "Szacowany pozostały czas", estimate: "szacunek", minutes: "min", progress: "Postęp symulacji",
  },
  uk: {
    starting: "Підготовка історії",
    phases: { statistics: "Завантаження довгострокової статистики", states: "Завантаження історичних станів", tariffs: "Завантаження історії тарифів", hourly: "Зіставлення погодинної історії", preparing: "Підготовка симуляції" },
    simulating: "Перебіг симуляції", case: "Прохід", hours: "Оброблено годин", elapsed: "Минуло", seconds: "с", eta: "Орієнтовний час до завершення", estimate: "оцінка", minutes: "хв", progress: "Перебіг симуляції",
  },
};
const whatIfBaselineNotes = {
  da: "Nuværende er et estimat: simuleringen antager, at den nuværende opsætning blev brugt gennem hele perioden. Fokusér især på forskellen mellem nuværende og scenariet; også forskellen er et estimat.",
  de: "Aktuell ist eine Schätzung: Die Simulation nimmt an, dass die aktuelle Konfiguration während des gesamten Zeitraums verwendet wurde. Achten Sie vor allem auf den Unterschied zwischen Aktuell und Szenario; auch dieser Unterschied ist eine Schätzung.",
  en: "Current is an estimate: the simulation assumes the current configuration was used throughout the period. Focus especially on the difference between current and scenario; that difference is also an estimate.",
  pl: "Wynik bieżący jest oszacowaniem: symulacja zakłada używanie obecnej konfiguracji przez cały okres. Skup się przede wszystkim na różnicy między wynikiem bieżącym a scenariuszem; ta różnica również jest oszacowaniem.",
  uk: "Поточний результат є оцінкою: симуляція припускає, що поточна конфігурація використовувалася протягом усього періоду. Насамперед звертайте увагу на різницю між поточним результатом і сценарієм; ця різниця також є оцінкою.",
};
const oldWhatIfHelpSuffix = {
  da: " Fremskridt og fejl vises også i notifikationer.",
  de: " Fortschritt und Fehler erscheinen auch in Benachrichtigungen.",
  en: " Progress and errors are also shown in notifications.",
  pl: " Postęp i błędy są również widoczne w powiadomieniach.",
  uk: " Перебіг і помилки також показуються у сповіщеннях.",
};
const oldInterpolationText = {
  da: ["lineært estimat", "Mellem punkterne vises et lineært estimat, ikke en ny simulering eller en prognose. Kapacitet vises afrundet til hele kWh; beregninger bruger de faktiske værdier."],
  de: ["lineare Schätzung", "Werte zwischen den Punkten sind linear geschätzt, keine neue Simulation oder Prognose. Die Kapazität wird auf ganze kWh gerundet angezeigt; Berechnungen verwenden die tatsächlichen Werte."],
  en: ["linear estimate", "Values between points are linearly estimated, not a new simulation or forecast. Capacity is displayed rounded to whole kWh; calculations use the actual values."],
  pl: ["oszacowanie liniowe", "Wartości między punktami są szacowane liniowo, a nie symulowane ani prognozowane. Pojemność jest wyświetlana w pełnych kWh; obliczenia używają rzeczywistych wartości."],
  uk: ["лінійна оцінка", "Значення між точками оцінені лінійно, а не отримані новою симуляцією чи прогнозом. Ємність показано округлено до цілих кВт·год; розрахунки використовують фактичні значення."],
};

function interpolationLabel(value, index, language) {
  for (const [locale, old] of Object.entries(oldInterpolationText)) {
    if (value === old[index]) return whatIfInterpolationLabels[locale][index];
  }
  return value || (whatIfInterpolationLabels[language] || whatIfInterpolationLabels.en)[index];
}

function smoothSlopes(points) {
  const steps = points.slice(1).map((point, index) => point.position - points[index].position);
  if (!steps.length || steps.some(step => !Number.isFinite(step) || step <= 0)) return null;
  const slopes = steps.map((step, index) => (points[index + 1].value - points[index].value) / step);
  if (slopes.length === 1) return [slopes[0], slopes[0]];
  const endpoint = (h0, h1, d0, d1) => {
    let value = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1);
    if (Math.sign(value) !== Math.sign(d0)) value = 0;
    else if (Math.sign(d0) !== Math.sign(d1) && Math.abs(value) > Math.abs(3 * d0)) value = 3 * d0;
    return value;
  };
  const result = [endpoint(steps[0], steps[1], slopes[0], slopes[1])];
  for (let i = 1; i < slopes.length; i++) {
    const left = slopes[i - 1], right = slopes[i];
    if (left === 0 || right === 0 || Math.sign(left) !== Math.sign(right)) {
      result.push(0);
    } else {
      const a = 2 * steps[i] + steps[i - 1], b = steps[i] + 2 * steps[i - 1];
      result.push((a + b) / (a / left + b / right));
    }
  }
  const n = slopes.length;
  result.push(endpoint(steps[n - 1], steps[n - 2], slopes[n - 1], slopes[n - 2]));
  return result;
}

function smoothValue(left, right, leftSlope, rightSlope, position) {
  const step = right.position - left.position;
  const t = (position - left.position) / step;
  return (2 * t ** 3 - 3 * t ** 2 + 1) * left.value +
    (t ** 3 - 2 * t ** 2 + t) * step * leftSlope +
    (-2 * t ** 3 + 3 * t ** 2) * right.value +
    (t ** 3 - t ** 2) * step * rightSlope;
}

function whatIfSession(entryId) {
  if (!whatIfSessions.has(entryId)) {
    whatIfSessions.set(entryId, {
      running: false, stopping: false, stopped: false, jobId: null, localJob: false,
      result: null, error: null, inputs: null, pendingInputs: null,
      completedAt: null, loadError: null, statusError: null, progress: null,
      progressSignature: null, progressSample: null, loading: false, statusLoading: false, revision: 0, listeners: new Set(),
    });
  }
  return whatIfSessions.get(entryId);
}

function publishWhatIf(session) {
  session.revision++;
  for (const listener of session.listeners) listener();
}

function whatIfError(error) {
  const detail = error && typeof error === "object" ? error.message || error : error;
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail) || String(detail);
  } catch {
    return String(detail);
  }
}

async function runWhatIf(session, hass, data) {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  const jobId = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  session.running = true;
  session.stopping = false;
  session.stopped = false;
  session.jobId = jobId;
  session.localJob = true;
  session.progress = null;
  session.progressSignature = null;
  session.progressSample = null;
  session.error = null;
  session.pendingInputs = data;
  publishWhatIf(session);
  try {
    const { config_entry_id, ...inputs } = data;
    const result = await hass.callWS({
      type: "battmind/what_if_start", config_entry_id, job_id: jobId, data: inputs,
    });
    if (result.job_id !== jobId) throw new Error("Unexpected simulation response");
    if (result.status === "cancelled") {
      session.stopped = true;
    } else if (result.status === "completed" && result.result) {
      session.result = result.result;
      session.inputs = data;
      session.completedAt = null;
    } else {
      throw new Error("Unexpected simulation response");
    }
  } catch (error) {
    session.error = whatIfError(error);
  } finally {
    session.running = false;
    session.stopping = false;
    session.localJob = false;
    session.jobId = null;
    session.progress = null;
    session.progressSignature = null;
    session.progressSample = null;
    session.pendingInputs = null;
    publishWhatIf(session);
    await loadLastWhatIf(session, hass, data.config_entry_id);
    await refreshWhatIfStatus(session, hass, data.config_entry_id);
  }
}

async function refreshWhatIfStatus(session, hass, entryId) {
  if (session.statusLoading) return;
  session.statusLoading = true;
  const revision = session.revision;
  try {
    const status = await hass.callWS({ type: "battmind/what_if_status", config_entry_id: entryId });
    if (session.revision !== revision) return;
    if (!status || !["idle", "running", "stopping"].includes(status.status) ||
        (status.status !== "idle" && !status.job_id)) throw new Error("Unexpected simulation status");
    if (session.localJob && (
      status.status === "idle" || status.job_id !== session.jobId
    )) return;
    const running = status.status !== "idle";
    const rawProgress = status.progress && typeof status.progress === "object" ? status.progress : null;
    const signature = rawProgress ? JSON.stringify(rawProgress) : null;
    const progressChanged = signature !== session.progressSignature;
    if (progressChanged) {
      if (!rawProgress) {
        session.progress = null;
        session.progressSample = null;
      } else {
        const progress = { ...rawProgress };
        if (progress.phase) {
          session.progressSample = null;
        } else {
          const totalHours = Number(progress.total_hours);
          const step = Number(progress.step);
          const hours = Number(progress.hours);
          const seconds = Number(progress.seconds);
          const percent = Number(progress.percent);
          const totalWork = totalHours * Number(progress.steps);
          const completedWork = (step - 1) * totalHours + hours;
          const previous = session.progressSample;
          if (previous && seconds - previous.seconds >= 20 && completedWork > previous.completedWork &&
              percent >= 5 && percent - previous.percent >= 2 && completedWork < totalWork) {
            progress.eta_seconds = Math.ceil(
              ((totalWork - completedWork) * (seconds - previous.seconds)) /
              (completedWork - previous.completedWork),
            );
          }
          if (Number.isFinite(completedWork) && Number.isFinite(seconds) &&
              (!previous || completedWork > previous.completedWork)) {
            session.progressSample = { completedWork, seconds, percent };
          }
        }
        session.progress = progress;
      }
      session.progressSignature = signature;
    }
    const changed = session.running !== running || session.jobId !== status.job_id ||
      session.stopping !== (status.status === "stopping") || session.statusError !== null || progressChanged;
    session.running = running;
    session.stopping = status.status === "stopping";
    session.jobId = status.job_id;
    session.statusError = null;
    if (changed) {
      publishWhatIf(session);
      if (!running) await loadLastWhatIf(session, hass, entryId);
    }
  } catch (error) {
    if (session.revision !== revision) return;
    const message = whatIfError(error);
    if (session.statusError !== message) {
      session.statusError = message;
      publishWhatIf(session);
    }
  } finally {
    session.statusLoading = false;
  }
}

async function stopWhatIf(session, hass, entryId) {
  if (!session.running || session.stopping || !session.jobId) return;
  const jobId = session.jobId;
  session.stopping = true;
  session.error = null;
  publishWhatIf(session);
  try {
    const result = await hass.callWS({ type: "battmind/what_if_cancel", config_entry_id: entryId, job_id: jobId });
    if (result.job_id !== jobId || result.status !== "stopping") throw new Error("Unexpected cancellation response");
  } catch (error) {
    if (session.jobId !== jobId) return;
    session.stopping = false;
    session.error = whatIfError(error);
    publishWhatIf(session);
    await refreshWhatIfStatus(session, hass, entryId);
  }
}

async function loadLastWhatIf(session, hass, entryId) {
  if (session.loading || session.running) return;
  session.loading = true;
  const revision = session.revision;
  try {
    const record = await hass.callWS({ type: "battmind/what_if_last", config_entry_id: entryId });
    if (session.revision !== revision || session.running) return;
    const changed = session.loadError !== null || (
      record && (!session.completedAt || record.completed_at > session.completedAt)
    );
    session.loadError = null;
    if (record && (!session.completedAt || record.completed_at > session.completedAt)) {
      session.result = record.simulation;
      session.inputs = record.inputs;
      session.completedAt = record.completed_at;
    }
    if (changed) publishWhatIf(session);
  } catch (error) {
    if (session.revision !== revision) return;
    const message = whatIfError(error);
    if (session.loadError !== message) {
      session.loadError = message;
      publishWhatIf(session);
    }
  } finally {
    session.loading = false;
  }
}

class BattMindWhatIfCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._connected = false;
    this._updateSession = () => this._renderSession();
  }

  setConfig(config) {
    if (!config.config_entry_id || !config.labels) {
      throw new Error("BattMind what-if card requires a config entry and labels");
    }
    if (this._session) this._session.listeners.delete(this._updateSession);
    clearInterval(this._refreshTimer);
    this._refreshTimer = null;
    this._config = config;
    this._session = whatIfSession(config.config_entry_id);
    if (this._connected) this._session.listeners.add(this._updateSession);
    this._build();
    this._renderSession();
    this._startRefresh();
  }

  set hass(hass) {
    const previousLanguage = (this._hass?.language || "en").toLowerCase().split("-")[0];
    this._hass = hass;
    const language = (hass.language || "en").toLowerCase().split("-")[0];
    if (this._config && language !== previousLanguage && !this._formDirty) this._build();
    this._renderSession();
    this._startRefresh();
  }

  connectedCallback() {
    this._connected = true;
    if (this._session) this._session.listeners.add(this._updateSession);
    this._renderSession();
    this._startRefresh();
  }

  disconnectedCallback() {
    this._connected = false;
    if (this._session) this._session.listeners.delete(this._updateSession);
    clearInterval(this._refreshTimer);
    this._refreshTimer = null;
  }

  _startRefresh() {
    if (!this._connected || !this._session || !this._hass || this._refreshTimer) return;
    const refresh = async () => {
      await refreshWhatIfStatus(this._session, this._hass, this._config.config_entry_id);
      await loadLastWhatIf(this._session, this._hass, this._config.config_entry_id);
    };
    refresh();
    this._refreshTimer = setInterval(refresh, 3000);
  }

  getCardSize() {
    return 8;
  }

  _element(tag, text, parent) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  }

  _input(form, label, name, value, min, max, step) {
    const wrapper = this._element("label", label, form);
    const input = this._element("input", undefined, wrapper);
    Object.assign(input, { name, type: "number", value, min, max, step, required: true });
    return input;
  }

  _labels() {
    const language = (this._hass?.language || "en").toLowerCase().split("-")[0];
    return { ...(whatIfModuleLabels[language] || whatIfModuleLabels.en), ...this._config.labels };
  }

  _build() {
    const labels = this._labels();
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { box-sizing: border-box; padding: clamp(16px, 3vw, 28px); color: var(--primary-text-color); }
        h2 { margin: 0 0 10px; font-size: 1.35rem; line-height: 1.3; }
        h3 { margin: 24px 0 10px; font-size: 1rem; line-height: 1.4; }
        p { margin: 8px 0; line-height: 1.55; }
        .intro, .form-note, .result-note { color: var(--secondary-text-color); font-size: .9rem; }
        .intro { max-width: 80ch; margin-bottom: 20px; }
        form { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 170px), 1fr));
          gap: 14px; align-items: end; padding: 18px; border: 1px solid var(--divider-color);
          border-radius: 16px; background: var(--secondary-background-color, var(--card-background-color)); }
        label { display: flex; flex-direction: column; gap: 7px; min-width: 0; font-size: .9rem; font-weight: 500; }
        input { box-sizing: border-box; width: 100%; min-height: 44px; padding: 10px 12px;
          border: 1px solid var(--divider-color); border-radius: 10px;
          background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
        input:focus-visible, button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        .check { grid-column: 1 / -1; flex-direction: row; align-items: center; gap: 10px;
          min-height: 36px; cursor: pointer; }
        .check input { width: 18px; height: 18px; min-height: 0; padding: 0; accent-color: var(--primary-color); }
        .range { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 140px), 1fr));
          gap: 14px; }
        .range[hidden] { display: none; }
        button { justify-self: start; min-height: 44px; padding: 10px 20px; border: 0; border-radius: 10px;
          background: var(--primary-color); color: var(--text-primary-color); cursor: pointer; font: inherit; font-weight: 600; }
        button:disabled { opacity: .6; cursor: wait; }
        button.stop { background: var(--card-background-color); color: var(--primary-text-color);
          border: 1px solid var(--divider-color); }
        button[hidden] { display: none; }
        .form-note { margin: 10px 2px 0; }
        .status { margin: 12px 0; line-height: 1.5; }
        .status:empty { display: none; }
        .progress { margin: 12px 0; }
        .progress[hidden] { display: none; }
        .progress progress { display: block; width: 100%; height: 14px; accent-color: var(--primary-color); }
        .progress p { margin: 6px 0 0; color: var(--secondary-text-color); font-size: .9rem; }
        .error { color: var(--error-color); }
        .results { margin-top: 26px; }
        .results > h2 { padding-top: 22px; border-top: 1px solid var(--divider-color); }
        .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 175px), 1fr));
          gap: 10px; margin: 16px 0; }
        .metric { min-width: 0; padding: 15px; border: 1px solid var(--divider-color);
          border-radius: 14px; background: var(--secondary-background-color, var(--card-background-color)); }
        .metric-label { color: var(--secondary-text-color); font-size: .85rem; line-height: 1.4; }
        .metric-value { margin-top: 6px; font-size: 1.25rem; font-weight: 650; overflow-wrap: anywhere; }
        .metric-caption { margin-top: 4px; color: var(--secondary-text-color); font-size: .8rem; }
        .meta { color: var(--secondary-text-color); font-size: .9rem; }
        .warning { padding: 10px 12px; border-inline-start: 4px solid var(--warning-color, var(--primary-color));
          border-radius: 6px; background: var(--secondary-background-color, var(--card-background-color)); }
        .inputs { display: flex; flex-wrap: wrap; gap: 8px; padding: 0; margin: 8px 0 16px; list-style: none; }
        .inputs li { padding: 6px 10px; border: 1px solid var(--divider-color); border-radius: 9px;
          font-size: .85rem; overflow-wrap: anywhere; }
        .section-title { margin: 30px 0 8px; padding-top: 20px; border-top: 1px solid var(--divider-color); }
        table { border-collapse: separate; border-spacing: 0; width: 100%; min-width: 530px; font-size: .9rem; }
        th, td { padding: 14px 16px; border-bottom: 1px solid var(--divider-color); text-align: right; white-space: nowrap; }
        tr:last-child > * { border-bottom: 0; }
        tr:first-child > th { background: var(--secondary-background-color, var(--card-background-color)); }
        th { color: var(--secondary-text-color); font-weight: 600; }
        td { font-variant-numeric: tabular-nums; }
        th:first-child, td:first-child { text-align: left; }
        .best { font-weight: 700; }
        .badge { display: block; margin-top: 4px; color: var(--primary-text-color); font-size: .75rem; font-weight: 500; }
        .scroll { overflow-x: auto; margin: 12px 0; border: 1px solid var(--divider-color); border-radius: 12px; }
        .chart-scroll { padding: 12px 4px 0; background: var(--secondary-background-color, var(--card-background-color)); }
        .chart-detail { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
        .chart-select { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
        .chart-select:focus-visible { position: static; width: auto; height: auto; overflow: visible; clip-path: none;
          max-width: 100%; margin-top: 8px; padding: 8px; border: 1px solid var(--divider-color);
          border-radius: 8px; background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
        svg { display: block; width: 100%; height: 220px; overflow: visible; }
        svg text { fill: var(--secondary-text-color); font-size: 11px; }
        svg polyline, svg path { fill: none; stroke: var(--primary-color); stroke-width: 2.5; stroke-linejoin: round; }
        svg line { stroke: var(--secondary-text-color); stroke-width: 1; opacity: .5; }
        svg circle { fill: var(--primary-color); }
        svg circle.best-point { fill: var(--success-color, var(--primary-color)); stroke: var(--primary-text-color); stroke-width: 1.5; }
        @media (max-width: 500px) { form { padding: 14px; } button { width: 100%; } }
      </style>`;
    const card = this._element("ha-card", undefined, this.shadowRoot);
    this._element("h2", labels.title, card);
    let help = labels.help;
    for (const suffix of Object.values(oldWhatIfHelpSuffix)) {
      if (typeof help === "string" && help.endsWith(suffix)) help = help.slice(0, -suffix.length);
    }
    this._element("p", help, card).className = "intro";
    this._baselineNote = this._element("p", "", card);
    this._baselineNote.className = "intro";
    const form = this._element("form", undefined, card);
    this._form = form;
    this._formDirty = false;
    const saved = this._session.pendingInputs || this._session.inputs;
    this._appliedInputs = saved;
    const history = this._input(form, labels.history, "history_days", String(saved?.history_days ?? 365), "1", "365", "1");
    const battery = this._input(form, labels.battery, "battery_size", String(saved?.battery_size ?? 20), "0", "1000", "any");
    const rangeLabel = this._element("label", undefined, form);
    rangeLabel.className = "check";
    const rangeToggle = this._element("input", undefined, rangeLabel);
    rangeToggle.type = "checkbox";
    this._element("span", labels.range, rangeLabel);
    const range = this._element("div", undefined, form);
    range.className = "range";
    rangeToggle.checked = saved != null && "range_start" in saved;
    range.hidden = !rangeToggle.checked;
    const inputs = [
      this._input(range, labels.range_start, "range_start", String(saved?.range_start ?? 0), "0", "1000", "any"),
      this._input(range, labels.range_end, "range_end", String(saved?.range_end ?? 30), "0", "1000", "any"),
      this._input(range, labels.range_step, "range_step", String(saved?.range_step ?? 5), "0.001", "1000", "any"),
    ];
    inputs.forEach(input => { input.disabled = !rangeToggle.checked; });
    const moduleLabel = this._element("label", undefined, form);
    moduleLabel.className = "check";
    const moduleToggle = this._element("input", undefined, moduleLabel);
    moduleToggle.type = "checkbox";
    this._element("span", labels.module_compare, moduleLabel);
    const moduleFields = this._element("div", undefined, form);
    moduleFields.className = "range";
    moduleToggle.checked = saved != null && "module_capacity" in saved;
    moduleFields.hidden = !moduleToggle.checked;
    const moduleInputs = {
      capacity: this._input(
        moduleFields, labels.module_capacity, "module_capacity",
        String(saved?.module_capacity ?? 5), "0.001", "1000", "any",
      ),
      price: this._input(
        moduleFields, labels.module_price, "module_price",
        String(saved?.module_price ?? 0), "0", "1000000000", "any",
      ),
      count: this._input(
        moduleFields, labels.module_count, "module_count",
        String(saved?.module_count ?? 5), "1", "12", "1",
      ),
    };
    Object.values(moduleInputs).forEach(input => { input.disabled = !moduleToggle.checked; });
    this._element("p", labels.form_explain, card).className = "form-note";
    this._formInputs = { history, battery, rangeToggle, range, inputs, moduleToggle, moduleFields, moduleInputs };
    form.addEventListener("input", () => { this._formDirty = true; });
    form.addEventListener("change", () => { this._formDirty = true; });
    rangeToggle.addEventListener("change", () => {
      range.hidden = !rangeToggle.checked;
      inputs.forEach(input => { input.disabled = !rangeToggle.checked; });
    });
    moduleToggle.addEventListener("change", () => {
      moduleFields.hidden = !moduleToggle.checked;
      Object.values(moduleInputs).forEach(input => { input.disabled = !moduleToggle.checked; });
    });
    this._button = this._element("button", labels.run, form);
    this._button.type = "submit";
    this._stopButton = this._element("button", labels.stop || whatIfRunLabels.en.stop, form);
    this._stopButton.type = "button";
    this._stopButton.className = "stop";
    this._stopButton.hidden = true;
    this._stopButton.addEventListener("click", () => this._stop());
    this._progress = this._element("div", undefined, card);
    this._progress.className = "progress";
    this._progress.hidden = true;
    this._progress.setAttribute("role", "status");
    this._progressBar = this._element("progress", undefined, this._progress);
    this._progressBar.max = 100;
    this._progressText = this._element("p", "", this._progress);
    this._status = this._element("p", "", card);
    this._status.className = "status";
    this._status.setAttribute("role", "status");
    this._results = this._element("div", labels.empty, card);
    this._results.className = "results";
    this._renderedRevision = -1;
    form.addEventListener("submit", event => {
      event.preventDefault();
      this._run(form, rangeToggle.checked, moduleToggle.checked);
    });
  }

  async _run(form, hasRange, hasModules = false) {
    if (this._session.running || !this._hass) return;
    const data = {
      config_entry_id: this._config.config_entry_id,
      history_days: Number(form.elements.history_days.value),
      battery_size: Number(form.elements.battery_size.value),
    };
    if (hasRange) {
      for (const key of ["range_start", "range_end", "range_step"]) {
        data[key] = Number(form.elements[key].value);
      }
    }
    if (hasModules) {
      for (const key of ["module_capacity", "module_price", "module_count"]) {
        data[key] = Number(form.elements[key].value);
      }
    }
    try {
      return await runWhatIf(this._session, this._hass, data);
    } catch (error) {
      this._session.error = whatIfError(error);
      publishWhatIf(this._session);
    }
  }

  async _stop() {
    if (!this._hass) return;
    return stopWhatIf(this._session, this._hass, this._config.config_entry_id);
  }

  _renderSession() {
    if (!this._session || !this._hass || !this._results || this._renderedRevision === this._session.revision) return;
    const labels = this._labels();
    this._renderedRevision = this._session.revision;
    if (!this._formDirty && this._session.inputs && this._session.inputs !== this._appliedInputs) {
      const saved = this._session.inputs;
      const {
        history, battery, rangeToggle, range, inputs, moduleToggle, moduleFields, moduleInputs,
      } = this._formInputs;
      history.value = String(saved.history_days ?? 365);
      battery.value = String(saved.battery_size ?? 20);
      rangeToggle.checked = "range_start" in saved;
      range.hidden = !rangeToggle.checked;
      ["range_start", "range_end", "range_step"].forEach((key, index) => {
        inputs[index].value = String(saved[key] ?? [0, 30, 5][index]);
        inputs[index].disabled = !rangeToggle.checked;
      });
      moduleToggle.checked = "module_capacity" in saved;
      moduleFields.hidden = !moduleToggle.checked;
      moduleInputs.capacity.value = String(saved.module_capacity ?? 5);
      moduleInputs.price.value = String(saved.module_price ?? 0);
      moduleInputs.count.value = String(saved.module_count ?? 5);
      Object.values(moduleInputs).forEach(input => { input.disabled = !moduleToggle.checked; });
      this._appliedInputs = saved;
    }
    this._button.disabled = this._session.running;
    const language = (this._hass.language || "en").toLowerCase().split("-")[0];
    const defaults = whatIfRunLabels[language] || whatIfRunLabels.en;
    this._baselineNote.textContent = labels.baseline_note || whatIfBaselineNotes[language] || whatIfBaselineNotes.en;
    this._stopButton.textContent = labels.stop || defaults.stop;
    this._stopButton.hidden = !this._session.running;
    this._stopButton.disabled = this._session.stopping;
    this._progress.hidden = !this._session.running;
    const progressLabels = whatIfProgressLabels[language] || whatIfProgressLabels.en;
    this._progressBar.setAttribute("aria-label", progressLabels.progress);
    if (this._session.running) {
      const progress = this._session.progress;
      if (progress?.phase) {
        this._progressBar.removeAttribute("value");
        this._progressBar.removeAttribute("aria-valuenow");
        const phase = progressLabels.phases[progress.phase] || progressLabels.starting;
        const elapsed = Number.isFinite(Number(progress.seconds))
          ? ` (${progressLabels.elapsed}: ${this._number(Number(progress.seconds))} ${progressLabels.seconds})`
          : "";
        this._progressText.textContent = `${phase}${elapsed}`;
      } else if (progress && Number.isFinite(Number(progress.percent))) {
        const percent = Math.max(0, Math.min(100, Number(progress.percent)));
        this._progressBar.value = percent;
        this._progressBar.setAttribute("aria-valuenow", String(percent));
        this._progressText.textContent =
          `${progressLabels.simulating}: ${percent}% — ${progressLabels.case} ${progress.step}/${progress.steps}; `
          + `${progressLabels.hours}: ${progress.hours}/${progress.total_hours}`;
        if (Number.isFinite(Number(progress.eta_seconds)) && Number(progress.eta_seconds) > 0) {
          const etaSeconds = Number(progress.eta_seconds);
          const eta = etaSeconds >= 60
            ? `${this._number(Math.ceil(etaSeconds / 60))} ${progressLabels.minutes}`
            : `${this._number(etaSeconds)} ${progressLabels.seconds}`;
          this._progressText.textContent +=
            `; ${progressLabels.eta}: ~${eta} (${progressLabels.estimate})`;
        }
      } else {
        this._progressBar.removeAttribute("value");
        this._progressBar.removeAttribute("aria-valuenow");
        this._progressText.textContent = progressLabels.starting;
      }
    } else {
      this._progressBar.removeAttribute("value");
      this._progressBar.removeAttribute("aria-valuenow");
      this._progressText.textContent = "";
    }
    this._status.className = this._session.error || this._session.loadError || this._session.statusError ? "status error" : "status";
    this._status.textContent = this._session.error ? `${labels.dashboard_failed || defaults.dashboard_failed}: ${this._session.error}`
      : this._session.running ? (this._session.stopping ? labels.stopping || defaults.stopping : labels.dashboard_running || defaults.dashboard_running)
        : this._session.statusError ? `${labels.load_failed}: ${this._session.statusError}`
        : this._session.stopped ? labels.stopped || defaults.stopped
        : this._session.loadError ? `${labels.load_failed}: ${this._session.loadError}` : "";
    if (this._session.result) {
      this._show(this._session.result);
    } else {
      this._results.textContent = this._session.running || this._session.error ? "" : labels.empty;
    }
  }

  _number(value) {
    return new Intl.NumberFormat(this._hass.language, { maximumFractionDigits: 2 }).format(value);
  }

  _capacity(value) {
    return new Intl.NumberFormat(this._hass.language, { maximumFractionDigits: Number.isInteger(value) ? 0 : 12 }).format(value);
  }

  _money(value) {
    return new Intl.NumberFormat(this._hass.language, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
  }

  _show(simulation) {
    const labels = this._labels();
    const currency = this._hass.config.currency;
    this._results.textContent = "";
    this._element("h2", labels.results, this._results);
    if (this._session.completedAt) {
      const completed = new Date(this._session.completedAt);
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      this._element("p", `${labels.completed}: ${completed.toLocaleString(this._hass.language)} (${zone})`, this._results).className = "meta";
    }
    if (this._session.inputs) {
      const inputNames = {
        history_days: labels.history, battery_size: labels.battery, range_start: labels.range_start,
        range_end: labels.range_end, range_step: labels.range_step,
        module_capacity: labels.module_capacity, module_price: labels.module_price,
        module_count: labels.module_count,
      };
      this._element("h3", labels.used_settings, this._results);
      const list = this._element("ul", undefined, this._results);
      list.className = "inputs";
      for (const [key, value] of Object.entries(this._session.inputs)) {
        if (key !== "config_entry_id") this._element("li", `${inputNames[key] || key}: ${value}`, list);
      }
    }
    const period = simulation.period;
    this._element("p", `${labels.period}: ${period.start} – ${period.end} (${this._number(period.hours)} ${labels.hours})`, this._results).className = "meta";
    if (simulation.coverage) {
      this._element("p", `${labels.missing}: ${this._number(simulation.coverage.missing_hours)} ${labels.hours}`, this._results).className =
        simulation.coverage.missing_hours ? "warning" : "meta";
    }
    if (simulation.tariff_estimated_hours || simulation.tariff_unavailable_hours) {
      this._element("p", `${labels.tariffs}: ${simulation.tariff_estimated_hours} / ${simulation.tariff_unavailable_hours}`, this._results).className = "warning";
    }
    if (!simulation.assumptions.sell_price_history_available) {
      this._element("p", labels.sell_history, this._results).className = "warning";
    }
    this._element("h2", labels.scenario_section, this._results).className = "section-title";
    this._element("h3", labels.profit, this._results);
    const summary = this._element("div", undefined, this._results);
    summary.className = "summary";
    for (const [label, caption, value] of [
      [labels.current, `${this._capacity(simulation.current_assumptions.battery_size)} kWh`, simulation.current.profit],
      [labels.scenario, `${this._capacity(simulation.assumptions.battery_size)} kWh`, simulation.scenario.profit],
      [labels.difference, `${labels.scenario} – ${labels.current}`, simulation.difference.profit],
    ]) {
      const metric = this._element("div", undefined, summary);
      metric.className = "metric";
      this._element("div", label, metric).className = "metric-label";
      this._element("div", `${this._money(value)} ${currency}`, metric).className = "metric-value";
      this._element("div", caption, metric).className = "metric-caption";
    }
    const scroll = this._element("div", undefined, this._results);
    scroll.className = "scroll";
    const table = this._element("table", undefined, scroll);
    const header = this._element("tr", undefined, table);
    for (const text of [
      "", `${labels.current} (${this._capacity(simulation.current_assumptions.battery_size)} kWh)`,
      `${labels.scenario} (${this._capacity(simulation.assumptions.battery_size)} kWh)`, labels.difference,
    ]) this._element("th", text, header).setAttribute("scope", "col");
    for (const [label, current, scenario, difference] of [
      [labels.profit, simulation.current.profit, simulation.scenario.profit, simulation.difference.profit],
      [labels.bill, simulation.current.with.bill, simulation.scenario.with.bill, simulation.difference.bill],
    ]) {
      const row = this._element("tr", undefined, table);
      this._element("th", label, row).setAttribute("scope", "row");
      for (const value of [current, scenario, difference]) this._element("td", `${this._money(value)} ${currency}`, row);
    }
    this._element("p", labels.bill_explain, this._results).className = "result-note";
    if (!simulation.range) {
      const points = [
          { label: `${labels.current} (${this._capacity(simulation.current_assumptions.battery_size)} kWh)`, value: simulation.current.profit },
          { label: `${labels.scenario} (${this._capacity(simulation.assumptions.battery_size)} kWh)`, value: simulation.scenario.profit },
        ];
      this._chart(points, labels.profit, currency);
    }
    if (simulation.range) {
      this._element("h2", labels.range_section, this._results).className = "section-title";
      const finiteProfits = simulation.range.map(item => item.profit).filter(Number.isFinite);
      const highestProfit = finiteProfits.length > 1 ? Math.max(...finiteProfits) : null;
      const rates = simulation.range.map((item, index) => {
        const previous = simulation.range[index - 1];
        const capacity = previous && item.battery_size - previous.battery_size;
        if (!previous || !(capacity > 0) || !Number.isFinite(capacity) ||
            !Number.isFinite(item.profit) || !Number.isFinite(previous.profit)) return null;
        return (item.profit - previous.profit) / capacity;
      });
      const finiteRates = rates.filter(Number.isFinite);
      const highestRate = finiteRates.length ? Math.max(...finiteRates) : null;
      const intervals = [];
      const rangeScroll = this._element("div", undefined, this._results);
      rangeScroll.className = "scroll";
      const rangeTable = this._element("table", undefined, rangeScroll);
      const rangeHeader = this._element("tr", undefined, rangeTable);
      const language = (this._hass.language || "en").toLowerCase().split("-")[0];
      const headerDefaults = whatIfIntervalHeaders[language] || whatIfIntervalHeaders.en;
      for (const text of [
        labels.battery, labels.profit, labels.bill,
        labels.interval_profit || headerDefaults[0], labels.interval_per_kwh || headerDefaults[1],
      ]) {
        this._element("th", text, rangeHeader).setAttribute("scope", "col");
      }
      for (const [index, item] of simulation.range.entries()) {
        const row = this._element("tr", undefined, rangeTable);
        this._element("th", `${this._capacity(item.battery_size)} kWh`, row).setAttribute("scope", "row");
        const profitCell = this._element("td", `${this._money(item.profit)} ${currency}`, row);
        if (highestProfit !== null && item.profit === highestProfit) {
          profitCell.className = "best";
          this._element("span", labels.highest_profit, profitCell).className = "badge";
        }
        this._element("td", `${this._money(item.bill)} ${currency}`, row);
        const previous = simulation.range[index - 1];
        if (rates[index] !== null) {
          const difference = item.profit - previous.profit;
          const value = rates[index];
          this._element("td", `${this._money(difference)} ${currency}`, row);
          const rateCell = this._element("td", `${this._money(value)} ${currency}/kWh`, row);
          if (value === highestRate) {
            rateCell.className = "best";
            this._element("span", labels.highest_rate, rateCell).className = "badge";
          }
          intervals.push({
            label: `${this._capacity(previous.battery_size)}–${this._capacity(item.battery_size)} kWh`,
            value,
            position: (previous.battery_size + item.battery_size) / 2,
            start: previous.battery_size,
            end: item.battery_size,
            best: value === highestRate,
          });
        } else {
          this._element("td", "–", row);
          this._element("td", "–", row);
        }
      }
      this._element("p", labels.range_columns, this._results).className = "result-note";
      const points = simulation.range.map(item => ({
        label: `${this._capacity(item.battery_size)} kWh`, value: item.profit,
        position: item.battery_size,
      }));
      const chartExplanations = [interpolationLabel(labels.interpolation_note, 1, language)];
      const segmentRates = rates.slice(1);
      if (smoothSlopes(points) && segmentRates.every(Number.isFinite) && finiteRates.length > 1 && Math.min(...finiteRates) < highestRate) {
        chartExplanations.push(labels.chart_growth_legend || whatIfGradientLegends[language] || whatIfGradientLegends.en);
      }
      this._chart(points, labels.chart, currency, value => this._money(value),
        chartExplanations, true, true, false, segmentRates);
      if (intervals.length) {
        const explanations = [labels.interval_explain, labels.interval_example];
        if (intervals.length > 1) {
          explanations.push(labels.interval_curve_note || whatIfIntervalCurveNotes[language] || whatIfIntervalCurveNotes.en);
        }
        if (intervals.length > 1 && smoothSlopes(intervals) && Math.min(...finiteRates) < highestRate) {
          explanations.push(labels.interval_color_legend || whatIfIntervalGradientLegends[language] || whatIfIntervalGradientLegends.en);
        }
        this._chart(intervals, labels.interval_chart || whatIfIntervalTitles[language] || whatIfIntervalTitles.en, `${currency}/kWh`, value => this._money(value),
          explanations, true, true, true);
      }
    }
    if (simulation.module_comparison) {
      const comparison = simulation.module_comparison;
      const annualization = comparison.annualization;
      const eligibilityKnown = annualization.months && typeof annualization.months === "object";
      const annualizationAvailable = eligibilityKnown && annualization.available;
      const reasonLabel = {
        missing_sell_price: labels.module_missing_sell_price,
        missing_sell_cost: labels.module_missing_sell_cost,
      }[annualization.reason] || labels.module_coverage_requirement;
      const unavailableLabel = eligibilityKnown ? reasonLabel : labels.module_rerun;
      this._element("h2", labels.module_section, this._results).className = "section-title";
      this._element("p", labels.module_baseline_note, this._results).className = "result-note";
      this._element("p",
        `${labels.module_history}: ${this._number(annualization.matched_hours)} ${labels.hours}; `
          + `${annualization.represented_months.length}/12 ${labels.module_months}`,
        this._results).className = annualizationAvailable ? "meta" : "warning";
      if (!annualizationAvailable) {
        this._element("p", unavailableLabel, this._results).className = "warning";
      }
      const warnings = comparison.warnings || {};
      for (const [key, label] of [
        ["dst_replay_resets", labels.module_dst_warning],
        ["tariff_estimated_hours", labels.module_tariff_warning],
        ["raw_current_sell_cost_hours", labels.module_current_cost_warning],
        ["raw_unknown_sell_cost_hours", labels.module_missing_sell_cost],
      ]) {
        if (warnings[key] > 0) {
          this._element("p", `${label} (${this._number(warnings[key])})`, this._results).className = "warning";
        }
      }
      const moduleScroll = this._element("div", undefined, this._results);
      moduleScroll.className = "scroll";
      const moduleTable = this._element("table", undefined, moduleScroll);
      const moduleHeader = this._element("tr", undefined, moduleTable);
      for (const text of [
        labels.module_added, labels.module_capacity, labels.module_profit, labels.module_gain_baseline,
        labels.module_incremental_gain, labels.module_annualized_gain, labels.module_price,
        labels.module_cumulative_cost, labels.module_payback,
      ]) this._element("th", text, moduleHeader).setAttribute("scope", "col");
      for (const rowData of comparison.rows) {
        const row = this._element("tr", undefined, moduleTable);
        this._element("th", this._number(rowData.added_modules), row).setAttribute("scope", "row");
        this._element("td", `${this._capacity(rowData.battery_size)} kWh`, row);
        this._element("td", `${this._money(rowData.profit)} ${currency}`, row);
        this._element("td", `${this._money(rowData.gain_vs_current_capacity)} ${currency}`, row);
        this._element("td", rowData.incremental_gain === null
          ? "—" : `${this._money(rowData.incremental_gain)} ${currency}`, row);
        this._element("td", !annualizationAvailable || rowData.annualized_incremental_gain === null
          ? "—" : `${this._money(rowData.annualized_incremental_gain)} ${currency}`, row);
        this._element("td", `${this._money(rowData.module_price)} ${currency}`, row);
        this._element("td", `${this._money(rowData.cumulative_module_cost)} ${currency}`, row);
        const payback = rowData.added_modules === 0
          ? "—"
          : !annualizationAvailable || rowData.payback_years === null
            ? (annualizationAvailable ? labels.module_no_payback : unavailableLabel)
            : `${this._number(rowData.payback_years)} ${labels.module_years}`;
        this._element("td", payback, row);
      }
      this._element("p", labels.module_note, this._results).className = "result-note";
    }
  }

  _chart(points, title, currency, format = value => this._money(value), explanations = [], connect = true, interpolate = false, colorByValue = false, segmentRates = null) {
    if (!points.length || points.some(point => !Number.isFinite(point.value))) return;
    this._element("h3", `${title} (${currency})`, this._results);
    for (const explanation of explanations) this._element("p", explanation, this._results).className = "result-note";
    const ns = "http://www.w3.org/2000/svg";
    const selector = this._element("select", undefined, this._results);
    selector.className = "chart-select";
    selector.setAttribute("aria-label", `${title} (${currency})`);
    points.forEach((point, index) => {
      const option = this._element("option", `${point.label} — ${format(point.value)} ${currency}`, selector);
      option.value = String(index);
    });
    const detail = this._element("p", "", this._results);
    detail.className = "chart-detail";
    detail.setAttribute("aria-live", "polite");
    const scroll = this._element("div", undefined, this._results);
    scroll.className = "scroll chart-scroll";
    const svg = document.createElementNS(ns, "svg");
    const width = Math.max(600, points.length * 100);
    svg.setAttribute("viewBox", `0 0 ${width} 200`);
    svg.style.minWidth = `${width}px`;
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${title} (${currency})`);
    const minimum = Math.min(0, ...points.map(point => point.value));
    const maximum = Math.max(0, ...points.map(point => point.value));
    const spread = maximum - minimum || 1;
    const positioned = points.every(point => Number.isFinite(point.position));
    const intervalAxis = !connect && points.every(point => Number.isFinite(point.start) && Number.isFinite(point.end));
    const minPosition = positioned ? Math.min(...points.map(point => intervalAxis ? point.start : point.position)) : 0;
    const maxPosition = positioned ? Math.max(...points.map(point => intervalAxis ? point.end : point.position)) : 0;
    const usableWidth = width - 120;
    const coords = points.map((point, index) => ({
      ...point,
      x: 60 + (points.length === 1 ? usableWidth / 2 :
        positioned && maxPosition > minPosition ? (point.position - minPosition) * usableWidth / (maxPosition - minPosition) :
          index * usableWidth / (points.length - 1)),
      y: 145 - (point.value - minimum) * 105 / spread,
    }));
    const slopes = interpolate && positioned ? smoothSlopes(points) : null;
    const capacityInput = slopes ? this._element("input", undefined, this._results) : null;
    if (capacityInput) {
      capacityInput.className = "chart-select";
      Object.assign(capacityInput, { type: "number", min: minPosition, max: maxPosition, step: "any", value: minPosition });
      capacityInput.setAttribute("aria-label", this._config.labels.battery);
    }
    const hover = document.createElementNS(ns, "g");
    hover.style.display = "none";
    hover.setAttribute("pointer-events", "none");
    const hoverLine = document.createElementNS(ns, "line");
    hoverLine.setAttribute("y1", "25");
    hoverLine.setAttribute("y2", "145");
    hover.appendChild(hoverLine);
    const hoverBox = document.createElementNS(ns, "rect");
    hoverBox.setAttribute("width", "205");
    hoverBox.setAttribute("height", "25");
    hoverBox.setAttribute("rx", "5");
    hoverBox.setAttribute("fill", "var(--card-background-color)");
    hoverBox.setAttribute("stroke", "var(--divider-color)");
    hover.appendChild(hoverBox);
    const hoverText = document.createElementNS(ns, "text");
    hover.appendChild(hoverText);
    const showHover = (x, y, text) => {
      const boxX = Math.max(0, Math.min(width - 205, x + 10));
      const boxY = Math.max(2, y - 32);
      hoverLine.setAttribute("x1", x);
      hoverLine.setAttribute("x2", x);
      hoverBox.setAttribute("x", boxX);
      hoverBox.setAttribute("y", boxY);
      hoverText.setAttribute("x", boxX + 6);
      hoverText.setAttribute("y", boxY + 16);
      hoverText.textContent = text;
      hover.style.display = "";
    };
    const showPoint = point => {
      detail.textContent = `${point.label} — ${title}: ${format(point.value)} ${currency}`;
      selector.value = String(coords.indexOf(point));
      if (capacityInput) {
        capacityInput.value = String(point.position);
        capacityInput.setAttribute("aria-invalid", "false");
      }
      showHover(point.x, point.y, `${point.label}: ${format(point.value)} ${currency}`);
    };
    selector.addEventListener("change", () => {
      const point = coords[Number(selector.value)];
      if (point) showPoint(point);
    });
    showPoint(coords[0]);
    hover.style.display = "none";
    const showCapacity = capacity => {
      if (!Number.isFinite(capacity) || capacity < minPosition || capacity > maxPosition) return;
      const rightIndex = points.findIndex(point => point.position >= capacity);
      const right = points[rightIndex];
      if (rightIndex === 0 || right.position === capacity) {
        showPoint(coords[rightIndex]);
        return;
      }
      const left = points[rightIndex - 1];
      const value = smoothValue(left, right, slopes[rightIndex - 1], slopes[rightIndex], capacity);
      selector.selectedIndex = -1;
      capacityInput.value = String(capacity);
      const language = (this._hass.language || "en").toLowerCase().split("-")[0];
      const estimate = interpolationLabel(this._config.labels.interpolated, 0, language);
      detail.textContent = `${this._capacity(capacity)} kWh — ${title}: ≈${format(value)} ${currency} (${estimate})`;
      const pointX = 60 + (capacity - minPosition) * usableWidth / (maxPosition - minPosition);
      const pointY = 145 - (value - minimum) * 105 / spread;
      showHover(pointX, pointY, `${this._capacity(capacity)} kWh: ≈${format(value)} ${currency}`);
    };
    if (capacityInput) {
      capacityInput.addEventListener("input", () => {
        const capacity = Number(capacityInput.value);
        const valid = capacityInput.value !== "" && Number.isFinite(capacity) && capacity >= minPosition && capacity <= maxPosition;
        capacityInput.setAttribute("aria-invalid", String(!valid));
        if (valid) showCapacity(capacity);
        else {
          detail.textContent = "";
          hover.style.display = "none";
        }
      });
      capacityInput.addEventListener("change", () => {
        if (capacityInput.value === "") return;
        const capacity = Number(capacityInput.value);
        if (!Number.isFinite(capacity)) return;
        capacityInput.setAttribute("aria-invalid", "false");
        showCapacity(Math.max(minPosition, Math.min(maxPosition, capacity)));
      });
    }
    const showNearest = event => {
      const bounds = svg.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const scale = Math.min(bounds.width / width, bounds.height / 200);
      const x = (event.clientX - bounds.left - (bounds.width - width * scale) / 2) / scale;
        if (slopes) {
        const exactPoint = coords.reduce((best, point) =>
          Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best);
        if (Math.abs(exactPoint.x - x) <= 3) { showPoint(exactPoint); return; }
        const rawCapacity = minPosition + Math.max(0, Math.min(1, (x - 60) / usableWidth)) * (maxPosition - minPosition);
        const rounded = Math.round(rawCapacity);
        const capacity = rounded >= minPosition && rounded <= maxPosition ? rounded : rawCapacity;
        showCapacity(capacity);
        return;
      }
      if (intervalAxis && maxPosition > minPosition) {
        const capacity = minPosition + Math.max(0, Math.min(1, (x - 60) / usableWidth)) * (maxPosition - minPosition);
        const index = points.findIndex(point => capacity >= point.start && capacity < point.end);
        if (index >= 0) { showPoint(coords[index]); showHover(x, coords[index].y, `${coords[index].label}: ${format(coords[index].value)} ${currency}`); return; }
        if (capacity === maxPosition) { showPoint(coords[coords.length - 1]); showHover(x, coords[coords.length - 1].y, `${coords[coords.length - 1].label}: ${format(coords[coords.length - 1].value)} ${currency}`); return; }
      }
      const nearest = coords.reduce((best, point) =>
        Math.abs(point.x - x) < Math.abs(best.x - x) ? point : best);
      showPoint(nearest);
      showHover(nearest.x, nearest.y, `${nearest.label}: ${format(nearest.value)} ${currency}`);
    };
    svg.addEventListener("pointermove", showNearest);
    svg.addEventListener("pointerdown", showNearest);
    svg.addEventListener("pointerleave", event => {
      if (event.pointerType !== "touch") hover.style.display = "none";
    });
    const zero = document.createElementNS(ns, "line");
    zero.setAttribute("x1", "60");
    zero.setAttribute("x2", width - 60);
    zero.setAttribute("y1", 145 - (0 - minimum) * 105 / spread);
    zero.setAttribute("y2", 145 - (0 - minimum) * 105 / spread);
    svg.appendChild(zero);
    let gradientId = null;
    if (slopes) {
      const path = document.createElementNS(ns, "path");
      let d = `M ${coords[0].x} ${coords[0].y}`;
      for (let index = 0; index < coords.length - 1; index++) {
        const dx = (coords[index + 1].x - coords[index].x) / 3;
        const capacityStep = (points[index + 1].position - points[index].position) / 3;
        const valueScale = 105 / spread;
        d += ` C ${coords[index].x + dx} ${coords[index].y - slopes[index] * capacityStep * valueScale}` +
          ` ${coords[index + 1].x - dx} ${coords[index + 1].y + slopes[index + 1] * capacityStep * valueScale}` +
          ` ${coords[index + 1].x} ${coords[index + 1].y}`;
      }
      path.setAttribute("d", d);
      const low = Math.min(...points.map(point => point.value));
      const high = Math.max(...points.map(point => point.value));
      const segmentLow = segmentRates && Math.min(...segmentRates);
      const segmentHigh = segmentRates && Math.max(...segmentRates);
      const colorBySegment = segmentRates && segmentRates.length === points.length - 1 &&
        segmentRates.every(Number.isFinite) && segmentHigh > segmentLow;
      if ((colorByValue && high > low) || colorBySegment) {
        const defs = document.createElementNS(ns, "defs");
        const gradient = document.createElementNS(ns, "linearGradient");
        gradientId = `battmind-profit-gradient-${++whatIfGradientId}`;
        gradient.setAttribute("id", gradientId);
        gradient.setAttribute("gradientUnits", "userSpaceOnUse");
        const stops = [];
        if (colorBySegment) {
          gradient.setAttribute("x1", coords[0].x);
          gradient.setAttribute("x2", coords[coords.length - 1].x);
          gradient.setAttribute("y1", "0");
          gradient.setAttribute("y2", "0");
          for (let index = 0; index < segmentRates.length; index++) {
            const ratio = (segmentRates[index] - segmentLow) / (segmentHigh - segmentLow);
            const color = [215, 92, 80].map((component, i) => Math.round(component + ([54, 162, 105][i] - component) * ratio));
            const left = 100 * (coords[index].x - coords[0].x) / (coords[coords.length - 1].x - coords[0].x);
            const right = 100 * (coords[index + 1].x - coords[0].x) / (coords[coords.length - 1].x - coords[0].x);
            stops.push([`${left}%`, `rgb(${color.join(", ")})`], [`${right}%`, `rgb(${color.join(", ")})`]);
          }
        } else {
          gradient.setAttribute("x1", "0");
          gradient.setAttribute("x2", "0");
          gradient.setAttribute("y1", 145 - (low - minimum) * 105 / spread);
          gradient.setAttribute("y2", 145 - (high - minimum) * 105 / spread);
          stops.push(["0%", "rgb(215, 92, 80)"], ["100%", "rgb(54, 162, 105)"]);
        }
        for (const [offset, color] of stops) {
          const stop = document.createElementNS(ns, "stop");
          stop.setAttribute("offset", offset);
          stop.setAttribute("stop-color", color);
          gradient.appendChild(stop);
        }
        defs.appendChild(gradient);
        svg.appendChild(defs);
        path.style.stroke = `url(#${gradientId})`;
      }
      svg.appendChild(path);
    } else if (connect) {
      const line = document.createElementNS(ns, "polyline");
      line.setAttribute("points", coords.map(point => `${point.x},${point.y}`).join(" "));
      svg.appendChild(line);
    }
    for (const point of coords) {
      const dot = document.createElementNS(ns, "circle");
      if (point.best) dot.setAttribute("class", "best-point");
      if (gradientId && colorByValue) dot.style.fill = `url(#${gradientId})`;
      dot.setAttribute("cx", point.x);
      dot.setAttribute("cy", point.y);
      dot.setAttribute("r", "4");
      const tooltip = document.createElementNS(ns, "title");
      tooltip.textContent = `${point.label}: ${format(point.value)} ${currency}`;
      dot.appendChild(tooltip);
      svg.appendChild(dot);
      const valueLabel = document.createElementNS(ns, "text");
      valueLabel.setAttribute("x", point.x);
      valueLabel.setAttribute("y", point.y - 9);
      valueLabel.setAttribute("text-anchor", "middle");
      valueLabel.textContent = format(point.value);
      svg.appendChild(valueLabel);
      const xLabel = document.createElementNS(ns, "text");
      xLabel.setAttribute("x", point.x);
      xLabel.setAttribute("y", "172");
      xLabel.setAttribute("text-anchor", "middle");
      xLabel.textContent = point.label;
      svg.appendChild(xLabel);
    }
    for (const [value, y] of [[maximum, 40], [minimum, 145]]) {
      const axis = document.createElementNS(ns, "text");
      axis.setAttribute("x", "55");
      axis.setAttribute("y", y);
      axis.setAttribute("text-anchor", "end");
      axis.textContent = format(value);
      svg.appendChild(axis);
    }
    svg.appendChild(hover);
    scroll.appendChild(svg);
  }
}

if (!customElements.get("battmind-what-if")) {
  customElements.define("battmind-what-if", BattMindWhatIfCard);
}
