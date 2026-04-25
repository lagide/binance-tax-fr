#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
binance_tax.py :Calcul plus/moins-values crypto 2025 pour formulaire 2086 (France)
Méthode officielle : article 150 VH bis du CGI (prix moyen pondéré global).

Ce script est une AIDE au calcul, pas un document fiscal opposable.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from decimal import Decimal, getcontext

import requests
from dotenv import load_dotenv
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

# ───────────────────────────────────────────────────────────────────────────────
# Configuration générale
# ───────────────────────────────────────────────────────────────────────────────

getcontext().prec = 28  # précision décimale élevée

BASE_DIR = Path(__file__).parent.resolve()
CACHE_PRICES_FILE = BASE_DIR / "cache_prix_coingecko.json"
CACHE_RAW_FILE = BASE_DIR / "cache_donnees_binance.json"
RAPPORT_FILE = BASE_DIR / "rapport_fiscal_2025.html"

DATE_DEBUT = datetime(2022, 1, 31, tzinfo=timezone.utc)
DATE_FIN = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
ANNEE_FISCALE = 2025

# Devises fiat ignorées dans le portefeuille crypto (dépôts/retraits SEPA)
FIAT_DEVISES = {"EUR", "USD", "GBP"}

# Quote assets à tester pour découvrir les paires de l'utilisateur
QUOTES_COMMUNS = ["USDT", "EUR", "BUSD", "FDUSD", "BTC", "ETH", "BNB", "USDC"]

# Mapping symbole Binance => ID CoinGecko (extensible si besoin)
# Liste des coins les plus courants. Si un asset n'est pas listé ici,
# le script tentera une résolution dynamique via /coins/list.
MAPPING_COINGECKO_STATIQUE = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "ADA": "cardano",
    "XRP": "ripple",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "ATOM": "cosmos",
    "LTC": "litecoin",
    "DOGE": "dogecoin",
    "SHIB": "shiba-inu",
    "TRX": "tron",
    "NEAR": "near",
    "FTM": "fantom",
    "ALGO": "algorand",
    "USDT": "tether",
    "USDC": "usd-coin",
    "BUSD": "binance-usd",
    "FDUSD": "first-digital-usd",
    "DAI": "dai",
    "EUR": None,  # fiat, prix = 1 EUR
}

# ───────────────────────────────────────────────────────────────────────────────
# Logging
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("binance-tax-fr")


# ───────────────────────────────────────────────────────────────────────────────
# Utilitaires
# ───────────────────────────────────────────────────────────────────────────────

def ms(dt: datetime) -> int:
    """Convertit un datetime UTC en millisecondes Unix (format Binance)."""
    return int(dt.timestamp() * 1000)


def from_ms(timestamp_ms: int) -> datetime:
    """Convertit un timestamp ms Binance en datetime UTC."""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def fenetres_90j(debut: datetime, fin: datetime):
    """Génère des fenêtres de 90 jours pour les endpoints limités."""
    cur = debut
    while cur < fin:
        nxt = min(cur + timedelta(days=90), fin)
        yield cur, nxt
        cur = nxt


def fenetres_30j(debut: datetime, fin: datetime):
    """Idem, 30 jours (endpoint convert)."""
    cur = debut
    while cur < fin:
        nxt = min(cur + timedelta(days=30), fin)
        yield cur, nxt
        cur = nxt


def fmt_eur(montant) -> str:
    """Formate un montant en EUR pour affichage."""
    return f"{Decimal(str(montant)):,.2f} €".replace(",", " ").replace(".", ",")


def fmt_qty(qty) -> str:
    """Formate une quantité crypto."""
    return f"{Decimal(str(qty)):,.8f}".rstrip("0").rstrip(".")


# ───────────────────────────────────────────────────────────────────────────────
# Cache des prix CoinGecko
# ───────────────────────────────────────────────────────────────────────────────

