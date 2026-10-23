"""Genera data.json para el Morning Brief.

Uso:  python update_data.py
Requiere:  pip install yfinance feedparser anthropic
Opcional:  variable de entorno ANTHROPIC_API_KEY (resúmenes en español y daily bursátil).
"""
import argparse, html, json, os, re, datetime as dt
import yfinance as yf
import feedparser

# Cotizaciones de referencia Macro (Índices, Commodities, Tasas y Monedas)
QUOTES = [
    ("S&P 500", "^GSPC"),
    ("Nasdaq", "^IXIC"),
    ("Dow Jones", "^DJI"),
    ("Merval", "^MERV"),
    ("Dólar DXY", "DX-Y.NYB"),
    ("Oro", "GC=F"),
    ("Petróleo WTI", "CL=F"),
    ("Bitcoin", "BTC-USD"),
    ("Rendimiento UST 10a", "^TNX"),
    ("Emergentes (EEM)", "EEM"),
]

# Panel extendido de activos para el Watchlist del Morning Brief
WATCH = [
    # --- Semiconductors & IA Hardware ---
    ("NVDA", "NVIDIA"),
    ("ASML", "ASML Holding"),
    ("MU", "Micron Technology"),
    ("AMD", "Advanced Micro Devices"),
    ("TSM", "Taiwan Semiconductor"),
    ("AVGO", "Broadcom"),
    ("INTC", "Intel"),
    ("QCOM", "Qualcomm"),

    # --- Big Tech & Software ---
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("GOOGL", "Alphabet / Google"),
    ("AMZN", "Amazon"),
    ("META", "Meta Platforms"),
    ("TSLA", "Tesla"),
    ("CRM", "Salesforce"),
    ("NFLX", "Netflix"),
    ("ORCL", "Oracle"),
    ("PLTR", "Palantir"),

    # --- Finanzas, Bancos & Pagos ---
    ("JPM", "JPMorgan Chase"),
    ("BAC", "Bank of America"),
    ("GS", "Goldman Sachs"),
    ("V", "Visa"),
    ("MA", "Mastercard"),
    ("NU", "Nu Holdings"),

    # --- Consumo, Salud & Defensivas ---
    ("XLP", "Consumer Staples ETF"),
    ("KO", "Coca-Cola"),
    ("PG", "Procter & Gamble"),
    ("WMT", "Walmart"),
    ("LLY", "Eli Lilly"),
    ("NKE", "Nike"),
    ("DIS", "Disney"),

    # --- Energía e Industriales ---
    ("VIST", "Vista Energy"),
    ("XLE", "Energy Sector ETF"),
    ("XOM", "ExxonMobil"),
    ("CVX", "Chevron"),
    ("CAT", "Caterpillar"),

    # --- Argentina / LatAm (CEDEARs & ADRs) ---
    ("MELI", "MercadoLibre"),
    ("GGAL", "Grupo Financiero Galicia"),
    ("YPF", "YPF"),
    ("BMA", "Banco Macro"),
    ("PAM", "Pampa Energía"),
    ("GLOB", "Globant"),
    ("CRESY", "Cresud"),
]

# Tickers principales a monitorear para noticias internacionales en Yahoo Finance
INTL_NEWS = [
    "^GSPC", "^IXIC", "^DJI", "NVDA", "AAPL", "MSFT", "META", "GOOGL", "AMZN", 
    "TSLA", "JPM", "ASML", "MU", "AMD", "TSM", "NU", "VIST", "XLP", "LLY", "XOM"
]

MAX_DIAS = 3  # No se publican noticias más viejas que esto

# Tickers principales a monitorear para noticias locales en Yahoo Finance
LOCAL_NEWS = ["MELI", "GGAL", "YPF", "BMA", "PAM", "VIST", "GLOB", "CRESY"]

