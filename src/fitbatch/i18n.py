"""User-facing texts in Polish and English (F21, N16)."""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "pl"

_TEXTS: dict[str, dict[str, str]] = {
    # general
    "app.tagline": {
        "pl": "Hurtowe pobieranie i wgrywanie danych treningowych. Dla Garmin Connect.",
        "en": "Bulk download & upload of your training data. For Garmin Connect.",
    },
    "app.disclaimer": {
        "pl": "FitBatch to niezależny, nieoficjalny projekt. Nie jest powiązany z Garmin Ltd. "
        "ani innymi platformami, nie jest przez nie wspierany ani sponsorowany. Garmin i "
        "Garmin Connect są znakami towarowymi Garmin Ltd. lub jej spółek zależnych.",
        "en": "FitBatch is an independent, unofficial project. It is not affiliated with, "
        "endorsed by or sponsored by Garmin Ltd. or any other platform. Garmin and Garmin "
        "Connect are trademarks of Garmin Ltd. or its subsidiaries.",
    },
    "app.license": {"pl": "Licencja: GPL-3.0-or-later", "en": "License: GPL-3.0-or-later"},
    "common.ok": {"pl": "OK", "en": "OK"},
    "common.cancel": {"pl": "Anuluj", "en": "Cancel"},
    "common.save": {"pl": "Zapisz", "en": "Save"},
    "common.close": {"pl": "Zamknij", "en": "Close"},
    "common.about": {"pl": "O programie", "en": "About"},
    "common.settings": {"pl": "Ustawienia", "en": "Settings"},
    "common.soon": {"pl": "wkrótce", "en": "coming soon"},
    # connection (F1, F3, F4)
    "conn.title": {"pl": "Połącz z platformą", "en": "Connect to a platform"},
    "conn.platform": {"pl": "Platforma", "en": "Platform"},
    "conn.method": {"pl": "Metoda logowania", "en": "Sign-in method"},
    "conn.method.api_key": {
        "pl": "Klucz API (zalecane, oficjalne) – wkrótce",
        "en": "API key (recommended, official) – coming soon",
    },
    "conn.method.email": {
        "pl": "E-mail + hasło (eksperymentalne)",
        "en": "Email + password (experimental)",
    },
    "conn.email_warning": {
        "pl": "Nieoficjalna metoda. Przy zbyt wielu zapytaniach Garmin może czasowo "
        "zablokować logowanie. Hasło służy tylko do tego logowania i nigdy nie jest zapisywane.",
        "en": "Unofficial method. Too many requests can make Garmin block sign-in for a while. "
        "Your password is used only for this sign-in and is never saved.",
    },
    "conn.email": {"pl": "E-mail", "en": "Email"},
    "conn.password": {"pl": "Hasło", "en": "Password"},
    "conn.connect": {"pl": "Połącz", "en": "Connect"},
    "conn.skip": {"pl": "Pomiń (bez połączenia)", "en": "Skip (offline)"},
    "conn.connecting": {"pl": "Łączę z Garmin Connect…", "en": "Connecting to Garmin Connect…"},
    "conn.restoring": {"pl": "Odnawiam zapisaną sesję…", "en": "Restoring the saved session…"},
    "conn.failed": {"pl": "Nie udało się połączyć: {error}", "en": "Could not connect: {error}"},
    "conn.locked": {
        "pl": "Zbyt wiele prób. Spróbuj ponownie za {minutes} min.",
        "en": "Too many attempts. Try again in {minutes} min.",
    },
    "conn.missing_fields": {"pl": "Podaj e-mail i hasło.", "en": "Enter email and password."},
    "conn.mfa_title": {"pl": "Kod weryfikacyjny", "en": "Verification code"},
    "conn.mfa_text": {
        "pl": "Garmin wysłał kod weryfikacyjny. Wpisz go poniżej.",
        "en": "Garmin sent a verification code. Enter it below.",
    },
    "status.connected": {"pl": "Garmin · {account}", "en": "Garmin · {account}"},
    "status.offline": {"pl": "Brak połączenia", "en": "Not connected"},
    "accounts.title": {"pl": "Konta", "en": "Accounts"},
    "accounts.email_connected": {
        "pl": "E-mail + hasło: połączono ({account})",
        "en": "Email + password: connected ({account})",
    },
    "accounts.email_saved": {
        "pl": "E-mail + hasło: zapisana sesja na tym komputerze",
        "en": "Email + password: session saved on this computer",
    },
    "accounts.email_none": {
        "pl": "E-mail + hasło: niepołączono",
        "en": "Email + password: not connected",
    },
    "accounts.api_key": {"pl": "Klucz API: wkrótce", "en": "API key: coming soon"},
    "accounts.delete": {
        "pl": "Rozłącz i usuń zapisane dane",
        "en": "Disconnect and delete saved data",
    },
    "accounts.connect": {"pl": "Połącz", "en": "Connect"},
    # export (F5–F16)
    "export.from": {"pl": "Od (RRRR-MM-DD)", "en": "From (YYYY-MM-DD)"},
    "export.to": {"pl": "Do (RRRR-MM-DD)", "en": "To (YYYY-MM-DD)"},
    "export.range.7d": {"pl": "7 dni", "en": "7 days"},
    "export.range.30d": {"pl": "30 dni", "en": "30 days"},
    "export.range.year": {"pl": "Rok", "en": "Year"},
    "export.range.all": {"pl": "Wszystko", "en": "All"},
    "export.bad_date": {"pl": "Nieprawidłowa data", "en": "Invalid date"},
    "export.bad_range": {"pl": "„Od” musi być ≤ „Do”", "en": "“From” must be ≤ “To”"},
    "export.formats": {"pl": "Formaty", "en": "Formats"},
    "export.no_format": {
        "pl": "Wybierz co najmniej jeden format.",
        "en": "Choose at least one format.",
    },
    "format.fit": {"pl": "FIT (oryginał)", "en": "FIT (original)"},
    "format.tcx": {"pl": "TCX", "en": "TCX"},
    "format.gpx": {"pl": "GPX", "en": "GPX"},
    "format.kml": {"pl": "KML (Google Earth)", "en": "KML (Google Earth)"},
    "format.splits.csv": {"pl": "Podziały (CSV)", "en": "Splits (CSV)"},
    "export.include": {
        "pl": "Nazwa zawiera (frazy po przecinku)",
        "en": "Name contains (comma-separated)",
    },
    "export.exclude": {"pl": "Nazwa nie zawiera", "en": "Name does not contain"},
    "export.types": {"pl": "Typy aktywności", "en": "Activity types"},
    "export.folder": {"pl": "Folder docelowy", "en": "Target folder"},
    "export.choose_folder": {"pl": "Wybierz…", "en": "Choose…"},
    "export.overwrite": {"pl": "Pobierz ponownie i nadpisz", "en": "Download again and overwrite"},
    "export.list": {"pl": "Pobierz listę aktywności", "en": "Get activity list"},
    "export.listing": {"pl": "Pobieram listę aktywności…", "en": "Getting the activity list…"},
    "export.matches": {"pl": "Pasuje {matched} z {total}", "en": "{matched} of {total} match"},
    "export.selected": {"pl": "zaznaczono {count}", "en": "{count} selected"},
    "export.select_all": {"pl": "Zaznacz wszystkie", "en": "Select all"},
    "export.select_none": {"pl": "Odznacz wszystkie", "en": "Select none"},
    "export.start": {"pl": "Pobierz zaznaczone", "en": "Download selected"},
    "export.resume": {"pl": "Wznów", "en": "Resume"},
    "export.cancel": {"pl": "Anuluj", "en": "Cancel"},
    "export.cancelling": {"pl": "Kończę bieżący plik…", "en": "Finishing the current file…"},
    "export.need_connection": {
        "pl": "Połącz się z Garmin Connect, aby pobrać listę.",
        "en": "Connect to Garmin Connect to get the list.",
    },
    "export.nothing_selected": {
        "pl": "Nie zaznaczono żadnej aktywności.",
        "en": "No activity selected.",
    },
    "export.progress": {"pl": "{done} / {total} · {name}", "en": "{done} / {total} · {name}"},
    "export.summary": {
        "pl": "Pobrano {saved}, pominięto {skipped}, błędy {failed}.",
        "en": "Saved {saved}, skipped {skipped}, errors {failed}.",
    },
    "export.cancelled": {
        "pl": "Anulowano. „Wznów” dokończy pozostałe.",
        "en": "Cancelled. “Resume” finishes the rest.",
    },
    "export.paused.rate_limit": {
        "pl": "Wstrzymano: Garmin ogranicza zapytania. "
        "Odczekaj kilkanaście minut i kliknij „Wznów”.",
        "en": "Paused: Garmin is limiting requests. Wait 15+ minutes and click “Resume”.",
    },
    "export.paused.authentication": {
        "pl": "Wstrzymano: sesja wygasła. Połącz się ponownie i kliknij „Wznów”.",
        "en": "Paused: the session expired. Connect again and click “Resume”.",
    },
    "export.open_folder": {"pl": "Otwórz folder", "en": "Open folder"},
    "export.error": {"pl": "Błąd: {error}", "en": "Error: {error}"},
    # log lines (F14)
    "log.saved": {"pl": "{fmt}: zapisano {file}", "en": "{fmt}: saved {file}"},
    "log.saved_substitute": {
        "pl": "{fmt}: brak FIT w Garminie, zapisano oryginał {file}",
        "en": "{fmt}: no FIT on Garmin, original upload saved as {file}",
    },
    "log.skipped": {"pl": "{fmt}: pominięto ({reason})", "en": "{fmt}: skipped ({reason})"},
    "log.failed": {"pl": "{fmt}: BŁĄD {error}", "en": "{fmt}: ERROR {error}"},
    "log.waiting": {
        "pl": "Garmin ogranicza zapytania, czekam {seconds} s…",
        "en": "Garmin is limiting requests, waiting {seconds} s…",
    },
    "reason.already_downloaded": {"pl": "już pobrano", "en": "already downloaded"},
    "reason.exists_on_disk": {"pl": "plik już jest w folderze", "en": "file already in folder"},
    "reason.no_gps": {"pl": "brak GPS", "en": "no GPS"},
    "reason.no_file": {"pl": "brak pliku", "en": "no file"},
    "reason.local_only": {
        "pl": "konwersja lokalna – wkrótce",
        "en": "local conversion – coming soon",
    },
    # settings (F21)
    "settings.language": {"pl": "Język", "en": "Language"},
    "settings.default_folder": {"pl": "Domyślny folder", "en": "Default folder"},
    "settings.default_formats": {"pl": "Domyślne formaty", "en": "Default formats"},
    "settings.restart_hint": {
        "pl": "Zmiana języka obowiązuje od razu po zapisaniu.",
        "en": "The language changes as soon as you save.",
    },
}


class Translator:
    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self.language = language

    def __call__(self, key: str, **params: Any) -> str:
        entry = _TEXTS.get(key)
        if entry is None:
            return key
        text = entry.get(self.language) or entry.get("en") or key
        return text.format(**params) if params else text


def all_keys() -> list[str]:
    return list(_TEXTS)


def missing_translations() -> list[tuple[str, str]]:
    return [(k, lang) for k, v in _TEXTS.items() for lang in ("pl", "en") if not v.get(lang)]