class CachePrix:
    """Cache local JSON pour éviter de spammer CoinGecko."""

    def __init__(self, fichier: Path):
        self.fichier = fichier
        self.data = {}
        if fichier.exists():
            try:
                self.data = json.loads(fichier.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Cache prix illisible, on repart à zéro : %s", e)
                self.data = {}

    def cle(self, asset: str, date_str: str) -> str:
        return f"{asset.upper()}|{date_str}"

    def get(self, asset: str, date_str: str):
        return self.data.get(self.cle(asset, date_str))

    def set(self, asset: str, date_str: str, prix_eur):
        self.data[self.cle(asset, date_str)] = prix_eur
        # Sauvegarde immédiate pour résister aux crashs
        self.fichier.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


# ───────────────────────────────────────────────────────────────────────────────
# Résolution des prix (klines Binance :gratuit, public, sans auth)
# ───────────────────────────────────────────────────────────────────────────────

class ResolveurPrix:
    """
    Récupère les prix EUR historiques via l'endpoint public Binance /api/v3/klines.
    Stratégie :
      1. Si paire {ASSET}EUR existe => close du jour J
      2. Sinon : {ASSET}USDT × (1/EURUSDT du jour J)
      3. Stablecoins USD-pegged => 1/EURUSDT
      4. EUR => 1
    """

    URL_KLINES = "https://api.binance.com/api/v3/klines"
    URL_EXINFO = "https://api.binance.com/api/v3/exchangeInfo"

    STABLES_USD = {"USDT", "USDC", "BUSD", "FDUSD", "DAI", "TUSD", "USDP"}

    def __init__(self, cache: CachePrix, delai_appel: float = 0.15):
        self.cache = cache
        self.delai = delai_appel  # endpoint public Binance : 1200 req/min
        self.session = requests.Session()
        self.assets_inconnus = set()
        self._symbols_valides = None
        # Cache mémoire EURUSDT par jour (évite de réinterroger)
        self._cache_eurusdt = {}

    def _charger_symbols(self):
        if self._symbols_valides is not None:
            return
        try:
            r = self.session.get(self.URL_EXINFO, timeout=30)
            r.raise_for_status()
            data = r.json()
            self._symbols_valides = {
                s["symbol"] for s in data.get("symbols", [])
                if s.get("status") == "TRADING" or True  # garder même les delisted
            }
        except Exception as e:
            log.warning("Impossible de charger exchangeInfo : %s", e)
            self._symbols_valides = set()

    def _kline_close(self, symbol: str, dt: datetime) -> Decimal:
        """Récupère le close 1d d'un symbole pour la date dt (UTC)."""
        # Fenêtre = jour entier UTC
        debut = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
        fin = debut + timedelta(days=1)
        params = {
            "symbol": symbol,
            "interval": "1d",
            "startTime": ms(debut),
            "endTime": ms(fin),
            "limit": 1,
        }
        for tentative in range(5):
            try:
                r = self.session.get(self.URL_KLINES, params=params, timeout=30)
                if r.status_code == 429:
                    attente = (tentative + 1) * 10
                    log.warning("Rate limit Binance klines, attente %ss...", attente)
                    time.sleep(attente)
                    continue
                if r.status_code == 400:
                    # symbole inconnu / pas de données
                    return Decimal("0")
                r.raise_for_status()
                data = r.json()
                if not data:
                    return Decimal("0")
                close = data[0][4]  # [openTime, open, high, low, close, ...]
                time.sleep(self.delai)
                return Decimal(str(close))
            except Exception as e:
                log.warning("Erreur klines %s @ %s : %s", symbol, dt.date(), e)
                time.sleep(2)
        return Decimal("0")

    def _eurusdt(self, dt: datetime) -> Decimal:
        """Prix EURUSDT (combien d'USDT pour 1 EUR) à la date dt. Cache mémoire."""
        cle = dt.strftime("%Y-%m-%d")
        if cle in self._cache_eurusdt:
            return self._cache_eurusdt[cle]
        # Cache disque
        cached = self.cache.get("__EURUSDT__", cle)
        if cached is not None:
            v = Decimal(str(cached))
            self._cache_eurusdt[cle] = v
            return v
        prix = self._kline_close("EURUSDT", dt)
        if prix == 0:
            # fallback : EURUSD via paire BUSD ou autre (rare)
            prix = self._kline_close("EURUSDC", dt)
        self.cache.set("__EURUSDT__", cle, str(prix))
        self._cache_eurusdt[cle] = prix
        return prix

    def prix_eur(self, asset: str, dt: datetime) -> Decimal:
        """Retourne le prix EUR d'un asset à une date donnée (ou 0 si introuvable)."""
        asset_up = asset.upper()
        if asset_up == "EUR":
            return Decimal("1")

        date_str = dt.strftime("%d-%m-%Y")
        cached = self.cache.get(asset_up, date_str)
        if cached is not None:
            return Decimal(str(cached))

        self._charger_symbols()

        # Stables USD : prix EUR = 1 / EURUSDT
        if asset_up in self.STABLES_USD:
            eurusdt = self._eurusdt(dt)
            prix = (Decimal("1") / eurusdt) if eurusdt > 0 else Decimal("0")
            self.cache.set(asset_up, date_str, str(prix))
            return prix

        # 1) Paire directe ASSET/EUR
        sym_eur = f"{asset_up}EUR"
        if sym_eur in self._symbols_valides:
            prix = self._kline_close(sym_eur, dt)
            if prix > 0:
                self.cache.set(asset_up, date_str, str(prix))
                return prix

        # 2) Via USDT
        sym_usdt = f"{asset_up}USDT"
        if sym_usdt in self._symbols_valides:
            prix_usdt = self._kline_close(sym_usdt, dt)
            eurusdt = self._eurusdt(dt)
            if prix_usdt > 0 and eurusdt > 0:
                prix = prix_usdt / eurusdt
                self.cache.set(asset_up, date_str, str(prix))
                return prix

        # 3) Via BTC (rare, pour les très anciens listings)
        sym_btc = f"{asset_up}BTC"
        if sym_btc in self._symbols_valides:
            prix_btc = self._kline_close(sym_btc, dt)
            btc_eur = self.prix_eur("BTC", dt) if asset_up != "BTC" else Decimal("0")
            if prix_btc > 0 and btc_eur > 0:
                prix = prix_btc * btc_eur
                self.cache.set(asset_up, date_str, str(prix))
                return prix

        # Échec
        self.assets_inconnus.add(asset_up)
        self.cache.set(asset_up, date_str, "0")
        return Decimal("0")


# ───────────────────────────────────────────────────────────────────────────────
# Récupération Binance
# ───────────────────────────────────────────────────────────────────────────────

class CollecteurBinance:
    """Encapsule tous les appels API Binance avec gestion de la pagination."""

    def __init__(self, client: Client):
        self.client = client

    def _safe_call(self, fn, *args, **kwargs):
        """Appelle un endpoint avec retry simple."""
        for tentative in range(5):
            try:
                return fn(*args, **kwargs)
            except BinanceAPIException as e:
                if e.code == -1003:  # rate limit
                    log.warning("Binance rate limit, attente 60s...")
                    time.sleep(60)
                    continue
                # endpoint inexistant pour ce compte => on remonte vide
                if e.code in (-2008, -1121, -1100):
                    return None
                log.warning("BinanceAPIException %s : %s", e.code, e.message)
                time.sleep(2)
            except BinanceRequestException as e:
                log.warning("BinanceRequestException : %s", e)
                time.sleep(5)
            except Exception as e:
                log.warning("Erreur inattendue : %s", e)
                time.sleep(5)
        return None

    # ─── Fiat deposits / withdrawals ──────────────────────────────────────────

    def fiat_history(self, transaction_type: str):
        """transaction_type : '0' = dépôts, '1' = retraits."""
        resultats = []
        for debut, fin in fenetres_90j(DATE_DEBUT, DATE_FIN):
            page = 1
            while True:
                rep = self._safe_call(
                    self.client.get_fiat_deposit_withdraw_history,
                    transactionType=transaction_type,
                    beginTime=ms(debut),
                    endTime=ms(fin),
                    page=page,
                    rows=500,
                )
                if not rep or "data" not in rep:
                    break
                lignes = rep.get("data") or []
                resultats.extend(lignes)
                if len(lignes) < 500:
                    break
                page += 1
                time.sleep(0.3)
        return resultats

    # ─── Spot trades ──────────────────────────────────────────────────────────

    def decouvrir_assets(self, fiat_deposits, fiat_withdrawals, convert_trades):
        """Construit la liste des assets potentiellement détenus."""
        assets = set()
        # Balances actuelles
        compte = self._safe_call(self.client.get_account)
        if compte:
            for b in compte.get("balances", []):
                free = Decimal(b.get("free", "0"))
                locked = Decimal(b.get("locked", "0"))
                if free + locked > 0:
                    assets.add(b["asset"])
        # Conversions
        for c in convert_trades:
            assets.add(c.get("fromAsset"))
            assets.add(c.get("toAsset"))
        # Fiat
        for d in fiat_deposits + fiat_withdrawals:
            assets.add(d.get("fiatCurrency"))
        assets.discard(None)
        return assets

    def trades_pour_paire(self, symbol: str):
        trades = []
        from_id = 0
        while True:
            rep = self._safe_call(
                self.client.get_my_trades, symbol=symbol, fromId=from_id, limit=1000
            )
            if not rep:
                break
            trades.extend(rep)
            if len(rep) < 1000:
                break
            from_id = max(int(t["id"]) for t in rep) + 1
            time.sleep(0.3)
        return trades

    def tous_les_trades(self, assets):
        """Itère sur les paires asset×quote pour trouver les trades."""
        # Liste valide des symbols spot
        info = self._safe_call(self.client.get_exchange_info)
        symbols_valides = {s["symbol"] for s in (info or {}).get("symbols", [])}

        candidats = set()
        for a in assets:
            for q in QUOTES_COMMUNS:
                if a == q:
                    continue
                # paire normale
                if f"{a}{q}" in symbols_valides:
                    candidats.add(f"{a}{q}")
                # paire inversée (ex: BTCEUR vs EURBTC)
                if f"{q}{a}" in symbols_valides:
                    candidats.add(f"{q}{a}")

        log.info("Recherche trades sur %d paires candidates...", len(candidats))
        tous = []
        for i, sym in enumerate(sorted(candidats), 1):
            log.info("  [%d/%d] %s", i, len(candidats), sym)
            t = self.trades_pour_paire(sym)
            for tr in t:
                tr["_symbol"] = sym
            tous.extend(t)
        return tous

    # ─── Convert ──────────────────────────────────────────────────────────────

    def convert_history(self):
        resultats = []
        for debut, fin in fenetres_30j(DATE_DEBUT, DATE_FIN):
            rep = self._safe_call(
                self.client.get_convert_trade_history,
                startTime=ms(debut),
                endTime=ms(fin),
                limit=1000,
            )
            if rep and "list" in rep:
                resultats.extend(rep["list"])
            time.sleep(0.3)
        return resultats

    # ─── Earn rewards (best effort) ───────────────────────────────────────────

    def earn_rewards(self):
        """Tentative de récupération des récompenses Earn (peut être partielle)."""
        rewards = []
        # Flexible savings interest
        endpoint_fns = [
            ("get_lending_interest_history", {"lendingType": "DAILY"}),
            ("get_lending_interest_history", {"lendingType": "ACTIVITY"}),
            ("get_lending_interest_history", {"lendingType": "CUSTOMIZED_FIXED"}),
        ]
        for nom, kw in endpoint_fns:
            fn = getattr(self.client, nom, None)
            if not fn:
                continue
            for debut, fin in fenetres_30j(DATE_DEBUT, DATE_FIN):
                rep = self._safe_call(
                    fn,
                    startTime=ms(debut),
                    endTime=ms(fin),
                    size=100,
                    **kw,
                )
                if isinstance(rep, list):
                    rewards.extend(rep)
                time.sleep(0.3)
        return rewards


# ───────────────────────────────────────────────────────────────────────────────
# Reconstruction du portefeuille (ledger)
# ───────────────────────────────────────────────────────────────────────────────

def construire_ledger(deposits_fiat, withdrawals_fiat, trades_spot, converts, rewards):
    """
    Construit une liste d'événements ordonnés chronologiquement
    pour reconstruire les balances à tout instant.

    Format événement : {time: datetime, type: str, deltas: {asset: Decimal}, meta: dict}
    """
    evts = []

    # Dépôts fiat => ajoutent EUR (mais EUR n'est pas comptabilisé dans le portefeuille crypto)
    for d in deposits_fiat:
        if (d.get("status") or "").lower() not in ("successful", "completed", "success"):
            continue
        if d.get("fiatCurrency") != "EUR":
            continue
        t = from_ms(int(d["createTime"]))
        montant = Decimal(str(d.get("amount", "0"))) - Decimal(str(d.get("totalFee", "0") or 0))
        evts.append({
            "time": t,
            "type": "fiat_deposit",
            "deltas": {"EUR": montant},
            "meta": d,
        })

    # Retraits fiat => cessions
    for w in withdrawals_fiat:
        if (w.get("status") or "").lower() not in ("successful", "completed", "success"):
            continue
        if w.get("fiatCurrency") != "EUR":
            continue
        t = from_ms(int(w["createTime"]))
        # Le montant net effectivement reçu = amount - fee
        montant_brut = Decimal(str(w.get("amount", "0")))
        frais = Decimal(str(w.get("totalFee", "0") or 0))
        montant_net = montant_brut - frais
        evts.append({
            "time": t,
            "type": "fiat_withdrawal",
            "deltas": {"EUR": -montant_brut},
            "meta": {**w, "_montant_net": str(montant_net), "_frais": str(frais)},
        })

    # Trades spot
    for tr in trades_spot:
        sym = tr["_symbol"]
        # On a besoin de connaître base/quote => on parse via exchangeInfo plus tard
        # Approche simple : récupère via le dict (commission, isBuyer, qty, quoteQty, price)
        t = from_ms(int(tr["time"]))
        qty = Decimal(str(tr["qty"]))
        quote_qty = Decimal(str(tr["quoteQty"]))
        commission = Decimal(str(tr.get("commission", "0") or 0))
        commission_asset = tr.get("commissionAsset")
        is_buyer = tr.get("isBuyer", False)
        # Symbole = base+quote => on déduit base/quote en cherchant un quote connu
        base, quote = _split_symbol(sym)
        if not base or not quote:
            continue
        deltas = {}
        if is_buyer:
            deltas[base] = qty
            deltas[quote] = -quote_qty
        else:
            deltas[base] = -qty
            deltas[quote] = quote_qty
        if commission_asset:
            deltas[commission_asset] = deltas.get(commission_asset, Decimal("0")) - commission
        evts.append({
            "time": t,
            "type": "spot_trade",
            "deltas": deltas,
            "meta": tr,
        })

    # Conversions
    for c in converts:
        if (c.get("orderStatus") or "").upper() != "SUCCESS":
            continue
        t = from_ms(int(c["createTime"]))
        from_a = c["fromAsset"]
        to_a = c["toAsset"]
        from_q = Decimal(str(c["fromAmount"]))
        to_q = Decimal(str(c["toAmount"]))
        evts.append({
            "time": t,
            "type": "convert",
            "deltas": {from_a: -from_q, to_a: to_q},
            "meta": c,
        })

    # Earn rewards
    for r in rewards:
        try:
            t = from_ms(int(r.get("time") or r.get("createTime") or 0))
            asset = r.get("asset")
            amount = Decimal(str(r.get("interest") or r.get("amount") or "0"))
            if not asset or amount == 0:
                continue
            evts.append({
                "time": t,
                "type": "earn_reward",
                "deltas": {asset: amount},
                "meta": r,
            })
        except Exception:
            continue

    evts.sort(key=lambda e: e["time"])
    return evts


def _split_symbol(symbol: str):
    """Sépare un symbole spot en (base, quote) à partir de la liste connue."""
    for q in sorted(QUOTES_COMMUNS, key=len, reverse=True):
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[: -len(q)], q
    return None, None


def appliquer_evt(balances: dict, evt: dict):
    """Applique les deltas d'un événement aux balances."""
    for asset, delta in evt["deltas"].items():
        if asset is None:
            continue
        balances[asset] = balances.get(asset, Decimal("0")) + delta


def valeur_portefeuille_crypto(balances: dict, dt: datetime, resolveur: ResolveurPrix) -> Decimal:
    """Valeur totale du portefeuille CRYPTO (hors EUR) à une date."""
    total = Decimal("0")
    for asset, qty in balances.items():
        if qty <= 0:
            continue
        if asset in FIAT_DEVISES:
            continue
        prix = resolveur.prix_eur(asset, dt)
        total += qty * prix
    return total


# ───────────────────────────────────────────────────────────────────────────────
# Calcul fiscal :méthode 150 VH bis CGI
# ───────────────────────────────────────────────────────────────────────────────

def calculer_cessions(events, resolveur: ResolveurPrix):
    """
    Applique la méthode du prix moyen pondéré global.
    Ne traite QUE les retraits SEPA EUR (= cessions imposables).

    Formule : PMV = PC - (PTA × PC / VGP)
    Après cession : PTA -= PTA × PC / VGP
    """
    balances = {}
    pta = Decimal("0")           # Prix Total Acquisitions cumulé
    cessions = []                 # détail pour le rapport
    deposits_eur_log = []         # justificatif

    for evt in events:
        # Avant tout, on applique l'événement
        if evt["type"] == "fiat_deposit":
            # Augmente le PTA (= EUR injecté pour acheter de la crypto)
            montant = evt["deltas"]["EUR"]
            pta += montant
            deposits_eur_log.append({
                "date": evt["time"],
                "montant": montant,
                "pta_apres": pta,
            })
            appliquer_evt(balances, evt)

        elif evt["type"] == "fiat_withdrawal":
            # CESSION
            meta = evt["meta"]
            t = evt["time"]
            montant_net = Decimal(meta.get("_montant_net", "0"))
            frais = Decimal(meta.get("_frais", "0"))
            # Prix de cession = montant net reçu (frais déductibles)
            prix_cession = montant_net

            # Valeur globale portefeuille CRYPTO juste avant cession
            # (le retrait EUR ne diminue PAS le portefeuille crypto)
            vgp_crypto = valeur_portefeuille_crypto(balances, t, resolveur)
            # VGP = portefeuille crypto + EUR détenu (la cession suppose qu'on a converti
            # de la crypto en EUR juste avant le retrait, donc on inclut l'EUR disponible)
            eur_disponible = balances.get("EUR", Decimal("0"))
            vgp = vgp_crypto + eur_disponible

            # Calcul plus/moins-value
            if vgp > 0:
                fraction_acq = pta * prix_cession / vgp
            else:
                fraction_acq = Decimal("0")
            plus_value = prix_cession - fraction_acq

            cession = {
                "date": t,
                "prix_cession_brut": Decimal(str(meta.get("amount", "0"))),
                "frais": frais,
                "prix_cession_net": prix_cession,
                "pta_avant": pta,
                "vgp": vgp,
                "vgp_crypto": vgp_crypto,
                "eur_disponible": eur_disponible,
                "fraction_acquisition": fraction_acq,
                "plus_value": plus_value,
            }
            # Réduction du PTA selon formule officielle
            pta -= fraction_acq
            if pta < 0:
                pta = Decimal("0")
            cession["pta_apres"] = pta
            cessions.append(cession)

            appliquer_evt(balances, evt)

        else:
            # trades / converts / rewards : ne touchent pas au PTA
            # (les rewards sont techniquement imposables comme BNC,
            # mais hors scope du formulaire 2086)
            appliquer_evt(balances, evt)

    return cessions, deposits_eur_log, balances


# ───────────────────────────────────────────────────────────────────────────────
# Génération du rapport HTML
# ───────────────────────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, system-ui, sans-serif; margin: 2em auto; max-width: 1100px; color: #222; line-height: 1.5; padding: 0 1em; }
h1 { color: #0b3d91; border-bottom: 3px solid #0b3d91; padding-bottom: .3em; }
h2 { color: #0b3d91; margin-top: 2em; border-left: 4px solid #f0b400; padding-left: .6em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.92em; }
th, td { border: 1px solid #ccc; padding: 8px 10px; text-align: right; }
th { background: #f0f3f8; color: #0b3d91; }
td:first-child, th:first-child { text-align: left; }
.alerte { background: #fff8e1; border-left: 4px solid #f0b400; padding: 1em 1.2em; margin: 1em 0; border-radius: 4px; }
.danger { background: #fdecea; border-left: 4px solid #c0392b; padding: 1em 1.2em; margin: 1em 0; border-radius: 4px; }
.ok { background: #e8f5e9; border-left: 4px solid #2e7d32; padding: 1em 1.2em; margin: 1em 0; border-radius: 4px; }
.total-pv { font-weight: bold; background: #e8f5e9; }
.total-mv { font-weight: bold; background: #fdecea; }
.numerique { text-align: right; font-variant-numeric: tabular-nums; }
.pied { margin-top: 3em; font-size: .85em; color: #666; border-top: 1px solid #ccc; padding-top: 1em; }
@media print { body { margin: 0; max-width: none; } h2 { page-break-before: auto; } }
"""

def generer_html(cessions, deposits_log, balances_finales, resolveur, assets_inconnus):
    cessions_2025 = [c for c in cessions if c["date"].year == ANNEE_FISCALE]
    total_pv = sum((c["plus_value"] for c in cessions_2025 if c["plus_value"] > 0), Decimal("0"))
    total_mv = sum((c["plus_value"] for c in cessions_2025 if c["plus_value"] < 0), Decimal("0"))
    solde_net = total_pv + total_mv  # mv déjà négative

    now = datetime.now()

    html = []
    html.append(f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>Rapport fiscal crypto {ANNEE_FISCALE}</title>
<style>{CSS}</style></head><body>
<h1>Rapport fiscal crypto :Année {ANNEE_FISCALE}</h1>
<p><em>Généré le {now.strftime('%d/%m/%Y à %H:%M')}</em></p>

<div class="danger">
<strong>⚠️ Avertissement légal</strong><br>
Ce document <strong>n'est PAS un document fiscal officiel</strong> ni opposable à l'administration.
Il s'agit d'une aide au calcul réalisée localement à partir de votre API Binance et des prix
historiques publics Binance (klines). Vérifiez chaque chiffre avant tout report dans votre déclaration.
Pour un rapport opposable, utilisez un service spécialisé (Waltio, Koinly).<br>
Méthode appliquée : article <strong>150 VH bis du CGI</strong> (prix moyen pondéré global).
</div>
""")

    # ─── Cessions
    html.append(f"<h2>1. Cessions imposables {ANNEE_FISCALE} (formulaire 2086)</h2>")
    if not cessions_2025:
        html.append("<p>Aucune cession SEPA EUR détectée pour 2025.</p>")
    else:
        html.append("""<table>
<tr>
<th>Date</th>
<th>Prix de cession brut</th>
<th>Frais</th>
<th>Prix de cession net</th>
<th>PTA avant</th>
<th>Valeur globale portefeuille</th>
<th>Fraction d'acquisition</th>
<th>Plus / moins-value</th>
</tr>""")
        for c in cessions_2025:
            cls = "total-pv" if c["plus_value"] > 0 else ("total-mv" if c["plus_value"] < 0 else "")
            html.append(f"""<tr class="{cls}">
<td>{c['date'].strftime('%d/%m/%Y')}</td>
<td>{fmt_eur(c['prix_cession_brut'])}</td>
<td>{fmt_eur(c['frais'])}</td>
<td>{fmt_eur(c['prix_cession_net'])}</td>
<td>{fmt_eur(c['pta_avant'])}</td>
<td>{fmt_eur(c['vgp'])}</td>
<td>{fmt_eur(c['fraction_acquisition'])}</td>
<td>{fmt_eur(c['plus_value'])}</td>
</tr>""")
        html.append("</table>")

    # ─── Totaux
    html.append("<h2>2. Totaux à reporter</h2>")
    html.append(f"""<div class="ok">
<p><strong>Total plus-values {ANNEE_FISCALE} :</strong> {fmt_eur(total_pv)}<br>
=> À reporter case <strong>3AN</strong> du formulaire <strong>2042 C</strong></p>
<p><strong>Total moins-values {ANNEE_FISCALE} :</strong> {fmt_eur(abs(total_mv))}<br>
=> À reporter case <strong>3BN</strong> du formulaire <strong>2042 C</strong> (reportable 10 ans)</p>
<p><strong>Solde net imposable :</strong> {fmt_eur(solde_net)}</p>
</div>""")

    # ─── Dépôts EUR
    html.append("<h2>3. Justificatif :Dépôts EUR cumulés (depuis 31/01/2022)</h2>")
    html.append("""<table>
<tr><th>Date</th><th>Montant déposé (net)</th><th>PTA cumulé après dépôt</th></tr>""")
    for d in deposits_log:
        html.append(f"""<tr>
<td>{d['date'].strftime('%d/%m/%Y %H:%M')}</td>
<td>{fmt_eur(d['montant'])}</td>
<td>{fmt_eur(d['pta_apres'])}</td>
</tr>""")
    html.append("</table>")
    total_dep = sum((d['montant'] for d in deposits_log), Decimal("0"))
    html.append(f"<p><strong>Total dépôts EUR : {fmt_eur(total_dep)}</strong></p>")

    # ─── Portefeuille au 31/12
    html.append(f"<h2>4. Actifs détenus au 31/12/{ANNEE_FISCALE}</h2>")
    html.append("""<table>
<tr><th>Actif</th><th>Quantité</th><th>Prix EUR au 31/12</th><th>Valeur EUR</th></tr>""")
    fin_annee = datetime(ANNEE_FISCALE, 12, 31, tzinfo=timezone.utc)
    total_porte = Decimal("0")
    for asset, qty in sorted(balances_finales.items()):
        if qty <= 0:
            continue
        if asset in FIAT_DEVISES:
            prix = Decimal("1") if asset == "EUR" else resolveur.prix_eur(asset, fin_annee)
        else:
            prix = resolveur.prix_eur(asset, fin_annee)
        valeur = qty * prix
        total_porte += valeur
        html.append(f"""<tr>
<td>{asset}</td>
<td>{fmt_qty(qty)}</td>
<td>{fmt_eur(prix)}</td>
<td>{fmt_eur(valeur)}</td>
</tr>""")
    html.append(f"""<tr class="total-pv">
<td colspan="3">Total portefeuille</td><td>{fmt_eur(total_porte)}</td>
</tr></table>""")

    # ─── Rappels
    html.append("""<h2>5. Rappels obligatoires</h2>
<div class="alerte">
<ul>
<li>Cocher la case <strong>8UU</strong> du formulaire 2042 (compte d'actifs numériques à l'étranger)</li>
<li>Remplir un formulaire <strong>3916-bis</strong> par compte étranger (1 pour Binance)</li>
<li>Conserver tous les justificatifs (relevés Binance, ce rapport) pendant au moins 6 ans</li>
</ul>
</div>""")

    if assets_inconnus:
        html.append(f"""<div class="danger">
<strong>⚠️ Assets non valorisés :</strong> {', '.join(sorted(assets_inconnus))}.
Ces actifs n'ont pas été trouvés via les klines Binance (delistés, renommés ou paire
inexistante) et leur prix n'a pas pu être inclus dans la valeur globale du portefeuille.
Vérifie manuellement (ex : MATIC a été renommé POL en septembre 2024).
</div>""")

    html.append("""<div class="pied">
Pour un rapport fiscal certifié et opposable, recommandation :
<a href="https://www.waltio.com">Waltio</a> ou <a href="https://koinly.io">Koinly</a>.
</div></body></html>""")

    RAPPORT_FILE.write_text("".join(html), encoding="utf-8")
    log.info("✅ Rapport HTML généré : %s", RAPPORT_FILE)


# ───────────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print(" Binance Tax FR :Calcul plus/moins-values crypto 2025")
    print(" Méthode : article 150 VH bis du CGI")
    print("=" * 70)

    load_dotenv(BASE_DIR / ".env")
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not api_key or not api_secret or api_key == "ta_cle_ici":
        log.error("Clés API manquantes. Copie .env.example vers .env et renseigne tes clés.")
        sys.exit(1)

    log.info("[1/6] Connexion à l'API Binance...")
    try:
        client = Client(api_key, api_secret)
        client.ping()
    except Exception as e:
        log.error("Connexion Binance impossible : %s", e)
        sys.exit(1)

    collecteur = CollecteurBinance(client)

    log.info("[2/6] Récupération des dépôts/retraits fiat EUR (2022 => 2025)...")
    deposits_fiat = collecteur.fiat_history("0") or []
    withdrawals_fiat = collecteur.fiat_history("1") or []
    log.info("  => %d dépôts fiat, %d retraits fiat", len(deposits_fiat), len(withdrawals_fiat))

    log.info("[3/6] Récupération des conversions Convert...")
    converts = collecteur.convert_history() or []
    log.info("  => %d conversions", len(converts))

    log.info("[4/6] Découverte des assets puis récupération des trades spot...")
    assets = collecteur.decouvrir_assets(deposits_fiat, withdrawals_fiat, converts)
    log.info("  => %d assets identifiés : %s", len(assets), ", ".join(sorted(assets)))
    trades_spot = collecteur.tous_les_trades(assets) or []
    log.info("  => %d trades spot récupérés", len(trades_spot))

    log.info("[5/6] Récupération des récompenses Earn (best-effort)...")
    rewards = collecteur.earn_rewards() or []
    log.info("  => %d récompenses", len(rewards))

    # Sauvegarde brute pour audit / debug
    try:
        CACHE_RAW_FILE.write_text(json.dumps({
            "deposits_fiat": deposits_fiat,
            "withdrawals_fiat": withdrawals_fiat,
            "converts": converts,
            "trades_spot_count": len(trades_spot),
            "rewards_count": len(rewards),
            "generated_at": datetime.now().isoformat(),
        }, default=str, indent=2), encoding="utf-8")
    except Exception:
        pass

    log.info("[6/6] Calcul fiscal et résolution des prix EUR (CoinGecko)...")
    cache = CachePrix(CACHE_PRICES_FILE)
    resolveur = ResolveurPrix(cache)

    events = construire_ledger(deposits_fiat, withdrawals_fiat, trades_spot, converts, rewards)
    log.info("  => %d événements consolidés", len(events))

    cessions, deposits_log, balances_finales = calculer_cessions(events, resolveur)
    log.info("  => %d cessions traitées dont %d en %d",
             len(cessions),
             len([c for c in cessions if c["date"].year == ANNEE_FISCALE]),
             ANNEE_FISCALE)

    generer_html(cessions, deposits_log, balances_finales, resolveur, resolveur.assets_inconnus)

    print()
    print("=" * 70)
    try:
        print(f" [OK] Terminé :rapport : {RAPPORT_FILE}")
    except UnicodeEncodeError:
        # Console Windows en cp1252 : on retombe sur de l'ASCII pur
        print(" [OK] Termine - rapport : " + str(RAPPORT_FILE).encode("ascii", "replace").decode("ascii"))
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning("Interruption utilisateur.")
        sys.exit(130)
    except Exception as e:
        log.exception("Erreur fatale : %s", e)
        sys.exit(1)