LOCAL_FEEDS = [
    "https://news.google.com/rss/search?q=mercados+argentina+bonos+merval+when:2d&hl=es-419&gl=AR&ceid=AR:es-419"
]
INTL_FEEDS = [
    "https://news.google.com/rss/search?q=wall+street+acciones+mercados+when:2d&hl=es-419&gl=AR&ceid=AR:es-419"
]

MODEL = "claude-sonnet-5-5"

CONTEXTO = {
    "apertura": "los mercados local y de EE.UU. todavía no abrieron: contá el cierre anterior, lo ocurrido en la madrugada y qué mirar al abrir.",
    "intradia": "los mercados están operando: contá lo que va de la rueda y las noticias del día.",
    "cierre": "los mercados local y de EE.UU. ya cerraron: contá cómo cerró la rueda y qué queda para mañana.",
}


def closes(symbols):
    df = yf.download(symbols, period="2mo", progress=False, auto_adjust=True)["Close"]
    return df.dropna(how="all")


def ago(when):
    mins = int((dt.datetime.now(dt.timezone.utc) - when).total_seconds() // 60)
    return f"hace {mins} min" if mins < 60 else f"hace {mins // 60} h" if mins < 1440 else f"hace {mins // 1440} d"


def reciente(when):
    return (dt.datetime.now(dt.timezone.utc) - when) <= dt.timedelta(days=MAX_DIAS)


def limpiar(titulo, resumen, src=""):
    """Quita HTML, entidades (&nbsp;) y el medio al final del título; descarta resúmenes que repiten el título."""
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", titulo)).replace("\xa0", " ")).strip()
    if src and t.endswith(" - " + src):
        t = t[: -len(src) - 3].strip()
    r = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", resumen)).replace("\xa0", " ")).strip()
    if not r or r.lower().startswith(t.lower()[:40]) or t.lower().startswith(r.lower()[:40]):
        r = ""
    return t, r


def yahoo_news(tickers, zone):
    out = []
    for tk in tickers:
        try:
            items = yf.Ticker(tk).news or []
        except Exception as ex:
            print("Yahoo news falló para", tk, "->", ex)
            continue
        for it in items:
            c = it.get("content") or it
            url = (c.get("canonicalUrl") or c.get("clickThroughUrl") or {}).get("url") or it.get("link")
            pub = c.get("pubDate")
            if not (c.get("title") and url and pub):
                continue
            when = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if not reciente(when):
                continue
            titulo, resumen = limpiar(c["title"], c.get("summary") or c.get("description") or "")
            out.append({
                "h": titulo,
                "s": resumen,
                "src": (c.get("provider") or {}).get("displayName") or "Yahoo Finance",
                "when": when,
                "url": url,
                "tk": [tk.lstrip("^")],
                "zona": zone
            })
    return out


def feed_news(feeds, zona, tk):
    out = []
    for feed in feeds:
        for e in feedparser.parse(feed).entries[:20]:
            if not getattr(e, "published_parsed", None):
                continue
            when = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc)
            if not reciente(when):
                continue
            src = (e.get("source") or {}).get("title", "Noticias")
            titulo, resumen = limpiar(e.title, e.get("summary", ""), src)
            out.append({
                "h": titulo,
                "s": resumen,
                "src": src,
                "when": when,
                "url": e.link,
                "tk": [tk],
                "zona": zona
            })
    return out


def dedupe(items, limit):
    seen, res = set(), []
    for n in sorted(items, key=lambda x: x["when"], reverse=True):
        key = n["h"].lower()[:60]
        if key not in seen:
            seen.add(key)
            res.append(n)
    return res[:limit]


