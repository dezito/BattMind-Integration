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
const whatIfRuleSwitches = [
  "optimizer_control",
  "use_midnight_battery_level",
  "solar_charging",
  "enhance_battery_efficiency",
  "cheapest_hour_fill_planner",
  "cheapest_hour_fill_up",
  "most_expensive_planner",
  "needed_before_max_level_planner",
  "negative_price_planner",
  "grid_charge_to_sell",
  "most_profit",
  "sell_excess_kwh_available",
  "force_powerwall_max_hours_old_discharge",
  "discharge_before_solar",
  "only_discharge_on_profit",
  "prioritize_discharge_hours_by_energy_cost",
  "optimize_solar_charging"
];
const whatIfRuleLabels = {
  "da": {
    "title": "Aktive regler og indstillinger",
    "edit_title": "Regler og indstillinger i scenariet",
    "edit_note": "Standard er de nuværende indstillinger. Kun værdier, du ændrer, bruges i scenariet; Current bruger altid de nuværende.",
    "edit_loading": "Henter nuværende indstillinger …",
    "edit_unavailable": "Nuværende indstillinger kunne ikke hentes; reglerne kan ikke ændres.",
    "edit_unsupported": "ikke understøttet",
    "edit_reset": "Nulstil til nuværende",
    "setting": "Indstilling",
    "on": "Til",
    "mode": "Styring",
    "optimizer": "Optimering",
    "rule_mode": "Regler",
    "note": "Værdierne er fra simulationen. Aktiverede regler er mulige at bruge, men har ikke nødvendigvis været anvendt. Stregen betyder inaktiv eller uden batteri.",
    "unavailable": "Regelværdier mangler i dette gemte resultat. Kør simulationen igen for at se dem.",
    "names": {
      "optimizer_control": "Styr batteriet med optimering af hele perioden",
      "use_midnight_battery_level": "Brug midnat batteriniveau",
      "solar_charging": "Solcelleoverskud til opladning",
      "enhance_battery_efficiency": "Forbedr batteriets virkningsgrad",
      "cheapest_hour_fill_planner": "Ladning: Billigste perioder planlægger",
      "cheapest_hour_fill_up": "Ladning: Fyld op i billigste periode",
      "most_expensive_planner": "Ladning: Dyreste perioder planlægger",
      "needed_before_max_level_planner": "Ladning: Nødvendig før max niveau planlægger",
      "negative_price_planner": "Ladning: Negative priser",
      "grid_charge_to_sell": "Ladning: Køb for at sælge",
      "most_profit": "Afladning: Maksimer profit",
      "sell_excess_kwh_available": "Afladning: Sælg overskydende kWh tilgængelig",
      "force_powerwall_max_hours_old_discharge": "Tvangsafladning: Maks. timer gamle kwh",
      "discharge_before_solar": "Afladning: Før solen",
      "only_discharge_on_profit": "Afladning: Kun ved profit",
      "prioritize_discharge_hours_by_energy_cost": "Afladning: Prioriter efter energikost",
      "optimize_solar_charging": "Optimer solcelleopladning",
      "usage_reserve": "Reserve til huset (sælges ikke)",
      "min_profit_per_kwh": "Min. profit per kWh (afladning)",
      "min_total_profit": "Min. total profit (salg til nettet)",
      "min_sell_kwh": "Min. overskydende kWh til salg",
      "cheap_price_periods": "Billige prisperioder",
      "cheapest_price_rise_threshold": "Stigning for billigste pris",
      "cheap_price_period_rise_threshold": "Stigning for billige prisperioder",
      "powerwall_max_hours_old": "Maks. timer gamle kWh i husbatteri",
      "exclude_sell_hours": "Ekskluder salgstimer"
    },
    "groups": {
      "charging": "Ladning",
      "discharging": "Afladning",
      "selling": "Salg",
      "shared": "Fælles"
    }
  },
  "de": {
    "title": "Aktive Regeln und Einstellungen",
    "edit_title": "Regeln und Einstellungen im Szenario",
    "edit_note": "Standard sind die aktuellen Einstellungen. Nur geänderte Werte werden im Szenario verwendet; Current nutzt immer die aktuellen.",
    "edit_loading": "Aktuelle Einstellungen werden geladen …",
    "edit_unavailable": "Die aktuellen Einstellungen konnten nicht geladen werden; die Regeln sind nicht änderbar.",
    "edit_unsupported": "nicht unterstützt",
    "edit_reset": "Auf aktuelle zurücksetzen",
    "setting": "Einstellung",
    "on": "Ein",
    "mode": "Steuerung",
    "optimizer": "Optimierung",
    "rule_mode": "Regeln",
    "note": "Die Werte stammen aus der Simulation. Aktivierte Regeln können verwendet worden sein, müssen aber nicht ausgeführt worden sein. Der Strich bedeutet inaktiv oder ohne Batterie.",
    "unavailable": "Regelwerte fehlen in diesem gespeicherten Ergebnis. Simulation erneut ausführen, um sie anzuzeigen.",
    "names": {
      "optimizer_control": "Batterie mit Optimierung des ganzen Zeitraums steuern",
      "use_midnight_battery_level": "Mitternachts-Batteriestand verwenden",
      "solar_charging": "Solarüberschuss-Laden",
      "enhance_battery_efficiency": "Batterieeffizienz verbessern",
      "cheapest_hour_fill_planner": "Laden: Günstigste Zeiträume",
      "cheapest_hour_fill_up": "Laden: In der günstigsten Zeit vollständig laden",
      "most_expensive_planner": "Laden: Teuerste Zeiträume",
      "needed_before_max_level_planner": "Laden: Planer vor maximalem Niveau",
      "negative_price_planner": "Laden: Negative Preise",
      "grid_charge_to_sell": "Laden: Kaufen um zu verkaufen",
      "most_profit": "Entladen: Gewinn maximieren",
      "sell_excess_kwh_available": "Entladen: Überschüssige kWh verkaufen",
      "force_powerwall_max_hours_old_discharge": "Erzwungene Entladung: Max. Alter der kWh in Stunden",
      "discharge_before_solar": "Entladen: Vor der Sonne",
      "only_discharge_on_profit": "Entladen: Nur bei Gewinn",
      "prioritize_discharge_hours_by_energy_cost": "Entladen: Nach Energiekosten priorisieren",
      "optimize_solar_charging": "Solarladung optimieren",
      "usage_reserve": "Reserve für das Haus (nicht verkaufen)",
      "min_profit_per_kwh": "Min. Gewinn pro kWh (Entladen)",
      "min_total_profit": "Min. Gesamtgewinn (Verkauf ins Netz)",
      "min_sell_kwh": "Min. überschüssige kWh zum Verkauf",
      "cheap_price_periods": "Günstige Preiszeiträume",
      "cheapest_price_rise_threshold": "Anstieg für niedrigsten Preis",
      "cheap_price_period_rise_threshold": "Anstieg für günstige Zeiträume",
      "powerwall_max_hours_old": "Max. Alter der kWh in der Hausbatterie",
      "exclude_sell_hours": "Verkaufsstunden ausschließen"
    },
    "groups": {
      "charging": "Laden",
      "discharging": "Entladen",
      "selling": "Verkauf",
      "shared": "Gemeinsam"
    }
  },
  "en": {
    "title": "Active rules and settings",
    "edit_title": "Rules and settings in the scenario",
    "edit_note": "The default is the current settings. Only values you change are used in the scenario; Current always uses the current ones.",
    "edit_loading": "Loading current settings …",
    "edit_unavailable": "The current settings could not be loaded; the rules cannot be changed.",
    "edit_unsupported": "not supported",
    "edit_reset": "Reset to current",
    "setting": "Setting",
    "on": "On",
    "mode": "Control",
    "optimizer": "Optimization",
    "rule_mode": "Rules",
    "note": "Values are from the simulation. Enabled rules may be used but did not necessarily execute. A dash means inactive or no battery.",
    "unavailable": "Rule values are missing from this saved result. Run the simulation again to see them.",
    "names": {
      "optimizer_control": "Control the battery with the whole period optimisation",
      "use_midnight_battery_level": "Use midnight battery level",
      "solar_charging": "Solar surplus charging",
      "enhance_battery_efficiency": "Enhance battery efficiency",
      "cheapest_hour_fill_planner": "Charging: Cheapest periods planner",
      "cheapest_hour_fill_up": "Charging: Fill up during cheapest period",
      "most_expensive_planner": "Charging: Most expensive periods planner",
      "needed_before_max_level_planner": "Charging: Required before max level planner",
      "negative_price_planner": "Charging: Negative prices",
      "grid_charge_to_sell": "Charging: Buy to sell",
      "most_profit": "Discharging: Maximise profit",
      "sell_excess_kwh_available": "Discharging: Sell excess kWh",
      "force_powerwall_max_hours_old_discharge": "Forced discharge: Max. kWh age in hours",
      "discharge_before_solar": "Discharging: Before the sun",
      "only_discharge_on_profit": "Discharging: Profit only",
      "prioritize_discharge_hours_by_energy_cost": "Discharging: Prioritise by energy cost",
      "optimize_solar_charging": "Optimise solar charging",
      "usage_reserve": "Reserve for house use (not sold)",
      "min_profit_per_kwh": "Min. profit per kWh (discharging)",
      "min_total_profit": "Min. total profit (sale to the grid)",
      "min_sell_kwh": "Minimum excess kWh for export",
      "cheap_price_periods": "Cheap price periods",
      "cheapest_price_rise_threshold": "Increase for cheapest price",
      "cheap_price_period_rise_threshold": "Increase for cheap periods",
      "powerwall_max_hours_old": "Max. age of kWh in home battery",
      "exclude_sell_hours": "Exclude selling hours"
    },
    "groups": {
      "charging": "Charging",
      "discharging": "Discharging",
      "selling": "Selling",
      "shared": "Shared"
    }
  },
  "pl": {
    "title": "Aktywne reguły i ustawienia",
    "edit_title": "Reguły i ustawienia w scenariuszu",
    "edit_note": "Domyślnie są to bieżące ustawienia. W scenariuszu używane są tylko zmienione wartości; Current zawsze używa bieżących.",
    "edit_loading": "Wczytywanie bieżących ustawień …",
    "edit_unavailable": "Nie udało się wczytać bieżących ustawień; reguł nie można zmienić.",
    "edit_unsupported": "nieobsługiwane",
    "edit_reset": "Przywróć bieżące",
    "setting": "Ustawienie",
    "on": "Wł.",
    "mode": "Sterowanie",
    "optimizer": "Optymalizacja",
    "rule_mode": "Reguły",
    "note": "Wartości pochodzą z symulacji. Włączone reguły mogą być użyte, ale nie musiały zostać wykonane. Kreska oznacza regułę nieaktywną lub brak baterii.",
    "unavailable": "W zapisanym wyniku brakuje wartości reguł. Uruchom symulację ponownie, aby je zobaczyć.",
    "names": {
      "optimizer_control": "Steruj baterią optymalizacją całego okresu",
      "use_midnight_battery_level": "Użyj poziomu baterii o północy",
      "solar_charging": "Ładowanie z nadwyżki solarnej",
      "enhance_battery_efficiency": "Zwiększ wydajność baterii",
      "cheapest_hour_fill_planner": "Ładowanie: Najtańsze okresy",
      "cheapest_hour_fill_up": "Ładowanie: Doładuj w najtańszym okresie",
      "most_expensive_planner": "Ładowanie: Najdroższe okresy",
      "needed_before_max_level_planner": "Ładowanie: Planowanie przed osiągnięciem maksymalnego poziomu",
      "negative_price_planner": "Ładowanie: Ujemne ceny",
      "grid_charge_to_sell": "Ładowanie: Kup, aby sprzedać",
      "most_profit": "Rozładowanie: Maksymalizuj zysk",
      "sell_excess_kwh_available": "Rozładowanie: Sprzedaj nadwyżkę kWh",
      "force_powerwall_max_hours_old_discharge": "Wymuszone rozładowanie: Maks. wiek kWh w godzinach",
      "discharge_before_solar": "Rozładowanie: Przed słońcem",
      "only_discharge_on_profit": "Rozładowanie: Tylko przy zysku",
      "prioritize_discharge_hours_by_energy_cost": "Rozładowanie: Priorytet wg kosztu energii",
      "optimize_solar_charging": "Optymalizuj ładowanie energią słoneczną",
      "usage_reserve": "Rezerwa dla domu (nie sprzedawaj)",
      "min_profit_per_kwh": "Min. zysk na kWh (rozładowanie)",
      "min_total_profit": "Min. całkowity zysk (sprzedaż do sieci)",
      "min_sell_kwh": "Min. nadwyżka kWh do sprzedaży",
      "cheap_price_periods": "Tanie okresy cenowe",
      "cheapest_price_rise_threshold": "Wzrost dla najniższej ceny",
      "cheap_price_period_rise_threshold": "Wzrost dla tanich okresów",
      "powerwall_max_hours_old": "Maks. wiek kWh w magazynie energii",
      "exclude_sell_hours": "Wyklucz godziny sprzedaży"
    },
    "groups": {
      "charging": "Ładowanie",
      "discharging": "Rozładowanie",
      "selling": "Sprzedaż",
      "shared": "Wspólne"
    }
  },
  "uk": {
    "title": "Активні правила та налаштування",
    "edit_title": "Правила та налаштування у сценарії",
    "edit_note": "За замовчуванням — поточні налаштування. У сценарії використовуються лише змінені значення; Current завжди використовує поточні.",
    "edit_loading": "Завантаження поточних налаштувань …",
    "edit_unavailable": "Не вдалося завантажити поточні налаштування; правила змінити неможливо.",
    "edit_unsupported": "не підтримується",
    "edit_reset": "Скинути до поточних",
    "setting": "Налаштування",
    "on": "Увімк.",
    "mode": "Керування",
    "optimizer": "Оптимізація",
    "rule_mode": "Правила",
    "note": "Значення взято із симуляції. Увімкнені правила можуть використовуватися, але не обов’язково виконувалися. Риска означає неактивне правило або відсутність батареї.",
    "unavailable": "У збереженому результаті немає значень правил. Запустіть симуляцію знову, щоб їх побачити.",
    "names": {
      "optimizer_control": "Керувати батареєю оптимізацією всього періоду",
      "use_midnight_battery_level": "Використовувати рівень батареї опівночі",
      "solar_charging": "Заряджання надлишком сонця",
      "enhance_battery_efficiency": "Підвищити ефективність акумулятора",
      "cheapest_hour_fill_planner": "Заряджання: Найдешевші періоди",
      "cheapest_hour_fill_up": "Заряджання: Повністю зарядити у найдешевший період",
      "most_expensive_planner": "Заряджання: Найдорожчі періоди",
      "needed_before_max_level_planner": "Заряджання: Планування перед досягненням максимального рівня",
      "negative_price_planner": "Заряджання: Від'ємні ціни",
      "grid_charge_to_sell": "Заряджання: Купити, щоб продати",
      "most_profit": "Розряджання: Максимізувати прибуток",
      "sell_excess_kwh_available": "Розряджання: Продати надлишкові кВт·год",
      "force_powerwall_max_hours_old_discharge": "Примусове розряджання: Макс. вік кВт·год у годинах",
      "discharge_before_solar": "Розряджання: Перед сонцем",
      "only_discharge_on_profit": "Розряджання: Лише при прибутку",
      "prioritize_discharge_hours_by_energy_cost": "Розряджання: Пріоритет за вартістю енергії",
      "optimize_solar_charging": "Оптимізувати заряджання від сонячної енергії",
      "usage_reserve": "Резерв для дому (не продавати)",
      "min_profit_per_kwh": "Мін. прибуток за кВт·год (розряджання)",
      "min_total_profit": "Мін. загальний прибуток (продаж у мережу)",
      "min_sell_kwh": "Мін. надлишок кВт·год для продажу",
      "cheap_price_periods": "Дешеві цінові періоди",
      "cheapest_price_rise_threshold": "Зростання для найнижчої ціни",
      "cheap_price_period_rise_threshold": "Зростання для дешевих періодів",
      "powerwall_max_hours_old": "Макс. вік кВт·год у домашній батареї",
      "exclude_sell_hours": "Виключити години продажу"
    },
    "groups": {
      "charging": "Заряджання",
      "discharging": "Розряджання",
      "selling": "Продаж",
      "shared": "Спільні"
    }
  }
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
    "module_insufficient_history": "Årlig gevinst og tilbagebetaling kræver en fuld periode på 365 dage samt tilstrækkelig historik og kendte historiske salgspriser og -omkostninger. Manglende timer kan estimeres ud fra samme eller nærmeste måned. En kortere periode bliver ikke omregnet til et år.",
    "module_coverage_requirement": "Årlig gevinst og tilbagebetaling kræver en fuld periode på 365 dage samt tilstrækkelig historik og kendte historiske salgspriser og -omkostninger. Manglende timer kan estimeres ud fra samme eller nærmeste måned. En kortere periode bliver ikke omregnet til et år.",
    "module_missing_sell_price": "Historiske salgspriser mangler. Gevinst er usikker, og tilbagebetaling kan ikke beregnes.",
    "module_missing_sell_cost": "Historiske salgsomkostninger mangler. Gevinst er usikker, og tilbagebetaling kan ikke beregnes.",
    "module_rerun": "Dette gemte resultat er fra en ældre version. Kør simulationen igen for at vurdere tilbagebetaling.",
    "module_dst_warning": "Skift til eller fra sommertid kan have nulstillet batteriniveauet i gennemspilningen; antal nulstillinger",
    "module_tariff_warning": "Timer med estimerede historiske tariffer",
    "module_current_cost_warning": "Årsestimatet bruger nuværende salgsomkostninger for historiske timer",
    "module_added": "Tilføjede moduler",
    "module_profit": "Historisk gevinst",
    "module_gain_baseline": "Gevinst i forhold til nuværende kapacitet",
    "module_incremental_gain": "Historisk gevinst fra forrige størrelse",
    "module_annualized_gain": "Modulet alene: årliggjort historisk gevinst",
    "module_estimated_annual_gain": "Modulet alene: ekstra gevinst pr. år",
    "module_cumulative_cost": "Samlet modulpris",
    "module_payback": "Modulet alene: enkel tilbagebetalingstid",
    "module_no_payback": "Ingen tilbagebetaling ved ikke-positiv gevinst",
    "module_years": "år",
    "module_estimate_note": "Estimatet udfylder manglende timer med gennemsnit fra samme måned eller nærmeste måned med nok data. Manglende sæsoner øger usikkerheden; det er ikke en prognose.",
    "module_estimated_hours": "Estimerede timer",
    "module_estimated_months": "Måneder estimeret med data fra andre måneder",
    "module_reliability_details": "Grundlag og forbehold",
    "module_estimate_unavailable": "Årlig gevinst og tilbagebetaling kræver en fuld periode på 365 dage samt tilstrækkelig historik og kendte historiske salgspriser og -omkostninger. Manglende timer kan estimeres ud fra samme eller nærmeste måned. En kortere periode bliver ikke omregnet til et år.",
    "module_monthly_title": "Månedlig fordeling: Nuværende, scenarie og forskel",
    "module_monthly_note": "Gevinst pr. kalendermåned i den valgte periode, så du kan se hvornår på året det giver mest. Det er ikke en prognose; måneder med få timer er ufuldstændige. Fed markerer bedste måned.",
    "module_monthly_month": "Måned",
    "module_payback_title": "Tilbagebetaling over tid: strømregning og batterikøb",
    "module_payback_note": "Grafen viser den samlede udgift over tid ved uændret årlig regning. Den grå linje er uden batteri og stiger med hele regningen. De øvrige linjer starter ved købsprisen for den batteristørrelse, der står i forklaringen, og stiger med regningen for den størrelse. Prikken viser, hvornår hele batteriet er tjent hjem mod slet intet batteri (i alt); forklaringen viser desuden, hvor lang tid det sidste modul alene tager. Alle tider tælles fra start. Grafen viser højst 30 år; prikker uden for det er ikke tegnet. Estimat ud fra historikken; ingen installation, finansiering, skatter eller fremtidige ændringer. Det nuværende batteri er prissat til samme pris pr. kWh som et modul.",
    "module_payback_current": "Nuværende batteri",
    "module_payback_none": "Uden batteri",
    "module_payback_axis": "Akkumuleret udgift",
    "module_whole_cost": "Hele batteriet: samlet pris",
    "module_whole_gain": "Hele batteriet: besparelse pr. år mod intet batteri",
    "module_whole_payback": "Hele batteriet: tilbagebetaling mod intet batteri",
    "module_in_total": "i alt",
    "module_extra_only": "sidste modul ({extra} kWh) alene",
    "module_summary_title": "Sådan læser du sammenligningen",
    "module_summary_current": "Dit nuværende batteri ({capacity} kWh) er prissat til ca. {price} {currency} og sparer ca. {gain} {currency} om året i forhold til slet intet batteri. Det tjener sig hjem efter {time}.",
    "module_summary_added": "Lægger du {modules} til ({capacity} kWh i alt), koster de ekstra {extra} kWh {price} {currency} og giver {gain} {currency} mere om året. De ekstra kWh alene tjener sig hjem efter {time}.",
    "module_summary_added_none": "Lægger du {modules} til ({capacity} kWh i alt), giver de ekstra {extra} kWh ikke mere om året, så de tjener sig ikke hjem.",
    "module_summary_total": "Hele {capacity} kWh-batteriet mod slet intet batteri er tjent hjem efter {time}. Det er hurtigere end de ekstra kWh alene, fordi dit nuværende batteri tjener sig hjem hurtigere og trækker gennemsnittet ned. Tallet svarer kun på, hvor lang tid det tager at tjene hele batteriet hjem, hvis du købte det i dag.",
    "module_summary_total_plain": "Hele {capacity} kWh-batteriet mod slet intet batteri er tjent hjem efter {time}.",
    "module_summary_advice": "Overvejer du at købe mere, så brug tiden for de ekstra kWh alene: dit nuværende batteri er allerede købt. Alle tal er historiske estimater, ikke garantier.",
    "module_payback_missing": "Grafen over tilbagebetaling kræver en ny kørsel af simulationen med flere moduler, årsestimat og en modulpris over 0.",
    "module_monthly_range_title": "Månedlig gevinst pr. batteristørrelse",
    "module_monthly_modules_title": "Månedlig ekstra gevinst pr. antal moduler (mod 0 ekstra moduler)",
    "module_graph_modules": "moduler",
    "module_graph_modules_few": "moduler",
    "module_graph_module": "modul",
    "module_graph_no_payback": "Ingen tilbagebetaling ved ikke-positiv gevinst",
    "module_estimate_details": "Tabellen har to dele. »Modulet alene« er det sidst tilføjede modul oven i forrige størrelse: brug den, hvis du overvejer at købe mere (enkel tilbagebetaling er prisen divideret med den ekstra årlige gevinst). »Hele batteriet« er nuværende batteri plus moduler mod slet intet batteri. Resultaterne er historiske estimater, ikke garantier.",
    "module_note": "Tabellen har to dele. »Modulet alene« er det sidst tilføjede modul oven i forrige størrelse: brug den, hvis du overvejer at købe mere (enkel tilbagebetaling er prisen divideret med den ekstra årlige gevinst). »Hele batteriet« er nuværende batteri plus moduler mod slet intet batteri. Resultaterne er historiske estimater, ikke garantier."
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
    "module_insufficient_history": "Jahresgewinn und Amortisation erfordern einen vollständigen Zeitraum von 365 Tagen, genügend Historie sowie bekannte historische Verkaufspreise und -kosten. Fehlende Stunden können anhand desselben oder des nächstgelegenen Monats geschätzt werden. Ein kürzerer Zeitraum wird nicht auf ein Jahr hochgerechnet.",
    "module_coverage_requirement": "Jahresgewinn und Amortisation erfordern einen vollständigen Zeitraum von 365 Tagen, genügend Historie sowie bekannte historische Verkaufspreise und -kosten. Fehlende Stunden können anhand desselben oder des nächstgelegenen Monats geschätzt werden. Ein kürzerer Zeitraum wird nicht auf ein Jahr hochgerechnet.",
    "module_missing_sell_price": "Historische Verkaufspreise fehlen. Der Gewinn ist unsicher und die Amortisation kann nicht berechnet werden.",
    "module_missing_sell_cost": "Historische Verkaufskosten fehlen. Der Gewinn ist unsicher und die Amortisation kann nicht berechnet werden.",
    "module_rerun": "Dieses gespeicherte Ergebnis stammt aus einer älteren Version. Simulation zur Beurteilung der Amortisation erneut ausführen.",
    "module_dst_warning": "Zeitumstellungen können den Batteriestand im Rücklauf zurückgesetzt haben; Anzahl der Rücksetzungen",
    "module_tariff_warning": "Stunden mit geschätzten historischen Tarifen",
    "module_current_cost_warning": "Jahresschätzung verwendet aktuelle Verkaufskosten für historische Stunden",
    "module_added": "Zusätzliche Module",
    "module_profit": "Historischer Gewinn",
    "module_gain_baseline": "Gewinn gegenüber aktueller Kapazität",
    "module_incremental_gain": "Historischer Gewinn gegenüber der vorherigen Größe",
    "module_annualized_gain": "Modul allein: annualisierter historischer Gewinn",
    "module_estimated_annual_gain": "Modul allein: zusätzlicher Gewinn pro Jahr",
    "module_cumulative_cost": "Kumulierte Modulkosten",
    "module_payback": "Modul allein: einfache Amortisationszeit",
    "module_no_payback": "Keine Amortisation bei nicht positivem Gewinn",
    "module_years": "Jahre",
    "module_estimate_note": "Fehlende Stunden werden mit Durchschnitten desselben oder des nächstgelegenen Monats mit genügend Daten geschätzt. Fehlende Jahreszeiten erhöhen die Unsicherheit; dies ist keine Prognose.",
    "module_estimated_hours": "Geschätzte Stunden",
    "module_estimated_months": "Mit anderen Monaten geschätzte Monate",
    "module_reliability_details": "Grundlage und Einschränkungen",
    "module_estimate_unavailable": "Jahresgewinn und Amortisation erfordern einen vollständigen Zeitraum von 365 Tagen, genügend Historie sowie bekannte historische Verkaufspreise und -kosten. Fehlende Stunden können anhand desselben oder des nächstgelegenen Monats geschätzt werden. Ein kürzerer Zeitraum wird nicht auf ein Jahr hochgerechnet.",
    "module_monthly_title": "Monatliche Verteilung: Aktuell, Szenario und Differenz",
    "module_monthly_note": "Gewinn pro Kalendermonat im gewählten Zeitraum, damit sichtbar wird, wann im Jahr es am meisten bringt. Das ist keine Prognose; Monate mit wenigen Stunden sind unvollständig. Fett markiert den besten Monat.",
    "module_monthly_month": "Monat",
    "module_payback_title": "Amortisation im Zeitverlauf: Stromrechnung und Batteriekauf",
    "module_payback_note": "Die Grafik zeigt die aufgelaufenen Kosten im Zeitverlauf bei konstanter Jahresrechnung. Die graue Linie gilt ohne Batterie und steigt mit der gesamten Rechnung. Die anderen Linien beginnen beim Kaufpreis der in der Legende genannten Batteriegröße und steigen mit der Rechnung für diese Größe. Der Punkt zeigt, wann sich die gesamte Batterie gegenüber gar keiner Batterie amortisiert hat (gesamt); die Legende zeigt zusätzlich, wie lange das letzte Modul allein braucht. Alle Zeiten werden ab Beginn gezählt. Die Grafik zeigt höchstens 30 Jahre; Punkte darüber hinaus werden nicht gezeichnet. Schätzung auf Basis der Historie; Installation, Finanzierung, Steuern und zukünftige Änderungen sind nicht enthalten. Die aktuelle Batterie wird mit demselben Preis pro kWh wie ein Modul angesetzt.",
    "module_payback_current": "Aktuelle Batterie",
    "module_payback_none": "Ohne Batterie",
    "module_payback_axis": "Aufgelaufene Kosten",
    "module_whole_cost": "Gesamte Batterie: Gesamtpreis",
    "module_whole_gain": "Gesamte Batterie: Ersparnis pro Jahr gegenüber ohne Batterie",
    "module_whole_payback": "Gesamte Batterie: Amortisation gegenüber ohne Batterie",
    "module_in_total": "gesamt",
    "module_extra_only": "letztes Modul ({extra} kWh) allein",
    "module_summary_title": "So lesen Sie den Vergleich",
    "module_summary_current": "Ihre aktuelle Batterie ({capacity} kWh) ist mit ca. {price} {currency} angesetzt und spart ca. {gain} {currency} pro Jahr gegenüber gar keiner Batterie. Sie amortisiert sich nach {time}.",
    "module_summary_added": "Wenn Sie {modules} hinzufügen ({capacity} kWh gesamt), kosten die zusätzlichen {extra} kWh {price} {currency} und bringen {gain} {currency} mehr pro Jahr. Die zusätzlichen kWh allein amortisieren sich nach {time}.",
    "module_summary_added_none": "Wenn Sie {modules} hinzufügen ({capacity} kWh gesamt), bringen die zusätzlichen {extra} kWh nicht mehr pro Jahr und amortisieren sich daher nicht.",
    "module_summary_total": "Die gesamte {capacity}-kWh-Batterie gegenüber gar keiner Batterie amortisiert sich nach {time}. Das ist schneller als die zusätzlichen kWh allein, weil sich Ihre aktuelle Batterie schneller amortisiert und den Durchschnitt senkt. Die Zahl beantwortet nur, wie lange es dauert, die gesamte Batterie zu amortisieren, wenn Sie sie heute kaufen würden.",
    "module_summary_total_plain": "Die gesamte {capacity}-kWh-Batterie gegenüber gar keiner Batterie amortisiert sich nach {time}.",
    "module_summary_advice": "Wenn Sie mehr kaufen möchten, nutzen Sie die Zeit der zusätzlichen kWh allein: Ihre aktuelle Batterie ist bereits gekauft. Alle Zahlen sind historische Schätzungen, keine Garantien.",
    "module_payback_missing": "Das Amortisationsdiagramm erfordert einen neuen Simulationslauf mit mehreren Modulen, Jahresschätzung und einem Modulpreis über 0.",
    "module_monthly_range_title": "Monatlicher Gewinn pro Batteriegröße",
    "module_monthly_modules_title": "Monatlicher Zusatzgewinn pro Modulanzahl (gegenüber 0 zusätzlichen Modulen)",
    "module_graph_modules": "Module",
    "module_graph_modules_few": "Module",
    "module_graph_module": "Modul",
    "module_graph_no_payback": "Keine Amortisation bei nicht positivem Gewinn",
    "module_estimate_details": "Die Tabelle hat zwei Teile. „Modul allein“ ist das zuletzt hinzugefügte Modul auf der vorherigen Größe: Nutzen Sie es, wenn Sie mehr kaufen möchten (einfache Amortisation ist der Preis geteilt durch den zusätzlichen Jahresgewinn). „Gesamte Batterie“ ist die aktuelle Batterie plus Module gegenüber gar keiner Batterie. Ergebnisse sind historische Schätzungen, keine Garantien.",
    "module_note": "Die Tabelle hat zwei Teile. „Modul allein“ ist das zuletzt hinzugefügte Modul auf der vorherigen Größe: Nutzen Sie es, wenn Sie mehr kaufen möchten (einfache Amortisation ist der Preis geteilt durch den zusätzlichen Jahresgewinn). „Gesamte Batterie“ ist die aktuelle Batterie plus Module gegenüber gar keiner Batterie. Ergebnisse sind historische Schätzungen, keine Garantien."
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
    "module_insufficient_history": "Annual gain and payback require a full 365-day window, sufficient history, and known historical sell prices and costs. Missing hours can be estimated from the same or nearest month. A shorter period is not extrapolated to a year.",
    "module_coverage_requirement": "Annual gain and payback require a full 365-day window, sufficient history, and known historical sell prices and costs. Missing hours can be estimated from the same or nearest month. A shorter period is not extrapolated to a year.",
    "module_missing_sell_price": "Historical sell prices are missing. Profit is uncertain and payback cannot be calculated.",
    "module_missing_sell_cost": "Historical selling costs are missing. Profit is uncertain and payback cannot be calculated.",
    "module_rerun": "This saved result is from an older version. Run the simulation again to assess payback.",
    "module_dst_warning": "Daylight-saving transitions may have reset the battery level during replay; number of resets",
    "module_tariff_warning": "Hours with estimated historical tariffs",
    "module_current_cost_warning": "Annual estimate uses current selling costs for historical hours",
    "module_added": "Modules added",
    "module_profit": "Historical profit",
    "module_gain_baseline": "Gain vs current capacity",
    "module_incremental_gain": "Historical gain from previous size",
    "module_annualized_gain": "Module alone: annualized historical gain",
    "module_estimated_annual_gain": "Module alone: extra gain per year",
    "module_cumulative_cost": "Cumulative module cost",
    "module_payback": "Module alone: simple payback",
    "module_no_payback": "No payback for non-positive gain",
    "module_years": "years",
    "module_estimate_note": "Missing hours are estimated using averages from the same month or the nearest month with enough data. Missing seasons increase uncertainty; this is not a forecast.",
    "module_estimated_hours": "Estimated hours",
    "module_estimated_months": "Months estimated using other months",
    "module_reliability_details": "Basis and limitations",
    "module_estimate_unavailable": "Annual gain and payback require a full 365-day window, sufficient history, and known historical sell prices and costs. Missing hours can be estimated from the same or nearest month. A shorter period is not extrapolated to a year.",
    "module_monthly_title": "Monthly breakdown: Current, scenario and difference",
    "module_monthly_note": "Gain per calendar month in the selected period, so you can see when in the year it pays most. This is not a forecast; months with few hours are incomplete. Bold marks the best month.",
    "module_monthly_month": "Month",
    "module_payback_title": "Payback over time: electricity bill and battery purchase",
    "module_payback_note": "The graph shows the accumulated cost over time at a constant yearly bill. The grey line is without a battery and rises with the whole bill. The other lines start at the purchase price of the battery size in the legend and rise with the bill for that size. The dot shows when the whole battery has paid back against no battery (total); the legend also shows how long the last module takes on its own. All times are counted from the start. The graph shows at most 30 years; dots beyond that are not drawn. Estimate from history; installation, finance, taxes and future changes are excluded. The current battery is priced at the same price per kWh as a module.",
    "module_payback_current": "Current battery",
    "module_payback_none": "Without battery",
    "module_payback_axis": "Accumulated cost",
    "module_whole_cost": "Whole battery: total price",
    "module_whole_gain": "Whole battery: saving per year vs no battery",
    "module_whole_payback": "Whole battery: payback vs no battery",
    "module_in_total": "total",
    "module_extra_only": "last module ({extra} kWh) alone",
    "module_summary_title": "How to read the comparison",
    "module_summary_current": "Your current battery ({capacity} kWh) is priced at about {price} {currency} and saves about {gain} {currency} a year compared with no battery at all. It pays back after {time}.",
    "module_summary_added": "If you add {modules} ({capacity} kWh in total), the extra {extra} kWh cost {price} {currency} and earn {gain} {currency} more a year. The extra kWh alone pay back after {time}.",
    "module_summary_added_none": "If you add {modules} ({capacity} kWh in total), the extra {extra} kWh do not earn more per year, so they never pay back.",
    "module_summary_total": "The whole {capacity} kWh battery against no battery at all pays back after {time}. That is faster than the extra kWh alone because your current battery pays back faster and pulls the average down. The figure only answers how long the whole battery would take to pay back if you bought it today.",
    "module_summary_total_plain": "The whole {capacity} kWh battery against no battery at all pays back after {time}.",
    "module_summary_advice": "If you are thinking of buying more, use the time for the extra kWh alone: your current battery is already bought. All figures are historical estimates, not guarantees.",
    "module_payback_missing": "The payback graph requires a new simulation run with several modules, an annual estimate and a module price above 0.",
    "module_monthly_range_title": "Monthly gain per battery size",
    "module_monthly_modules_title": "Monthly extra gain per number of modules (versus 0 extra modules)",
    "module_graph_modules": "modules",
    "module_graph_modules_few": "modules",
    "module_graph_module": "module",
    "module_graph_no_payback": "No payback for non-positive gain",
    "module_estimate_details": "The table has two parts. “Module alone” is the last added module on top of the previous size: use it if you are thinking of buying more (simple payback is the price divided by the extra yearly gain). “Whole battery” is the current battery plus modules against no battery at all. Results are historical estimates, not guarantees.",
    "module_note": "The table has two parts. “Module alone” is the last added module on top of the previous size: use it if you are thinking of buying more (simple payback is the price divided by the extra yearly gain). “Whole battery” is the current battery plus modules against no battery at all. Results are historical estimates, not guarantees."
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
    "module_insufficient_history": "Roczny zysk i okres zwrotu wymagają pełnego okresu 365 dni, wystarczającej historii oraz znanych historycznych cen i kosztów sprzedaży. Brakujące godziny można oszacować na podstawie tego samego lub najbliższego miesiąca. Krótszy okres nie jest przeliczany na rok.",
    "module_coverage_requirement": "Roczny zysk i okres zwrotu wymagają pełnego okresu 365 dni, wystarczającej historii oraz znanych historycznych cen i kosztów sprzedaży. Brakujące godziny można oszacować na podstawie tego samego lub najbliższego miesiąca. Krótszy okres nie jest przeliczany na rok.",
    "module_missing_sell_price": "Brakuje historycznych cen sprzedaży. Zysk jest niepewny i nie można obliczyć okresu zwrotu.",
    "module_missing_sell_cost": "Brakuje historycznych kosztów sprzedaży. Zysk jest niepewny i nie można obliczyć okresu zwrotu.",
    "module_rerun": "Ten zapisany wynik pochodzi ze starszej wersji. Uruchom symulację ponownie, aby ocenić okres zwrotu.",
    "module_dst_warning": "Zmiany czasu mogły zresetować poziom baterii podczas odtwarzania; liczba resetów",
    "module_tariff_warning": "Godziny z szacowanymi historycznymi taryfami",
    "module_current_cost_warning": "Szacunek roczny używa bieżących kosztów sprzedaży dla godzin historycznych",
    "module_added": "Dodane moduły",
    "module_profit": "Historyczny zysk",
    "module_gain_baseline": "Zysk względem obecnej pojemności",
    "module_incremental_gain": "Historyczny zysk względem poprzedniej wielkości",
    "module_annualized_gain": "Sam moduł: roczny przeliczony zysk historyczny",
    "module_estimated_annual_gain": "Sam moduł: dodatkowy zysk rocznie",
    "module_cumulative_cost": "Łączny koszt modułów",
    "module_payback": "Sam moduł: prosty okres zwrotu",
    "module_no_payback": "Brak zwrotu przy zysku nie większym od zera",
    "module_years": "lata",
    "module_estimate_note": "Brakujące godziny są szacowane na podstawie średnich z tego samego lub najbliższego miesiąca z wystarczającymi danymi. Brakujące pory roku zwiększają niepewność; nie jest to prognoza.",
    "module_estimated_hours": "Szacowane godziny",
    "module_estimated_months": "Miesiące oszacowane na podstawie innych miesięcy",
    "module_reliability_details": "Podstawa i ograniczenia",
    "module_estimate_unavailable": "Roczny zysk i okres zwrotu wymagają pełnego okresu 365 dni, wystarczającej historii oraz znanych historycznych cen i kosztów sprzedaży. Brakujące godziny można oszacować na podstawie tego samego lub najbliższego miesiąca. Krótszy okres nie jest przeliczany na rok.",
    "module_monthly_title": "Podział miesięczny: obecnie, scenariusz i różnica",
    "module_monthly_note": "Zysk w każdym miesiącu kalendarzowym wybranego okresu, aby zobaczyć, kiedy w roku jest największy. To nie jest prognoza; miesiące z małą liczbą godzin są niepełne. Pogrubienie oznacza najlepszy miesiąc.",
    "module_monthly_month": "Miesiąc",
    "module_payback_title": "Zwrot w czasie: rachunek za prąd i zakup baterii",
    "module_payback_note": "Wykres pokazuje skumulowany koszt w czasie przy stałym rocznym rachunku. Szara linia oznacza brak baterii i rośnie o cały rachunek. Pozostałe linie zaczynają się od ceny zakupu rozmiaru baterii podanego w legendzie i rosną o rachunek dla tego rozmiaru. Kropka pokazuje, kiedy cała bateria zwróciła się wobec braku baterii (łącznie); legenda pokazuje też, ile czasu potrzebuje sam ostatni moduł. Wszystkie czasy liczone są od początku. Wykres obejmuje maksymalnie 30 lat; kropki poza tym zakresem nie są rysowane. Szacunek na podstawie historii; montaż, finansowanie, podatki i przyszłe zmiany nie są uwzględnione. Obecna bateria jest wyceniana po tej samej cenie za kWh co moduł.",
    "module_payback_current": "Obecna bateria",
    "module_payback_none": "Bez baterii",
    "module_payback_axis": "Skumulowany koszt",
    "module_whole_cost": "Cała bateria: cena łączna",
    "module_whole_gain": "Cała bateria: oszczędność rocznie wobec braku baterii",
    "module_whole_payback": "Cała bateria: zwrot wobec braku baterii",
    "module_in_total": "łącznie",
    "module_extra_only": "ostatni moduł ({extra} kWh) osobno",
    "module_summary_title": "Jak czytać porównanie",
    "module_summary_current": "Twoja obecna bateria ({capacity} kWh) jest wyceniona na ok. {price} {currency} i oszczędza ok. {gain} {currency} rocznie w porównaniu z brakiem baterii. Zwraca się po {time}.",
    "module_summary_added": "Jeśli dodasz {modules} (razem {capacity} kWh), dodatkowe {extra} kWh kosztują {price} {currency} i dają {gain} {currency} więcej rocznie. Same dodatkowe kWh zwracają się po {time}.",
    "module_summary_added_none": "Jeśli dodasz {modules} (razem {capacity} kWh), dodatkowe {extra} kWh nie dają więcej rocznie, więc się nie zwrócą.",
    "module_summary_total": "Cała bateria {capacity} kWh w porównaniu z brakiem baterii zwraca się po {time}. To szybciej niż same dodatkowe kWh, bo Twoja obecna bateria zwraca się szybciej i obniża średnią. Ta liczba odpowiada tylko na pytanie, jak długo zwracałaby się cała bateria, gdybyś kupił ją dzisiaj.",
    "module_summary_total_plain": "Cała bateria {capacity} kWh w porównaniu z brakiem baterii zwraca się po {time}.",
    "module_summary_advice": "Jeśli rozważasz zakup więcej, użyj czasu dla samych dodatkowych kWh: Twoja obecna bateria jest już kupiona. Wszystkie liczby to historyczne szacunki, nie gwarancje.",
    "module_payback_missing": "Wykres zwrotu wymaga ponownego uruchomienia symulacji z kilkoma modułami, szacunkiem rocznym i ceną modułu powyżej 0.",
    "module_monthly_range_title": "Miesięczny zysk dla każdej pojemności baterii",
    "module_monthly_modules_title": "Miesięczny dodatkowy zysk dla liczby modułów (względem 0 dodatkowych modułów)",
    "module_graph_modules": "modułów",
    "module_graph_modules_few": "moduły",
    "module_graph_module": "moduł",
    "module_graph_no_payback": "Brak zwrotu przy niedodatnim zysku",
    "module_estimate_details": "Tabela ma dwie części. „Sam moduł” to ostatnio dodany moduł na poprzednim rozmiarze: użyj go, jeśli rozważasz zakup więcej (prosty okres zwrotu to cena podzielona przez dodatkowy roczny zysk). „Cała bateria” to obecna bateria plus moduły wobec braku baterii. Wyniki są historycznymi szacunkami, a nie gwarancją.",
    "module_note": "Tabela ma dwie części. „Sam moduł” to ostatnio dodany moduł na poprzednim rozmiarze: użyj go, jeśli rozważasz zakup więcej (prosty okres zwrotu to cena podzielona przez dodatkowy roczny zysk). „Cała bateria” to obecna bateria plus moduły wobec braku baterii. Wyniki są historycznymi szacunkami, a nie gwarancją."
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
    "module_insufficient_history": "Річний прибуток та окупність потребують повного періоду 365 днів, достатньої історії та відомих історичних цін і витрат на продаж. Пропущені години можна оцінити за тим самим або найближчим місяцем. Коротший період не перераховується на рік.",
    "module_coverage_requirement": "Річний прибуток та окупність потребують повного періоду 365 днів, достатньої історії та відомих історичних цін і витрат на продаж. Пропущені години можна оцінити за тим самим або найближчим місяцем. Коротший період не перераховується на рік.",
    "module_missing_sell_price": "Історичні ціни продажу відсутні. Прибуток невизначений, а окупність неможливо обчислити.",
    "module_missing_sell_cost": "Історичні витрати на продаж відсутні. Прибуток невизначений, а окупність неможливо обчислити.",
    "module_rerun": "Цей збережений результат отримано старішою версією. Запустіть симуляцію знову для оцінки окупності.",
    "module_dst_warning": "Перехід на літній або зимовий час міг скинути рівень заряду під час відтворення; кількість скидань",
    "module_tariff_warning": "Години з оціненими історичними тарифами",
    "module_current_cost_warning": "Річна оцінка використовує поточні витрати на продаж для історичних годин",
    "module_added": "Додані модулі",
    "module_profit": "Історичний прибуток",
    "module_gain_baseline": "Прибуток відносно поточної ємності",
    "module_incremental_gain": "Історичний прибуток відносно попереднього розміру",
    "module_annualized_gain": "Лише модуль: річний історичний прибуток",
    "module_estimated_annual_gain": "Лише модуль: додатковий прибуток на рік",
    "module_cumulative_cost": "Сукупна вартість модулів",
    "module_payback": "Лише модуль: простий строк окупності",
    "module_no_payback": "Немає окупності за непозитивного прибутку",
    "module_years": "років",
    "module_estimate_note": "Пропущені години оцінюються за середніми значеннями того самого або найближчого місяця з достатніми даними. Відсутні сезони збільшують невизначеність; це не прогноз.",
    "module_estimated_hours": "Оцінені години",
    "module_estimated_months": "Місяці, оцінені за іншими місяцями",
    "module_reliability_details": "Підстава та обмеження",
    "module_estimate_unavailable": "Річний прибуток та окупність потребують повного періоду 365 днів, достатньої історії та відомих історичних цін і витрат на продаж. Пропущені години можна оцінити за тим самим або найближчим місяцем. Коротший період не перераховується на рік.",
    "module_monthly_title": "Розподіл за місяцями: поточний, сценарій і різниця",
    "module_monthly_note": "Прибуток за кожен календарний місяць вибраного періоду, щоб побачити, коли протягом року він найбільший. Це не прогноз; місяці з малою кількістю годин неповні. Жирним позначено найкращий місяць.",
    "module_monthly_month": "Місяць",
    "module_payback_title": "Окупність у часі: рахунок за електроенергію та купівля батареї",
    "module_payback_note": "Графік показує накопичені витрати в часі за незмінного річного рахунку. Сіра лінія — без батареї, вона зростає на весь рахунок. Інші лінії починаються з ціни покупки розміру батареї, указаного в легенді, і зростають на рахунок для цього розміру. Крапка показує, коли вся батарея окупилася порівняно з відсутністю батареї (разом); легенда також показує, скільки часу потрібно лише останньому модулю. Усі часи відраховуються від початку. Графік охоплює не більше 30 років; крапки за його межами не малюються. Оцінка за історією; монтаж, фінансування, податки та майбутні зміни не враховано. Поточну батарею оцінено за тією самою ціною за kWh, що й модуль.",
    "module_payback_current": "Поточна батарея",
    "module_payback_none": "Без батареї",
    "module_payback_axis": "Накопичені витрати",
    "module_whole_cost": "Уся батарея: загальна ціна",
    "module_whole_gain": "Уся батарея: економія на рік порівняно з відсутністю батареї",
    "module_whole_payback": "Уся батарея: окупність порівняно з відсутністю батареї",
    "module_in_total": "разом",
    "module_extra_only": "останній модуль ({extra} kWh) окремо",
    "module_summary_title": "Як читати порівняння",
    "module_summary_current": "Ваша поточна батарея ({capacity} kWh) оцінена приблизно в {price} {currency} і заощаджує приблизно {gain} {currency} на рік порівняно з відсутністю батареї. Вона окупається через {time}.",
    "module_summary_added": "Якщо додати {modules} (разом {capacity} kWh), додаткові {extra} kWh коштують {price} {currency} і дають на {gain} {currency} більше на рік. Самі додаткові kWh окупаються через {time}.",
    "module_summary_added_none": "Якщо додати {modules} (разом {capacity} kWh), додаткові {extra} kWh не дають більше на рік, тому не окупаються.",
    "module_summary_total": "Уся батарея {capacity} kWh порівняно з відсутністю батареї окупається через {time}. Це швидше, ніж самі додаткові kWh, бо ваша поточна батарея окупається швидше й знижує середнє. Це число відповідає лише на питання, як довго окупалася б уся батарея, якби ви купили її сьогодні.",
    "module_summary_total_plain": "Уся батарея {capacity} kWh порівняно з відсутністю батареї окупається через {time}.",
    "module_summary_advice": "Якщо розглядаєте купівлю більше, використовуйте час для самих додаткових kWh: ваша поточна батарея вже куплена. Усі числа — історичні оцінки, а не гарантії.",
    "module_payback_missing": "Графік окупності потребує нового запуску симуляції з кількома модулями, річною оцінкою та ціною модуля більше 0.",
    "module_monthly_range_title": "Місячний прибуток для кожної ємності батареї",
    "module_monthly_modules_title": "Місячний додатковий прибуток за кількістю модулів (порівняно з 0 додатковими модулями)",
    "module_graph_modules": "модулів",
    "module_graph_modules_few": "модулі",
    "module_graph_module": "модуль",
    "module_graph_no_payback": "Немає окупності за недодатного прибутку",
    "module_estimate_details": "Таблиця має дві частини. «Лише модуль» — останній доданий модуль поверх попереднього розміру: використовуйте її, якщо розглядаєте купівлю більше (простий строк окупності — це ціна, поділена на додатковий річний прибуток). «Уся батарея» — поточна батарея плюс модулі порівняно з відсутністю батареї. Результати є історичними оцінками, а не гарантіями.",
    "module_note": "Таблиця має дві частини. «Лише модуль» — останній доданий модуль поверх попереднього розміру: використовуйте її, якщо розглядаєте купівлю більше (простий строк окупності — це ціна, поділена на додатковий річний прибуток). «Уся батарея» — поточна батарея плюс модулі порівняно з відсутністю батареї. Результати є історичними оцінками, а не гарантіями."
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
    this._ensureRuleDefaults();
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

  _ruleLabels() {
    const language = (this._hass?.language || "en").toLowerCase().split("-")[0];
    return whatIfRuleLabels[language] || whatIfRuleLabels.en;
  }

  async _ensureRuleDefaults() {
    if (this._ruleDefaults || this._ruleLoading || !this._hass || !this._config || !this._ruleStatus) return;
    this._ruleLoading = true;
    try {
      const settings = await this._hass.callWS({
        type: "battmind/what_if_settings", config_entry_id: this._config.config_entry_id,
      });
      if (!settings?.switches || !settings.numbers) throw new Error("no settings");
      this._ruleDefaults = settings;
      this._buildRuleFields();
    } catch (error) {
      if (this._ruleStatus) this._ruleStatus.textContent = this._ruleLabels().edit_unavailable;
    } finally {
      this._ruleLoading = false;
    }
  }

  _buildRuleFields() {
    const defaults = this._ruleDefaults;
    if (!defaults || !this._ruleBox || this._ruleFields) return;
    const labels = this._ruleLabels();
    this._ruleStatus.hidden = true;
    const fields = this._element("div", undefined, this._ruleBox);
    fields.className = "range";
    this._ruleFields = fields;
    this._ruleInputs = new Map();
    const unsupported = new Set(defaults.unsupported || []);
    for (const key of whatIfRuleSwitches) {
      if (!(key in defaults.switches)) continue;
      const wrapper = this._element("label", undefined, fields);
      wrapper.className = "check";
      const input = this._element("input", undefined, wrapper);
      input.type = "checkbox";
      input.name = `rule_${key}`;
      input.disabled = unsupported.has(key);
      this._element("span", unsupported.has(key) ? `${labels.names[key]} (${labels.edit_unsupported})` : labels.names[key], wrapper);
      this._ruleInputs.set(key, input);
    }
    for (const [key, meta] of Object.entries(defaults.numbers)) {
      const input = this._input(fields, labels.names[key], `rule_${key}`, String(meta.value), String(meta.min), String(meta.max), "any");
      this._ruleInputs.set(key, input);
    }
    const hours = this._element("label", labels.names.exclude_sell_hours, fields);
    const hoursInput = this._element("input", undefined, hours);
    Object.assign(hoursInput, { name: "rule_exclude_sell_hours", type: "text", value: defaults.exclude_sell_hours || "" });
    this._ruleInputs.set("exclude_sell_hours", hoursInput);
    const reset = this._element("button", labels.edit_reset, fields);
    reset.type = "button";
    reset.className = "stop";
    reset.addEventListener("click", () => {
      this._applyRuleValues(null);
      this._formDirty = true;
    });
    this._element("p", labels.edit_note, fields).className = "form-note";
    this._applyRuleValues(this._session?.pendingInputs || this._session?.inputs);
  }

  _applyRuleValues(saved) {
    const defaults = this._ruleDefaults;
    if (!defaults) return;
    for (const [key, input] of this._ruleInputs) {
      if (input.type === "checkbox") input.checked = Boolean(saved?.[key] ?? defaults.switches[key]);
      else if (key === "exclude_sell_hours") input.value = String(saved?.[key] ?? defaults.exclude_sell_hours ?? "");
      else input.value = String(saved?.[key] ?? defaults.numbers[key].value);
    }
  }

  _ruleOverrides() {
    const defaults = this._ruleDefaults;
    const overrides = {};
    if (!defaults) return overrides;
    for (const [key, input] of this._ruleInputs) {
      if (input.disabled) continue;
      if (input.type === "checkbox") {
        if (input.checked !== defaults.switches[key]) overrides[key] = input.checked;
      } else if (key === "exclude_sell_hours") {
        const value = input.value.replaceAll(" ", "");
        if (value !== (defaults.exclude_sell_hours || "")) overrides[key] = value;
      } else {
        const value = Number(input.value);
        if (input.value !== "" && Number.isFinite(value) && value !== defaults.numbers[key].value) overrides[key] = value;
      }
    }
    return overrides;
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
        .module-scroll table { width: 100%; min-width: 0; table-layout: fixed; }
        .module-scroll th, .module-scroll td { padding: 8px; }
        .module-scroll th { white-space: normal; min-width: 0; line-height: 1.2; overflow-wrap: anywhere; }
        .module-scroll td { white-space: normal; overflow-wrap: anywhere; }
        .module-chart { margin: 12px 0; padding: 12px 4px 4px; background: var(--secondary-background-color, var(--card-background-color)); }
        .module-chart svg { display: block; width: 100%; height: 220px; }
        .module-legend { display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 4px; font-size: .85rem; }
        .module-legend-item { display: flex; align-items: center; gap: 5px; }
        .module-legend-color { width: 18px; height: 3px; flex: none; }
        .rules-edit { grid-column: 1 / -1; }
        .rules-edit summary { cursor: pointer; font-weight: 600; margin-bottom: 8px; }
        .rules-edit .range { margin-top: 10px; }
        .rules-edit .check { grid-column: auto; }
        .rules-edit .form-note { grid-column: 1 / -1; margin: 0; }
        .rules-scroll table { min-width: 0; }
        .rules-scroll th, .rules-scroll td { padding: 7px 10px; white-space: normal; }
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
    this._ruleInputs = new Map();
    this._ruleBox = this._element("details", undefined, form);
    this._ruleBox.className = "rules-edit";
    const ruleLabels = this._ruleLabels();
    this._element("summary", ruleLabels.edit_title, this._ruleBox);
    this._ruleStatus = this._element("p", ruleLabels.edit_loading, this._ruleBox);
    this._ruleStatus.className = "result-note";
    this._ruleFields = null;
    if (this._ruleDefaults) this._buildRuleFields();
    this._ensureRuleDefaults();
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
      return await runWhatIf(this._session, this._hass, { ...data, ...this._ruleOverrides() });
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
      this._applyRuleValues(saved);
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
    const periodMonths = Object.keys(simulation.scenario.months || {}).sort();
    const monthly = periodMonths.length > 3 && simulation.current.months;
    if (monthly) {
      const profits = data => Object.fromEntries(Object.entries(data).map(([key, value]) => [key, value.profit]));
      const currentMonths = profits(simulation.current.months);
      const scenarioMonths = profits(simulation.scenario.months);
      this._monthlyBars(labels.module_monthly_title, labels.module_monthly_note, periodMonths, [
        { label: labels.current, values: currentMonths },
        { label: labels.scenario, values: scenarioMonths },
        { label: labels.difference, values: Object.fromEntries(periodMonths.map(key =>
          [key, (scenarioMonths[key] ?? 0) - (currentMonths[key] ?? 0)])) },
      ], labels);
    }
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
    if (monthly && simulation.range?.every(item => item.months)) {
      const picked = this._spread(simulation.range, 6);
      this._monthlyBars(labels.module_monthly_range_title, labels.module_monthly_note, periodMonths,
        picked.map(item => ({ label: `${this._capacity(item.battery_size)} kWh`, values: item.months })), labels);
    }
    if (simulation.module_comparison) {
      const comparison = simulation.module_comparison;
      const annualization = comparison.annualization;
      const eligibilityKnown = annualization.months && typeof annualization.months === "object";
      const annual = comparison.annual;
      const hasAnnual = annual && ["strict", "estimated"].includes(annual.method) && Array.isArray(annual.rows);
      const annualizationAvailable = hasAnnual || (!annual && eligibilityKnown && annualization.available);
      const reasonLabel = {
        missing_sell_price: labels.module_missing_sell_price,
        missing_sell_cost: labels.module_missing_sell_cost,
      }[annual?.status_reason || annualization.reason] || labels.module_estimate_unavailable;
      const unavailableLabel = eligibilityKnown ? reasonLabel : labels.module_rerun;
      this._element("h2", labels.module_section, this._results).className = "section-title";
      this._element("p", labels.module_baseline_note, this._results).className = "result-note";
      this._element("p",
        `${labels.module_history}: ${this._number(annualization.matched_hours)} ${labels.hours}; `
          + `${annualization.represented_months.length}/12 ${labels.module_months}`,
        this._results).className = "meta";
      if (annual?.method === "estimated") {
        this._element("p", labels.module_estimate_note, this._results).className = "result-note";
        this._element("p", `${labels.module_estimated_hours}: ${this._number(annual.provenance.estimated_hours)}`,
          this._results).className = "meta";
      }
      if (!annualizationAvailable) {
        this._element("p", unavailableLabel, this._results).className = "warning";
      }
      const reliability = this._element("details", undefined, this._results);
      this._element("summary", labels.module_reliability_details, reliability);
      if (["strict", "estimated"].includes(annual?.method)
        && comparison.warnings?.raw_current_sell_cost_hours > 0) {
        this._element("p",
          `${labels.module_current_cost_warning} (${this._number(comparison.warnings.raw_current_sell_cost_hours)})`,
          this._results).className = "warning";
      }
      if (annual?.method === "estimated" && annual.provenance.estimated_from_other_months?.length) {
        this._element("p", `${labels.module_estimated_months}: ${annual.provenance.estimated_from_other_months.join(", ")}`,
          reliability).className = "result-note";
      }
      const warnings = comparison.warnings || {};
      for (const [key, label] of [
        ["dst_replay_resets", labels.module_dst_warning],
        ["tariff_estimated_hours", labels.module_tariff_warning],
        ["raw_current_sell_cost_hours", labels.module_current_cost_warning],
        ["raw_unknown_sell_cost_hours", labels.module_missing_sell_cost],
      ]) {
        if (warnings[key] > 0) {
          this._element("p", `${label} (${this._number(warnings[key])})`, reliability).className = "result-note";
        }
      }
      // One result per setup against no battery (price, yearly saving, payback of the whole battery); used by the
      // summary, the table and the graph. null when the current battery's price or the yearly bills are unknown.
      const paybackSeries = hasAnnual ? this._modulePaybackSeries(annual.rows, comparison, labels) : null;
      const moduleSummary = hasAnnual ? this._moduleSummary(annual, comparison, paybackSeries, labels) : [];
      if (moduleSummary.length) {
        this._element("h3", labels.module_summary_title, this._results);
        const summaryBox = this._element("div", undefined, this._results);
        summaryBox.className = "module-summary";
        for (const sentence of moduleSummary) this._element("p", sentence, summaryBox);
      }
      const moduleScroll = this._element("div", undefined, this._results);
      moduleScroll.className = "scroll module-scroll";
      const moduleTable = this._element("table", undefined, moduleScroll);
      const moduleHeader = this._element("tr", undefined, moduleTable);
      for (const text of [
        `${labels.module_added} / ${labels.module_capacity}`,
        labels.module_incremental_gain,
        annual?.method === "estimated" ? labels.module_estimated_annual_gain : labels.module_annualized_gain,
        labels.module_price, labels.module_payback,
        labels.module_whole_cost, labels.module_whole_gain, labels.module_whole_payback,
      ]) this._element("th", text, moduleHeader).setAttribute("scope", "col");
      let hasNoPayback = false;
      for (const rowData of comparison.rows) {
        const annualRow = hasAnnual ? annual.rows.find(item => item.module_count === rowData.added_modules) : null;
        const gain = hasAnnual ? annualRow?.marginal_vs_previous?.annual_gain : rowData.annualized_incremental_gain;
        const years = hasAnnual ? annualRow?.marginal_vs_previous?.payback_years : rowData.payback_years;
        const row = this._element("tr", undefined, moduleTable);
        this._element("th", `${this._number(rowData.added_modules)} · ${this._capacity(rowData.battery_size)} kWh`, row)
          .setAttribute("scope", "row");
        this._element("td", rowData.incremental_gain === null
          ? "—" : `${this._money(rowData.incremental_gain)} ${currency}`, row);
        this._element("td", !annualizationAvailable || !Number.isFinite(gain)
          ? "—" : `${this._money(gain)} ${currency}`, row);
        this._element("td", `${this._money(rowData.module_price)} ${currency}`, row);
        const noPayback = rowData.added_modules > 0 && annualizationAvailable && !Number.isFinite(years);
        const payback = rowData.added_modules === 0 || !annualizationAvailable || noPayback
          ? "—" : `${this._number(years)} ${labels.module_years}`;
        const paybackCell = this._element("td", payback, row);
        if (noPayback) {
          hasNoPayback = true;
          paybackCell.setAttribute("aria-label", labels.module_no_payback);
          paybackCell.setAttribute("title", labels.module_no_payback);
        }
        // The whole battery (current battery plus the added modules) against no battery at all.
        const line = paybackSeries?.find(item => item.modules === rowData.added_modules);
        this._element("td", line ? `${this._money(line.start)} ${currency}` : "—", row);
        this._element("td", line ? `${this._money(paybackSeries[0].rate - line.rate)} ${currency}` : "—", row);
        const wholeYears = line && Number.isFinite(line.totalYears);
        const wholeCell = this._element("td", wholeYears ? `${this._number(line.totalYears)} ${labels.module_years}` : "—", row);
        if (line && !wholeYears) {
          hasNoPayback = true;
          wholeCell.setAttribute("aria-label", labels.module_no_payback);
          wholeCell.setAttribute("title", labels.module_no_payback);
        }
      }
      if (hasNoPayback) this._element("p", labels.module_no_payback, this._results).className = "result-note";
      this._element("p", labels.module_estimate_details, this._results).className = "result-note";
      if (hasAnnual) {
        if (paybackSeries) {
          this._modulePaybackChart(paybackSeries, labels);
        } else {
          this._element("p", labels.module_payback_missing, this._results).className = "result-note";
        }
      }
      const baseline = comparison.rows.find(row => row.added_modules === 0);
      const added = comparison.rows.filter(row => row.added_modules > 0 && row.months);
      if (monthly && baseline?.months && added.length) {
        this._monthlyBars(labels.module_monthly_modules_title, labels.module_monthly_note, periodMonths,
          this._spread(added, 6).map(row => ({
            label: this._moduleCountLabel(row.added_modules, labels),
            values: Object.fromEntries(periodMonths.map(key => [key, (row.months[key] ?? 0) - (baseline.months[key] ?? 0)])),
          })), labels);
      }
    }
    this._showRuleSettings(simulation);
  }

  _showRuleSettings(simulation) {
    const language = (this._hass?.language || "en").toLowerCase().split("-")[0];
    const labels = whatIfRuleLabels[language] || whatIfRuleLabels.en;
    const sides = [simulation.current_assumptions, simulation.assumptions];
    const known = sides.map(side => side && side.rules && typeof side.rules === "object");
    const active = side => side && side.battery_size > 0;
    const enabled = (side, key) => active(side) && side.rules?.[key] === true
      && (key !== "cheapest_hour_fill_up" || side.rules.cheapest_hour_fill_planner === true)
      && (!["optimize_solar_charging", "discharge_before_solar"].includes(key)
        || side.rules.solar_charging === true);
    const selling = side => active(side) && (
      ["most_profit", "sell_excess_kwh_available", "grid_charge_to_sell",
        "force_powerwall_max_hours_old_discharge"].some(key => enabled(side, key))
      || (enabled(side, "discharge_before_solar") && enabled(side, "solar_charging"))
    );
    this._element("h2", labels.title, this._results).className = "section-title";
    this._element("p", labels.note, this._results).className = "result-note";
    if (known.some(value => !value)) {
      this._element("p", labels.unavailable, this._results).className = "warning";
    }
    if (!known.some(Boolean)) return;
    const groups = [
      { key: "charging", rules: [
        "solar_charging", "cheapest_hour_fill_planner", "cheapest_hour_fill_up",
        "most_expensive_planner", "needed_before_max_level_planner",
        "negative_price_planner", "optimize_solar_charging",
      ], parameters: ["cheap_price_periods", "cheapest_price_rise_threshold", "cheap_price_period_rise_threshold"] },
      { key: "discharging", rules: [
        "only_discharge_on_profit", "prioritize_discharge_hours_by_energy_cost",
        "force_powerwall_max_hours_old_discharge",
      ], parameters: ["powerwall_max_hours_old"] },
      { key: "selling", rules: [
        "grid_charge_to_sell", "most_profit", "sell_excess_kwh_available", "discharge_before_solar",
      ], parameters: ["min_total_profit", "min_sell_kwh", "exclude_sell_hours"] },
      { key: "shared", rules: [], parameters: ["usage_reserve", "min_profit_per_kwh"] },
    ];
    const rows = new Map(groups.map(group => [group.key, []]));
    if (sides.some((side, index) => known[index] && active(side))) {
      rows.get("charging").push([labels.mode, ...sides.map((side, index) =>
        known[index] && active(side) && side.control_mode === "optimizer" ? labels.optimizer
          : known[index] && active(side) && side.control_mode === "rules" ? labels.rule_mode : "—")]);
    }
    for (const group of groups) {
      for (const key of group.rules) {
        if (sides.some((side, index) => known[index] && enabled(side, key))) {
          rows.get(group.key).push([labels.names[key], ...sides.map((side, index) =>
          known[index] && enabled(side, key) ? labels.on : "—")]);
        }
      }
      const parameterUnits = {
        usage_reserve: "%", min_profit_per_kwh: "currency_kwh", min_total_profit: "currency",
        min_sell_kwh: "kWh", cheap_price_periods: "",
        cheapest_price_rise_threshold: "currency_kwh", cheap_price_period_rise_threshold: "currency_kwh",
        powerwall_max_hours_old: "h", exclude_sell_hours: "text",
      };
      for (const key of group.parameters) {
        const unit = parameterUnits[key];
        const values = sides.map((side, index) => {
          if (!known[index] || !active(side)) return null;
          if (key.startsWith("cheap") && !enabled(side, "cheapest_hour_fill_planner")) return null;
          if (key === "powerwall_max_hours_old" && !enabled(side, "force_powerwall_max_hours_old_discharge")) return null;
          if (["min_total_profit", "min_sell_kwh", "exclude_sell_hours"].includes(key) && !selling(side)) return null;
          const value = side[key];
          if (unit === "text") {
            const hours = typeof value === "string"
              ? value.replaceAll(" ", "").split(",").filter(hour => /^[0-9]+$/.test(hour))
              : [];
            return hours.length ? hours.join(",") : null;
          }
          if (typeof value !== "number" || !Number.isFinite(value)) return null;
          if (unit === "currency" || unit === "currency_kwh") {
            const amount = new Intl.NumberFormat(this._hass.language, {
              minimumFractionDigits: 2, maximumFractionDigits: 20,
            }).format(value);
            return `${amount} ${this._hass.config.currency}${unit === "currency_kwh" ? "/kWh" : ""}`;
          }
          const amount = new Intl.NumberFormat(this._hass.language, { maximumFractionDigits: 20 }).format(value);
          return `${amount}${unit ? ` ${unit}` : ""}`;
        });
        if (values.some(value => value !== null)) rows.get(group.key).push([labels.names[key], ...values.map(value => value ?? "—")]);
      }
    }
    const visibleGroups = groups.filter(group => rows.get(group.key).length);
    if (!visibleGroups.length) return;
    const scroll = this._element("div", undefined, this._results);
    scroll.className = "scroll rules-scroll";
    const table = this._element("table", undefined, scroll);
    const header = this._element("tr", undefined, table);
    for (const text of [labels.setting, this._labels().current, this._labels().scenario]) {
      this._element("th", text, header).setAttribute("scope", "col");
    }
    for (const group of visibleGroups) {
      const heading = this._element("tr", undefined, table);
      this._element("th", labels.groups[group.key], heading).setAttribute("colspan", "3");
      for (const [name, current, scenario] of rows.get(group.key)) {
        const row = this._element("tr", undefined, table);
        this._element("th", name, row).setAttribute("scope", "row");
        this._element("td", current, row);
        this._element("td", scenario, row);
      }
    }
  }

  _spread(items, limit) {
    if (items.length <= limit) return items;
    return Array.from({ length: limit }, (_, index) => items[Math.round(index * (items.length - 1) / (limit - 1))]);
  }

  _monthlyBars(title, note, months, series, labels) {
    if (!months.length || !series.length) return;
    const language = this._hass.language;
    const monthName = key => {
      const [year, month] = key.split("-").map(Number);
      return new Intl.DateTimeFormat(language, { month: "short", year: "2-digit", timeZone: "UTC" })
        .format(new Date(Date.UTC(year, month - 1, 1)));
    };
    const currency = this._hass.config.currency;
    const colors = ["var(--primary-color)", "#e65100", "#388e3c", "#ad1457", "#6a1b9a", "#00838f"];
    this._element("h3", `${title} (${currency})`, this._results);
    this._element("p", note, this._results).className = "result-note";
    const scroll = this._element("div", undefined, this._results);
    scroll.className = "scroll";
    const table = this._element("table", undefined, scroll);
    const header = this._element("tr", undefined, table);
    this._element("th", labels.module_monthly_month, header).setAttribute("scope", "col");
    for (const item of series) this._element("th", item.label, header).setAttribute("scope", "col");
    const best = series.map(item => Math.max(...months.map(key => item.values[key] ?? -Infinity)));
    for (const key of months) {
      const row = this._element("tr", undefined, table);
      this._element("th", monthName(key), row).setAttribute("scope", "row");
      series.forEach((item, index) => {
        const value = item.values[key];
        const cell = this._element("td", Number.isFinite(value) ? `${this._money(value)} ${currency}` : "—", row);
        if (Number.isFinite(value) && value === best[index] && months.length > 1) cell.style.fontWeight = "700";
      });
    }
    const all = months.flatMap(key => series.map(item => item.values[key] ?? 0));
    const minimum = Math.min(0, ...all);
    const maximum = Math.max(0, ...all);
    const spread = maximum - minimum || 1;
    const y = value => 145 - (value - minimum) * 105 / spread;
    const left = 60;
    const slot = 480 / months.length;
    const barWidth = Math.max(1.5, Math.min(22, slot * 0.8 / series.length));
    const wrapper = this._element("div", undefined, this._results);
    wrapper.className = "module-chart";
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 600 200");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${title} (${currency})`);
    wrapper.appendChild(svg);
    const text = (content, px, py, anchor = "middle") => {
      const node = document.createElementNS(ns, "text");
      node.setAttribute("x", px);
      node.setAttribute("y", py);
      node.setAttribute("text-anchor", anchor);
      node.textContent = content;
      svg.appendChild(node);
    };
    const axis = document.createElementNS(ns, "line");
    axis.setAttribute("x1", left); axis.setAttribute("x2", 540);
    axis.setAttribute("y1", y(0)); axis.setAttribute("y2", y(0));
    svg.appendChild(axis);
    text(this._money(maximum), 55, 40, "end");
    text(this._money(minimum), 55, 145, "end");
    months.forEach((key, monthIndex) => {
      const start = left + monthIndex * slot + (slot - barWidth * series.length) / 2;
      series.forEach((item, index) => {
        const value = item.values[key] ?? 0;
        const bar = document.createElementNS(ns, "rect");
        bar.setAttribute("x", start + index * barWidth);
        bar.setAttribute("width", barWidth);
        bar.setAttribute("y", Math.min(y(value), y(0)));
        bar.setAttribute("height", Math.abs(y(value) - y(0)));
        bar.setAttribute("rx", "2");
        bar.style.fill = colors[index % colors.length];
        svg.appendChild(bar);
      });
      if (months.length <= 12 || monthIndex % 2 === 0) {
        text(monthName(key), left + monthIndex * slot + slot / 2, 172);
      }
    });
    const legend = this._element("div", undefined, wrapper);
    legend.className = "module-legend";
    series.forEach((item, index) => {
      const entry = this._element("div", undefined, legend);
      entry.className = "module-legend-item";
      const swatch = this._element("span", undefined, entry);
      swatch.className = "module-legend-color";
      swatch.style.backgroundColor = colors[index % colors.length];
      this._element("span", item.label, entry);
    });
    this._hoverTip(svg, 600, 200, 25, 145, pointer => {
      const index = Math.floor((pointer - left) / slot);
      if (index < 0 || index >= months.length) return null;
      const key = months[index];
      return {
        x: left + index * slot + slot / 2,
        lines: [monthName(key), ...series.map(item => Number.isFinite(item.values[key])
          ? `${item.label}: ${this._money(item.values[key])} ${currency}` : `${item.label}: —`)],
      };
    });
  }

  _moduleCountLabel(count, labels) {
    const category = new Intl.PluralRules(this._hass.language).select(count);
    const word = category === "one" ? labels.module_graph_module
      : category === "few" ? labels.module_graph_modules_few
        : labels.module_graph_modules;
    return `${this._number(count)} ${word || labels.module_graph_modules}`;
  }

  _fill(template, values) {
    return Object.entries(values).reduce((text, [key, value]) => text.replaceAll(`{${key}}`, value), template);
  }

  // Plain-language sentences for the module comparison, built from the run's numbers. A sentence is left out
  // (never filled with 0) unless every number in it is known.
  _moduleSummary(annual, comparison, paybackSeries, labels) {
    const currency = this._hass.config.currency;
    const sentences = [];
    const line = modules => paybackSeries?.find(item => item.modules === modules);
    const current = line(0);
    if (current && Number.isFinite(current.totalYears)) {
      sentences.push(this._fill(labels.module_summary_current, {
        capacity: this._capacity(current.capacity), price: this._money(current.start),
        gain: this._money(paybackSeries[0].rate - current.rate), currency, time: this._duration(current.totalYears),
      }));
    }
    const step = annual.rows.find(row => row.module_count === 1)?.marginal_vs_previous;
    if (step && Number.isFinite(step.annual_gain) && comparison.module_capacity > 0 && comparison.module_price > 0
      && Number.isFinite(comparison.base_capacity)) {
      const values = {
        modules: this._moduleCountLabel(1, labels),
        capacity: this._capacity(comparison.base_capacity + comparison.module_capacity),
        extra: this._capacity(comparison.module_capacity), price: this._money(comparison.module_price),
        gain: this._money(step.annual_gain), currency,
        time: Number.isFinite(step.payback_years) ? this._duration(step.payback_years) : "",
      };
      sentences.push(this._fill(step.annual_gain > 0 && Number.isFinite(step.payback_years)
        ? labels.module_summary_added : labels.module_summary_added_none, values));
    }
    const first = line(1);
    if (current && first && Number.isFinite(first.totalYears)) {
      // The whole battery's time is an average of the current battery and the module: it is only shorter than the
      // module alone when the current battery pays back faster, so only then say that (and why).
      const faster = !Number.isFinite(step?.payback_years) || first.totalYears < step.payback_years;
      sentences.push(this._fill(faster ? labels.module_summary_total : labels.module_summary_total_plain, {
        capacity: this._capacity(first.capacity), time: this._duration(first.totalYears),
      }));
    }
    if (sentences.length) sentences.push(labels.module_summary_advice);
    return sentences;
  }

  // Series for the payback graph: the accumulated cost over time at a constant yearly bill (`start` + `rate` * years).
  //  - dashed line "without battery": starts at 0 and rises with the whole yearly bill (`without`);
  //  - the current battery and each +N modules: start at the purchase price (current battery price plus the added
  //    modules, `start`) and rise with the yearly bill with that setup, for the battery size `capacity`.
  // Two times from the start, so nothing has to be added up (null = never, no saving):
  //  - `totalYears`: where the line crosses the dashed line, i.e. the whole setup has paid back; the chart marks it
  //    with a dot on the dashed line;
  //  - `years`, shown in the legend: where the line crosses the line before it. For the current battery that is the
  //    dashed line (same as `totalYears`); for +N modules it is the last added module on its own (its price / its
  //    extra yearly saving, the table's simple payback).
  // The whole series is null (nothing is drawn) when the current battery's price is unknown, negative or (with a
  // current battery) zero, or the "without" bill is not a number, so a missing price is never treated as 0 (a
  // negative bill is a real net-exporter bill and is drawn); a setup with no price at all is skipped.
  _modulePaybackSeries(rows, comparison, labels) {
    const hasCurrentBattery = comparison.base_capacity > 0;
    const currentCost = comparison.base_capacity / comparison.module_capacity * comparison.module_price;
    const usable = rows.filter(row => Number.isFinite(row.annual_bill?.with) && Number.isFinite(row.cumulative_cost));
    const without = rows.find(row => Number.isFinite(row.annual_bill?.without))?.annual_bill.without;
    if (!usable.some(row => row.module_count === 0) || !Number.isFinite(without)
      || !Number.isFinite(currentCost) || currentCost < 0 || (hasCurrentBattery && currentCost === 0)) return null;
    const none = { modules: -1, dashed: true, label: labels.module_payback_none, start: 0, rate: without, years: null };
    const setups = usable.filter(row => row.module_count > 0 || hasCurrentBattery).map(row => ({
      modules: row.module_count,
      label: row.module_count === 0 ? labels.module_payback_current
        : `+${this._moduleCountLabel(row.module_count, labels)}`,
      capacity: comparison.base_capacity + row.module_count * comparison.module_capacity,
      extra: comparison.module_capacity,
      start: currentCost + row.cumulative_cost,
      rate: row.annual_bill.with,
    })).filter(item => item.start > 0);
    let reference = none;
    for (const item of setups) {
      const saving = reference.rate - item.rate;
      const years = saving > 0 ? (item.start - reference.start) / saving : null;
      item.years = years !== null && Number.isFinite(years) && years >= 0 ? years : null;
      // Where the line crosses the dashed no-battery line: the whole setup has paid back (the dot on the chart).
      const totalSaving = none.rate - item.rate;
      const total = totalSaving > 0 ? item.start / totalSaving : null;
      item.totalYears = total !== null && Number.isFinite(total) && total >= 0 ? total : null;
      reference = item;
    }
    return setups.length ? [none, ...setups] : null;
  }

  _modulePaybackChart(lines, labels) {
    if (!lines.length) return;
    const currency = this._hass.config.currency;
    const title = labels.module_payback_title;
    const name = item => item.dashed ? item.label : `${this._capacity(item.capacity)} kWh · ${item.label}`;
    const crossings = lines.flatMap(item => [item.years, item.totalYears]).filter(value => Number.isFinite(value));
    const horizon = Math.min(30, Math.max(5, Math.ceil(Math.max(0, ...crossings) * 1.6 / 5) * 5));
    const valueAt = (item, time) => item.start + item.rate * time;
    const endpoints = lines.flatMap(item => [valueAt(item, 0), valueAt(item, horizon)]);
    if (endpoints.some(value => !Number.isFinite(value))) return;
    // The value axis is rounded to an even step (10k, 20k, ...) so it has evenly spaced gridlines.
    const niceStep = raw => {
      const power = 10 ** Math.floor(Math.log10(raw));
      const unit = raw / power;
      return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * power;
    };
    const rawMaximum = Math.max(0, ...endpoints);
    const rawMinimum = Math.min(0, ...endpoints);
    const valueStep = niceStep((rawMaximum - rawMinimum) / 5 || 1);
    const minimum = Math.floor(rawMinimum / valueStep) * valueStep;
    const maximum = Math.ceil(rawMaximum / valueStep) * valueStep || valueStep;
    const spread = maximum - minimum;
    // A tick step that divides the horizon, so the right edge has a tick and a gridline too.
    const timeStep = [1, 2, 5, 10].find(step => horizon / step <= 8 && horizon % step === 0);
    const y = value => 395 - (value - minimum) * 355 / spread;
    const x = value => 60 + value / horizon * 480;
    const compact = value => Math.abs(value) >= 1000 ? `${this._number(value / 1000)}k` : this._number(value);
    const colors = ["var(--primary-color)", "#e65100", "#388e3c", "#ad1457", "#6a1b9a", "#00838f",
      "#5d4037", "#c62828", "#558b2f", "#4527a0", "#00796b", "#9e5d00", "#455a64"];
    // Different stroke patterns as well as colours, so the lines can be told apart without colour.
    const patterns = ["", "8 4", "2 4", "10 4 2 4", "4 2", "12 3", "1 3", "6 2 1 2", "8 2", "3 3", "14 4", "5 5", "2 2"];
    const colorOf = item => item.dashed ? "var(--secondary-text-color)" : colors[item.modules % colors.length];
    this._element("h3", `${title} (${currency})`, this._results);
    this._element("p", labels.module_payback_note, this._results).className = "result-note";
    const wrapper = this._element("div", undefined, this._results);
    wrapper.className = "module-chart";
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    // 50% taller than before: the plot area and the pixel height are both 1.5 times as high.
    svg.setAttribute("viewBox", "0 0 600 450");
    svg.style.height = "495px";
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${title} (${currency})`);
    wrapper.appendChild(svg);
    const axisText = (text, px, py, anchor = "middle") => {
      const node = document.createElementNS(ns, "text");
      node.setAttribute("x", px);
      node.setAttribute("y", py);
      node.setAttribute("text-anchor", anchor);
      node.textContent = text;
      svg.appendChild(node);
      return node;
    };
    const gridLine = (x1, y1, x2, y2) => {
      const node = document.createElementNS(ns, "line");
      node.setAttribute("x1", x1); node.setAttribute("y1", y1);
      node.setAttribute("x2", x2); node.setAttribute("y2", y2);
      // Inline style: the card's `svg line` CSS rule would override presentation attributes.
      node.style.stroke = "var(--divider-color)";
      node.style.strokeWidth = "1";
      node.style.opacity = "1";
      svg.appendChild(node);
      return node;
    };
    for (let index = 0, count = Math.round(spread / valueStep); index <= count; index++) {
      const value = minimum + index * valueStep;
      gridLine(x(0), y(value), x(horizon), y(value));
      axisText(compact(value), 55, y(value) + 4, "end");
    }
    for (let time = 0; time <= horizon + 1e-9; time += timeStep) {
      gridLine(x(time), y(maximum), x(time), y(minimum));
      axisText(this._number(time), x(time), 412);
    }
    axisText(labels.module_years, 300, 442);
    const axisTitle = axisText(labels.module_payback_axis, 12, 220);
    axisTitle.setAttribute("transform", "rotate(-90 12 220)");
    const legend = this._element("div", undefined, wrapper);
    legend.className = "module-legend";
    const markers = [];
    for (const item of lines) {
      const color = colorOf(item);
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", x(0)); line.setAttribute("y1", y(valueAt(item, 0)));
      line.setAttribute("x2", x(horizon)); line.setAttribute("y2", y(valueAt(item, horizon)));
      line.style.stroke = color;
      line.style.strokeWidth = "2.5";
      line.style.strokeLinecap = "round";
      line.style.opacity = "1";
      const pattern = item.dashed ? "6 4" : patterns[item.modules % patterns.length];
      if (pattern) line.style.strokeDasharray = pattern;
      svg.appendChild(line);
      const dot = document.createElementNS(ns, "circle");
      dot.setAttribute("cx", x(horizon));
      dot.setAttribute("cy", y(valueAt(item, horizon)));
      dot.setAttribute("r", "4");
      dot.style.fill = color;
      svg.appendChild(dot);
      const entry = this._element("div", undefined, legend);
      entry.className = "module-legend-item";
      const swatch = this._element("span", undefined, entry);
      swatch.className = "module-legend-color";
      swatch.style.backgroundColor = color;
      if (item.dashed) {
        this._element("span", name(item), entry);
        continue;
      }
      // The dot sits where the line crosses the dashed line (the whole setup has paid back, y = the dashed line's
      // value at that time), with a thin line down to the time axis; the legend shows what the last added module
      // takes on its own.
      const total = item.totalYears;
      if (total !== null && total <= horizon) {
        const at = y(valueAt(lines[0], total));
        const drop = document.createElementNS(ns, "line");
        drop.setAttribute("x1", x(total)); drop.setAttribute("y1", at);
        drop.setAttribute("x2", x(total)); drop.setAttribute("y2", y(minimum));
        drop.style.stroke = color;
        drop.style.strokeWidth = "1";
        drop.style.strokeDasharray = "2 3";
        drop.style.opacity = "0.6";
        svg.appendChild(drop);
        markers.push({ x: x(total), y: at, years: total, color,
          text: `${this._capacity(item.capacity)} kWh ${labels.module_in_total} · ${this._duration(total)}` });
      }
      const duration = value => Number.isFinite(value) ? this._duration(value) : labels.module_graph_no_payback;
      // Current battery: one time. +N: the whole battery against no battery, and the last module on its own.
      this._element("span", item.modules === 0 ? `${name(item)}: ${duration(item.years)}`
        : `${name(item)}: ${labels.module_in_total} ${duration(item.totalYears)} · `
          + `${this._fill(labels.module_extra_only, { extra: this._capacity(item.extra) })} ${duration(item.years)}`, entry);
    }
    this._paybackMarkers(svg, ns, markers);
    this._hoverTip(svg, 600, 450, 25, 395, pointer => {
      const time = Math.max(0, Math.min(horizon, (pointer - 60) / 480 * horizon));
      return {
        x: x(time),
        lines: [this._duration(time),
          ...lines.map(item => `${name(item)}: ${this._money(valueAt(item, time))} ${currency}`)],
        colors: ["", ...lines.map(colorOf)],
        dots: lines.map(item => ({ x: x(time), y: y(valueAt(item, time)), color: colorOf(item) })),
      };
    });
  }

  _hoverTip(svg, width, height, top, bottom, resolve) {
    const ns = "http://www.w3.org/2000/svg";
    const hover = document.createElementNS(ns, "g");
    hover.style.display = "none";
    hover.setAttribute("pointer-events", "none");
    const line = document.createElementNS(ns, "line");
    line.setAttribute("y1", top);
    line.setAttribute("y2", bottom);
    hover.appendChild(line);
    const box = document.createElementNS(ns, "rect");
    box.setAttribute("rx", "5");
    box.setAttribute("fill", "var(--card-background-color)");
    box.setAttribute("stroke", "var(--divider-color)");
    hover.appendChild(box);
    const texts = [];
    const dots = [];
    const update = event => {
      const bounds = svg.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      const scale = Math.min(bounds.width / width, bounds.height / height);
      const x = (event.clientX - bounds.left - (bounds.width - width * scale) / 2) / scale;
      const info = resolve(x);
      if (!info) { hover.style.display = "none"; return; }
      while (texts.length < info.lines.length) {
        const node = document.createElementNS(ns, "text");
        hover.appendChild(node);
        texts.push(node);
      }
      const points = info.dots || [];
      while (dots.length < points.length) {
        const node = document.createElementNS(ns, "circle");
        node.setAttribute("r", "5");
        node.setAttribute("stroke", "var(--card-background-color)");
        node.setAttribute("stroke-width", "2");
        hover.appendChild(node);
        dots.push(node);
      }
      dots.forEach((node, index) => {
        const point = points[index];
        node.style.display = point ? "" : "none";
        if (point) {
          node.setAttribute("cx", point.x);
          node.setAttribute("cy", point.y);
          node.style.fill = point.color;
        }
      });
      const boxWidth = Math.min(width - 4, Math.max(...info.lines.map(text => text.length)) * 6 + 14);
      const boxHeight = info.lines.length * 13 + 8;
      const preferred = info.x > width / 2 ? info.x - boxWidth - 10 : info.x + 10;
      const boxX = Math.max(2, Math.min(width - boxWidth - 2, preferred));
      const boxY = Math.max(2, Math.min(height - boxHeight - 2, top));
      line.setAttribute("x1", info.x);
      line.setAttribute("x2", info.x);
      box.setAttribute("x", boxX);
      box.setAttribute("y", boxY);
      box.setAttribute("width", boxWidth);
      box.setAttribute("height", boxHeight);
      box.setAttribute("fill-opacity", "0.9");
      texts.forEach((node, index) => {
        node.textContent = info.lines[index] ?? "";
        node.setAttribute("x", boxX + 6);
        node.setAttribute("y", boxY + 14 + index * 13);
        node.style.fill = info.colors?.[index] || "";
      });
      hover.style.display = "";
    };
    svg.addEventListener("pointermove", update);
    svg.addEventListener("pointerdown", update);
    svg.addEventListener("pointerleave", event => {
      if (event.pointerType !== "touch") hover.style.display = "none";
    });
    svg.appendChild(hover);
  }

  _duration(years) {
    const whole = Math.floor(years);
    const months = Math.min(11, Math.round((years - whole) * 12));
    const carry = months === 12 ? 1 : 0;
    const unit = (value, name) => new Intl.NumberFormat(this._hass.language,
      { style: "unit", unit: name, unitDisplay: "short" }).format(value);
    const parts = [];
    if (whole + carry > 0) parts.push(unit(whole + carry, "year"));
    if (months % 12 > 0 || !parts.length) parts.push(unit(months % 12, "month"));
    return parts.join(" ");
  }

  _paybackMarkers(svg, ns, items) {
    items.forEach((item, index) => {
      const dot = document.createElementNS(ns, "circle");
      dot.setAttribute("cx", item.x);
      dot.setAttribute("cy", item.y);
      dot.setAttribute("r", "5");
      dot.setAttribute("stroke", "var(--card-background-color)");
      dot.setAttribute("stroke-width", "2");
      dot.style.fill = item.color;
      svg.appendChild(dot);
      const label = document.createElementNS(ns, "text");
      const text = item.text ?? this._duration(item.years);
      const above = item.y - 10 - text.length * 7 >= 5;
      // Labels of dots that are close together are moved sideways so they do not print over each other.
      const neighbours = items.slice(0, index).filter(other => Math.abs(other.x - item.x) < 12).length;
      const px = item.x + 4 + neighbours * 12;
      const py = above ? item.y - 10 : item.y + 10;
      label.setAttribute("x", px);
      label.setAttribute("y", py);
      label.setAttribute("transform", `rotate(-90 ${px} ${py})`);
      label.setAttribute("text-anchor", above ? "start" : "end");
      label.style.fill = item.color;
      label.style.fontWeight = "700";
      label.textContent = text;
      svg.appendChild(label);
    });
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
