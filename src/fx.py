from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

_ENV = "UPDATER_CURRENCY"
_ENV_REGION = "UPDATER_REGION"
_TTL = 43200.0
_RETRY = 600.0
_FEEDS = (
    "https://open.er-api.com/v6/latest/USD",
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
    "https://api.frankfurter.dev/v1/latest?base=USD",
)

_lock = threading.Lock()
_state = {"rates": {}, "at": 0.0, "warm": False, "busy": False, "failed": 0.0}


def _table(text, cast=str):
    p = text.split()
    return {p[i]: cast(p[i + 1]) for i in range(0, len(p) - 1, 2)}


_BAKED = _table("""
USD 1.000 EUR 0.8934 GBP 0.7454 JPY 158.4 CNY 6.684 INR 94.83 AUD 1.434 CAD 1.399
CHF 0.8200 SEK 9.791 NOK 9.281 DKK 6.680 PLN 3.945 CZK 21.76 HUF 325.9 RON 4.772
BGN 1.748 TRY 50.04 BRL 5.486 MXN 18.74 ARS 1550 CLP 978.1 COP 3221 PEN 3.465
ZAR 16.62 NGN 1346 KES 130.0 EGP 55.37 ILS 3.164 AED 3.6725 SAR 3.750 QAR 3.640
KWD 0.3112 BHD 0.3760 OMR 0.3845 JOD 0.7090 HKD 7.846 SGD 1.281 TWD 32.77
KRW 1344 THB 33.76 MYR 4.077 IDR 18140 PHP 63.52 VND 25769 PKR 276.5 BDT 124.5
LKR 332.4 NPR 151.7 UAH 45.68 RUB 81.42 KZT 452.7 UZS 11911 GEL 2.595 AMD 360.2
AZN 1.701 MAD 9.823 TND 3.004 DZD 136.0 GHS 11.68 TZS 2640 UGX 4092 RWF 1479
ETB 161.7 MZN 63.75 ZMW 20.26 BWP 14.09 MUR 47.58 ISK 121.7 HRK 6.732 RSD 108.0
BAM 1.748 MKD 61.50 ALL 92.50 MDL 17.80 BYN 3.200 XPF 119.3 XOF 655.96 XAF 655.96
CDF 2900 AOA 950.0 GMD 73.00 GNF 8700 SLE 22.80 LRD 200.0 SDG 600.0 LYD 6.600
SOS 570.0 NAD 16.62 LSL 16.62 SZL 16.62 MWK 1750 MGA 4600 SCR 14.50 CVE 100.4
BTN 94.83 MMK 2100 KHR 4075 LAK 21500 MNT 3400 MOP 8.080 BND 1.281 MVR 15.40
AFN 65.00 PGK 4.200 FJD 2.240 SBD 8.500 VUV 120.0 WST 2.700 TOP 2.350 BOB 6.900
PYG 7300 UYU 40.00 VES 60.00 CRC 500.0 PAB 1.000 GTQ 7.700 HNL 25.00 NIO 36.80
DOP 60.00 CUP 24.00 JMD 157.0 TTD 6.800 BBD 2.000 BSD 1.000 BZD 2.000 GYD 209.0
SRD 36.00 HTG 130.0 IRR 42000 IQD 1310 LBP 89500 SYP 13000 YER 250.0 TMT 3.500
KGS 87.00 TJS 10.80 ZWL 360.0 ERN 15.00 DJF 178.0 BIF 2900 KMF 440.0 MRU 39.60
STN 22.00 SSP 1300 XCD 2.700 AWG 1.790 ANG 1.800 BMD 1.000 KYD 0.830 FKP 0.7454
GIP 0.7454
""", float)

_ZERO = frozenset(
    "JPY KRW VND IDR CLP ISK HUF PYG UGX RWF XAF XOF XPF KMF DJF GNF BIF VUV MGA".split()
)