def with_claude(quotes, news, modo):
    """Resumen corto (s), ampliado (x) y daily bursátil, solo con lo que dicen las fuentes."""
    import anthropic
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3)))
    src = [{"i": i, "zona": n["zona"], "titulo": n["h"], "texto": n["s"]} for i, n in enumerate(news)]
    prompt = (
        f"Hoy es {now:%d/%m/%Y} y son las {now:%H:%M} en Argentina: {CONTEXTO[modo]} No menciones la hora exacta en el texto del reporte. "
        "Escribí en español rioplatense neutro y profesional. Usá SOLO los datos de las cotizaciones y noticias de abajo; "
        "no inventes cifras ni hechos, y no des recomendaciones de compra o venta.\n"
        "Devolvé SOLO un JSON con esta forma: "
        '{"news":[{"i":0,"s":"resumen de 1-2 oraciones","x":"resumen ampliado de 4-6 oraciones"}],'
        '"daily":"reporte para WhatsApp de unas 250 palabras, con *negritas*, secciones Internacional, Local y Para seguir"}\n\n'
        f"COTIZACIONES (último precio disponible, variación % contra la rueda previa): {json.dumps(quotes, ensure_ascii=False)}\n"
        f"NOTICIAS: {json.dumps(src, ensure_ascii=False)}"
    )
    msg = anthropic.Anthropic().messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = re.sub(r"^```(?:json)?|```$", "", msg.content[0].text.strip(), flags=re.M).strip()
    return json.loads(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modo", choices=list(CONTEXTO), default="apertura")
    modo = ap.parse_args().modo
    
    # Descargar datos para las listas QUOTES y WATCH
    all_symbols = list(set([s for _, s in QUOTES] + [s for s, _ in WATCH]))
    q = closes(all_symbols)
    
    quotes, watch = [], []
    for name, sym in QUOTES:
        if sym in q and not q[sym].dropna().empty:
            s = q[sym].dropna()
            quotes.append([name, round(float(s.iloc[-1]), 2), round(float(s.iloc[-1] / s.iloc[-2] - 1) * 100, 2)])
        else:
            print(f"Advertencia: No se obtuvieron datos para QUOTES symbol {sym}")

    for sym, name in WATCH:
        if sym in q and not q[sym].dropna().empty:
            s = q[sym].dropna()
            watch.append([
                sym,
                name,
                round(float(s.iloc[-1]), 2),
                round(float(s.iloc[-1] / s.iloc[-2] - 1) * 100, 2),
                [round(float(v), 2) for v in s.iloc[-20:]]
            ])
        else:
            print(f"Advertencia: No se obtuvieron datos para WATCH symbol {sym}")

    y_intl = yahoo_news(INTL_NEWS, "Internacional")
    y_loc = yahoo_news(LOCAL_NEWS, "Local")
    print("Noticias de Yahoo:", len(y_intl), "internacionales,", len(y_loc), "locales")
    
    news = (
        dedupe(y_intl + feed_news(INTL_FEEDS, "Internacional", "EEUU"), 15)
        + dedupe(y_loc + feed_news(LOCAL_FEEDS, "Local", "AR"), 10)
    )
    
    data = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(), "quotes": quotes, "watch": watch}

    ai = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            ai = with_claude(quotes, news, modo)
        except Exception as e:  # si falla, se publican los textos originales
            print("Claude no disponible:", e)
            
    by_i = {r["i"]: r for r in ai.get("news", [])}
    data["news"] = [
        {
            "h": n["h"],
            "s": by_i.get(i, {}).get("s") or n["s"][:280],
            "x": by_i.get(i, {}).get("x", ""),
            "src": n["src"],
            "t": ago(n["when"]),
            "tk": n["tk"],
            "url": n["url"],
            "z": n["zona"]
        }
        for i, n in enumerate(news)
    ]
    
    try:  # conserva los otros reportes del día (apertura / cierre)
        with open("data.json", encoding="utf-8") as f:
            data["dailies"] = json.load(f).get("dailies", {})
    except Exception:
        data["dailies"] = {}
        
    if ai.get("daily"):
        data["dailies"][modo] = {"texto": ai["daily"], "gen": dt.datetime.now(dt.timezone.utc).isoformat()}

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("data.json listo:", len(quotes), "cotizaciones,", len(watch), "activos en watch,", len(data["news"]), "noticias")


if __name__ == "__main__":
    main()