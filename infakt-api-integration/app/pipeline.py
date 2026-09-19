"""Orkiestracja: pobierz zasoby za okres z inFakt, odfiltruj koszty, policz
podsumowanie wg wzoru panelu Statystyki, zapisz wszystko do plików JSON.
"""
from __future__ import annotations

import calendar
import logging
from pathlib import Path
from typing import Any

from .aggregator import zbuduj_podsumowanie
from .client import InfaktClient
from .config import settings
from .denylist import odfiltruj_faktury, odfiltruj_koszty, wczytaj_denylist
from .resources import (
    FAKTURY_PRZYCHODOWE,
    POLA_DATY,
    filtruj_ksef_faktury,
    filtruj_po_okresie,
    filtruj_zrodlo_ksef,
    pobierz_zasob,
    zbuduj_filtr_daty,
)
from .storage import zapisz_json

logger = logging.getLogger(__name__)


def zakres_miesiaca(okres: str) -> tuple[str, str]:
    """okres: 'YYYY-MM' -> (pierwszy_dzien_miesiaca, pierwszy_dzien_kolejnego_miesiaca)."""
    try:
        rok_str, miesiac_str = okres.split("-")
        rok, miesiac = int(rok_str), int(miesiac_str)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Nieprawidłowy okres {okres!r} — oczekiwano formatu YYYY-MM") from exc
    if not 1 <= miesiac <= 12:
        raise ValueError(f"Nieprawidłowy miesiąc w okresie {okres!r}")
    calendar.monthrange(rok, miesiac)  # waliduje rok/miesiąc
    od = f"{rok:04d}-{miesiac:02d}-01"
    if miesiac == 12:
        do = f"{rok + 1:04d}-01-01"
    else:
        do = f"{rok:04d}-{miesiac + 1:02d}-01"
    return od, do


def uruchom_pobieranie(
    okres: str,
    client: InfaktClient | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Pobiera dane inFakt za dany okres i zapisuje surowe zasoby + podsumowanie do JSON."""
    client = client or InfaktClient(
        api_key=settings.api_key,
        environment=settings.environment,
        max_retries=settings.max_retries,
        timeout_s=settings.timeout_s,
    )
    output_dir = output_dir or settings.output_dir
    od, do = zakres_miesiaca(okres)
    filtr = zbuduj_filtr_daty(od, do)

    # Filtrowanie po dacie na serwerze jest zawodne (potwierdzone na żywo:
    # documents/costs.json je ignoruje) — wysyłamy filtr jako podpowiedź, ale
    # o właściwe zawężenie do okresu dbamy zawsze też po stronie klienta
    # (filtruj_po_okresie), więc wynik jest poprawny niezależnie od tego, czy
    # serwer go uwzględnił.
    denylist = wczytaj_denylist(settings.supplier_denylist_nip)

    faktury_surowe = {nazwa: pobierz_zasob(client, nazwa, params=filtr) for nazwa in FAKTURY_PRZYCHODOWE}
    faktury = {}
    bez_daty_faktur = {}
    faktur_odrzuconych_zrodlo: dict[str, int] = {}
    faktur_odrzuconych_denylist: dict[str, int] = {}
    faktur_bez_rozpoznanego_nip_klienta: dict[str, int] = {}
    for nazwa, rekordy in faktury_surowe.items():
        w_okresie, bez_daty = filtruj_po_okresie(rekordy, POLA_DATY[nazwa], od, do)
        bez_daty_faktur[nazwa] = bez_daty
        # Tylko faktury wystawione przez KSeF są uznawane za wiarygodne dla
        # rozliczeń — analogicznie do kosztów, patrz README.
        w_okresie_ksef = filtruj_ksef_faktury(w_okresie)
        faktur_odrzuconych_zrodlo[nazwa] = len(w_okresie) - len(w_okresie_ksef)
        dopuszczone, odrzucone_faktury, bez_nip_klienta = odfiltruj_faktury(w_okresie_ksef, denylist)
        faktury[nazwa] = dopuszczone
        faktur_odrzuconych_denylist[nazwa] = len(odrzucone_faktury)
        faktur_bez_rozpoznanego_nip_klienta[nazwa] = bez_nip_klienta

    koszty_surowe = pobierz_zasob(client, "costs", params=filtr)
    koszty_w_okresie, koszty_bez_daty = filtruj_po_okresie(koszty_surowe, POLA_DATY["costs"], od, do)
    # Tylko koszty wczytane przez KSeF są uznawane za wiarygodne dla rozliczeń
    # — patrz README, sekcja "Filtrowanie po źródle (KSeF)".
    koszty_ksef = filtruj_zrodlo_ksef(koszty_w_okresie)
    koszty_odrzucone_zrodlo = len(koszty_w_okresie) - len(koszty_ksef)
    koszty, odrzucone, bez_nip = odfiltruj_koszty(koszty_ksef, denylist)

    # Pole daty dla ZUS/podatku dochodowego/JPK V7 nie jest potwierdzone —
    # te trzy zasoby NIE są filtrowane po stronie klienta (patrz README).
    skladki_zus = pobierz_zasob(client, "insurance_fees", params=filtr)
    podatki_dochodowe = pobierz_zasob(client, "income_taxes", params=filtr)
    jpk_v7 = pobierz_zasob(client, "saf_v7_files", params=filtr)

    katalog_surowy = output_dir / "raw" / okres
    for nazwa, rekordy in faktury_surowe.items():
        zapisz_json(rekordy, katalog_surowy / f"{nazwa}_raw.json")
        zapisz_json(faktury[nazwa], katalog_surowy / f"{nazwa}.json")
    # Jedyny plik dla kosztów: po filtrze okresu I denyliście — to, co faktycznie
    # wchodzi do podsumowania. costs_raw/costs_filtered/costs_excluded_by_denylist
    # świadomie nie są już zapisywane (patrz README).
    zapisz_json(koszty, katalog_surowy / "costs_in_period.json")
    zapisz_json(skladki_zus, katalog_surowy / "insurance_fees.json")
    zapisz_json(podatki_dochodowe, katalog_surowy / "income_taxes.json")
    zapisz_json(jpk_v7, katalog_surowy / "saf_v7_files.json")

    podsumowanie = zbuduj_podsumowanie(faktury, koszty, skladki_zus, podatki_dochodowe)
    podsumowanie.update(
        {
            "okres": okres,
            "srodowisko": client.environment,
            "kosztow_odrzuconych_zrodlo_nie_ksef": koszty_odrzucone_zrodlo,
            "kosztow_odrzuconych_denylist": len(odrzucone),
            "kosztow_bez_rozpoznanego_nip": bez_nip,
            "kosztow_bez_rozpoznanej_daty": koszty_bez_daty,
            "faktur_bez_rozpoznanej_daty": bez_daty_faktur,
            "faktur_odrzuconych_zrodlo_nie_ksef": faktur_odrzuconych_zrodlo,
            "faktur_odrzuconych_denylist": faktur_odrzuconych_denylist,
            "faktur_bez_rozpoznanego_nip_klienta": faktur_bez_rozpoznanego_nip_klienta,
            "zus_podatki_jpk_bez_filtrowania_okresu": True,
        }
    )
    zapisz_json(podsumowanie, output_dir / "summary" / f"{okres}.json")
    logger.info("Zapisano dane inFakt za %s do %s", okres, output_dir)
    return podsumowanie