_SYM = _table("""
USD $ EUR € GBP £ JPY ¥ CNY ¥ INR ₹ AUD A$ CAD CA$ CHF CHF SEK kr NOK kr DKK kr
PLN zł CZK Kč HUF Ft RON lei BGN лв TRY ₺ BRL R$ MXN MX$ ARS AR$ CLP CL$ COP COL$
PEN S/ ZAR R NGN ₦ KES KSh EGP E£ ILS ₪ AED د.إ SAR ﷼ QAR ﷼ KWD د.ك BHD د.ب
OMR ﷼ JOD د.ا HKD HK$ SGD S$ TWD NT$ KRW ₩ THB ฿ MYR RM IDR Rp PHP ₱ VND ₫
PKR Rs BDT ৳ LKR Rs NPR Rs UAH ₴ RUB ₽ KZT ₸ UZS soʻm GEL ₾ AMD ֏ AZN ₼ MAD DH
TND DT DZD DA GHS ₵ TZS TSh UGX USh RWF FRw ETB Br MZN MT ZMW ZK BWP P MUR Rs
ISK kr HRK € RSD дин BAM KM MKD ден ALL L MDL L BYN Br XPF F XOF FCFA XAF FCFA
CDF FC AOA Kz GMD D GNF FG SLE Le LRD L$ SDG ج.س LYD ل.د SOS Sh NAD N$ LSL L
SZL E MWK MK MGA Ar SCR ₨ CVE Esc BTN Nu. MMK K KHR ៛ LAK ₭ MNT ₮ MOP MOP$
BND B$ MVR Rf AFN ؋ PGK K FJD FJ$ SBD SI$ VUV VT WST WS$ TOP T$ BOB Bs PYG ₲
UYU $U VES Bs. CRC ₡ PAB B/. GTQ Q HNL L NIO C$ DOP RD$ CUP $ JMD J$ TTD TT$
BBD Bds$ BSD B$ BZD BZ$ GYD G$ SRD Sr$ HTG G IQD ع.د LBP ل.ل SYP £ YER ﷼
TMT m KGS сом TJS SM ZWL Z$ ERN Nfk DJF Fdj BIF FBu KMF CF MRU UM STN Db
SSP £ XCD EC$ AWG ƒ ANG ƒ KYD CI$ FKP £ GIP £
""")

_REGION = _table("""
US USD GB GBP IE EUR FR EUR DE EUR AT EUR BE EUR NL EUR LU EUR ES EUR PT EUR IT EUR
GR EUR FI EUR EE EUR LV EUR LT EUR CY EUR MT EUR SK EUR SI EUR HR EUR AD EUR MC EUR
SM EUR VA EUR XK EUR ME EUR NO NOK SE SEK DK DKK IS ISK CH CHF LI CHF PL PLN CZ CZK
HU HUF RO RON BG BGN RS RSD BA BAM MK MKD AL ALL MD MDL UA UAH BY BYN RU RUB TR TRY
GE GEL AM AMD AZ AZN KZ KZT UZ UZS KG KGS TJ TJS TM TMT CA CAD MX MXN BR BRL AR ARS
CL CLP CO COP PE PEN UY UYU PY PYG BO BOB VE VES EC USD SV USD PA PAB GT GTQ HN HNL
NI NIO CR CRC DO DOP CU CUP JM JMD TT TTD BB BBD BS BSD BZ BZD GY GYD SR SRD HT HTG
PR USD IL ILS PS ILS AE AED SA SAR QA QAR KW KWD BH BHD OM OMR JO JOD LB LBP IQ IQD
IR IRR SY SYP YE YER EG EGP MA MAD TN TND DZ DZD LY LYD SD SDG ET ETB ER ERN DJ DJF
SO SOS KE KES TZ TZS UG UGX RW RWF BI BIF CD CDF CG XAF GA XAF CM XAF CF XAF TD XAF
NE XOF ML XOF BF XOF SN XOF CI XOF TG XOF BJ XOF GW XOF GM GMD GN GNF SL SLE LR LRD
GH GHS NG NGN ZA ZAR NA NAD BW BWP LS LSL SZ SZL ZW ZWL MZ MZN ZM ZMW MW MWK MU MUR
SC SCR MG MGA KM KMF AO AOA CV CVE ST STN MR MRU SS SSP JP JPY CN CNY HK HKD MO MOP
TW TWD KR KRW SG SGD MY MYR TH THB ID IDR PH PHP VN VND IN INR PK PKR BD BDT LK LKR
NP NPR BT BTN MM MMK KH KHR LA LAK MN MNT BN BND MV MVR AU AUD NZ NZD PG PGK FJ FJD
SB SBD VU VUV WS WST TO TOP NC XPF PF XPF
""")


