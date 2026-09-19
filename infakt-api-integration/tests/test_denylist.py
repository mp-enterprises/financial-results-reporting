from __future__ import annotations

from app.denylist import (
    normalizuj_nip,
    odfiltruj_faktury,
    odfiltruj_koszty,
    wczytaj_denylist,
    wyciagnij_nip_klienta,
    wyciagnij_nip_sprzedawcy,
)


def test_normalizuj_nip_usuwa_separatory():
    assert normalizuj_nip("111-222-33-44") == "1112223344"
    assert normalizuj_nip("111 222 33 44") == "1112223344"
    assert normalizuj_nip("") == ""
    assert normalizuj_nip(None) == ""  # type: ignore[arg-type]


def test_wczytaj_denylist_parsuje_liste_z_separatorami():
    assert wczytaj_denylist("111-222-33-44, 5551112233;9998887766") == {
        "1112223344",
        "5551112233",
        "9998887766",
    }


def test_wczytaj_denylist_pusty_string_zwraca_pusty_zbior():
    assert wczytaj_denylist("") == set()
    assert wczytaj_denylist("   ") == set()


def test_wyciagnij_nip_sprzedawcy_zwraca_none_gdy_brak_pola():
    assert wyciagnij_nip_sprzedawcy({"id": 1}) is None


def test_wyciagnij_nip_sprzedawcy_znajduje_pole(koszt_przykladowy):
    assert wyciagnij_nip_sprzedawcy(koszt_przykladowy) == "1112223344"


def test_odfiltruj_koszty_bez_denylist_zwraca_wszystko(koszt_przykladowy):
    dopuszczone, odrzucone, bez_nip = odfiltruj_koszty([koszt_przykladowy], set())
    assert dopuszczone == [koszt_przykladowy]
    assert odrzucone == []
    assert bez_nip == 0


def test_odfiltruj_koszty_usuwa_dostawce_z_denylisty(koszt_przykladowy):
    dopuszczone, odrzucone, bez_nip = odfiltruj_koszty([koszt_przykladowy], {"1112223344"})
    assert dopuszczone == []
    assert odrzucone == [koszt_przykladowy]
    assert bez_nip == 0


def test_odfiltruj_koszty_dopuszcza_spoza_denylisty(koszt_przykladowy):
    dopuszczone, odrzucone, bez_nip = odfiltruj_koszty([koszt_przykladowy], {"9999999999"})
    assert dopuszczone == [koszt_przykladowy]
    assert odrzucone == []


def test_odfiltruj_koszty_bez_rozpoznanego_nip_jest_fail_open():
    koszt_bez_nip = {"id": 99, "gross_price": 1000}
    dopuszczone, odrzucone, bez_nip = odfiltruj_koszty([koszt_bez_nip], {"1112223344"})
    assert dopuszczone == [koszt_bez_nip]
    assert odrzucone == []
    assert bez_nip == 1


def test_wyciagnij_nip_klienta_znajduje_pole():
    faktura = {"id": 1, "client_tax_code": "524-294-93-77"}
    assert wyciagnij_nip_klienta(faktura) == "5242949377"


def test_wyciagnij_nip_klienta_zwraca_none_gdy_brak_pola():
    assert wyciagnij_nip_klienta({"id": 1}) is None


def test_odfiltruj_faktury_usuwa_klienta_z_denylisty():
    faktura = {"id": 1, "client_tax_code": "5242949377"}
    dopuszczone, odrzucone, bez_nip = odfiltruj_faktury([faktura], {"5242949377"})
    assert dopuszczone == []
    assert odrzucone == [faktura]
    assert bez_nip == 0


def test_odfiltruj_faktury_dopuszcza_spoza_denylisty():
    faktura = {"id": 1, "client_tax_code": "9999999999"}
    dopuszczone, odrzucone, bez_nip = odfiltruj_faktury([faktura], {"5242949377"})
    assert dopuszczone == [faktura]
    assert odrzucone == []