def _mark(code):
    return _SYM.get(code) or f"{code}\u00a0"


def _territory():
    region = ""
    try:
        from PySide6.QtCore import QLocale

        loc = QLocale.system()
        try:
            region = (QLocale.territoryToCode(loc.territory()) or "").upper()
        except Exception:
            region = ""
        if not region:
            name = loc.name().replace("-", "_")
            tail = name.rsplit("_", 1)[-1].upper()
            region = tail if len(tail) == 2 and tail.isalpha() else ""
    except Exception:
        region = ""
    if region:
        return region
    for var in ("LC_ALL", "LC_MONETARY", "LANG"):
        part = os.environ.get(var, "").replace("-", "_").split(".")[0]
        tail = part.rsplit("_", 1)[-1].upper()
        if len(tail) == 2 and tail.isalpha():
            return tail
    return ""


def territory():
    env = os.environ.get(_ENV_REGION, "").strip().upper()
    if len(env) == 2 and env.isalpha():
        return env
    return _territory()


def _russian_ui() -> bool:
    """Whether the app is currently showing Russian copy."""
    try:
        from .i18n import current_language

        return str(current_language() or "").strip().lower().split("-")[0] == "ru"
    except Exception:
        return False


def donation_route() -> str:
    """The donation method for this reader, or "" for the normal worldwide row.

    A Russian card cannot be used on the international coffee pages, so Russian
    readers in Russia get Boosty alone. A Russian reader outside Russia gets the
    Buy Me a Coffee page in Boosty's place, which also makes that fallback
    testable from a non-Russian machine.
    """
    if not _russian_ui():
        return ""
    return "boosty" if territory() == "RU" else "bmc"


def routed():
    """Back-compat alias for :func:`donation_route`."""
    return donation_route()


def _detect():
    env = os.environ.get(_ENV, "").strip().upper()
    if len(env) == 3 and env.isalpha():
        return env
    region = _territory()
    if region in _REGION:
        return _REGION[region]
    zone = ""
    try:
        zone = (time.tzname[0] if time.tzname else "").lower()
        info = str(time.localtime().tm_gmtoff) if hasattr(time.localtime(), "tm_gmtoff") else ""
        zone = f"{zone} {info}".lower()
    except Exception:
        zone = ""
    if "jst" in zone or "tokyo" in zone:
        return "JPY"
    if "london" in zone or "bst" in zone:
        return "GBP"
    if "cest" in zone or "cet" in zone or "berlin" in zone or "paris" in zone or "rome" in zone:
        return "EUR"
    return "USD"


def pair():
    code = _detect()
    return (_mark(code), code)


def rate(code="USD"):
    code = (code or "USD").upper()
    value = _state["rates"].get(code) or _BAKED.get(code) or 1.0
    return float(value)


def _unit(code):
    # Whole units only where the currency has no minor unit; everything else
    # keeps cents, so "$5.79" never collapses to "$6".
    return 1.0 if code in _ZERO else 0.01


def local(usd, code="USD"):
    q = _unit(code)
    return round(float(usd or 0) * rate(code) / q) * q


def local_amount(value, code="USD"):
    """Format an amount that is already in ``code``, without converting again."""
    code = (code or "USD").upper()
    value = float(value or 0)
    if code in _ZERO:
        return f"{value:,.0f}"
    big = abs(value) >= 1000
    if float(value).is_integer():
        return f"{value:,.0f}" if big else str(int(value))
    return f"{value:,.2f}" if big else f"{value:.2f}"


def amount(usd, code="USD"):
    """The converted figure alone, grouped and rounded to the currency's unit."""
    code = (code or "USD").upper()
    return local_amount(local(usd, code), code)


def _with_symbol(amount_text, code):
    code = (code or "USD").upper()
    symbol = _mark(code)
    gap = " " if len(symbol) > 1 or (symbol and symbol[-1].isalpha()) else ""
    return f"{symbol}{gap}{amount_text}"


def money(usd, code="USD"):
    """A converted amount with the reader's currency mark."""
    return _with_symbol(amount(usd, code), code)


def money_local(value, code="USD"):
    """A local-currency amount with its mark, without converting again."""
    return _with_symbol(local_amount(value, code), code)


def goal(usd, code="USD"):
    """The target in the reader's currency, rounded up to a friendly unit."""
    value = float(usd or 0) * rate(code)
    q = 1000.0 if value >= 20000 else (100.0 if value >= 2000 else (10.0 if value >= 200 else 1.0))
    return math.ceil(value / q - 1e-9) * q


def share(raised, target):
    if target <= 0:
        return 0.0
    return min(100.0, max(0.0, float(raised) / float(target) * 100.0))


def _cache_path():
    from .catalog import _app_cache_dir

    return _app_cache_dir() / "fx.json"


def _warm():
    if _state["warm"]:
        return
    _state["warm"] = True
    try:
        blob = json.loads(_cache_path().read_text(encoding="utf-8"))
        rates = {str(k).upper(): float(v) for k, v in blob.get("rates", {}).items()}
        if rates:
            with _lock:
                if not _state["rates"]:
                    _state["rates"] = rates
                    _state["at"] = float(blob.get("at") or 0.0)
    except Exception:
        pass


def _parse(blob):
    if not isinstance(blob, dict):
        return {}
    for key in ("rates", "usd"):
        inner = blob.get(key)
        if not isinstance(inner, dict):
            continue
        out = {}
        for k, v in inner.items():
            try:
                out[str(k).upper()] = float(v)
            except (TypeError, ValueError):
                continue
        if out.get("USD"):
            return out
    return {}


def _pull():
    from .network import _get_ssl_context

    ctx = _get_ssl_context()
    for url in _FEEDS:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "InnioasisUpdater/3.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=8.0, context=ctx) as resp:
                if resp.status != 200:
                    continue
                rates = _parse(json.loads(resp.read().decode("utf-8", "replace")))
            if not rates:
                continue
            with _lock:
                _state["rates"] = rates
                _state["at"] = time.time()
            try:
                _cache_path().write_text(
                    json.dumps({"at": time.time(), "rates": rates}), encoding="utf-8"
                )
            except Exception:
                pass
            return True
        except Exception as e:
            log.debug("fx feed failed: %s", e)
    return False


def _run(callback):
    try:
        _warm()
        now = time.time()
        stale = now - float(_state["at"] or 0.0) >= _TTL
        cooling = now - float(_state["failed"] or 0.0) < _RETRY
        if stale and not cooling:
            if _pull():
                _state["failed"] = 0.0
            else:
                # Offline or every feed is down: keep the baked table, stay
                # quiet, and don't retry on every dialog or bar that opens.
                _state["failed"] = time.time()
    finally:
        with _lock:
            _state["busy"] = False
    if callback:
        try:
            callback()
        except Exception:
            log.debug("fx callback failed", exc_info=True)


def refresh(callback=None):
    with _lock:
        if _state["busy"]:
            return
        _state["busy"] = True
    threading.Thread(target=_run, args=(callback,), daemon=True).start()
